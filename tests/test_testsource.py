"""The Test button for sources. Run: python tests/test_testsource.py"""
import json
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.modbus_sim import Slave  # noqa: E402
from durian.testsource import test_source  # noqa: E402
from _support import new_store  # noqa: E402


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"sensors": {"temp": 21.5, "ok": True, "name": "x"}, "items": [{"v": 1}, {"v": 2}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


web = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=web.serve_forever, daemon=True).start()

# ---- HTTP JSON lists the numbers it can see, in the path syntax a point uses (booleans and text are not numbers)
r = test_source({"type": "http_json", "url": f"http://127.0.0.1:{web.server_port}/", "timeout": 2})
assert r["ok"] and [f["name"] for f in r["found"]] == ["sensors.temp", "items[0].v", "items[1].v"] and r["found"][0]["value"] == 21.5, r
r = test_source({"type": "http_json", "url": "http://127.0.0.1:1/", "timeout": 1})
assert not r["ok"] and r["message"]  # a refused connection is an answer, not an exception

# ---- SQL shows the rows
db = Path(tempfile.mkdtemp()) / "t.db"
c = sqlite3.connect(db)
c.execute("create table r(name text, value real)")
c.executemany("insert into r values (?,?)", [("tank1", 4.5), ("tank2", 7)])
c.commit()
c.close()
r = test_source({"type": "sql", "path": str(db), "query": "select name, value from r"})
assert r["ok"] and r["found"] == [{"name": "tank1", "value": 4.5}, {"name": "tank2", "value": 7}], r
assert not test_source({"type": "sql", "path": str(db), "query": "select nope from r"})["ok"]

# ---- Modbus: an answering device passes, a closed port fails, and a bad setting is reported before anything is opened
sim = Slave("tcp").start()
assert test_source({"type": "modbus", "link": {"kind": "tcp", "host": "127.0.0.1", "port": sim.port}, "timeout": 1, "retries": 0})["ok"]
bad = test_source({"type": "modbus", "link": {"kind": "tcp", "host": "127.0.0.1", "port": 1}, "timeout": 0.5, "retries": 0})
assert not bad["ok"] and "No answer" in bad["message"], bad
invalid = test_source({"type": "modbus", "link": {"kind": "tcp"}})
assert not invalid["ok"] and "not valid" in invalid["message"]
assert not test_source({"type": "virtual"})["ok"] and not test_source("nonsense")["ok"]

# ---- the API: administrators only
store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
store.add_user("op", hash_password("viewer-password-1"), ["user"])
op = app.auth.identity(app.auth.login("op", "viewer-password-1"))
body = {"type": "http_json", "url": f"http://127.0.0.1:{web.server_port}/", "timeout": 2}
assert app.handle("POST", "/api/test/source", {}, body, admin)[1]["ok"]
try:
    app.handle("POST", "/api/test/source", {}, body, op)
    raise AssertionError("a non-admin must not be able to make the server connect anywhere")
except ApiError as e:
    assert e.status == 403
print("ok")
