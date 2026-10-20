"""Password reset by admin-issued code. Run: python tests/test_reset.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import AuthError  # noqa: E402
from _support import new_store  # noqa: E402

app = App(new_store())
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, who=admin):
    try:
        return app.handle(method, path, {}, body, who)
    except ApiError as e:
        return e.status, {"error": str(e)}


assert call("PUT", "/api/users/bob", {"roles": ["user"], "password": "bob-password-123"})[0] == 200
bob_tok = app.auth.login("bob", "bob-password-123")
bob = app.auth.identity(bob_tok)
assert call("POST", "/api/users/bob/reset", who=bob)[0] == 403 and call("POST", "/api/users/nobody/reset")[0] == 404
code = call("POST", "/api/users/bob/reset")[1]["code"]


def reset(**k):
    try:
        app.reset_password({"username": "bob", "code": code, "new": "brand-new-pass-1", **k})
        return 200
    except ApiError as e:
        return e.status


assert reset(code="rst_wrong") == 400 and reset(new="short") == 400   # a weak password does not spend the code
assert reset() == 200 and reset() == 400                              # works once
assert app.auth.identity(bob_tok)["name"] is None                      # old sessions are gone
assert app.auth.login("bob", "brand-new-pass-1")
try:
    app.auth.login("bob", "bob-password-123")
    raise SystemExit("old password still works")
except AuthError:
    pass

# five wrong codes lock guessing, even for the right code; an expired code fails
code = call("POST", "/api/users/bob/reset")[1]["code"]
for _ in range(5):
    reset(code="rst_nope")
assert reset() == 429
app.auth.failures.clear()
app.auth.resets["bob"] = (app.auth.resets["bob"][0], time.time() - 1)
assert reset() == 400
print("ok")
