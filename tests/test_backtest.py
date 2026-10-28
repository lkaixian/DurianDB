"""Robust forecasting and the rolling-origin backtest. Run: python tests/test_backtest.py"""
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.backtest import linear, naive_last, robust, run, seasonal_naive, theil_sen  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

DAY = 86400

# ---- theil_sen: exact line recovered, and three wild points do not move it
line = [(i * 10.0, 3.0 * i * 10 + 7) for i in range(60)]
s, b = theil_sen(line)
assert abs(s - 3.0) < 1e-9 and abs(b - 7.0) < 1e-6, (s, b)
dirty = [(t, v + (900 if i in (50, 54, 58) else 0)) for i, (t, v) in enumerate(line)]
s, b = theil_sen(dirty)
assert abs(s - 3.0) < 1e-6, s
assert theil_sen([(float(i), 2.0 * i) for i in range(5000)])[0] == 2.0  # subsampled, still exact

# ---- every method has the (history, step_ts) -> [(ts, value, low, high)] shape
hist = [(i * 60.0, float(i)) for i in range(30)]
want = [30 * 60.0, 31 * 60.0]
for fn in (naive_last, linear, robust):
    rows = fn(hist, want)
    assert [r[0] for r in rows] == want and all(low <= v <= high for _t, v, low, high in rows), (fn.__name__, rows)
assert seasonal_naive(hist, [5 * DAY + 7200]) == [None]  # an hour of the week the history never saw: absent, not guessed

# ---- robust beats OLS on a ramp with three spikes in the training window (held-out clean tail)
rng = random.Random(7)
ramp = [(i * 60.0, 2.0 * i + rng.gauss(0, 1)) for i in range(100)]
ramp = [(t, v + (500 if i in (70, 75, 78) else 0)) for i, (t, v) in enumerate(ramp)]
out = run(ramp, horizon=20, folds=1)["methods"]
print("ramp+spikes MAE: robust %.2f linear %.2f naive %.2f" % tuple(out[m]["mae"] for m in ("robust", "linear", "naive_last")))
assert out["robust"]["mae"] < out["linear"]["mae"] / 5, out
assert out["robust"]["n"] == out["linear"]["n"] == 20

