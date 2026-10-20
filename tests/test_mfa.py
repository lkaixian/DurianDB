"""Two-factor sign-in (TOTP): RFC 6238 vector, setup, enable, login, one-time codes, disable, admin reset. Run: python tests/test_mfa.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import AuthError, totp  # noqa: E402
from _support import new_store  # noqa: E402

import base64  # noqa: E402
assert totp(base64.b32encode(b"12345678901234567890").decode(), 59)[0] == "287082"  # RFC 6238 appendix B, truncated to 6 digits

app = App(new_store())
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, who=admin):
    try:
        return app.handle(method, path, {}, body, who)
    except ApiError as e:
        return e.status, {"error": str(e)}


assert call("GET", "/api/me")[1]["mfa"] is False
secret = call("POST", "/api/mfa/setup")[1]["secret"]
assert call("POST", "/api/mfa/enable", {"code": "000000"})[0] == 400
now = time.time()
assert call("POST", "/api/mfa/enable", {"code": totp(secret, now)[0]})[0] == 200 and call("GET", "/api/me")[1]["mfa"] is True
assert call("POST", "/api/mfa/setup")[0] == 409

# password alone is no longer enough, and asking for the code is not a failed attempt
try:
    app.auth.login("admin", "admin-password-123")
    raise SystemExit("login without a code worked")
except AuthError as e:
    assert "code required" in str(e) and "admin" not in app.auth.failures
try:
    app.auth.login("admin", "admin-password-123", "000000")
    raise SystemExit("wrong code worked")
except AuthError:
    assert app.auth.failures["admin"][0] == 1
code = totp(secret, time.time() + 30)[0]  # the next step is within the accepted drift and not yet used
assert app.auth.login("admin", "admin-password-123", code)
try:
    app.auth.login("admin", "admin-password-123", code)
    raise SystemExit("a code was accepted twice")
except AuthError:
    pass

# disable needs the password; a lost phone is cleared by an admin
assert call("POST", "/api/mfa/disable", {"password": "nope"})[0] == 403
assert call("POST", "/api/mfa/disable", {"password": "admin-password-123"})[0] == 200 and call("GET", "/api/me")[1]["mfa"] is False
assert call("PUT", "/api/users/bob", {"roles": ["user"], "password": "bob-password-123"})[0] == 200
app.store.run("update users set totp=? where name='bob'", (secret,))
assert [u for u in call("GET", "/api/users")[1] if u["name"] == "bob"][0]["mfa"] is True
assert call("PUT", "/api/users/bob", {"roles": ["user"], "reset_mfa": True})[0] == 200 and app.store.user("bob")["totp"] is None
print("ok")
