"""BACnet/IP data source: Who-Is/I-Am binding, then ReadPropertyMultiple (ReadProperty as a fallback).

BACnet/IP is a UDP protocol whose frames are a BVLC header, an NPDU and an APDU. A driver needs no
dependency: the tags are a handful of nibble-encoded bytes, which this module builds and parses with the
standard library alone. A point names an object type, an instance and one property in its `bacnet`
section, read with ReadPropertyMultiple in one request per poll, so a device that implements only
ReadProperty still works.

The device is found by a Who-Is and bound by the I-Am that carries the configured device instance; its
reply address is remembered until a poll fails, when the next poll asks again. A reply must carry the
invoke id that was sent, so a second device on the wire cannot be mistaken for the one configured.
Malformed datagrams are ignored, a peer that keeps sending unrelated datagrams is dropped after a
bounded number of them, a reply may be segmented (segments are reassembled and acknowledged), and each
request has one timeout.

A device that rejects ReadPropertyMultiple falls back to one ReadProperty per object. A property error
inside a ReadPropertyMultiple reply, an Error/Reject/Abort PDU, or a timeout on one object is reported
per point, so one bad object never hides the values of the others. A timeout on the whole request is
the source's health sentence. Concatenated text and status flags are decoded; text points are allowed
because an object name genuinely is text.
"""
import math
import socket
import struct
import time

from .core import sentence
from .polled import PolledSource

MAX_OBJECTS = 20  # objects one poll reads, so one tick and one request stay bounded
MAX_SEGMENTS = 32  # segments of one reply before the driver stops reading
MAX_IGNORED = 16  # datagrams that are not the reply before the driver gives up on the request
MAX_DEVICES = 32  # I-Am replies one binding may consider

DEFAULT_PORT = 47808
MAX_INSTANCE = 0x3FFFFF

BVLC_START = 0x81
BVLC_BACNET_IP = 0x0A
BVLC_FORWARDED = 0x0B

CONFIRMED_REQUEST = 0x00
UNCONFIRMED_REQUEST = 0x10
SIMPLE_ACK = 0x20
COMPLEX_ACK = 0x30
SEGMENT_ACK = 0x40
ERROR_PDU = 0x50
REJECT_PDU = 0x60
ABORT_PDU = 0x70

SERVICE_I_AM = 0
SERVICE_WHO_IS = 8
SERVICE_READ_PROPERTY = 12
SERVICE_READ_PROPERTY_MULTIPLE = 14

# Application tags. The values this driver reads from a reply.
APP_NULL = 0
APP_BOOLEAN = 1
APP_UNSIGNED = 2
APP_SIGNED = 3
APP_REAL = 4
APP_DOUBLE = 5
APP_OCTETS = 6
APP_CHARACTER = 7
APP_BITS = 8
APP_ENUMERATED = 9
APP_DATE = 10
APP_TIME = 11
APP_OBJECT_ID = 12

OBJECT_TYPES = {"analogInput": 0, "analogOutput": 1, "analogValue": 2, "binaryInput": 3,
                "binaryOutput": 4, "binaryValue": 5, "multiStateValue": 19}
OBJECT_NAMES = {v: k for k, v in OBJECT_TYPES.items()}
PROPERTIES = {"presentValue": 85, "statusFlags": 111, "units": 117, "objectName": 77}
PROPERTY_NAMES = {v: k for k, v in PROPERTIES.items()}

REJECT_REASONS = {
    0: "the device rejected the request", 1: "the device is out of buffer space",
    2: "the device does not recognise that service", 3: "the request used an invalid tag",
    4: "the request lacked a required parameter", 5: "a parameter was out of range",
    6: "the request had too many arguments", 7: "the device did not recognise a parameter",
    8: "the request carried unexpected data",
}
ABORT_REASONS = {
    0: "the device aborted the request", 1: "the device is out of buffer space",
    2: "the device says the request is invalid in its current state",
    3: "a higher-priority task preempted the request", 4: "the device does not support segmentation",
    5: "the device reported a security error", 6: "the device has insufficient security",
    7: "the window size was out of range", 8: "the device exceeded its reply time",
    9: "the device is out of resources", 10: "the device's transaction state timed out",
    11: "the request was longer than the device accepts",
}
ERROR_CODES = {
    3: "the device is busy", 9: "the property has the wrong data type",
    25: "the device denied access to that property", 27: "the device denied the request",
    31: "the device has no such object", 32: "the device has no such property",
}

