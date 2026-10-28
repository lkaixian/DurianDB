"""Haystack source: read-only poll of a Project Haystack server's `read` op (curVal). Run: python tests/test_haystack_source.py"""
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from _support import new_store  # noqa: E402

GRID = {"meta": {"ver": "3.0"}, "cols": [{"name": "id"}, {"name": "dis"}, {"name": "curVal"}], "rows": [
    {"id": {"_kind": "ref", "val": "p1", "dis": "Zone Temp"}, "dis": "ZoneTemp", "curVal": {"_kind": "number", "val": 21.5, "unit": "C"}},
    {"id": {"_kind": "ref", "val": "p2"}, "curVal": 7},                                  # named by id: no dis
    {"id": {"_kind": "ref", "val": "p3"}, "dis": "Nan", "curVal": {"_kind": "number", "val": "NaN"}},
    {"id": {"_kind": "ref", "val": "p4"}, "dis": "Inf", "curVal": 1e999},
    {"id": {"_kind": "ref", "val": "p5"}, "dis": "Txt", "curVal": "on"},
    {"id": {"_kind": "ref", "val": "p6"}, "dis": "NoCur"}, {}]}  # {} = a requested id that was not found
seen = []


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        seen.append((self.command, self.path, self.headers.get("Authorization"), self.headers.get("Accept"), self.headers.get("Content-Type"), json.loads(raw or b"null")))
        body = json.dumps(GRID).replace("1e999", "Infinity").encode() if self.path == "/api/read" else b"{}"
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    do_GET = do_PUT = do_DELETE = lambda self: seen.append((self.command, self.path))

    def log_message(self, *a):
        pass


web = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=web.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{web.server_port}/api"
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
src = lambda n: next(x for x in app.engine.sources if x.name == n and not x._stop.is_set())  # a config change rebuilds sources
last = lambda: {p["name"]: p["last"] for p in call("GET", "/api/points")[1]}

# ---- config
assert "ids" in props(put("source", "h", {"type": "haystack", "url": URL})), "ids or filter is required"
assert "url" in props(put("source", "h", {"type": "haystack", "ids": ["p1"]}))
assert "ids" in props(put("source", "h", {"type": "haystack", "url": URL, "ids": [5]}))
assert "ids" in props(put("source", "h", {"type": "haystack", "url": URL, "ids": ["p1"], "filter": "point"})), "not both"
tok = tmp / "tok"; tok.write_text("secret\n")
assert put("source", "h", {"type": "haystack", "url": URL, "ids": ["p1", "p2"], "token_file": str(tok), "period": 60})[0] == 200
assert put("source", "hf", {"type": "haystack", "url": URL, "filter": "point and cur", "period": 60})[0] == 200
assert put("point", "bad", {"source": "h", "settable": True})[0] == 422, "read-only"

# ---- reading
for n, extra in (("ZoneTemp", {}), ("p2", {}), ("Nan", {}), ("Inf", {}), ("Txt", {}), ("NoCur", {}), ("Missing", {})):
    assert put("point", n, {"source": "h", **extra})[0] == 200, n
s = next(x for x in app.engine.sources if x.name == "h"); time.sleep(0.3); s.cycle(); app.tick(time.time() + 1)
v = last()
assert v["ZoneTemp"] == 21.5 and v["p2"] == 7.0, v
assert all(v[n] is None for n in ("Nan", "Inf", "Txt", "NoCur", "Missing")), v
assert "rejected" in s.error, s.error
m, path, auth, acc, ct, body = seen[-1]
assert (m, path, auth, acc, ct) == ("POST", "/api/read", "Bearer secret", "application/json", "application/json"), seen[-1]
assert body == {"_kind": "grid", "meta": {"ver": "3.0"}, "cols": [{"name": "id"}],
                "rows": [{"id": {"_kind": "ref", "val": "p1"}}, {"id": {"_kind": "ref", "val": "p2"}}]}, body
assert put("point", "HfPt", {"source": "hf"})[0] == 200
sf = src("hf"); sf.cycle()
assert any(x[5] == {"_kind": "grid", "meta": {"ver": "3.0"}, "cols": [{"name": "filter"}], "rows": [{"filter": "point and cur"}]} for x in seen if len(x) > 5), seen  # background polls also land in seen
assert {(x[0], x[1]) for x in seen} == {("POST", "/api/read")}, "only the read op is ever sent"

# ---- a filter with no matches is an empty grid: points rejected, source still up
GRID["rows"] = []
sf = src("hf"); sf.cycle()
assert sf.connected is True and "rejected" in sf.error, sf.error

# ---- server down: source down, in words
web.shutdown(); web.server_close()
s = src("h"); s.url = "http://127.0.0.1:1/api/read"; s.next_try = 0; s.cycle()
assert s.connected is False and s.error, s.error
print("ok")
