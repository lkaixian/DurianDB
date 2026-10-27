"""A calculated point that cannot evaluate must reach an operator: one (source) calc alarm after down_after, cleared when the expression works.
Run: python tests/test_calc_health.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.calc import parse  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.core import SOURCE, Level  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None):
    try:
        return app.handle(method, path, {}, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


def calc_events():
    return [e for e in app.store.events() if e["point"] == SOURCE + "calcs"]


# fixed has a value; boiler-eff reads a point that does not exist, so its expression cannot evaluate
assert call("PUT", "/api/config/point/fixed", {"source": "demo", "generator": {"kind": "constant", "value": 10}})[0] == 200
assert call("PUT", "/api/config/source/calcs", {"type": "calc", "down_after": 5})[0] == 200
assert call("PUT", "/api/config/point/boiler-eff", {"source": "calcs", "calc": {"expression": 'p("no-such-input") * 1.8'}})[0] == 200

t = time.time()
app.tick(t)
assert calc_events() == [], "a first failed calculation is not an alarm yet"
pts = {p["name"]: p for p in call("GET", "/api/points")[1]}
assert pts["boiler-eff"]["quality"] == "bad"

app.tick(t + 4)
assert calc_events() == [], "the alarm waits for the source's down_after"

app.tick(t + 5)
events = calc_events()
assert len(events) == 1 and events[0]["level"] == int(Level.URGENT), events
msg = events[0]["message"]
assert "1 calculation(s) failed" in msg and "boiler-eff" in msg and "no-such-input has no good value" in msg, msg
assert "Traceback" not in msg and "LookupError" not in msg, msg

# the point page carries the same calculation reason while the source is down
assert call("GET", "/api/points/boiler-eff")[1]["source_down"] == msg

# one event, not one per tick
app.tick(t + 6)
assert len(calc_events()) == 1

# the expression works again: the point updates and the alarm closes
cs = next(s for s in app.engine.sources if s.name == "calcs")
cs.trees["boiler-eff"] = parse('p("fixed") * 1.8')
app.tick(t + 7)
assert calc_events()[0]["cleared_ts"] == t + 7
assert call("GET", "/api/points/boiler-eff")[1]["source_down"] is None
pts = {p["name"]: p for p in call("GET", "/api/points")[1]}
assert pts["boiler-eff"]["quality"] == "good" and pts["boiler-eff"]["last"] == 18.0, pts["boiler-eff"]
print("ok")
