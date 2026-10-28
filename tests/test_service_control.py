"""durian start / status / stop / restart / logs with a tiny fake server as the child.
Run: python tests/test_service_control.py"""
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import cli  # noqa: E402
from durian.core import pid_alive  # noqa: E402

tmp = Path(tempfile.mkdtemp())
os.environ["DURIAN_HOME"] = str(tmp)
os.environ["DURIAN_DB"] = str(tmp / "durian.db")
os.environ.pop("DURIAN_SAFE", None)
s = socket.socket()
s.bind(("127.0.0.1", 0))
PORT = s.getsockname()[1]
s.close()

FAKE = tmp / "fake.py"
FAKE.write_text('''
import os, sys, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
h = HTTPServer(("127.0.0.1", int(os.environ["DURIAN_PORT"])), H)
threading.Thread(target=h.serve_forever, daemon=True).start()
print("fake server up", flush=True)
stop = os.path.join(os.environ["DURIAN_HOME"], "durian.stop")
while not os.path.exists(stop) or os.environ.get("FAKE_IGNORE_STOP"):
    time.sleep(0.1)
print("fake server leaving", flush=True)
''')
cli.child_command = lambda args: [sys.executable, str(FAKE)]
cli.STOP_WAIT = 3  # seconds; the real wait is 20
pidf, stopf, logf = tmp / "durian.pid", tmp / "durian.stop", tmp / "durian.log"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue() + err.getvalue()


pids = []
try:
    # nothing running
    code, out = run("status")
    assert code == 1 and "stopped" in out, out
    code, out = run("stop")
    assert code == 0 and "not running" in out, out

    # start: pid file, health, URL
    code, out = run("start", "--port", str(PORT))
    assert code == 0 and f"http://127.0.0.1:{PORT}" in out, out
    info = json.loads(pidf.read_text())
    pids.append(info["pid"])
    assert info["port"] == PORT and info["host"] == "127.0.0.1" and info["tls"] is False and info["safe"] is False and info["started"]
    assert pid_alive(info["pid"])
    code, out = run("status")
    assert code == 0 and "running" in out and f"pid {info['pid']}" in out and "spool" in out, out

    # a second start is refused with a sentence
    code, out = run("start", "--port", str(PORT))
    assert code == 1 and "already running" in out, out

    # logs
    code, out = run("logs", "-n", "5")
    assert code == 0 and "fake server up" in out, out

    # stop through the stop file: clean exit, files gone
    code, out = run("stop")
    assert code == 0 and "stopped cleanly" in out, out
    assert not pidf.exists() and not stopf.exists() and not pid_alive(info["pid"])
    assert "fake server leaving" in logf.read_text()

    # restart from stopped starts it; restart again replaces the process
    code, out = run("restart", "--port", str(PORT))
    assert code == 0, out
    first = json.loads(pidf.read_text())["pid"]
    pids.append(first)
    code, out = run("restart", "--port", str(PORT))
    second = json.loads(pidf.read_text())["pid"]
    pids.append(second)
    assert code == 0 and second != first and not pid_alive(first), out

    # a child that ignores the stop file is killed after the wait
    os.environ["FAKE_IGNORE_STOP"] = "1"
    run("stop")
    code, out = run("start", "--port", str(PORT))
    assert code == 0, out
    pid = json.loads(pidf.read_text())["pid"]
    pids.append(pid)
    t = time.time()
    code, out = run("stop")
    assert code == 0 and "killed" in out and not pid_alive(pid) and not pidf.exists() and not stopf.exists(), out
    assert 2 < time.time() - t < 15
    del os.environ["FAKE_IGNORE_STOP"]

    # a stale pid file: status says so, start replaces it, stop clears it
    pidf.write_text(json.dumps({"pid": pid, "port": PORT, "host": "127.0.0.1", "tls": False, "started": time.time(), "safe": False}))
    code, out = run("status")
    assert code == 1 and "stale" in out, out
    code, out = run("stop")
    assert code == 0 and "stale" in out and not pidf.exists(), out
    pidf.write_text(json.dumps({"pid": pid, "port": PORT, "host": "127.0.0.1", "tls": False, "started": time.time(), "safe": False}))
    code, out = run("start", "--port", str(PORT))
    assert code == 0, out
    pids.append(json.loads(pidf.read_text())["pid"])
    run("stop")

    # a child that dies at once: start gives the log tail and exits 1
    cli.child_command = lambda args: [sys.executable, "-c", "print('boom: cannot bind'); raise SystemExit(3)"]
    code, out = run("start", "--port", str(PORT))
    assert code == 1 and "boom: cannot bind" in out and not pidf.exists(), out

    # the log is rotated at start once it passes 5 MB
    logf.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    run("start", "--port", str(PORT))
    assert (tmp / "durian.log.1").exists() and logf.stat().st_size < 1000
finally:
    for p in pids:
        if pid_alive(p):
            if os.name == "nt":
                os.system(f"taskkill /PID {p} /T /F >nul 2>&1")
            else:
                os.kill(p, 9)
print("ok")
