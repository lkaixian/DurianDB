"""Persistence: time-series history, events, users, audit trail, settings and config objects.

Backends: SQLite (default) and MySQL/MariaDB. Pick with the URL given to Store:
    :memory:  /path/to/file.db  sqlite:///path/to/file.db  mysql://user:password@host:3306/database
Config objects (sources, points, handlers, dashboards...) live in one table as JSON keyed by (kind, name).
Validation belongs to the layer that writes them, not here. Schema changes go through durian/migrations.py.
"""
import json
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path

from .migrations import MIGRATIONS

ROLLUPS = {"avg": "avg(value)", "min": "min(value)", "max": "max(value)", "count": "count(*)"}
TYPES = {
    "sqlite": {"pk": "integer primary key", "key": "text", "text": "text", "real": "real", "int": "integer"},
    "mysql": {"pk": "bigint auto_increment primary key", "key": "varchar(190)", "text": "longtext", "real": "double", "int": "bigint"},
}


class Store:
    def __init__(self, url=":memory:"):
        self.url, self.lock = url, threading.RLock()  # the lock also guards the engine's runtime objects, see app
        if url.startswith("mysql://"):
            self.kind = "mysql"
            u = urllib.parse.urlparse(url)
            self._mysql = dict(host=u.hostname, port=u.port or 3306, user=urllib.parse.unquote(u.username or ""),
                               password=urllib.parse.unquote(u.password or ""), database=u.path.lstrip("/"), charset="utf8mb4")
            self.db = self._connect_mysql()
        else:
            self.kind = "sqlite"
            path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else url
            self.path = path
            self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
            if path != ":memory:":
                self.db.execute("pragma journal_mode = wal")  # readers do not block the writer
        self.types = TYPES[self.kind]
        self.migrate()

    # ---- connection and statements
    def _connect_mysql(self):
        import pymysql
        return pymysql.connect(**self._mysql, autocommit=False, connect_timeout=10)

    def _x(self, sql):  # one SQL text for both databases: the placeholder is the only thing that differs
        return sql.replace("?", "%s") if self.kind == "mysql" else sql

    def _exec(self, sql, args=()):
        if self.kind == "mysql":
            self.db.ping(reconnect=True)
            cur = self.db.cursor()
            try:
                cur.execute(self._x(sql), args)
            except Exception:
                self.db.rollback()
                raise
            return cur
        return self.db.execute(sql, args)

    def ddl(self, sql):
        with self.lock:
            self._exec(sql.format(**self.types))
            self.db.commit()

    def q(self, sql, args=()):
        with self.lock:
            cur = self._exec(sql, args)
            cols = [c[0] for c in cur.description or []]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            if self.kind == "mysql":
                self.db.commit()  # end the read snapshot so later reads see new rows
            return rows

    def run(self, sql, args=()):
        with self.lock:
            cur = self._exec(sql, args)
            self.db.commit()
            return cur

    def many(self, sql, rows):
        with self.lock:
            if self.kind == "mysql":
                self.db.ping(reconnect=True)
                cur = self.db.cursor()
                try:
                    cur.executemany(self._x(sql), rows)
                except Exception:
                    self.db.rollback()
                    raise
            else:
                self.db.executemany(sql, rows)
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    # ---- migrations
    def version(self):
        try:
            r = self.q("select max(version) as v from schema_version")
            return r[0]["v"] or 0
        except Exception:
            return 0

    def migrate(self):
        with self.lock:
            self.ddl("create table if not exists schema_version(version {int} not null, applied {real}, description {key})")
            have = self.version()
            for v, desc, fn in MIGRATIONS:
                if v > have:
                    fn(self)
                    self.run("insert into schema_version values (?,?,?)", (v, time.time(), desc))

    # ---- settings
    def get_setting(self, key, default=None):
        r = self.q("select svalue from settings where skey=?", (key,))
        return json.loads(r[0]["svalue"]) if r else default

    def set_setting(self, key, value):
        self._upsert("settings", ("skey",), "svalue", (key,), json.dumps(value))

    def _upsert(self, table, keys, valcol, keyvals, val):
        cols = ", ".join(keys + (valcol,))
        marks = ", ".join("?" * (len(keys) + 1))
        if self.kind == "mysql":
            sql = f"insert into {table}({cols}) values ({marks}) on duplicate key update {valcol}=values({valcol})"
        else:
            sql = f"insert into {table}({cols}) values ({marks}) on conflict({', '.join(keys)}) do update set {valcol}=excluded.{valcol}"
        self.run(sql, tuple(keyvals) + (val,))

    # ---- history
    def add_history(self, rows):
        self.many("insert into history(point, ts, value, quality) values (?,?,?,?)", [(*r, 0)[:4] for r in rows])  # rows without a quality are good

    def recent(self, point, n=60):
        return self.q("select ts, value from history where point=? and quality < 2 order by ts desc limit ?", (point, n))[::-1]

    def series(self, point, since, bucket, rollup):
        """Bucketed history; bucket start time is returned as ts. Caller validates the arguments."""
        b = "floor(ts / ?)" if self.kind == "mysql" else "cast(ts / ? as int)"
        if rollup == "last":
            return self.q(f"select b * ? as ts, value from (select {b} as b, value, "
                          f"row_number() over (partition by {b} order by ts desc) as rn "
                          "from history where point = ? and ts >= ? and quality < 2) t where rn = 1 order by b",
                          (bucket, bucket, bucket, point, since))
        return self.q(f"select {b} * ? as ts, {ROLLUPS[rollup]} as value from history "
                      f"where point = ? and ts >= ? and quality < 2 group by {b} order by ts",
                      (bucket, bucket, point, since, bucket))

    def history_range(self, point, start, end, limit):
        return self.q("select ts, value, quality from history where point=? and ts>=? and ts<=? and quality < 2 order by ts limit ?", (point, start, end, limit))

    def history_summary(self, point, start, end):
        """Count, min, max and average of the good samples in a window, the first and last good values, and the number of bad samples."""
        good = "point=? and ts>=? and ts<=? and quality < 2"
        r = self.q("select count(case when quality < 2 then 1 end) as n, min(case when quality < 2 then value end) as lo, "
                   "max(case when quality < 2 then value end) as hi, avg(case when quality < 2 then value end) as mean, "
                   "count(case when quality = 2 then 1 end) as bad from history where point=? and ts>=? and ts<=?", (point, start, end))[0]
        if r["n"]:
            r["first"] = self.q(f"select value from history where {good} order by ts limit 1", (point, start, end))[0]["value"]
            r["last"] = self.q(f"select value from history where {good} order by ts desc limit 1", (point, start, end))[0]["value"]
        return r

    def alarm_counts(self, start, end):
        return {r["point"]: r["n"] for r in self.q("select point, count(*) as n from events where ts>=? and ts<=? group by point", (start, end))}

    def purge_history(self, cutoff, point=None, except_points=(), limit=5000):
        """Delete up to `limit` rows older than cutoff, for one point or for all but the listed ones. Returns rows deleted."""
        where, args = "ts < ?", [cutoff]
        if point is not None:
            where += " and point = ?"
            args.append(point)
        if except_points:
            where += f" and point not in ({', '.join('?' * len(except_points))})"
            args += list(except_points)
        if self.kind == "mysql":
            return self.run(f"delete from history where {where} limit ?", args + [limit]).rowcount
        return self.run(f"delete from history where rowid in (select rowid from history where {where} limit ?)", args + [limit]).rowcount

    def history_stats(self):
        r = self.q("select count(*) as n, min(ts) as oldest, max(ts) as newest, count(distinct point) as points from history")[0]
        return {"rows": r["n"], "oldest": r["oldest"], "newest": r["newest"], "points": r["points"]}

    # ---- events
    def add_event(self, ev):
        return self.run("insert into events(point,level,message,ts,held) values (?,?,?,?,?)",
                        (ev.point, int(ev.level), ev.message, ev.ts, ev.held)).lastrowid

    def hold_event(self, event_id, held):
        self.run("update events set held=? where id=?", (held, event_id))

    def clear_event(self, event_id, ts):
        self.run("update events set cleared_ts=? where id=? and cleared_ts is null", (ts, event_id))

    def ack_event(self, event_id, user, ts):
        return self.run("update events set acked_ts=?, acked_by=? where id=? and acked_ts is null",
                        (ts, user, event_id)).rowcount

    def events_for_kpis(self, since):
        """Alarms raised since `since`, plus every alarm still active however old."""
        return self.q("select point, ts, cleared_ts, acked_ts, held from events where ts >= ? or cleared_ts is null", (since,))

    def events(self, limit=100, point=None):
        where, args = ("where point=? ", (point,)) if point else ("", ())
        return self.q(f"select * from events {where}order by acked_ts is not null, level desc, ts desc limit ?", args + (limit,))

    # ---- users
    def add_user(self, name, pw_hash, roles):
        self.run("insert into users(name, hash, roles, created) values (?,?,?,?)", (name, pw_hash, json.dumps(roles), time.time()))

    def user(self, name):
        r = self.q("select * from users where name=?", (name,))
        return {**r[0], "roles": json.loads(r[0]["roles"])} if r else None

    def users(self):
        return [{"name": u["name"], "roles": json.loads(u["roles"])} for u in self.q("select * from users order by name")]

    def delete_user(self, name):
        return self.run("delete from users where name=?", (name,)).rowcount

    def set_hash(self, name, pw_hash):
        return self.run("update users set hash=? where name=?", (pw_hash, name)).rowcount

    # ---- machine tokens
    def add_token(self, name, token_hash, owner, scope, source, expires):
        self.run("insert into api_tokens(name, token_hash, owner, scope, source, created, expires) values (?,?,?,?,?,?,?)",
                 (name, token_hash, owner, scope, source, time.time(), expires))

    def token_by_hash(self, token_hash):
        r = self.q("select * from api_tokens where token_hash=?", (token_hash,))
        return r[0] if r else None

    def tokens(self):
        return self.q("select id, name, owner, scope, source, created, expires, last_used, disabled from api_tokens order by name")

    def touch_token(self, token_id, ts):
        self.run("update api_tokens set last_used=? where id=?", (ts, token_id))

    def set_token_disabled(self, name, disabled):
        return self.run("update api_tokens set disabled=? where name=?", (1 if disabled else 0, name)).rowcount

    def delete_token(self, name):
        return self.run("delete from api_tokens where name=?", (name,)).rowcount

    # ---- audit
    def audit(self, user, action, detail=""):
        self.run("insert into audit(ts,user,action,detail) values (?,?,?,?)", (time.time(), user, action, detail))

    def audit_log(self, limit=200):
        return self.q("select * from audit order by id desc limit ?", (limit,))

    # ---- config objects
    def put(self, kind, name, body):
        self._upsert("objects", ("kind", "name"), "body", (kind, name), json.dumps(body))

    def get(self, kind, name):
        r = self.q("select body from objects where kind=? and name=?", (kind, name))
        return json.loads(r[0]["body"]) if r else None

    def list(self, kind):
        return [{"name": r["name"], **json.loads(r["body"])} for r in
                self.q("select name, body from objects where kind=? order by name", (kind,))]

    def delete(self, kind, name):
        return self.run("delete from objects where kind=? and name=?", (kind, name)).rowcount

    # ---- database facts
    def table_counts(self):
        return {t: self.q(f"select count(*) as n from {t}")[0]["n"] for t in ("history", "events", "users", "audit", "objects")}

    def size_bytes(self):
        if self.kind == "mysql":
            r = self.q("select coalesce(sum(data_length + index_length), 0) as n from information_schema.tables where table_schema = database()")
            return int(r[0]["n"])
        if self.path == ":memory:":
            return 0
        return sum(p.stat().st_size for p in (Path(self.path), Path(self.path + "-wal")) if p.exists())

    def describe(self):
        """Where the data lives, with any password removed."""
        if self.kind == "mysql":
            m = self._mysql
            return f"mysql://{m['user']}@{m['host']}:{m['port']}/{m['database']}"
        return self.path

    def backup_to(self, dest):
        """Consistent copy of a SQLite database while it is in use. Returns the file size."""
        if self.kind != "sqlite" or self.path == ":memory:":
            raise NotImplementedError("in-app backup supports file-based SQLite only")
        with self.lock:
            out = sqlite3.connect(dest)
            try:
                self.db.backup(out)
                ok = out.execute("pragma integrity_check").fetchone()[0]
            finally:
                out.close()
        if ok != "ok":
            Path(dest).unlink(missing_ok=True)
            raise RuntimeError(f"backup failed its integrity check: {ok}")
        return Path(dest).stat().st_size
