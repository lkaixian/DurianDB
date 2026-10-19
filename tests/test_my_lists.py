"""User-owned watch lists: private to the owner, usable as a point filter, protected from others. Run: python tests/test_my_lists.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
app = App(store)
app.auth.bootstrap("admin-password-123")
for n in ("alice", "bob"):
    store.add_user(n, hash_password(n + "-password-1"), ["user"])
who = {n: app.auth.identity(app.auth.login(n, n + "-password-1")) for n in ("alice", "bob")}
who["admin"] = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(u, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, who[u])
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


assert call("alice", "PUT", "/api/mylists/my-boiler", {"points": ["boiler-temp"]})[0] == 200
names = lambda u: {w["name"] for w in call(u, "GET", "/api/watchlists")[1]}
assert "my-boiler" in names("alice") and "my-boiler" not in names("bob")
assert [w["mine"] for w in call("alice", "GET", "/api/watchlists")[1] if w["name"] == "my-boiler"] == [True]
s, pts = call("alice", "GET", "/api/points", watchlist="my-boiler")
assert s == 200 and [p["name"] for p in pts] == ["boiler-temp"]
assert call("bob", "GET", "/api/points", watchlist="my-boiler")[0] == 404          # not visible, so as if it did not exist
assert call("bob", "PUT", "/api/mylists/my-boiler", {"points": ["x"]})[0] == 409   # and not overwritable
assert call("bob", "DELETE", "/api/mylists/my-boiler")[0] == 409
assert call("alice", "PUT", "/api/mylists/bad", {"points": "nope"})[0] == 422 and call("alice", "PUT", "/api/mylists/bad", {})[0] == 422
assert call("alice", "DELETE", "/api/mylists/ghost")[0] == 404
assert call("alice", "DELETE", "/api/mylists/my-boiler")[0] == 200 and "my-boiler" not in names("alice")
assert call("alice", "PUT", "/api/mylists/bad name", {"points": ["a"]})[0] == 422
print("ok")
