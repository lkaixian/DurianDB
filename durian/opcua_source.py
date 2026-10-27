"""OPC UA binary client, read-only, over UA-TCP: Hello/Acknowledge, one channel and session per poll.

Only `SecurityPolicy None` is implemented, and that is deliberate. A read-only driver needs the binary
encoding, the secure-channel and session handshake, and the DataValue/Variant decode; encrypting the
channel needs certificates, signature algorithms and nonce handling, which is a large amount of
cryptography to reimplement cleanly with the standard library. A config that asks for a secure policy is
refused with a sentence rather than silently downgraded, so nobody assumes the traffic is encrypted. The
endpoint URL is `opc.tcp://host:port[/path]`, and `opcua.address` is `host:port` (14360 is a typical lab
port; 4840 is the usual default).

A point names its NodeId in string form in `opcua.node` (`ns=2;i=85`, `ns=2;s=Temperature`). One Read per
poll carries every distinct node, so a poll is one request after the handshake; the response size, the
node count and the number of ignored messages are capped, and the whole poll shares one timeout. Status
codes decide quality: a Good code is GOOD, an Uncertain code UNCERTAIN and a Bad code BAD, and the status
name goes into the source's health sentence while the rest of the poll still updates. Common Variants
decode to numbers and strings: Boolean, SByte..Double, String, DateTime (Unix seconds for a number point,
ISO 8601 for a text point) and ByteString (shown as a byte count for a text point, since binary data is not
a reading). Every protocol and network failure is a sentence; nothing but a failed whole poll escapes
`fetch()`.

No Browse: the node ids are configured, so a browse would only be a convenience for an operator editing
the config and another request the poll would have to bound. The client reads one attribute (Value) of the
configured nodes, writes nothing, subscribes to nothing, and closes the session and channel when done.
"""
import datetime
import math
import socket
import struct
import time

from .core import Quality, sentence
from .polled import PolledSource

MAX_MESSAGE = 1_048_576  # bytes in one message; also the receive buffer advertised in Hello
MAX_NODES = 500  # distinct nodes one Read may carry, so one poll stays bounded
MAX_IGNORED = 16  # messages that are not the reply before the driver gives up on the request
MAX_ARRAY = 10_000  # elements decoded from one array, so a hostile count cannot spin the parser

VALUE_ATTRIBUTE = 13
TIMESTAMPS_NEITHER = 3
SECURITY_MODE_NONE = 1
REQUEST_TYPE_ISSUE = 0
CHANNEL_LIFETIME_MS = 3_600_000
SESSION_TIMEOUT = 60.0

OPEN_SECURE_CHANNEL_REQUEST = 446
OPEN_SECURE_CHANNEL_RESPONSE = 449
CLOSE_SECURE_CHANNEL_REQUEST = 452
SERVICE_FAULT = 397
CREATE_SESSION_REQUEST = 461
CREATE_SESSION_RESPONSE = 464
ACTIVATE_SESSION_REQUEST = 467
ACTIVATE_SESSION_RESPONSE = 470
CLOSE_SESSION_REQUEST = 473
CLOSE_SESSION_RESPONSE = 476
READ_REQUEST = 631
READ_RESPONSE = 634
ANONYMOUS_IDENTITY_TOKEN = 321

EPOCH_1601_TO_1970 = 11_644_473_600  # seconds between the OPC UA epoch and the Unix epoch

# Built-in type ids a Variant's encoding mask can carry.
TYPE_NAMES = {
    0: "an empty value", 1: "Boolean", 2: "SByte", 3: "Byte", 4: "Int16", 5: "UInt16", 6: "Int32",
    7: "UInt32", 8: "Int64", 9: "UInt64", 10: "Float", 11: "Double", 12: "String", 13: "DateTime",
    14: "Guid", 15: "ByteString", 16: "XmlElement", 17: "NodeId", 18: "ExpandedNodeId",
    19: "StatusCode", 20: "QualifiedName", 21: "LocalizedText", 22: "ExtensionObject", 23: "DataValue",
    24: "Variant", 25: "DiagnosticInfo",
}

