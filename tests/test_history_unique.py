"""One history row per (point, ts): a replayed backfill does not double-count, a duplicate inside a batch
does not abort it, and the migration folds duplicates already stored (C19). Run: python tests/test_history_unique.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.store import Store  # noqa: E402
from _support import new_store  # noqa: E402

st = new_store()
st.add_history([("p", 100.0, 10.0), ("p", 101.0, 20.0)])
st.add_history([("p", 100.0, 10.0), ("p", 101.0, 20.0)])  # the same backfill replayed
assert st.history_stats()["rows"] == 2, st.history_stats()
assert st.history_summary("p", 0, 200)["n"] == 2

# a duplicate inside one batch is skipped, and the rest of the batch still lands
st.add_history([("q", 1.0, 1.0), ("q", 1.0, 1.0), ("q", 2.0, 2.0), ("p", 100.0, 99.0), ("p", 102.0, 30.0)])
assert [r["ts"] for r in st.history_range("q", 0, 10, 10)] == [1.0, 2.0]
assert [r["ts"] for r in st.history_range("p", 0, 200, 10)] == [100.0, 101.0, 102.0]
assert st.history_range("p", 100, 100, 10)[0]["value"] == 10.0, "the first write stands"
assert st.series("p", 0, 1000, "sum")[0]["value"] == 60.0, "rollups count each sample once"

# the migration folds duplicates a database already holds, then enforces uniqueness
path = str(Path(tempfile.mkdtemp()) / "old.db")
old = Store(path)
old.add_history([("a", 1.0, 5.0), ("a", 2.0, 6.0)])
old.run("drop index h_uniq")
old.run("insert into history(point, ts, value, quality) values ('a', 1.0, 5.0, 0)")
old.run("insert into history(point, ts, value, quality) values ('a', 1.0, 5.0, 0)")
old.run("delete from schema_version where version = (select max(version) from schema_version)")
old.close()
new = Store(path)
assert new.history_stats()["rows"] == 2, new.history_stats()
new.add_history([("a", 1.0, 5.0)])
assert new.history_stats()["rows"] == 2

# _index swallows only "already exists"; a MySQL "Duplicate entry" means the unique index FAILED
from durian.migrations import _index, v11_history_unique  # noqa: E402


class Ddl:
    def __init__(self, msg): self.msg = msg
    def ddl(self, sql): raise Exception(self.msg)


_index(Ddl("Duplicate key name 'h_uniq'"), "x"); _index(Ddl("index h_uniq already exists"), "x")
try:
    _index(Ddl("Duplicate entry 'a-1' for key 'h_uniq'"), "x")
except Exception as e:
    assert "Duplicate entry" in str(e)
else:
    raise AssertionError("a failed unique index must not be swallowed")

# a crash between the delete and the re-insert loses no sample
crash = Store(str(Path(tempfile.mkdtemp()) / "crash.db"))
crash.run("drop index h_uniq")
for _ in range(2):
    crash.run("insert into history(point, ts, value, quality) values ('c', 1.0, 5.0, 0)")
real_run = crash.run
crash.run = lambda sql, *a: (_ for _ in ()).throw(RuntimeError("boom")) if sql.startswith("insert") else real_run(sql, *a)
try:
    v11_history_unique(crash)
except RuntimeError:
    pass
crash.run = real_run
assert crash.history_stats()["rows"] == 2, crash.history_stats()
print("ok")
