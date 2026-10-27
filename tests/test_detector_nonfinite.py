"""A non-finite reading is unknown, not evidence of normal: it holds every value-driven detector.

ISA-18.2: a NaN, +inf or -inf sample proves neither that the condition still holds nor that it has
returned to normal. Feeding one into `value > limit` reads as "false" and clears a live alarm, which
the next good sample raises again: RTN -> re-raise chatter with no process change behind it.
Run: python tests/test_detector_nonfinite.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.core import ChangeDetector, HighLimit, NoChange, Point, RateOfChange, Smoothness  # noqa: E402

NONFINITE = (float("nan"), float("inf"), float("-inf"))

# ---- a live alarm stays live: no clear event, no clear, no re-raise on the next good sample
for bad in NONFINITE:
    p = Point("p")
    d = HighLimit(10, deadband=0)
    assert d.check(p, 11.0, 0.0).message == "11.0 > 10"
    assert d.check(p, bad, 1.0) is None, f"{bad!r} raised or cleared something"
    assert d.event is not None and d.event.cleared_ts is None, f"{bad!r} cleared a live alarm"
    assert d.check(p, 11.0, 2.0) is None and d.event is not None, "the alarm holds, no RTN/re-raise churn"

# ---- a non-finite sample never raises a fresh alarm
for bad in NONFINITE:
    p = Point("p")
    d = HighLimit(10, deadband=0)
    assert d.check(p, bad, 0.0) is None and d.event is None, f"{bad!r} raised an alarm"

# ---- and no value-driven detector takes a side effect from it
p = Point("p")

r = RateOfChange(2.0)
assert r.check(p, 10.0, 0.0) is None and r.check(p, 20.0, 1.0) is not None  # 10/s: raised
prev, held = r.prev, r.event
assert r.check(p, float("nan"), 2.0) is None and r.prev == prev and r.event is held

c = ChangeDetector(0.5)
assert c.check(p, float("inf"), 0.0) is None and c.prev is None, "inf must not become the change baseline"
assert c.check(p, 1.0, 1.0) is None, "the first real sample has nothing to change from"

n = NoChange(5.0)
assert n.check(p, 1.0, 0.0) is None
assert n.check(p, float("-inf"), 1.0) is None and n.ref == 1.0 and n.changed == 0.0

s = Smoothness(1.0, samples=3)
assert s.check(p, 1.0, 0.0) is None
assert s.check(p, float("inf"), 1.0) is None and len(s.window) == 1, "a non-finite sample must not join the window"

print("ok")
