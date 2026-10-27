"""Sparkplug B payloads: the protobuf wire format, decoded without protobuf.

Written 2026-10-27 from the Eclipse Sparkplug B 3.0.0 specification
(sparkplug.eclipse.org, Appendix A / sparkplug_b.proto). Field numbers are read
from that appendix once and written here:

  Payload  timestamp=1  metrics=2  seq=3  uuid=4  body=5
  Metric   name=1  alias=2  timestamp=3  datatype=4
           is_historical=5  is_transient=6  is_null=7  metadata=8  properties=9
           int_value=10  long_value=11  float_value=12  double_value=13
           boolean_value=14  string_value=15  bytes_value=16
           dataset_value=17  template_value=18  extension_value=19

Only the fields DurianDB's model uses are kept: is_historical, is_transient,
metadata and properties are parsed and ignored. Array datatypes (22-34) travel
in bytes_value packed little-endian, as the specification's reference encoder
writes them; this codec keeps those bytes opaque. DataSet (16) and Template
(19) are likewise opaque nested payloads. A DataSet or Template value is not
interpreted here; that belongs to the driver, not the wire codec.

Unknown field numbers are skipped by their wire type, so a newer edge node can
add a field without breaking this decoder. No MQTT client is imported and no
network is touched.
"""
import struct
from dataclasses import dataclass, field
from typing import Optional

MAX_PAYLOAD = 1_048_576  # 1 MB, the same ceiling the HTTP body uses
MAX_METRICS = 10_000

VARINT, FIXED64, LENGTH, START_GROUP, END_GROUP, FIXED32 = 0, 1, 2, 3, 4, 5


class SparkplugError(ValueError):
    """A Sparkplug payload that cannot be encoded or decoded. The message is a sentence for the operator."""


@dataclass
class Metric:
    name: Optional[str] = None
    alias: Optional[int] = None
    timestamp: Optional[int] = None
    datatype: Optional[int] = None
    is_null: bool = False
    value: object = None


@dataclass
class Payload:
    timestamp: Optional[int] = None
    metrics: list = field(default_factory=list)
    seq: Optional[int] = None
    uuid: Optional[str] = None
    body: Optional[bytes] = None


DATATYPE_NAMES = {
    0: "Unknown",
    1: "Int8", 2: "Int16", 3: "Int32", 4: "Int64",
    5: "UInt8", 6: "UInt16", 7: "UInt32", 8: "UInt64",
    9: "Float", 10: "Double", 11: "Boolean", 12: "String",
    13: "DateTime", 14: "Text", 15: "UUID", 16: "DataSet",
    17: "Bytes", 18: "File", 19: "Template",
    20: "PropertySet", 21: "PropertySetList",
    22: "Int8Array", 23: "Int16Array", 24: "Int32Array", 25: "Int64Array",
    26: "UInt8Array", 27: "UInt16Array", 28: "UInt32Array", 29: "UInt64Array",
    30: "FloatArray", 31: "DoubleArray", 32: "BooleanArray", 33: "StringArray",
    34: "DateTimeArray",
}

VALUE_FIELDS = {
    10: "int_value", 11: "long_value", 12: "float_value", 13: "double_value",
    14: "boolean_value", 15: "string_value", 16: "bytes_value",
    17: "dataset_value", 18: "template_value", 19: "extension_value",
}

# datatype -> (protobuf field number, wire type) of its value
_VALUE_FIELD = {
    1: (10, VARINT), 2: (10, VARINT), 3: (10, VARINT), 4: (11, VARINT),
    5: (10, VARINT), 6: (10, VARINT), 7: (10, VARINT), 8: (11, VARINT),
    9: (12, FIXED32), 10: (13, FIXED64), 11: (14, VARINT),
    12: (15, LENGTH), 13: (11, VARINT), 14: (15, LENGTH), 15: (15, LENGTH),
    16: (17, LENGTH), 17: (16, LENGTH), 18: (16, LENGTH), 19: (18, LENGTH),
}
for _code in range(22, 35):
    _VALUE_FIELD[_code] = (16, LENGTH)  # packed array bytes travel in bytes_value

