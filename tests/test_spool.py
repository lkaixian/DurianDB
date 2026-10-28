"""Readings survive a database that fails and a process that stops. Run: python tests/test_spool.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path  # noqa: E402

from _support import new_store  # noqa: E402
from durian.app import App  # noqa: E402
from durian.spool import Spool  # noqa: E402
from durian.store import Store  # noqa: E402

admin = {"name": "admin", "roles": {"superadmin"}}
tmp = Path(tempfile.mkdtemp())
R = lambda n, t0=1000: [("p", t0 + i, float(i), 0) for i in range(n)]  # noqa: E731

# --- Spool alone: oldest first, batches, idempotent after a crash, corrupt tail skipped
sp = Spool(tmp / "a.ndjson")
sp.append(R(3, 10)); sp.append(R(3, 20))
assert sp.rows == 6
got = []
sp.replay(lambda rows: got.append(list(rows)), batch=4)
assert [len(g) for g in got] == [4, 2] or sum(len(g) for g in got) == 6, got
flat = [r for g in got for r in g]
assert [r[1] for r in flat] == [10, 11, 12, 20, 21, 22], flat
assert sp.rows == 0 and not list(tmp.glob("a.ndjson*")), list(tmp.glob("a.ndjson*"))

sp.append(R(2, 30))
def boom(rows): raise OSError("down")  # noqa: E704
try:
    sp.replay(boom); assert False
except OSError:
    pass
assert sp.rows == 2, "a failed replay keeps the rows"
sp2 = Spool(tmp / "a.ndjson")  # a restart finds them
assert sp2.rows == 2

with open(tmp / "a.ndjson", "ab") as f:
    f.write(b'["p", 40, 1.0')  # power cut mid-line
sp3 = Spool(tmp / "a.ndjson")
got = []
sp3.replay(lambda rows: got.extend(rows))
assert [r[1] for r in got] == [30, 31], got
assert sp3.rows == 0

# bounded: oldest segment goes, drops are counted
small = Spool(tmp / "b.ndjson", max_bytes=2000)
for i in range(60):
    small.append(R(5, i * 10))
size = sum(f.stat().st_size for f in tmp.glob("b.ndjson*"))
assert size <= 2000 + 600, size
assert small.dropped > 0 and small.rows + small.dropped == 300, (small.rows, small.dropped)
got = []
small.replay(lambda rows: got.extend(rows))
assert got[-1][1] == 590 + 4, "the newest rows are the ones kept"

# --- App: store down -> spool; back up -> replayed
store = new_store()
app = App(store, spool=tmp / "spool.ndjson")
real = store.add_history
store.add_history = boom
app.pending = R(10)
app.tick(2000)
assert app.pending == [] and app.spool.rows >= 10, (len(app.pending), app.spool.rows)
h = app.handle("GET", "/api/health", {}, None, admin)[1]
assert h["spool"]["rows"] >= 10 and h["spool"]["dropped"] == 0 and h["ok"] is not None, h
assert app.sys_event is not None, "db trouble is still raised plainly"
store.add_history = real
app.tick(2002)
assert app.spool.rows == 0 and not (tmp / "spool.ndjson").exists()
assert len(store.run("select * from history where point = 'p'").fetchall()) == 10
h = app.handle("GET", "/api/health", {}, None, admin)[1]
assert "spool" not in h, h

# --- stop spools what is pending; a new App replays it
app.pending = R(4, 5000)
app.stop()
assert app.pending == [] and (tmp / "spool.ndjson").exists()
app2 = App(store, spool=tmp / "spool.ndjson")
assert app2.spool.rows == 4
app2.tick(3000)
assert app2.spool.rows == 0
assert len(store.run("select * from history where point = 'p'").fetchall()) == 14

# --- spool write fails too: rows stay in memory (bounded by PENDING_MAX as before)
app2.spool.append = boom
store.add_history = boom
app2.pending = R(3, 9000)
app2.tick(3002)
assert len(app2.pending) == 3

# --- :memory: store has no spool
assert App(Store()).spool is None

print("ok")
