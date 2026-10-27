"""The shared ISO-8601 formatters and CSV writer, and the exact output of every CSV path. Run: python tests/test_iso_csv.py"""
import io
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import export, pointcsv  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.core import Event, Level  # noqa: E402
from durian.csvfmt import csv_bytes, iso, iso_millis  # noqa: E402
from _support import new_store  # noqa: E402

T = 1_700_000_000.0  # 2023-11-14T22:13:20Z

# ---- the one ISO formatter: whole seconds, a fraction, a missing time
assert iso(T) == "2023-11-14T22:13:20Z"
assert iso(T + 0.999) == "2023-11-14T22:13:20Z"  # a fraction is truncated, never rounded
assert iso(0) == "1970-01-01T00:00:00Z"
assert iso(None) == ""
assert iso_millis(T) == "2023-11-14T22:13:20.000Z"
assert iso_millis(T + 0.125) == "2023-11-14T22:13:20.125Z"

# ---- the one CSV writer: quoting, empty cells, and '\n' on every platform
assert csv_bytes(["a", "b"], [[1, "x,y"], ['q"z', ""]]) == b'a,b\n1,"x,y"\n"q""z",\n'
assert b"\r" not in csv_bytes(["a"], [[1]])

store = new_store()
seed(store)
app = App(store, Path(tempfile.mkdtemp()) / "backups")
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


store.add_history([("boiler-temp", T + 10, 80.0), ("boiler-temp", T + 20, 90.0), ("boiler-temp", T + 30, 85.0, 1),
                   ("pump-setpoint", T + 0.0, 1.0), ("pump-setpoint", T + 0.125, 2.25), ("pump-setpoint", T + 1.5, 3.0)])
e1 = store.add_event(Event("pump-setpoint", Level.WARNING, "too hot, really", T + 0.5))
e2 = store.add_event(Event("pump-setpoint", Level.INFORMATION, "just news", T + 2.0))
store.clear_event(e1, T + 3.0)  # a returned alarm leaves its cleared time
store.ack_event(e2, "admin", T + 4.0)  # an active alarm leaves its acknowledger and time

# ---- history export: timestamp_utc, epoch_seconds, value
assert app.maint.export_history("pump-setpoint", T, T + 10) == (
    b"timestamp_utc,epoch_seconds,value\n"
    b"2023-11-14T22:13:20.000Z,1700000000.000,1.0\n"
    b"2023-11-14T22:13:20.125Z,1700000000.125,2.25\n"
    b"2023-11-14T22:13:21.500Z,1700000001.500,3.0\n")

# ---- report summary CSV: whole-second from and to, empty for a value that is not there
status, raw = call("GET", "/api/reports/summary", **{"from": str(T), "to": str(T + 100), "format": "csv", "tag": "area:boiler"})
assert status == 200 and raw.data == (
    b"point,unit,description,samples,min,max,avg,first,last,bad_samples,alarms,from,to\n"
    b"boiler-temp,C,,3,80.0,90.0,85.0,80.0,85.0,0,0,2023-11-14T22:13:20Z,2023-11-14T22:15:00Z\n"), raw.data

# ---- events export CSV: newest first, a missing time is an empty cell
status, raw = call("GET", "/api/events/search", **{"format": "csv", "state": "all"})
assert status == 200 and raw.data == (
    b"raised_utc,point,level,message,state,held,acknowledged_by,acknowledged_utc,returned_to_normal_utc\n"
    b"2023-11-14T22:13:22Z,pump-setpoint,INFORMATION,just news,active_acked,,admin,2023-11-14T22:13:24Z,\n"
    b'2023-11-14T22:13:20Z,pump-setpoint,WARNING,"too hot, really",rtn_unacked,,,,2023-11-14T22:13:23Z\n'), raw.data

# ---- point CSV: common settings in columns, the rest as compact JSON
assert pointcsv.export([{"name": "p1", "source": "demo", "tags": {"area": "x"}, "settable": True,
                         "read_roles": ["user"], "generator": {"kind": "constant", "value": 3}}]) == (
    b"name,source,folder,unit,description,tags,logging,tolerance,settable,read_roles,set_roles,eu_low,eu_high,decimals,set_min,set_max,max_step,confirm,extra\n"
    b'p1,demo,,,,area:x,,,true,user,,,,,,,,,"{""generator"":{""kind"":""constant"",""value"":3}}"\n')

# ---- the bundle's own CSV members: same formatter, their own column sets
data, _name, _manifest = export.build(store, days=2, points=["pump-setpoint"], now=T + 7200)
z = zipfile.ZipFile(io.BytesIO(data))
assert z.read("history/pump-setpoint.csv") == (
    b"timestamp_utc,value,quality\n"
    b"2023-11-14T22:13:20.000Z,1.0,good\n"
    b"2023-11-14T22:13:20.125Z,2.25,good\n"
    b"2023-11-14T22:13:21.500Z,3.0,good\n")
assert z.read("events.csv") == (
    b"time_utc,point,level,message,state,cleared\n"
    b'2023-11-14T22:13:20.500Z,pump-setpoint,WARNING,"too hot, really",rtn_unacked,2023-11-14T22:13:23.000Z\n'
    b"2023-11-14T22:13:22.000Z,pump-setpoint,INFORMATION,just news,active_acked,\n")

print("ok")
