"""Maintenance windows. Run: python tests/test_windows.py"""
import datetime as dt
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import windows  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

at = lambda d, h, m: dt.datetime(2026, 10, d, h, m).timestamp()  # 2026-10-19 is a Monday

# ---- schedules
wk = {"schedule": {"type": "weekly", "days": [0], "start": "22:00", "end": "02:00"}}  # Monday night, crossing midnight
assert windows.active(wk, at(19, 23, 0)) and windows.active(wk, at(20, 1, 59))
assert not windows.active(wk, at(19, 21, 59)) and not windows.active(wk, at(20, 2, 0)) and not windows.active(wk, at(20, 23, 0))
day = {"schedule": {"type": "weekly", "days": [0, 1], "start": "08:00", "end": "17:00"}}
assert windows.active(day, at(20, 8, 0)) and not windows.active(day, at(20, 17, 0)) and not windows.active(day, at(21, 9, 0))
once = {"schedule": {"type": "once", "from": 100, "to": 200}}
assert windows.active(once, 100) and not windows.active(once, 200) and not windows.active({**once, "enabled": False}, 150)
assert windows.active({"schedule": {"type": "manual"}, "active": True}, 0) and not windows.active({"schedule": {"type": "manual"}}, 0)

# ---- scope
m = {"scope": {"points": ["a"], "sources": ["plc"], "tags": {"area": "boiler"}}}
assert windows.covers(m, "a") and windows.covers(m, "x", source="plc") and windows.covers(m, "x", {"area": "boiler", "z": "1"}) and not windows.covers(m, "x", {"area": "line"}, "demo")
assert not windows.covers({"scope": {"tags": {}}}, "x")

# ---- validation
N = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ok = {"mode": "suppress", "scope": {"points": ["a"]}, "schedule": {"type": "manual"}}
assert windows.validate("w", ok, N) == []
for bad in ({"mode": "mute"}, {"scope": {}}, {"scope": {"points": [1]}}, {"schedule": {"type": "daily"}}, {"schedule": {"type": "once", "from": 5, "to": 5}},
            {"schedule": {"type": "weekly", "days": [], "start": "01:00", "end": "02:00"}}, {"schedule": {"type": "weekly", "days": [7], "start": "01:00", "end": "02:00"}},
            {"schedule": {"type": "weekly", "days": [1], "start": "1:00", "end": "02:00"}}, {"schedule": {"type": "weekly", "days": [1], "start": "01:00", "end": "01:00"}},
            {"enabled": "yes"}, {"active": 1}):
    assert windows.validate("w", {**ok, **bad}, N), bad

# ---- in the app: suppress holds, acknowledge records and acknowledges, drop records nothing; ending a window announces what is still active
store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None):
    try:
        return app.handle(method, path, {}, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e)}


HOT = {"source": "demo", "generator": {"kind": "constant", "value": 95}, "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"}], "tags": {"area": "t"}}
for n in ("hot1", "hot2", "hot3"):
    assert call("PUT", f"/api/config/point/{n}", HOT)[0] == 200
assert call("PUT", "/api/config/maintenance/bad", {"mode": "x"})[0] == 422
sent = []
app.outputs.on_event = lambda ev, kind: sent.append((ev.point, kind)) if ev.point.startswith("hot") else None
W = lambda mode, point, active: call("PUT", f"/api/config/maintenance/{mode}", {"mode": mode, "scope": {"points": [point]}, "schedule": {"type": "manual"}, "active": active, "reason": "pump swap"})
assert W("suppress", "hot1", True)[0] == 200 and W("acknowledge", "hot2", True)[0] == 200 and W("drop", "hot3", True)[0] == 200
rows = lambda n: store.q("select held, acked_by, cleared_ts from events where point=? order by id", (n,))
now = time.time()
app.tick(now)
assert rows("hot1")[0]["held"] == "maintenance" and rows("hot1")[0]["acked_by"] is None
assert rows("hot2")[0]["held"] == "maintenance" and rows("hot2")[0]["acked_by"] == "maintenance: acknowledge"
assert rows("hot3") == []
assert sent == [], sent  # nothing was announced
d = call("GET", "/api/points/hot1")[1]
assert d["maintenance"] == [{"name": "suppress", "mode": "suppress"}], d["maintenance"]
assert [e["state"] for e in call("GET", "/api/events")[1] if e["point"] == "hot2"] == ["active_acked"]

# the window ends: alarms that are still active are announced
assert W("suppress", "hot1", False)[0] == 200 and W("acknowledge", "hot2", False)[0] == 200
app.tick(now + 1)
assert sorted(sent) == [("hot1", "raised"), ("hot2", "raised")], sent
assert rows("hot1")[0]["held"] is None
print("ok")
