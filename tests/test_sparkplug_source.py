"""Sparkplug B source: births, data, aliases, value types, sequence gaps and deaths, with no broker.
Run: python tests/test_sparkplug_source.py"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.config import SOURCE_TYPES, validate_source  # noqa: E402
from durian.core import Engine, Point, Quality  # noqa: E402
from durian.sparkplug_codec import Metric, Payload, encode_payload  # noqa: E402
from durian.sparkplug_source import SparkplugSource  # noqa: E402


class Fake:
    """A paho client stand-in: construction and stop must not touch a socket."""

    def __init__(self):
        self.stopped = False

    def connect_async(self, *a, **k): pass

    def loop_start(self): pass

    def loop_stop(self): self.stopped = True

    def disconnect(self): self.stopped = True

    def subscribe(self, *a, **k): pass


def source():
    return SparkplugSource("plant", group="Plant", factory=lambda n: Point(n), client=Fake())


def m(name=None, datatype=None, value=None, **kw):
    return Metric(name=name, datatype=datatype, value=value, **kw)


def send(src, topic, payload, ts):
    src.feed(topic, encode_payload(payload))
    return src.poll(ts)


# ---- NBIRTH creates node points, NDATA updates them, and a device adds its own level
src = source()
out = send(src, "spBv1.0/Plant/NBIRTH/line1", Payload(seq=0, metrics=[m("bdSeq", 4, 1), m("temp", 10, 21.5)]), 100.0)
assert out["line1/temp"] == (21.5, 100.0, Quality.GOOD), out
assert out["line1/bdSeq"] == (1.0, 100.0, Quality.GOOD), out
out = send(src, "spBv1.0/Plant/NDATA/line1", Payload(seq=1, metrics=[m("temp", 10, 22.0)]), 101.0)
assert out == {"line1/temp": (22.0, 101.0, Quality.GOOD)}, out
send(src, "spBv1.0/Plant/DBIRTH/line1/pump", Payload(seq=2, metrics=[m("speed", 9, 1450.0)]), 102.0)
out = send(src, "spBv1.0/Plant/DDATA/line1/pump", Payload(seq=3, metrics=[m("speed", 9, 1470.0)]), 103.0)
assert out == {"line1/pump/speed": (1470.0, 103.0, Quality.GOOD)}, out
assert src.gaps == 0 and src.error is None, (src.gaps, src.error)

# ---- a DDATA alias resolves through the DBIRTH, and one with no birth is dropped and counted
src = source()
send(src, "spBv1.0/Plant/DBIRTH/line1/pump",
     Payload(seq=0, metrics=[Metric(name="speed", alias=7, datatype=9, value=1450.0)]), 100.0)
out = send(src, "spBv1.0/Plant/DDATA/line1/pump", Payload(seq=1, metrics=[Metric(alias=7, datatype=9, value=1460.0)]), 101.0)
assert out == {"line1/pump/speed": (1460.0, 101.0, Quality.GOOD)}, out
before = src.dropped
out = send(src, "spBv1.0/Plant/DDATA/line1/other", Payload(seq=2, metrics=[Metric(alias=99, datatype=9, value=1.0)]), 102.0)
assert out == {} and src.dropped == before + 1, (out, src.dropped)

# ---- the node-level birth also declares aliases for NDATA
src = source()
send(src, "spBv1.0/Plant/NBIRTH/line1",
     Payload(seq=0, metrics=[Metric(name="temp", alias=3, datatype=10, value=1.0)]), 100.0)
out = send(src, "spBv1.0/Plant/NDATA/line1", Payload(seq=1, metrics=[Metric(alias=3, datatype=10, value=2.0)]), 101.0)
assert out == {"line1/temp": (2.0, 101.0, Quality.GOOD)}, out

# ---- numeric datatypes become numbers; booleans, nulls, non-finite floats and bytes are dropped
src = source()
out = send(src, "spBv1.0/Plant/NBIRTH/n", Payload(seq=0, metrics=[
    m("i", 3, -7), m("u", 7, 42), m("f", 9, 1.5), m("d", 10, 2.5),
    m("bool", 11, True), m("null", 10, is_null=True), m("nan", 10, float("nan")),
    m("inf", 10, float("inf")), m("bytes", 17, b"\x00"), m("unknown", 0),
]), 100.0)
assert out["n/i"] == (-7.0, 100.0, Quality.GOOD), out
assert out["n/u"] == (42.0, 100.0, Quality.GOOD), out
assert out["n/f"] == (1.5, 100.0, Quality.GOOD), out
assert out["n/d"] == (2.5, 100.0, Quality.GOOD), out
assert src.dropped == 6, (src.dropped, out)
assert not any(k in out for k in ("n/bool", "n/null", "n/nan", "n/inf", "n/bytes", "n/unknown")), out

# ---- text datatypes make text points; a numeric point does not silently take text
src = source()
out = send(src, "spBv1.0/Plant/NBIRTH/n", Payload(seq=0, metrics=[m("status", 12, "Running"), m("note", 14, "line one")]), 100.0)
assert out["n/status"] == ("Running", 100.0, Quality.GOOD), out
assert out["n/note"] == ("line one", 100.0, Quality.GOOD), out
assert src.points["n/status"].text is True and src.points["n/note"].text is True
src2 = source()
src2.add(Point("n/status"))
out = send(src2, "spBv1.0/Plant/NBIRTH/n", Payload(seq=0, metrics=[m("status", 12, "Running")]), 100.0)
assert out == {} and src2.dropped == 1, (out, src2.dropped)

# ---- the metric's own millisecond timestamp wins when it is sane; a wild one falls back to the tick
src = source()
out = send(src, "spBv1.0/Plant/NBIRTH/n",
           Payload(seq=0, metrics=[m("temp", 10, 20.0, timestamp=1_700_000_000_000)]), 1_700_000_100.0)
assert out["n/temp"] == (20.0, 1_700_000_000.0, Quality.GOOD), out
out = send(src, "spBv1.0/Plant/NDATA/n",
           Payload(seq=1, metrics=[m("temp", 10, 21.0, timestamp=9_000_000_000_000)]), 1_700_000_101.0)
assert out["n/temp"] == (21.0, 1_700_000_101.0, Quality.GOOD), out

# ---- a skipped or backwards seq is a readable gap; a new bdSeq is a new session, not a gap
src = source()
send(src, "spBv1.0/Plant/NBIRTH/line1", Payload(seq=0, metrics=[m("bdSeq", 4, 1)]), 100.0)
send(src, "spBv1.0/Plant/NDATA/line1", Payload(seq=1, metrics=[m("temp", 10, 1.0)]), 101.0)
out = send(src, "spBv1.0/Plant/NDATA/line1", Payload(seq=3, metrics=[m("temp", 10, 3.0)]), 102.0)
assert out["line1/temp"] == (3.0, 102.0, Quality.GOOD), out  # the value that did arrive is still used
assert src.gaps == 1, src.gaps
assert "expected 2" in src.error and "received 3" in src.error, src.error
assert "SparkplugError" not in src.error and "Exception" not in src.error, src.error
send(src, "spBv1.0/Plant/NBIRTH/line1", Payload(seq=0, metrics=[m("bdSeq", 4, 2)]), 103.0)
send(src, "spBv1.0/Plant/NDATA/line1", Payload(seq=1, metrics=[m("temp", 10, 4.0)]), 104.0)
assert src.gaps == 1, src.gaps  # the new session did not report the old sequence as a gap
send(src, "spBv1.0/Plant/NDATA/line1", Payload(seq=0, metrics=[m("temp", 10, 5.0)]), 105.0)
assert src.gaps == 2 and "expected 2" in src.error and "received 0" in src.error, (src.gaps, src.error)

# ---- NDEATH makes the node's points BAD without inventing a value, and only a new NBIRTH revives them
src = source()
src.connected = True
engine = Engine()
engine.sources.append(src)
src.feed("spBv1.0/Plant/NBIRTH/line1", encode_payload(Payload(seq=0, metrics=[m("bdSeq", 4, 1), m("temp", 10, 21.5)])))
engine.tick(100.0)
point = src.points["line1/temp"]
assert point.quality == Quality.GOOD and point.last == 21.5
src.feed("spBv1.0/Plant/NDEATH/line1", encode_payload(Payload(metrics=[m("bdSeq", 4, 1)])))
engine.tick(101.0)
assert point.quality == Quality.BAD and point.last == 21.5, (point.quality, point.last)
before = src.dropped
src.feed("spBv1.0/Plant/NDATA/line1", encode_payload(Payload(seq=1, metrics=[m("temp", 10, 99.0)])))
engine.tick(102.0)
assert src.dropped == before + 1 and point.quality == Quality.BAD and point.last == 21.5, (src.dropped, point.quality, point.last)
src.feed("spBv1.0/Plant/NBIRTH/line1", encode_payload(Payload(seq=0, metrics=[m("bdSeq", 4, 1), m("temp", 10, 22.5)])))
engine.tick(103.0)
assert point.quality == Quality.GOOD and point.last == 22.5, (point.quality, point.last)

# ---- a malformed payload, an unknown message type and a foreign topic are dropped, never raised
src = source()
before = src.dropped
src.feed("spBv1.0/Plant/NDATA/line1", b"\x08\x80")
assert src.poll(100.0) == {} and src.dropped == before + 1, src.dropped
for topic in ("spBv1.0/Plant/NCmd/line1", "spBv1.0/Other/NDATA/line1", "spBv1.0/Plant/NDATA"):
    src.feed(topic, b"")
assert src.poll(101.0) == {} and src.dropped == before + 4, src.dropped

# ---- config validation accepts the type and asks for the group and a usable credentials file
assert "sparkplug" in SOURCE_TYPES
bad = validate_source("sp", {"type": "sparkplug", "creds": "no-such-file.json"})
assert {"creds", "group"} <= {e["property"] for e in bad}, bad
with tempfile.TemporaryDirectory() as folder:
    creds = Path(folder) / "creds.json"
    creds.write_text(json.dumps({"host": "h", "port": 8883, "ca": "ca.pem", "user": "u", "password": "p"}), encoding="utf-8")
    assert validate_source("sp", {"type": "sparkplug", "group": "Plant", "creds": str(creds)}) == []

# ---- stop() stops the loop and disconnects, like the MQTT source
fake = Fake()
SparkplugSource("plant", group="Plant", client=fake).stop()
assert fake.stopped

print("ok")
