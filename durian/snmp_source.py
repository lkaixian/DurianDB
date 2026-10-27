"""SNMP v2c data source: exact-OID GETs and a bounded GETNEXT walk, decoded by a small BER reader kept here.

SNMP is the oldest way plant equipment exposes readings, and a driver for it needs no dependency: a v2c
request is a BER-encoded datagram, which this module builds and parses with the standard library alone.
A point names either one OID in its `snmp.address` field, read with GET, or a numeric subtree in its
`snmp.walk` field, read with GETNEXT until the answer leaves the subtree. Either field keeps the shape of
a Modbus point's `modbus.address`, so the config editor and the point importer need no new form.

A walk has no table model: one point still holds one value, so it keeps the first varbind under the
subtree that its datatype can store (a number point takes the first numeric leaf, a text point any
scalar) and reports the point when none fits. A walk stops when the device answers endOfMibView, steps
outside the subtree, stops advancing, or reaches WALK_LIMIT varbinds, so a device with a huge tree cannot
hold a tick open; reaching the cap is a sentence on the source, not a silent truncation. A device that
ends a subtree early is normal, not an error. A walk that times out mid-way keeps the values already read
and reports the timeout the same way.

A GET carries every configured exact OID in one request, and each walk point costs one walk of one
request per step. A timeout is retried once; any transport, protocol or request-id failure becomes the
source's health sentence instead of an exception escaping into the engine. Values stay typed: numeric
points take INTEGER, Counter32, Gauge32 and TimeTicks, text points take an OCTET STRING (or any other
value rendered as text). An OID the device refuses, does not return, or answers with a type this point
cannot use is reported per point and counted, while the rest of the poll still updates.
"""
import socket

from .polled import PolledSource

MAX_VARBINDS = 10_000  # a reply is one UDP datagram; this only stops a hostile length from looping
WALK_LIMIT = 1000  # varbinds one walk point reads per poll, so a huge subtree cannot hold a tick open

INTEGER = 0x02
OCTET_STRING = 0x04
NULL = 0x05
OBJECT_ID = 0x06
SEQUENCE = 0x30
IP_ADDRESS = 0x40
COUNTER32 = 0x41
GAUGE32 = 0x42
TIMETICKS = 0x43
GET_REQUEST = 0xA0
GET_NEXT = 0xA1
GET_RESPONSE = 0xA2

NO_SUCH_OBJECT = 0x80
NO_SUCH_INSTANCE = 0x81
END_OF_MIB_VIEW = 0x82

SUPPORTED_VALUES = (INTEGER, OCTET_STRING, OBJECT_ID, IP_ADDRESS, COUNTER32, GAUGE32, TIMETICKS)

# Error statuses a GET can come back with. Each value is the sentence an operator reads.
ERRORS = {
    0: "no error",
    1: "the answer would be too large for the device",
    2: "the device has no object at that OID",
    3: "the device rejected the value",
    4: "that object is read-only",
    5: "the device reported a general error",
    6: "access to that object was denied",
    7: "the object has a different type than the request",
    8: "the object has a different length than the request",
    9: "the object is encoded differently than the request",
    10: "the value is wrong for that object",
    11: "that object cannot be created",
    12: "the value is inconsistent with that object",
    13: "the device is out of resources",
    14: "the device could not apply the change",
    15: "the device could not undo the change",
    16: "authorization failed for that object",
    17: "that object is not writable",
    18: "the object name is inconsistent",
}

MISSING = {
    NO_SUCH_OBJECT: "the device has no such object",
    NO_SUCH_INSTANCE: "the device has no instance of that object",
    END_OF_MIB_VIEW: "the device says that object is past the end of its MIB",
}


class SnmpError(ValueError):
    """A request or reply that cannot be built or understood. The message is a sentence for the operator."""


def _length(n):
    """The BER length header for n content bytes."""
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag, payload):
    """One BER tag, length and value."""
    return bytes([tag]) + _length(len(payload)) + payload


def _int(n):
    """Minimal two's-complement content for a BER INTEGER."""
    if n == 0:
        return b"\x00"
    return n.to_bytes((n.bit_length() + 8) // 8, "big", signed=True)


def _base128(n):
    """One OID sub-identifier in base-128, continuation bits on all but the last byte."""
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def encode_oid(text):
    """The content bytes of a dotted OID. Raises SnmpError with a sentence when it is not one."""
    if not isinstance(text, str):
        raise SnmpError("an OID must be text")
    parts = text.strip().split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts):
        raise SnmpError(f"'{text}' is not a dotted OID such as 1.3.6.1.2.1.1.5.0")
    nums = [int(p) for p in parts]
    if nums[0] > 2 or (nums[0] < 2 and nums[1] > 39):
        raise SnmpError(f"'{text}' is not a dotted OID such as 1.3.6.1.2.1.1.5.0")
    if any(n > 0xFFFFFFFF for n in nums):
        raise SnmpError(f"'{text}' has an OID component wider than 32 bits")
    out = bytearray(_base128(nums[0] * 40 + nums[1]))
    for n in nums[2:]:
        out.extend(_base128(n))
    return bytes(out)