STATUS_FLAGS = ("in-alarm", "fault", "overridden", "out-of-service")

MISSING_PEER = ("the device instance {device} did not answer a Who-Is at {host}:{port}; "
                "check that the device is on the network and the instance number is right")


class BacnetError(ValueError):
    """A request or reply that cannot be built or understood. The message is a sentence for the operator."""


class _Unsupported(Exception):
    """ReadPropertyMultiple was refused, so the poll falls back to one ReadProperty per object."""


def parse_address(text):
    """(host, port) from 'host:port', defaulting the port to 47808. Raises BacnetError with a sentence."""
    if not isinstance(text, str) or not text.strip():
        raise BacnetError("a BACnet address must be host:port, such as 10.0.0.5:47808")
    text = text.strip()
    host, sep, port = text.rpartition(":")
    if not sep:
        host, port = text, str(DEFAULT_PORT)
    if not host or host.startswith("[") or host.endswith("]"):
        raise BacnetError("a BACnet address must be host:port, such as 10.0.0.5:47808")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise BacnetError("the port in a BACnet address must be 1 to 65535, such as 10.0.0.5:47808")
    return host, int(port)


def _length_code(n):
    """(LVT nibble, bytes after the tag) for a BACnet length."""
    if n <= 4:
        return n, b""
    if n <= 253:
        return 5, bytes([n])
    if n <= 0xFFFF:
        return 5, b"\xfe" + n.to_bytes(2, "big")
    return 5, b"\xff" + n.to_bytes(4, "big")


def _header(number, context, lvt):
    """The tag header. Context tags carry bit 3; numbers above 14 take an extra octet."""
    flag = 0x08 if context else 0
    if number < 15:
        return bytes([(number << 4) | flag | lvt])
    return bytes([0xF0 | flag | lvt, number])


def _app(number, payload):
    """One application-tagged value."""
    lvt, extra = _length_code(len(payload))
    return _header(number, False, lvt) + extra + payload


def _context(number, payload):
    """One context-tagged primitive value."""
    lvt, extra = _length_code(len(payload))
    return _header(number, True, lvt) + extra + payload


def _context_unsigned(number, value):
    """A context-tagged unsigned value in its shortest form."""
    width = 1 if value < 0x100 else 2 if value < 0x10000 else 3 if value < 0x1000000 else 4
    return _context(number, value.to_bytes(width, "big"))


def _open(number):
    return _header(number, True, 6)


def _close(number):
    return _header(number, True, 7)


