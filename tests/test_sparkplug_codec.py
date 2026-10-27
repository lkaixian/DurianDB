"""Sparkplug B payload codec: round trips and every refusal. Run: python tests/test_sparkplug_codec.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.sparkplug_codec import (  # noqa: E402
    MAX_METRICS, MAX_PAYLOAD, Metric, Payload, SparkplugError, decode_payload, encode_payload,
)

assert issubclass(SparkplugError, ValueError)
assert "paho.mqtt.client" not in sys.modules  # the codec must not pull in the broker client


def roundtrip(payload):
    got = decode_payload(encode_payload(payload))
    assert got == payload, (payload, got)
    return got


def refuse(fn, message):
    try:
        fn()
    except SparkplugError as e:
        assert str(e) == message, f"expected {message!r}, got {str(e)!r}"
    else:
        raise AssertionError("nothing was refused: " + message)


def metric(name=None, datatype=None, value=None, **kw):
    return Metric(name=name, datatype=datatype, value=value, **kw)


# ---- round trips
roundtrip(Payload())  # empty
roundtrip(Payload(metrics=[metric("d", 10, 2.5)]))  # one double
roundtrip(Payload(metrics=[
    metric("i32", 3, -42),
    metric("i64", 4, -(2 ** 40)),
    metric("u64", 8, 2 ** 63 + 5),
    metric("f", 9, 1.5),
    metric("b0", 11, False),
    metric("b1", 11, True),
    metric("s", 12, "boiler °C"),
    metric("t", 14, "text"),
    metric("u", 15, "06a4c1e0-1f2b-4c3d-9e8f-0123456789ab"),
    metric("dt", 13, 1_700_000_000_000),
    metric("bytes", 17, b"\x00\xff\x10"),
    metric("file", 18, b"%PDF-1.4"),
]))
roundtrip(Payload(metrics=[Metric(name="nulled", datatype=3, is_null=True)]))
roundtrip(Payload(metrics=[Metric(name="nulled", is_null=True)]))  # null without a datatype
roundtrip(Payload(metrics=[Metric(alias=42, datatype=7, value=5)]))  # alias only, no name
# DataSet, Template and the packed array datatypes travel as opaque bytes, as the wire does
roundtrip(Payload(metrics=[
    metric("set", 16, b"\x08\x01"),
    metric("tpl", 19, b"\x0a\x00"),
    metric("arr", 24, b"\x01\x00\x00\x00\x02\x00\x00\x00"),
]))
roundtrip(Payload(timestamp=123, seq=255, uuid="plant-line-1", body=b"\x00\x01\xfe"))

many = [metric(f"m{i}", 3, i) for i in range(1000)]
got = roundtrip(Payload(metrics=many))
assert len(got.metrics) == 1000 and got.metrics[999].value == 999

# datatype and value survive exactly; an Int32 is not silently widened
assert roundtrip(Payload(metrics=[metric("n", 3, -7)])).metrics[0].datatype == 3
assert roundtrip(Payload(metrics=[metric("n", 3, 0)])).metrics[0].value == 0
assert roundtrip(Payload(metrics=[metric("n", 12, "")])).metrics[0].value == ""

# ---- a newer edge node: unknown fields are skipped by wire type
known = encode_payload(Payload(metrics=[metric("x", 3, 42)]))
got = decode_payload(b"\x08\x01" + b"\x30\x7b" + known)  # payload field 6 (varint) in the middle
assert got.timestamp == 1 and got.metrics == [Metric(name="x", datatype=3, value=42)], got

metric_with_unknown = (b"\x0a\x01x"              # name = "x"
                       b"\xf5\x01\x00\x00\x00\x00"  # field 30, fixed32, in the middle
                       b"\x20\x03"              # datatype = Int32
                       b"\x50\x2a")             # int_value = 42
got = decode_payload(b"\x12" + bytes([len(metric_with_unknown)]) + metric_with_unknown)
assert got.metrics == [Metric(name="x", datatype=3, value=42)], got

# the spec's own extra metric fields (is_historical, is_transient, metadata, properties) are ignored
legacy = (b"\x0a\x01x" b"\x20\x03" b"\x28\x01" b"\x30\x00" b"\x42\x00" b"\x4a\x00" b"\x50\x07")
assert decode_payload(b"\x12" + bytes([len(legacy)]) + legacy).metrics == [Metric(name="x", datatype=3, value=7)]

# ---- every refusal, with the sentence it promises
refuse(lambda: decode_payload(b"\x08\x80"), "the payload ends in the middle of a varint")
refuse(lambda: decode_payload(b"\x22\x05ab"), "a field claims 5 bytes but only 2 remain")
refuse(lambda: decode_payload(b"\x00"), "field number 0 is not valid")

refuse(lambda: encode_payload(Payload(metrics=[metric("x", 35, 1)])),
       "metric 'x' has datatype 35, which is not in the Sparkplug datatype table")
bad_type = b"\x0a\x01x\x20\x23"  # name "x", datatype 35
refuse(lambda: decode_payload(b"\x12" + bytes([len(bad_type)]) + bad_type),
       "metric 'x' has datatype 35, which is not in the Sparkplug datatype table")

refuse(lambda: encode_payload(Payload(metrics=[Metric(datatype=3, value=1)])),
       "a metric has neither a name nor an alias")
nameless = b"\x20\x03\x50\x01"  # datatype 3, int_value 1, no name or alias
refuse(lambda: decode_payload(b"\x12" + bytes([len(nameless)]) + nameless),
       "a metric has neither a name nor an alias")

two_values = b"\x0a\x01x\x20\x03\x50\x01\x7a\x03abc"  # name, datatype, int_value and string_value
refuse(lambda: decode_payload(b"\x12" + bytes([len(two_values)]) + two_values),
       "metric 'x' has more than one value field set")

refuse(lambda: decode_payload(b"\x00" * (MAX_PAYLOAD + 1)),
       f"the payload is {MAX_PAYLOAD + 1} bytes, over the {MAX_PAYLOAD}-byte maximum")
refuse(lambda: encode_payload(Payload(metrics=[metric("m", 3, 1)] * (MAX_METRICS + 1))),
       f"the payload has {MAX_METRICS + 1} metrics, over the {MAX_METRICS} maximum")

refuse(lambda: encode_payload(Payload(metrics=[metric("x", 3, "text")])),
       "metric 'x' has datatype 3 (Int32) but the value is a string")
wrong_value = b"\x0a\x01x\x20\x03\x7a\x03abc"  # Int32 datatype carrying a string value
refuse(lambda: decode_payload(b"\x12" + bytes([len(wrong_value)]) + wrong_value),
       "metric 'x' has datatype 3 (Int32) but carries string_value")

# a value that does not fit its datatype is a sentence, not a wrapped number
refuse(lambda: encode_payload(Payload(metrics=[metric("x", 5, 300)])),
       "metric 'x' is UInt8 but 300 is outside 0 to 255")
refuse(lambda: encode_payload(Payload(metrics=[metric("x", 3, 2 ** 40)])),
       f"metric 'x' is Int32 but {2 ** 40} is outside {-2 ** 31} to {2 ** 31 - 1}")
print("ok")
