"""The export bundle: contents, manifest counts, safe names, bad requests, and the daily schedule. Run: python tests/test_export.py"""
import csv
import datetime as dt
import io
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import export  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.core import Event, Level  # noqa: E402
from _support import new_store  # noqa: E402

tmp = Path(tempfile.mkdtemp())
DAY = 86400
now = time.time()

store = new_store()
seed(store)
app = App(store, tmp / "backups")
app.auth.bootstrap("admin-password-123")
store.add_user("viewer", hash_password("viewer-password-1"), ["user"])
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
viewer = app.auth.identity(app.auth.login("viewer", "viewer-password-1"))


def call(who, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, who)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


# ---- two days of samples, one alarm in the window and one outside it
store.add_history([("boiler-temp", now - 2 * DAY + h * 3600, float(h)) for h in range(3)])
store.add_history([("pump-setpoint", now - DAY + h * 3600, 10.0 + h) for h in range(2)])
store.add_event(Event("boiler-temp", Level.WARNING, "too hot", now - 3600))
store.add_event(Event("boiler-temp", Level.INFORMATION, "old news", now - 30 * DAY))

data, name, manifest = export.build(store, days=2, points=["boiler-temp", "pump-setpoint"], now=now)
assert name == "durian-export-" + dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y%m%d-%H%M%S") + ".zip", name
z = zipfile.ZipFile(io.BytesIO(data))
members = set(z.namelist())
assert members == {"history/boiler-temp.csv", "history/pump-setpoint.csv", "events.csv", "config.json", "manifest.json"}, members
assert json.loads(z.read("manifest.json")) == manifest
assert manifest["points"]["count"] == 2 and manifest["window"]["days"] == 2 and manifest["durian_version"]
assert manifest["rows"]["history_by_point"] == {"boiler-temp": 3, "pump-setpoint": 2} and manifest["rows"]["history"] == 5
hist = list(csv.reader(io.StringIO(z.read("history/boiler-temp.csv").decode())))
assert hist[0] == ["timestamp_utc", "value", "quality"] and len(hist) == 4 and hist[1][2] == "good", hist
assert hist[1][0].endswith("Z") and "T" in hist[1][0]
events = list(csv.reader(io.StringIO(z.read("events.csv").decode())))
assert events[0] == ["time_utc", "point", "level", "message", "state", "cleared"] and len(events) == 2, events
assert events[1][2] == "WARNING" and events[1][4] == "active_unacked"
assert manifest["rows"]["events"] == 1
config = json.loads(z.read("config.json"))
assert config["format"] == "durian-config" and "boiler-temp" in {p["name"] for p in config["objects"]["point"]}
assert manifest["objects"]["point"] == len(store.list("point"))

# ---- a point named like a path cannot escape the zip
store.put("point", "../evil", {"source": "demo"})
store.add_history([("../evil", now - 100, 1.0)])
data, _name, manifest = export.build(store, days=1, points=["../evil"], now=now)
z = zipfile.ZipFile(io.BytesIO(data))
assert "history/evil.csv" in z.namelist(), z.namelist()
for member in z.namelist():
    assert not member.startswith(("/", "\\")) and ".." not in member.split("/"), member
assert manifest["points"]["files"] == {"../evil": "history/evil.csv"}

# ---- the same through HTTP, and the manifest preview that does not build a bundle
status, raw = call(admin, "GET", "/api/export/bundle", days="2", points="boiler-temp,pump-setpoint")
assert status == 200 and raw.ctype == "application/zip" and zipfile.ZipFile(io.BytesIO(raw.data)).namelist()
status, doc = call(admin, "GET", "/api/export/manifest", days="2", points="boiler-temp")
assert status == 200 and doc["points"]["count"] == 1 and doc["window"]["days"] == 2 and doc["rows"] is None
assert set(doc["contents"]) == {"history", "events", "config", "manifest"} and doc["contents"]["history"] == ["history/boiler-temp.csv"]
assert call(viewer, "GET", "/api/export/bundle")[0] == 403 and call(viewer, "GET", "/api/export/manifest")[0] == 403

# ---- a bad window is the caller's mistake: 422 with a sentence
for bad in ("x", "0", "-1", "91"):
    status, body = call(admin, "GET", "/api/export/bundle", days=bad)
    assert status == 422 and body["error"] and "days" in body["error"], (bad, body)
    assert call(admin, "GET", "/api/export/manifest", days=bad)[0] == 422, bad
store.set_setting("retention_days", 2)
status, body = call(admin, "GET", "/api/export/bundle", days="7")
assert status == 422 and "retention" in body["error"], body
store.set_setting("retention_days", 365)
assert call(admin, "GET", "/api/export/bundle", days="7", points="ghost")[0] == 422

# ---- the schedule writes one bundle a day, prunes old ones, and -1 writes nothing
store.set_setting("export_hour", 0)
store.set_setting("export_keep", 2)
at = lambda d: dt.datetime(2030, 6, 1 + d, 0, 5).timestamp()
for d in range(3):
    assert app.maint.tick(at(d)) == ["export"], d
    app.maint.wait_export()
files = sorted(p.name for p in (tmp / "exports").iterdir())
assert len(files) == 2, files  # export_keep: the oldest bundle is gone
status = call(admin, "GET", "/api/database")[1]
assert status["last_export"]["detail"]["name"] == files[-1], (status["last_export"], files)
assert [e["name"] for e in status["exports"]] == sorted(files, reverse=True)

store.set_setting("export_hour", -1)
assert app.maint.tick(at(3)) == []
app.maint.wait_export()
assert sorted(p.name for p in (tmp / "exports").iterdir()) == files  # nothing written while the setting is off

# ---- a bundle that cannot be built shows up in the maintenance status, not only in the log
store.set_setting("export_hour", 0)


def broken(*a, **k):
    raise export.ExportError("there is not enough space to write the bundle")


real, export.build = export.build, broken
try:
    assert app.maint.tick(at(4)) == ["export"]
    app.maint.wait_export()
    status = call(admin, "GET", "/api/database")[1]
    assert status["last_export"]["detail"]["error"] == "there is not enough space to write the bundle", status["last_export"]
finally:
    export.build = real

print("ok")
