"""Dashboard validation and permissions. Run: python tests/test_dashboards.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.dashboards import VALIDATORS  # noqa: E402
from durian.store import Store  # noqa: E402

store = Store()
seed(store)
app = App(store)
app.auth.bootstrap("admin-password-123")
for n, roles in (("viewer", ["user"]), ("designer", ["user", "designer"]), ("other", ["user", "designer"]), ("ops", ["ops"])):
    store.add_user(n, hash_password(f"{n}-password-123"), roles)
ident = lambda n, pw: app.auth.identity(app.auth.login(n, pw))
admin, viewer, designer, other, ops = (ident("admin", "admin-password-123"), ident("viewer", "viewer-password-123"),
                                       ident("designer", "designer-password-123"), ident("other", "other-password-123"), ident("ops", "ops-password-123"))
ANON = app.auth.identity(None)


def call(who, method, path, body=None):
    try:
        return app.handle(method, path, {}, body, who)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


props = lambda r: {e["property"] for e in r[1]["errors"]}
W = lambda **k: {"id": "a", "type": "value", "point": "boiler-temp", "x": 0, "y": 0, "w": 3, "h": 2, **k}
D = lambda *ws, **k: {"title": "T", "widgets": list(ws), **k}

# every widget type has a validator, and the seeded dashboard is valid
assert set(VALIDATORS) == {"value", "gauge", "chart", "alarms", "table", "setpoint", "text", "status"}
s, seeded = call(viewer, "GET", "/api/dashboards/plant-overview")
assert s == 200 and len(seeded["widgets"]) == 8 and seeded["editable"] is False
assert call(admin, "PUT", "/api/dashboards/copy", {k: v for k, v in seeded.items() if k not in ("name", "editable")})[0] == 200

# ---- validation reports every problem with a path, including overlap and grid fit
r = call(admin, "PUT", "/api/dashboards/bad", {"title": "", "columns": 99, "refresh": 0, "read_roles": "x", "widgets": [
    W(id="a"), W(id="a", x=1), W(id="b", type="nope"), W(id="c", x=11, w=3), W(id="d", w=0), W(id="e", type="gauge", min=5, max=1, x=6),
    W(id="f", type="chart", points=[], window=7, x=0, y=5), W(id="g", type="table", tag="x", points=["p"], y=8), W(id="h", type="text", text="", y=10),
    W(id="i", type="value", rules=[{"op": "~", "value": 1, "tone": "red"}], y=12), W(id="j", type="alarms", limit=0, y=14)]})
assert r[0] == 422 and {"title", "columns", "refresh", "read_roles", "widgets[1].id", "widgets[1].x", "widgets[2].type", "widgets[3].w", "widgets[4].x",
                        "widgets[5].min", "widgets[6].points", "widgets[6].window", "widgets[7].tag", "widgets[8].text", "widgets[9].rules[0]", "widgets[10].limit"} <= props(r), props(r)
assert "overlaps widget a" in str(r[1]["errors"])
assert call(admin, "PUT", "/api/dashboards/ok", D(W(), W(id="b", x=3), W(id="c", type="gauge", min=0, max=1, zones=[{"from": 0.5, "to": 1, "tone": "alarm"}], x=6)))[0] == 200
assert call(admin, "PUT", "/api/dashboards/bad name", D(W()))[0] == 422

# ---- warnings: unknown points, and public dashboards over non-public points
s, r = call(admin, "PUT", "/api/dashboards/warn", D(W(point="ghost"), read_roles=["user"]))
assert s == 200 and any("ghost" in w["message"] for w in r["warnings"])
s, r = call(admin, "PUT", "/api/dashboards/pub", D(W(), read_roles=["anonymous"]))
assert s == 200 and any("not readable by anonymous" in w["message"] for w in r["warnings"])

# ---- visibility: public dashboards are visible to anonymous visitors, but their points are not
assert [d["name"] for d in call(ANON, "GET", "/api/dashboards")[1]] == ["pub"]
assert call(ANON, "GET", "/api/dashboards/pub")[0] == 200 and call(ANON, "GET", "/api/dashboards/ok")[0] == 404
assert call(ANON, "GET", "/api/points") == (200, [])  # dashboard visibility does not widen data access
assert call(ops, "GET", "/api/dashboards")[1][0]["name"] == "pub" and len(call(viewer, "GET", "/api/dashboards")[1]) == 5

# ---- who may create, edit, delete, publish
assert call(viewer, "PUT", "/api/dashboards/mine", D(W()))[0] == 403 and call(ANON, "PUT", "/api/dashboards/mine", D(W()))[0] == 401
assert call(designer, "PUT", "/api/dashboards/mine", D(W()))[0] == 200
assert call(designer, "GET", "/api/dashboards/mine")[1]["owner"] == "designer" and call(designer, "GET", "/api/dashboards/mine")[1]["editable"] is True
assert call(other, "PUT", "/api/dashboards/mine", D(W(), read_roles=["user"]))[0] == 403          # can read (default user role) but not edit
assert call(other, "DELETE", "/api/dashboards/mine")[0] == 403
assert call(designer, "PUT", "/api/dashboards/mine", D(W(), edit_roles=["designer"], owner="somebody-else"))[0] == 200  # owner field is ignored
assert call(designer, "GET", "/api/dashboards/mine")[1]["owner"] == "designer"
assert call(other, "PUT", "/api/dashboards/mine", D(W(title="x"), read_roles=["user"], edit_roles=["designer"]))[0] == 200  # edit role grants edit
r = call(designer, "PUT", "/api/dashboards/mine2", D(W(), read_roles=["anonymous"]))
assert r[0] == 422 and "read_roles" in props(r)                                                   # only a superadmin publishes publicly
assert call(designer, "PUT", "/api/dashboards/pub", D(W()))[0] == 404 or call(designer, "PUT", "/api/dashboards/pub", D(W()))[0] == 403
hidden = call(admin, "PUT", "/api/dashboards/secret", D(W(), read_roles=["ops"]))
assert hidden[0] == 200 and call(viewer, "GET", "/api/dashboards/secret")[0] == 404 and call(viewer, "PUT", "/api/dashboards/secret", D(W()))[0] == 404
assert call(designer, "PUT", "/api/dashboards/secret", D(W()))[0] == 404                           # a hidden name is not revealed by a refusal
assert call(ops, "GET", "/api/dashboards/secret")[0] == 200 and call(ops, "PUT", "/api/dashboards/secret", D(W()))[0] == 403
assert call(designer, "DELETE", "/api/dashboards/mine")[0] == 200 and call(designer, "DELETE", "/api/dashboards/mine")[0] == 404
assert call(admin, "DELETE", "/api/dashboards/secret")[0] == 200
assert [a["action"] for a in call(admin, "GET", "/api/audit")[1]][:2] == ["dashboard.delete", "dashboard.delete"]
print("ok")
