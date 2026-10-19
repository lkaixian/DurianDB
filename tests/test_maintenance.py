"""Retention, backups, schedule, config export/import, history CSV, and a failing database. Run: python tests/test_maintenance.py"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App, SYSTEM  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.maintenance import Raw  # noqa: E402
from durian.store import Store  # noqa: E402

tmp = Path(tempfile.mkdtemp())
DAY = 86400


def make(name):
    st = Store(str(tmp / f"{name}.db"))
    seed(st)
    app = App(st, tmp / f"{name}-backups")
    app.auth.bootstrap("admin-password-123")
    st.add_user("viewer", hash_password("viewer-password-1"), ["user"])
    return st, app, app.auth.identity(app.auth.login("admin", "admin-password-123")), app.auth.identity(app.auth.login("viewer", "viewer-password-1"))


def call(app, who, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, who)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


st, app, admin, viewer = make("a")
c = lambda *a, **k: call(app, admin, *a, **k)
now = time.time()

# ---- retention: global setting, per-point override, 0 keeps forever, purge is batched
assert call(app, viewer, "GET", "/api/database")[0] == 403
assert c("GET", "/api/database")[1]["settings"]["retention_days"] == 365
assert c("PUT", "/api/database/settings", {"retention_days": 30})[0] == 200
for bad in ({"retention_days": -1}, {"retention_days": "x"}, {"backup_hour": 24}, {"nope": 1}, {"backup_keep": 0}):
    assert c("PUT", "/api/database/settings", bad)[0] == 422, bad
assert c("PUT", "/api/config/point/boiler-temp", {"source": "demo", "retention_days": 5, "generator": {"kind": "constant", "value": 1}})[0] == 200
assert c("PUT", "/api/config/point/line-pressure", {"source": "demo", "retention_days": 0, "generator": {"kind": "constant", "value": 1}})[0] == 200
assert "retention_days" in {e["property"] for e in c("PUT", "/api/config/point/x", {"source": "demo", "retention_days": -3})[1]["errors"]}
rows = [(p, now - d * DAY, 1.0) for p in ("boiler-temp", "line-pressure", "other") for d in (1, 10, 100)]
st.add_history(rows)
r = c("POST", "/api/database/purge")[1]
left = {(x["point"], round((now - x["ts"]) / DAY)) for x in st.q("select point, ts from history")}
assert r == {"deleted": 3, "complete": True}, r  # boiler-temp loses 10 and 100 days (5-day override), other loses 100 days (30-day default)
assert left == {("boiler-temp", 1), ("line-pressure", 1), ("line-pressure", 10), ("line-pressure", 100), ("other", 1), ("other", 10)}, left  # line-pressure: 0 keeps forever
assert c("POST", "/api/database/purge")[1]["deleted"] == 0 and st.get_setting("last_purge")["detail"] == 0

# ---- backups: create, list, prune to the keep count, download, refuse names that are not ours
assert c("PUT", "/api/database/settings", {"backup_keep": 2})[0] == 200
names = []
for i in range(4):
    names.append(app.maint.backup(now=now + i * 10)["name"])
listed = [b["name"] for b in c("GET", "/api/database")[1]["backups"]]
assert listed == sorted(names, reverse=True)[:2], (listed, names)
s, raw = c("GET", f"/api/database/backups/{listed[0]}")
assert s == 200 and isinstance(raw, Raw) and raw.data[:15] == b"SQLite format 3" and raw.filename == listed[0]
for evil in ("../../etc/passwd", "durian-20200101-000000.db/../x", "x.db", "durian-1.db", ".."):
    assert c("GET", f"/api/database/backups/{evil}")[0] == 404, evil
assert app.maint.backup_file("../a.db") is None and c("POST", "/api/database/backup")[1]["name"].startswith("durian-")
mem = App(Store())
mem.auth.bootstrap("admin-password-123")
assert call(mem, mem.auth.identity(mem.auth.login("admin", "admin-password-123")), "POST", "/api/database/backup")[0] == 501

# ---- schedule: each job runs once per day, at or after its hour; a failure is not retried every minute
import datetime as dt  # noqa: E402
st.set_setting("purge_hour", 3); st.set_setting("backup_hour", 4)
at = lambda h, d=0: dt.datetime(2030, 6, 1 + d, h, 5).timestamp()
assert app.maint.tick(at(2)) == [] and app.maint.tick(at(3)) == ["purge"] and app.maint.tick(at(3)) == []
assert app.maint.tick(at(5)) == ["backup"] and app.maint.tick(at(23)) == []
assert app.maint.tick(at(3, 1)) == ["purge"] and app.maint.tick(at(5, 1)) == ["backup"]
st.set_setting("backup_hour", -1)
assert app.maint.tick(at(5, 2)) == ["purge"]  # backups disabled, purge still runs

# ---- config export and import
doc = json.loads(c("GET", "/api/export")[1].data)
assert doc["format"] == "durian-config" and {o["name"] for o in doc["objects"]["point"]} >= {"boiler-temp", "pump-setpoint"} and doc["objects"]["dashboard"]
st2, app2, admin2, _ = make("b")
for kind in ("point", "source", "dashboard"):
    for o in st2.list(kind):
        st2.delete(kind, o["name"])
c2 = lambda *a, **k: call(app2, admin2, *a, **k)
s, r = c2("POST", "/api/import", doc)
assert s == 200 and r["applied"] is False and r["summary"]["point"]["created"] >= 3 and st2.list("point") == []  # a plain call changes nothing
s, r = c2("POST", "/api/import", doc, apply="1")
assert s == 200 and r["applied"] and st2.get("point", "pump-setpoint") == st.get("point", "pump-setpoint") and st2.get("dashboard", "plant-overview")["owner"] is None
assert c2("POST", "/api/import", doc, apply="1")[1]["summary"]["point"]["unchanged"] >= 3          # idempotent
assert {p["name"] for p in c2("GET", "/api/points")[1]} >= {"pump-setpoint"}                          # the engine was rebuilt
bad = json.loads(json.dumps(doc))
bad["objects"]["point"][0]["source"] = "ghost"; bad["objects"]["handler"] = [{"name": "h", "type": "webhook", "url": "file:///x"}]; bad["objects"]["nope"] = []
s, r = c2("POST", "/api/import", bad)
props = {e["property"] for e in r["errors"]}
assert s == 422 and any(p.endswith(".source") and p.startswith("point/") for p in props) and "handler/h.url" in props and "objects.nope" in props, props
assert c2("POST", "/api/import", {"format": "other"})[0] == 422 and c2("POST", "/api/import", doc, mode="wipe")[0] == 422
only = {"format": "durian-config", "version": 1, "objects": {"source": [{"name": "s9", "type": "virtual"}], "point": [{"name": "p9", "source": "s9"}]}}
assert c2("POST", "/api/import", only)[0] == 200                                                      # a point may use a source from the same file
before = {k: len(st2.list(k)) for k in ("source", "point", "dashboard")}
s, r = c2("POST", "/api/import", only, mode="replace")
assert r["summary"]["point"]["deleted"] == before["point"] and r["summary"]["source"]["deleted"] == before["source"] and len(st2.list("point")) == before["point"]  # dry run
c2("POST", "/api/import", only, mode="replace", apply="1")
assert [o["name"] for o in st2.list("point")] == ["p9"] and [o["name"] for o in st2.list("source")] == ["s9"] and st2.list("dashboard") == []

# ---- history CSV
st.add_history([("pump-setpoint", 1_700_000_000.0 + i, float(i)) for i in range(5)])
s, raw = c("GET", "/api/export/history", point="pump-setpoint", **{"from": "1700000000", "to": "1700000003"})
lines = raw.data.decode().splitlines()
assert s == 200 and raw.ctype.startswith("text/csv") and lines[0] == "timestamp_utc,epoch_seconds,value" and len(lines) == 5 and lines[1].startswith("2023-11-14T22:13:20.000Z,1700000000.000,0.0")
assert call(app, viewer, "GET", "/api/export/history", point="pump-setpoint")[0] == 200
assert c("GET", "/api/export/history", point="ghost")[0] == 404 and c("GET", "/api/export/history", point="pump-setpoint", **{"from": "x"})[0] == 400
csv_in = raw.data.decode() + "1700000100,7\n2023-11-15T00:00:00Z,8\nnot-a-time,9\n1700000200,nan\n1700000300\n946684799,1\n"
s, r = c("POST", "/api/import/history", csv_in, point="pump-setpoint")
assert s == 200 and r["imported"] == 6 and r["skipped"] == 4 and "line" in r["first_error"], r      # header line is not counted as an error
assert len(st.history_range("pump-setpoint", 1_700_000_050, 1_700_000_150, 10)) == 1
assert c("POST", "/api/import/history", csv_in, point="ghost")[0] == 400 and c("POST", "/api/import/history", {"a": 1}, point="pump-setpoint")[0] == 400
assert call(app, viewer, "POST", "/api/import/history", csv_in, point="pump-setpoint")[0] == 403

# ---- a failing database: samples wait, an alarm is raised for administrators, and everything is saved once it recovers
st3, app3, admin3, viewer3 = make("c")
c3 = lambda *a, **k: call(app3, admin3, *a, **k)
app3.tick(1000.0)
base = st3.history_stats()["rows"]
real = st3.add_history
def broken(rows): raise OSError("disk full")
st3.add_history = broken
for t in (1002.0, 1004.0):
    app3.tick(t)
assert len(app3.pending) > 0 and st3.history_stats()["rows"] == base
sysev = [e for e in c3("GET", "/api/events")[1] if e["point"] == SYSTEM]
assert len(sysev) == 1 and "not being saved" in sysev[0]["message"] and sysev[0]["cleared_ts"] is None
assert not [e for e in call(app3, viewer3, "GET", "/api/events")[1] if e["point"] == SYSTEM]       # operators do not see it
assert c3("GET", "/api/database")[1]["buffered_samples"] == len(app3.pending)
queued = len(app3.pending)
st3.add_history = real
app3.tick(1006.0)
assert app3.pending == [] and st3.history_stats()["rows"] >= base + queued
assert [e for e in c3("GET", "/api/events")[1] if e["point"] == SYSTEM][0]["cleared_ts"] is not None
# and the buffer is capped, counting what it had to drop
import durian.app as A  # noqa: E402
A.PENDING_MAX = 3
st3.add_history = broken
app3.tick(1008.0); app3.tick(1010.0)
assert len(app3.pending) == 3 and app3.lost > 0
print("ok")
