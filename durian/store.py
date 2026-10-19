"""SQLite persistence: time-series history, events, users, audit trail, and config objects.

Config objects (sources, points, handlers, watch lists) live in one table as JSON, keyed by
(kind, name). Validation belongs to the layer that writes them, not to the store.
"""
import json
import sqlite3
import threading
import time

SCHEMA = """
create table if not exists history(point text, ts real, value real);
create index if not exists h_idx on history(point, ts);
create table if not exists events(id integer primary key, point text, level int, message text,
  ts real, cleared_ts real, acked_ts real, acked_by text);
create table if not exists users(name text primary key, hash text not null, roles text not null, created real);
create table if not exists audit(id integer primary key, ts real, user text, action text, detail text);
create table if not exists objects(kind text, name text, body text not null, primary key(kind, name));
"""
ROLLUPS = {"avg": "avg(value)", "min": "min(value)", "max": "max(value)", "count": "count(*)"}


class Store:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()  # also guards the engine's runtime objects, see server
        self.db.executescript(SCHEMA)

    def q(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            cols = [c[0] for c in cur.description or []]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def run(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # history
    def add_history(self, rows):
        with self.lock:
            self.db.executemany("insert into history values (?,?,?)", rows)
            self.db.commit()

    def recent(self, point, n=60):
        return self.q("select ts, value from history where point=? order by ts desc limit ?", (point, n))[::-1]

    def series(self, point, since, bucket, rollup):
        """Bucketed history; bucket start time is returned as ts. Caller validates the arguments."""
        if rollup == "last":
            return self.q("select b * ? as ts, value from (select cast(ts / ? as int) b, value, "
                          "row_number() over (partition by cast(ts / ? as int) order by ts desc) rn "
                          "from history where point = ? and ts >= ?) where rn = 1 order by b",
                          (bucket, bucket, bucket, point, since))
        return self.q(f"select cast(ts / ? as int) * ? as ts, {ROLLUPS[rollup]} as value from history "
                      "where point = ? and ts >= ? group by cast(ts / ? as int) order by ts",
                      (bucket, bucket, point, since, bucket))

    # events
    def add_event(self, ev):
        return self.run("insert into events(point,level,message,ts) values (?,?,?,?)",
                        (ev.point, int(ev.level), ev.message, ev.ts)).lastrowid

    def clear_event(self, event_id, ts):
        self.run("update events set cleared_ts=? where id=? and cleared_ts is null", (ts, event_id))

    def ack_event(self, event_id, user, ts):
        return self.run("update events set acked_ts=?, acked_by=? where id=? and acked_ts is null",
                        (ts, user, event_id)).rowcount

    def events(self, limit=100):
        return self.q("select * from events order by acked_ts is not null, level desc, ts desc limit ?", (limit,))

    # users
    def add_user(self, name, pw_hash, roles):
        self.run("insert into users values (?,?,?,?)", (name, pw_hash, json.dumps(roles), time.time()))

    def user(self, name):
        r = self.q("select * from users where name=?", (name,))
        return {**r[0], "roles": json.loads(r[0]["roles"])} if r else None

    def users(self):
        return [{"name": u["name"], "roles": json.loads(u["roles"])} for u in self.q("select * from users order by name")]

    def delete_user(self, name):
        return self.run("delete from users where name=?", (name,)).rowcount

    def set_hash(self, name, pw_hash):
        return self.run("update users set hash=? where name=?", (pw_hash, name)).rowcount

    # audit
    def audit(self, user, action, detail=""):
        self.run("insert into audit(ts,user,action,detail) values (?,?,?,?)", (time.time(), user, action, detail))

    def audit_log(self, limit=200):
        return self.q("select * from audit order by id desc limit ?", (limit,))

    # config objects
    def put(self, kind, name, body):
        self.run("insert into objects values (?,?,?) on conflict(kind,name) do update set body=excluded.body",
                 (kind, name, json.dumps(body)))

    def get(self, kind, name):
        r = self.q("select body from objects where kind=? and name=?", (kind, name))
        return json.loads(r[0]["body"]) if r else None

    def list(self, kind):
        return [{"name": r["name"], **json.loads(r["body"])} for r in
                self.q("select name, body from objects where kind=? order by name", (kind,))]

    def delete(self, kind, name):
        return self.run("delete from objects where kind=? and name=?", (kind, name)).rowcount