# The statuses an operator is likely to meet. Anything else is shown as its numeric code.
STATUS_NAMES = {
    0x00000000: "Good",
    0x40000000: "Uncertain",
    0x40920000: "UncertainLastUsableValue",
    0x80000000: "Bad",
    0x80010000: "BadUnexpectedError", 0x80020000: "BadInternalError", 0x80030000: "BadOutOfMemory",
    0x80040000: "BadResourceUnavailable", 0x80050000: "BadCommunicationError",
    0x80060000: "BadEncodingError", 0x80070000: "BadDecodingError",
    0x80080000: "BadEncodingLimitsExceeded", 0x80090000: "BadUnknownResponse",
    0x800A0000: "BadTimeout", 0x800B0000: "BadServiceUnsupported", 0x800C0000: "BadShutdown",
    0x800D0000: "BadServerNotConnected", 0x800E0000: "BadServerHalted",
    0x80140000: "BadConnectionRejected", 0x80150000: "BadConnectionClosed",
    0x80170000: "BadInvalidState", 0x80180000: "BadEndOfStream",
    0x801F0000: "BadUserAccessDenied",
    0x80200000: "BadIdentityTokenInvalid", 0x80210000: "BadIdentityTokenRejected",
    0x80220000: "BadSecureChannelIdInvalid", 0x80230000: "BadSecureChannelClosed",
    0x80240000: "BadSecureChannelTokenUnknown", 0x80250000: "BadSequenceNumberInvalid",
    0x80260000: "BadProtocolVersionUnsupported", 0x80270000: "BadRequestHeaderInvalid",
    0x80280000: "BadSessionIdInvalid", 0x80290000: "BadSessionClosed",
    0x802A0000: "BadSessionNotActivated", 0x802B0000: "BadSubscriptionIdInvalid",
    0x802C0000: "BadRequestHeaderInvalid",
    0x80330000: "BadNodeIdInvalid", 0x80340000: "BadNodeIdUnknown",
    0x80350000: "BadAttributeIdInvalid", 0x80360000: "BadIndexRangeInvalid",
    0x80370000: "BadIndexRangeNoData", 0x80380000: "BadDataEncodingInvalid",
    0x80390000: "BadDataEncodingUnsupported", 0x803A0000: "BadNotReadable",
    0x803B0000: "BadNotWritable", 0x803C0000: "BadOutOfRange", 0x803D0000: "BadNotSupported",
    0x803E0000: "BadNotFound", 0x803F0000: "BadObjectDeleted",
    0x80400000: "BadNotImplemented", 0x80410000: "BadMonitoringModeInvalid",
    0x80420000: "BadMonitoringFilterInvalid", 0x80430000: "BadMonitoringFilterUnsupported",
    0x80440000: "BadFilterNotAllowed", 0x80450000: "BadContinuationPointInvalid",
    0x80460000: "BadNoContinuationPoints", 0x80470000: "BadReferenceTypeIdInvalid",
    0x80480000: "BadNoMatch", 0x80490000: "BadMaxAgeInvalid",
    0x804A0000: "BadHistoryOperationInvalid", 0x804B0000: "BadHistoryOperationUnsupported",
    0x804C0000: "BadTooManyOperations", 0x804D0000: "BadNothingToDo",
}

_NULL_NODE = (0, "i", 0)
_UNSUPPORTED = object()  # a value this driver consumed but cannot make a reading of


class OpcuaError(ValueError):
    """A request or reply that cannot be built or understood. The message is a sentence for the operator."""


class _Fault(OpcuaError):
    """A well-formed reply whose service result says the request was refused."""


def status_name(code):
    """The OPC UA status name, or its numeric code when this driver does not know it."""
    return STATUS_NAMES.get(code, f"status 0x{code:08x}")


def status_quality(code):
    """GOOD for severity Good, UNCERTAIN for severity Uncertain, BAD for Bad (and the reserved severity)."""
    severity = code >> 30
    if severity == 0:
        return Quality.GOOD
    if severity == 1:
        return Quality.UNCERTAIN
    return Quality.BAD


def parse_address(text):
    """(host, port) from 'host:port'. Raises OpcuaError with a sentence when it is not one."""
    if not isinstance(text, str):
        raise OpcuaError("give opcua.address as host:port, such as 10.0.0.5:14360")
    host, sep, port = text.strip().rpartition(":")
    if (not sep or not host or len(host) > 253 or host.startswith("[") or host.endswith("]")
            or not port.isdigit() or not 1 <= int(port) <= 65535):
        raise OpcuaError("give opcua.address as host:port, such as 10.0.0.5:14360")
    return host, int(port)


def parse_node_id(text):
    """(namespace, kind, value) from a NodeId string such as 'ns=2;i=85' or 'ns=2;s=Temperature'.

    Only numeric (`i=`) and string (`s=`) identifiers are read; a GUID or opaque NodeId is refused with a
    sentence, because a point configured with one would need a decoder this driver does not carry.
    """
    if not isinstance(text, str):
        raise OpcuaError("an OPC UA node id must be text such as ns=2;i=85 or ns=2;s=Temperature")
    text = text.strip()
    if not text or len(text) > 1024:
        raise OpcuaError("an OPC UA node id must be text such as ns=2;i=85 or ns=2;s=Temperature")
    namespace, rest = 0, text
    if rest.startswith("ns="):
        number, sep, rest = rest[3:].partition(";")
        if not sep or not number.isdigit() or int(number) > 65535:
            raise OpcuaError(f"'{text}' is not an OPC UA node id such as ns=2;i=85 or ns=2;s=Temperature")
        namespace = int(number)
    kind, sep, value = rest.partition("=")
    if sep and kind == "i" and value.isdigit() and int(value) <= 0xFFFFFFFF:
        return namespace, "i", int(value)
    if sep and kind == "s" and 1 <= len(value) <= 1024:
        return namespace, "s", value
    raise OpcuaError(f"'{text}' is not an OPC UA node id such as ns=2;i=85 or ns=2;s=Temperature")


