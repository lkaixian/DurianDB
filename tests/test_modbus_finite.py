"""A float register holding NaN or infinity is a failed read, not a value. Run: python tests/test_modbus_finite.py"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.modbus import encode  # noqa: E402
from _support import new_store  # noqa: E402
from durian.app import App  # noqa: E402
from durian.modbus_sim import Slave  # noqa: E402

srv = Slave("tcp").start()
dev = srv.devices[1]
dev.holding[0:2] = encode(float("nan"), "float32")
dev.holding[2:4] = encode(float("inf"), "float32")
dev.holding[4] = 65535  # scaled so far it overflows to infinity
dev.holding[6:8] = encode(20.0, "float32")

store = new_store()
store.put("source", "plc", {"type": "modbus", "link": {"kind": "tcp", "host": "127.0.0.1", "port": srv.port},
                            "timeout": 0.3, "retries": 0, "period": 60, "monitor": True})
for n, m in {"nan": {"address": 0, "type": "float32"}, "inf": {"address": 2, "type": "float32"},
             "huge": {"address": 4, "type": "uint16", "multiplier": 1e308},
             "fine": {"address": 6, "type": "float32"}}.items():
    store.put("point", n, {"source": "plc", "modbus": {"unit": 1, "range": "holding", **m}})

app = App(store)
app.reload()
src = next(s for s in app.engine.sources if s.name == "plc")
time.sleep(0.2)  # let the thread's first cycle and the reload settle
src.next_try = 0
src.cycle()

vals = src.poll(time.time())
for name in ("nan", "inf", "huge"):
    assert vals[name] == (None, vals[name][1], 2), (name, vals[name])  # BAD quality, no value: nothing reaches history
assert vals["fine"][0] == 20.0, vals["fine"]
assert "3 point(s) rejected" in src.error and "nan" in src.error and "inf" in src.error, src.error
assert src.connected is True, "one unreadable value does not take the link down"

# the cycle does not raise, so the source stays online and only the bad points are marked
app.tick(time.time())
assert app.store.recent("nan") == [], "a rejected reading is never stored"
srv.stop()
print("ok")
