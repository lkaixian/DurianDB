"""An unhandled exception is counted and, when one route keeps doing it, raises one (system) alarm.
Run: python tests/test_internal_errors.py"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.disable(logging.CRITICAL)  # the handler logs the traceback it is supposed to log; keep the test output clean
import durian.server as server  # noqa: E402
from durian.app import App, INTERNAL_ALARM_AFTER, INTERNAL_ALARM_SECONDS  # noqa: E402
from durian.core import Level  # noqa: E402
from durian.server import Handler  # noqa: E402
from _support import new_store  # noqa: E402


def system_events(app):
    return [e for e in app.store.events() if e["point"] == "(system)" and e["cleared_ts"] is None]


class FakeHandler(Handler):
    """The real handler with the socket taken out: enough to drive the path that turns an exception into a 500."""

    def __init__(self, path):
        self.path, self.headers, self.sent = path, {}, []

    def send(self, code, payload, ctype="application/json", extra=()):
        self.sent.append((code, payload))


store = new_store()
app = App(store)
server.app = app  # serve() reads the module global
admin = {"name": "admin", "roles": {"superadmin"}}
real = app.handle


def boom(method, path, query, body, ident):
    if path == "/api/points/secret-name":
        raise ValueError("point secret-name is not in the reply")
    return real(method, path, query, body, ident)


app.handle = boom

# two internal errors: each is a 500, the counter goes up, and the console gets a sentence without the class name
h = FakeHandler("/api/points/secret-name")
h.serve("GET")
h.serve("GET")
assert h.sent == [(500, {"error": "internal error"})] * 2, h.sent

health = real("GET", "/api/health", {}, None, admin)[1]
assert health["internal_errors"] == 2, health
last = health["last_internal_error"]
assert last["route"] == "GET /api/points", last  # the route shape, never the point name
assert "secret-name" not in last["route"]
assert "ValueError" not in last["message"] and "point secret-name is not in the reply" in last["message"], last
assert isinstance(last["ts"], float)

# ten errors in ten minutes is not yet a programming fault; the eleventh raises one URGENT alarm naming the route
for _ in range(INTERNAL_ALARM_AFTER - 2):
    h.serve("GET")
assert system_events(app) == [], "ten errors is not more than ten"
h.serve("GET")
alarms = system_events(app)
assert len(alarms) == 1 and alarms[0]["level"] == int(Level.URGENT), alarms
assert "GET /api/points" in alarms[0]["message"] and "internal errors" in alarms[0]["message"], alarms
first = alarms[0]["id"]

h.serve("GET")  # one more error must not raise a second alarm for the same route
assert len(system_events(app)) == 1, "one alarm per route, not one per error"

# a tick inside the window leaves it up; a tick after the window with the route quiet clears it
app.tick(time.time() + INTERNAL_ALARM_SECONDS // 2)
assert len(system_events(app)) == 1
app.tick(time.time() + INTERNAL_ALARM_SECONDS + 1)
assert system_events(app) == [], "a healthy tick clears the alarm"
closed = [e for e in store.events(limit=1000) if e["id"] == first][0]
assert closed["cleared_ts"] is not None, "the alarm was closed, not deleted"

# the count is since start, and the last error is still the last one seen
health = real("GET", "/api/health", {}, None, admin)[1]
assert health["internal_errors"] == INTERNAL_ALARM_AFTER + 2, health
assert health["last_internal_error"]["route"] == "GET /api/points"

print("ok")
