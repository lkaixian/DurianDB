"""Auth checks. Run: python tests/test_auth.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.auth import Auth, AuthError, can, held, hash_password, verify_password  # noqa: E402
from durian.store import Store  # noqa: E402
from _support import new_store  # noqa: E402


def fails(fn, status):
    try:
        fn()
    except AuthError as e:
        assert e.status == status, (e.status, status)
        return
    raise AssertionError("expected AuthError")


h = hash_password("correct horse battery")
assert verify_password("correct horse battery", h) and not verify_password("wrong", h) and not verify_password("x", "garbage")
assert h != hash_password("correct horse battery")  # salted

assert held(["superadmin"]) == {"superadmin", "user", "anonymous"} and held(["ops"]) == {"ops", "anonymous"}  # public means everyone
assert can({"anonymous"}, ["anonymous"]) and not can({"anonymous"}, []) and can({"superadmin"}, [])
assert can(held(["user"]), ["anonymous"]) and not can(held(["user"]), ["ops"]) and can(held(["ops"]), ["ops", "x"])

st = new_store()
a = Auth(st)
pw = a.bootstrap()
assert pw and len(pw) >= 10 and a.bootstrap() is None  # only the first call creates the admin
tok = a.login("admin", pw)
assert a.identity(tok) == {"name": "admin", "roles": {"superadmin", "user", "anonymous"}}
a.logout(tok)
assert a.identity(tok)["name"] is None and a.identity("nonsense")["roles"] == {"anonymous"}

st.add_user("viewer", hash_password("viewer-password-1"), ["user"])
assert a.identity(a.login("viewer", "viewer-password-1"))["name"] == "viewer"
fails(lambda: a.login("nobody", "whatever-whatever"), 401)

for _ in range(4):  # 5th bad attempt locks the account, even for the right password
    fails(lambda: a.login("viewer", "bad"), 401)
fails(lambda: a.login("viewer", "bad"), 401)
fails(lambda: a.login("viewer", "viewer-password-1"), 429)

a2 = Auth(new_store())
assert a2.bootstrap("a-chosen-password-1") is None and a2.login("admin", "a-chosen-password-1")
fails(lambda: Auth(new_store()).bootstrap("short"), 400)
print("ok")
