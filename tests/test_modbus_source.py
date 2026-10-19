"""Modbus source through the whole stack: config validation, polling, scaling, failures, writes.
Run: python tests/test_modbus_source.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.modbus import encode  # noqa: E402
from durian.modbus_sim import Slave  # noqa: E402
from durian.store import Store  # noqa: E402
from _support import new_store  # noqa: E402

app = App(new_store())
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
from durian.auth import hash_password  # noqa: E402
app.store.add_user("op", hash_password("operator-password-1"), ["user"])
op = app.auth.identity(app.auth.login("op", "operator-password-1"))


def call(ident, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, ident)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


def put(kind, name, body):
    return call(admin, "PUT", f"/api/config/{kind}/{name}", body)


def props(r):
    return {e["property"] for e in r[1]["errors"]}


# ---- validation: every problem reported at once
r = put("source", "bad", {"type": "modbus", "link": {"kind": "serial", "port": "", "baudrate": 1234, "parity": "X"},
                          "framing": "tcp", "retries": 99, "timeout": 0, "backoff_scale": 1, "monitor": "yes"})
assert r[0] == 422 and {"link.port", "link.baudrate", "link.parity", "framing", "retries", "timeout", "backoff_scale", "monitor"} <= props(r), props(r)
assert put("source", "bad", {"type": "modbus"})[0] == 422 and put("source", "bad", {"type": "modbus", "link": {"kind": "tcp", "host": "", "port": 0}})[0] == 422

srv = Slave("tcp").start()
dev = srv.devices[1]
dev.holding[0] = 1234
dev.holding[10:12] = encode(21.5, "float32")                  # big/big
dev.holding[20:22] = encode(-3.25, "float32", word_order="little")
dev.holding[30:32] = encode(-70000, "int32", byte_order="little")
dev.holding[40] = 0b1000                                      # bit 3 set
dev.holding[50] = 600                                          # scaled: 600 * 0.1 - 40 = 20.0
dev.input[0] = 77
dev.coils[5], dev.discrete[6] = True, True
SRC = {"type": "modbus", "link": {"kind": "tcp", "host": "127.0.0.1", "port": srv.port}, "timeout": 0.3, "retries": 0,
       "period": 60, "backoff_scale": 2, "backoff_max": 30, "monitor": True}
assert put("source", "plc", SRC)[0] == 200
M = lambda **k: {"unit": 1, **k}
POINTS = {"u16": M(range="holding", address=0, type="uint16"), "f32": M(range="holding", address=10, type="float32"),
          "f32sw": M(range="holding", address=20, type="float32", word_order="little"),
          "i32bs": M(range="holding", address=30, type="int32", byte_order="little"),
          "bit3": M(range="holding", address=40, type="bool", bit=3), "bit2": M(range="holding", address=40, type="bool", bit=2),
          "scaled": M(range="holding", address=50, type="uint16", multiplier=0.1, additive=-40),
          "inreg": M(range="input", address=0, type="uint16"), "coil5": M(range="coil", address=5),
          "disc6": M(range="discrete", address=6), "other-unit": {"unit": 2, "range": "holding", "address": 0, "type": "uint16"},
          "nowhere": M(range="holding", address=998, type="float64")}   # needs 4 registers, the map ends at 1000: the device refuses it
for n, m in POINTS.items():
    r = put("point", n, {"source": "plc", "modbus": m, "settable": n in ("u16", "scaled", "coil5", "f32")})
    assert r[0] == 200, (n, r)

# point validation
for bad, field in ((M(range="holding", address=0, type="nope"), "modbus.type"), (M(range="holding", address=65535, type="uint32"), "modbus.address"),
                   (M(range="holding", address=0, type="uint16", byte_order="mixed"), "modbus.byte_order"),
                   (M(range="holding", address=0, type="uint16", multiplier=0), "modbus.multiplier"),
                   (M(range="coil", address=0, type="uint16"), "modbus.type"), ({"unit": 0, "range": "holding", "address": 0, "type": "uint16"}, "modbus.unit"),
                   (M(range="holding", address=0, type="bool", bit=16), "modbus.bit"), (M(range="nope", address=0), "modbus.range")):
    assert field in props(put("point", "p", {"source": "plc", "modbus": bad})), (bad, field)
assert "modbus" in props(put("point", "p", {"source": "plc"}))
assert "settable" in props(put("point", "p", {"source": "plc", "modbus": M(range="input", address=0, type="uint16"), "settable": True}))
assert "settable" in props(put("point", "p", {"source": "plc", "modbus": M(range="holding", address=0, type="bool"), "settable": True}))

# ---- polling: every type, order, scaling
src = next(s for s in app.engine.sources if s.name == "plc")
time.sleep(0.3)  # let the background thread's first cycle finish before we drive one ourselves
src.cycle()
app.tick(time.time())
vals = {p["name"]: p["last"] for p in call(admin, "GET", "/api/points")[1]}
assert vals["u16"] == 1234 and vals["f32"] == 21.5 and vals["f32sw"] == -3.25 and vals["i32bs"] == -70000, vals
assert vals["bit3"] == 1.0 and vals["bit2"] == 0.0 and abs(vals["scaled"] - 20.0) < 1e-9, vals
assert vals["inreg"] == 77 and vals["coil5"] == 1.0 and vals["disc6"] == 1.0 and vals["other-unit"] == 0, vals
assert vals["nowhere"] is None and vals["plc-unit1-online"] == 1.0 and vals["plc-unit2-online"] == 1.0
assert "1 point(s) rejected" in src.error and "nowhere" in src.error and "illegal data address" in src.error  # only that point failed
def requests_for_one_cycle(source):
    before = len([r for r in srv.requests if r[0] == 1])
    source.cycle()
    return len([r for r in srv.requests if r[0] == 1]) - before


# contiguous-only: coil, discrete, input, and 7 separate holding reads (bit2 and bit3 share one register, so they share a read)
assert requests_for_one_cycle(src) == 10
assert put("source", "plc", {**SRC, "contiguous_only": False})[0] == 200  # nearby registers are now read together
src = next(s for s in app.engine.sources if s.name == "plc")
time.sleep(0.3)
assert requests_for_one_cycle(src) == 5  # coil, discrete, input, holding 0..51, holding 998
app.tick(time.time())
again = {p["name"]: p["last"] for p in call(admin, "GET", "/api/points")[1]}
assert again["f32"] == 21.5 and again["i32bs"] == -70000 and again["bit3"] == 1.0  # merging must not change any value

# ---- writes: scaling is reversed, coils work, bad writes are refused and reported
assert call(op, "POST", "/api/points/u16/set", {"value": 4321})[0] == 403  # operator has no set role on this point yet
assert put("point", "u16", {"source": "plc", "modbus": POINTS["u16"], "settable": True, "set_roles": ["user"]})[0] == 200
src = next(s for s in app.engine.sources if s.name == "plc")
assert call(op, "POST", "/api/points/u16/set", {"value": 4321})[0] == 200 and dev.holding[0] == 4321
put("point", "scaled", {"source": "plc", "modbus": POINTS["scaled"], "settable": True, "set_roles": ["user"]})
assert call(op, "POST", "/api/points/scaled/set", {"value": 25.0})[0] == 200 and dev.holding[50] == 650   # (25 + 40) / 0.1
put("point", "coil5", {"source": "plc", "modbus": POINTS["coil5"], "settable": True, "set_roles": ["user"]})
assert call(op, "POST", "/api/points/coil5/set", {"value": 0})[0] == 200 and dev.coils[5] is False
put("point", "f32", {"source": "plc", "modbus": POINTS["f32"], "settable": True, "set_roles": ["user"]})
assert call(op, "POST", "/api/points/f32/set", {"value": 1.5})[0] == 200 and dev.holding[10:12] == encode(1.5, "float32")
assert call(op, "POST", "/api/points/u16/set", {"value": 70000})[0] == 502       # does not fit in 16 bits
assert call(op, "POST", "/api/points/u16/set", {"value": None})[0] == 502        # cannot be released
srv.drop_next = 0

# ---- failure: the link dies, the source backs off, monitors go to zero, then recovers
src = next(s for s in app.engine.sources if s.name == "plc")
srv.stop()
src.cfg["period"] = 1.0  # so the growth is visible below the cap
src.next_try = 0
src.cycle()
assert src.connected is False and src.fails == 1 and "retry in" in src.error, src.error
wait1 = src.next_try - time.time()
src.next_try = 0
src.cycle()
wait2 = src.next_try - time.time()
assert src.fails == 2 and 1.9 < wait1 < 2.1 and 3.9 < wait2 < 4.1, (wait1, wait2)  # period x scale^failures: 2 s then 4 s
src.cfg["backoff_max"] = 3
src.next_try = 0
src.cycle()
assert 2.9 < src.next_try - time.time() < 3.1  # capped
app.tick(time.time())
assert {p["name"]: p["last"] for p in call(admin, "GET", "/api/points")[1]}["plc-unit1-online"] == 0.0
srv2 = Slave("tcp", srv.port).start()
srv2.devices[1].holding[0] = 99
src.next_try = 0
src.cycle()
assert src.connected is True and src.fails == 0, src.error
app.tick(time.time())
assert {p["name"]: p["last"] for p in call(admin, "GET", "/api/points")[1]}["u16"] == 99
srv2.stop()

# ---- RTU over a serial URL, ASCII, and the point of being a plain editable config
for framing in ("rtu", "ascii"):
    s3 = Slave(framing).start()
    s3.devices[1].holding[0] = 555
    cfg = {"type": "modbus", "link": {"kind": "serial", "port": f"socket://127.0.0.1:{s3.port}", "baudrate": 9600}, "framing": framing,
           "timeout": 0.3, "retries": 0, "period": 60}
    assert put("source", "serial-" + framing, cfg)[0] == 200
    assert put("point", "s-" + framing, {"source": "serial-" + framing, "modbus": M(range="holding", address=0, type="uint16")})[0] == 200
    s = next(x for x in app.engine.sources if x.name == "serial-" + framing)
    time.sleep(0.3)
    s.cycle()
    app.tick(time.time())
    assert {p["name"]: p["last"] for p in call(admin, "GET", "/api/points")[1]}["s-" + framing] == 555, framing
    s3.stop()
print("ok")