def _app_unsigned(value):
    return _app(APP_UNSIGNED, value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big"))


def _app_enumerated(value):
    return _app(APP_ENUMERATED, value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big"))


def _app_real(value):
    return _app(APP_REAL, struct.pack(">f", value))


def _app_text(value):
    """A character string with the UTF-8 charset octet (0)."""
    return _app(APP_CHARACTER, b"\x00" + value.encode("utf-8"))


def _object_id(object_type, instance):
    return ((object_type << 22) | instance).to_bytes(4, "big")


def _app_object_id(object_type, instance):
    return _app(APP_OBJECT_ID, _object_id(object_type, instance))


def _npdu(apdu):
    """A BACnet/IP NPDU for a local (unicast) application message."""
    return b"\x01\x00" + apdu


def _bvlc(npdu):
    """A BACnet/IP frame: BVLC header, then the NPDU."""
    return bytes([BVLC_START, BVLC_BACNET_IP]) + (len(npdu) + 4).to_bytes(2, "big") + npdu


def encode_whois():
    """A unicast Who-Is. The I-Am that answers identifies the device and binds its address."""
    return _bvlc(_npdu(bytes([UNCONFIRMED_REQUEST, SERVICE_WHO_IS])))


def encode_rpm(invoke, specs):
    """A ReadPropertyMultiple request for [(object type, instance, [property ids])]."""
    apdu = bytes([CONFIRMED_REQUEST, 0x05, invoke, SERVICE_READ_PROPERTY_MULTIPLE])
    for object_type, instance, properties in specs:
        apdu += _context(0, _object_id(object_type, instance)) + _open(1)
        apdu += b"".join(_context_unsigned(0, prop) for prop in properties)
        apdu += _close(1)
    return _bvlc(_npdu(apdu))


def encode_read_property(invoke, object_type, instance, prop):
    """One ReadProperty request, the fallback for a device that refuses ReadPropertyMultiple."""
    apdu = (bytes([CONFIRMED_REQUEST, 0x05, invoke, SERVICE_READ_PROPERTY])
            + _context(0, _object_id(object_type, instance)) + _context_unsigned(1, prop))
    return _bvlc(_npdu(apdu))


def encode_segment_ack(invoke, sequence, window=1):
    """A SegmentACK from this client, accepting one segment at a time."""
    return _bvlc(_npdu(bytes([SEGMENT_ACK, invoke, sequence, window])))


def _read_tag(data, pos):
    """(tag number, is context, LVT, position after the header)."""
    if pos >= len(data):
        raise BacnetError("the reply ends where a tag was expected")
    first = data[pos]
    pos += 1
    number, context, lvt = first >> 4, bool(first & 0x08), first & 0x07
    if number == 0x0F:
        if pos >= len(data):
            raise BacnetError("the reply ends in the middle of a tag number")
        number = data[pos]
        pos += 1
        if number == 0xFF:
            raise BacnetError("the reply uses a tag number this reader does not support")
    return number, context, lvt, pos


def _content(data, pos, lvt):
    """(content bytes, new position) for a primitive tag whose header ends at pos."""
    if lvt <= 4:
        length = lvt
    elif lvt == 5:
        if pos >= len(data):
            raise BacnetError("the reply ends where an extended length was expected")
        first = data[pos]
        pos += 1
        if first < 254:
            length = first
        elif first == 254:
            if pos + 2 > len(data):
                raise BacnetError("the reply ends in the middle of an extended length")
            length, pos = int.from_bytes(data[pos:pos + 2], "big"), pos + 2
        else:
            if pos + 4 > len(data):
                raise BacnetError("the reply ends in the middle of an extended length")
            length, pos = int.from_bytes(data[pos:pos + 4], "big"), pos + 4
    else:
        raise BacnetError("the reply has an opening or closing tag where a value was expected")
    end = pos + length
    if end > len(data):
        raise BacnetError(f"the reply claims {length} bytes but only {len(data) - pos} remain")
    return data[pos:end], end


def _decode_application(number, raw):
    """A Python value from one application-tagged value."""
    if number == APP_NULL:
        return None
    if number == APP_BOOLEAN:
        if len(raw) != 1:
            raise BacnetError("a true/false value in the reply is not one byte")
        return bool(raw[0])
    if number in (APP_UNSIGNED, APP_ENUMERATED):
        if len(raw) > 4:
            raise BacnetError("a whole number in the reply is wider than 32 bits")
        return int.from_bytes(raw, "big") if raw else 0
    if number == APP_SIGNED:
        if len(raw) > 4:
            raise BacnetError("a signed number in the reply is wider than 32 bits")
        return int.from_bytes(raw, "big", signed=True) if raw else 0
    if number == APP_REAL:
        if len(raw) != 4:
            raise BacnetError("a real value in the reply is not four bytes")
        return struct.unpack(">f", raw)[0]
    if number == APP_DOUBLE:
        if len(raw) != 8:
            raise BacnetError("a double value in the reply is not eight bytes")
        return struct.unpack(">d", raw)[0]
    if number == APP_OCTETS:
        return raw
    if number == APP_CHARACTER:
        if not raw:
            raise BacnetError("a text value in the reply has no charset")
        charset, body = raw[0], raw[1:]
        try:
            return body.decode("utf-8" if charset == 0 else "latin-1")
        except UnicodeDecodeError:
            raise BacnetError("a text value in the reply is not valid UTF-8") from None
    if number == APP_BITS:
        if not raw:
            raise BacnetError("a bit string in the reply is empty")
        unused = raw[0]
        if unused > 7:
            raise BacnetError("a bit string in the reply has more than seven unused bits")
        mask, bit = 0, 0
        for byte in raw[1:]:
            for shift in range(8):
                if bit >= (len(raw) - 1) * 8 - unused:
                    break
                if byte & (0x80 >> shift):
                    mask |= 1 << bit
                bit += 1
        return mask
    if number == APP_OBJECT_ID:
        if len(raw) != 4:
            raise BacnetError("an object identifier in the reply is not four bytes")
        value = int.from_bytes(raw, "big")
        return (value >> 22, value & MAX_INSTANCE)
    if number in (APP_DATE, APP_TIME):
        return raw
    raise BacnetError(f"the reply has a value of unsupported type {number}")


def _app_value(data, pos):
    """(Python value, new position) from an application-tagged value at pos."""
    number, context, lvt, pos = _read_tag(data, pos)
    if context:
        raise BacnetError("the reply has a context tag where a value was expected")
    raw, pos = _content(data, pos, lvt)
    return _decode_application(number, raw), pos


def _read_error_body(data, pos):
    """(error class, error code) from an Error PDU or a property-access error."""
    out = []
    for _ in range(2):
        number, context, lvt, p = _read_tag(data, pos)
        if context:
            raw, pos = _content(data, p, lvt)
            out.append(int.from_bytes(raw, "big") if raw else 0)
        else:
            value, pos = _app_value(data, pos)
            out.append(value if isinstance(value, int) else 0)
    return out[0], out[1]


def decode_datagram(data):
    """The APDU of a BACnet/IP datagram as a dict. Raises BacnetError with a sentence when it is not one.

    The kinds are `who-is`, `i-am`, `confirmed`, `complex`, `simple`, `error`, `reject`, `abort` and
    `segment-ack`. A segmented ComplexACK carries `segmented`, `more`, `seq`, `window`, `service` and
    its own chunk in `body`; an unsegmented one has `service` and the whole `body`.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise BacnetError("the reply is not bytes")
    data = bytes(data)
    if len(data) < 6 or data[0] != BVLC_START:
        raise BacnetError("the reply is not a BACnet/IP message")
    if data[1] not in (BVLC_BACNET_IP, BVLC_FORWARDED):
        raise BacnetError(f"the reply has BVLC function 0x{data[1]:02x}, not BACnet/IP")
    length = int.from_bytes(data[2:4], "big")
    if length != len(data):
        raise BacnetError("the reply's BVLC length does not match the datagram")
    npdu = data[4:]
    if len(npdu) < 2 or npdu[0] != 1:
        raise BacnetError("the reply is not a BACnet network message")
    control, pos = npdu[1], 2
    if control & 0x20:  # destination specifier
        if pos + 3 > len(npdu):
            raise BacnetError("the reply ends inside its destinations")
        dlen = npdu[pos + 2]
        pos += 3 + dlen
    if control & 0x10:  # source specifier
        if pos + 3 > len(npdu):
            raise BacnetError("the reply ends inside its source address")
        slen = npdu[pos + 2]
        pos += 3 + slen
    if control & 0x80:
        raise BacnetError("the reply is a network-layer message, not an application reply")
    if pos >= len(npdu):
        raise BacnetError("the reply has no application message")
    apdu = npdu[pos:]
    pdu, flags = apdu[0] >> 4, apdu[0] & 0x0F
    if pdu == UNCONFIRMED_REQUEST >> 4:
        if len(apdu) < 2:
            raise BacnetError("the reply ends where a service was expected")
        service = apdu[1]
        if service == SERVICE_I_AM:
            device, pos = _app_value(apdu, 2)
            if not isinstance(device, tuple) or device[0] != 8:
                raise BacnetError("an I-Am reply does not name a device")
            return {"kind": "i-am", "device": device[1]}
        if service == SERVICE_WHO_IS:
            return {"kind": "who-is"}
        raise BacnetError(f"the reply is an unconfirmed service {service}, which DurianDB does not read")
    if pdu == CONFIRMED_REQUEST >> 4:
        if len(apdu) < 4:
            raise BacnetError("the reply ends inside a confirmed request")
        return {"kind": "confirmed", "invoke": apdu[2], "service": apdu[3], "body": apdu[4:]}
    if pdu == COMPLEX_ACK >> 4:
        segmented, more = bool(flags & 0x08), bool(flags & 0x04)
        if len(apdu) < 3:
            raise BacnetError("the reply ends inside a ComplexACK")
        invoke = apdu[1]
        if segmented:
            if len(apdu) < 5:
                raise BacnetError("the reply ends inside a segmented ComplexACK")
            return {"kind": "complex", "segmented": True, "more": more, "invoke": invoke,
                    "seq": apdu[2], "window": apdu[3], "service": apdu[4], "body": apdu[5:]}
        return {"kind": "complex", "segmented": False, "more": False, "invoke": invoke,
                "service": apdu[2], "body": apdu[3:]}
    if pdu == SIMPLE_ACK >> 4:
        if len(apdu) < 3:
            raise BacnetError("the reply ends inside a SimpleACK")
        return {"kind": "simple", "invoke": apdu[1], "service": apdu[2]}
    if pdu == SEGMENT_ACK >> 4:
        if len(apdu) < 4:
            raise BacnetError("the reply ends inside a SegmentACK")
        return {"kind": "segment-ack", "invoke": apdu[1], "seq": apdu[2], "window": apdu[3]}
    if pdu == ERROR_PDU >> 4:
        if len(apdu) < 3:
            raise BacnetError("the reply ends inside an error message")
        return {"kind": "error", "invoke": apdu[1], "service": apdu[2], "body": apdu[3:]}
    if pdu in (REJECT_PDU >> 4, ABORT_PDU >> 4):
        if len(apdu) < 3:
            raise BacnetError("the reply ends inside a rejection or abort")
        return {"kind": "reject" if pdu == REJECT_PDU >> 4 else "abort",
                "invoke": apdu[1], "reason": apdu[2], "server": bool(flags & 0x01)}
    raise BacnetError(f"the reply has message type {pdu}, which DurianDB does not read")


def decode_rpm_ack(body):
    """{(object type, instance, property id): ('value', v) or ('error', sentence)} from an RPM ACK body."""
    out, pos = {}, 0
    number, context, lvt, after = _read_tag(body, pos)
    outer = context and number == 0 and lvt == 6  # some devices wrap the list in [0] ... [0]
    if outer:
        pos = after
    while pos < len(body):
        number, context, lvt, after = _read_tag(body, pos)
        if outer and context and number == 0 and lvt == 7:
            pos = after
            break
        if not (context and number == 0 and lvt == 4):
            raise BacnetError("the reply is not a ReadPropertyMultiple acknowledgement")
        object_type, instance = int.from_bytes(body[after:after + 4], "big") >> 22, \
            int.from_bytes(body[after:after + 4], "big") & MAX_INSTANCE
        pos = after + 4
        number, context, lvt, after = _read_tag(body, pos)
        if not (context and number == 1 and lvt == 6):
            raise BacnetError("a ReadPropertyMultiple result has no property list")
        pos = after
        while True:
            number, context, lvt, after = _read_tag(body, pos)
            if context and number == 1 and lvt == 7:
                pos = after
                break
            if not (context and number == 2):
                raise BacnetError("a ReadPropertyMultiple result has no property identifier")
            raw, pos = _content(body, after, lvt)
            prop = int.from_bytes(raw, "big") if raw else 0
            number, context, lvt, after = _read_tag(body, pos)
            if context and number == 3 and lvt <= 4:  # an array index this driver never asks for
                _, pos = _content(body, after, lvt)
                number, context, lvt, after = _read_tag(body, pos)
            if not (context and lvt == 6 and number in (4, 5)):
                raise BacnetError("a ReadPropertyMultiple result has neither a value nor an error")
            pos = after
            if number == 4:
                value, pos = _app_value(body, pos)
                number, context, lvt, after = _read_tag(body, pos)
                if not (context and number == 4 and lvt == 7):
                    raise BacnetError("a ReadPropertyMultiple value is not closed")
                pos = after
                out[(object_type, instance, prop)] = ("value", value)
            else:
                error_class, error_code = _read_error_body(body, pos)
                pos = _skip_close(body, pos, 5)
                out[(object_type, instance, prop)] = (
                    "error", _property_error(object_type, instance, prop, error_class, error_code))
    return out


def _skip_close(data, pos, number):
    """Skip the error body up to and including the closing tag `number`, returning the new position."""
    while pos < len(data):
        n, context, lvt, after = _read_tag(data, pos)
        if context and n == number and lvt == 7:
            return after
        if lvt in (6, 7):
            pos = after
        else:
            _, pos = _content(data, after, lvt)
    raise BacnetError("the reply ends before a property error is closed")


def _property_error(object_type, instance, prop, error_class, error_code):
    what = ERROR_CODES.get(error_code, f"error code {error_code}")
    kind = OBJECT_NAMES.get(object_type, f"object type {object_type}")
    name = PROPERTY_NAMES.get(prop, f"property {prop}")
    return f"the device reported {what} for {kind} {instance} {name}"


def _status_text(mask):
    found = [name for bit, name in enumerate(STATUS_FLAGS) if mask & (1 << bit)]
    return ", ".join(found) if found else "normal"


def _text(value, where):
    """Printable text no longer than 255 characters, or BacnetError with a sentence."""
    if isinstance(value, str):
        text = value
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float) and math.isfinite(value):
        text = str(int(value)) if value.is_integer() else str(value)
    else:
        raise BacnetError(f"the device returned a value for {where} that cannot be shown as text")
    if not 1 <= len(text) <= 255 or not text.isprintable():
        raise BacnetError(f"the text for {where} is empty, longer than 255 characters, or has control characters")
    return text


def _kind(value):
    if isinstance(value, str):
        return "text"
    if isinstance(value, bool):
        return "a true/false value"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, tuple):
        return "an object identifier"
    if isinstance(value, bytes):
        return "a byte string"
    return "a value of an unknown type"


def _point_value(point, prop, value, object_type, instance):
    """The value a point stores from one property. Raises BacnetError with a sentence when it cannot."""
    kind = OBJECT_NAMES.get(object_type, f"object type {object_type}")
    where = f"{kind} {instance} {PROPERTY_NAMES.get(prop, prop)}"
    if prop == "objectName":
        if not isinstance(value, str):
            raise BacnetError(f"the device returned {_kind(value)} for the name of {where}")
        if not point.text:
            raise BacnetError(f"the name of {where} is text, so make this point a text point")
        return _text(value, where)
    if prop == "statusFlags":
        if not isinstance(value, int) or isinstance(value, bool):
            raise BacnetError(f"the device returned {_kind(value)} for the status flags of {where}")
        return _status_text(value) if point.text else float(value)
    if prop == "units":
        if not isinstance(value, int) or isinstance(value, bool):
            raise BacnetError(f"the device returned {_kind(value)} for the units of {where}")
        return str(value) if point.text else float(value)
    if value is None:
        raise BacnetError(f"the device returned no value for {where}")
    if point.text:
        return _text(value, where)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise BacnetError(f"the device returned a number that is not finite for {where}")
        return number
    raise BacnetError(f"the device returned {_kind(value)} for {where}, but this point is a number")


class BacnetSource(PolledSource):
    """Read configured objects from one BACnet/IP device, one bounded request cycle per poll."""

    def __init__(self, name, cfg):
        super().__init__(name, cfg.get("period", 5.0), cfg.get("backoff_scale", 1.5), cfg.get("backoff_max", 300.0))
        section = cfg.get("bacnet") or {}
        self.host, self.port = parse_address(section.get("address"))
        self.device = section.get("device")
        self.timeout = float(cfg.get("timeout", 2.0))
        self._invoke = 0
        self._peer = None

    def _next_invoke(self):
        """A fresh invoke id, so a late reply to an earlier request cannot be mistaken for this one."""
        self._invoke = self._invoke % 255 + 1
        return self._invoke

    def failure_text(self, e):
        """BACnet failures are already sentences; sentence() also strips any errno tag a wrapped OS error brings."""
        return sentence(e)

    def _specs(self):
        """([(point, object type, instance, property id)], rejection sentences for bad point config)."""
        specs, rejected = [], []
        for p in self.points.values():
            section = p.meta.get("bacnet")
            object_type = OBJECT_TYPES.get(section.get("object_type")) if isinstance(section, dict) else None
            instance = section.get("instance") if isinstance(section, dict) else None
            prop = PROPERTIES.get(section.get("property")) if isinstance(section, dict) else None
            if object_type is None or not isinstance(instance, int) or isinstance(instance, bool) \
                    or not 0 <= instance <= MAX_INSTANCE or prop is None:
                rejected.append(f"{p.name}: the point's bacnet object_type, instance or property is not valid; edit the point")
                continue
            specs.append((p, object_type, instance, prop))
        return specs, rejected

    def fetch(self):
        specs, rejected = self._specs()
        if not specs:
            return {}, rejected
        by_object, order = {}, []
        for p, object_type, instance, prop in specs:
            key = (object_type, instance)
            if key not in by_object:
                if len(order) >= MAX_OBJECTS:
                    rejected.append(f"{p.name}: only {MAX_OBJECTS} objects are read per poll, so this point's object "
                                    "was not read; split the points across sources")
                    continue
                by_object[key] = []
                order.append(key)
            by_object[key].append((p, prop))

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                peer = self._bind(sock)
                try:
                    results = self._read_multiple(sock, peer, order, by_object)
                except _Unsupported:
                    results = self._read_each(sock, peer, order, by_object)
        except Exception:
            self._peer = None  # the next poll binds again, in case the device moved or was rebooted
            raise

        values = {}
        for object_type, instance in order:
            for p, prop in by_object[(object_type, instance)]:
                item = results.get((object_type, instance, prop))
                if item is None:
                    kind = OBJECT_NAMES.get(object_type, f"object type {object_type}")
                    rejected.append(f"{p.name}: the device did not return {PROPERTY_NAMES.get(prop, prop)} "
                                    f"for {kind} {instance}")
                    continue
                state, value = item
                if state == "error":
                    rejected.append(f"{p.name}: {value}")
                    continue
                try:
                    values[p.name] = _point_value(p, PROPERTY_NAMES.get(prop), value, object_type, instance)
                except BacnetError as e:
                    rejected.append(f"{p.name}: {sentence(e)}")
        return values, rejected

    def _bind(self, sock):
        """The device's address from its I-Am, remembered until a poll fails."""
        if self._peer is not None:
            return self._peer
        try:
            self._send(sock, (self.host, self.port), encode_whois())
        except BacnetError:
            raise
        deadline = time.monotonic() + self.timeout
        seen = []
        for _ in range(MAX_DEVICES):
            try:
                msg, addr = self._recv_until(
                    sock, deadline, lambda m: m["kind"] == "i-am", "a Who-Is reply",
                    MISSING_PEER.format(device=self.device, host=self.host, port=self.port))
            except BacnetError as e:
                if seen:
                    raise BacnetError(
                        f"the device instance {self.device} did not answer a Who-Is at {self.host}:{self.port} "
                        f"(instance(s) {', '.join(seen)} did); check the instance number") from None
                raise e
            seen.append(str(msg["device"]))
            if msg["device"] == self.device:
                self._peer = addr
                return addr
        raise BacnetError(f"more than {MAX_DEVICES} devices answered the Who-Is at {self.host}:{self.port}; "
                          "give the address of the device itself")

    def _read_multiple(self, sock, peer, order, by_object):
        """Read every object with one ReadPropertyMultiple. Raises _Unsupported when the device refuses it."""
        invoke = self._next_invoke()
        specs = [(object_type, instance, list(dict.fromkeys(prop for _, prop in by_object[(object_type, instance)])))
                 for object_type, instance in order]
        msg = self._request(sock, peer, encode_rpm(invoke, specs), invoke, "a ReadPropertyMultiple reply")
        if msg["kind"] == "complex":
            if msg["service"] != SERVICE_READ_PROPERTY_MULTIPLE:
                raise BacnetError(f"the device answered service {msg['service']} to a ReadPropertyMultiple request")
            try:
                return decode_rpm_ack(msg["body"])
            except BacnetError as e:
                raise BacnetError(f"the ReadPropertyMultiple reply could not be read: {sentence(e)}") from None
        if msg["kind"] in ("reject", "error"):
            raise _Unsupported()
        if msg["kind"] == "abort":
            raise BacnetError(f"the device aborted the ReadPropertyMultiple request "
                              f"({ABORT_REASONS.get(msg['reason'], 'abort reason ' + str(msg['reason']))})")
        raise BacnetError("the device answered a ReadPropertyMultiple request with a message that carries no values")

    def _read_each(self, sock, peer, order, by_object):
        """One ReadProperty per object, for a device that refused ReadPropertyMultiple."""
        results = {}
        for object_type, instance in order:
            for _, prop in by_object[(object_type, instance)]:
                key = (object_type, instance, prop)
                if key in results:
                    continue
                invoke = self._next_invoke()
                msg = self._request(sock, peer, encode_read_property(invoke, object_type, instance, prop), invoke,
                                    "a ReadProperty reply")
                if msg["kind"] == "complex" and msg["service"] == SERVICE_READ_PROPERTY:
                    try:
                        value, _ = _app_value(msg["body"], 0)
                    except BacnetError as e:
                        results[key] = ("error", sentence(e))
                    else:
                        results[key] = ("value", value)
                elif msg["kind"] == "error":
                    error_class, error_code = _read_error_body(msg["body"], 0)
                    results[key] = ("error", _property_error(object_type, instance, prop, error_class, error_code))
                elif msg["kind"] == "reject":
                    results[key] = ("error", f"{REJECT_REASONS.get(msg['reason'], 'the device rejected the request')}"
                                             f" for {OBJECT_NAMES.get(object_type, object_type)} {instance} "
                                             f"{PROPERTY_NAMES.get(prop, prop)}")
                elif msg["kind"] == "abort":
                    results[key] = ("error", f"{ABORT_REASONS.get(msg['reason'], 'the device aborted the request')}"
                                             f" for {OBJECT_NAMES.get(object_type, object_type)} {instance} "
                                             f"{PROPERTY_NAMES.get(prop, prop)}")
                elif msg["kind"] == "simple":
                    results[key] = ("error", "the device acknowledged the read without returning a value")
                else:
                    results[key] = ("error", "the device answered the read with a message that carries no value")
        return results

    def _request(self, sock, peer, packet, invoke, what):
        """Send one request and return the matching reply, reassembling a segmented ComplexACK."""
        deadline = time.monotonic() + self.timeout
        self._send(sock, peer, packet)
        msg, _ = self._recv_until(sock, deadline, lambda m: m.get("invoke") == invoke, what,
                                  f"the device at {self.host}:{self.port} did not answer request {invoke} "
                                  f"within {self.timeout:g}s", invoke=invoke)
        if msg["kind"] != "complex" or not msg.get("segmented"):
            return msg
        parts = [msg["body"]]
        for _ in range(MAX_SEGMENTS):
            self._send(sock, peer, encode_segment_ack(invoke, msg["seq"], 1))
            if not msg["more"]:
                return {**msg, "body": b"".join(parts), "segmented": False}
            want = (msg["seq"] + 1) % 256
            msg, _ = self._recv_until(sock, deadline, lambda m: m.get("invoke") == invoke,
                                      "the next segment",
                                      f"the device stopped sending a segmented reply to request {invoke} "
                                      f"within {self.timeout:g}s", invoke=invoke)
            if msg["kind"] != "complex" or not msg.get("segmented"):
                raise BacnetError(f"the device broke a segmented reply to request {invoke} with a message "
                                  "that is not another segment")
            if msg["seq"] != want:
                raise BacnetError(f"the device sent segment {msg['seq']} of request {invoke} while segment "
                                  f"{want} was expected")
            parts.append(msg["body"])
        raise BacnetError(f"the device sent more than {MAX_SEGMENTS} segments for request {invoke}; "
                          "DurianDB stopped reading it")

    def _recv_until(self, sock, deadline, match, what, timeout_sentence, invoke=None):
        """(message, address) for the first datagram that matches, ignoring malformed or unrelated ones."""
        ignored, last_invoke = 0, None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if invoke is not None and last_invoke is not None:
                    raise BacnetError(f"the device answered request {last_invoke} while request {invoke} was sent; "
                                      f"another device may be answering {self.host}:{self.port}")
                raise BacnetError(timeout_sentence)
            sock.settimeout(remaining)
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as e:
                raise BacnetError(f"cannot read from {self.host}:{self.port}: {sentence(e)}") from None
            try:
                msg = decode_datagram(data)
            except BacnetError:
                msg = None
            if msg is not None and match(msg):
                return msg, addr
            if msg is not None and msg.get("invoke") is not None:
                last_invoke = msg["invoke"]
            ignored += 1
            if ignored >= MAX_IGNORED:
                raise BacnetError(f"the device at {self.host}:{self.port} kept sending datagrams that are not "
                                  f"{what}")

    def _send(self, sock, target, packet):
        try:
            sock.sendto(packet, target)
        except socket.gaierror:
            raise BacnetError(f"cannot resolve the BACnet host name '{self.host}'") from None
        except OSError as e:
            raise BacnetError(f"cannot send to {self.host}:{self.port}: {sentence(e)}") from None