def _node_text(node):
    """A NodeId as its configured string form, for a rejection sentence."""
    namespace, kind, value = node
    head = f"ns={namespace};" if namespace else ""
    return f"{head}{kind}={value}"


def _string(text):
    """A String or nullable String in OPC UA binary encoding."""
    if text is None:
        return struct.pack("<i", -1)
    raw = text.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def _bytes(raw):
    """A ByteString or nullable ByteString in OPC UA binary encoding."""
    if raw is None:
        return struct.pack("<i", -1)
    return struct.pack("<i", len(raw)) + raw


def _node_id_bytes(node):
    """One NodeId in its shortest binary encoding."""
    namespace, kind, value = node
    if kind == "i":
        if namespace == 0 and value <= 0xFF:
            return bytes([0x00, value])
        if namespace <= 0xFF and value <= 0xFFFF:
            return bytes([0x01, namespace]) + struct.pack("<H", value)
        return b"\x02" + struct.pack("<HI", namespace, value)
    if kind == "s":
        return b"\x03" + struct.pack("<H", namespace) + _string(value)
    if kind == "g":
        return b"\x04" + struct.pack("<H", namespace) + bytes.fromhex(value)
    if kind == "b":
        return b"\x05" + struct.pack("<H", namespace) + _bytes(value)
    raise OpcuaError(f"a node id of kind '{kind}' cannot be encoded")


def _now_ticks():
    """The current time as OPC UA DateTime ticks (100 ns units since 1601-01-01)."""
    return int((time.time() + EPOCH_1601_TO_1970) * 10_000_000)


