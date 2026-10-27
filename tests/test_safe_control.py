"""Tag fields and safe control of setpoints: limits, step size, confirmation. Run: python tests/test_safe_control.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
call = lambda ident, m, p, b=None: (lambda r: r)(_c(ident, m, p, b))


def _c(ident, method, path, body):
    try:
        return app.handle(method, path, {}, body, ident)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


SP = {"source": "demo", "unit": "bar", "settable": True, "generator": {"kind": "constant", "value": 5}, "description": "Header pressure setpoint",
      "eu_low": 0, "eu_high": 10, "decimals": 1, "set_max": 8, "max_step": 2, "confirm": True}
assert call(admin, "PUT", "/api/config/point/sp", SP)[0] == 200
app.tick(time.time())

# ---- fields are validated
for bad in ({"eu_low": 5, "eu_high": 5}, {"set_min": 9, "set_max": 8}, {"decimals": 9}, {"decimals": True}, {"max_step": 0}, {"confirm": "yes"},
            {"description": "x" * 201}, {"eu_low": "0"}):
    assert call(admin, "PUT", "/api/config/point/sp2", {**SP, **bad})[0] == 422, bad
assert call(admin, "PUT", "/api/config/point/ro", {"source": "demo", "set_max": 5})[0] == 422  # limits on a point that cannot be set

# ---- the point view carries the fields
v = next(p for p in call(admin, "GET", "/api/points")[1] if p["name"] == "sp")
assert v["description"] == "Header pressure setpoint" and v["decimals"] == 1 and v["eu"] == {"low": 0, "high": 10}
assert v["set_limits"] == {"min": 0, "max": 8, "max_step": 2, "confirm": True, "reason_required": False}  # min falls back to the engineering range

# ---- limits and step size are enforced before anything else happens
assert call(admin, "POST", "/api/points/sp/set", {"value": 9})[0] == 422  # above set_max
assert call(admin, "POST", "/api/points/sp/set", {"value": -1})[0] == 422  # below the engineering range
s, r = call(admin, "POST", "/api/points/sp/set", {"value": 7.5})
assert s == 422 and "at most 2" in r["error"]  # 5 -> 7.5 is too big a jump
assert not any(a["action"] == "set" and a["detail"].startswith("sp") for a in call(admin, "GET", "/api/audit")[1])

# ---- confirmation: the first call only asks, the second must repeat the same value with the token, once
s, r = call(admin, "POST", "/api/points/sp/set", {"value": 6})
assert s == 202 and r["confirm"]
tok = r["confirm"]
assert call(admin, "POST", "/api/points/sp/set", {"value": 6, "confirm": "wrong"})[0] == 202  # a wrong token just asks again
assert call(admin, "POST", "/api/points/sp/set", {"value": 7, "confirm": tok})[0] == 202  # a token is for one value
s, r = call(admin, "POST", "/api/points/sp/set", {"value": 6})
assert call(admin, "POST", "/api/points/sp/set", {"value": 6, "confirm": r["confirm"]})[0] == 200
assert call(admin, "POST", "/api/points/sp/set", {"value": 6, "confirm": r["confirm"]})[0] == 202  # used up
app.tick(time.time())
assert next(p for p in call(admin, "GET", "/api/points")[1] if p["name"] == "sp")["last"] == 6
assert any(a["detail"] == "sp: 5.0 -> 6" or a["detail"] == "sp: 5 -> 6" for a in call(admin, "GET", "/api/audit")[1])

# an expired token is refused
s, r = call(admin, "POST", "/api/points/sp/set", {"value": 7})
app.store.run("update confirm_tokens set expires=? where token=?", (time.time() - 1, r["confirm"]))
assert call(admin, "POST", "/api/points/sp/set", {"value": 7, "confirm": r["confirm"]})[0] == 202

# releasing needs neither limits nor confirmation
assert call(admin, "POST", "/api/points/sp/set", {"value": None})[0] == 200

# ---- reasons: a point can demand one with every write, and it lands in the audit log
assert call(admin, "PUT", "/api/config/point/rs", {"source": "demo", "settable": True, "set_roles": ["user"], "require_reason": True, "generator": {"kind": "constant", "value": 1}})[0] == 200
assert call(admin, "PUT", "/api/config/point/rs2", {"source": "demo", "require_reason": True})[0] == 422  # only for a settable point
app.tick(time.time())
assert call(admin, "POST", "/api/points/rs/set", {"value": 3})[0] == 422 and call(admin, "POST", "/api/points/rs/set", {"value": 3, "reason": "  "})[0] == 422
assert call(admin, "POST", "/api/points/rs/set", {"value": 3, "reason": 5})[0] == 400 and call(admin, "POST", "/api/points/rs/set", {"value": 3, "reason": "x" * 201})[0] == 400
assert call(admin, "POST", "/api/points/rs/set", {"value": 3, "reason": "valve swap"})[0] == 200
assert any(a["detail"].endswith("-> 3 reason: valve swap") for a in call(admin, "GET", "/api/audit")[1])
assert next(p for p in call(admin, "GET", "/api/points")[1] if p["name"] == "rs")["set_limits"]["reason_required"] is True

# ---- binary and multistate points: labels for values, a labelled point view
ST = {"source": "demo", "generator": {"kind": "constant", "value": 1}, "states": {"0": "Stopped", "1": "Running", "2": "Fault"},
      "detectors": [{"kind": "STATE", "states": [2], "level": "URGENT"}, {"kind": "NO_CHANGE", "seconds": 600}, {"kind": "RANGE", "low": 0, "high": 5}]}
assert call(admin, "PUT", "/api/config/point/pump", ST)[0] == 200
for bad in ({"states": {}}, {"states": {"x": "On"}}, {"states": {"1": ""}}, {"states": {"1": "y" * 41}}, {"states": ["a"]},
            {"detectors": [{"kind": "STATE", "states": []}]}, {"detectors": [{"kind": "RANGE", "low": 2, "high": 1}]}):
    assert call(admin, "PUT", "/api/config/point/pump2", {**ST, **bad})[0] == 422, bad
app.tick(time.time())
v = next(p for p in call(admin, "GET", "/api/points")[1] if p["name"] == "pump")
assert v["text"] == "Running" and v["states"]["2"] == "Fault", v

# ---- interval logging config
for bad in ({"logging": "INTERVAL"}, {"logging": "INTERVAL", "log_interval": 0}, {"logging": "INTERVAL", "log_interval": 10, "log_mode": "median"}):
    assert call(admin, "PUT", "/api/config/point/iv", {"source": "demo", **bad})[0] == 422, bad
assert call(admin, "PUT", "/api/config/point/iv", {"source": "demo", "logging": "INTERVAL", "log_interval": 10, "log_mode": "avg"})[0] == 200

# ---- folders: validated, shown, and usable as a filter (a folder includes what is below it)
for bad in ("a//b", "/x", "x/", " ", "y" * 65):
    assert call(admin, "PUT", "/api/config/point/fo", {"source": "demo", "folder": bad})[0] == 422, bad
for n, f in (("fo-a", "Plant/Boiler"), ("fo-b", "Plant/Boiler/Drum"), ("fo-c", "Plant/Line"), ("fo-d", "Plantation")):
    assert call(admin, "PUT", f"/api/config/point/{n}", {"source": "demo", "folder": f})[0] == 200
names = lambda **q: sorted(p["name"] for p in app.handle("GET", "/api/points", q, None, admin)[1] if p["name"].startswith("fo-"))
assert names(folder="Plant") == ["fo-a", "fo-b", "fo-c"] and names(folder="Plant/Boiler") == ["fo-a", "fo-b"] and names(folder="Plant/Boiler/") == ["fo-a", "fo-b"] and names() == ["fo-a", "fo-b", "fo-c", "fo-d"]
# ---- write rate limit: a settable point refuses a second write inside a time window.
# `min_write_gap` is whole seconds from 0 to 3600; absent or 0 means no limit. It sits with the other safe-control
# fields (set_min/set_max/max_step/confirm/require_reason) so both operator writes and handler writes meet it.
# Time is faked the way tests/test_notes.py fakes it: swap the module's clock for this section, then restore it.
import durian.app as appmod  # noqa: E402


class Clock:
    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now

    def monotonic(self):
        return self.now


RL = {"source": "demo", "settable": True, "generator": {"kind": "constant", "value": 5}, "set_max": 9, "min_write_gap": 2}
assert call(admin, "PUT", "/api/config/point/rl", RL)[0] == 200
real_time, appmod.time = appmod.time, Clock(time.time() + 10)  # clear of when the point was created
try:
    assert call(admin, "POST", "/api/points/rl/set", {"value": 6})[0] == 200  # the first write is never held back
    s, r = call(admin, "POST", "/api/points/rl/set", {"value": 7})  # a second write in the same instant
    assert s == 422 and r["error"].startswith("rl") and "at most once every 2 seconds" in r["error"], (s, r)
    assert any(a["action"] == "set.refused" and "rl" in a["detail"] and "at most once every 2 seconds" in a["detail"]
               for a in call(admin, "GET", "/api/audit")[1]), "the refusal is in the audit trail"
    appmod.time.now += 2.5
    assert call(admin, "POST", "/api/points/rl/set", {"value": 7})[0] == 200  # past the window, a write gets through
finally:
    appmod.time = real_time

# the field itself is validated and names itself when it is wrong
for bad in (-1, 3601, 1.5, True, "5"):
    s, r = call(admin, "PUT", "/api/config/point/rl-bad", {**RL, "min_write_gap": bad})
    assert s == 422, (bad, s, r)
    assert any(e["property"] == "min_write_gap" for e in r["errors"]), (bad, r)
assert call(admin, "PUT", "/api/config/point/rl-zero", {**RL, "min_write_gap": 0})[0] == 200
assert call(admin, "PUT", "/api/config/point/rl-max", {**RL, "min_write_gap": 3600})[0] == 200
assert call(admin, "PUT", "/api/config/point/rl-ro", {"source": "demo", "min_write_gap": 2})[0] == 422  # only a settable point

# zero really means no limit: back-to-back writes both land
assert call(admin, "POST", "/api/points/rl-zero/set", {"value": 6})[0] == 200
assert call(admin, "POST", "/api/points/rl-zero/set", {"value": 7})[0] == 200

# ---- M1: a text point is rate-limited too; text has no range, but the time window still applies
TXT = {"source": "demo", "settable": True, "datatype": "text", "generator": {"kind": "constant", "value": "A"}, "min_write_gap": 60}
assert call(admin, "PUT", "/api/config/point/rl-txt", TXT)[0] == 200
appmod.time = Clock(time.time() + 30)
try:
    assert call(admin, "POST", "/api/points/rl-txt/set", {"value": "B"})[0] == 200
    s, r = call(admin, "POST", "/api/points/rl-txt/set", {"value": "C"})
    assert s == 422 and r["error"].startswith("rl-txt") and "at most once every 60 seconds" in r["error"], (s, r)
finally:
    appmod.time = real_time

# ---- n1: a refused value does not land; a set-point handler meets the same window
RH = {"source": "demo", "settable": True, "generator": {"kind": "constant", "value": 5}, "set_max": 9, "min_write_gap": 2}
assert call(admin, "PUT", "/api/config/point/rl-h", RH)[0] == 200
appmod.time = Clock(time.time() + 40)
try:
    last = lambda: next(p for p in call(admin, "GET", "/api/points")[1] if p["name"] == "rl-h")["last"]
    assert call(admin, "POST", "/api/points/rl-h/set", {"value": 6})[0] == 200
    s, r = call(admin, "POST", "/api/points/rl-h/set", {"value": 8})
    assert s == 422 and "at most once every 2 seconds" in r["error"], (s, r)
    app.tick(time.time())
    assert last() == 6, "the refused 8 did not land"
    appmod.time.now += 2.5
    app.handler_write("rl-h", 8, "handler")
    app.tick(time.time())
    assert last() == 8, "a set-point handler's write gets through past the window"
    app.handler_write("rl-h", 7, "handler")  # immediately after: the handler meets the same window
    app.tick(time.time())
    assert last() == 8, "a handler's write inside the window is refused"
finally:
    appmod.time = real_time

# ---- m2: the window is consumed once the device write lands, even if persisting the override then fails
M2 = {"source": "demo", "settable": True, "generator": {"kind": "constant", "value": 5}, "set_max": 9, "min_write_gap": 2}
assert call(admin, "PUT", "/api/config/point/rl-m2", M2)[0] == 200
appmod.time = Clock(time.time() + 50)
try:
    p, s = app.points()["rl-m2"]
    real_put = app.store.put

    def broken_put(kind, name, value, *a, **k):
        if kind == "override":
            raise RuntimeError("database is down")
        return real_put(kind, name, value, *a, **k)

    app.store.put = broken_put
    try:
        try:
            app.perform_write(p, s, 6, "tester")
        except RuntimeError:
            pass  # the device write happened; persisting the override failed
    finally:
        app.store.put = real_put
    s, r = call(admin, "POST", "/api/points/rl-m2/set", {"value": 7})
    assert s == 422 and "at most once every 2 seconds" in r["error"], (s, r)
finally:
    appmod.time = real_time

# ---- n2: deleting a point forgets its window, so a point recreated under the same name starts fresh
assert call(admin, "DELETE", "/api/config/point/rl-m2")[0] == 200
assert call(admin, "PUT", "/api/config/point/rl-m2", {**M2, "min_write_gap": 60})[0] == 200
assert call(admin, "POST", "/api/points/rl-m2/set", {"value": 6})[0] == 200  # the fresh point's first write

# ---- m1: two operator writes racing at the same instant: the engine lock and the re-check let exactly one through
RACE = {"source": "demo", "settable": True, "generator": {"kind": "constant", "value": 5}, "set_max": 9, "min_write_gap": 60}
assert call(admin, "PUT", "/api/config/point/rl-race", RACE)[0] == 200
appmod.time = Clock(time.time() + 60)
try:
    import threading

    _s = app.points()["rl-race"][1]
    _real_write = _s.write

    def _slow_write(name, value):  # hold the engine lock long enough for the second request to pass the outer check
        time.sleep(0.2)
        _real_write(name, value)

    _s.write = _slow_write
    try:
        for _ in range(20):
            app.last_write.pop("rl-race", None)  # a fresh window each repeat
            barrier, results = threading.Barrier(2), []

            def write(value):
                barrier.wait()
                results.append(call(admin, "POST", "/api/points/rl-race/set", {"value": value})[0])

            ts = [threading.Thread(target=write, args=(6,)), threading.Thread(target=write, args=(7,))]
            for t in ts:
                t.start()
            for t in ts:
                t.join(5)
            assert sorted(results) == [200, 422], results
    finally:
        _s.write = _real_write
finally:
    appmod.time = real_time

print("ok")
