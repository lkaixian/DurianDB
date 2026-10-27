"""Email recipient groups and subject/body templates. Run: python tests/test_email_groups.py"""
import email
import socketserver
import sys
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
PORT = smtp.server_address[1]


def call(method, path, body=None):
    try:
        return app.handle(method, path, {}, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


def wait(pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end and not pred():
        time.sleep(0.05)
    return pred()


MAIL_H = {"type": "email", "from": "durian@example.com", "smtp": {"host": "127.0.0.1", "port": PORT},
          "min_level": "WARNING"}

# ---- 1. recipient groups are ordinary config objects: create, list, update, delete through the generic API
assert call("PUT", "/api/config/emailgroup/ops", {"addresses": ["ops1@example.com", "lead@example.com"]})[0] == 200
assert call("PUT", "/api/config/emailgroup/night", {"addresses": ["night@example.com", "lead@example.com"]})[0] == 200
assert [g["name"] for g in call("GET", "/api/config/emailgroup")[1]] == ["night", "ops"]
assert call("PUT", "/api/config/emailgroup/tmp", {"addresses": ["tmp@example.com"]})[0] == 200
assert call("DELETE", "/api/config/emailgroup/tmp")[0] == 200
assert call("GET", "/api/config/emailgroup/tmp")[0] == 404

# ---- 2. a malformed address in a GROUP is rejected when the group is saved, not when a mail is sent
for bad in ({"addresses": ["fine@example.com", "not-an-address"]}, {"addresses": []}, {"addresses": "a@example.com"}):
    status, payload = call("PUT", "/api/config/emailgroup/bad", bad)
    assert status == 422 and any(e["property"] == "addresses" for e in payload["errors"]), (bad, status, payload)
assert call("GET", "/api/config/emailgroup/bad")[0] == 404  # nothing was stored

# ---- 3. an unknown group name is one validation error that names the group
status, payload = call("PUT", "/api/config/handler/unknown", {**MAIL_H, "to": ["lead@example.com"], "groups": ["ghost"]})
assert status == 422, (status, payload)
errs = [e for e in payload["errors"] if e["property"] == "groups"]
assert len(errs) == 1 and "ghost" in errs[0]["message"], payload

# ---- 4. a handler reaches its group's addresses: `to` union the groups, de-duplicated, order kept
assert call("PUT", "/api/config/point/hot", {"source": "demo", "generator": {"kind": "constant", "value": 95},
                                             "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"}]})[0] == 200
H = {**MAIL_H, "to": ["lead@example.com", "ops1@example.com"], "groups": ["ops", "night"], "scope": {"points": ["hot"]}}
assert call("PUT", "/api/config/handler/mail", H)[0] == 200
MAIL.clear()
app.tick(time.time())
assert wait(lambda: MAIL), "no mail was sent to the handler's recipients"
m = MAIL[0]
assert m["to"] == ["lead@example.com", "ops1@example.com", "night@example.com"], m["to"]
assert m["from"].endswith("<durian@example.com>"), m["from"]

# ---- 5. subject and body templates substitute every documented event placeholder from a real raised alarm
T = {**MAIL_H, "to": ["t@example.com"], "groups": ["ops"], "scope": {"points": ["hot2"]},
     "subject": "{level} {point} {bogus} {value} {source}",
     "body": "P {point}\nV {value}\nL {level}\nT {time}\nM {message}\nS {source}\nU {bogus}\nHTML <b>{point}</b>\n"}
assert call("PUT", "/api/config/point/hot2", {"source": "demo", "generator": {"kind": "constant", "value": 95},
                                              "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"}]})[0] == 200
assert call("PUT", "/api/config/handler/tpl", T)[0] == 200
MAIL.clear()
ts = time.time()
app.tick(ts)
assert wait(lambda: any("t@example.com" in x["to"] for x in MAIL)), MAIL
t = next(x for x in MAIL if "t@example.com" in x["to"])
parsed = email.message_from_string(t["body"])
assert parsed["Subject"] == "URGENT hot2 {bogus} 95 demo", parsed["Subject"]
text = parsed.get_payload()
for line in (f"P hot2", "V 95", "L URGENT", f"T {ts:.0f}", "M 95 > 90", "S demo", "U {bogus}",
             "HTML <b>hot2</b>"):
    assert line in text, (line, text)

# ---- 6. template strings are capped and a subject cannot smuggle a new header
status, payload = call("PUT", "/api/config/handler/cap", {**MAIL_H, "to": ["a@example.com"], "subject": "x" * 201})
assert status == 422 and any(e["property"] == "subject" for e in payload["errors"]), payload
status, payload = call("PUT", "/api/config/handler/cap", {**MAIL_H, "to": ["a@example.com"], "body": "x" * 2001})
assert status == 422 and any(e["property"] == "body" for e in payload["errors"]), payload
status, payload = call("PUT", "/api/config/handler/cap", {**MAIL_H, "to": ["a@example.com"], "subject": "two\nlines"})
assert status == 422 and any(e["property"] == "subject" for e in payload["errors"]), payload

