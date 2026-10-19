"""HTTP sender publisher: validation, change-only sending with a resend interval, bearer token, bad points skipped. Run: python tests/test_http_sender.py"""
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

GOT = []


class Sink(BaseHTTPRequestHandler):
    def do_POST(self):
        GOT.append((self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), Sink)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_port}/api/ingest/in"
creds = Path(tempfile.mkdtemp()) / "t.json"
creds.write_text(json.dumps({"token": "dtk_abc"}))

store = new_store()
seed(store)
app = App(store)
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def put(name, body):
    try:
        return app.handle("PUT", f"/api/config/publisher/{name}", {}, body, admin)
    except ApiError as e:
        return e.status, {"errors": e.errors}


props = lambda r: {e["property"] for e in r[1]["errors"]}
assert "url" in props(put("p", {"type": "http", "url": "ftp://x", "points": ["*"]}))
assert "creds" in props(put("p", {"type": "http", "url": URL, "creds": "nope.json", "points": ["*"]}))
assert "resend" in props(put("p", {"type": "http", "url": URL, "resend": 0, "points": ["*"]}))
assert "points" in props(put("p", {"type": "http", "url": URL, "points": []}))
assert "type" in props(put("p", {"type": "ftp", "points": ["*"]}))
assert put("p", {"type": "http", "url": URL, "creds": str(creds), "points": ["pump-setpoint"], "resend": 3600})[0] == 200
app.reload()

t = time.time()
app.tick(t)
app.tick(t + 1)  # same value again: not sent a second time
for _ in range(50):
    if GOT:
        break
    time.sleep(0.05)
time.sleep(0.3)
assert len(GOT) == 1, GOT
auth, body = GOT[0]
assert auth == "Bearer dtk_abc" and body["point"] == "pump-setpoint" and body["value"] == 40 and body["quality"] == "good", GOT
assert app.handle("POST", "/api/points/pump-setpoint/set", {}, {"value": 55}, admin)[0] == 200
app.tick(t + 2)
for _ in range(50):
    if len(GOT) > 1:
        break
    time.sleep(0.05)
assert len(GOT) == 2 and GOT[1][1]["value"] == 55, GOT  # a changed value goes at once
print("ok")
