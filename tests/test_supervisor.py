"""Supervisor: keeps the server child alive, restarts hung/dead ones, falls to safe mode on a crash loop.
Run: python tests/test_supervisor.py"""
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian import supervisor  # noqa: E402

FAKE = r'''
import http.server, os, sys, time
mode, safe = os.environ.get("FAKE_MODE"), os.environ.get("DURIAN_SAFE") == "1"
open(os.environ["FAKE_LOG"], "a").write("start safe=%d\n" % safe)
if mode == "crash" and not safe:
    sys.exit(3)
born = time.time()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if mode == "hang" and not safe and time.time() - born > 1.5:
            time.sleep(60)
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok": true}')
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["DURIAN_PORT"])), H).serve_forever()
'''
tmp = tempfile.mkdtemp()
script = os.path.join(tmp, "fake.py")
open(script, "w").write(FAKE)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run(mode, seconds, **kw):
    """Run the supervisor on a fake child for `seconds`; return (starts, status)."""
    d = tempfile.mkdtemp()
    log = os.path.join(d, "starts.log")
    os.environ.update(FAKE_MODE=mode, FAKE_LOG=log, DURIAN_PORT=str(free_port()), DURIAN_DB=os.path.join(d, "x.db"))
    stop = threading.Event()
    opts = dict(health_interval=0.2, hang_after=2, startup_grace=5, backoff_cap=0.4, probe_timeout=0.3, **kw)
    t = threading.Thread(target=supervisor.run, args=([sys.executable, script], stop), kwargs=opts)
    t.start()
    time.sleep(seconds)
    stop.set()
    t.join(20)
    assert not t.is_alive(), "supervisor did not stop"
    starts = open(log).read().split("\n")[:-1] if os.path.exists(log) else []
    return starts, json.load(open(os.path.join(d, "supervisor.json")))


# healthy child: started once, stopped cleanly, nothing left running
starts, st = run("ok", 1.5)
assert starts == ["start safe=0"], starts
assert st["state"] == "stopped" and st["restarts"] == 0 and st["safe"] is False

# hung child (answers once, then stalls): killed and restarted
starts, st = run("hang", 6)
assert len(starts) >= 2 and st["restarts"] >= 1 and "hung" in st["last_exit"], (starts, st)

# crash loop: after N failures one safe-mode child runs and stays
starts, st = run("crash", 4, crash_limit=3)
assert starts[:3] == ["start safe=0"] * 3 and starts[3] == "start safe=1" and len(starts) == 4, starts
assert st["safe"] is True and st["state"] == "stopped"

# alert: http POST with {"text": ...}; a dead alert URL is swallowed
got = []


class A(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), A)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["DURIAN_ALERT_URL"] = f"http://127.0.0.1:{srv.server_port}/hook"
run("crash", 3, crash_limit=2)
assert got and "safe mode" in got[-1]["text"], got
srv.shutdown()
os.environ["DURIAN_ALERT_URL"] = "http://127.0.0.1:1/none"
run("crash", 2, crash_limit=2)  # must not raise
assert supervisor.alert("file:///etc/passwd", "x") is False  # http(s) only
os.environ.pop("DURIAN_ALERT_URL")

# default command
assert supervisor.default_cmd(False)[1:] == ["-m", "durian.server"]
assert supervisor.default_cmd(True)[1:] == ["run"]
print("ok")
