"""A handler or publisher that cannot deliver must be visible, not just a log line. Run: python tests/test_output_health.py"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _support import new_store  # noqa: E402
from durian.app import App  # noqa: E402
from durian.core import Event, Level  # noqa: E402

# ---- one local server that takes every POST, says 200, 500 or nothing (a timeout), and keeps the request
GOT = []
FAIL = [True]


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        GOT.append({"path": self.path, "type": self.headers.get("Content-Type"),
                    "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
        if self.path == "/slow":  # accept, then say nothing until well past the sender's 5s timeout
            time.sleep(6)
            return
        code = 500 if self.path == "/500" or (self.path == "/flip" and FAIL[0]) else 200
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"

store = new_store()
app = App(store)
admin = {"name": "admin", "roles": {"superadmin"}}


def put(name, body):
    return app.handle("PUT", f"/api/config/handler/{name}", {}, body, admin)


def wait(pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end and not pred():
        time.sleep(0.05)
    return pred()


for name, path, point in (("w", "/ok", "ok"), ("bad", "/500", "bad"), ("dead", "/closed", "dead"), ("flaky", "/flip", "flaky"), ("slow", "/slow", "slow")):
    url = "http://127.0.0.1:1/hook" if path == "/closed" else BASE + path
    assert put(name, {"type": "webhook", "url": url, "min_level": "INFORMATION", "scope": {"points": [point]}})[0] == 200

out = app.handle("GET", "/api/outputs", {}, None, admin)[1]
assert {t["name"] for t in out["targets"]} == {"w", "bad", "dead", "flaky", "slow"}, out
assert all(t["failed"] == 0 and t["last_error"] is None for t in out["targets"]), "nothing has been tried yet"
assert out["failed_deliveries"] == 0

# ---- a delivered webhook: the URL, the JSON shape and every value are what the receiver gets
ev = Event("ok", Level.URGENT, "95 > 90", 1_700_000_000.0)
app.outputs.on_event(ev, "raised")
assert wait(lambda: GOT), GOT
assert GOT[0]["path"] == "/ok" and GOT[0]["type"] == "application/json", GOT
assert GOT[0]["body"] == {"event": "raised", "point": "ok", "level": "URGENT", "message": "95 > 90",
                          "ts": 1_700_000_000.0, "cleared_ts": None}, GOT
assert wait(lambda: app.outputs.status.get("w", {}).get("last_ok")), app.outputs.status["w"]

# ---- a 500: the target is marked failed with a sentence an operator can read, and the event is counted
app.outputs.on_event(Event("bad", Level.URGENT, "boom", time.time()), "raised")
assert wait(lambda: app.outputs.status.get("bad", {}).get("failed")), app.outputs.status.get("bad")
assert app.outputs.status["bad"]["last_error"] == "the receiver answered 500 Internal Server Error", app.outputs.status["bad"]
assert sum(1 for g in GOT if g["path"] == "/500") == 1, GOT

# ---- a connection refused: the same, in words
app.outputs.on_event(Event("dead", Level.URGENT, "boom", time.time()), "raised")
assert wait(lambda: app.outputs.status.get("dead", {}).get("failed")), app.outputs.status.get("dead")
assert app.outputs.status["dead"]["last_error"] == "the connection was refused", app.outputs.status["dead"]

h = app.handle("GET", "/api/health", {}, None, admin)[1]
assert h["ok"] is False and h["failed_deliveries"] == 2, h
assert {f["name"] for f in h["faults"]} == {"bad", "dead"}, h["faults"]

# ---- a target that starts working again stops being a fault and records when it last worked
app.outputs.on_event(Event("flaky", Level.URGENT, "boom", time.time()), "raised")
assert wait(lambda: app.outputs.status.get("flaky", {}).get("failed")), app.outputs.status.get("flaky")
FAIL[0] = False
app.outputs.on_event(Event("flaky", Level.URGENT, "fine now", time.time()), "raised")
assert wait(lambda: app.outputs.status.get("flaky", {}).get("last_ok")), app.outputs.status["flaky"]
assert app.outputs.status["flaky"]["failing"] == 0 and app.outputs.status["flaky"]["last_error"] is None
assert {f["name"] for f in app.outputs.faults()} == {"bad", "dead"}, app.outputs.faults()

# ---- a delivery that outlasts its timeout gives up with a sentence instead of hanging forever
started = time.monotonic()
app.outputs.on_event(Event("slow", Level.URGENT, "boom", time.time()), "raised")
assert wait(lambda: app.outputs.status.get("slow", {}).get("failed"), timeout=9), app.outputs.status.get("slow")
elapsed = time.monotonic() - started
assert app.outputs.status["slow"]["last_error"] == "the receiver did not answer within 5s", app.outputs.status["slow"]
assert 5 <= elapsed < 8, f"gave up after {elapsed:.1f}s"

# ---- every failed delivery was counted, none silently dropped: four failures above
assert app.outputs.failed == 4, app.outputs.failed
assert app.handle("GET", "/api/health", {}, None, admin)[1]["failed_deliveries"] == 4
assert {f["name"] for f in app.outputs.faults()} == {"bad", "dead", "slow"}, app.outputs.faults()
w = next(t for t in app.handle("GET", "/api/outputs", {}, None, admin)[1]["targets"] if t["name"] == "bad")
assert w["failed"] == 1 and w["last_error"], w

# a handler that was never asked to deliver anything is not blamed
app2 = App(new_store())
app2.handle("PUT", "/api/config/handler/quiet", {}, {"type": "webhook", "url": "http://127.0.0.1:1/q", "min_level": "LIFE_SAFETY"}, admin)
assert app2.handle("GET", "/api/outputs", {}, None, admin)[1]["targets"][0]["failed"] == 0

# a publisher whose credentials cannot be read is reported at start, not dropped silently
store.put("publisher", "broken", {"type": "http", "url": "http://127.0.0.1:1/x", "points": ["*"], "creds": "no-such-file.json"})
app.reload()
faults = {f["name"] for f in app.outputs.faults()}
assert "broken" in faults, faults
b = next(t for t in app.handle("GET", "/api/outputs", {}, None, admin)[1]["targets"] if t["name"] == "broken")
assert b["last_error"].startswith("the credentials file could not be read"), b

print("ok")
