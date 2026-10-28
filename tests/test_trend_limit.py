"""TREND_LIMIT raises BEFORE a limit is crossed, from the fitted slope. Run: python tests/test_trend_limit.py"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.core import Point, make_detector  # noqa: E402

p = Point("tank")
CFG = {"kind": "TREND_LIMIT", "limit": 100, "direction": "above", "horizon": 600, "window": 20}


def run(cfg, values, step=10.0, t0=0.0):
    d = make_detector(cfg)
    first = None
    for i, v in enumerate(values):
        ev = d.check(p, v, t0 + i * step)
        if ev and first is None:
            first = i
    return d, first


# ---- a ramp of 1/10s toward 100 from 0: ETA falls below 600s once the value passes 40
d, first = run(CFG, [i for i in range(60)])
assert first is not None and d.event is not None, "ramp to the limit never alarmed"
assert 38 <= first <= 46, f"alarmed at sample {first}, ETA was not yet inside the horizon (or was late)"
assert d.event.message.startswith("will reach 100 in about ") and d.event.message.endswith("at the current rate"), d.event.message
assert "min" in d.event.message

# ---- below direction mirrors it
d, first = run({**CFG, "limit": 0, "direction": "below"}, [100 - i for i in range(70)])
assert first is not None and 38 <= first <= 46, first

# ---- a flat noisy series never alarms, whatever the seed
for seed in range(100):
    rnd = random.Random(seed)
    d, first = run({**CFG, "limit": 60}, [50 + rnd.gauss(0, 2) for _ in range(300)])
    assert first is None, f"seed {seed} alarmed on noise at sample {first}"

# ---- a ramp heading away from the limit never alarms
assert run(CFG, [50 - i for i in range(40)])[1] is None
assert run({**CFG, "direction": "below", "limit": 0}, [10 + i for i in range(40)])[1] is None

# ---- already past the limit: that is the HIGH/LOW detector's job
assert run(CFG, [100 + i for i in range(30)])[1] is None
# and if it crosses while raised, this alarm steps aside
d, first = run(CFG, [i for i in range(60)])
d.check(p, 130.0, 10_000.0)
assert d.event is None

# ---- the alarm clears when the ramp flattens
d, first = run(CFG, [i for i in range(45)])
assert d.event is not None
ev = d.event
for i in range(45, 90):
    d.check(p, 44.0, i * 10.0)
assert d.event is None and ev.cleared_ts is not None, "did not clear on a flat series"

# ---- time-aware: the same values arriving 10x slower are 10x farther away
assert run(CFG, [i for i in range(40)], step=100.0)[1] is None

# ---- non-finite and held state
d, first = run(CFG, [i for i in range(45)])
held = d.event
assert held is not None
for bad in (float("nan"), float("inf"), float("-inf")):
    assert d.check(p, bad, 460.0) is None and d.event is held and held.cleared_ts is None
n = len(d.window)
d.check(p, float("nan"), 470.0)
assert len(d.window) == n, "a non-finite sample joined the window"

# ---- config refusals name the property
for bad, prop in (({"limit": "x"}, "limit"), ({"limit": float("nan")}, "limit"), ({"direction": "up"}, "direction"),
                  ({"horizon": 59}, "horizon"), ({"horizon": 86401}, "horizon"), ({"horizon": "1"}, "horizon"),
                  ({"window": 4}, "window"), ({"window": 1001}, "window"), ({"window": 5.5}, "window"), ({"window": True}, "window")):
    try:
        make_detector({**CFG, **bad})
    except ValueError as e:
        assert str(e).startswith(prop), (bad, str(e))
    else:
        raise AssertionError(f"{bad} was accepted")
for missing in ("limit", "direction", "horizon"):
    cfg = {k: v for k, v in CFG.items() if k != missing}
    try:
        make_detector(cfg)
    except ValueError as e:
        assert str(e).startswith(missing), str(e)
    else:
        raise AssertionError(f"missing {missing} accepted")
make_detector({**CFG, "horizon": 60, "window": 5})
make_detector({**CFG, "horizon": 86400, "window": 1000})
print("ok")
