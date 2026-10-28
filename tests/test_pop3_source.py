"""POP3 source: numbers parsed out of received e-mail, against an in-process fake POP3 server. Run: python tests/test_pop3_source.py"""
import json
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.pop3_source import Pop3Source  # noqa: E402
from _support import new_store  # noqa: E402

MAILS = {}      # uid -> raw message bytes, in arrival order
SIZES = {}      # uid -> size the fake LISTs (a lying server)
DELETED = []
LOGINS = []


def serve(srv):
    while True:
        try:
            c, _ = srv.accept()
        except OSError:
            return
        try:
            session(c)
        except OSError:  # the client hung up on purpose (a message over max_bytes)
            pass
        c.close()


def session(c):
    if True:
        f = c.makefile("rwb")
        w = lambda s: (f.write(s.encode() + b"\r\n"), f.flush())
        w("+OK fake ready")
        user = None
        for raw in f:
            cmd, _, arg = raw.decode().strip().partition(" ")
            cmd = cmd.upper()
            ids = list(MAILS)
            if cmd == "USER":
                user = arg; w("+OK")
            elif cmd == "PASS":
                LOGINS.append((user, arg)); w("+OK" if arg == "s3cret" else "-ERR bad login")
            elif cmd == "UIDL":
                w("+OK"); [w(f"{i + 1} {u}") for i, u in enumerate(ids)]; w(".")
            elif cmd == "LIST":
                w("+OK"); [w(f"{i + 1} {SIZES.get(u, len(MAILS[u]))}") for i, u in enumerate(ids)]; w(".")
            elif cmd == "RETR":
                w("+OK"); f.write(MAILS[ids[int(arg) - 1]] + b"\r\n.\r\n"); f.flush()
            elif cmd == "DELE":
                DELETED.append(ids[int(arg) - 1]); w("+OK")
            elif cmd == "QUIT":
                w("+OK bye"); break
            else:
                w("-ERR what")


srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(5)
PORT = srv.getsockname()[1]
threading.Thread(target=serve, args=(srv,), daemon=True).start()


def mail(uid, subject, body=""):
    MAILS[uid] = f"From: a@b.c\r\nSubject: {subject}\r\n\r\n{body}\r\n".encode()


tmp = Path(tempfile.mkdtemp())
creds = tmp / "c.json"; creds.write_text(json.dumps({"user": "bob", "password": "s3cret"}))
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
cfg = {"type": "pop3", "host": "127.0.0.1", "port": PORT, "security": "none", "creds": str(creds), "period": 60}

# ---- config
assert put("source", "m", cfg)[0] == 200
assert "host" in props(put("source", "x", {**cfg, "host": ""}))
assert "security" in props(put("source", "x", {**cfg, "security": "tls13"}))
assert "creds" in props(put("source", "x", {**cfg, "creds": str(tmp / "nope.json")}))
assert "delete" in props(put("source", "x", {**cfg, "delete": "yes"}))
assert put("point", "t", {"source": "m", "pattern": r"Temp=(-?\d+(?:\.\d+)?)", "where": "subject"})[0] == 200
assert put("point", "p", {"source": "m", "pattern": r"pressure: (\d+)", "where": "body"})[0] == 200
assert "pattern" in props(put("point", "z", {"source": "m", "where": "body"})), "pattern is required"
assert "pattern" in props(put("point", "z", {"source": "m", "pattern": "(unclosed"}))
assert "pattern" in props(put("point", "z", {"source": "m", "pattern": r"\d+"})), "needs exactly one capture group"
assert "pattern" in props(put("point", "z", {"source": "m", "pattern": "(" + "a" * 200 + ")"})), "too long"
for evil in (r"(a+)+(\d)", r"((\d)*)+", r"(\d+\s*)*x(\d)", r"(.*)*(\d)"):
    assert "pattern" in props(put("point", "z", {"source": "m", "pattern": evil})), evil
assert "where" in props(put("point", "z", {"source": "m", "pattern": "(1)", "where": "header"}))
# a regex timeout is impossible in stdlib, so the accepted subset itself must be safe
for evil in (r"(\d|\d)+x(\d)", r"(\d+)\d*\d*\d*\d*x", r"(.*).*.*.*.*X", r"(a|aa)+$(\d)", r"(\d)\1", r"(?=a)(\d)", r"(?<!a)(\d)", r"(\d+)\s*\d*\s*"):
    assert "pattern" in props(put("point", "z", {"source": "m", "pattern": evil})), evil
