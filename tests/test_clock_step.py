"""A backward wall-clock step must not freeze polled points. Run: python tests/test_clock_step.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.core import Engine, Level, Point, Source  # noqa: E402
from durian.polled import PolledSource  # noqa: E402


class Feed(Source):
    out = []

    def poll(self, ts):
        out, self.out = self.out, []
        return out


eng, s, p = Engine(), Feed("s"), Point("p")
s.add(p)
eng.sources.append(s)
s.out = [("p", 1.0, 1000.0)]
eng.tick(1000.0)
s.out = [("p", 2.0, 990.0)]  # ordinary out-of-order push: still dropped
eng.tick(1001.0)
assert p.last == 1.0 and not eng.events
s.out = [("p", 3.0, 500.0)]  # clock stepped back: tick time 500 < previous tick 1001
eng.tick(500.0)
assert p.last == 3.0 and p.last_ts == 500.0, (p.last, p.last_ts)
notes = [e for e in eng.events if "clock" in e.message]
assert len(notes) == 1 and notes[0].level == Level.INFORMATION, eng.events
eng.tick(501.0)
assert len([e for e in eng.events if "clock" in e.message]) == 1

# polled backoff must not depend on the wall clock
class Bad(PolledSource):
    def fetch(self):
        raise OSError("down")

b = Bad("b", period=10.0)
b.cycle()
assert abs(b.next_try - (time.monotonic() + 15.0)) < 1.0, b.next_try
print("ok")
