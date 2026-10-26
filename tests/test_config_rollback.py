"""A config the server cannot start must not be left stored, and an import must be all or nothing. Run: python tests/test_config_rollback.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _support import new_store  # noqa: E402
from durian.app import ApiError, App  # noqa: E402

store = new_store()
app = App(store)
admin = {"name": "admin", "roles": {"superadmin"}}

# ---- a save that cannot be started is rolled back, and the previous version stays
app.handle("PUT", "/api/config/source/v", {}, {"type": "virtual", "period": 60, "points": [{"name": "gen", "kind": "sine", "period": 60, "min": 0, "max": 10}]}, admin)
good = app.store.get("source", "v")
assert good is not None

real = App.reload
boom = {"n": 0}


def explode(self):
    """Fail the way a bad config does: the next reload (the rollback) works again."""
    boom["n"] += 1
    if boom["n"] <= 1:
        raise RuntimeError("cannot build the engine")
    return real(self)


App.reload = explode
try:
    app.handle("PUT", "/api/config/source/v", {}, {"type": "virtual", "points": []}, admin)
except ApiError as e:
    assert e.status == 409 and "not saved" in str(e) and "previous version" in str(e), e
else:
    raise AssertionError("a config that cannot start must be refused")
App.reload = real

assert app.store.get("source", "v") == good, "the stored config is exactly what it was before the failed save"
assert not [a for a in app.store.audit_log(limit=50) if a["action"] == "config.put" and "v" in a["detail"] and "created" not in a["detail"] and "period" in a["detail"]], "a rolled-back save is not audited as a change"
app.reload()  # and the running engine still holds the good version
assert [s.name for s in app.engine.sources] == ["v"]

# a new object that cannot be started leaves nothing behind
boom["n"] = 0
App.reload = explode
try:
    app.handle("PUT", "/api/config/source/broken", {}, {"type": "virtual", "points": []}, admin)
except ApiError:
    pass
App.reload = real
assert app.store.get("source", "broken") is None, "a failed new object is not stored"

# ---- an import that fails halfway changes nothing
store.put("source", "keep", {"type": "virtual", "points": [{"name": "k", "kind": "sine", "period": 60, "min": 0, "max": 1}]})
store.put("point", "keep-p", {"source": "keep", "unit": "C"})
before = {(k, o["name"]): o for k in ("source", "point") for o in store.list(k)}

doc = {"format": "durian-config", "version": 1, "exported_at": 0, "objects": {
    "source": [{"name": "new-src", "type": "virtual", "points": [{"name": "n", "kind": "sine", "period": 60, "min": 0, "max": 1}]}],
    "point": [{"name": "new-p", "source": "new-src"}]}}
real_put = store.put
calls = {"n": 0}


def flaky(kind, name, body):
    calls["n"] += 1
    if kind == "point":
        raise OSError("disk hiccup")  # fails after the source has been written
    return real_put(kind, name, body)


store.put = flaky
try:
    app.import_config({"apply": "1"}, doc, {**admin})
except (OSError, ApiError):
    pass
store.put = real_put

after = {(k, o["name"]): o for k in ("source", "point") for o in store.list(k)}
assert after == before, ("an import that fails halfway must leave the configuration untouched", set(after) ^ set(before))
assert calls["n"] >= 1, "the import did write before failing"

# and a clean import does apply, in one go
errs, summary = app.maint.import_config(doc, {
    "source": lambda n, b, v: [], "point": lambda n, b, v: [], "handler": lambda n, b, v: [],
    "publisher": lambda n, b, v: [], "watchlist": lambda n, b, v: [], "maintenance": lambda n, b, v: [],
    "dashboard": lambda n, b, v: []}, "merge", False, "admin")
assert errs == [], errs
assert summary["source"]["created"] == 1 and summary["point"]["created"] == 1, summary
assert store.get("point", "new-p") is not None

print("ok")
