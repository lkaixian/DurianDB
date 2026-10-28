"""LDAP simple-bind sign-in against an in-process fake directory on 127.0.0.1. Run: python tests/test_ldap_auth.py"""
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import AuthError, hash_password  # noqa: E402
from _support import new_store  # noqa: E402

GOOD_DN, GOOD_PW = "uid=alice,ou=people,dc=example,dc=com", "alice-secret-1"


def tlv(buf, pos):  # test-side BER reader: (tag, start, end)
    first = buf[pos + 1]
    if first < 0x80:
        return buf[pos], pos + 2, pos + 2 + first
    k = first & 0x7F
    n = int.from_bytes(buf[pos + 2:pos + 2 + k], "big")
    return buf[pos], pos + 2 + k, pos + 2 + k + n


def L(n):
    return bytes([n]) if n < 128 else b"\x81" + bytes([n])


class Fake:
    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port, self.seen = self.srv.getsockname()[1], []  # seen: (dn, password) per bind
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            c, _ = self.srv.accept()
            data = c.recv(4096)
            _, s, _ = tlv(data, 0)
            _, ms, me = tlv(data, s)  # messageID
            _, bs, _ = tlv(data, me)  # BindRequest [APPLICATION 0]
            _, vs, ve = tlv(data, bs)  # version
            _, ns, ne = tlv(data, ve)  # name
            _, ps, pe = tlv(data, ne)  # [0] simple password
            dn, pw = data[ns:ne].decode(), data[ps:pe].decode()
            self.seen.append((dn, pw))
            code = 0 if (dn, pw) == (GOOD_DN, GOOD_PW) else 51 if pw == "busy-pw" else 49
            op = b"\x0a\x01" + bytes([code]) + b"\x04\x00\x04\x00"
            body = b"\x02\x01\x01" + b"\x61" + L(len(op)) + op
            c.sendall(b"\x30" + L(len(body)) + body)
            c.close()


def fails(fn, status):
    try:
        fn()
    except ApiError as e:
        assert e.status == status, (e.status, status, str(e))
        return e
    raise AssertionError("expected an ApiError")


fake = Fake()
store = new_store()
app = App(store)
auth = app.auth
auth.bootstrap("admin-password-123")
store.add_user("viewer", hash_password("viewer-password-1"), ["user"])
auth._dummy = "not-a-hash"
admin = {"name": "admin", "roles": {"superadmin", "user", "anonymous"}}
cfg = {"url": f"ldap://127.0.0.1:{fake.port}", "bind_template": "uid={user},ou=people,dc=example,dc=com", "default_roles": ["user"]}

# ---- saving: validated like sso; plain ldap:// only on localhost; roles must exist
bad = fails(lambda: app.handle("PUT", "/api/auth/ldap", {}, {**cfg, "url": "ldap://dir.example.com"}, admin), 422)
assert any(e["property"] == "url" for e in bad.errors), bad.errors
for patch in ({"bind_template": "uid=nobody"}, {"default_roles": ["nosuchrole"]}, {"default_roles": []}, {"extra": 1}):
    fails(lambda p=patch: app.handle("PUT", "/api/auth/ldap", {}, {**cfg, **p}, admin), 422)
assert app.handle("PUT", "/api/auth/ldap", {}, {**cfg, "url": "ldaps://dir.example.com"}, admin)[0] == 200
assert app.handle("PUT", "/api/auth/ldap", {}, cfg, admin)[0] == 200

# ---- not a local account: falls through to the bind, auto-creates the account with default_roles
tok = auth.login("alice", GOOD_PW)
assert auth.identity(tok)["name"] == "alice" and "user" in auth.identity(tok)["roles"]
assert fake.seen == [(GOOD_DN, GOOD_PW)], fake.seen
assert store.user("alice")["roles"] == ["user"]
assert any(a["action"] == "ldap.login" and a["user"] == "alice" for a in store.audit_log())
auth.login("alice", GOOD_PW)  # second sign-in: the account now exists but still signs in by bind

