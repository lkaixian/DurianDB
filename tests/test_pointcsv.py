"""Points as CSV: export, round trip, validation of every row, dry run. Run: python tests/test_pointcsv.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import pointcsv  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
ANON = app.auth.identity(None)


def call(ident, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, ident)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


# ---- export carries the common settings in columns and the rest as JSON
s, raw = call(admin, "GET", "/api/export/points")
text = raw.data.decode()
assert s == 200 and raw.filename == "points.csv" and text.splitlines()[0].startswith("name,source,unit,description,tags,logging")
assert "boiler-temp,demo,C" in text and "area:boiler" in text and "generator" in text
assert call(ANON, "GET", "/api/export/points")[0] == 401

# ---- round trip: importing what was exported changes nothing
before = {p["name"]: {k: v for k, v in p.items() if k != "name"} for p in store.list("point")}
s, r = call(admin, "POST", "/api/import/points", text, apply="1")
assert s == 200 and r["created"] == 0 and r["updated"] == len(before) and r["applied"]
after = {p["name"]: {k: v for k, v in p.items() if k != "name"} for p in store.list("point")}
assert after == before, [(k, before[k], after.get(k)) for k in before if before[k] != after.get(k)][:2]

# ---- a new file: a dry run reports, apply writes
new = ("name,source,unit,tags,logging,settable,set_roles,set_max,extra\n"
       'csv-a,demo,bar,area:csv;line:1,ALL,true,user,8,"{""generator"":{""kind"":""constant"",""value"":3}}"\n'
       'csv-b,demo,,,,,,,"{""generator"":{""kind"":""constant"",""value"":4}}"\n')
s, r = call(admin, "POST", "/api/import/points", new)
assert s == 200 and r == {"applied": False, "created": 2, "updated": 0} and not store.get("point", "csv-a")
s, r = call(admin, "POST", "/api/import/points", new, apply="1")
a = store.get("point", "csv-a")
assert s == 200 and a["tags"] == {"area": "csv", "line": "1"} and a["settable"] is True and a["set_max"] == 8 and a["set_roles"] == ["user"] and a["generator"]["value"] == 3

# ---- every problem is reported with its line, nothing is written
bad = ("name,source,tolerance,settable,extra\n"
       "ok1,demo,,,\n"
       "bad1,ghost,,,\n"                      # unknown source
       "bad2,demo,x,,\n"                      # not a number
       "bad3,demo,,maybe,\n"                  # not a boolean
       'bad4,demo,,,"{not json"\n'            # not JSON
       "ok1,demo,,,\n")                       # listed twice
s, r = call(admin, "POST", "/api/import/points", bad, apply="1")
errs = {(e["line"], e["property"]) for e in r["errors"]}
assert s == 422 and {(3, "source"), (4, "tolerance"), (5, "settable"), (6, "extra"), (7, "name")} <= errs, errs
assert store.get("point", "ok1") is None and store.get("point", "bad1") is None
assert call(admin, "POST", "/api/import/points", "name,colour\na,b\n")[0] == 422  # unknown column
assert call(admin, "POST", "/api/import/points", "unit\nx\n")[0] == 422 and call(admin, "POST", "/api/import/points", {"a": 1})[0] == 400
print("ok")
