"""Query watch lists: a saved query over folder, tags, source and a name substring resolves at read time.
Run: python tests/test_watchlists_query.py"""
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
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
store.add_user("alice", hash_password("alice-password-1"), ["user"])
alice = app.auth.identity(app.auth.login("alice", "alice-password-1"))
who = {"admin": admin, "alice": alice}


def call(u, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, who[u])
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


def names(u, wl):
    status, pts = call(u, "GET", "/api/points", watchlist=wl)
    assert status == 200, (wl, status, pts)
    return sorted(p["name"] for p in pts)


def point(name, body):
    status, payload = call("admin", "PUT", "/api/config/point/" + name, body)
    assert status == 200, (name, status, payload)


# The live points a query can select: folders (with a subtree), tags and two sources.
assert call("admin", "PUT", "/api/config/source/sim", {"type": "virtual", "enabled": True})[0] == 200
point("plant-boiler-temp", {"source": "demo", "folder": "plant/boiler", "tags": {"area": "plant"}})
point("plant-boiler-feed", {"source": "demo", "folder": "plant/boiler/feed", "tags": {"area": "plant"}})
point("plant-chiller", {"source": "demo", "folder": "plant/chiller", "tags": {"area": "plant"}})
point("plant-wide", {"source": "demo", "folder": "plant", "tags": {"area": "plant"}})
point("sim-flow", {"source": "sim", "folder": "sim/line", "tags": {"area": "plant"}})
point("manual-orphan", {"source": "demo", "folder": "misc", "tags": {"area": "misc"}})

# ---- 1. static point lists keep working unchanged
assert call("alice", "PUT", "/api/mylists/static-boiler", {"points": ["boiler-temp"]})[0] == 200
assert names("alice", "static-boiler") == ["boiler-temp"]

# ---- 2. a watch list body may carry `query` instead of points; every documented shape is accepted
for name, q in (("q-folder", {"folder": "plant/boiler"}),
                ("q-tags", {"tags": ["area:plant"]}),
                ("q-source", {"source": "sim"}),
                ("q-name", {"tags": ["area:plant"], "name_like": "boiler"})):
    status, payload = call("alice", "PUT", "/api/mylists/" + name, {"query": q})
    assert status == 200, (name, status, payload)

# ---- 3. junk queries are refused, and the per-property error names the query
for bad in ({"query": {}}, {"query": {"bogus": 1}}, {"query": {"tags": "area:plant"}}, {"query": []}):
    status, payload = call("alice", "PUT", "/api/mylists/junk", bad)
    assert status == 422, (bad, status, payload)
    assert any(e["property"].startswith("query") for e in payload["errors"]), (bad, payload)
assert "junk" not in {w["name"] for w in call("alice", "GET", "/api/watchlists")[1]}

# ---- 4. a query selects from the live point fields: folder and everything below it, tag pairs, source,
# and a name substring; it does not need a folder tag or any membership table of its own
assert names("alice", "q-folder") == ["plant-boiler-feed", "plant-boiler-temp"]
assert names("alice", "q-tags") == ["plant-boiler-feed", "plant-boiler-temp", "plant-chiller", "plant-wide", "sim-flow"]
assert names("alice", "q-source") == ["sim-flow"]
assert names("alice", "q-name") == ["plant-boiler-feed", "plant-boiler-temp"]

# a body may carry both: the listed points and the query's matches are unioned
assert call("alice", "PUT", "/api/mylists/q-union", {"query": {"tags": ["area:plant"]},
                                                     "points": ["manual-orphan"]})[0] == 200
assert names("alice", "q-union") == ["manual-orphan", "plant-boiler-feed", "plant-boiler-temp",
                                     "plant-chiller", "plant-wide", "sim-flow"]

# ---- 5. resolving reads the current config: a point created later appears and a folder move drops it,
# while the list itself stays exactly as it was saved
stored = next(w for w in call("alice", "GET", "/api/watchlists")[1] if w["name"] == "q-folder")
assert stored["query"] == {"folder": "plant/boiler"}, stored
point("plant-boiler-new", {"source": "demo", "folder": "plant/boiler", "tags": {"area": "plant"}})
assert names("alice", "q-folder") == ["plant-boiler-feed", "plant-boiler-new", "plant-boiler-temp"]
point("plant-boiler-temp", {"source": "demo", "folder": "plant/chiller", "tags": {"area": "plant"}})
assert names("alice", "q-folder") == ["plant-boiler-feed", "plant-boiler-new"]
stored = next(w for w in call("alice", "GET", "/api/watchlists")[1] if w["name"] == "q-folder")
assert stored["query"] == {"folder": "plant/boiler"}, stored

# ---- 6. resolution starts from the points the caller may read, exactly like a static list does
point("secret-plant", {"source": "demo", "folder": "secret", "tags": {"area": "secret"},
                       "read_roles": ["superadmin"]})
assert call("admin", "PUT", "/api/config/watchlist/secret-static",
            {"points": ["secret-plant"], "read_roles": ["user"]})[0] == 200
assert call("admin", "PUT", "/api/config/watchlist/secret-query",
            {"query": {"tags": ["area:secret"]}, "read_roles": ["user"]})[0] == 200
assert names("alice", "secret-static") == []   # the existing static path already filters by the point's read roles
assert names("alice", "secret-query") == []
assert names("admin", "secret-query") == ["secret-plant"]

print("ok")
