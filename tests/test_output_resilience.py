"""A failed reload keeps the old engine running; MQTT/Sparkplug publishers report health on a bounded queue;
stopping the outputs never waits on a hung receiver. Run: python tests/test_output_resilience.py"""
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _support import new_store  # noqa: E402
from durian import outputs as outmod  # noqa: E402
from durian.app import App  # noqa: E402
from durian.core import Event, Level  # noqa: E402

admin = {"name": "admin", "roles": {"superadmin"}}

# ---- (a) a reload that cannot build leaves the old engine and outputs running, untouched
store = new_store()
app = App(store)
app.handle("PUT", "/api/config/source/v", {}, {"type": "virtual", "period": 60}, admin)
old_engine, old_outputs = app.engine, app.outputs
stopped = []
for s in old_engine.sources:
    s.stop = lambda s=s: stopped.append(s.name)
real_stop = old_outputs.stop
old_outputs.stop = lambda: stopped.append("outputs")
real_list, armed = store.list, [True]


def flaky_list(kind, *a, **k):
    if armed[0]:
        armed[0] = False
        raise OSError("database is locked")
    return real_list(kind, *a, **k)


store.list = flaky_list
try:
    app.reload()
except OSError:
    pass
else:
    raise AssertionError("the build error must propagate")
store.list = real_list
assert app.engine is old_engine and app.outputs is old_outputs, "the old engine and outputs stay in place"
assert stopped == [], f"the old sources and outputs were stopped before the new engine existed: {stopped}"
old_outputs.stop = real_stop
app.reload()  # and a good reload still swaps and stops the old ones
assert app.engine is not old_engine

# ---- (b) MQTT publisher: bounded paho queue, and the connection state reaches the Delivery column
class Fake:
    def __init__(self):
        self.on_connect = self.on_disconnect = self.on_connect_fail = None
        self.max = None
        self.published = []

    def max_queued_messages_set(self, n): self.max = n

    def connect_async(self, *a, **k): pass

    def loop_start(self): pass

    def loop_stop(self): pass

    def disconnect(self): pass

    def publish(self, *a, **k): self.published.append(a)


fake = Fake()
outmod.load_creds = lambda path: {"host": "h", "port": 1}
outmod.tls_client = lambda c: fake
st = new_store()
st.put("publisher", "m", {"type": "mqtt", "creds": "x", "prefix": "out/", "points": ["*"]})
o = outmod.Outputs(st)
assert fake.max == 1000, f"the paho queue must be bounded, got {fake.max}"
fake.on_disconnect(fake, None, None, 7, None)
f = {x["name"]: x for x in o.faults()}
assert "m" in f and "broker" in f["m"]["last_error"] and "Error" not in f["m"]["last_error"], o.status
fake.on_connect(fake, None, None, 0, None)
assert o.faults() == [] and o.status["m"]["last_ok"], o.status
fake.on_connect_fail(fake, None)
assert o.faults(), "a broker that never answers is a fault"
o.stop()

# the Sparkplug publisher keeps its own on_connect (births) and also reports health
fake = Fake()
st = new_store()
st.put("publisher", "sp", {"type": "sparkplug", "creds": "x", "group": "G", "node": "n", "points": ["*"]})
o = outmod.Outputs(st)
assert fake.max == 1000
fake.on_connect(fake, None, None, 0, None)
assert fake.published, "the Sparkplug birth must still be sent on connect"
assert o.status["sp"]["last_ok"], o.status
fake.on_disconnect(fake, None, None, 1, None)
assert [x["name"] for x in o.faults()] == ["sp"]
o.stop()

# ---- (c) stop() does not wait on a full queue behind a hung receiver
hung = socket.socket()
hung.bind(("127.0.0.1", 0))
hung.listen(5)
conns = []
threading.Thread(target=lambda: [conns.append(hung.accept()) for _ in range(3)], daemon=True).start()
o = outmod.Outputs(new_store())
for i in range(1100):
    o._put(("webhook", f"http://127.0.0.1:{hung.getsockname()[1]}/h", {"i": i}), "hung")
time.sleep(0.3)  # the worker is now stuck on the first request
assert o.q.full(), "the queue must be full for this to prove anything"
t0 = time.monotonic()
o.stop()
took = time.monotonic() - t0
assert took < 2, f"stop() took {took:.1f}s behind a hung receiver"
t0 = time.monotonic()
o.worker.join(7)
assert not o.worker.is_alive(), "the worker must exit once its current delivery gives up"

print("ok")
