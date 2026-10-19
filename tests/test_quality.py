"""Data quality and source timestamps. Run: python tests/test_quality.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.core import Engine, HighLimit, Logging, NoUpdate, Point, Quality, Source  # noqa: E402
from durian.ingest import HttpInSource, parse_quality  # noqa: E402
from durian.mqtt_source import stamp  # noqa: E402
from durian.polled import PolledSource  # noqa: E402
from _support import new_store  # noqa: E402

G, U, B = Quality.GOOD, Quality.UNCERTAIN, Quality.BAD
events = []

# ---- a BAD update holds the last value, is logged once, never alarms, never counts as an update
p = Point("p", detectors=[HighLimit(10), NoUpdate(5)])
p.update(20.0, 100.0, events.append)
assert p.quality == G and len(events) == 1
p.update(None, 101.0, events.append, B)
p.update(None, 102.0, events.append, B)
assert p.last == 20.0 and p.last_ts == 100.0 and p.quality == B and len(events) == 1
assert p.take_unsaved() == [(100.0, 20.0, 0), (101.0, 20.0, 2)]  # one bad row, not one per cycle
p.update(20.0, 103.0, events.append)  # recovered with the same value: still logged, the gap is visible
assert p.quality == G and p.take_unsaved() == [(103.0, 20.0, 0)]
assert Point("n", logging=Logging.NEVER).update(None, 1, events.append, B) is None

# ---- the engine: source time kept, future clocks doubted, older-than-known values dropped, NoUpdate on our clock
class Feed(Source):
    out = []
    def poll(self, ts):
        out, self.out = self.out, []
        return out

eng, s, q = Engine(), Feed("s"), Point("q", detectors=[NoUpdate(30)])
s.add(q)
eng.sources.append(s)
seen = []
eng.publishers.append(lambda pt, v, t: seen.append((v, t, pt.quality)))
s.out = [("q", 1.0, 990.0)]
eng.tick(1000.0)
assert (q.last_ts, q.last_recv, q.quality) == (990.0, 1000.0, G)
s.out = [("q", 2.0, 5000.0)]  # a device clock far in the future
eng.tick(1010.0)
assert (q.last, q.last_ts, q.quality) == (2.0, 1010.0, U)
s.out = [("q", 3.0, 900.0)]  # older than what we have
eng.tick(1020.0)
assert q.last == 2.0
s.out = [("q", 4.0, 1015.0, B)]
eng.tick(1025.0)
assert (q.last, q.quality) == (2.0, B) and seen[-1][2] == B  # published as held value with bad quality
q2 = Point("q2", detectors=[NoUpdate(30)])
s.add(q2)
s.out = [("q2", 1.0, 100.0)]  # old source time, but just received: not "no update"
eng.tick(2000.0)
assert not any(e.point == "q2" for e in eng.events)
eng.tick(2040.0)
assert any(e.point == "q2" for e in eng.events)

# ---- parse_quality
assert parse_quality(None) == G and parse_quality("Bad") == B and parse_quality(1) == U and parse_quality("2") == B
for bad in ("meh", 7, [1], "x"):
    try:
        parse_quality(bad)
        raise AssertionError(bad)
    except ValueError:
        pass

# ---- HTTP ingest carries quality, rejects a bad one
h = HttpInSource("h")
h.add(Point("t"))
acc, rej = h.submit([("t", 1.0, 50.0, 0, "uncertain"), ("t", 2.0, 51.0, 1, "dubious")], now=60.0)
assert acc == 1 and "quality" in rej[0]["reason"]
assert h.poll(0) == [("t", 1.0, 50.0, U)]

# ---- MQTT payload stamps
assert stamp(b'{"value": 1, "ts": 1700000000, "quality": "bad"}') == (1700000000.0, B)
assert stamp(b"21.5") == (None, G) and stamp(b'{"value": 1, "quality": "??"}') == (None, U) and stamp(b"\xff") == (None, G)

# ---- a polled source that fails marks every point BAD; points a fetch could not fill are BAD too
class Flaky(PolledSource):
    mode = "ok"
    def fetch(self):
        if self.mode == "down":
            raise OSError("down")
        return {"a": 1.0}, ["b: missing"]

f = Flaky("f")
f.add(Point("a"))
f.add(Point("b"))
f.cycle()
got = f.poll(0)
assert got["a"][0] == 1.0 and got["a"][2] == G and got["b"][0] is None and got["b"][2] == B
f.mode, f.next_try = "down", 0
f.cycle()
assert all(v[2] == B for v in f.poll(0).values())

# ---- history keeps quality; charts, ranges and CSV never see bad rows
st = new_store()
st.add_history([("x", 100.0, 1.0), ("x", 101.0, 2.0, 0), ("x", 102.0, 2.0, 2), ("x", 103.0, 3.0, 1)])
assert [r["ts"] for r in st.history_range("x", 0, 200, 10)] == [100.0, 101.0, 103.0]
assert st.q("select quality from history where ts=102")[0]["quality"] == 2
assert [r["value"] for r in st.series("x", 0, 10, "count")] == [3]
assert st.series("x", 0, 1, "last")[-1]["value"] == 3.0
assert [r["ts"] for r in st.recent("x")] == [100.0, 101.0, 103.0]
print("ok")
