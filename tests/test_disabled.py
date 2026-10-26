"""Disabling an account: no sign-in, live sessions end, its tokens stop. Run: python tests/test_disabled.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _support import new_store  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.auth import AuthError  # noqa: E402

store = new_store()
app = App(store)
app.auth.bootstrap("admin-password-1")
admin = app.auth.identity(app.login({"username": "admin", "password": "admin-password-1"}))

call = lambda ident, m, p, b=None: app.handle(m, p, {}, b, ident)
assert call(admin, "PUT", "/api/users/patrick", {"roles": ["superadmin"], "password": "patrick-password-1"})[0] == 200
pt = app.login({"username": "patrick", "password": "patrick-password-1"})
pident = app.auth.identity(pt)
token = app.handle("POST", "/api/tokens", {}, {"name": "plant", "scope": "read"}, pident)[1]["token"]
assert app.auth.token_identity(token)["name"] == "patrick"

# disabled: the session and the token both stop, and signing in fails with the same message as a bad password
assert call(admin, "PUT", "/api/users/patrick", {"roles": ["superadmin"], "disabled": True})[0] == 200
assert app.auth.identity(pt)["name"] is None
assert app.auth.token_identity(token) is None
try:
    app.auth.login("patrick", "patrick-password-1")
    raise AssertionError("a disabled account signed in")
except AuthError as ex:
    assert "bad username, password or code" == str(ex)

# it still cannot be disabled twice-over into a problem, and enabling brings it back
assert store.user("patrick")["disabled"] == 1
assert call(admin, "PUT", "/api/users/patrick", {"roles": ["superadmin"], "disabled": False})[0] == 200
assert app.auth.login("patrick", "patrick-password-1")
assert store.user("patrick")["disabled"] == 0

# an administrator cannot lock themselves out
try:
    call(admin, "PUT", "/api/users/admin", {"roles": ["superadmin"], "disabled": True})
    raise AssertionError("disabled yourself")
except ApiError as ex:
    assert ex.status == 409

# the audit trail records it, with who did it
actions = [a["action"] for a in store.q("select action from audit where user='admin' order by id")]
assert "user.disabled" in actions and "user.enabled" in actions

print("ok")
