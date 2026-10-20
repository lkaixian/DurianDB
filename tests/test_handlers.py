"""Email and set-point event handlers, with scope. Run: python tests/test_handlers.py"""
import json
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

# ---- a tiny SMTP server that keeps what it receives (plain text, AUTH PLAIN accepted)
MAIL = []


class Smtp(socketserver.StreamRequestHandler):
    def handle(self):
        w = lambda t: self.wfile.write(t.encode() + b"\r\n")
        w("220 test")
        msg, data = {}, False
        for raw in self.rfile:
            line = raw.decode().rstrip("\r\n")
            if data:
                if line == ".":
                    MAIL.append({**msg, "body": "\n".join(self.lines)})
                    data = False
                    w("250 queued")
                else:
                    self.lines.append(line)
                continue
            cmd = line.upper()
            if cmd.startswith("EHLO") or cmd.startswith("HELO"):
                w("250-test")
                w("250 AUTH PLAIN")
            elif cmd.startswith("AUTH"):
                msg["auth"] = line
                w("235 ok")
            elif cmd.startswith("MAIL FROM"):
                msg["from"] = line
                msg["to"] = []
                w("250 ok")
            elif cmd.startswith("RCPT TO"):
                msg["to"].append(line.split(":", 1)[1].strip("<> "))
                w("250 ok")
            elif cmd == "DATA":
                self.lines, data = [], True
                w("354 go")
            elif cmd == "QUIT":
                w("221 bye")
                return
            else:
                w("250 ok")


smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Smtp)
smtp.daemon_threads = True
threading.Thread(target=smtp.serve_forever, daemon=True).start()

store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
creds = Path(tempfile.mkdtemp()) / "smtp.json"
creds.write_text(json.dumps({"user": "ops", "password": "secret"}))


def call(method, path, body=None):
    try:
        return app.handle(method, path, {}, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


# ---- validation
MAIL_H = {"type": "email", "to": ["a@example.com"], "from": "durian@example.com", "smtp": {"host": "127.0.0.1", "port": smtp.server_address[1]}, "min_level": "WARNING"}
for bad in ({"to": []}, {"to": ["nope"]}, {"from": "x"}, {"smtp": {"host": ""}}, {"smtp": {"host": "h", "security": "tls"}}, {"creds": "missing.json"}, {"scope": []}, {"type": "pager"}):
    assert call("PUT", "/api/config/handler/m", {**MAIL_H, **bad})[0] == 422, bad
SET_H = {"type": "setpoint", "point": "valve", "value": 0, "min_level": "URGENT"}
for bad in ({"point": ""}, {"value": "x"}, {"clear_value": "x"}, {"release_on_clear": 1}):
    assert call("PUT", "/api/config/handler/s", {**SET_H, **bad})[0] == 422, bad

# ---- points: one that alarms (in area boiler), one that is set by a handler, one that alarms elsewhere
HOT = {"source": "demo", "generator": {"kind": "constant", "value": 95}, "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"}]}
assert call("PUT", "/api/config/point/hot", {**HOT, "tags": {"area": "boiler"}})[0] == 200
assert call("PUT", "/api/config/point/hot-line", {**HOT, "tags": {"area": "line"}})[0] == 200
assert call("PUT", "/api/config/point/valve", {"source": "demo", "generator": {"kind": "constant", "value": 100}, "settable": True, "set_roles": ["user"],
                                              "set_min": 0, "set_max": 100, "max_step": 60})[0] == 200
assert call("PUT", "/api/config/handler/mail", {**MAIL_H, "creds": str(creds), "scope": {"tags": {"area": "boiler"}}, "on_clear": True})[0] == 200
assert call("PUT", "/api/config/handler/close-valve", {**SET_H, "scope": {"points": ["hot"]}, "release_on_clear": True})[0] == 200
app.tick(time.time())
for _ in range(50):
    if MAIL and any(a["action"] == "set" for a in store.audit_log(20)):
        break
    time.sleep(0.1)

# ---- email: only the boiler alarm, with the subject, recipients and credentials
assert len(MAIL) == 1, MAIL
m = MAIL[0]
assert m["to"] == ["a@example.com"] and "durian@example.com" in m["from"] and m["auth"].startswith("AUTH PLAIN")
assert "Subject: [URGENT] hot: 95 > 90" in m["body"], m["body"]
assert "hot-line" not in json.dumps(m)

