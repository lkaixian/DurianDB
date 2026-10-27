"""A point name may contain a slash (a Sparkplug point is node/device/metric), so the console percent-encodes it."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _support import new_store  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.config import make_point  # noqa: E402

store = new_store()
app = App(store)
app.auth.bootstrap("admin-password-1")
admin = app.auth.identity(app.login({"username": "admin", "password": "admin-password-1"}))
call = lambda m, p, b=None: app.handle(m, p, {}, b, admin)

# a source exists to hang the point on, then the point itself is named the way the Sparkplug source names one
assert call("PUT", "/api/config/source/v", {"type": "virtual"})[0] == 200
if app.engine is None:
    app.reload()
source = next(s for s in app.engine.sources if s.name == "v")
source.add(make_point("Scada/Device/Temp", {}, "v"))

# the encoded path reaches it
body = call("GET", "/api/points/Scada%2FDevice%2FTemp")[1]
assert body["name"] == "Scada/Device/Temp" and body["source"] == "v", body

# the slash is decoded once, never twice: a doubly encoded name stays the literal name and finds nothing
try:
    call("GET", "/api/points/Scada%252FDevice%252FTemp")
    raise AssertionError("a doubly encoded name addressed a point")
except ApiError as ex:
    assert ex.status == 404

# an encoded slash cannot become a new path segment: the same name still has to be a route the router can see
try:
    call("GET", "/api/points/Scada/Device/Temp")
    raise AssertionError("a raw slash in the path reached a point")
except ApiError as ex:
    assert ex.status == 404 and str(ex) == "not found"

# and a percent-encoded traversal in the name slot is just a name that does not exist
try:
    call("GET", "/api/points/%2E%2E%2Fetc")
    raise AssertionError("an encoded traversal reached a point")
except ApiError as ex:
    assert ex.status == 404 and str(ex) == "unknown point", str(ex)

print("ok")