class _Reader:
    """A bounded reader for OPC UA binary replies. Every structural problem is an OpcuaError sentence."""

    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n, what):
        if n < 0 or self.pos + n > len(self.data):
            raise OpcuaError(f"the server's reply ends in the middle of {what}")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def u8(self):
        return self.take(1, "a byte")[0]

    def i8(self):
        return struct.unpack("<b", self.take(1, "a signed byte"))[0]

    def u16(self):
        return struct.unpack("<H", self.take(2, "a 16-bit number"))[0]

    def i16(self):
        return struct.unpack("<h", self.take(2, "a signed 16-bit number"))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4, "a 32-bit number"))[0]

    def i32(self):
        return struct.unpack("<i", self.take(4, "a signed 32-bit number"))[0]

    def u64(self):
        return struct.unpack("<Q", self.take(8, "a 64-bit number"))[0]

    def i64(self):
        return struct.unpack("<q", self.take(8, "a signed 64-bit number"))[0]

    def f32(self):
        return struct.unpack("<f", self.take(4, "a Float"))[0]

    def f64(self):
        return struct.unpack("<d", self.take(8, "a Double"))[0]

    def boolean(self):
        return self.u8() != 0

    def string(self, what="a string"):
        n = self.i32()
        if n == -1:
            return None
        if n < 0 or n > MAX_MESSAGE:
            raise OpcuaError(f"the server's {what} has an impossible length")
        try:
            return self.take(n, what).decode("utf-8")
        except UnicodeDecodeError:
            raise OpcuaError(f"the server's {what} is not valid UTF-8") from None

    def byte_string(self, what="a byte string"):
        n = self.i32()
        if n == -1:
            return None
        if n < 0 or n > MAX_MESSAGE:
            raise OpcuaError(f"the server's {what} has an impossible length")
        return self.take(n, what)

    def count(self, what):
        """An array length, with -1 (a null array) read as empty and an impossible length refused."""
        n = self.i32()
        if n == -1:
            return 0
        if n < 0 or n > MAX_ARRAY:
            raise OpcuaError(f"the server's {what} claims an impossible length")
        return n

    def node_id(self):
        encoding = self.u8()
        kind, namespace_uri, server_index = encoding & 0x3F, encoding & 0x40, encoding & 0x80
        if kind == 0:  # TwoByte: namespace 0 and a one-byte identifier
            node = (0, "i", self.u8())
        elif kind == 1:  # FourByte: namespace byte and a 16-bit identifier
            node = (self.u8(), "i", self.u16())
        elif kind == 2:  # Numeric: 16-bit namespace and 32-bit identifier
            node = (self.u16(), "i", self.u32())
        elif kind == 3:
            node = (self.u16(), "s", self.string("a node id"))
        elif kind == 4:
            node = (self.u16(), "g", self.take(16, "a GUID node id").hex())
        elif kind == 5:
            node = (self.u16(), "b", self.byte_string("an opaque node id"))
        else:
            raise OpcuaError(f"the server sent a node id with an unknown encoding 0x{encoding:02x}")
        if namespace_uri:
            self.string("a node id namespace URI")
        if server_index:
            self.u32()
        return node

    def diagnostic_info(self, depth=0):
        if depth > 8:
            raise OpcuaError("the server's diagnostics are nested too deeply")
        mask = self.u8()
        if mask & 0x01:
            self.i32()
        if mask & 0x02:
            self.i32()
        if mask & 0x04:
            self.i32()
        if mask & 0x08:
            self.i32()
        if mask & 0x10:
            self.string("diagnostic text")
        if mask & 0x20:
            self.u32()
        if mask & 0x40:
            self.diagnostic_info(depth + 1)
        if mask & 0x80:
            raise OpcuaError("the server set an unknown diagnostic flag")

    def extension_object(self):
        self.node_id()
        encoding = self.u8()
        if encoding == 0:
            return
        if encoding in (1, 2):
            n = self.i32()
            if n < 0 or n > MAX_MESSAGE:
                raise OpcuaError("the server's extension object has an impossible length")
            self.take(n, "an extension object")
            return
        raise OpcuaError(f"the server used an unknown extension object encoding {encoding}")

    def localized_text(self):
        mask = self.u8()
        locale = self.string("a locale") if mask & 0x01 else None
        text = self.string("a localized text") if mask & 0x02 else None
        if mask & 0xFC:
            raise OpcuaError("the server set an unknown localized-text flag")
        return text if text is not None else locale

    def scalar(self, type_id):
        """One scalar Variant value, or _UNSUPPORTED after consuming a type this driver cannot use."""
        if type_id == 1:
            return self.boolean()
        if type_id == 2:
            return self.i8()
        if type_id == 3:
            return self.u8()
        if type_id == 4:
            return self.i16()
        if type_id == 5:
            return self.u16()
        if type_id == 6:
            return self.i32()
        if type_id == 7:
            return self.u32()
        if type_id == 8:
            return self.i64()
        if type_id == 9:
            return self.u64()
        if type_id == 10:
            return self.f32()
        if type_id == 11:
            return self.f64()
        if type_id == 12:
            return self.string("a value")
        if type_id == 13:
            return self.i64()  # DateTime ticks
        if type_id == 14:
            return self.take(16, "a GUID").hex()
        if type_id == 15:
            return self.byte_string("a value")
        if type_id == 16:
            return self.string("an XML value")
        if type_id == 17:
            return self.node_id()
        if type_id == 18:
            return self.node_id()
        if type_id == 19:
            return self.u32()
        if type_id == 20:
            return (self.u16(), self.string("a qualified name"))
        if type_id == 21:
            return self.localized_text()
        if type_id == 22:
            self.extension_object()
            return _UNSUPPORTED
        if type_id == 23:
            self.data_value()
            return _UNSUPPORTED
        if type_id == 24:
            self.variant()
            return _UNSUPPORTED
        if type_id == 25:
            self.diagnostic_info()
            return _UNSUPPORTED
        raise OpcuaError(f"the server used value type {type_id}, which this source does not read")

    def variant(self):
        """(value, type name). A null Variant is (None, 'an empty value'); a type this driver cannot use is _UNSUPPORTED."""
        mask = self.u8()
        if mask & 0x40:
            raise OpcuaError("the server set an unknown variant flag")
        type_id = mask & 0x3F
        name = TYPE_NAMES.get(type_id, f"type {type_id}")
        if type_id == 0:
            return None, name
        if mask & 0x80:
            n = self.count("a value array")
            return [self.scalar(type_id) for _ in range(n)], f"an array of {name}"
        return self.scalar(type_id), name

    def data_value(self):
        """(has_value, value, type name, status code) from one DataValue."""
        mask = self.u8()
        if mask & 0xC0:
            raise OpcuaError("the server set an unknown data value flag")
        has_value, value, name = False, None, None
        if mask & 0x01:
            value, name = self.variant()
            has_value = True
        status = self.u32() if mask & 0x02 else 0
        if mask & 0x04:
            self.i64()
        if mask & 0x08:
            self.i64()
        if mask & 0x10:
            self.u16()
        if mask & 0x20:
            self.u16()
        return has_value, value, name, status


def _text(value, node):
    """Printable text no longer than 255 characters, or OpcuaError with a sentence."""
    if not isinstance(value, str):
        raise OpcuaError(f"the server returned a value for {_node_text(node)} that cannot be shown as text")
    if not 1 <= len(value) <= 255 or not value.isprintable():
        raise OpcuaError(f"the text for {_node_text(node)} is empty, longer than 255 characters, or has control characters")
    return value