# ---- 7. a group a handler still names cannot be deleted, so a recipient list cannot shrink silently
status, payload = call("DELETE", "/api/config/emailgroup/ops")
assert status == 409, (status, payload)
assert call("GET", "/api/config/emailgroup/ops")[0] == 200

# ---- 8. config export carries groups and import puts them back before the handlers that name them
doc = app.maint.export_config()
assert "emailgroup" in doc["objects"], sorted(doc["objects"])
assert sorted(g["name"] for g in doc["objects"]["emailgroup"]) == ["night", "ops"]
for h in ("mail", "tpl"):
    assert call("DELETE", f"/api/config/handler/{h}")[0] == 200
for g in ("night", "ops"):
    assert call("DELETE", f"/api/config/emailgroup/{g}")[0] == 200
assert call("GET", "/api/config/emailgroup/ops")[0] == 404
status, payload = app.import_config({"apply": "1"}, doc, admin)
assert status == 200, payload
assert payload["summary"]["emailgroup"] == {"created": 2, "updated": 0, "unchanged": 0, "deleted": 0}, payload["summary"]
assert [g["name"] for g in call("GET", "/api/config/emailgroup")[1]] == ["night", "ops"]
restored = call("GET", "/api/config/handler/mail")[1]
assert restored["groups"] == ["ops", "night"] and restored["to"] == ["lead@example.com", "ops1@example.com"], restored

# ---- 9. a CR/LF in a state label must not reach the Subject header and must not kill the tick's other alarms
assert call("PUT", "/api/config/point/aaa-foam", {"source": "demo", "generator": {"kind": "constant", "value": 1},
                                                   "states": {"1": "FOAM\r\n"},
                                                   "detectors": [{"kind": "STATE", "states": [1], "level": "URGENT"}]})[0] == 200
assert call("PUT", "/api/config/point/zzz-after", {"source": "demo", "generator": {"kind": "constant", "value": 95},
                                                    "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"}]})[0] == 200
assert call("PUT", "/api/config/handler/aaa-poison", {**MAIL_H, "to": ["bad@example.com"], "scope": {"points": ["aaa-foam"]},
                                                       "subject": "A {value} B"})[0] == 200
assert call("PUT", "/api/config/handler/zzz-good", {**MAIL_H, "to": ["good@example.com"],
                                                     "scope": {"points": ["aaa-foam", "zzz-after"]}})[0] == 200
MAIL.clear()
ts = time.time()
try:
    app.tick(ts)
except Exception as ex:  # the whole tick must survive a header it cannot write
    raise AssertionError(f"on_event raised: {type(ex).__name__}: {ex}")
assert wait(lambda: len([m for m in MAIL if "good@example.com" in m["to"]]) >= 2), [m["to"] for m in MAIL]
bad = next(m for m in MAIL if "bad@example.com" in m["to"])
bs = email.message_from_string(bad["body"])["Subject"]
assert "\r" not in bs and "\n" not in bs and bs == "A FOAM B", repr(bs)
good = [email.message_from_string(m["body"]) for m in MAIL if "good@example.com" in m["to"]]
assert len([p for p in good if "zzz-after" in p["Subject"]]) == 1, [p["Subject"] for p in good]  # the second event survived
assert all("\r" not in p["Subject"] and "\n" not in p["Subject"] for p in good), [p["Subject"] for p in good]
foam = [p for p in good if "aaa-foam" in p["Subject"]]
assert foam and foam[0]["Subject"] == "[URGENT] aaa-foam: state is FOAM", [p["Subject"] for p in good]

# ---- 10. a replace import that would delete a group a retained handler names is refused, and nothing is deleted
doc = app.maint.export_config()
doc["objects"]["emailgroup"] = []  # replace would drop night and ops, but mail/tpl still name them
try:
    app.import_config({"apply": "1", "mode": "replace"}, doc, admin)
    raise AssertionError("a replace import deleted a group a retained handler still names")
except ApiError as ex:
    assert ex.status == 422, ex.status
    assert any("ops" in e["message"] for e in ex.errors), ex.errors
assert call("GET", "/api/config/emailgroup/ops")[0] == 200
assert call("GET", "/api/config/handler/mail")[1]["groups"] == ["ops", "night"]

# ---- 11. mixed-case twins are one recipient, and the first spelling is kept
assert call("PUT", "/api/config/emailgroup/twin", {"addresses": ["ops@example.com"]})[0] == 200
assert call("PUT", "/api/config/point/case-point", {"source": "demo", "generator": {"kind": "constant", "value": 95},
                                                     "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"}]})[0] == 200
assert call("PUT", "/api/config/handler/twin", {**MAIL_H, "to": ["Ops@Example.com"], "groups": ["twin"],
                                                "scope": {"points": ["case-point"]}})[0] == 200
MAIL.clear()
app.tick(time.time())
assert wait(lambda: [m for m in MAIL if any(a.casefold() == "ops@example.com" for a in m["to"])])
twins = [m for m in MAIL if any(a.casefold() == "ops@example.com" for a in m["to"])]
assert len(twins) == 1, [m["to"] for m in twins]
assert twins[0]["to"] == ["Ops@Example.com"], twins[0]["to"]

print("ok")
