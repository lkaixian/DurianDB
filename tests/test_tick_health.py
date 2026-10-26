"""A failing poll loop must reach an operator, and stop being an alarm once ticks work again. Run: python tests/test_tick_health.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _support import new_store  # noqa: E402
from durian.app import TICK_ALARM_AFTER, ApiError, App  # noqa: E402
from durian.core import Level  # noqa: E402


def system_events(app):
    return [e for e in app.store.events() if e["point"] == "(system)" and e["cleared_ts"] is None]


def broken(app, exc=RuntimeError("source exploded")):
    def boom(ts=None):
        raise exc
    app.engine.tick = boom
    return app


store = new_store()
app = App(store)
admin = {"name": "admin", "roles": {"superadmin"}}

# a healthy tick says so
app.tick(1000)
h = app.handle("GET", "/api/health", {}, None, admin)[1]
assert h["ok"] is True and h["tick_failures"] == 0 and h["tick_ok_ts"] == 1000, h

# one failure is not news: no alarm yet, but the state is already visible
broken(app)
try:
    app.tick(1001)
except RuntimeError:
    pass
h = app.handle("GET", "/api/health", {}, None, admin)[1]
assert h["ok"] is False and h["tick_failures"] == 1, h
assert "source exploded" in h["tick_error"], h
assert system_events(app) == [], "one blip alarms nobody"

# the third consecutive failure raises one URGENT alarm, named with the error
for ts in (1002, 1003):
    try:
        app.tick(ts)
    except RuntimeError:
        pass
h = app.handle("GET", "/api/health", {}, None, admin)[1]
assert h["tick_failures"] == TICK_ALARM_AFTER, h
alarms = system_events(app)
assert len(alarms) == 1, alarms
assert alarms[0]["level"] == int(Level.URGENT) and "poll loop is failing" in alarms[0]["message"], alarms[0]
assert "source exploded" in alarms[0]["message"], "the alarm names the real failure"
first_id = alarms[0]["id"]

# a fourth failure must not raise a second alarm
try:
    app.tick(1004)
except RuntimeError:
    pass
assert len(system_events(app)) == 1, "the alarm is raised once, not once per failed tick"

# the alarm is a system alarm: only a superadmin sees it
try:
    app.handle("GET", "/api/events", {}, None, {"name": "bob", "roles": {"user"}})
except ApiError:
    pass
else:
    assert all(e["point"] != "(system)" for e in app.handle("GET", "/api/events", {}, None, {"name": "bob", "roles": {"user"}})[1])

# a good tick clears it, and the counter starts again
app.engine = None
app.reload()
app.tick(1005)
assert system_events(app) == [], "a working loop clears the alarm"
h = app.handle("GET", "/api/health", {}, None, admin)[1]
assert h["ok"] is True and h["tick_failures"] == 0 and h["tick_ok_ts"] == 1005, h
closed = [e for e in app.store.events(limit=1000) if e["id"] == first_id][0]
assert closed["cleared_ts"] is not None, "the alarm was closed, not deleted"

# and it comes back if the loop fails again
broken(app)
for ts in (1006, 1007, 1008):
    try:
        app.tick(ts)
    except RuntimeError:
        pass
fresh = system_events(app)
assert len(fresh) == 1 and fresh[0]["id"] != first_id, fresh

print("ok")