_INT_BITS = {1: 8, 2: 16, 3: 32, 4: 64, 5: 8, 6: 16, 7: 32, 8: 64, 13: 64}


def _label(m):
    if m.name is not None:
        return f"metric '{m.name}'"
    if m.alias is not None:
        return f"metric with alias {m.alias}"
    return "a metric with no name or alias"


def _kind(v):
    if isinstance(v, bool):
        return "a boolean"
    if isinstance(v, str):
        return "a string"
    if isinstance(v, (bytes, bytearray)):
        return "bytes"
    if isinstance(v, (int, float)):
        return "a number"
    if isinstance(v, (list, tuple)):
        return "a list"
    return "not a value Sparkplug can carry"


def _mismatch(label, dt, value):
    return SparkplugError(f"{label} has datatype {dt} ({DATATYPE_NAMES[dt]}) but the value is {_kind(value)}")


def _utf8(text, label):
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise SparkplugError(f"{label} is not valid UTF-8 text") from None


def _decode_utf8(raw, what):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SparkplugError(f"{what} is not valid UTF-8") from None


def _wire(what, got, want):
    return SparkplugError(f"{what} has wire type {got} instead of {want}")


# ---- the wire format

def _varint_write(n, out):
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _varint_read(data, pos):
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise SparkplugError("the payload ends in the middle of a varint")
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            if result > 0xFFFFFFFFFFFFFFFF:
                raise SparkplugError("a number in the payload is wider than 64 bits")
            return result, pos
        shift += 7
        if shift >= 70:
            raise SparkplugError("a number in the payload is wider than 64 bits")


def _tag_write(field_number, wire, out):
    _varint_write((field_number << 3) | wire, out)


def _length_write(field_number, raw, out):
    _tag_write(field_number, LENGTH, out)
    _varint_write(len(raw), out)
    out.extend(raw)


def _length_read(data, pos):
    n, pos = _varint_read(data, pos)
    end = pos + n
    if end > len(data):
        raise SparkplugError(f"a field claims {n} bytes but only {len(data) - pos} remain")
    return data[pos:end], end


def _skip(data, pos, wire):
    """Move past an unknown field by its wire type."""
    if wire == VARINT:
        _, pos = _varint_read(data, pos)
    elif wire == FIXED64:
        if pos + 8 > len(data):
            raise SparkplugError("a 64-bit field runs past the end of the payload")
        pos += 8
    elif wire == LENGTH:
        _, pos = _length_read(data, pos)
    elif wire == FIXED32:
        if pos + 4 > len(data):
            raise SparkplugError("a 32-bit field runs past the end of the payload")
        pos += 4
    elif wire == START_GROUP:
        depth = 1
        while depth:
            key, pos = _varint_read(data, pos)
            number, inner = key >> 3, key & 7
            if number == 0:
                raise SparkplugError("field number 0 is not valid")
            if inner == END_GROUP:
                depth -= 1
            elif inner == START_GROUP:
                depth += 1
            else:
                pos = _skip(data, pos, inner)
    else:
        raise SparkplugError("a field ends a group that was never started")
    return pos


# ---- values

