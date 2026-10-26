"""A handler or publisher that cannot deliver must be visible, not just a log line. Run: python tests/test_output_health.py"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _support import new_store  # noqa: E402
from durian.app import App  # noqa: E402
from durian.core import Event, Level  # noqa: E402

store = new_store()
app = App(store)
admin = {"name": "admin", "roles": {"superadmin"}}

# a webhook that points at a closed port: delivery fails, every time
app.handle("PUT", "/api/config/handler/w", {}, {"type": "webhook", "url": "http://127.0.0.1:1/hook", "min_level": "INFORMATION"}, admin)
app.handle("PUT", "/api/config/publisher/p", {}, {"type": "http", "url": "http://127.0.0.1:1/in", "points": ["*"]}, admin)

out = app.handle("GET", "/api/outputs", {}, None, admin)[1]
assert {t["name"] for t in out["targets"]} == {"w", "p"}, out
assert all(t["failed"] == 0 and t["last_error"] is None for t in out["targets"]), "nothing has been tried yet"
assert out["failed_deliveries"] == 0

# raise one alarm through the webhook handler
app.outputs.on_event(Event("t", Level.URGENT, "boom", time.time()), "raised")
deadline = time.time() + 10
while time.time() < deadline and not app.outputs.status.get("w", {}).get("failed"):
    time.sleep(0.05)
st = app.outputs.status.get("w")
assert st and st["failed"] == 1, st
assert "URLError" in st["last_error"] or "Connection" in st["last_error"], st
assert st["last_ok"] is None and st["since"] is not None, "never worked yet"

# the fault is on the health view and on the target list, named
h = app.handle("GET", "/api/health", {}, None, admin)[1]
assert h["ok"] is False and h["failed_deliveries"] >= 1, h
assert "w" in [f["name"] for f in h["faults"]], h["faults"]
out = app.handle("GET", "/api/outputs", {}, None, admin)[1]
w = next(t for t in out["targets"] if t["name"] == "w")
assert w["failed"] == 1 and w["last_error"], w
# a handler that was never asked to deliver anything is not blamed
app2 = App(new_store())
app2.handle("PUT", "/api/config/handler/quiet", {}, {"type": "webhook", "url": "http://127.0.0.1:1/q", "min_level": "LIFE_SAFETY"}, admin)
assert app2.handle("GET", "/api/outputs", {}, None, admin)[1]["targets"][0]["failed"] == 0

# a working delivery clears the fault for that target and records when it last worked
class Fake:
    def open(self, req, timeout=None):
        return type("R", (), {"read": lambda self, n: b"ok"})()

import durian.outputs as O
O.opener = Fake()
app.outputs.on_event(Event("t", Level.URGENT, "boom again", time.time()), "raised")
deadline = time.time() + 10
while time.time() < deadline and not app.outputs.status.get("w", {}).get("last_ok"):
    time.sleep(0.05)
w = app.outputs.status["w"]
assert w["last_ok"] is not None, w
assert app.handle("GET", "/api/health", {}, None, admin)[1]["faults"] == [], "a working route is not a fault"

# a publisher whose credentials cannot be read is reported at start, not dropped silently
store.put("publisher", "broken", {"type": "http", "url": "http://127.0.0.1:1/x", "points": ["*"], "creds": "no-such-file.json"})
app.reload()
faults = {f["name"] for f in app.outputs.faults()}
assert "broken" in faults, faults
b = next(t for t in app.handle("GET", "/api/outputs", {}, None, admin)[1]["targets"] if t["name"] == "broken")
assert "FileNotFoundError" in b["last_error"], b

print("ok")
