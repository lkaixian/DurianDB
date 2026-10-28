"""The persistent TCP publisher: one open socket, one JSON line per change, failures visible. Run: python tests/test_tcp_publisher.py"""
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _support import new_store  # noqa: E402
from durian.app import ApiError, App  # noqa: E402

admin = {"name": "admin", "roles": {"superadmin"}}


def wait(pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end and not pred():
        time.sleep(0.05)
    return pred()


lsn = socket.socket()
lsn.bind(("127.0.0.1", 0))
lsn.listen(5)
PORT = lsn.getsockname()[1]

store = new_store()
store.put("source", "demo", {"type": "virtual", "enabled": True})
store.put("point", "t1", {"source": "demo"})
app = App(store)


def put(name, body):
    return app.handle("PUT", f"/api/config/publisher/{name}", {}, body, admin)


# ---- validation at save
for bad in ({"host": "", "port": PORT}, {"host": "h", "port": 0}, {"host": "h", "port": "x"}, {"port": PORT}, {"host": "h", "port": PORT, "tls": "yes"}):
    try:
        put("v", {"type": "tcp", "points": ["*"], **bad})
    except ApiError as ex:
        assert ex.status == 422 and not any(e["message"].startswith("not a known") for e in ex.errors), (bad, ex.errors)
    else:
        raise AssertionError(f"saved {bad}")
assert put("good", {"type": "tcp", "host": "127.0.0.1", "port": PORT, "points": ["t1"]})[0] == 200

# ---- one line per change, over one connection
point = app.points()["t1"][0]
app.outputs.on_value(point, 21.5, 1_700_000_000.0)
lsn.settimeout(10)
conn, _ = lsn.accept()
f = conn.makefile("rb")
conn.settimeout(10)
assert json.loads(f.readline()) == {"point": "t1", "value": 21.5, "ts": 1_700_000_000.0, "quality": "good"}
app.outputs.on_value(point, 22.5, 1_700_000_001.0)
assert json.loads(f.readline())["value"] == 22.5  # same socket, no second accept
assert wait(lambda: app.outputs.status.get("good", {}).get("last_ok"))

# ---- the receiver goes away: failure is shown in words
f.close()
conn.close()
lsn.close()
n = 100.0
def broken():
    global n
    n += 1
    app.outputs.on_value(point, n, time.time())
    return app.outputs.status.get("good", {}).get("failing")
assert wait(broken), app.outputs.status
t = next(t for t in app.handle("GET", "/api/outputs", {}, None, admin)[1]["targets"] if t["name"] == "good")
assert t["last_error"], t

# ---- connection refused from the start
s = socket.socket()
s.bind(("127.0.0.1", 0))
dead = s.getsockname()[1]
s.close()
assert put("dead", {"type": "tcp", "host": "127.0.0.1", "port": dead, "points": ["t1"]})[0] == 200
point = app.points()["t1"][0]
app.outputs.on_value(point, 1.0, time.time())
assert wait(lambda: app.outputs.status.get("dead", {}).get("failed")), app.outputs.status
assert app.outputs.status["dead"]["last_error"] == "the connection was refused", app.outputs.status["dead"]
app.outputs.stop()
print("ok")
