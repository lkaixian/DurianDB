"""A non-finite source timestamp (MQTT {"ts":NaN}) must not poison last_ts or history. Run: python tests/test_ts_nan_mqtt.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.core import Engine, Point, Quality, Source  # noqa: E402
from durian.ingest import parse_time  # noqa: E402
from durian.mqtt_source import stamp  # noqa: E402

for bad in (float("nan"), float("inf"), float("-inf"), "nan", "inf"):
    try:
        parse_time(bad)
        raise AssertionError(f"parse_time accepted {bad!r}")
    except ValueError:
        pass

assert stamp(b'{"value":5,"ts":NaN}')[0] is None  # an unusable ts is no ts


class Feed(Source):
    out = []

    def poll(self, ts):
        out, self.out = self.out, []
        return out


eng, s, p = Engine(), Feed("s"), Point("p")
s.add(p)
eng.sources.append(s)
eng.tick(1000.0)
# the engine seam too: a source that hands a NaN/-inf time straight to tick
for i, bad in enumerate((float("nan"), float("-inf"))):
    now = 1001.0 + i
    s.out = [("p", 5.0, bad)]
    eng.tick(now)
    assert p.last_ts == now and p.quality == Quality.UNCERTAIN, (bad, p.last_ts, p.quality)
    assert all(t == t and abs(t) != float("inf") for t, *_ in p.take_unsaved())
s.out = [("p", 6.0, 1000.5)]  # out-of-order guard still works
eng.tick(1010.0)
assert p.last == 5.0
print("ok")
