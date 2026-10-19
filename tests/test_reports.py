"""Summary report per point. Run: python tests/test_reports.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
ANON = app.auth.identity(None)


def call(ident, path, **q):
    try:
        return app.handle("GET", path, q, None, ident)
    except ApiError as e:
        return e.status, {"error": str(e)}


T = 2_000_000_000.0
store.add_history([("boiler-temp", T + 10, 80.0), ("boiler-temp", T + 20, 90.0), ("boiler-temp", T + 30, 85.0, 1),
                   ("boiler-temp", T + 40, 999.0, 2), ("boiler-temp", T - 5000, 1.0), ("line-pressure", T + 15, 6.0)])
store.run("insert into events(point,level,message,ts) values ('boiler-temp',4,'x',?)", (T + 20,))
s, doc = call(admin, "/api/reports/summary", **{"from": str(T), "to": str(T + 100)})
rows = {r["point"]: r for r in doc["items"]}
b = rows["boiler-temp"]
assert (b["samples"], b["min"], b["max"], b["avg"], b["first"], b["last"], b["bad_samples"], b["alarms"]) == (3, 80.0, 90.0, 85.0, 80.0, 85.0, 1, 1), b
assert rows["line-pressure"]["samples"] == 1 and rows["pump-setpoint"]["samples"] == 0 and rows["pump-setpoint"]["min"] is None
assert rows["boiler-temp"]["unit"] == "C"

# narrowing, csv, limits, access
assert [r["point"] for r in call(admin, "/api/reports/summary", **{"from": str(T), "to": str(T + 100), "tag": "area:line"})[1]["items"]] == ["line-pressure", "pump-setpoint"]
s, raw = call(admin, "/api/reports/summary", **{"from": str(T), "to": str(T + 100), "format": "csv", "tag": "area:boiler"})
text = raw.data.decode().splitlines()
assert s == 200 and raw.filename == "report.csv" and text[0].startswith("point,unit,description,samples") and text[1].startswith("boiler-temp,C,,3,80.0,90.0,85.0,80.0,85.0,1,1,")
assert call(admin, "/api/reports/summary", hours="x")[0] == 400 and call(admin, "/api/reports/summary", hours="1000")[0] == 400
assert call(admin, "/api/reports/summary", watchlist="ghost")[0] == 404
assert call(ANON, "/api/reports/summary")[1]["items"] == []  # anonymous sees no points, so the report is empty
print("ok")