def decode_oid(raw):
    """A dotted OID from its content bytes. Raises SnmpError with a sentence when the bytes stop mid-component."""
    if not raw:
        raise SnmpError("an OID in the reply is empty")
    nums, value = [], 0
    for b in raw:
        value = (value << 7) | (b & 0x7F)
        if value > 0xFFFFFFFF:
            raise SnmpError("an OID in the reply has a component wider than 32 bits")
        if not b & 0x80:
            nums.append(value)
            value = 0
    if raw[-1] & 0x80:
        raise SnmpError("an OID in the reply ends in the middle of a component")
    if not nums:
        raise SnmpError("an OID in the reply is empty")
    first = nums[0]
    if first < 40:
        head = [0, first]
    elif first < 80:
        head = [1, first - 40]
    else:
        head = [2, first - 80]
    return ".".join(str(n) for n in head + nums[1:])


def _read_length(data, pos):
    """(length, new position) of a BER length header."""
    if pos >= len(data):
        raise SnmpError("the reply ends where a length was expected")
    b = data[pos]
    pos += 1
    if b < 0x80:
        return b, pos
    count = b & 0x7F
    if count == 0:
        raise SnmpError("the reply uses an indefinite length, which SNMP does not allow")
    if count > 4:
        raise SnmpError("a length in the reply is too large")
    if pos + count > len(data):
        raise SnmpError("the reply ends in the middle of a length")
    return int.from_bytes(data[pos:pos + count], "big"), pos + count


def _read_tlv(data, pos, what):
    """(tag, content, new position) of one BER value, saying which field failed when it does."""
    if pos >= len(data):
        raise SnmpError(f"the reply ends where {what} was expected")
    tag = data[pos]
    length, pos = _read_length(data, pos + 1)
    end = pos + length
    if end > len(data):
        raise SnmpError(f"{what} claims {length} bytes but the reply has {len(data) - pos}")
    return tag, data[pos:end], end


def _int_value(raw, what):
    """A signed BER INTEGER."""
    if not raw:
        raise SnmpError(f"{what} in the reply has no bytes")
    if len(raw) > 8:
        raise SnmpError(f"{what} in the reply is wider than 64 bits")
    return int.from_bytes(raw, "big", signed=True)


def _uint_value(raw, what):
    """An unsigned counter, gauge or clock value."""
    if not raw:
        return 0
    if len(raw) > 8:
        raise SnmpError(f"{what} in the reply is wider than 64 bits")
    return int.from_bytes(raw, "big")


def _value(tag, raw):
    """A typed value from a varbind. SEQUENCE, exception tags and unknown tags become None; their tag is kept."""
    if tag == INTEGER:
        return _int_value(raw, "an integer value")
    if tag == OCTET_STRING:
        return raw
    if tag == NULL:
        return None
    if tag == OBJECT_ID:
        return decode_oid(raw)
    if tag == IP_ADDRESS:
        if len(raw) != 4:
            raise SnmpError(f"an IpAddress value is {len(raw)} bytes instead of 4")
        return ".".join(str(b) for b in raw)
    if tag in (COUNTER32, GAUGE32, TIMETICKS):
        return _uint_value(raw, "a counter value")
    return None


def _varbinds(raw):
    """[(oid, tag, value)] from a variable-bindings sequence."""
    out, pos = [], 0
    while pos < len(raw):
        tag, body, pos = _read_tlv(raw, pos, "a variable binding")
        if tag != SEQUENCE:
            raise SnmpError("a variable binding in the reply is not a sequence")
        p = 0
        tag, oid_raw, p = _read_tlv(body, p, "an OID")
        if tag != OBJECT_ID:
            raise SnmpError("a variable binding in the reply has no OID")
        oid = decode_oid(oid_raw)
        tag, value_raw, p = _read_tlv(body, p, "a value")
        out.append((oid, tag, _value(tag, value_raw)))
        if len(out) > MAX_VARBINDS:
            raise SnmpError(f"the reply has more than {MAX_VARBINDS} variable bindings")
    return out


