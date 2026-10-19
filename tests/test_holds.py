"""Shelving, suppression by design and out of service. Run: python tests/test_holds.py"""
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


def call(ident, method, path, body=None):
    try:
        return app.handle(method, path, {}, body, ident)
    except ApiError as e:
        return e.status, {"error": str(e)}


call(admin, "PUT", "/api/users/op", {"password": "viewer-password-1", "roles": ["user"]})
op = app.auth.identity(app.auth.login("op", "viewer-password-1"))
HOT = {"source": "demo", "generator": {"kind": "constant", "value": 95}, "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"}]}
for n, b in (("hot", HOT), ("hot2", HOT), ("mode", {"source": "demo", "generator": {"kind": "constant", "value": 1}}),
             ("sup", {**HOT, "suppress_when": {"point": "mode", "op": ">", "value": 0.5}})):
    assert call(admin, "PUT", f"/api/config/point/{n}", b)[0] == 200, n
assert call(admin, "PUT", "/api/config/point/bad", {**HOT, "suppress_when": {"point": "bad", "op": ">", "value": 1}})[0] == 422
sent = []
app.outputs.on_event = lambda ev, kind: sent.append((ev.point, kind)) if ev.point in ("hot", "hot2", "sup") else None  # the seeded random walks may alarm too
state = lambda n: [(e["state"], e["held"]) for e in call(op, "GET", "/api/events")[1] if e["point"] == n]
now = time.time()

# an alarm on a point nobody has shelved is announced; one on a suppressed point is recorded and held
app.tick(now)
assert ("hot", "raised") in sent and ("sup", "raised") not in sent
assert state("sup") == [("active_unacked", "suppressed")] and state("hot") == [("active_unacked", None)]

# shelving: validated, audited, holds an active alarm, releases it (and announces it) when unshelved
assert call(ANON, "POST", "/api/alarms/hot/shelve", {"minutes": 5, "reason": "sensor drift"})[0] == 401
assert call(op, "POST", "/api/alarms/hot/shelve", {"minutes": 5})[0] == 422
assert call(op, "POST", "/api/alarms/hot/shelve", {"minutes": 0, "reason": "sensor drift"})[0] == 422
assert call(op, "POST", "/api/alarms/ghost/shelve", {"minutes": 5, "reason": "sensor drift"})[0] == 404
assert call(op, "POST", "/api/alarms/hot/shelve", {"minutes": 5, "reason": "sensor drift"})[0] == 200
assert state("hot") == [("active_unacked", "shelved")] and call(op, "GET", "/api/alarms")[1][0]["user"] == "op"
sent.clear()
assert call(op, "POST", "/api/alarms/hot/unshelve")[0] == 200 and sent == [("hot", "raised")] and state("hot")[0][1] is None
assert call(op, "POST", "/api/alarms/hot/unshelve")[0] == 409

# a shelve placed before the alarm: recorded, not announced; the timer releases it
assert call(op, "POST", "/api/alarms/hot2/shelve", {"minutes": 5, "reason": "maintenance"})[0] == 200
app.tick(now + 1)
assert state("hot2") == [("active_unacked", "shelved")] and ("hot2", "raised") not in sent
sent.clear()
app.tick(now + 330)
assert state("hot2") == [("active_unacked", None)] and sent == [("hot2", "raised")] and call(op, "GET", "/api/alarms")[1] == []

# suppression ends when its condition does
call(admin, "PUT", "/api/config/point/mode", {"source": "demo", "generator": {"kind": "constant", "value": 0}})
app.tick(now + 400)
assert state("sup")[0][1] is None

# out of service: superadmin only, alarms close and are not evaluated, back in service they return
assert call(op, "POST", "/api/alarms/hot/out-of-service", {"reason": "being replaced"})[0] == 403
assert call(admin, "POST", "/api/alarms/hot/out-of-service", {"reason": "being replaced"})[0] == 200
app.tick(now + 401)
app.tick(now + 402)
assert state("hot") and not any(s[0].startswith("active") for s in state("hot"))
assert call(op, "POST", "/api/alarms/hot/shelve", {"minutes": 5, "reason": "sensor drift"})[0] == 409
app.reload()  # out of service survives a restart of the engine
app.tick(now + 403)
assert not any(s[0].startswith("active") for s in state("hot"))
assert call(admin, "POST", "/api/alarms/hot/in-service")[0] == 200
app.tick(now + 404)
assert any(s[0] == "active_unacked" for s in state("hot"))
assert {"alarm.shelve", "alarm.out-of-service", "alarm.in-service", "shelve.expired"} <= {r["action"] for r in store.audit_log(50)}

# ---- KPIs, computed from stored alarms, only for points the caller can see
st = app.store
st.run("delete from events")
st.run("delete from objects where kind='alarmhold'")
app.holds = {}
T = 1_000_000_200.0  # on a 10 minute boundary
for point, dt_, ack in (("hot", 0, 30), ("hot", 100, None), ("hot", 200, 10), ("hot2", 5, None)):  # three inside one 10 minute bucket
    st.run("insert into events(point,level,message,ts,acked_ts) values (?,?,?,?,?)", (point, 4, "x", T + dt_, None if ack is None else T + dt_ + ack))
st.run("insert into events(point,level,message,ts,cleared_ts) values ('hot2',4,'old',?,?)", (T - 100 * 3600, T - 99 * 3600))  # outside the window
st.run("insert into events(point,level,message,ts) values ('hot2',4,'stuck',?)", (T - 50 * 3600,))  # active for 50 h, outside the window
k = app.alarm_kpis(app.visible_points(op), False, 24, now=T + 3600)
assert (k["alarms"], k["peak_per_10min"], k["flood_10min_periods"]) == (4, 4, 0) and k["per_hour"] == 0.17, k
assert k["top_points"][0] == {"point": "hot", "alarms": 3} and k["chattering_points"] == [{"point": "hot", "periods": 1}]
assert k["median_seconds_to_acknowledge"] == 30 and k["unacknowledged_now"] == 3
assert k["active_now"] == 5 and k["stale_now"] == 1, k
assert app.alarm_kpis({}, False, 24, now=T)["alarms"] == 0  # a caller who sees no points sees no alarms
assert call(op, "GET", "/api/alarms/kpis")[1]["hours"] == 24 and call(ANON, "GET", "/api/alarms/kpis")[1]["alarms"] == 0
print("ok")
