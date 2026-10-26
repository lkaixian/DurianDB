"""A rate-of-change alarm must not clear on a sample that carries no interval. Run: python tests/test_rate_same_timestamp.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.config import make_point  # noqa: E402


def det(**cfg):
    p = make_point("t", {"source": "v", "detectors": [{"kind": "RATE_OF_CHANGE", "per_second": 1, **cfg}]}, "v")
    return p, p.detectors[0]


# a repeated timestamp is not evidence of normality: the alarm stays up
p, d = det()
assert d.check(p, 0.0, 10.0) is None
assert d.check(p, 5.0, 11.0).message == "changing 5.00/s > 1/s"
assert d.check(p, 9.0, 11.0) is None, "the sample itself raises nothing"
assert d.event is not None and d.event.message == "changing 5.00/s > 1/s", "and it does not clear the alarm"
assert d.check(p, 9.0, 11.0) is None and d.event is not None, "repeating it changes nothing"

# an older timestamp is equally no evidence
assert d.check(p, 2.0, 10.5) is None and d.event is not None, "an out-of-order sample keeps the alarm"
assert d.prev == (5.0, 11.0), "and does not become the new reference"

# a real, slow interval clears it: 0.2 units in 9s is well under 1/s
assert d.check(p, 5.2, 20.0) is None and d.event is None

# and with no alarm up, a repeated timestamp simply does nothing
p2, d2 = det()
d2.check(p2, 0.0, 10.0)
assert d2.check(p2, 5.0, 10.0) is None and d2.event is None, "no spurious alarm from a zero interval"
assert d2.check(p2, 0.0, 20.0) is None and d2.event is None

print("ok")
