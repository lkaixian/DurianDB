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
                {"kind": "HIGH_LIMIT", "limit": 1, "duration": -1}, {"kind": "NO_UPDATE", "seconds": True}):
    try:
        make_detector(bad_cfg)
        raise AssertionError(bad_cfg)
    except ValueError:
        pass
print("ok")