# ---- an empty password never reaches the network, and counts as a failure
before = len(fake.seen)
for pw in ("", None):
    fails(lambda p=pw: auth.login("bob", p), 401)
    fails(lambda p=pw: auth.login("alice", p), 401)
assert len(fake.seen) == before, "an empty password was sent to the directory"

# ---- DN injection: the name is escaped, so it cannot add RDNs
fails(lambda: auth.login("x,ou=admins", "whatever-pw"), 401)
assert fake.seen[-1][0] == r"uid=x\,ou\=admins,ou=people,dc=example,dc=com", fake.seen[-1]
from durian.ldap import escape_dn  # noqa: E402
assert escape_dn('a,b+c"d\\e<f>g;h=i') == r'a\,b\+c\"d\\e\<f\>g\;h\=i'
assert escape_dn("#x y ") == r"\#x y\ " and escape_dn(" x") == r"\ x" and escape_dn("a\0b") == r"a\00b"

n0 = len(store.audit_log(1000))
for i in range(20):  # made-up names: lockout state yes, audit rows no
    fails(lambda i=i: auth.login(f"ghost{i}", "wrong-password"), 401)
assert len(store.audit_log(1000)) == n0, "made-up names wrote audit rows"
fails(lambda: auth.login("alice", "wrong-password"), 401)  # a known LDAP account still audits
log = store.audit_log(1000)
assert len(log) == n0 + 1 and log[0]["action"] == "ldap.failed" and log[0]["user"] == "alice", log[0]
assert GOOD_PW not in repr(log) and "whatever-pw" not in repr(log)

# ---- a local account never falls through to the directory
before = len(fake.seen)
fails(lambda: auth.login("viewer", GOOD_PW), 401)
fails(lambda: auth.login("viewer", "viewer-password-1x"), 401)
assert len(fake.seen) == before, "a local account was sent to the directory"
assert auth.identity(auth.login("viewer", "viewer-password-1"))["name"] == "viewer"

# ---- failed binds count toward the lockout
for _ in range(5):
    fails(lambda: auth.login("carol", "wrong-password"), 401)
fails(lambda: auth.login("carol", "wrong-password"), 429)
assert store.failure("carol")["attempts"] == 5

# ---- not configured / disabled: no fall-through at all
before = len(fake.seen)
app.handle("PUT", "/api/auth/ldap", {}, {**cfg, "enabled": False}, admin)
fails(lambda: auth.login("dave", GOOD_PW), 401)
app.handle("DELETE", "/api/auth/ldap", {}, None, admin)
fails(lambda: auth.login("dave", GOOD_PW), 401)
assert len(fake.seen) == before

# ---- a busy directory (resultCode 51) is a sentence, not a lockout
app.handle("PUT", "/api/auth/ldap", {}, cfg, admin)
e = fails(lambda: auth.login("frank", "busy-pw"), 502)
assert store.failure("frank") is None and "busy" in str(e).lower(), (store.failure("frank"), str(e))

# ---- a lone surrogate never reaches the network and counts as a wrong password
before = len(fake.seen)
fails(lambda: auth.login("grace", "pw-\ud800-x"), 401)
assert len(fake.seen) == before and store.failure("grace")["attempts"] == 1

# ---- an unreachable directory is a sentence, not a lockout
dead = socket.socket()
dead.bind(("127.0.0.1", 0))
port = dead.getsockname()[1]
dead.close()
app.handle("PUT", "/api/auth/ldap", {}, {**cfg, "url": f"ldap://127.0.0.1:{port}"}, admin)
fails(lambda: auth.login("erin", GOOD_PW), 502)
assert store.failure("erin") is None
e = fails(lambda: auth.login("erin", GOOD_PW), 502)
assert "Error" not in str(e), str(e)

print("ok")
