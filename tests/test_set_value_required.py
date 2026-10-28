"""A set request with no `value` is refused by name; it never writes the old "missing" sentinel (C16).
Run: python tests/test_set_value_required.py
"""
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
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None):
    try:
        return app.handle(method, path, {}, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


assert call("PUT", "/api/config/point/recipe", {"source": "demo", "datatype": "text", "settable": True, "set_roles": ["user"],
                                                "generator": {"kind": "constant", "value": "A"}})[0] == 200
assert call("PUT", "/api/config/point/num", {"source": "demo", "settable": True, "set_roles": ["user"]})[0] == 200
app.reload()
app.tick(time.time())

for point in ("recipe", "num"):
    for body in ({}, {"reason": "x"}):
        s, r = call("POST", f"/api/points/{point}/set", body)
        assert s == 400 and "value" in r["error"], (point, body, s, r)
app.tick(time.time() + 1)
assert call("GET", "/api/points/recipe")[1]["last"] == "A", "a refused request wrote nothing"

assert call("POST", "/api/points/recipe/set", {"value": "B"})[0] == 200
assert call("POST", "/api/points/recipe/set", {"value": None})[0] == 200, "null still releases the point"
print("ok")
