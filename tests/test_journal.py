"""Alarm journal: filters, paging, visibility, CSV. Run: python tests/test_journal.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
store.add_user("op", hash_password("viewer-password-1"), ["user"])
store.add_user("nobody", hash_password("viewer-password-1"), ["other"])
op = app.auth.identity(app.auth.login("op", "viewer-password-1"))
nobody = app.auth.identity(app.auth.login("nobody", "viewer-password-1"))


def get(ident, **q):
    try:
        return app.handle("GET", "/api/events/search", q, None, ident)
    except ApiError as e:
        return e.status, {"error": str(e)}


store.run("delete from events")
now = time.time()
# level 3 warning, 4 urgent; ts counts back from now
rows = [("boiler-temp", 4, "92 > 90", now - 100, None, None, None), ("boiler-temp", 4, "93 > 90", now - 5000, now - 4000, None, None),
        ("line-pressure", 3, "7.6 > 7.5", now - 9000, now - 8000, now - 7000, "op"), ("pump-setpoint", 3, "stuck", now - 40 * 86400, now - 39 * 86400, now - 38 * 86400, "op"),
        ("(system)", 5, "history is not being saved", now - 50, None, None, None), ("hidden-point", 4, "x", now - 10, None, None, None)]
for p, lvl, msg, ts, cleared, acked, by in rows:
    store.run("insert into events(point,level,message,ts,cleared_ts,acked_ts,acked_by) values (?,?,?,?,?,?,?)", (p, lvl, msg, ts, cleared, acked, by))

s, r = get(op)
assert s == 200 and r["total"] == 4 and [e["point"] for e in r["items"]] == ["boiler-temp", "boiler-temp", "line-pressure", "pump-setpoint"], r  # newest first; system and unknown points are hidden
assert r["items"][0]["state"] == "active_unacked" and r["items"][2]["state"] == "closed" and r["items"][1]["state"] == "rtn_unacked"
assert get(admin)[1]["total"] == 5  # the superadmin also sees system alarms; "hidden-point" is not a configured point so nobody does
assert get(nobody)[1]["total"] == 0 and get(nobody)[1]["items"] == []

assert get(op, state="unacked")[1]["total"] == 2 and get(op, state="active")[1]["total"] == 1 and get(op, state="closed")[1]["total"] == 2
assert get(op, level="URGENT")[1]["total"] == 2 and get(op, level="warning")[1]["total"] == 4
assert get(op, hours="24")[1]["total"] == 3 and get(op, hours="1")[1]["total"] == 1
assert [e["point"] for e in get(op, q="PRESS")[1]["items"]] == ["line-pressure"] and get(op, q="> 90")[1]["total"] == 2 and get(op, q="100%")[1]["total"] == 0
p1 = get(op, limit="2")[1]
p2 = get(op, limit="2", offset="2")[1]
assert (p1["total"], len(p1["items"]), len(p2["items"])) == (4, 2, 2) and p1["items"][0]["id"] != p2["items"][0]["id"]
assert get(op, state="nope")[0] == 400 and get(op, level="LOUD")[0] == 400 and get(op, hours="x")[0] == 400
s, raw = get(op, format="csv", state="unacked")
lines = raw.data.decode().splitlines()
assert raw.filename == "alarms.csv" and lines[0].startswith("raised_utc,point,level") and len(lines) == 3 and "boiler-temp" in lines[1] and "active_unacked" in lines[1]
assert get(app.auth.identity(None))[1]["total"] == 0

# ---- acknowledge many at once: only what the caller can see, ids that cannot be seen are skipped, already acknowledged ones do not count
many = lambda ident, ids: app.handle("POST", "/api/events/ack-many", {}, {"ids": ids}, ident)
ids = {r["point"]: r["id"] for r in store.q("select id, point from events where acked_ts is null")}
assert set(ids) == {"boiler-temp", "(system)", "hidden-point"}, ids
mine = [r["id"] for r in store.q("select id from events where point='boiler-temp' and acked_ts is null")]
assert many(op, mine + [ids["(system)"], ids["hidden-point"], 999999])[1] == {"acked": len(mine)}
assert store.q("select acked_by from events where id=?", (mine[0],))[0]["acked_by"] == "op" and store.q("select acked_ts from events where id=?", (ids["(system)"],))[0]["acked_ts"] is None
assert many(op, mine)[1] == {"acked": 0} and many(admin, [ids["(system)"]])[1] == {"acked": 1}
for bad in (None, [], ["a"], [True], list(range(201))):
    try:
        many(op, bad)
        raise AssertionError(bad)
    except ApiError as e:
        assert e.status == 400
try:
    many(app.auth.identity(None), [1])
    raise AssertionError("anonymous")
except ApiError as e:
    assert e.status == 401
print("ok")