def _check_value(m):
    """Refuse a value that does not match the metric's datatype."""
    label, dt, v = _label(m), m.datatype, m.value
    if dt not in _VALUE_FIELD:
        if dt in (20, 21):
            raise SparkplugError(f"{label} has datatype {dt} ({DATATYPE_NAMES[dt]}), which is not a metric value type")
        raise SparkplugError(f"{label} has datatype {dt} ({DATATYPE_NAMES[dt]}) but carries a value")
    if dt in _INT_BITS:
        if isinstance(v, bool) or not isinstance(v, int):
            raise _mismatch(label, dt, v)
        bits = _INT_BITS[dt]
        if dt in (1, 2, 3, 4, 13):
            low, high = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        else:
            low, high = 0, (1 << bits) - 1
        if not low <= v <= high:
            raise SparkplugError(f"{label} is {DATATYPE_NAMES[dt]} but {v} is outside {low} to {high}")
    elif dt in (9, 10):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise _mismatch(label, dt, v)
        try:
            struct.pack("<f" if dt == 9 else "<d", float(v))
        except (OverflowError, struct.error):
            raise SparkplugError(f"{label} is {DATATYPE_NAMES[dt]} but {v} is outside its range") from None
    elif dt == 11:
        if not isinstance(v, bool):
            raise _mismatch(label, dt, v)
    elif dt in (12, 14, 15):
        if not isinstance(v, str):
            raise _mismatch(label, dt, v)
        _utf8(v, label)
    else:  # DataSet, Bytes, File, Template and the packed arrays
        if not isinstance(v, (bytes, bytearray)):
            raise _mismatch(label, dt, v)


def _write_value(m, out):
    dt, v = m.datatype, m.value
    if dt in (1, 2, 3):
        _tag_write(10, VARINT, out)
        _varint_write(v & 0xFFFFFFFF, out)
    elif dt in (5, 6, 7):
        _tag_write(10, VARINT, out)
        _varint_write(v, out)
    elif dt in (4, 13):
        _tag_write(11, VARINT, out)
        _varint_write(v & 0xFFFFFFFFFFFFFFFF, out)
    elif dt == 8:
        _tag_write(11, VARINT, out)
        _varint_write(v, out)
    elif dt == 9:
        _tag_write(12, FIXED32, out)
        out.extend(struct.pack("<f", float(v)))
    elif dt == 10:
        _tag_write(13, FIXED64, out)
        out.extend(struct.pack("<d", float(v)))
    elif dt == 11:
        _tag_write(14, VARINT, out)
        _varint_write(1 if v else 0, out)
    elif dt in (12, 14, 15):
        _length_write(15, _utf8(v, _label(m)), out)
    elif dt == 16:
        _length_write(17, bytes(v), out)
    elif dt == 19:
        _length_write(18, bytes(v), out)
    else:  # Bytes, File and the packed arrays
        _length_write(16, bytes(v), out)


def _read_value(data, pos, field_number, wire):
    if field_number in (10, 11, 14):
        if wire != VARINT:
            raise _wire(f"metric field '{VALUE_FIELDS[field_number]}'", wire, VARINT)
        value, pos = _varint_read(data, pos)
        return (bool(value) if field_number == 14 else value), pos
    if field_number == 12:
        if wire != FIXED32:
            raise _wire("metric field 'float_value'", wire, FIXED32)
        if pos + 4 > len(data):
            raise SparkplugError("a 32-bit field runs past the end of the metric")
        return struct.unpack("<f", data[pos:pos + 4])[0], pos + 4
    if field_number == 13:
        if wire != FIXED64:
            raise _wire("metric field 'double_value'", wire, FIXED64)
        if pos + 8 > len(data):
            raise SparkplugError("a 64-bit field runs past the end of the metric")
        return struct.unpack("<d", data[pos:pos + 8])[0], pos + 8
    if wire != LENGTH:
        raise _wire(f"metric field '{VALUE_FIELDS[field_number]}'", wire, LENGTH)
    raw, pos = _length_read(data, pos)
    if field_number == 15:
        return _decode_utf8(raw, "a string value in the payload"), pos
    return raw, pos