def decode_response(data):
    """(request id, error status, error index, binds) from a GET response datagram.

    Raises SnmpError with a sentence when the bytes are not an SNMP v2c response. A value this module does
    not read, and a noSuchObject-style exception value, keeps its tag and a None value so one point can be
    reported without failing the whole poll.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise SnmpError("the reply is not bytes")
    data = bytes(data)
    tag, body, end = _read_tlv(data, 0, "the SNMP message")
    if tag != SEQUENCE:
        raise SnmpError("the reply is not an SNMP message")
    if end != len(data):
        raise SnmpError("the reply has bytes after the SNMP message")

    pos = 0
    tag, version_raw, pos = _read_tlv(body, pos, "the version")
    if tag != INTEGER:
        raise SnmpError("the reply has no SNMP version")
    version = _int_value(version_raw, "the version")
    if version != 1:
        raise SnmpError(f"the device speaks SNMP version {version}, but DurianDB reads version 2c")
    tag, _, pos = _read_tlv(body, pos, "the community")
    if tag != OCTET_STRING:
        raise SnmpError("the reply has no community")
    tag, pdu, pos = _read_tlv(body, pos, "the response")
    if pos != len(body):
        raise SnmpError("the SNMP message has unexpected extra fields")
    if tag != GET_RESPONSE:
        raise SnmpError(f"the device answered with a message of type 0x{tag:02x}, not a GET response")

    pos = 0
    tag, raw, pos = _read_tlv(pdu, pos, "the request id")
    if tag != INTEGER:
        raise SnmpError("the response has no request id")
    request_id = _int_value(raw, "the request id")
    tag, raw, pos = _read_tlv(pdu, pos, "the error status")
    if tag != INTEGER:
        raise SnmpError("the response has no error status")
    error_status = _int_value(raw, "the error status")
    tag, raw, pos = _read_tlv(pdu, pos, "the error index")
    if tag != INTEGER:
        raise SnmpError("the response has no error index")
    error_index = _int_value(raw, "the error index")
    tag, raw, pos = _read_tlv(pdu, pos, "the variable bindings")
    if tag != SEQUENCE:
        raise SnmpError("the response has no variable bindings")
    if pos != len(pdu):
        raise SnmpError("the response PDU has unexpected extra fields")
    return request_id, error_status, error_index, _varbinds(raw)


def _encode_request(request_id, community, tag, oids):
    """A complete SNMP v2c request datagram of the given PDU type for a list of dotted OIDs."""
    binds = b"".join(_tlv(SEQUENCE, _tlv(OBJECT_ID, encode_oid(o)) + _tlv(NULL, b"")) for o in oids)
    pdu = _tlv(tag, _tlv(INTEGER, _int(request_id)) + _tlv(INTEGER, _int(0)) + _tlv(INTEGER, _int(0))
               + _tlv(SEQUENCE, binds))
    return _tlv(SEQUENCE, _tlv(INTEGER, _int(1)) + _tlv(OCTET_STRING, community.encode("utf-8")) + pdu)


def encode_get(request_id, community, oids):
    """A complete SNMP v2c GET datagram for a list of dotted OIDs."""
    return _encode_request(request_id, community, GET_REQUEST, oids)


def encode_getnext(request_id, community, oid):
    """A complete SNMP v2c GETNEXT datagram asking for the OID that follows `oid`."""
    return _encode_request(request_id, community, GET_NEXT, [oid])


def _oid_key(oid):
    """A dotted OID as a tuple, so it compares in OID order and not in text order."""
    return tuple(int(n) for n in oid.split("."))


def _in_subtree(oid, base):
    """True when `oid` is the subtree root or a descendant of it."""
    return oid == base or oid.startswith(base + ".")


def _kind(tag):
    """What a value of this tag is, for a rejection sentence."""
    return {INTEGER: "a number", OCTET_STRING: "text", OBJECT_ID: "an object identifier", IP_ADDRESS: "an IP address",
            COUNTER32: "a counter", GAUGE32: "a gauge", TIMETICKS: "a clock value"}.get(tag, "a value of an unknown type")


def _text(value, oid):
    """A printable text value no longer than 255 characters, or SnmpError."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            raise SnmpError(f"the text for {oid} is not valid UTF-8") from None
    elif isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        raise SnmpError(f"the device returned a value for {oid} that cannot be shown as text")
    if not 1 <= len(value) <= 255 or not value.isprintable():
        raise SnmpError(f"the text for {oid} is empty, longer than 255 characters, or contains control characters")
    return value


def _point_value(point, oid, tag, value):
    """The value a point stores from one varbind. Raises SnmpError with a sentence when it cannot."""
    if tag in MISSING:
        raise SnmpError(f"{MISSING[tag]} at {oid}")
    if tag == NULL:
        raise SnmpError(f"the device returned no value for {oid}")
    if tag not in SUPPORTED_VALUES:
        raise SnmpError(f"the device returned a value of unsupported type 0x{tag:02x} for {oid}")
    if point.text:
        return _text(value, oid)
    if isinstance(value, int):
        return float(value)
    raise SnmpError(f"the device returned {_kind(tag)} for {oid}, but this point is a number")


