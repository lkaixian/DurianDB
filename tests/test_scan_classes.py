"""Scan classes: a point may poll at its own cadence instead of every source period.

Spec pinned here (F3, docs/research/10 row 2):
- `scan` is a whole number from 1 to 16; absent means 1.  16 is the ceiling because a scan class
  is a small integer multiple of the source period: 1 is today's every-poll, and an integer keeps
  the phase exact, while 16 catches a typo such as 1000 at save time instead of silently polling
  once a day.  0, negatives, fractions, text and out-of-range values are refused, naming `scan`.
- A point with scan n is read on the source's first poll after it is configured and every n-th
  poll after that: polls 1, 1+n, 1+2n...  Every point has a fresh value right after a reload, and
  the phase needs no state carried across one.
- A poll a point skips is not a failed read: its value and quality stand, and the no-update and
  source-down machinery stay quiet while it is inside its own cadence.

The driver is the Modbus simulator the other Modbus tests use: it logs one PDU per request on
loopback, so each point's reads are countable without hardware.  Run: python tests/test_scan_classes.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.core import Quality  # noqa: E402
from durian.modbus_sim import Slave  # noqa: E402
from _support import new_store  # noqa: E402


def ident(app):
    app.auth.bootstrap("admin-password-123")
    return app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(app, who, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, who)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


def props(r):
    return {e["property"] for e in r[1]["errors"]}


# ---- validation: scan 1..16 saves; anything else is refused by property name
vapp = App(new_store())
vadmin = ident(vapp)
vput = lambda kind, name, body: call(vapp, vadmin, "PUT", f"/api/config/{kind}/{name}", body)


def point(scan=None):
    body = {"source": "plc", "modbus": {"unit": 1, "range": "holding", "address": 0, "type": "uint16"}}
    if scan is not None:
        body["scan"] = scan
    return body


# port 1 has nothing listening: only config validation is exercised here
assert vput("source", "plc", {"type": "modbus", "link": {"kind": "tcp", "host": "127.0.0.1", "port": 1},
                              "timeout": 0.05, "retries": 0, "period": 60})[0] == 200
assert vput("point", "absent", point())[0] == 200  # no scan keeps today's every-poll behavior
assert vput("point", "one", point(1))[0] == 200
for n in (2, 4, 16):
    assert vput("point", f"scan-{n}", point(n))[0] == 200, n
for bad in (0, -1, 1.5, "fast", 17, 10 ** 9):
    r = vput("point", "bad", point(bad))
    assert r[0] == 422 and "scan" in props(r), (bad, r)

# ---- cadence: A (scan 1) every poll, B (scan 4) on polls 1, 5, 9...
srv = Slave("tcp").start()
srv.devices[1].holding[0] = 11
srv.devices[1].holding[100] = 22
app = App(new_store())
admin = ident(app)
put = lambda kind, name, body: call(app, admin, "PUT", f"/api/config/{kind}/{name}", body)

assert put("source", "plc", {"type": "modbus", "link": {"kind": "tcp", "host": "127.0.0.1", "port": srv.port},
                             "timeout": 0.5, "retries": 0, "period": 60})[0] == 200
assert put("point", "A", {"source": "plc", "scan": 1,
                          "modbus": {"unit": 1, "range": "holding", "address": 0, "type": "uint16"}})[0] == 200
assert put("point", "B", {"source": "plc", "scan": 4,
                          "modbus": {"unit": 1, "range": "holding", "address": 100, "type": "uint16"},
                          "detectors": [{"kind": "NO_UPDATE", "seconds": 1000, "level": "WARNING"}]})[0] == 200

# Each valid PUT reloads the engine, so the source built for A alone may already have polled once.
# reload() stops that source before returning, so the log at this point is a clean boundary: drop
# everything before it and keep the final source's first cycle.
src = next(s for s in app.engine.sources if s.name == "plc")
log_base = len(srv.requests)
deadline = time.monotonic() + 3
while src.connected is None and time.monotonic() < deadline:
    time.sleep(0.01)
assert src.connected is True, src.error
del srv.requests[:log_base]


def starts():
    """The holding-register start address of every read the fake slave answered."""
    return [int.from_bytes(pdu[1:3], "big") for unit, pdu in srv.requests if unit == 1 and pdu[0] == 3]


# poll 1 (the background thread's first cycle) reads both points: the phase starts fresh
assert starts() == [0, 100], starts()
app.tick(time.time())  # apply that first cycle, so B has its starting value and receive time
b_recv = src.points["B"].last_recv
assert src.points["B"].quality == Quality.GOOD and src.points["B"].last == 22.0

pattern = []
for i in range(1, 9):  # manual polls 2..9
    before = len(starts())
    src.cycle()
    pattern.append(starts()[before:])
    if i == 3:
        # manual polls 2, 3 and 4 skipped B: it was last read on poll 1, one full scan before its
        # next read.  Four source periods (240 s) have passed, inside B's configured 1000 s.
        # A skipped poll must not look like a failed read or a silent source.
        app.tick(time.time() + 240)
        b = src.points["B"]
        assert b.quality == Quality.GOOD and b.last == 22.0, (b.quality, b.last)
        assert b.last_recv == b_recv, "a skipped poll changed when B was last received"
        assert src.connected is True and src.fails == 0 and src.error is None, (src.connected, src.error)
        assert src.down_reason is None and app.engine.down.get("plc") is None
        assert not any(ev.point == "B" for ev in app.open_events), [ev.point for ev in app.open_events]

# polls 2,3,4 read A only; poll 5 reads both; 6,7,8 read A only; poll 9 reads both
assert pattern == [[0], [0], [0], [0, 100], [0], [0], [0], [0, 100]], pattern
assert sum(got.count(0) for got in pattern) == 8, pattern
assert sum(got.count(100) for got in pattern) == 2, pattern

srv.stop()
print("ok")
