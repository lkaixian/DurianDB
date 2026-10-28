"""Correlation between points, and the correlate / related routes. Run: python tests/test_correlate.py"""
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.correlate import best_lag, pearson, spearman  # noqa: E402
from _support import new_store  # noqa: E402

# ---- the maths: aligned lists, n >= 10, constant -> None
xs = [float(i) for i in range(20)]
assert abs(pearson(xs, [2 * x + 1 for x in xs]) - 1) < 1e-12
assert abs(pearson(xs, [-x for x in xs]) + 1) < 1e-12
assert abs(spearman(xs, [x ** 3 for x in xs]) - 1) < 1e-12  # monotone but not linear: rank r is 1
assert pearson(xs, [5.0] * 20) is None and spearman(xs, [5.0] * 20) is None
assert pearson(xs[:9], xs[:9]) is None  # too thin
rng = random.Random(7)
noise_a, noise_b = [rng.random() for _ in range(300)], [rng.random() for _ in range(300)]
assert abs(pearson(noise_a, noise_b)) < 0.2
wave = [math.sin(i / 3) + 0.01 * rng.random() for i in range(120)]
lag, r0, rbest = best_lag(wave[3:], wave[:-3], 6)  # ys[i+3] = xs[i]: y trails x by 3
assert lag == 3 and rbest > 0.99, (lag, r0, rbest)
assert best_lag(xs, [5.0] * 20, 6) == (None, None, None)

# ---- the routes
store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
store.add_user("viewer", hash_password("viewer-password-1"), ["user"])
VIEWER = {"name": "viewer", "roles": {"anonymous", "user"}}
store.put("point", "secret-num", {"source": "demo", "unit": "x", "read_roles": ["ops"],
                                  "generator": {"kind": "sine", "offset": 1, "amplitude": 1, "period": 60}})
store.put("point", "flat", {"source": "demo", "unit": "x", "generator": {"kind": "sine", "offset": 1, "amplitude": 1, "period": 60}})
store.put("point", "txt", {"source": "demo", "datatype": "text"})
app.reload()

base = int(time.time() // 60) * 60 - 200 * 60
base_wave = [math.sin(i / 7) + 0.3 * math.sin(i / 2.3) for i in range(200)]
store.add_history(
    [("boiler-temp", base + i * 60 + 5, 50 + 10 * v) for i, v in enumerate(base_wave)]
    + [("line-pressure", base + i * 60 + 5, 9 - 2 * v) for i, v in enumerate(base_wave)]  # anti-correlated
    + [("secret-num", base + i * 60 + 5, 3 * v) for i, v in enumerate(base_wave)]  # perfectly correlated, hidden
    + [("flat", base + i * 60 + 5, 4.0) for i in range(200)]
    + [("pump-setpoint", base + i * 60 + 5, base_wave[max(i - 3, 0)]) for i in range(200)])  # trails by 3 buckets


def call(ident, path, query=None, method="GET"):
    try:
        return app.handle(method, path, query or {}, None, ident)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


Q = {"hours": "24", "bucket": "60"}
status, p = call(admin, "/api/points/boiler-temp/correlate", {**Q, "with": "line-pressure"})
assert status == 200 and p["point"] == "boiler-temp" and p["with"] == "line-pressure" and p["n"] >= 150, p
assert p["pearson"] < -0.999 and p["strength"] == "strong", p
assert p["spearman"] < -0.99 and p["best_lag"] == 0, p
status, p = call(admin, "/api/points/boiler-temp/correlate", {**Q, "with": "secret-num"})
assert status == 200 and p["pearson"] > 0.999, p
status, p = call(admin, "/api/points/boiler-temp/correlate", {**Q, "with": "pump-setpoint", "lag": "6"})
assert status == 200 and p["best_lag"] == 3 and abs(p["r_at_best_lag"]) > abs(p["pearson"]), p
status, p = call(admin, "/api/points/boiler-temp/correlate", {**Q, "with": "flat"})
assert status == 200 and p["pearson"] is None and p["strength"] == "none" and "constant" in p["note"], p
status, p = call(admin, "/api/points/boiler-temp/correlate", {**Q, "with": "line-pressure", "hours": "1"})
assert status == 200 and p["n"] >= 10 and p["note"] is None, p
status, p = call(admin, "/api/points/boiler-temp/correlate", {"hours": "1", "bucket": "3600", "with": "line-pressure"})
assert status == 200 and p["pearson"] is None and "at least 10" in p["note"], p

# related: ordered by |r|, hidden point excluded for a user, never named
status, p = call(admin, "/api/points/boiler-temp/related", {"hours": "24", "bucket": "60", "top": "5"})
names = [e["point"] for e in p["related"]] if status == 200 else p
assert status == 200 and "boiler-temp" not in names and "flat" not in names and "txt" not in names, p
assert set(names[:2]) == {"line-pressure", "secret-num"} and p["related"][0]["pearson"] is not None, p
assert all(abs(a["pearson"]) >= abs(b["pearson"]) for a, b in zip(p["related"], p["related"][1:])), p
assert len(call(admin, "/api/points/boiler-temp/related", {"top": "1"})[1]["related"]) == 1
status, p = call(VIEWER, "/api/points/boiler-temp/related", {"hours": "24", "bucket": "60"})
assert status == 200 and "secret-num" not in str(p) and "line-pressure" in str(p), p

# hidden / unknown / invalid
assert call(VIEWER, "/api/points/boiler-temp/correlate", {**Q, "with": "secret-num"})[0] == 404
assert call(VIEWER, "/api/points/secret-num/correlate", {**Q, "with": "boiler-temp"})[0] == 404
assert call(VIEWER, "/api/points/secret-num/related")[0] == 404
assert call(admin, "/api/points/boiler-temp/correlate", {**Q, "with": "ghost"})[0] == 404
assert call(admin, "/api/points/ghost/correlate", {**Q, "with": "boiler-temp"})[0] == 404
for q, prop in (({}, "with"), ({"with": "flat", "hours": "0"}, "hours"), ({"with": "flat", "hours": "169"}, "hours"),
                ({"with": "flat", "bucket": "0"}, "bucket"), ({"with": "flat", "lag": "13"}, "lag"),
                ({"with": "flat", "hours": "168", "bucket": "60"}, "bucket")):
    status, p = call(admin, "/api/points/boiler-temp/correlate", q)
    assert status == 422 and p["errors"][0]["property"] == prop, (q, status, p)
assert call(admin, "/api/points/boiler-temp/related", {"top": "0"})[0] == 422
assert call(admin, "/api/points/boiler-temp/related", {"hours": "168", "bucket": "60"})[0] == 422
assert call(admin, "/api/points/txt/correlate", {**Q, "with": "boiler-temp"})[0] == 400
assert call(admin, "/api/points/boiler-temp/correlate", {**Q, "with": "txt"})[0] == 400
assert call(admin, "/api/points/boiler-temp/correlate", {**Q, "with": "flat"}, method="POST")[0] in (404, 405)

print("ok")
