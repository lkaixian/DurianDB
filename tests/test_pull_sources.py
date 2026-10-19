"""HTTP JSON and SQL sources through config, polling, failures and safety. Run: python tests/test_pull_sources.py"""
import json
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.http_source import dig  # noqa: E402
from durian.sql_source import SqlSource  # noqa: E402
from durian.store import Store  # noqa: E402
from _support import new_store  # noqa: E402

tmp = Path(tempfile.mkdtemp())

# ---- path lookup
doc = {"a": {"b": [{"c": 1}, {"c": 2.5}]}, "t": True, "s": "text", "n": None}
assert dig(doc, "a.b[1].c") == 2.5 and dig(doc, "a.b[0].c") == 1 and dig(doc, "t") is True
for bad in ("a.x", "a.b[5].c", "a.b.c", "t.x", "nope"):
    try:
        dig(doc, bad)
        raise AssertionError(bad)
    except (KeyError, IndexError, TypeError):
        pass

# ---- a small web server to poll
state = {"temp": 21.5, "calls": 0, "auth": []}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        state["calls"] += 1
        state["auth"].append(self.headers.get("Authorization"))
        if self.path == "/data":
            body = json.dumps({"sensors": {"temp": state["temp"], "ok": True, "label": "boiler", "list": [10, 20]}}).encode()
        elif self.path == "/auth":
            if self.headers.get("Authorization") != "Bearer secret-token":
                self.send_response(401)
                self.end_headers()
                return
            body = b'{"v": 5}'
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/data")
            self.end_headers()
            return
        elif self.path == "/big":
            body = json.dumps({"pad": "x" * 5000}).encode()
        else:
            body = b"<html>not json</html>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


web = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=web.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{web.server_port}"

app = App(new_store())
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


put = lambda kind, name, body: call("PUT", f"/api/config/{kind}/{name}", body)
props = lambda r: {e["property"] for e in r[1]["errors"]}
last = lambda: {p["name"]: p["last"] for p in call("GET", "/api/points")[1]}
src_of = lambda name: next(s for s in app.engine.sources if s.name == name)

# ---- validation
r = put("source", "w", {"type": "http_json", "url": "file:///etc/passwd", "headers": {"X-A": "a\nb", "bad name": "v"}, "timeout": 0,
                        "token_file": str(tmp / "nope"), "backoff_scale": 1})
assert r[0] == 422 and {"url", "headers", "timeout", "token_file", "backoff_scale"} <= props(r), props(r)
assert put("source", "w", {"type": "http_json"})[0] == 422
r = put("source", "q", {"type": "sql", "path": str(tmp / "missing.db"), "query": "delete from t"})
assert r[0] == 422 and {"path", "query"} <= props(r)
for q in ("select 1; drop table t", "update t set a=1", "  "):
    assert "query" in props(put("source", "q", {"type": "sql", "path": __file__, "query": q})), q

# ---- HTTP JSON polling, scaling, partial rejection
(tmp / "token").write_text("secret-token\n")
assert put("source", "web", {"type": "http_json", "url": URL + "/data", "period": 60, "timeout": 2})[0] == 200
for n, path, extra in (("temp", "sensors.temp", {}), ("flag", "sensors.ok", {}), ("second", "sensors.list[1]", {"scale": {"multiplier": 0.5, "additive": 1}}),
                       ("text", "sensors.label", {}), ("gone", "sensors.nothing", {}), ("badpath", "sensors.list[9]", {})):
    assert put("point", n, {"source": "web", "http": {"path": path}, **extra})[0] == 200, n
for bad in ({"http": {"path": "a..b"}}, {"http": {"path": "a b"}}, {}, {"http": {"path": "x"}, "settable": True}, {"http": {"path": "x"}, "scale": {"multiplier": 0}}):
    assert put("point", "bad", {"source": "web", **bad})[0] == 422, bad
