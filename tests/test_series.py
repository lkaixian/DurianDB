"""Check rollups and input validation on an in-memory history. Run: python tests/test_series.py"""
import os
import sqlite3
import sys
from pathlib import Path

os.environ["DURIAN_MQTT_CREDS"] = "none"  # keep the MQTT source out of the test
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import server  # noqa: E402

server.db = sqlite3.connect(":memory:")
server.db.execute("create table history(point text, ts real, value real)")
server.db.executemany("insert into history values ('boiler-temp', ?, ?)",
                      [(100, 10), (101, 20), (105, 60), (112, 5)])  # buckets of 10 s: 100 and 110


def got(rollup):
    return [(r["ts"], r["value"]) for r in server.series("boiler-temp", 0, 10, rollup)]


assert got("avg") == [(100, 30), (110, 5)]
assert got("min") == [(100, 10), (110, 5)]
assert got("max") == [(100, 60), (110, 5)]
assert got("last") == [(100, 60), (110, 5)]
assert got("count") == [(100, 3), (110, 1)]
assert server.series_request("point=nope")[0] == 400
assert server.series_request("point=boiler-temp&rollup=sum")[0] == 400
assert server.series_request("point=boiler-temp&window=abc")[0] == 400
assert server.series_request("point=boiler-temp&window=100&bucket=500")[0] == 400
assert server.series_request("point=boiler-temp&rollup=last&window=3600")[0] == 200
print("ok")
