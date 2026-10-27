"""EWMA baseline, robust anomaly score and short-horizon forecast. Run: python tests/test_forecast.py"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.core import Point, make_detector  # noqa: E402
from durian.forecast import baseline, ewma, forecast, score  # noqa: E402
from _support import new_store  # noqa: E402

# ---- ewma: a constant series converges, a step is followed within a few samples
mean, sigma = ewma([7.0] * 20, 0.3)
assert abs(mean - 7.0) < 1e-12 and sigma < 1e-12, (mean, sigma)
mean, _ = ewma([0.0] * 10 + [10.0] * 4, 0.5)
assert abs(mean - 10.0) <= 1.0, mean  # 10 - 10 * 0.5^4 = 9.375
mean, _ = ewma([0.0] * 10 + [10.0] * 20, 0.5)
assert abs(mean - 10.0) < 1e-4, mean

# ---- baseline: keys, the last update, and what it refuses
b = baseline([(i, 3.0) for i in range(5)], 0.4)
assert b == {"mean": 3.0, "sigma": 0.0, "count": 5, "updated": 4}, b
for bad in ([(0, 1.0)], [(0, 1.0), (1, float("nan"))], [(0, 1.0), (1, float("inf"))]):
    try:
        baseline(bad, 0.5)
    except ValueError as e:
        assert "sample" in str(e) or "finite" in str(e), e
    else:
        raise AssertionError(f"{bad} should be refused")
for alpha in (0, -0.1, 1.1, float("nan")):
    try:
        baseline([(0, 1.0), (1, 2.0)], alpha)
    except ValueError:
        pass
    else:
        raise AssertionError(f"alpha {alpha} should be refused")

# ---- a straight ramp with a little deterministic noise: extrapolates tightly, interval widens with distance
ramp = [(i * 60.0, 10.0 + 0.5 * i + (0.1 if i % 2 else -0.1)) for i in range(30)]
r = forecast(ramp, 5)
assert r["note"] is None and len(r["items"]) == 5, r
truth = [10.0 + 0.5 * i for i in range(30, 35)]
for row, want in zip(r["items"], truth):
    assert abs(row["value"] - want) < 0.2, (row, want)
    assert row["low"] < row["value"] < row["high"], row
assert [row["ts"] for row in r["items"]] == [1740.0 + 60.0 * i for i in range(1, 6)]
widths = [row["high"] - row["low"] for row in r["items"]]
assert widths == sorted(widths) and widths[-1] > widths[0], widths

# ---- too short, flat, or every sample at one timestamp: a note, never a made-up number or a divide-by-zero
short = forecast([(i, float(i)) for i in range(3)], 4)
assert short["items"] == [] and "at least 6" in short["note"], short
flat = forecast([(i, 5.0) for i in range(20)], 4)
assert flat["items"] == [] and "flat" in flat["note"], flat
same_ts = forecast([(0, float(i)) for i in range(10)], 3)
assert same_ts["items"] == [] and "timestamp" in same_ts["note"], same_ts

# ---- the robust score agrees with ANOMALY on the verdicts: spike raises, flat window has no scale
p = Point("temp")
d = make_detector({"kind": "ANOMALY", "z_limit": 3, "window": 10})
prior = [10, 11] * 5  # 10 samples, so the median is 10.5 and the MAD is 0.5
for i, v in enumerate(prior):
    d.check(p, v, i)
ev = d.check(p, 13, 10)
sc = score([(i, v) for i, v in enumerate(prior)], 13)
assert ev is not None and sc is not None and sc["z"] > 3 and sc["samples"] == 10, (ev, sc)

d = make_detector({"kind": "ANOMALY", "z_limit": 3})
assert all(d.check(p, 5, i) is None for i in range(4)) and d.check(p, 500, 4) is None
assert score([(i, 5) for i in range(4)], 500) is None, "a flat window has no scale"
assert score([(i, float(i)) for i in range(2)], 9) is None, "fewer than three samples has no spread"

# ---- the HTTP endpoints: same visibility, validated parameters, stored points plus forecast rows
store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
now = time.time()
store.add_history([("boiler-temp", now - (39 - i) * 60.0, 10.0 + 0.5 * i) for i in range(40)])


def call(method, path, query=None):
    try:
        return app.handle(method, path, query or {}, None, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


status, payload = call("GET", "/api/points/boiler-temp/forecast", {"hours": "soon"})
assert status == 422 and payload["errors"][0]["property"] == "hours", payload
status, payload = call("GET", "/api/points/boiler-temp/forecast", {"hours": "0"})
assert status == 422 and payload["errors"][0]["property"] == "hours", payload
status, payload = call("GET", "/api/points/boiler-temp/forecast", {"bucket": "0"})
assert status == 422 and payload["errors"][0]["property"] == "bucket", payload
status, payload = call("GET", "/api/points/ghost/forecast")
assert status == 404 and payload["error"] == "unknown point", payload

status, payload = call("GET", "/api/points/boiler-temp/forecast", {"hours": "1", "bucket": "60"})
assert status == 200 and payload["note"] is None, payload
assert len(payload["points"]) == 40, len(payload["points"])  # the truth a console can draw without a second call
assert len(payload["forecast"]) == 60, len(payload["forecast"])  # one hour at one-minute steps
assert abs(payload["forecast"][0]["value"] - 30.0) < 0.2, payload["forecast"][0]

status, payload = call("GET", "/api/points/line-pressure/forecast")
assert status == 200 and payload["points"] == [] and payload["forecast"] == [] and payload["note"], payload

status, payload = call("GET", "/api/points/boiler-temp/baseline", {"window": "20"})
assert status == 200 and payload["baseline"]["count"] == 20 and payload["baseline"]["updated"] == now, payload
status, payload = call("GET", "/api/points/line-pressure/baseline")
assert status == 200 and payload["baseline"] is None and payload["note"], payload

print("ok")
