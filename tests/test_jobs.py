"""Slow work runs off the request thread. Run: python tests/test_jobs.py"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.jobs import Jobs  # noqa: E402
from durian.store import Store  # noqa: E402


def wait(jobs, jid, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        r = jobs.get(jid)
        if r and r["state"] != "running":
            return r
        time.sleep(0.01)
    raise AssertionError(f"job {jid} did not finish")


# ---- the runner: a slow job runs off the caller's thread, and reports when it is done
jobs = Jobs()
t0 = time.monotonic()
jid = jobs.submit("slow", lambda: (time.sleep(0.4), "finished")[1])
assert time.monotonic() - t0 < 0.2, "submit must return at once"
r = wait(jobs, jid)
assert r["state"] == "done" and r["result"] == "finished" and r["error"] is None, r
assert r["finished"] >= r["started"]

# ---- a failing job records a sentence, never only a traceback in the log
jid = jobs.submit("boom", lambda: 1 / 0)
r = wait(jobs, jid)
assert r["state"] == "error" and r["error"] == "division by zero", r
assert "ZeroDivisionError" not in r["error"]

# ---- the API: a backup is a job by default; ?wait=1 keeps the old synchronous body
tmp = Path(tempfile.mkdtemp())
store = Store(str(tmp / "jobs.db"))
seed(store)
app = App(store, tmp / "backups")
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
store.add_user("viewer", hash_password("viewer-password-1"), ["user"])
viewer = app.auth.identity(app.auth.login("viewer", "viewer-password-1"))


def call(who, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, who)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


status, body = call(admin, "POST", "/api/database/backup")
assert status == 202 and isinstance(body["job"], int) and "ask GET /api/jobs" in body["message"], body
s, j = call(admin, "GET", f"/api/jobs/{body['job']}")
if j["state"] == "running":
    j = wait(app.jobs, body["job"])
assert s == 200 and j["state"] == "done" and j["result"]["name"].startswith("durian-"), j

status, r = call(admin, "POST", "/api/database/backup", wait="1")
assert status == 200 and r["name"].startswith("durian-") and r["bytes"] > 0, r

# ---- source test: 202 and a job by default, ?wait=1 still answers at once
status, body = call(admin, "POST", "/api/test/source", {"type": "nonsense"})
assert status == 202 and "ask GET /api/jobs" in body["message"], body
j = wait(app.jobs, body["job"])
assert j["state"] == "done" and j["result"]["ok"] is False, j
status, r = call(admin, "POST", "/api/test/source", {"type": "nonsense"}, wait="1")
assert status == 200 and r["ok"] is False, r

# ---- an unknown job is a sentence; only its owner, or a superadmin, may read one
assert call(admin, "GET", "/api/jobs/999999")[0] == 404
jid = app.jobs.submit("owned", lambda: "mine", owner="admin")
assert call(viewer, "GET", f"/api/jobs/{jid}")[0] == 403
assert call(admin, "GET", f"/api/jobs/{jid}")[0] == 200
print("ok")