class SnmpSource(PolledSource):
    """Read exact OIDs with SNMP v2c GET and subtrees with a bounded GETNEXT walk, one request at a time."""

    def __init__(self, name, cfg):
        super().__init__(name, cfg.get("period", 5.0), cfg.get("backoff_scale", 1.5), cfg.get("backoff_max", 300.0))
        self.host = cfg["host"]
        self.port = cfg.get("port", 161)
        self.community = cfg.get("community", "public")
        self.timeout = float(cfg.get("timeout", 2.0))
        self._request_id = 0

    def _next_id(self):
        """A fresh positive request id for the next datagram."""
        self._request_id = self._request_id % 0x7FFFFFFF + 1
        return self._request_id

    def failure_text(self, e):
        """SNMP failures are already sentences, so the operator never sees an exception class name."""
        return str(e)

    def fetch(self):
        wanted, walks = [], []
        for p in self.points.values():
            snmp = p.meta.get("snmp") or {}
            if isinstance(snmp.get("address"), str):
                wanted.append((p, snmp["address"]))
            elif isinstance(snmp.get("walk"), str):
                walks.append((p, snmp["walk"]))
        values, rejected = {}, []
        if wanted:
            oids = list(dict.fromkeys(oid for _, oid in wanted))  # one index per OID, however many points read it
            request_id = self._next_id()
            data = self._exchange(encode_get(request_id, self.community, oids))
            got_id, status, _, binds = decode_response(data)
            if got_id != request_id:
                raise SnmpError(f"the device answered request {got_id} while request {request_id} was sent; "
                                "another agent may be answering this address")
            if status:
                what = ERRORS.get(status, f"the device reported error {status}")
                rejected.extend(f"{p.name} ({oid}): {what}" for p, oid in wanted)
            else:
                by_oid = {oid: (tag, value) for oid, tag, value in binds}
                for p, oid in wanted:
                    if oid not in by_oid:
                        rejected.append(f"{p.name}: the device did not return {oid}")
                        continue
                    tag, value = by_oid[oid]
                    try:
                        values[p.name] = _point_value(p, oid, tag, value)
                    except ValueError as e:
                        rejected.append(f"{p.name}: {e}")
        for p, base in walks:
            binds, note = self._walk(base)
            if note:
                rejected.append(f"{p.name}: {note}")
            first_error, found = None, False
            for oid, tag, value in binds:
                try:
                    result = _point_value(p, oid, tag, value)
                except ValueError as e:
                    first_error = first_error or str(e)
                    continue
                values[p.name] = result
                found = True
                break
            if not found and not note:  # a timeout already explains why the point has no value
                rejected.append(f"{p.name}: {first_error or f'the device returned no value under {base}'}")
        return values, rejected

    def _walk(self, base):
        """([(oid, tag, value)], sentence or None) from GETNEXT steps that stay under `base`.

        One request and response per step, each with its own request id. A failure before anything was
        read raises, so the poll retries and backs off as it does for an exact GET; a failure after some
        values were read returns them with a sentence, so those values still reach their points.
        """
        binds, current = [], base
        while True:
            request_id = self._next_id()
            try:
                data = self._exchange(encode_getnext(request_id, self.community, current))
                got_id, status, _, step = decode_response(data)
                if got_id != request_id:
                    raise SnmpError(f"the device answered request {got_id} while request {request_id} was sent; "
                                    "another agent may be answering this address")
                if status:
                    raise SnmpError(ERRORS.get(status, f"the device reported error {status}"))
                if not step:
                    return binds, None
                for oid, tag, value in step:
                    if tag in MISSING or not _in_subtree(oid, base):  # the subtree ended: normal, not an error
                        return binds, None
                    if _oid_key(oid) <= _oid_key(current):
                        return binds, f"the device returned {oid} after {current}, so the walk could not advance"
                    binds.append((oid, tag, value))
                    current = oid
                    if len(binds) >= WALK_LIMIT:
                        return binds, (f"the walk from {base} stopped at the {WALK_LIMIT}-varbind cap, "
                                       "so the subtree may be larger than one poll should read")
            except SnmpError as e:
                if not binds:
                    raise
                return binds, str(e)

    def _exchange(self, packet):
        """One request datagram, with at most one retry after a timeout. Other failures fail at once."""
        for _ in range(2):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.settimeout(self.timeout)
                    try:
                        s.sendto(packet, (self.host, self.port))
                    except socket.gaierror:
                        raise SnmpError(f"cannot resolve the SNMP host name '{self.host}'") from None
                    except OSError as e:
                        raise SnmpError(f"cannot send to {self.host}:{self.port}: {e}") from None
                    data, _ = s.recvfrom(65535)
                    return data
            except socket.timeout:
                continue
            except OSError as e:
                raise SnmpError(f"cannot reach {self.host}:{self.port}: {e}") from None
        raise SnmpError(f"the device at {self.host}:{self.port} did not answer within {self.timeout:g}s")
