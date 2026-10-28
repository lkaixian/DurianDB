"""Automatic change-point detection and its route. Run: python tests/test_changepoint.py"""
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.changepoint import MAX_N, detect  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

# ---- two clean level shifts, found where they are and nowhere else
for s in range(5):
    r = random.Random(s)
    v = [(10 if i < 100 else 20 if i < 220 else 5) + r.gauss(0, 0.5) for i in range(300)]
    found = detect(v)
    assert [abs(c["index"] - t) <= 3 for c, t in zip(found, (100, 220))] == [True, True] and len(found) == 2, (s, found)
    assert found[0]["shift"] > 9 and found[1]["shift"] < -14 and abs(found[0]["before_mean"] - 10) < 0.5, found
    assert found[0]["after_mean"] == found[1]["before_mean"] and found[0]["strength"] > 1, found

# ---- pure noise finds nothing, whatever the seed
for s in range(20):
    r = random.Random(100 + s)
    assert detect([r.gauss(50, 2) for _ in range(400)]) == [], s

# ---- a ramp is a trend, not a staircase; a constant is nothing
assert detect([0.1 * i for i in range(300)]) == []
r = random.Random(7)
assert len(detect([0.05 * i + r.gauss(0, 0.5) for i in range(400)])) <= 1
assert detect([3.0] * 100) == []
assert detect([1.0, 2.0, 3.0]) == []  # thinner than 2 * min_seg
assert len(detect([0.0] * 50 + [1.0] * 50)) == 1  # noiseless step still found

# ---- at most ten, sorted by time, and the size cap is refused
many = [(i // 30 % 2) * 10.0 + random.Random(i).gauss(0, 0.1) for i in range(30 * 20)]
found = detect(many)
assert len(found) == 10 and [c["index"] for c in found] == sorted(c["index"] for c in found), len(found)
try:
    detect([0.0] * (MAX_N + 1))
    raise AssertionError("expected refusal")
except ValueError:
    pass

# ---- the HTTP endpoint
store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
store.add_user("viewer", hash_password("viewer-password-1"), ["user"])
ANON = {"name": None, "roles": {"anonymous"}}
VIEWER = {"name": "viewer", "roles": {"anonymous", "user"}}

now = time.time()
rr = random.Random(1)
store.add_history([("boiler-temp", now - (600 - i) * 60.0 - 30, (10.0 if i < 300 else 25.0) + rr.gauss(0, 0.3))
                   for i in range(600)])


def call(ident, path, query=None):
    try:
        return app.handle("GET", path, query or {}, None, ident)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


status, p = call(admin, "/api/points/boiler-temp/changepoints", {"hours": "10", "bucket": "60"})
assert status == 200 and p["hours"] == 10 and p["bucket"] == 60 and p["point"] == "boiler-temp", p
assert len(p["changes"]) == 1, p
c = p["changes"][0]
assert set(c) == {"ts", "before", "after", "shift", "strength"} and abs(c["shift"] - 15) < 1, c
assert abs(c["ts"] - (now - 300 * 60.0)) < 600, c

for q, prop in (({"hours": "0"}, "hours"), ({"hours": "721"}, "hours"), ({"bucket": "0"}, "bucket"),
                ({"hours": "720", "bucket": "60"}, "bucket")):  # 43200 steps
    status, p = call(admin, "/api/points/boiler-temp/changepoints", q)
    assert status == 422 and p["errors"][0]["property"] == prop, (q, status, p)

status, p = call(admin, "/api/points/line-pressure/changepoints")
assert status == 200 and p["changes"] == [] and p["note"], p
assert call(admin, "/api/points/ghost/changepoints")[0] == 404
assert call(ANON, "/api/points/boiler-temp/changepoints")[0] == 404
assert call(VIEWER, "/api/points/boiler-temp/changepoints")[0] == 200

print("ok")