import durian.pop3_source as ps  # noqa: E402
hostile = ["1" * 20000, " " * 20000, "1 " * 10000, "Temp:" + " " * 20000, "-" + "1." * 10000]
for ok in (r"Temp:\s?([-+]?\d+(?:\.\d+)?)", r"(\d+)\s*kW", r"([-+]?\d+\.?\d*)", r"(\w+)=",r"Temp=(-?\d+(?:\.\d+)?)"):
    assert put("point", "z", {"source": "m", "pattern": ok})[0] == 200, ok
    for h in hostile:
        t0 = time.perf_counter(); re.search(ok, h[:ps.MAX_TEXT]); dt = time.perf_counter() - t0
        assert dt < 0.1, (ok, len(h), dt)
call("DELETE", "/api/config/point/z")
big = Pop3Source._texts(b"Subject: " + b"1" * 30000 + b"\r\n\r\n" + b"2" * 30000)
assert len(big["subject"]) <= 2000 and len(big["body"]) <= 2000, "the matcher is only handed a short prefix"
# security: ssl unless the host is this machine
assert "security" in props(put("source", "x", {**cfg, "host": "mail.example.com", "security": "none"}))
assert "security" not in props(put("source", "x", {k: v for k, v in cfg.items() if k != "security"} | {"host": "mail.example.com"})), "default is ssl"
assert "security" not in props(put("source", "x", {**cfg, "host": "mail.example.com", "security": "starttls"}))
call("DELETE", "/api/config/source/x")

# ---- reading: newest matching message wins; non-finite and non-matching mail ignored
mail("u1", "Temp=10.5"); mail("u2", "hello"); mail("u3", "Temp=21.5", "pressure: 7"); mail("u4", "Temp=nan")
src = next(s for s in app.engine.sources if s.name == "m")
time.sleep(0.3); src.cycle(); app.tick(time.time() + 1)
assert src.connected, src.error
assert last()["t"] == 21.5 and last()["p"] == 7.0, last()
assert LOGINS[-1] == ("bob", "s3cret") and not DELETED, (LOGINS, DELETED)

# ---- no new mail: the last value is kept, nothing is deleted
src.next_try = 0; src.cycle(); app.tick(time.time() + 2)
assert last()["t"] == 21.5 and src.connected and not DELETED, last()

# ---- new mail replaces it
mail("u5", "Temp=30"); src.cycle(); app.tick(time.time() + 3)
assert last()["t"] == 30.0, last()

# ---- message cap per cycle and size cap
import durian.pop3_source as ps  # noqa: E402
assert ps.MAX_MESSAGES <= 100
mail("u6", "Temp=99", "x" * 200000)  # over max_bytes: skipped, not read
src.cycle(); app.tick(time.time() + 4)
assert last()["t"] == 30.0, last()

# ---- a server that lies in LIST: bytes are counted as they arrive and the message is dropped, not buffered
mail("u8", "Temp=1", ("y" * 70 + "\n") * 4000); SIZES["u8"] = 50
src.next_try = 0; src.cycle()
assert src.connected is False and "max_bytes" in src.error, src.error
del MAILS["u8"]
src.next_try = 0; src.cycle()
assert src.connected, ("the oversized message is marked seen, so it does not wedge the source", src.error)

# ---- a RETR that times out does not lose the message: it is fetched again next cycle
mail("u7", "Temp=77")
real_connect = src._connect


class Flaky:
    def __init__(self, c): self.c = c
    def retr(self, n): raise TimeoutError("timed out")
    def __getattr__(self, a): return getattr(self.c, a)


src._connect = lambda: Flaky(real_connect())
src.next_try = 0; src.cycle()
assert src.connected is False, "the timeout should mark the source down"
src._connect = real_connect
src.next_try = 0; src.cycle(); app.tick(time.time() + 5)
assert last()["t"] == 77.0, last()

# ---- delete only when asked
assert put("source", "m", {**cfg, "delete": True})[0] == 200
src = next(s for s in app.engine.sources if s.name == "m")
src.cycle()
assert "u5" in DELETED, DELETED

# ---- wrong password and a dead server: source down, in words
creds.write_text(json.dumps({"user": "bob", "password": "wrong"}))
src.next_try = 0; src.cycle()
assert src.connected is False and src.error and "Error" not in src.error and "error_proto" not in src.error, src.error
srv.close()
dead = socket.socket(); dead.bind(("127.0.0.1", 0)); dead_port = dead.getsockname()[1]; dead.close()
assert put("source", "m", {**cfg, "port": dead_port})[0] == 200
src = next(s for s in app.engine.sources if s.name == "m")
src.cycle()
assert src.connected is False and src.error and "Error" not in src.error, src.error
print("ok")
