"""Baseline drift between two windows, and the drift route. Run: python tests/test_drift.py"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.forecast import DRIFT_MIN_SAMPLES, drift  # noqa: E402
from _support import new_store  # noqa: E402

# ---- steady noise: two windows drawn the same way leave the baseline where it was
noise = lambda base: [(i, base + (0.1 if i % 2 else -0.1)) for i in range(8)]
steady = drift(noise(10.0), noise(10.0))
assert steady["verdict"] == "steady" and steady["difference"] == 0 and steady["z"] == 0, steady
assert steady["now"]["samples"] == 8 and steady["before"]["samples"] == 8 and steady["note"] is None, steady
assert steady["now"]["median"] == 10.0 and steady["before"]["median"] == 10.0, steady

# ---- a clear step: the median moves by 3 against a spread of 0.148, far past the 3 sigma verdict
step = drift(noise(13.0), noise(10.0))
assert step["verdict"] == "drifting" and step["direction"] == "up", step
assert step["z"] > 3 and abs(step["difference"] - 3.0) < 1e-12, step
down = drift(noise(9.0), noise(10.0))
assert down["verdict"] == "drifting" and down["direction"] == "down" and down["difference"] == -1.0, down

# ---- too thin: a note and "unknown", never a confident answer
thin = drift(noise(10.0)[:DRIFT_MIN_SAMPLES - 1], noise(10.0))
assert thin["verdict"] == "unknown" and thin["z"] is None and "at least 5" in thin["note"], thin
assert thin["now"]["samples"] == 4 and thin["before"]["samples"] == 8, thin

# ---- constant windows have zero spread: no division by zero, and a flat step is still reported
flat = drift([(i, 5.0) for i in range(6)], [(i, 5.0) for i in range(6)])
assert flat["verdict"] == "steady" and flat["z"] == 0.0 and flat["scale"] == 0 and flat["note"] is None, flat
jump = drift([(i, 7.0) for i in range(6)], [(i, 5.0) for i in range(6)])
assert jump["verdict"] == "drifting" and jump["direction"] == "up" and jump["z"] is None, jump
assert jump["difference"] == 2.0 and "flat" in jump["note"], jump

# ---- a value that is not finite is a note, not an exception
bad = drift(noise(10.0), [(0, float("nan"))] * 6)
assert bad["verdict"] == "unknown" and "finite" in bad["note"], bad

# ---- the HTTP endpoint: same visibility as the point, validated parameters, honest thin history
store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
store.add_user("viewer", hash_password("viewer-password-1"), ["user"])
store.add_user("ops", hash_password("ops-password-1234"), ["ops"])
ANON = {"name": None, "roles": {"anonymous"}}
VIEWER = {"name": "viewer", "roles": {"anonymous", "user"}}
OPS = {"name": "ops", "roles": {"anonymous", "ops"}}

now = time.time()
# Two side-by-side days of one-minute samples: yesterday at 10, today at 20, with the same small noise.
store.add_history([("boiler-temp", now - (2880 - i) * 60.0 - 30,
                    (10.0 if i < 1440 else 20.0) + (0.1 if i % 2 else -0.1)) for i in range(2880)])


def call(ident, method, path, query=None):
    try:
        return app.handle(method, path, query or {}, None, ident)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


status, payload = call(admin, "GET", "/api/points/boiler-temp/drift")
assert status == 200 and payload["hours"] == 24 and payload["compare"] == 24, payload
assert payload["verdict"] == "drifting" and payload["direction"] == "up", payload
assert payload["now"]["samples"] > 5 and payload["before"]["samples"] > 5, payload
assert abs(payload["difference"] - 10.0) < 0.1 and payload["z"] > 3, payload

for query, prop in (({"hours": "soon"}, "hours"), ({"hours": "0"}, "hours"),
                    ({"compare": "soon"}, "compare"), ({"hours": "48", "compare": "24"}, "compare")):
    status, payload = call(admin, "GET", "/api/points/boiler-temp/drift", query)
    assert status == 422 and payload["errors"][0]["property"] == prop, (query, status, payload)

status, payload = call(admin, "GET", "/api/points/line-pressure/drift")
assert status == 200 and payload["verdict"] == "unknown" and "at least 5" in payload["note"], payload
assert payload["now"]["samples"] == 0 and payload["before"]["samples"] == 0, payload
assert call(admin, "GET", "/api/points/ghost/drift")[0] == 404

# ---- the drift line follows the point's read roles exactly: anonymous and a foreign role see 404
assert call(ANON, "GET", "/api/points/boiler-temp/drift")[0] == 404
assert call(VIEWER, "GET", "/api/points/boiler-temp/drift")[0] == 200
assert call(OPS, "GET", "/api/points/boiler-temp/drift")[0] == 404

print("ok")
