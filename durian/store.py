"""Persistence: time-series history, events, users, audit trail, settings and config objects.

Backends: SQLite (default) and MySQL/MariaDB. Pick with the URL given to Store:
    :memory:  /path/to/file.db  sqlite:///path/to/file.db  mysql://user:password@host:3306/database
Config objects (sources, points, handlers, dashboards...) live in one table as JSON keyed by (kind, name).
Validation belongs to the layer that writes them, not here. Schema changes go through durian/migrations.py.
"""
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
import urllib.parse
from pathlib import Path

from .core import sentence
from .migrations import MIGRATIONS

ROLLUPS = {"avg": "avg(value)", "min": "min(value)", "max": "max(value)", "count": "count(*)", "sum": "sum(value)"}
ALL_ROLLUPS = tuple(ROLLUPS) + ("last", "first", "delta")  # these three need the order of the samples, so they are written out in series()
TYPES = {
    "sqlite": {"pk": "integer primary key", "key": "text", "text": "text", "real": "real", "int": "integer"},
    "mysql": {"pk": "bigint auto_increment primary key", "key": "varchar(190)", "text": "longtext", "real": "double", "int": "bigint"},
}


class Store:
    def __init__(self, url=":memory:"):
        self.url, self.lock = url, threading.RLock()  # guards this connection; engine state has its own lock in app
        self._tx = 0  # depth of an enclosing atomic() block; run() does not commit inside one
        if url.startswith("mysql://"):  # must differ: opening the database is driver work, not SQL; every statement below is shared
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

    def _int_div(self, col):
        """`col` divided by the next ? and truncated to an integer, written once because the databases spell it
        differently: MySQL's division keeps a fraction and needs floor(), SQLite's cast truncates. For every
        timestamp history stores, both return the start of the same bucket."""
        return f"floor({col} / ?)" if self.kind == "mysql" else f"cast({col} / ? as int)"

    def _exec(self, sql, args=(), batch=False):
        """One statement, or a batch of them, through one path on both databases. MySQL only adds housekeeping:
        keep the connection alive, send %s placeholders, undo the failed statement when no atomic() owns it."""
        if self.kind == "mysql":
            self.db.ping(reconnect=True)  # the server drops connections that sat idle between ticks
        cur = self.db.cursor()
        try:
            (cur.executemany if batch else cur.execute)(self._x(sql), args)
        except Exception:
            # must differ: MySQL holds even a plain read in a transaction, so a failed statement there has to be
            # undone here unless an enclosing atomic() owns the rollback
            if self.kind == "mysql" and not self._tx:
                self.db.rollback()
            raise
        return cur

    def ddl(self, sql):
        with self.lock:
            self._exec(sql.format(**self.types))
            self.db.commit()

    def q(self, sql, args=()):
        with self.lock:
            cur = self._exec(sql, args)
            cols = [c[0] for c in cur.description or []]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            if self.kind == "mysql" and not self._tx:
                # must differ: autocommit off means MySQL holds the read snapshot open even after a plain select,
                # so the transaction has to end here for later reads to see new rows; SQLite opens none for a read.
                self.db.commit()  # an enclosing atomic() owns its transaction
            return rows

    def run(self, sql, args=()):
        with self.lock:
            cur = self._exec(sql, args)
            if not self._tx:
                self.db.commit()
            return cur

    @contextmanager
    def atomic(self):
        """Everything written inside is one transaction: a failure halfway undoes all of it. Nests safely."""
        with self.lock:
            self._tx += 1
            try:
                yield
            except BaseException:
                self._tx = 0
                self.db.rollback()
                raise
            finally:
                self._tx -= 1
                if self._tx <= 0:
                    self._tx = 0
                    self.db.commit()

    def many(self, sql, rows):
        with self.lock:
            self._exec(sql, rows, batch=True)
            if not self._tx:
                self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    # ---- migrations
    def version(self):
        """0 only when the table does not exist yet. A table that exists but cannot be read is an error: treating it as 0
        would replay every migration over a live database."""
        try:
            r = self.q("select max(version) as v from schema_version")
            return r[0]["v"] or 0
        except Exception as e:
            if not self._has_table("schema_version"):
                return 0
            raise RuntimeError(f"the database schema version cannot be read: {type(e).__name__}. Restore the database or repair the schema_version table.") from e

    def _has_table(self, name):
        if self.kind == "mysql":  # must differ: each database keeps its list of tables in its own catalog
            return bool(self.q("select 1 from information_schema.tables where table_schema = database() and table_name = ?", (name,)))
        return bool(self.q("select 1 from sqlite_master where type='table' and name=?", (name,)))

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
        self._upsert("settings", ("skey",), {"skey": key, "svalue": json.dumps(value)})

    def _upsert(self, table, keys, row):
        """Insert a row, or update its value columns in place when the key columns are already taken. The whole
        statement is built once for both databases; only the conflict clause differs in wording, not in effect."""
        cols = list(row)
        upd = [c for c in cols if c not in keys]
        if self.kind == "mysql":
            tail = "on duplicate key update " + ", ".join(f"{c}=values({c})" for c in upd)
        else:
            tail = f"on conflict({', '.join(keys)}) do update set " + ", ".join(f"{c}=excluded.{c}" for c in upd)
        self.run(f"insert into {table}({', '.join(cols)}) values ({', '.join('?' * len(cols))}) {tail}", tuple(row[c] for c in cols))

    # ---- history
    def add_history(self, rows):
        # a (point, ts) already stored is skipped, so a replayed backfill never lands twice; must differ: the keyword's spelling
        ins = "insert ignore" if self.kind == "mysql" else "insert or ignore"
        self.many(ins + " into history(point, ts, value, quality, svalue) values (?,?,?,?,?)",  # rows without a quality are good
                  [(r[0], r[1], None if isinstance(r[2], str) else r[2], r[3] if len(r) > 3 else 0, r[2] if isinstance(r[2], str) else None) for r in rows])

    def recent(self, point, n=60):
        return self.q("select ts, value from history where point=? and quality < 2 order by ts desc limit ?", (point, n))[::-1]

    def series(self, point, since, bucket, rollup):
        """Bucketed history; bucket start time is returned as ts. Caller validates the arguments."""
        b = self._int_div("ts")  # one bucketing expression for both dialects, so a rollup change lands on each backend at once
        if rollup in ("last", "first"):
            return self.q(f"select b * ? as ts, value from (select {b} as b, value, "
                          f"row_number() over (partition by {b} order by ts {'desc' if rollup == 'last' else 'asc'}) as rn "
                          "from history where point = ? and ts >= ? and quality < 2) t where rn = 1 order by b",
                          (bucket, bucket, bucket, point, since))
        if rollup == "delta":  # last minus first of the bucket: what a counter or a meter advanced by
            return self.q(f"select b * ? as ts, last_v - first_v as value from (select b, max(case when rd = 1 then value end) as last_v, "
                          f"max(case when ra = 1 then value end) as first_v from (select {b} as b, value, "
                          f"row_number() over (partition by {b} order by ts desc) as rd, row_number() over (partition by {b} order by ts asc) as ra "
                          "from history where point = ? and ts >= ? and quality < 2) t group by b) u order by b",
                          (bucket, bucket, bucket, bucket, point, since))
        return self.q(f"select {b} * ? as ts, {ROLLUPS[rollup]} as value from history "
                      f"where point = ? and ts >= ? and quality < 2 group by {b} order by ts",
                      (bucket, bucket, point, since, bucket))

    def history_range(self, point, start, end, limit):
        return self.q("select ts, value, quality, svalue from history where point=? and ts>=? and ts<=? and quality < 2 order by ts limit ?", (point, start, end, limit))

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
        if self.kind == "mysql":  # must differ: MySQL has DELETE ... LIMIT, SQLite has no limit on delete and deletes the rowids a subquery picks
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

    def events_search(self, allowed, level_min=0, state="all", since=None, until=None, q=None, limit=50, offset=0):
        """Alarm journal: newest first, filtered, with the total. `allowed` is the set of event point names the caller may see."""
        where, args = ["level >= ?"], [level_min]
        if allowed is not None:
            if not allowed:
                return [], 0
            where.append(f"point in ({', '.join('?' * len(allowed))})")
            args += sorted(allowed)
        where += {"all": [], "unacked": ["acked_ts is null"], "active": ["cleared_ts is null"], "closed": ["cleared_ts is not null and acked_ts is not null"]}[state]
        if since is not None:
            where.append("ts >= ?")
            args.append(since)
        if until is not None:
            where.append("ts <= ?")
            args.append(until)
        if q:  # instr instead of LIKE: no wildcard characters to escape, and both databases have it
            where.append("(instr(lower(point), ?) > 0 or instr(lower(message), ?) > 0)")
            args += [q.lower(), q.lower()]
        clause = " and ".join(where)
        total = self.q(f"select count(*) as n from events where {clause}", args)[0]["n"]
        return self.q(f"select * from events where {clause} order by ts desc, id desc limit ? offset ?", args + [limit, offset]), total

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
        return [{"name": u["name"], "roles": json.loads(u["roles"]), "mfa": bool(u["totp"]), "disabled": bool(u["disabled"])}
                for u in self.q("select * from users order by name")]

    def set_disabled(self, name, disabled):
        return self.run("update users set disabled=? where name=?", (1 if disabled else 0, name)).rowcount

    def delete_user(self, name):
        return self.run("delete from users where name=?", (name,)).rowcount

    def set_hash(self, name, pw_hash):
        return self.run("update users set hash=? where name=?", (pw_hash, name)).rowcount

    # ---- shared sign-in state: sessions, lockouts, the TOTP replay guard and write confirmations. Kept here so a
    # second server process sees the same session, lockout, spent code and confirm token instead of its own copy.
    def add_session(self, token, name, expires):
        self.run("insert into sessions(token, name, expires) values (?,?,?)", (token, name, expires))

    def session(self, token):
        r = self.q("select name, expires from sessions where token=?", (token,))
        return r[0] if r else None

    def delete_session(self, token):
        return self.run("delete from sessions where token=?", (token,)).rowcount

    def sessions_for(self, name):
        return [r["token"] for r in self.q("select token from sessions where name=?", (name,))]

    def purge_sessions(self, now):
        return self.run("delete from sessions where expires <= ?", (now,)).rowcount

    def failure(self, name):
        r = self.q("select attempts, locked_until from auth_failures where name=?", (name,))
        return r[0] if r else None

    def failure_count(self):
        return self.q("select count(*) as n from auth_failures")[0]["n"]

    def set_failure(self, name, attempts, locked_until):
        self._upsert("auth_failures", ("name",), {"name": name, "attempts": attempts, "locked_until": locked_until, "seen": time.time()})

    def delete_failure(self, name):
        return self.run("delete from auth_failures where name=?", (name,)).rowcount

    def purge_failures(self, now):
        return self.run("delete from auth_failures where locked_until > 0 and locked_until <= ?", (now,)).rowcount

    def prune_failures(self, limit, now=None):
        """Keep lockouts bounded: names with no account and expired lockouts go first, then the oldest unlocked
        entry, never one that is locked out now. Returns how many rows went."""
        now = now or time.time()
        with self.lock:
            rows = self.q("select name, locked_until from auth_failures order by id")
            known = {u["name"] for u in self.users()}
            drop = [r["name"] for r in rows
                    if (r["name"][6:] if r["name"].startswith("reset:") else r["name"]) not in known
                    or (r["locked_until"] and r["locked_until"] <= now)]
            left = [r for r in rows if r["name"] not in set(drop)]
            excess = len(left) - limit
            for r in left:
                if excess <= 0:
                    break
                if (r["locked_until"] or 0) <= now:
                    drop.append(r["name"])
                    excess -= 1
            for i in range(0, len(drop), 500):
                batch = drop[i:i + 500]
                self.run(f"delete from auth_failures where name in ({', '.join('?' * len(batch))})", batch)
            return len(drop)

    def claim_step(self, name, step):
        """Record `step` as spent, atomically. True only for the call that moved the guard forward, so two
        processes cannot both accept the same TOTP code."""
        # must differ: both say "the step only moves forward", but SQLite has a where on its conflict update and
        # MySQL does not, so there the column is guarded with if() instead
        if self.kind == "mysql":
            cur = self.run("insert into auth_last_step(name, last_step) values (?,?) "
                           "on duplicate key update last_step=if(last_step < values(last_step), values(last_step), last_step)",
                           (name, step))
        else:
            cur = self.run("insert into auth_last_step(name, last_step) values (?,?) "
                           "on conflict(name) do update set last_step=excluded.last_step where excluded.last_step > auth_last_step.last_step",
                           (name, step))
        return cur.rowcount > 0

    def add_confirm(self, token, body, expires, keep):
        """Store a one-use confirmation, dropping expired rows and the oldest live one past `keep`. Returns how
        many live tokens were evicted."""
        now = time.time()
        with self.lock:
            self.run("delete from confirm_tokens where expires <= ?", (now,))
            dropped = 0
            while self.q("select count(*) as n from confirm_tokens")[0]["n"] >= keep:
                old = self.q("select token from confirm_tokens order by id limit 1")
                if not old or not self.run("delete from confirm_tokens where token=?", (old[0]["token"],)).rowcount:
                    break
                dropped += 1
            self.run("insert into confirm_tokens(token, body, expires) values (?,?,?)", (token, body, expires))
            return dropped

    def take_confirm(self, token):
        """Remove a confirmation and return (body, expires). The delete decides the winner, so the token is spent
        once even when two processes ask at the same moment."""
        with self.lock:
            r = self.q("select body, expires from confirm_tokens where token=?", (token,))
            if not r or not self.run("delete from confirm_tokens where token=?", (token,)).rowcount:
                return None
            return r[0]["body"], r[0]["expires"]

    def purge_confirms(self, now):
        return self.run("delete from confirm_tokens where expires <= ?", (now,)).rowcount

    # ---- engine scheduling shared by server processes: one tick lease and one row per escalated alarm. These
    # decide who acts, not what happened, so a second process over the same database neither double-ticks nor
    # double-escalates.
    LEASE_ID = 1

    def take_lease(self, owner, ttl, now=None):
        """Claim or renew the one tick lease atomically. True only for the caller that holds it after this call,
        so two server processes over one database never both tick."""
        now = time.time() if now is None else now
        # must differ: both hand the lease over when it has expired or already belongs to the caller, but SQLite
        # says so in a where on the conflict update and MySQL has no where there, so it repeats the test in if()
        if self.kind == "mysql":
            cur = self.run("insert into tick_lease(id, owner, expires) values (?,?,?) "
                           "on duplicate key update owner=if(expires <= ? or owner = ?, values(owner), owner), "
                           "expires=if(expires <= ? or owner = ?, values(expires), expires)",
                           (self.LEASE_ID, owner, now + ttl, now, owner, now, owner))
        else:
            cur = self.run("insert into tick_lease(id, owner, expires) values (?,?,?) "
                           "on conflict(id) do update set owner=excluded.owner, expires=excluded.expires "
                           "where tick_lease.expires <= ? or tick_lease.owner = ?",
                           (self.LEASE_ID, owner, now + ttl, now, owner))
        if cur.rowcount > 0:
            return True
        # MySQL counts only the rows it changed: a renewal that happens to write the same expiry still belongs
        # to its owner, so confirm with a read before refusing it.
        r = self.q("select owner from tick_lease where id=?", (self.LEASE_ID,))
        return bool(r and r[0]["owner"] == owner)

    def lease(self):
        """The tick lease row (owner, expires), or None when no process holds it."""
        r = self.q("select owner, expires from tick_lease where id=?", (self.LEASE_ID,))
        return r[0] if r else None

    def release_lease(self, owner):
        """Give the lease back on a clean shutdown, so another process can take over at once. Only its owner may."""
        return self.run("delete from tick_lease where id=? and owner=?", (self.LEASE_ID, owner)).rowcount

    def claim_escalation(self, handler, event_id):
        """Record that (handler, alarm) is being escalated, atomically. True only for the one call that got the
        row, so two server processes never mail the same escalation twice."""
        # must differ: SQLite skips a duplicate with do nothing; MySQL has no do nothing, so it rewrites the row
        # with the value it already holds, which changes no row and likewise reports zero
        if self.kind == "mysql":
            cur = self.run("insert into escalations(handler, event_id) values (?,?) "
                           "on duplicate key update event_id=event_id", (handler, event_id))
        else:
            cur = self.run("insert into escalations(handler, event_id) values (?,?) "
                           "on conflict(handler, event_id) do nothing", (handler, event_id))
        return cur.rowcount > 0

    def purge_escalations(self):
        """Forget escalations of alarms that are no longer open: a closed alarm needs no memory of being escalated."""
        return self.run("delete from escalations where event_id not in "
                        "(select id from events where cleared_ts is null)").rowcount

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

    # ---- read-only console (superadmin): one select, never a table that holds a hash or a token
    SECRET = ("users", "api_tokens", "sessions", "auth_failures", "auth_last_step", "confirm_tokens", "objects",
              "lockouts", "totp", "reset", "token", "hash", "sqlite_", "information_schema", "mysql", "settings")
    REFUSED = ("pragma", "attach", "detach", "insert", "update", "delete", "drop", "create", "alter", "replace", "vacuum",
               "reindex", "into", "load_extension", "load_file", "sleep", "benchmark", "with", "call", "set", "lock")

    def query_ro(self, query, limit=1000, seconds=5):
        """Run one SELECT and return (columns, rows, truncated). PermissionError is a refusal by name, ValueError a failed statement, both plain sentences.
        Three layers: the text is checked by name, the connection is read-only, and SQLite's authorizer
        re-checks every table actually read, so a trick the text check misses still reads nothing secret."""
        q = query
        while True:  # strip leading whitespace and comments
            t = q.lstrip()
            if t.startswith("--"):
                q = t.partition("\n")[2]
            elif t.startswith("/*"):
                q = t.split("*/", 1)[1] if "*/" in t else ""
            else:
                q = t
                break
        low = q.lower()
        if not low.startswith("select"):
            raise PermissionError(f"only a single SELECT statement is allowed, not {(low.split() or ['an empty statement'])[0].upper()}")
        for bad in (";", "--", "/*", "#"):
            if bad in q:
                raise PermissionError(f"'{bad}' is not allowed: one statement, no comments")
        words = set(re.findall(r"[a-z_]+", low))
        for w in self.REFUSED:
            if w in words:
                raise PermissionError(f"{w.upper()} is not allowed in the SQL console")
        for w in self.SECRET:
            if w in low:
                raise PermissionError(f"the SQL console cannot read '{w}': it holds secrets")
        with self.lock:
            try:
                if self.kind == "mysql":
                    self.db.ping(reconnect=True)
                    cur = self.db.cursor()
                    cur.execute("set session max_execution_time = %s", (seconds * 1000,))
                    cur.execute("start transaction read only")
                    cur.execute(q.replace("%", "%%"))
                else:
                    t0 = time.time()
                    self.db.execute("pragma query_only = on")
                    self.db.set_progress_handler(lambda: time.time() - t0 > seconds, 10000)
                    self.db.set_authorizer(self._console_auth)
                    cur = self.db.execute(q)
                cols = [c[0] for c in cur.description or []]
                rows = cur.fetchmany(limit + 1)
            except Exception as ex:
                raise ValueError(sentence(ex) or "the statement could not be run") from None
            finally:
                if self.kind == "mysql":
                    self.db.rollback()
                else:
                    self.db.set_authorizer(None)
                    self.db.set_progress_handler(None, 0)
                    self.db.execute("pragma query_only = off")
        return cols, [dict(zip(cols, r)) for r in rows[:limit]], len(rows) > limit

    def _console_auth(self, action, a, b, db, trigger):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_DENY if any(w in (a or "").lower() for w in self.SECRET) else sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (b or "").lower() != "load_extension":
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    # ---- audit
    def audit(self, user, action, detail=""):
        self.run("insert into audit(ts,user,action,detail) values (?,?,?,?)", (time.time(), user, action, detail))

    def audit_log(self, limit=200):
        return self.q("select * from audit order by id desc limit ?", (limit,))

    # ---- config objects
    def put(self, kind, name, body):
        self._upsert("objects", ("kind", "name"), {"kind": kind, "name": name, "body": json.dumps(body)})

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
        if self.kind == "mysql":  # must differ: MySQL adds up its statistics catalog; a SQLite database is just files on disk
            r = self.q("select coalesce(sum(data_length + index_length), 0) as n from information_schema.tables where table_schema = database()")
            return int(r[0]["n"])
        if self.path == ":memory:":
            return 0
        return sum(p.stat().st_size for p in (Path(self.path), Path(self.path + "-wal")) if p.exists())

    def describe(self):
        """Where the data lives, with any password removed."""
        if self.kind == "mysql":  # must differ: MySQL is the URL it dials, SQLite is the file it opened
            m = self._mysql
            return f"mysql://{m['user']}@{m['host']}:{m['port']}/{m['database']}"
        return self.path

    def backup_to(self, dest):
        """Consistent copy of a SQLite database while it is in use. Returns the file size."""
        if self.kind != "sqlite" or self.path == ":memory:":  # must differ: the online backup API only exists in SQLite
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