def _number_text(number):
    return str(int(number)) if float(number).is_integer() else str(number)


def _point_value(point, node, value, name, has_value, status):
    """The value a point stores from one DataValue. Raises OpcuaError with a sentence when it cannot."""
    if not has_value:
        raise OpcuaError(f"the server returned no value for {_node_text(node)} ({status_name(status)})")
    if value is _UNSUPPORTED or isinstance(value, list):
        what = name if value is _UNSUPPORTED else f"an array of {name}"
        raise OpcuaError(f"the server returned {what} for {_node_text(node)}, which this source does not read")
    if name == "DateTime":
        seconds = value / 10_000_000 - EPOCH_1601_TO_1970
        if point.text:
            try:
                return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                raise OpcuaError(f"the server returned a date for {_node_text(node)} that cannot be shown") from None
        if not math.isfinite(seconds):
            raise OpcuaError(f"the server returned a date for {_node_text(node)} that is not finite")
        return seconds
    if isinstance(value, bytes):
        if point.text:
            return f"binary value ({len(value)} bytes)"
        raise OpcuaError(f"the server returned a byte string for {_node_text(node)}, but this point is a number")
    if isinstance(value, str):
        if point.text:
            return _text(value, node)
        raise OpcuaError(f"the server returned text for {_node_text(node)}, but this point is a number")
    if isinstance(value, tuple):  # a NodeId, QualifiedName or GUID
        text = _node_text(value) if len(value) == 3 and isinstance(value[1], str) and value[1] in ("i", "s") \
            else str(value)
        if point.text:
            return _text(text, node)
        raise OpcuaError(f"the server returned an identifier for {_node_text(node)}, but this point is a number")
    if isinstance(value, bool):
        return ("true" if value else "false") if point.text else (1.0 if value else 0.0)
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise OpcuaError(f"the server returned a number that is not finite for {_node_text(node)}")
        return _number_text(number) if point.text else number
    raise OpcuaError(f"the server returned {name} for {_node_text(node)}, which this point cannot store")


