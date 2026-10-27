"""Store behavior on every backend, migrations, purge, backup. Run: python tests/test_store.py
Set DURIAN_TEST_MYSQL_URL=mysql://user:pass@host:port/db to run the same checks against MySQL/MariaDB (the database is emptied first)."""
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.core import Event, Level  # noqa: E402
from durian.migrations import MIGRATIONS  # noqa: E402
from durian.store import Store  # noqa: E402

tmp = Path(tempfile.mkdtemp())


def check(st):
    assert st.version() == MIGRATIONS[-1][0] and st.version() >= 2
    # settings
    assert st.get_setting("nope", 7) == 7
    st.set_setting("retention_days", 30); st.set_setting("retention_days", 45); st.set_setting("nested", {"a": [1, 2]})
    assert st.get_setting("retention_days") == 45 and st.get_setting("nested") == {"a": [1, 2]}
    # history, rollups, ranges
    st.add_history([("p", 100, 10), ("p", 101, 20), ("p", 105, 60), ("p", 112, 5), ("q", 100, 1)])
    got = lambda r: [(int(x["ts"]), float(x["value"])) for x in st.series("p", 0, 10, r)]
    assert got("avg") == [(100, 30), (110, 5)] and got("min") == [(100, 10), (110, 5)] and got("max") == [(100, 60), (110, 5)]
    assert got("last") == [(100, 60), (110, 5)] and got("count") == [(100, 3), (110, 1)]
    assert [r["value"] for r in st.recent("p", 2)] == [60, 5] and len(st.history_range("p", 101, 105, 10)) == 2
    assert st.series("none", 0, 10, "avg") == []
    # integer-second bucketing on edges that are not round tens, and the one helper that spells it: MySQL floors
    # the division, SQLite truncates the cast, and every backend must answer through the same shared statement
    assert st._int_div("ts") == ("floor(ts / ?)" if st.kind == "mysql" else "cast(ts / ? as int)")
    assert [(int(x["ts"]), float(x["value"])) for x in st.series("p", 0, 7, "avg")] == [(98, 15), (105, 60), (112, 5)]
    # purge: batched, per point, with exclusions
    assert st.purge_history(103, limit=1) == 1                      # the limit really limits
    assert st.purge_history(103, except_points=("q",)) == 1         # p@101 goes (p@100 already gone); q is protected
    assert st.history_stats()["rows"] == 3 and st.purge_history(103, point="q") == 1 and st.purge_history(103) == 0
    assert [r["ts"] for r in st.recent("p", 10)] == [105, 112]
    s = st.history_stats(); assert s["rows"] == 2 and s["oldest"] == 105 and s["newest"] == 112 and s["points"] == 1
    # events
    e1 = st.add_event(Event("p", Level.URGENT, "one", 1.0)); e2 = st.add_event(Event("p", Level.WARNING, "two", 2.0))
    st.clear_event(e2, 3.0)
    assert st.ack_event(e1, "bob", 4.0) == 1 and st.ack_event(e1, "bob", 5.0) == 0
    assert [e["id"] for e in st.events()] == [e2, e1] and st.events()[1]["acked_by"] == "bob" and st.events()[0]["cleared_ts"] == 3.0
    # users, audit, objects
    st.add_user("ann", "h1", ["user"]); st.add_user("bob", "h2", ["superadmin"])
    assert st.user("ann")["roles"] == ["user"] and [u["name"] for u in st.users()] == ["ann", "bob"] and st.user("zed") is None
    assert st.set_hash("ann", "h3") == 1 and st.user("ann")["hash"] == "h3" and st.delete_user("bob") == 1 and st.delete_user("bob") == 0
    st.audit("ann", "x", "d"); assert st.audit_log(1)[0]["action"] == "x" and st.audit_log(1)[0]["user"] == "ann"
    st.put("point", "a", {"v": 1}); st.put("point", "a", {"v": 2}); st.put("point", "b", {"v": 3})
    assert st.get("point", "a") == {"v": 2} and [o["name"] for o in st.list("point")] == ["a", "b"] and st.delete("point", "a") == 1 and st.get("point", "a") is None
    # the shared upsert with three value columns: a second write updates the one row on either database
    st.set_failure("zed", 1, 0); st.set_failure("zed", 3, 12.5)
    assert st.failure_count() == 1 and st.failure("zed")["attempts"] == 3 and st.failure("zed")["locked_until"] == 12.5
    # a statement that fails raises, and the store stays usable afterwards (MySQL undoes its open transaction)
    raised = False
    try:
        st.run("select * from no_such_table")
    except Exception:
        raised = True
    assert raised and st.get_setting("retention_days") == 45
    # facts
    c = st.table_counts(); assert c["history"] == 2 and c["events"] == 2 and c["users"] == 1 and c["audit"] == 1
    assert st.size_bytes() >= 0 and "password" not in st.describe() and st.describe()
    # unicode survives
    st.put("point", "ü", {"t": "temperatuur °C ✓"}); assert st.get("point", "ü") == {"t": "temperatuur °C ✓"}


check(Store())                                                    # in memory
check(Store(str(tmp / "a.db")))                                   # a file, in WAL mode
if os.environ.get("DURIAN_TEST_MYSQL_URL"):
    url = os.environ["DURIAN_TEST_MYSQL_URL"]
    probe = Store(url)
    for t in ("history", "events", "users", "audit", "objects", "settings", "schema_version"):
        probe.run(f"drop table if exists {t}")
    probe.close()
    my = Store(url)
    check(my)
    assert Store(url).version() == my.version()                    # opening again applies nothing twice
    print("mysql ok")

# ---- a database made before migrations existed upgrades in place and keeps its data
old = tmp / "old.db"
con = sqlite3.connect(old)
con.executescript("""
create table history(point text, ts real, value real);
create index h_idx on history(point, ts);
create table events(id integer primary key, point text, level int, message text, ts real, acked_ts real);
create table users(name text primary key, hash text not null, roles text not null, created real);
create table audit(id integer primary key, ts real, user text, action text, detail text);
create table objects(kind text, name text, body text not null, primary key(kind, name));
insert into history values ('legacy', 1, 2.5);
insert into users values ('root', 'hash', '["superadmin"]', 1);
""")
con.commit(); con.close()
up = Store(str(old))
assert up.version() == MIGRATIONS[-1][0] and up.recent("legacy")[0]["value"] == 2.5 and up.user("root")["roles"] == ["superadmin"]
up.set_setting("k", 1); assert up.get_setting("k") == 1
up.close()
assert Store(str(old)).version() == MIGRATIONS[-1][0]            # a second start changes nothing

# ---- backup: consistent copy of a live database, refused where unsupported
live = Store(str(tmp / "live.db"))
live.add_history([("p", float(i), float(i)) for i in range(1000)])
size = live.backup_to(str(tmp / "copy.db"))
copy = Store(str(tmp / "copy.db"))
assert size > 0 and copy.history_stats()["rows"] == 1000 and copy.version() == live.version()
try:
    Store().backup_to(str(tmp / "x.db"))
    raise AssertionError("memory databases cannot be backed up")
except NotImplementedError:
    pass
print("ok")