# ---- set point: the write went through the same checks and was audited under the handler's name
valve = next(p for p in call("GET", "/api/points")[1] if p["name"] == "valve")
assert valve["last"] == 100, valve  # 100 -> 0 is 100 away: the step limit (60) refused it, the value did not move
assert any(a["action"] == "set.refused" and "close-valve" in a["user"] and "at most 60" in a["detail"] for a in store.audit_log(30)), store.audit_log(30)
# loosen the step: the handler's write now goes through
assert call("PUT", "/api/config/point/valve", {"source": "demo", "generator": {"kind": "constant", "value": 100}, "settable": True, "set_roles": ["user"], "set_min": 0, "set_max": 100})[0] == 200
app.tick(time.time() + 1)
for _ in range(50):
    if any(a["action"] == "set" and a["user"] == "handler close-valve" for a in store.audit_log(30)):
        break
    time.sleep(0.1)
assert any(a["action"] == "set" and a["user"] == "handler close-valve" for a in store.audit_log(30))
app.tick(time.time() + 2)  # the written value reaches the point on the next poll
assert next(p for p in call("GET", "/api/points")[1] if p["name"] == "valve")["last"] == 0.0

# ---- a scheduled report: validation, sent once a day from its hour, CSV attached
import datetime as dt  # noqa: E402
REP = {**MAIL_H, "type": "report", "hour": 7, "creds": str(creds)}
for bad in ({"hour": 24}, {"hour": "7"}, {"hours": 0}, {"watchlist": ""}):
    assert call("PUT", "/api/config/handler/rep", {**REP, **bad})[0] == 422, bad
assert call("PUT", "/api/config/handler/rep", REP)[0] == 200
MAIL.clear()
day = lambda h: dt.datetime(2030, 1, 5, h).timestamp()
assert app.send_reports(day(6)) == [] and app.send_reports(day(7)) == ["rep"] and app.send_reports(day(9)) == []
assert app.send_reports(day(7) + 86400) == ["rep"]
reports = lambda: [m for m in MAIL if "DurianDB report" in m["body"]]
for _ in range(50):
    if len(reports()) >= 2:
        break
    time.sleep(0.1)
import email  # noqa: E402
csv_part = email.message_from_string(reports()[0]["body"]).get_payload()[1]
assert len(reports()) == 2 and csv_part.get_filename() == "report.csv" and csv_part.get_payload(decode=True).startswith(b"point,unit,description"), MAIL
# ---- slack: a webhook with {text}
assert call("PUT", "/api/config/handler/sl", {"type": "slack", "url": "ftp://x"})[0] == 422
assert call("PUT", "/api/config/handler/sl", {"type": "slack", "url": "http://127.0.0.1:9/x", "min_level": "URGENT"})[0] == 200
sent = []
app.outputs._put = lambda item: sent.append(item)
from durian.core import Event, Level  # noqa: E402
app.outputs.on_event(Event(point="hot", level=Level.URGENT, message="95 > 90", ts=1.0), "raised")
assert [i[2]["text"] for i in sent if i[0] == "webhook" and "text" in i[2]] == ["[URGENT] hot: 95 > 90"], sent
# ---- escalation: once, only while unacknowledged
assert call("PUT", "/api/config/handler/esc", {**MAIL_H, "escalate_after": 0, "escalate_to": ["boss@example.com"]})[0] == 422
assert call("PUT", "/api/config/handler/esc", {**MAIL_H, "escalate_after": 5, "escalate_to": ["boss"]})[0] == 422
assert call("PUT", "/api/config/handler/esc", {**MAIL_H, "scope": {"points": ["hot"]}, "escalate_after": 5, "escalate_to": ["boss@example.com"]})[0] == 200
sent.clear()
app.outputs._put = lambda item: sent.append(item)  # a config save rebuilt the outputs
now = time.time()
app.tick(now)
hot = [e for e in app.open_events if e.point == "hot"]
assert hot, app.open_events
app.escalate(now + 60)
assert not [i for i in sent if i[0] == "mail" and "ESCALATED" in i[2]["Subject"]]
app.escalate(now + 400)
app.escalate(now + 500)
esc = [i for i in sent if i[0] == "mail" and "ESCALATED" in i[2]["Subject"]]
assert len(esc) == 1 and esc[0][2]["To"] == "boss@example.com", sent
print("ok")
