"""Tag fields and safe control of setpoints: limits, step size, confirmation. Run: python tests/test_safe_control.py"""
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
call = lambda ident, m, p, b=None: (lambda r: r)(_c(ident, m, p, b))


def _c(ident, method, path, body):
    try:
        return app.handle(method, path, {}, body, ident)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


SP = {"source": "demo", "unit": "bar", "settable": True, "generator": {"kind": "constant", "value": 5}, "description": "Header pressure setpoint",
      "eu_low": 0, "eu_high": 10, "decimals": 1, "set_max": 8, "max_step": 2, "confirm": True}
assert call(admin, "PUT", "/api/config/point/sp", SP)[0] == 200
app.tick(time.time())

# ---- fields are validated
for bad in ({"eu_low": 5, "eu_high": 5}, {"set_min": 9, "set_max": 8}, {"decimals": 9}, {"decimals": True}, {"max_step": 0}, {"confirm": "yes"},
            {"description": "x" * 201}, {"eu_low": "0"}):
    assert call(admin, "PUT", "/api/config/point/sp2", {**SP, **bad})[0] == 422, bad
assert call(admin, "PUT", "/api/config/point/ro", {"source": "demo", "set_max": 5})[0] == 422  # limits on a point that cannot be set

# ---- the point view carries the fields
v = next(p for p in call(admin, "GET", "/api/points")[1] if p["name"] == "sp")
assert v["description"] == "Header pressure setpoint" and v["decimals"] == 1 and v["eu"] == {"low": 0, "high": 10}
assert v["set_limits"] == {"min": 0, "max": 8, "max_step": 2, "confirm": True}  # min falls back to the engineering range

# ---- limits and step size are enforced before anything else happens
assert call(admin, "POST", "/api/points/sp/set", {"value": 9})[0] == 422  # above set_max
assert call(admin, "POST", "/api/points/sp/set", {"value": -1})[0] == 422  # below the engineering range
s, r = call(admin, "POST", "/api/points/sp/set", {"value": 7.5})
assert s == 422 and "at most 2" in r["error"]  # 5 -> 7.5 is too big a jump
assert not any(a["action"] == "set" and a["detail"].startswith("sp") for a in call(admin, "GET", "/api/audit")[1])

# ---- confirmation: the first call only asks, the second must repeat the same value with the token, once
s, r = call(admin, "POST", "/api/points/sp/set", {"value": 6})
assert s == 202 and r["confirm"]
tok = r["confirm"]
assert call(admin, "POST", "/api/points/sp/set", {"value": 6, "confirm": "wrong"})[0] == 202  # a wrong token just asks again
assert call(admin, "POST", "/api/points/sp/set", {"value": 7, "confirm": tok})[0] == 202  # a token is for one value
s, r = call(admin, "POST", "/api/points/sp/set", {"value": 6})
assert call(admin, "POST", "/api/points/sp/set", {"value": 6, "confirm": r["confirm"]})[0] == 200
assert call(admin, "POST", "/api/points/sp/set", {"value": 6, "confirm": r["confirm"]})[0] == 202  # used up
app.tick(time.time())
assert next(p for p in call(admin, "GET", "/api/points")[1] if p["name"] == "sp")["last"] == 6
assert any(a["detail"] == "sp: 5.0 -> 6" or a["detail"] == "sp: 5 -> 6" for a in call(admin, "GET", "/api/audit")[1])

# an expired token is refused
s, r = call(admin, "POST", "/api/points/sp/set", {"value": 7})
app.confirms[r["confirm"]] = (app.confirms[r["confirm"]][0], time.time() - 1)
assert call(admin, "POST", "/api/points/sp/set", {"value": 7, "confirm": r["confirm"]})[0] == 202

# releasing needs neither limits nor confirmation
assert call(admin, "POST", "/api/points/sp/set", {"value": None})[0] == 200
print("ok")
