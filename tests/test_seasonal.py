"""Hour-of-week seasonal baseline, anomaly score and routes. Run: python tests/test_seasonal.py"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.forecast import score  # noqa: E402
from durian.seasonal import profile, score_against, slot_of  # noqa: E402
from _support import new_store  # noqa: E402

DAY = 86400
# 1970-01-01 was a Thursday (dow 3); 1970-01-05 a Monday 00:00 UTC
assert slot_of(0) == (3, 0) and slot_of(4 * DAY) == (0, 0) and slot_of(4 * DAY + 3600 * 5 + 1) == (0, 5)
assert slot_of(4 * DAY, 3600) == (0, 1) and slot_of(4 * DAY, -3600) == (6, 23)


def level(ts, tz=0):  # daily pattern: 30 at 15:00, 10 at 03:00, plus tiny deterministic noise
    hour = (ts + tz) % DAY / 3600
    return 20 + 10 * math.cos((hour - 15) / 24 * 2 * math.pi) + ((int(ts) // 900) % 5 - 2) * 0.1


# ---- the library: 14 days of 15-minute samples
base = 4 * DAY + 100 * 7 * DAY  # a Monday 00:00 UTC
pairs = [(base + i * 900, level(base + i * 900)) for i in range(14 * 96)]
prof = profile(pairs)
assert len(prof) == 168 and all(s["n"] == 8 for s in prof.values()), len(prof)

at3am, at3pm = base + 3 * 3600 + 450, base + 15 * 3600 + 450
three_pm_value = level(at3pm)
assert score_against(prof, at3pm, three_pm_value)["z"] < 3  # normal at 3pm
hit = score_against(prof, at3am, three_pm_value)  # the same reading at 3am is far from 3am's own slot
assert hit["z"] > 10 and abs(hit["expected"] - 10) < 0.5 and hit["slot"] == (0, 3), hit
flat = score(pairs, three_pm_value)  # the point of the feature: one flat profile cannot see it
assert flat is None or flat["z"] < 3, flat

# slots under 3 samples are absent, never guessed
sparse = profile([(base + i * 3600, 1.0) for i in range(24)] + [(base + 7 * DAY, 1.0)])
assert sparse == {}, sparse
gap = score_against(prof, at3am, float("nan"))
assert gap["z"] is None and "finite" in gap["reason"], gap
assert score_against({}, at3am, 5.0)["z"] is None and "fewer than 3" in score_against({}, at3am, 5.0)["reason"]
flatprof = profile([(base + i * 7 * DAY, 7.0) for i in range(5)])
assert "equal" in score_against(flatprof, base, 7.0)["reason"]
# non-finite history values are excluded, not fatal
assert profile([(base + i * 7 * DAY, v) for i, v in enumerate([1.0, 2.0, 3.0, float("nan"), float("inf")])])[(0, 0)]["n"] == 3

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
tz =time.localtime(now).tm_gmtoff
store.add_history([("boiler-temp", t, level(t, tz)) for t in (now - (14 * 96 - i) * 900 - 30 for i in range(14 * 96))])


def call(ident, method, path, query=None):
    try:
        return app.handle(method, path, query or {}, None, ident)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


status, payload = call(admin, "GET", "/api/points/boiler-temp/seasonal")
assert status == 200 and payload["days"] == 14 and payload["point"] == "boiler-temp", payload
assert payload["coverage"] > 0.9 and len(payload["slots"]) > 150 and payload["note"] is None, payload["coverage"]
assert set(payload["slots"][0]) == {"dow", "hour", "median", "mad", "n"}, payload["slots"][0]

# newest sample sits where the daily pattern puts it -> normal; a spike on top of it -> unusual
status, payload = call(admin, "GET", "/api/points/boiler-temp/score")
assert status == 200 and payload["verdict"] in ("normal", "unknown") and payload["days"] == 14, payload
store.add_history([("boiler-temp", now + 1, level(now + 1, tz) + 25.0)])
status, payload = call(admin, "GET", "/api/points/boiler-temp/score")
assert status == 200 and payload["verdict"] == "unusual" and payload["z"] > 3, payload
assert payload["slot"] and abs(payload["value"] - payload["expected"]) > 20, payload

# thin history: 200, a note, empty slots, verdict unknown, never an exception
status, payload = call(admin, "GET", "/api/points/line-pressure/seasonal")
assert status == 200 and payload["slots"] == [] and payload["coverage"] == 0 and payload["note"], payload
status, payload = call(admin, "GET", "/api/points/line-pressure/score")
assert status == 200 and payload["verdict"] == "unknown" and payload["z"] is None and payload["note"], payload

# text points are 400, parameter bounds 422, unknown points 404
for route in ("seasonal", "score"):
    assert call(admin, "GET", f"/api/points/status-text/{route}")[0] == 400
    assert call(admin, "GET", f"/api/points/ghost/{route}")[0] == 404
    for days in ("1", "61", "soon"):
        status, payload = call(admin, "GET", f"/api/points/boiler-temp/{route}", {"days": days})
        assert status == 422 and payload["errors"][0]["property"] == "days", (route, days, payload)
    assert call(admin, "GET", f"/api/points/boiler-temp/{route}", {"days": "2"})[0] == 200
    assert call(admin, "GET", f"/api/points/boiler-temp/{route}", {"days": "60"})[0] == 200
    # same visibility as the point
    assert call(ANON, "GET", f"/api/points/boiler-temp/{route}")[0] == 404
    assert call(VIEWER, "GET", f"/api/points/boiler-temp/{route}")[0] == 200
    assert call(OPS, "GET", f"/api/points/boiler-temp/{route}")[0] == 404

print("ok")
