"""A slow tick must not stall the console: the engine has its own lock, separate from the database's.
Run: python tests/test_engine_lock.py"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _support import new_store  # noqa: E402
from durian.app import App  # noqa: E402

store = new_store()
store.put("source", "v", {"type": "virtual", "points": [{"name": "gen", "kind": "sine", "period": 60, "min": 0, "max": 10}]})
store.put("point", "gen", {"source": "v"})
app = App(store)
admin = {"name": "admin", "roles": {"superadmin"}}
app.reload()

in_tick = threading.Event()
release = threading.Event()
real = app.engine.tick


def slow_tick(ts):
    in_tick.set()
    release.wait(5)
    return real(ts)


app.engine.tick = slow_tick
t = threading.Thread(target=app.tick, daemon=True)
t.start()
assert in_tick.wait(2), "the tick did not start"

# while the tick is stuck, a request that only reads the database answers at once
started = time.monotonic()
assert app.handle("GET", "/api/config/point/gen", {}, None, admin)[0] == 200
assert app.handle("GET", "/api/config/source", {}, None, admin)[0] == 200
assert time.monotonic() - started < 1.0, "a slow tick blocked a query"

# a reload, which rebuilds the engine, still waits for the tick: the two never run at once
done = threading.Event()
threading.Thread(target=lambda: (app.reload(), done.set()), daemon=True).start()
assert not done.wait(0.2), "reload ran while a tick was in progress"
release.set()
assert done.wait(5) and t.join(5) is None, "the reload did not follow the tick"

# and the engine is still the one that was rebuilt, with the tick's values flushed
assert [s.name for s in app.engine.sources] == ["v"]
print("ok")