src = src_of("web")
time.sleep(0.4)
src.cycle()
app.tick(time.time())
v = last()
assert v["temp"] == 21.5 and v["flag"] == 1.0 and v["second"] == 11.0 and v["text"] is None and v["gone"] is None, v
assert "3 point(s) rejected" in src.error and src.connected is True
state["temp"] = 30.0
src.cycle()
app.tick(time.time() + 1)
assert last()["temp"] == 30.0

# ---- safety: redirects, oversized replies, non-JSON, auth header from a token file
assert put("source", "redir", {"type": "http_json", "url": URL + "/redirect", "period": 60})[0] == 200
put("point", "r1", {"source": "redir", "http": {"path": "sensors.temp"}})
s = src_of("redir"); time.sleep(0.3); s.cycle()
assert s.connected is False and "HTTPError" in s.error and "302" in s.error, s.error  # not followed
put("source", "bigger", {"type": "http_json", "url": URL + "/big", "max_bytes": 1024, "period": 60})
put("point", "b1", {"source": "bigger", "http": {"path": "pad"}})
s = src_of("bigger"); time.sleep(0.3); s.cycle()
assert "larger than max_bytes" in s.error, s.error
put("source", "html", {"type": "http_json", "url": URL + "/nothing", "period": 60})
put("point", "h1", {"source": "html", "http": {"path": "x"}})
s = src_of("html"); time.sleep(0.3); s.cycle()
assert s.connected is False and "JSONDecodeError" in s.error, s.error
put("source", "authd", {"type": "http_json", "url": URL + "/auth", "token_file": str(tmp / "token"), "period": 60})
put("point", "a1", {"source": "authd", "http": {"path": "v"}})
s = src_of("authd"); time.sleep(0.3); s.cycle(); app.tick(time.time() + 2)
assert s.connected is True and last()["a1"] == 5.0 and "Bearer secret-token" in state["auth"]

# ---- failure and back-off: server goes away, then returns
s = src_of("web"); s.period, s.backoff_scale, s.backoff_max = 1.0, 2.0, 30.0
web.shutdown(); web.server_close()
s.next_try = 0; s.cycle()
assert s.connected is False and s.fails == 1 and "retry in" in s.error and 1.9 < s.next_try - time.time() < 2.1
s.next_try = 0; s.cycle()
assert s.fails == 2 and 3.9 < s.next_try - time.time() < 4.1
assert last()["temp"] == 30.0  # the last good value stays; a NO_UPDATE detector is what flags staleness

# ---- SQL source
db = tmp / "plant.db"
con = sqlite3.connect(db)
con.execute("create table readings(name text, value real)")
con.executemany("insert into readings values (?,?)", [("tank1", 12.5), ("tank2", 40), ("bad", None)])
con.commit()
assert put("source", "sql", {"type": "sql", "path": str(db), "query": "select name, value from readings", "period": 60})[0] == 200
for n, key, extra in (("t1", "tank1", {}), ("t2", "tank2", {"scale": {"multiplier": 2}}), ("t3", "tank3", {}), ("tb", "bad", {})):
    assert put("point", n, {"source": "sql", "sql": {"key": key}, **extra})[0] == 200
assert "sql.key" in props(put("point", "x", {"source": "sql"}))
s = src_of("sql"); time.sleep(0.3); s.cycle(); app.tick(time.time() + 3)
v = last()
assert v["t1"] == 12.5 and v["t2"] == 80.0 and v["t3"] is None and v["tb"] is None, v
assert "2 point(s) rejected" in s.error
con.execute("update readings set value = 99 where name = 'tank1'"); con.commit()
s.cycle(); app.tick(time.time() + 4)
assert last()["t1"] == 99.0
# defense in depth: even if validation were bypassed, the connection cannot write
w = SqlSource("w", {"path": str(db), "query": "insert into readings values ('x', 1)", "period": 60})
w.cycle()
assert w.connected is False and "readonly" in w.error.lower(), w.error
assert con.execute("select count(*) from readings").fetchone()[0] == 3
con.close()
print("ok")
