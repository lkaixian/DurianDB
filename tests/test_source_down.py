"""Source-down alarm and heartbeat. Run: python tests/test_source_down.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.core import SOURCE, Engine, Point, Quality, Source  # noqa: E402
from _support import new_store  # noqa: E402


class Link(Source):
    connected = True
    out = []

    def poll(self, ts):
        if self.fail:
            raise OSError("refused")
        out, self.out = self.out, []
        return out
    fail = False


def mk(**kw):
    s = Link("plc", **kw)
    s.add(Point("a"))
    e = Engine()
    e.sources.append(s)
    return e, s


# ---- link lost: alarm only after down_after, points go BAD, everything clears on recovery
e, s = mk(down_after=5.0)
s.out = [("a", 1.0)]
e.tick(0.0)
s.connected = False
e.tick(1.0)
e.tick(4.0)
assert e.events == []  # a blip is not an alarm
e.tick(7.0)
assert [(x.point, x.level.name) for x in e.events] == [(SOURCE + "plc", "URGENT")] and "not connected" in e.events[0].message
assert s.down_reason and s.points["a"].quality == Quality.BAD and s.points["a"].last == 1.0
e.tick(8.0)
assert len(e.events) == 1  # one alarm, not one per tick
s.connected, s.out = True, [("a", 2.0)]
e.tick(9.0)
assert e.events[0].cleared_ts == 9.0 and s.down_reason is None and s.points["a"].quality == Quality.GOOD

# ---- a source whose poll raises is down too, with the error as the reason
e, s = mk(down_after=0.0)
s.fail = True
e.tick(1.0)
assert "OSError" in e.events[0].message
s.fail = False
e.tick(2.0)
assert e.events[0].cleared_ts == 2.0

# ---- heartbeat: silence from a source that never reports a link state
class Push(Source):
    out = []

    def poll(self, ts):
        out, self.out = self.out, []
        return out


p = Push("hook", heartbeat=30.0, down_after=0.0)
p.add(Point("x"))
e = Engine()
e.sources.append(p)
p.out = [("x", 1.0)]
e.tick(100.0)
e.tick(125.0)
assert e.events == []
e.tick(131.0)
assert "heartbeat 30s" in e.events[0].message and p.points["x"].quality == Quality.BAD
p.out = [("x", 2.0)]
e.tick(140.0)
assert e.events[0].cleared_ts == 140.0 and p.points["x"].quality == Quality.GOOD
p.out = [("x", None, 150.0, Quality.BAD)]  # a bad value is not data
e.tick(150.0)
e.tick(171.0)
assert len(e.events) == 2

# ---- poll overrun: a poll slower than its period raises an alarm once, and clears when polls fit again
e, s = mk(down_after=0.0)
s.period, s.poll_ms = 2.0, 500.0
e.tick(1.0)
assert e.events == []
s.poll_ms = 3100.0
e.tick(2.0)
e.tick(3.0)
assert len(e.events) == 1 and "poll took 3.1s" in e.events[0].message and e.events[0].level.name == "URGENT" and s.overruns == 1
s.poll_ms = 800.0
e.tick(4.0)
assert e.events[0].cleared_ts == 4.0

# ---- configuration and visibility through the app
store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(ident, method, path, body=None):
    try:
        return app.handle(method, path, {}, body, ident)
    except ApiError as e:
        return e.status, {"error": str(e)}


call(admin, "PUT", "/api/users/op", {"password": "viewer-password-1", "roles": ["user"]})
op = app.auth.identity(app.auth.login("op", "viewer-password-1"))
for bad in ({"heartbeat_seconds": 1}, {"heartbeat_seconds": "x"}, {"down_after": -1}, {"down_after": 4000}):
    assert call(admin, "PUT", "/api/config/source/hook", {"type": "http_in", **bad})[0] == 422, bad
assert call(admin, "PUT", "/api/config/source/hook", {"type": "http_in", "heartbeat_seconds": 5, "down_after": 0})[0] == 200
assert call(admin, "PUT", "/api/config/source/secret", {"type": "http_in", "heartbeat_seconds": 5, "down_after": 0, "read_roles": []})[0] == 200
now = time.time()
app.tick(now)
app.tick(now + 10)
mine = lambda ident: [x["point"] for x in call(ident, "GET", "/api/events")[1] if x["point"].startswith(SOURCE)]
assert mine(op) == [SOURCE + "hook"] and set(mine(admin)) == {SOURCE + "hook", SOURCE + "secret"}  # a source nobody may read stays hidden
ev = next(x for x in call(op, "GET", "/api/events")[1] if x["point"] == SOURCE + "hook")
assert call(op, "POST", f"/api/events/{ev['id']}/ack")[0] == 200
health = {x["name"]: x for x in call(admin, "GET", "/api/sources")[1]}
assert "heartbeat" in health["hook"]["down"]
print("ok")