def _check_decoded(m, field_number):
    """Refuse bytes whose value field does not match the datatype, then un-signed the numbers."""
    dt, name = m.datatype, DATATYPE_NAMES[m.datatype]
    if dt not in _VALUE_FIELD:
        raise SparkplugError(f"{_label(m)} has datatype {dt} ({name}) but carries {VALUE_FIELDS[field_number]}")
    if field_number != _VALUE_FIELD[dt][0]:
        raise SparkplugError(f"{_label(m)} has datatype {dt} ({name}) but carries {VALUE_FIELDS[field_number]}")
    v = m.value
    if dt in (1, 2, 3):
        if v > 0xFFFFFFFF:
            raise SparkplugError(f"{_label(m)} is {name} but the value {v} is outside its range")
        if v >= 1 << 31:
            v -= 1 << 32
        bits = _INT_BITS[dt]
        low, high = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        if not low <= v <= high:
            raise SparkplugError(f"{_label(m)} is {name} but the value {v} is outside {low} to {high}")
        m.value = v
    elif dt in (5, 6, 7):
        high = (1 << _INT_BITS[dt]) - 1
        if not 0 <= v <= high:
            raise SparkplugError(f"{_label(m)} is {name} but the value {v} is outside 0 to {high}")
    elif dt in (4, 13):
        m.value = v - (1 << 64) if v >= 1 << 63 else v
    elif dt == 11:
        m.value = bool(v)


# ---- public API

def encode_payload(payload):
    """Encode a Payload to protobuf bytes. Raises SparkplugError with a sentence on anything a device could not read."""
    if len(payload.metrics) > MAX_METRICS:
        raise SparkplugError(f"the payload has {len(payload.metrics)} metrics, over the {MAX_METRICS} maximum")
    out = bytearray()
    if payload.timestamp is not None:
        _whole(payload.timestamp, 64, "the payload timestamp")
        _tag_write(1, VARINT, out)
        _varint_write(payload.timestamp, out)
    for m in payload.metrics:
        _length_write(2, _encode_metric(m), out)
    if payload.seq is not None:
        _whole(payload.seq, 64, "the payload seq")
        _tag_write(3, VARINT, out)
        _varint_write(payload.seq, out)
    if payload.uuid is not None:
        if not isinstance(payload.uuid, str):
            raise SparkplugError("the payload uuid is not text")
        _length_write(4, _utf8(payload.uuid, "the payload uuid"), out)
    if payload.body is not None:
        if not isinstance(payload.body, (bytes, bytearray)):
            raise SparkplugError("the payload body is not bytes")
        _length_write(5, bytes(payload.body), out)
    if len(out) > MAX_PAYLOAD:
        raise SparkplugError(f"the payload is {len(out)} bytes, over the {MAX_PAYLOAD}-byte maximum")
    return bytes(out)


def _whole(value, bits, what):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < (1 << bits):
        raise SparkplugError(f"{what} is not a whole number from 0 to {(1 << bits) - 1}")


def _encode_metric(m):
    if m.name is None and m.alias is None:
        raise SparkplugError("a metric has neither a name nor an alias")
    label = _label(m)
    if m.datatype is not None:
        if isinstance(m.datatype, bool) or not isinstance(m.datatype, int) or not 0 <= m.datatype <= 34:
            raise SparkplugError(f"{label} has datatype {m.datatype!r}, which is not in the Sparkplug datatype table")
    if m.is_null and m.value is not None:
        raise SparkplugError(f"{label} is null but also carries a value")
    if m.value is not None and m.datatype is None:
        raise SparkplugError(f"{label} has a value but no datatype")
    if m.value is not None:
        _check_value(m)

    out = bytearray()
    if m.name is not None:
        if not isinstance(m.name, str):
            raise SparkplugError(f"{label} has a name that is not text")
        _length_write(1, _utf8(m.name, label), out)
    if m.alias is not None:
        _whole(m.alias, 64, f"{label} alias")
        _tag_write(2, VARINT, out)
        _varint_write(m.alias, out)
    if m.timestamp is not None:
        _whole(m.timestamp, 64, f"{label} timestamp")
        _tag_write(3, VARINT, out)
        _varint_write(m.timestamp, out)
    if m.datatype is not None:
        _tag_write(4, VARINT, out)
        _varint_write(m.datatype, out)
    if m.is_null:
        _tag_write(7, VARINT, out)
        _varint_write(1, out)
    if m.value is not None:
        _write_value(m, out)
    return bytes(out)


