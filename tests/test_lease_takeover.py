"""A lease held by a dead process on this host is taken at once; any other holder is named with the time left.
Run: python tests/test_lease_takeover.py"""
import logging
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _support import new_store  # noqa: E402
from durian.app import App  # noqa: E402
from durian.core import pid_alive  # noqa: E402

HOST = socket.gethostname()
p = subprocess.Popen([sys.executable, "-c", "pass"])
p.wait()
dead = p.pid
assert not pid_alive(dead) and pid_alive(os.getpid())

records = []


class Catch(logging.Handler):
    def emit(self, r):
        records.append(r)


logging.getLogger("durian.app").addHandler(Catch())
logging.getLogger("durian.app").setLevel(logging.INFO)


def plant(store, owner, left):
    store.run("delete from tick_lease")
    store.run("insert into tick_lease(id, owner, expires) values (1,?,?)", (owner, time.time() + left))


def texts():
    return [r.getMessage() for r in records]


store = new_store()
app = App(store)
parts = app.lease_owner.split(":")
assert parts[0] == HOST and parts[1] == str(os.getpid()) and len(parts) == 3, app.lease_owner

# dead pid on this host: taken at once, one sentence
plant(store, f"{HOST}:{dead}:abc", 25)
app.tick()
assert app.lease_held and store.lease()["owner"] == app.lease_owner
assert any("took over" in t and str(dead) in t for t in texts()), texts()

# a live pid (this test) with another token: not taken, named, with the seconds left
records.clear()
plant(store, f"{HOST}:{os.getpid()}:other", 17)
app.lease_warned = False
app.tick()
app.tick()
assert not app.lease_held and store.lease()["owner"].endswith(":other")
w = [t for t in texts() if "not polling" in t]
assert len(w) == 1 and f"process {os.getpid()} on {HOST}" in w[0] and "expires in 1" in w[0] and "take over" in w[0], w

# it keeps retrying: when the lease expires it takes over and says so once
records.clear()
store.run("update tick_lease set expires=? where id=1", (time.time() - 1,))
app.tick()
assert app.lease_held and len([t for t in texts() if "took over" in t]) == 1, texts()
app.tick()
assert len([t for t in texts() if "took over" in t]) == 1

# another host: never judged dead
records.clear()
app2 = App(store)
plant(store, f"elsewhere:{dead}:abc", 20)
app2.tick()
assert not app2.lease_held and any("elsewhere" in t for t in texts()), texts()

# an owner id from before this change (no host or pid) is waited out, not guessed at
plant(store, "0123456789abcdef", 20)
app2.tick()
assert not app2.lease_held
print("ok")
