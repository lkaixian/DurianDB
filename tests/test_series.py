"""Check rollups on an in-memory history. Run: python tests/test_series.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.store import Store  # noqa: E402
from _support import new_store  # noqa: E402

st = new_store()
st.add_history([("p", 100, 10), ("p", 101, 20), ("p", 105, 60), ("p", 112, 5)])  # 10 s buckets: 100 and 110


def got(rollup):
    return [(r["ts"], r["value"]) for r in st.series("p", 0, 10, rollup)]


assert got("avg") == [(100, 30), (110, 5)]
assert got("min") == [(100, 10), (110, 5)]
assert got("max") == [(100, 60), (110, 5)]
assert got("last") == [(100, 60), (110, 5)]
assert got("count") == [(100, 3), (110, 1)]
assert got("first") == [(100, 10), (110, 5)]
assert got("sum") == [(100, 90), (110, 5)]
assert got("delta") == [(100, 50), (110, 0)]  # last minus first of each bucket
assert st.series("other", 0, 10, "avg") == []
print("ok")