def decode_payload(data):
    """Decode protobuf bytes to a Payload. Raises SparkplugError with a sentence on malformed or oversized input."""
    if isinstance(data, memoryview):
        data = data.tobytes()
    elif isinstance(data, bytearray):
        data = bytes(data)
    if not isinstance(data, bytes):
        raise SparkplugError("the payload is not bytes")
    if len(data) > MAX_PAYLOAD:
        raise SparkplugError(f"the payload is {len(data)} bytes, over the {MAX_PAYLOAD}-byte maximum")

    payload = Payload()
    pos = 0
    while pos < len(data):
        key, pos = _varint_read(data, pos)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise SparkplugError("field number 0 is not valid")
        if number == 1:
            if wire != VARINT:
                raise _wire("the payload timestamp", wire, VARINT)
            payload.timestamp, pos = _varint_read(data, pos)
        elif number == 2:
            if wire != LENGTH:
                raise _wire("the payload metrics", wire, LENGTH)
            raw, pos = _length_read(data, pos)
            payload.metrics.append(_decode_metric(raw))
            if len(payload.metrics) > MAX_METRICS:
                raise SparkplugError(f"the payload has more than {MAX_METRICS} metrics")
        elif number == 3:
            if wire != VARINT:
                raise _wire("the payload seq", wire, VARINT)
            payload.seq, pos = _varint_read(data, pos)
        elif number == 4:
            if wire != LENGTH:
                raise _wire("the payload uuid", wire, LENGTH)
            raw, pos = _length_read(data, pos)
            payload.uuid = _decode_utf8(raw, "the payload uuid")
        elif number == 5:
            if wire != LENGTH:
                raise _wire("the payload body", wire, LENGTH)
            payload.body, pos = _length_read(data, pos)
        else:
            pos = _skip(data, pos, wire)
    return payload


def _decode_metric(raw):
    m = Metric()
    value_field = None
    pos = 0
    while pos < len(raw):
        key, pos = _varint_read(raw, pos)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise SparkplugError("field number 0 is not valid")
        if number == 1:
            if wire != LENGTH:
                raise _wire("a metric name", wire, LENGTH)
            text, pos = _length_read(raw, pos)
            m.name = _decode_utf8(text, "a metric name")
        elif number == 2:
            if wire != VARINT:
                raise _wire("a metric alias", wire, VARINT)
            m.alias, pos = _varint_read(raw, pos)
        elif number == 3:
            if wire != VARINT:
                raise _wire("a metric timestamp", wire, VARINT)
            m.timestamp, pos = _varint_read(raw, pos)
        elif number == 4:
            if wire != VARINT:
                raise _wire("a metric datatype", wire, VARINT)
            m.datatype, pos = _varint_read(raw, pos)
        elif number == 7:
            if wire != VARINT:
                raise _wire("a metric is_null", wire, VARINT)
            flag, pos = _varint_read(raw, pos)
            m.is_null = bool(flag)
        elif number in VALUE_FIELDS:
            if value_field is not None:
                raise SparkplugError(f"{_label(m)} has more than one value field set")
            value_field = number
            m.value, pos = _read_value(raw, pos, number, wire)
        else:
            pos = _skip(raw, pos, wire)  # includes is_historical, is_transient, metadata, properties

    if m.name is None and m.alias is None:
        raise SparkplugError("a metric has neither a name nor an alias")
    if m.datatype is not None and not 0 <= m.datatype <= 34:
        raise SparkplugError(f"{_label(m)} has datatype {m.datatype}, which is not in the Sparkplug datatype table")
    if m.is_null and m.value is not None:
        raise SparkplugError(f"{_label(m)} is null but also carries a value")
    if value_field is not None:
        if m.datatype is None:
            raise SparkplugError(f"{_label(m)} has a value but no datatype")
        _check_decoded(m, value_field)
    return m
