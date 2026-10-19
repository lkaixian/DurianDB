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
app.outputs.on_event = lambda ev, kind: sent.append((ev.point, kind))
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
print("ok")
