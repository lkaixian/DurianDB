"""The request envelope fails as sentences, not 500s, and a corrupt schema is not replayed. Run: python tests/test_envelope.py"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import durian.app as A  # noqa: E402
from _support import new_store  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.store import Store  # noqa: E402

store = new_store()
app = App(store)
admin = {"name": "admin", "roles": {"superadmin"}}

# ---- a JSON body that is not an object is the caller's mistake, not a server fault
for bad in ([1, 2], 7, True):
    try:
        app.handle("PUT", "/api/config/source/s", {}, bad, admin)
    except ApiError as e:
        assert e.status == 400 and "JSON object" in str(e), (bad, e.status, str(e))
    else:
        raise AssertionError(f"{bad!r} should be refused")

# the sign-in and reset paths get the same treatment instead of .get on a list
for path in ("/api/login", "/api/reset"):
    try:
        app.handle("POST", path, {}, ["not", "an", "object"], {"name": None, "roles": set()})
    except ApiError as e:
        assert e.status in (400, 401), (path, e.status, str(e))

# a text body is still allowed for the CSV imports: the rows are checked, not the body refused
try:
    app.handle("POST", "/api/import/points", {}, "name,source\nx,y\n", admin)
except ApiError as e:
    assert e.status == 422, e.status  # source 'y' does not exist: a row problem, not a body problem
else:
    raise AssertionError("expected the row to be rejected")

# ---- a schema version that exists but cannot be read is an error, not a reason to replay everything
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "corrupt.db")
s = Store(path)
s.close()
raw = sqlite3.connect(path)
raw.execute("drop table schema_version")
raw.execute("create table schema_version(broken text)")  # the table exists, but has no `version` column
raw.commit()
raw.close()
try:
    Store(path)
except RuntimeError as e:
    assert "schema version cannot be read" in str(e), e
else:
    raise AssertionError("a corrupt schema_version must stop the server, not reset it")

# a database that has never been migrated is simply version 0: the table is absent, not broken
fresh = Store(os.path.join(tmp, "fresh.db"))
assert fresh.version() > 0, "a new database migrates itself"

print("ok")
