"""Persisted models and their daily retraining. Run: python tests/test_models.py"""
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import models  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.store import Store  # noqa: E402

logging.disable(logging.CRITICAL)
tmp = Path(tempfile.mkdtemp())
DAY = 86400
db = str(tmp / "m.db")
st = Store(db)
seed(st)
app = App(st, tmp / "backups")
app.auth.bootstrap("admin-password-123")
st.add_user("viewer", hash_password("viewer-password-1"), ["user"])
ident = lambda u, p: app.auth.identity(app.auth.login(u, p))
admin, viewer, anon = ident("admin", "admin-password-123"), ident("viewer", "viewer-password-1"), {"name": None, "roles": {"anonymous"}}
now = time.time()


def call(who, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, who)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


def feed(point, days=14, step=900):
    st.add_history([(point, now - i * step, 10.0 + (now - i * step) // 3600 % 24 + (i % 3) * 0.1) for i in range(days * DAY // step)])


feed("boiler-temp")
st.add_history([("line-pressure", now - i, 1.0) for i in range(3)])  # thin
assert call(admin, "PUT", "/api/config/point/status-text", {"source": "demo", "datatype": "text", "generator": {"kind": "constant", "value": "RUN"}})[0] == 200

# ---- train persists, get returns it, a restart still has it
m = models.train(st, "boiler-temp", now)
assert m and m["point"] == "boiler-temp" and m["trained_at"] == now and m["window_days"] == 14 and m["samples"] > 1000, m
assert set(m["baseline"]) == {"mean", "sigma"} and m["seasonal"]["coverage"] == 1.0 and len(m["seasonal"]["slots"]) == 168
assert models.get(st, "boiler-temp") == m
st2 = Store(db)
assert models.get(st2, "boiler-temp") == m
st2.close() if hasattr(st2, "close") else None

# ---- stale, and the points that get no model
assert not models.stale(m, now + DAY, 8 * DAY) and models.stale(m, now + 9 * DAY, 8 * DAY)
assert models.train(st, "status-text", now) is None and models.train(st, "line-pressure", now) is None
assert models.train(st, "no-such-point", now) is None and models.get(st, "line-pressure") is None

# ---- the daily job: once per day, resumes by cursor, failures audited and retried tomorrow
models.drop(st, "boiler-temp")
assert call(admin, "PUT", "/api/database/settings", {"models_hour": 0})[0] == 200
assert call(admin, "PUT", "/api/database/settings", {"models_hour": 24})[0] == 422
models.BATCH = 1
assert "models" in app.maint.tick(now) and st.get_setting("models_cursor") is not None
for _ in range(60):
    if "models" not in app.maint.tick(now):
        break
assert models.get(st, "boiler-temp") and st.get_setting("models_cursor") is None
assert "models" not in app.maint.tick(now), "ran twice in one day"
models.BATCH = 25
models.drop(st, "boiler-temp")
assert "models" in app.maint.tick(now + DAY) and models.get(st, "boiler-temp")
real = models.train
models.train = lambda *a: 1 / 0
assert "models" not in app.maint.tick(now + 2 * DAY) and "models.failed" in {r["action"] for r in st.q("select action from audit")}
assert "models" not in app.maint.tick(now + 2 * DAY), "a failure is retried tomorrow, not every minute"
models.train = real

# ---- routes
models.drop(st, "boiler-temp")
s, b = call(viewer, "GET", "/api/points/boiler-temp/model")
assert s == 200 and b["model"] is None and b["note"], b
assert call(admin, "GET", "/api/points/nope/model")[0] == 404 and call(anon, "GET", "/api/points/boiler-temp/model")[0] == 404
assert call(viewer, "POST", "/api/models/retrain")[0] == 403 and call(anon, "POST", "/api/models/retrain")[0] == 401
assert call(admin, "POST", "/api/models/retrain", {"point": "nope"})[0] == 404
s, b = call(admin, "POST", "/api/models/retrain", {"point": "boiler-temp"})
assert s == 202 and isinstance(b["job"], int), (s, b)
while app.jobs.get(b["job"])["state"] == "running":
    time.sleep(0.01)
assert app.jobs.get(b["job"])["state"] == "done" and "models.retrain" in {r["action"] for r in st.q("select action from audit")}
assert call(viewer, "GET", "/api/points/boiler-temp/model")[1]["model"]["point"] == "boiler-temp"

# ---- score reports where its profile came from
assert call(viewer, "GET", "/api/points/boiler-temp/score")[1]["source"] == "stored"
assert call(viewer, "GET", "/api/points/boiler-temp/score", days="7")[1]["source"] == "live"
old = models.get(st, "boiler-temp")
st.set_setting("model:boiler-temp", {**old, "trained_at": time.time() - 9 * DAY})
assert call(viewer, "GET", "/api/points/boiler-temp/score")[1]["source"] == "live"
models.drop(st, "boiler-temp")
assert call(viewer, "GET", "/api/points/boiler-temp/score")[1]["source"] == "live"

# ---- deleting a point drops its model
assert call(admin, "PUT", "/api/config/point/tmp-pt", {"source": "demo", "generator": {"kind": "constant", "value": 1}})[0] == 200
feed("tmp-pt")
assert models.train(st, "tmp-pt", now) and models.get(st, "tmp-pt")
assert call(admin, "DELETE", "/api/config/point/tmp-pt")[0] == 200 and models.get(st, "tmp-pt") is None
print("ok")
