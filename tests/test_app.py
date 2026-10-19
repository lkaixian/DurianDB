"""End-to-end API behavior without HTTP. Run: python tests/test_app.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.store import Store  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
app = App(store)
ADMIN_PW = "admin-password-123"
assert app.auth.bootstrap(ADMIN_PW) is None
ANON = app.auth.identity(None)
admin = app.auth.identity(app.auth.login("admin", ADMIN_PW))


def call(ident, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, ident)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


def as_user(name):
    return app.auth.identity(app.auth.login(name, "viewer-password-1"))


# users: create, weak password refused, cannot delete self
assert call(admin, "PUT", "/api/users/viewer", {"password": "short", "roles": ["user"]})[0] == 422
assert call(admin, "PUT", "/api/users/viewer", {"password": "viewer-password-1", "roles": ["user"]})[0] == 200
assert call(admin, "PUT", "/api/users/ops", {"password": "viewer-password-1", "roles": ["ops"]})[0] == 200
assert call(admin, "DELETE", "/api/users/admin")[0] == 409
assert call(admin, "PUT", "/api/users/admin", {"roles": ["user"]})[0] == 409
viewer, ops = as_user("viewer"), as_user("ops")
assert call(viewer, "GET", "/api/users")[0] == 403 and call(ANON, "GET", "/api/users")[0] == 401

# anonymous sees nothing by default; a user sees the seeded points; me works
assert call(ANON, "GET", "/api/points") == (200, [])
s, pts = call(viewer, "GET", "/api/points")
assert s == 200 and {p["name"] for p in pts} == {"boiler-temp", "line-pressure", "pump-setpoint"}
assert call(viewer, "GET", "/api/me")[1]["roles"] == ["anonymous", "user"]

# point on a constant generator breaches its limit: event, then clears, shows in series
body = {"source": "demo", "unit": "C", "tags": {"area": "test"}, "generator": {"kind": "constant", "value": 95},
        "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"}], "read_roles": ["user"]}
assert call(admin, "PUT", "/api/config/point/hot", body)[0] == 200
app.tick(1000.0)
s, evs = call(viewer, "GET", "/api/events")
hot = [e for e in evs if e["point"] == "hot"]
assert len(hot) == 1 and hot[0]["level_name"] == "URGENT" and hot[0]["cleared_ts"] is None
assert call(ANON, "POST", f"/api/events/{hot[0]['id']}/ack")[0] == 401
assert hot[0]["state"] == "active_unacked"
assert call(viewer, "POST", f"/api/events/{hot[0]['id']}/ack")[0] == 200
assert call(viewer, "GET", "/api/events")[1][0]["state"] == "active_acked"
assert call(viewer, "POST", f"/api/events/{hot[0]['id']}/ack")[0] == 409  # already acknowledged
call(admin, "POST", "/api/points/hot/set", None)  # not settable
assert call(admin, "POST", "/api/points/hot/set", {"value": 1})[0] == 403  # hot is not settable: refused for everyone
body["settable"], body["set_roles"] = True, ["user"]
call(admin, "PUT", "/api/config/point/hot", body)
app.tick(1001.0)
assert call(viewer, "POST", "/api/points/hot/set", {"value": 50})[0] == 200
app.tick(1002.0)
assert next(p for p in call(viewer, "GET", "/api/points")[1] if p["name"] == "hot")["last"] == 50
assert call(viewer, "POST", "/api/points/hot/set", {"value": "x"})[0] == 400
assert call(ANON, "POST", "/api/points/hot/set", {"value": 5})[0] == 404  # anonymous cannot even see it
call(admin, "PUT", "/api/config/point/other", dict(body, tags={}))  # a config edit rebuilds the engine...
app.tick(1002.5)
assert next(p for p in call(viewer, "GET", "/api/points")[1] if p["name"] == "hot")["last"] == 50  # ...held value survives
call(admin, "DELETE", "/api/config/point/other")
assert call(viewer, "POST", "/api/points/hot/set", {"value": None})[0] == 200  # release
app.tick(1003.0)
assert any(a["action"] == "set" for a in call(admin, "GET", "/api/audit")[1])
rows = store.q("select cleared_ts from events where point='hot'")
assert rows[0]["cleared_ts"] is not None  # return to normal was stored when the value dropped

# reload closes open events (detector state is rebuilt)
body["generator"]["value"] = 99
call(admin, "PUT", "/api/config/point/hot", body)  # reload already happened here
app.tick(2000.0)
assert any(e["cleared_ts"] is None for e in store.q("select * from events where point='hot'"))
call(admin, "PUT", "/api/config/point/hot", body)
assert all(e["cleared_ts"] is not None for e in store.q("select * from events where point='hot'"))

# read roles hide points everywhere, including series
body2 = dict(body, read_roles=["ops"], tags={"area": "ops-only"})
call(admin, "PUT", "/api/config/point/secret", body2)
app.tick(3000.0)
assert "secret" not in {p["name"] for p in call(viewer, "GET", "/api/points")[1]}
assert "secret" in {p["name"] for p in call(ops, "GET", "/api/points")[1]}
assert call(viewer, "GET", "/api/series", point="secret")[0] == 404
assert call(ops, "GET", "/api/series", point="secret")[0] == 200

# tags and watch lists
assert {p["name"] for p in call(viewer, "GET", "/api/points", tag="area:line")[1]} == {"line-pressure", "pump-setpoint"}
assert call(admin, "PUT", "/api/config/watchlist/line", {"tags": {"area": "line"}})[0] == 200
assert {p["name"] for p in call(viewer, "GET", "/api/points", watchlist="line")[1]} == {"line-pressure", "pump-setpoint"}
assert call(viewer, "GET", "/api/points", watchlist="nope")[0] == 404

# validation reports every problem at once, with property names
s, r = call(admin, "PUT", "/api/config/point/bad name", {"source": "ghost", "logging": "X", "tolerance": -1,
            "detectors": [{"kind": "HIGH_LIMIT"}], "tags": {"a": 1}})
props = {e["property"] for e in r["errors"]}
assert s == 422 and {"name", "source", "logging", "tolerance", "detectors[0]", "tags"} <= props
assert call(admin, "PUT", "/api/config/source/x", {"type": "mqtt", "creds": "nope.json"})[0] == 422
assert call(admin, "PUT", "/api/config/handler/h", {"type": "webhook", "url": "file:///etc/passwd"})[0] == 422
assert call(admin, "PUT", "/api/config/publisher/p", {"type": "mqtt", "creds": "nope.json", "points": []})[0] == 422

# deleting a source that still has points is refused
assert call(admin, "DELETE", "/api/config/source/demo")[0] == 409

# series validation, and unknown routes
assert call(viewer, "GET", "/api/series", point="hot", rollup="sum")[0] == 400
assert call(viewer, "GET", "/api/series", point="hot", window="abc")[0] == 400
assert call(viewer, "GET", "/api/series", point="hot", window="3600", bucket="10")[0] == 200
assert call(admin, "GET", "/api/nothing")[0] == 404 and call(admin, "GET", "/etc/passwd")[0] == 404
assert call(viewer, "GET", "/api/nothing")[0] == 403  # unknown names fall in the admin-only area

# password change needs the old one
assert call(viewer, "POST", "/api/password", {"old": "wrong", "new": "another-password-1"})[0] == 403
assert call(viewer, "POST", "/api/password", {"old": "viewer-password-1", "new": "another-password-1"})[0] == 200
app.auth.login("viewer", "another-password-1")
print("ok")
