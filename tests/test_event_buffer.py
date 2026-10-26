"""Alarms waiting for a failed database are capped, counted, and announced. Run: python tests/test_event_buffer.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import durian.app as A  # noqa: E402
from _support import new_store  # noqa: E402
from durian.app import App  # noqa: E402
from durian.core import Event, Level  # noqa: E402

store, app = new_store(), None
app = App(store)
admin = {"name": "admin", "roles": {"superadmin"}}

# the database refuses every alarm write
real = store.add_event
def down(ev):
    raise OSError("database is down")


store.add_event = down


def tick_with(n, ts):
    """One tick that produces n alarms."""
    app.engine.events.extend(Event("p", Level.URGENT, f"alarm {i}", ts) for i in range(n))
    app.tick(ts)


A.EVENT_PENDING_MAX = 3
tick_with(10, 1000)
assert len(app.unstored) == 3, len(app.unstored)
assert app.events_lost == 7, app.events_lost

# while writes fail, nothing can be recorded about it; the health view carries the count
assert [e for e in app.store.events(limit=100) if e["point"] == "(system)"] == [], "the database cannot record its own fault"

# the count is on the health view
h = app.handle("GET", "/api/health", {}, None, admin)[1]
assert h["events_lost"] == 7 and h["unstored_events"] == 3, h

# more failures do not raise a second alarm, and do not grow the buffer past the cap
def still_down(ev):
    raise OSError("still down")


store.add_event = still_down
tick_with(10, 1002)
assert len(app.unstored) == 3 and app.events_lost == 17, (len(app.unstored), app.events_lost)  # 3 held + 10 new, capped at 3

# the database comes back: the held alarms are written, and the system alarm clears
store.add_event = real
app.tick(1004)
assert app.unstored == [], app.unstored
sys_ev = [e for e in app.store.events(limit=100) if e["point"] == "(system)"]
assert len(sys_ev) == 1 and "17 alarm(s) were dropped" in sys_ev[0]["message"], sys_ev
assert app.events_lost == 0, "the loss is reported once, not once per tick"
h = app.handle("GET", "/api/health", {}, None, admin)[1]
assert h["ok"] is True and h["events_lost"] == 0 and h["unstored_events"] == 0, h

print("ok")
