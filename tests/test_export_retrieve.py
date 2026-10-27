"""Stored export bundles: the list, the download, and the names that must not be served.
Run: python tests/test_export_retrieve.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

tmp = Path(tempfile.mkdtemp())
store = new_store()
seed(store)
app = App(store, tmp / "backups")  # the export folder is backups' sibling, tmp / "exports"
app.auth.bootstrap("admin-password-123")
store.add_user("viewer", hash_password("viewer-password-1"), ["user"])
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
viewer = app.auth.identity(app.auth.login("viewer", "viewer-password-1"))
ANON = {"name": None, "roles": {"anonymous"}}


def call(who, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, who)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


# ---- no export folder yet: an empty list, not an error
assert app.maint.export_dir == tmp / "exports"
assert call(admin, "GET", "/api/database/exports") == (200, [])

# ---- two stored bundles plus files the list must ignore: a stray file and a wrong extension
exports_dir = app.maint.export_dir
exports_dir.mkdir(parents=True)
older = "durian-export-20250101-000500.zip"
newer = "durian-export-20250102-000500.zip"
payload = b"PK\x03\x04 a fake bundle the download must return unchanged"
(exports_dir / older).write_bytes(b"old bundle")
(exports_dir / newer).write_bytes(payload)
(exports_dir / "notes.txt").write_text("not a bundle")
(exports_dir / "durian-export-20250102-000500.tar").write_bytes(b"wrong extension")

status, listed = call(admin, "GET", "/api/database/exports")
assert status == 200, (status, listed)
assert [b["name"] for b in listed] == [newer, older], listed  # newest first
assert listed[0]["bytes"] == len(payload) and listed[1]["bytes"] == len(b"old bundle")
assert listed[0]["ts"] == (exports_dir / newer).stat().st_mtime, listed[0]["ts"]

# ---- a stored bundle downloads as a zip under its own name
status, raw = call(admin, "GET", f"/api/database/exports/{newer}")
assert status == 200 and raw.ctype == "application/zip" and raw.filename == newer, (status, raw)
assert raw.data == payload

# ---- the download is audited like a built bundle
row = next(a for a in store.audit_log() if a["action"] == "export.download")
assert row["user"] == "admin" and row["detail"] == f"{newer}: {len(payload)} bytes", row

# ---- a missing name, a traversal, an absolute-ish name and a wrong extension are all 404, all the same sentence
status, missing = call(admin, "GET", "/api/database/exports/durian-export-29990101-000000.zip")
assert status == 404 and missing["error"] == "unknown export bundle", missing
for path in ("/api/database/exports/..%2F..%2F" + newer,      # encoded traversal, reaches the route
             "/api/database/exports/C:\\Windows\\win.ini",     # an absolute path, reaches the route
             "/api/database/exports/durian-export-20250102-000500.tar"):  # exists, but the wrong kind of file
    status, body = call(admin, "GET", path)
    assert status == 404 and body["error"] == missing["error"], (path, status, body)
assert call(admin, "GET", "/api/database/exports/../" + newer)[0] == 404  # the router refuses the shape

# ---- anonymous and plain-user callers can neither list nor download
for who, denied in ((ANON, 401), (viewer, 403)):
    assert call(who, "GET", "/api/database/exports")[0] == denied, who
    assert call(who, "GET", f"/api/database/exports/{newer}")[0] == denied, who

print("ok")
