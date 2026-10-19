"""End-to-end check of outputs against a running server and the lab broker.

Needs: python -m durian.server running (DURIAN_ADMIN_PASSWORD set, tick 1 s), lab broker from tools/mqtt_lab.py,
and tools/sim_publisher.py running. Run: DURIAN_ADMIN_PASSWORD=... python tools/e2e_check.py [base_url]
"""
import http.cookiejar
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.mqtt_source import tls_client  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
CREDS = Path(__file__).resolve().parent.parent / "lab" / "mqtt" / "creds.json"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Requested-With": "durian", "Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


hooks = []


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        hooks.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


web = HTTPServer(("127.0.0.1", 9055), Hook)
threading.Thread(target=web.serve_forever, daemon=True).start()

c = json.loads(CREDS.read_text())
seen = []
sub = tls_client(c)
sub.on_connect = lambda cl, u, f, rc, p: cl.subscribe([("durian-out/#", 1), ("durian-cmd/#", 1)])
sub.on_message = lambda cl, u, m: seen.append((m.topic, json.loads(m.payload)))
sub.connect(c["host"], c["port"])
sub.loop_start()

assert call("POST", "/api/login", {"username": "admin", "password": os.environ["DURIAN_ADMIN_PASSWORD"]})[0] == 200
assert call("PUT", "/api/config/handler/e2e-hook", {"type": "webhook", "url": "http://127.0.0.1:9055/hook", "min_level": "WARNING"})[0] == 200
s, r = call("PUT", "/api/config/publisher/e2e-out", {"type": "mqtt", "creds": str(CREDS), "prefix": "durian-out/", "points": ["e2e-hot"]})
assert s == 200, r
overlap = call("PUT", "/api/config/publisher/bad", {"type": "mqtt", "creds": str(CREDS), "prefix": "durian/x/", "points": ["*"]})
assert overlap[0] == 422 and "overlaps" in json.dumps(overlap[1]), overlap  # would feed its own output back in
assert call("PUT", "/api/config/point/e2e-hot", {"source": "demo", "generator": {"kind": "constant", "value": 99},
            "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"}]})[0] == 200
assert call("PUT", "/api/config/point/mqtt-flow", {"source": "lab-mqtt", "settable": True, "set_roles": ["user"]})[0] == 200
time.sleep(4)
assert call("POST", "/api/points/mqtt-flow/set", {"value": 7})[0] == 200
time.sleep(3)
call("PUT", "/api/config/point/e2e-hot", {"source": "demo", "generator": {"kind": "constant", "value": 10},  # reload closes the event
     "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"}]})
time.sleep(3)

print("webhooks:", [(h["event"], h["point"], h["level"]) for h in hooks])
print("mqtt seen:", sorted({t for t, _ in seen}))
assert any(h["event"] == "raised" and h["point"] == "e2e-hot" and h["level"] == "URGENT" for h in hooks), "no raised webhook"
assert any(t == "durian-out/e2e-hot" and p["value"] == 99 for t, p in seen), "no published value"
assert any(t == "durian-cmd/mqtt-flow" and p == {"value": 7} for t, p in seen), "no command sent"
for kind, name in (("handler", "e2e-hook"), ("publisher", "e2e-out"), ("point", "e2e-hot"), ("point", "mqtt-flow")):
    call("DELETE", f"/api/config/{kind}/{name}")
sub.loop_stop()
web.shutdown()
print("ok")
