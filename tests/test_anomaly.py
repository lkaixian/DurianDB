"""An ANOMALY detector judges a sample against the window before it, not the one it joins. Run: python tests/test_anomaly.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.core import Level, Point, make_detector  # noqa: E402

p = Point("temp")


def run(d, values, t0=0.0):
    """Feed values one per second; return the events the detector produced."""
    out = []
    for i, v in enumerate(values):
        ev = d.check(p, v, t0 + i)
        if ev:
            out.append(ev)
    return out


# ---- a lone spike raises; the samples before it set the scale, so it cannot dilute its own z
d = make_detector({"kind": "ANOMALY", "z_limit": 3, "window": 10})
evs = run(d, [10, 11, 10, 11, 10, 11, 10, 11, 10, 13])
assert len(evs) == 1 and evs[0].level == Level.WARNING, evs
assert "13 is" in evs[0].message and "sd from mean" in evs[0].message and "over 9 samples" in evs[0].message, evs[0].message
assert evs[0].point == "temp"

# ---- a level shift settles: the window learns the new level and the alarm clears on its own
raised = evs[0]
run(d, [20] * 10, t0=100)
assert raised.cleared_ts is not None, "the detector did not clear after the window moved"

# ---- a steady ramp is not an outlier: the z of a straight line is bounded by sqrt(6) however steep it is
d = make_detector({"kind": "ANOMALY", "z_limit": 3, "window": 4})
assert run(d, [i * 100 for i in range(300)]) == []

# ---- fewer than three samples, and a flat window, have no spread to measure: no verdict, no exception
d = make_detector({"kind": "ANOMALY", "z_limit": 3})
run(d, [1, 1])  # two samples: still too few to judge
assert d.check(p, 1e9, 10) is None, "one sample after two cannot be judged"
d = make_detector({"kind": "ANOMALY", "z_limit": 3})
assert run(d, [5, 5, 5, 5]) == [] and d.check(p, 500, 10) is None, "a flat window has no scale"

# ---- deadband: while raised, a value past the limit-minus-band keeps the alarm up even though it is inside
# the limit itself (z 1.98 < 2); a normal value clears it
d = make_detector({"kind": "ANOMALY", "z_limit": 2, "window": 10, "deadband": 0.5})
for i in range(20):
    d.check(p, [0, 20][i % 2], i)
assert d.check(p, 35, 30) is not None, "35 sd 2.5 away should raise"
assert d.check(p, 37, 31) is None and d.event is not None, "z 1.98 is inside the deadband: the alarm stays up"
assert d.check(p, 20, 32) is None and d.event is None, "back to normal: it clears"

# ---- and the config it refuses
for bad in ({"kind": "ANOMALY"}, {"kind": "ANOMALY", "z_limit": 0}, {"kind": "ANOMALY", "z_limit": float("nan")},
            {"kind": "ANOMALY", "z_limit": 3, "window": 2}, {"kind": "ANOMALY", "z_limit": 3, "window": "ten"},
            {"kind": "ANOMALY", "z_limit": 3, "deadband": -1}):
    try:
        make_detector(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"{bad} should be refused")

print("ok")
