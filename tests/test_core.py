"""Core behavior checks. Run: python tests/test_core.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.core import (Engine, HighLimit, Level, LowLimit, NoUpdate, Point, RateOfChange, Source,  # noqa: E402
                         make_detector)


class Scripted(Source):
    def __init__(self, name, series):
        super().__init__(name)
        self.series = iter(series)

    def poll(self, ts):
        v = next(self.series)
        return {} if v is None else {"p": v}


def run(series, point):
    src = Scripted("s", series)
    src.add(point)
    eng = Engine()
    eng.sources.append(src)
    for t in range(len(series)):
        eng.tick(float(t))
    return eng, point


# logging: on-change skips repeats, tolerance skips small moves
eng, p = run([20, 20, 95, 96, 30], Point("p", detectors=[HighLimit(90, Level.URGENT)]))
assert [v for _, v in p.cache] == [20, 95, 96, 30]
assert len(eng.events) == 1 and eng.events[0].level == Level.URGENT
assert eng.events[0].cleared_ts == 4.0  # returned to normal at the last sample
eng, p = run([10, 10.2, 10.4, 11], Point("p", tolerance=0.5))
assert [v for _, v in p.cache] == [10, 11]

# duration: a spike shorter than the duration raises nothing
eng, _ = run([95, 50, 95, 95, 95], Point("p", detectors=[HighLimit(90, duration=2)]))
assert len(eng.events) == 1 and eng.events[0].ts == 4.0

# low limit, rate of change
eng, _ = run([5, 50], Point("p", detectors=[LowLimit(10)]))
assert len(eng.events) == 1 and eng.events[0].cleared_ts == 1.0
eng, _ = run([0, 1, 40], Point("p", detectors=[RateOfChange(10)]))
assert len(eng.events) == 1 and eng.events[0].ts == 2.0

# no update: silence raises, a value clears
eng, _ = run([1, None, None, None, 2], Point("p", detectors=[NoUpdate(2)]))
assert len(eng.events) == 1 and eng.events[0].ts == 3.0 and eng.events[0].cleared_ts == 4.0

# unsaved samples drain once; cache stays bounded
p = Point("p", logging=2)
for i in range(300):
    p.update(float(i), float(i), lambda e: None)
assert len(p.cache) == 100 and len(p.take_unsaved()) == 300 and p.take_unsaved() == []

# a failing source does not stop the others, and reports its error
class Broken(Source):
    def poll(self, ts):
        raise RuntimeError("boom")

good = Scripted("g", [1]); good.add(Point("p"))
bad = Broken("b")
eng = Engine(); eng.sources += [bad, good]; eng.tick(0.0)
assert bad.error == "RuntimeError: boom" and good.points["p"].last == 1

# config validation
assert make_detector({"kind": "HIGH_LIMIT", "limit": 5, "level": "urgent"}).level == Level.URGENT
for bad_cfg in ({"kind": "NOPE"}, {"kind": "HIGH_LIMIT"}, {"kind": "HIGH_LIMIT", "limit": 1, "level": "x"},
                {"kind": "HIGH_LIMIT", "limit": 1, "duration": -1}, {"kind": "LOW_LIMIT", "limit": 1, "deadband": -1}, {"kind": "HIGH_LIMIT", "limit": 1, "deadband": "x"}, {"kind": "NO_UPDATE", "seconds": True}):
    try:
        make_detector(bad_cfg)
        raise AssertionError(bad_cfg)
    except ValueError:
        pass
# MQTT payloads are untrusted: only finite numbers get through
from durian.mqtt_source import parse  # noqa: E402
assert parse(b"21.5") == 21.5 and parse(b'{"value": 3}') == 3.0 and parse(b"-4") == -4.0
for junk in (b"NaN", b"Infinity", b"-Infinity", b'{"value": NaN}', b"true", b'"12"', b"[1]", b"{}", b"\xff\xfe", b"", b"1e999"):
    assert parse(junk) is None, junk

# ---- hysteresis: a value hovering on the limit raises once; it clears only past the deadband
for kind, vals in (("HIGH_LIMIT", [9, 11, 10.5, 9.5, 11, 8.9, 11]), ("LOW_LIMIT", [11, 9, 9.5, 10.5, 9, 11.1, 9])):
    eng, _ = run(vals, Point("p", detectors=[make_detector({"kind": kind, "limit": 10, "deadband": 1})]))
    assert len(eng.events) == 2 and eng.events[0].cleared_ts is not None and eng.events[1].cleared_ts is None, (kind, eng.events)
eng, _ = run([9, 11, 9.5, 11], Point("p", detectors=[make_detector({"kind": "HIGH_LIMIT", "limit": 10})]))
assert len(eng.events) == 2  # without a deadband it chatters, as before

from durian.core import alarm_state  # noqa: E402
assert [alarm_state(None, None), alarm_state(5, None), alarm_state(None, 6), alarm_state(5, 6)] == ["active_unacked", "active_acked", "rtn_unacked", "closed"]

# ---- off-delay: the alarm stays until the value has been normal for off_delay seconds, and a relapse restarts the wait
eng, _ = run([11, 9, 9, 11, 9, 9, 9, 9], Point("p", detectors=[make_detector({"kind": "HIGH_LIMIT", "limit": 10, "off_delay": 2})]))
assert len(eng.events) == 1 and eng.events[0].cleared_ts == 6.0, [e.cleared_ts for e in eng.events]
try:
    make_detector({"kind": "HIGH_LIMIT", "limit": 1, "off_delay": -1})
    raise AssertionError("negative off_delay")
except ValueError:
    pass

# ---- more detector kinds
from durian.core import state_label  # noqa: E402
pt = lambda **k: Point("p", detectors=[make_detector(k)], meta={"states": {"0": "Stopped", "1": "Running", "2": "Fault"}})
eng, _ = run([0, 1, 2, 2, 1, 2], pt(kind="STATE", states=[2], level="URGENT"))
assert [e.message for e in eng.events] == ["state is Fault", "state is Fault"] and eng.events[0].cleared_ts == 4.0 and eng.events[1].cleared_ts is None
assert state_label(pt(kind="CHANGE").__class__("x", meta={"states": {"1": "On"}}), 1.0) == "On" and state_label(Point("x"), 3.5) == "3.5"
eng, _ = run([5, 6, 11, 12, 8, 5], pt(kind="RANGE", low=0, high=10))
assert len(eng.events) == 1 and eng.events[0].cleared_ts == 4.0 and "outside" in eng.events[0].message
eng, _ = run([5, 15, 5], pt(kind="RANGE", low=10, high=20, mode="inside"))
assert len(eng.events) == 1 and "inside" in eng.events[0].message
eng, _ = run([0, 0, 1, 1, 2], pt(kind="CHANGE"))
assert [e.message for e in eng.events] == ["changed from Stopped to Running", "changed from Running to Fault"] and all(e.cleared_ts == e.ts for e in eng.events)
eng, _ = run([5.0, 5.05, 5.1, 5.0, 5.2, 6.0, 7.0], pt(kind="NO_CHANGE", seconds=3, tolerance=0.3))
assert len(eng.events) == 1 and eng.events[0].ts == 4.0 and eng.events[0].cleared_ts == 5.0, [(e.ts, e.cleared_ts) for e in eng.events]
for bad_cfg in ({"kind": "STATE"}, {"kind": "STATE", "states": []}, {"kind": "STATE", "states": ["a"]}, {"kind": "RANGE", "low": 5, "high": 5},
                {"kind": "RANGE", "low": 1, "high": 2, "mode": "near"}, {"kind": "NO_CHANGE", "seconds": 0}, {"kind": "CHANGE", "tolerance": -1}):
    try:
        make_detector(bad_cfg)
        raise AssertionError(bad_cfg)
    except ValueError:
        pass

# ---- smoothness (scatter over a window) and CUSUM (small steady drift)
eng, _ = run([5, 5, 5, 5, 5, 9, 1, 9, 1, 5, 5, 5, 5, 5, 5, 5], pt(kind="SMOOTHNESS", samples=4, limit=1))
assert len(eng.events) == 1 and "scatter" in eng.events[0].message and eng.events[0].cleared_ts is not None, [(e.ts, e.message) for e in eng.events]
eng, _ = run([5.0] * 12, pt(kind="SMOOTHNESS", samples=4, limit=1))
assert not eng.events  # a steady value is smooth
eng, _ = run([10, 10, 11, 11, 11, 11, 11, 11] + [10] * 30, pt(kind="CUSUM", target=10, slack=0.2, threshold=3))
assert len(eng.events) == 1 and "above 10" in eng.events[0].message and eng.events[0].ts == 5.0 and eng.events[0].cleared_ts is not None, [(e.ts, e.message, e.cleared_ts) for e in eng.events]
assert not run([9.9, 10.1] * 10, pt(kind="CUSUM", target=10, slack=0.2, threshold=3))[0].events  # noise around the target never accumulates
eng, _ = run([10, 9, 9, 9, 9], pt(kind="CUSUM", target=10, slack=0, threshold=3))
assert "below 10" in eng.events[0].message
for bad_cfg in ({"kind": "SMOOTHNESS", "limit": 1, "samples": 2}, {"kind": "SMOOTHNESS"}, {"kind": "CUSUM", "target": 1}, {"kind": "CUSUM", "target": 1, "threshold": 1, "slack": -1}):
    try:
        make_detector(bad_cfg)
        raise AssertionError(bad_cfg)
    except ValueError:
        pass

# ---- interval logging: one sample per period, as the last value or an aggregate, stamped with the period's end
from durian.core import Logging  # noqa: E402
for mode, want in (("instant", [(3.0, 4.0), (6.0, 7.0)]), ("avg", [(3.0, 2.5), (6.0, 6.0)]), ("min", [(3.0, 1.0), (6.0, 5.0)]), ("max", [(3.0, 4.0), (6.0, 7.0)])):
    p = Point("p", logging=Logging.INTERVAL, interval=3.0, interval_mode=mode)
    for t in range(7):
        p.update(float(t + 1), float(t), lambda e: None)
    assert [(a, b) for a, b, _ in p.take_unsaved()] == want, (mode, want)
print("ok")