# ---- seasonal_naive wins on a daily pattern (14 days of 15-minute samples, 3 one-day folds)
def level(ts):
    hour = ts % DAY / 3600
    return 20 + 10 * math.cos((hour - 15) / 24 * 2 * math.pi) + ((int(ts) // 900) % 5 - 2) * 0.1


base = 4 * DAY + 100 * 7 * DAY  # a Monday 00:00 UTC
daily = [(base + i * 900, level(base + i * 900)) for i in range(14 * 96)]
m = run(daily, horizon=96, folds=3)["methods"]
print("daily MAE: seasonal %.3f linear %.3f naive %.3f" % tuple(m[k]["mae"] for k in ("seasonal_naive", "linear", "naive_last")))
assert m["seasonal_naive"]["mae"] < m["linear"]["mae"] and m["seasonal_naive"]["mae"] < m["naive_last"]["mae"], m
assert m["seasonal_naive"]["n"] == 3 * 96, m["seasonal_naive"]

# ---- coverage is a fraction; for Gaussian noise the 95% interval covers about 95%
rng = random.Random(11)
noise = [(i * 60.0, 50 + rng.gauss(0, 1)) for i in range(3000)]
g = run(noise, horizon=100, folds=10)["methods"]
for name, r in g.items():
    assert 0 <= r["coverage"] <= 1 and r["n"] > 0 and r["rmse"] >= r["mae"] >= 0, (name, r)
# n=1000 scored points: se = sqrt(.95*.05/1000) = 0.007, so 0.03 is about 4 se; the extrapolation error of a fit on
# 1000+ points is far smaller than that. (naive_last is deliberately wider: its step errors are sqrt(2) sigma.)
print("gaussian coverage: " + ", ".join("%s %.3f" % (k, v["coverage"]) for k, v in g.items()))
assert abs(g["linear"]["coverage"] - 0.95) < 0.03 and abs(g["robust"]["coverage"] - 0.95) < 0.03, g

# ---- mape only where |actual| > eps
z = run([(i * 60.0, 0.0 if i % 2 else 1.0 + i * 0.01) for i in range(60)], horizon=10, folds=2)["methods"]["naive_last"]
assert z["mape"] is not None
allzero = run([(i * 60.0, (i % 3) * 0.0) for i in range(60)], horizon=10, folds=2)["methods"]["naive_last"]
assert allzero["mape"] is None, allzero

# ---- thin data never raises
thin = run([(i * 60.0, float(i)) for i in range(5)], horizon=12, folds=5)
assert thin["methods"] == {} and thin["note"], thin
assert run([], 12, 5)["methods"] == {}

# ---- the HTTP routes
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
status, payload = app.handle("PUT", "/api/config/point/status-text", {}, {
    "source": "demo", "datatype": "text", "generator": {"kind": "constant", "value": "RUN"}}, admin)
assert status in (200, 201), payload

now = time.time()
store.add_history([("boiler-temp", t, level(t)) for t in (now - (80 * 12 - i) * 300 - 30 for i in range(80 * 12))])


def call(ident, path, query=None):
    try:
        return app.handle("GET", path, query or {}, None, ident)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


Q = {"hours": "72", "bucket": "300", "horizon": "12", "folds": "5"}
status, p = call(admin, "/api/points/boiler-temp/backtest", Q)
assert status == 200 and set(p) == {"point", "unit", "hours", "bucket", "horizon", "folds", "methods", "best", "note"}, p
assert p["horizon"] == 12 and p["folds"] == 5 and p["methods"] and p["best"] in p["methods"], p
assert set(p["methods"]["linear"]) == {"mae", "rmse", "mape", "coverage", "n"}, p
assert p["best"] == min((v["mae"], k) for k, v in p["methods"].items() if v["n"] >= 12)[1], p
print("route best:", p["best"], {k: round(v["mae"], 3) for k, v in p["methods"].items()})
assert call(admin, "/api/points/boiler-temp/backtest")[0] == 200  # defaults

status, p = call(admin, "/api/points/line-pressure/backtest", Q)  # thin: an answer with a note
assert status == 200 and p["methods"] == {} and p["best"] is None and p["note"], p

for key, bad in (("hours", "5"), ("hours", "721"), ("hours", "x"), ("bucket", "0"), ("bucket", "86401"), ("horizon", "0"),
                 ("horizon", "201"), ("folds", "0"), ("folds", "11")):
    status, p = call(admin, "/api/points/boiler-temp/backtest", {**Q, key: bad})
    assert status == 422 and p["errors"][0]["property"] == key, (key, bad, p)
assert call(admin, "/api/points/boiler-temp/backtest", {"hours": "720", "bucket": "60"})[0] == 422  # 43200 steps > 3000
assert call(admin, "/api/points/boiler-temp/backtest", {"hours": "720", "bucket": "900"})[0] == 200  # 2880 steps
assert call(admin, "/api/points/status-text/backtest")[0] == 400
assert call(admin, "/api/points/ghost/backtest")[0] == 404
assert [call(i, "/api/points/boiler-temp/backtest")[0] for i in (ANON, VIEWER, OPS)] == [404, 200, 404]

# ---- forecast: method and ahead; the default answer is unchanged
F = "/api/points/boiler-temp/forecast"
d = call(admin, F, {"hours": "6", "bucket": "300"})
assert d[0] == 200 and "method" not in d[1] and len(d[1]["forecast"]) == 72, d[1].get("note")
assert call(admin, F, {"hours": "6", "bucket": "300", "method": "linear"}) == d
r = call(admin, F, {"hours": "6", "bucket": "300", "method": "robust", "ahead": "5"})
assert r[0] == 200 and len(r[1]["forecast"]) == 5 and r[1]["method"] == "robust" and r[1]["points"] == d[1]["points"], r[1]
assert all(set(x) == {"ts", "value", "low", "high"} and x["low"] <= x["value"] <= x["high"] for x in r[1]["forecast"])
assert len(call(admin, F, {"hours": "6", "bucket": "300", "ahead": "3"})[1]["forecast"]) == 3
for key, bad in (("method", "arima"), ("ahead", "0"), ("ahead", "2001"), ("ahead", "x")):
    status, p = call(admin, F, {"hours": "6", "bucket": "300", key: bad})
    assert status == 422 and p["errors"][0]["property"] == key, (key, bad, p)
t = call(admin, "/api/points/line-pressure/forecast", {"method": "robust"})
assert t[0] == 200 and t[1]["forecast"] == [] and t[1]["note"], t

print("ok")
