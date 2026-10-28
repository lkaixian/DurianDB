"""eGauge source: instantaneous XML from a fake meter. Run: python tests/test_egauge_source.py"""
import http.server
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from _support import new_store  # noqa: E402

GOOD = (b'<?xml version="1.0" encoding="UTF-8" ?><data serial="0x1"><ts>1700000000</ts>'
        b'<r t="P" n="Grid"><v>999</v><i>1234.5</i></r><r t="P" n="Solar"><v>1</v><i>-250</i></r>'
        b'<r t="V" n="L1"><v>1</v><i>nan</i></r><r t="P" n="Inf"><i>inf</i></r></data>')
HOSTILE = b'<?xml version="1.0"?><!DOCTYPE data [<!ENTITY x "boom">]><data><r n="Grid"><i>&x;</i></r></data>'
state = {"body": GOOD, "paths": [], "auth": []}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        state["paths"].append(self.path); state["auth"].append(self.headers.get("Authorization"))
        self.send_response(200); self.send_header("Content-Type", "text/xml"); self.end_headers(); self.wfile.write(state["body"])

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]

tmp = Path(tempfile.mkdtemp())
app = App(new_store())
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


put = lambda kind, name, body: call("PUT", f"/api/config/{kind}/{name}", body)
props = lambda r: {e["property"] for e in r[1].get("errors", [])}
last = lambda: {p["name"]: p["last"] for p in call("GET", "/api/points")[1]}

# ---- config
assert "host" in props(put("source", "g", {"type": "egauge"})), "host is required"
assert "port" in props(put("source", "g", {"type": "egauge", "host": "h", "port": 0}))
assert "tls" in props(put("source", "g", {"type": "egauge", "host": "h", "tls": "yes"}))
assert "creds" in props(put("source", "g", {"type": "egauge", "host": "h", "creds": str(tmp / "nope.json")}))
cf = tmp / "c.json"; cf.write_text(json.dumps({"user": "owner", "password": "pw"}))
assert put("source", "m", {"type": "egauge", "host": "127.0.0.1", "port": port, "tls": False, "period": 60, "creds": str(cf)})[0] == 200
for n in ("Grid", "Solar", "L1", "Inf", "Absent"):
    assert put("point", n, {"source": "m"})[0] == 200, n

# ---- reading
s = next(x for x in app.engine.sources if x.name == "m"); time.sleep(0.3); s.cycle(); app.tick(time.time() + 1)
v = last()
assert v["Grid"] == 1234.5 and v["Solar"] == -250.0, v
assert v["L1"] is None and v["Inf"] is None and v["Absent"] is None, v  # non-finite and missing registers are not values
assert "rejected" in s.error and "L1" in s.error, s.error
assert state["paths"][-1].startswith("/cgi-bin/egauge?inst"), state["paths"]

# ---- hostile XML (DOCTYPE + entity) is refused, in words
state["body"] = HOSTILE
s.next_try = 0; s.cycle()
assert s.connected is False and "not allowed" in s.error, s.error
assert "boom" not in s.error and "Error" not in s.error, s.error

# ---- not XML at all
state["body"] = b"<data><r"
s.next_try = 0; s.cycle()
assert s.connected is False and "XML" in s.error, s.error

# ---- nothing listening: source down
srv.shutdown(); srv.server_close()
s.next_try = 0; s.cycle()
assert s.connected is False and s.error and "Error" not in s.error, s.error
print("ok")