class OpcuaSource(PolledSource):
    """Read configured nodes from one OPC UA server, one bounded request cycle per poll."""

    def __init__(self, name, cfg):
        super().__init__(name, cfg.get("period", 5.0), cfg.get("backoff_scale", 1.5), cfg.get("backoff_max", 300.0))
        section = cfg.get("opcua") if isinstance(cfg.get("opcua"), dict) else {}
        self.host, self.port = parse_address(section.get("address"))
        path = section.get("path", "")
        self.path = path.strip("/") if isinstance(path, str) else ""
        policy = section.get("security_policy", "None")
        if not (isinstance(policy, str) and policy.lower() == "none"):
            raise OpcuaError("only SecurityPolicy None is implemented, so opcua.security_policy must be None; "
                             "DurianDB refuses to pretend it encrypts this connection")
        mode = section.get("security_mode", "None")
        if not (isinstance(mode, str) and mode.lower() == "none"):
            raise OpcuaError("only SecurityMode None is implemented, so opcua.security_mode must be None")
        self.timeout = float(cfg.get("timeout", 2.0))
        self._request_id = 0
        self._sequence = 0
        self._channel_id = 0
        self._token_id = 0
        self._auth = _NULL_NODE
        self._qualities = {}

    def _next_request_id(self):
        """A fresh positive request id, so a late reply to an earlier request cannot be mistaken for this one."""
        self._request_id = self._request_id % 0x7FFFFFFF + 1
        return self._request_id

    def endpoint(self):
        """The endpoint URL sent in the Hello and the session request."""
        return f"opc.tcp://{self.host}:{self.port}" + (f"/{self.path}" if self.path else "")

    def failure_text(self, e):
        """OPC UA failures are already sentences; sentence() also strips any errno tag a wrapped OS error brings."""
        return sentence(e)

    def _specs(self):
        """([(point, node)], rejection sentences for a point whose node id cannot be read)."""
        specs, rejected = [], []
        for p in self.points.values():
            section = p.meta.get("opcua")
            node = section.get("node") if isinstance(section, dict) else None
            try:
                specs.append((p, parse_node_id(node)))
            except OpcuaError as e:
                rejected.append(f"{p.name}: {sentence(e)}")
        return specs, rejected

    def fetch(self):
        self._qualities = {}
        specs, rejected = self._specs()
        if not specs:
            return {}, rejected
        nodes, by_node = [], {}
        for p, node in specs:
            if node in by_node:
                by_node[node].append(p)
                continue
            if len(nodes) >= MAX_NODES:
                rejected.append(f"{p.name}: only {MAX_NODES} nodes are read per poll, so {_node_text(node)} "
                                "was not read; split the points across sources")
                continue
            by_node[node] = [p]
            nodes.append(node)

        deadline = time.monotonic() + self.timeout
        try:
            with socket.create_connection((self.host, self.port), self.timeout) as sock:
                sock.settimeout(self.timeout)
                self._sequence, self._channel_id, self._auth = 0, 0, _NULL_NODE
                self._hello(sock, deadline)
                self._open_channel(sock, deadline)
                self._create_session(sock, deadline)
                self._activate_session(sock, deadline)
                results = self._read(sock, deadline, nodes)
                self._close(sock, deadline)
        except OpcuaError:
            raise
        except socket.gaierror:
            raise OpcuaError(f"cannot resolve the OPC UA host name '{self.host}'") from None
        except socket.timeout:
            raise OpcuaError(f"the OPC UA server at {self.host}:{self.port} did not answer within "
                             f"{self.timeout:g}s") from None
        except OSError as e:
            raise OpcuaError(f"cannot reach {self.host}:{self.port}: {sentence(e)}") from None

        values = {}
        for i, node in enumerate(nodes):
            result = results[i] if i < len(results) else None
            for p in by_node[node]:
                if result is None:
                    rejected.append(f"{p.name}: the server did not return a value for {_node_text(node)}")
                    continue
                has_value, value, name, status = result
                quality = status_quality(status)
                try:
                    decoded = _point_value(p, node, value, name, has_value, status)
                except OpcuaError as e:
                    rejected.append(f"{p.name}: {sentence(e)}")
                    if quality == Quality.UNCERTAIN:
                        self._qualities[p.name] = Quality.UNCERTAIN
                    continue
                values[p.name] = decoded
                if quality != Quality.GOOD:
                    self._qualities[p.name] = quality
                    rejected.append(f"{p.name} ({_node_text(node)}): the server reported {status_name(status)}")
        return values, rejected

    def cycle(self):
        """One fetch, with status-derived quality kept for the engine. A failed poll backs off as usual."""
        try:
            values, rejected = self.fetch()
        except Exception as e:  # anything the transport can raise counts as a failed cycle
            if self._stop.is_set():
                return
            self.fails += 1
            wait = min(self.period * self.backoff_scale ** self.fails, self.backoff_max)
            self.next_try = time.time() + wait
            self.error, self.connected = f"{self.failure_text(e)} (retry in {wait:.0f}s)", False
            with self._lock:
                self._latest.update({n: (None, time.time(), Quality.BAD) for n in self.points})
            return
        if self._stop.is_set():
            return
        self.fails, self.next_try, self.connected = 0, 0.0, True
        self.error = f"{len(rejected)} point(s) rejected: " + "; ".join(rejected[:3]) if rejected else None
        now = time.time()
        with self._lock:
            for n, v in values.items():
                self._latest[n] = (v, now, self._qualities.get(n, Quality.GOOD))
            self._latest.update({n: (None, now, Quality.BAD) for n in self.points if n not in values})

    def _hello(self, sock, deadline):
        """Hello/Acknowledge, so this client and the server agree on the message sizes they accept."""
        body = (struct.pack("<IIIII", 0, MAX_MESSAGE, MAX_MESSAGE, MAX_MESSAGE, 1)
                + _string(self.endpoint()))
        self._send_raw(sock, b"HELF" + struct.pack("<I", 8 + len(body)) + body)
        mtype, chunk, rest = self._read_message(sock, deadline)
        if mtype == b"ERR":
            raise OpcuaError(self._error_sentence(rest))
        if mtype != b"ACK" or chunk != b"F":
            raise OpcuaError(f"the OPC UA server answered the Hello with {mtype.decode('latin-1')!r}, "
                             "not an Acknowledge")
        if len(rest) < 20:
            raise OpcuaError("the server's Acknowledge is too short")

    def _open_channel(self, sock, deadline):
        request_id = self._next_request_id()
        fields = (struct.pack("<I", 0) + struct.pack("<I", REQUEST_TYPE_ISSUE)
                  + struct.pack("<I", SECURITY_MODE_NONE) + _bytes(b"")
                  + struct.pack("<I", CHANNEL_LIFETIME_MS))
        body = (_node_id_bytes((0, "i", OPEN_SECURE_CHANNEL_REQUEST))
                + self._request_header(_NULL_NODE, request_id) + fields)
        self._send(sock, b"OPN", body, request_id)
        raw = self._await(sock, deadline, b"OPN", request_id, "an OpenSecureChannel reply")
        reader = _Reader(raw)
        self._check_response(reader, OPEN_SECURE_CHANNEL_RESPONSE, "OpenSecureChannel")
        reader.u32()  # server protocol version
        self._channel_id = reader.u32()
        self._token_id = reader.u32()
        reader.i64()
        reader.u32()
        reader.byte_string("a server nonce")

    def _create_session(self, sock, deadline):
        request_id = self._next_request_id()
        description = (_string(f"urn:DurianDB:{self.name}") + _string("urn:DurianDB")
                       + struct.pack("<I", 1) + _string(None) + _string(None) + struct.pack("<i", 0))
        fields = (description + _string(None) + _string(self.endpoint())
                  + _string(f"DurianDB {self.name}"[:64]) + _bytes(None) + _bytes(None)
                  + struct.pack("<d", SESSION_TIMEOUT) + struct.pack("<I", MAX_MESSAGE))
        body = (_node_id_bytes((0, "i", CREATE_SESSION_REQUEST))
                + self._request_header(_NULL_NODE, request_id) + fields)
        self._send(sock, b"MSG", body, request_id)
        raw = self._await(sock, deadline, b"MSG", request_id, "a CreateSession reply")
        reader = _Reader(raw)
        self._check_response(reader, CREATE_SESSION_RESPONSE, "CreateSession")
        reader.node_id()  # SessionId
        self._auth = reader.node_id()
        reader.f64()
        reader.byte_string("a server nonce")
        reader.byte_string("the server certificate")

    def _activate_session(self, sock, deadline):
        request_id = self._next_request_id()
        token_body = _string("anonymous")
        token = (_node_id_bytes((0, "i", ANONYMOUS_IDENTITY_TOKEN)) + b"\x01"
                 + struct.pack("<i", len(token_body)) + token_body)
        fields = (_string(None) + _bytes(None) + struct.pack("<i", 0) + struct.pack("<i", 0)
                  + token + _string(None) + _bytes(None))
        body = (_node_id_bytes((0, "i", ACTIVATE_SESSION_REQUEST))
                + self._request_header(self._auth, request_id) + fields)
        self._send(sock, b"MSG", body, request_id)
        raw = self._await(sock, deadline, b"MSG", request_id, "an ActivateSession reply")
        reader = _Reader(raw)
        self._check_response(reader, ACTIVATE_SESSION_RESPONSE, "ActivateSession")
        reader.byte_string("a server nonce")
        for _ in range(reader.count("activation results")):
            code = reader.u32()
            if code:
                raise _Fault(f"the OPC UA server rejected the anonymous user: {status_name(code)}")
        for _ in range(reader.count("activation diagnostics")):
            reader.diagnostic_info()

    def _read(self, sock, deadline, nodes):
        request_id = self._next_request_id()
        items = b"".join(_node_id_bytes(node) + struct.pack("<I", VALUE_ATTRIBUTE)
                         + _string(None) + struct.pack("<H", 0) + _string(None) for node in nodes)
        fields = (struct.pack("<d", 0.0) + struct.pack("<I", TIMESTAMPS_NEITHER)
                  + struct.pack("<i", len(nodes)) + items)
        body = (_node_id_bytes((0, "i", READ_REQUEST))
                + self._request_header(self._auth, request_id) + fields)
        self._send(sock, b"MSG", body, request_id)
        ignored = 0
        while True:
            raw = self._await(sock, deadline, b"MSG", request_id, "a Read reply")
            try:
                reader = _Reader(raw)
                self._check_response(reader, READ_RESPONSE, "Read")
                return [reader.data_value() for _ in range(reader.count("read results"))]
            except _Fault:
                raise
            except OpcuaError:
                ignored += 1
                if ignored >= MAX_IGNORED:
                    raise OpcuaError(f"the OPC UA server at {self.host}:{self.port} kept sending "
                                     "messages that are not a readable Read reply") from None

    def _close(self, sock, deadline):
        """Close the session and the channel. The values are already read; a failed courtesy close keeps them."""
        request_id = self._next_request_id()
        try:
            body = (_node_id_bytes((0, "i", CLOSE_SESSION_REQUEST))
                    + self._request_header(self._auth, request_id))
            self._send(sock, b"MSG", body, request_id)
            raw = self._await(sock, deadline, b"MSG", request_id, "a CloseSession reply")
            self._check_response(_Reader(raw), CLOSE_SESSION_RESPONSE, "CloseSession")
        except (OSError, OpcuaError):
            return
        try:
            request_id = self._next_request_id()
            body = (_node_id_bytes((0, "i", CLOSE_SECURE_CHANNEL_REQUEST))
                    + self._request_header(self._auth, request_id))
            self._send(sock, b"CLO", body, request_id)
        except OSError:
            pass

    def _check_response(self, reader, expected, what):
        """Consume the type NodeId and ResponseHeader, raising on a fault or a reply to another service."""
        node = reader.node_id()
        if node == (0, "i", SERVICE_FAULT):
            _, result = self._response_header(reader)
            raise _Fault(f"the OPC UA server refused {what}: {status_name(result)}")
        if node != (0, "i", expected):
            raise OpcuaError(f"the server answered {what} with {_node_text(node)}")
        _, result = self._response_header(reader)
        if result:
            raise _Fault(f"the OPC UA server refused {what}: {status_name(result)}")

    def _response_header(self, reader):
        reader.i64()  # timestamp
        handle = reader.u32()
        result = reader.u32()
        reader.diagnostic_info()
        for _ in range(reader.count("a string table")):
            reader.string("a string table entry")
        reader.extension_object()
        return handle, result

    def _request_header(self, auth, request_id):
        return (_node_id_bytes(auth) + struct.pack("<q", _now_ticks())
                + struct.pack("<I", request_id) + struct.pack("<I", 0)
                + _string(None) + struct.pack("<I", max(1, int(self.timeout * 1000)))
                + b"\x00\x00\x00")  # a null ExtensionObject: null NodeId and NoBody

    def _send(self, sock, mtype, body, request_id):
        """One OPN/MSG/CLO chunk: header, channel id, sequence number, request id, then the service body."""
        self._sequence = self._sequence % 0xFFFFFFFF + 1
        payload = struct.pack("<III", self._channel_id, self._sequence, request_id) + body
        packet = mtype + b"F" + struct.pack("<I", 8 + len(payload)) + payload
        if len(packet) > MAX_MESSAGE:
            raise OpcuaError(f"the request is larger than the {MAX_MESSAGE}-byte cap; read fewer nodes per poll")
        self._send_raw(sock, packet)

    def _send_raw(self, sock, packet):
        try:
            sock.sendall(packet)
        except socket.gaierror:
            raise OpcuaError(f"cannot resolve the OPC UA host name '{self.host}'") from None
        except OSError as e:
            raise OpcuaError(f"cannot send to {self.host}:{self.port}: {sentence(e)}") from None

    def _read_message(self, sock, deadline):
        """(type, chunk flag, content) of one complete message, with the header's size cap enforced."""
        head = self._recv_exact(sock, 8, deadline, "a message header")
        mtype, chunk = head[:3], head[3:4]
        size = int.from_bytes(head[4:8], "little")
        if size < 8 or size > MAX_MESSAGE:
            raise OpcuaError(f"the server announced a {size}-byte message, outside the {MAX_MESSAGE}-byte cap")
        return mtype, chunk, self._recv_exact(sock, size - 8, deadline, "a message")

    def _await(self, sock, deadline, expected, request_id, what):
        """The body of the reply with the matching request id, ignoring unrelated messages up to a cap."""
        ignored, last = 0, None
        while True:
            mtype, chunk, rest = self._read_message(sock, deadline)
            if mtype == b"ERR":
                raise OpcuaError(self._error_sentence(rest))
            if chunk == b"C":
                raise OpcuaError("the server split its reply into chunks, which this source does not reassemble")
            if chunk == b"A":
                raise OpcuaError("the server aborted its reply")
            if mtype != expected or chunk != b"F" or len(rest) < 12:
                ignored += 1
                if ignored >= MAX_IGNORED:
                    raise OpcuaError(f"the OPC UA server at {self.host}:{self.port} kept sending "
                                     f"messages that are not {what}")
                continue
            _, _, got_id = struct.unpack("<III", rest[:12])
            if got_id != request_id:
                last = got_id
                ignored += 1
                if ignored >= MAX_IGNORED:
                    raise OpcuaError(f"the OPC UA server answered request {last} while request "
                                     f"{request_id} was sent; another client may be using {self.host}:{self.port}")
                continue
            return rest[12:]

    def _recv_exact(self, sock, n, deadline, what):
        buf = bytearray()
        while len(buf) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OpcuaError(f"the OPC UA server at {self.host}:{self.port} did not answer within "
                                 f"{self.timeout:g}s")
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(n - len(buf))
            except socket.timeout:
                raise OpcuaError(f"the OPC UA server at {self.host}:{self.port} did not answer within "
                                 f"{self.timeout:g}s") from None
            except OSError as e:
                raise OpcuaError(f"cannot read from {self.host}:{self.port}: {sentence(e)}") from None
            if not chunk:
                raise OpcuaError(f"the OPC UA server at {self.host}:{self.port} closed the connection while "
                                 f"{what} was still arriving")
            buf += chunk
        return bytes(buf)

    def _error_sentence(self, rest):
        """The sentence for an ERR message: its status code and whatever reason the server gave."""
        try:
            reader = _Reader(rest)
            code, reason = reader.u32(), reader.string("an error reason")
        except OpcuaError:
            return f"the OPC UA server at {self.host}:{self.port} refused the connection"
        if reason:
            return f"the OPC UA server at {self.host}:{self.port} refused the connection: {status_name(code)} ({reason})"
        return f"the OPC UA server at {self.host}:{self.port} refused the connection: {status_name(code)}"
