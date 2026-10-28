"""Both import seams refuse a key the server does not read, by name, and a config exported from a live server
still re-imports (C20). Run: python tests/test_import_keys.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
app = App(store)
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


# a typo'd point in a config import is refused by name and never stored
doc = {"format": "durian-config", "version": 1, "objects": {"point": [{"name": "typo-imp", "source": "demo", "max_stepp": 5}]}}
for q in ({}, {"apply": "1"}):
    s, r = call("POST", "/api/import", doc, **q)
    assert s == 422 and any("max_stepp" in e["property"] for e in r["errors"]), (q, s, r)
assert store.get("point", "typo-imp") is None

# the same through the CSV seam (a column the point form does not have)
s, r = call("POST", "/api/import/points", 'name,source,extra\ntypo-csv,demo,"{""max_stepp"": 5}"\n', apply="1")
assert s == 422 and any(e["property"] == "max_stepp" for e in r["errors"]), (s, r)
assert store.get("point", "typo-csv") is None

# a config exported from this server re-imports, on every kind it holds
call("PUT", "/api/config/emailgroup/ops", {"addresses": ["a@example.com"]})
call("PUT", "/api/config/watchlist/wl", {"tags": {"area": "boiler"}})
exported = app.maint.export_config()
assert sum(len(v) for v in exported["objects"].values()) > 5
s, r = call("POST", "/api/import", exported, apply="1")
assert s == 200, (s, r)
print("ok")
