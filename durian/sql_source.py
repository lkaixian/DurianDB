"""SQL source: run one read-only SELECT against a SQLite file each cycle. The first column is a key, the second a number.

The database is opened read-only, the connection is set query_only, and a runtime limit stops a runaway query.
"""
import math
import re
import sqlite3
import time
from pathlib import Path

from .core import sentence
from .polled import PolledSource, number, scale

SELECT = re.compile(r"^\s*select\s", re.I)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_source(b, add):
    if not (isinstance(b.get("path"), str) and Path(b["path"]).is_file()):
        add("path", "must be the path of an existing SQLite file")
    q = b.get("query")
    if not (isinstance(q, str) and 0 < len(q) <= 2000 and SELECT.match(q) and ";" not in q.strip().rstrip(";")):
        add("query", "must be a single SELECT returning a key column and a value column")
    for k, lo, hi in (("timeout", 0.5, 60), ("period", 1, 86400), ("backoff_max", 1, 86400)):
        if k in b and not (_num(b[k]) and lo <= b[k] <= hi):
            add(k, f"must be a number from {lo} to {hi}")
    if "backoff_scale" in b and not (_num(b["backoff_scale"]) and 1 < b["backoff_scale"] <= 10):
        add("backoff_scale", "must be greater than 1 and at most 10")


def validate_point(b, add):
    s = b.get("sql")
    if not (isinstance(s, dict) and isinstance(s.get("key"), str) and 0 < len(s["key"]) <= 200):
        add("sql.key", "a point on a SQL source needs the key its row is found by")


class SqlSource(PolledSource):
    def __init__(self, name, cfg):
        super().__init__(name, cfg.get("period", 10.0), cfg.get("backoff_scale", 1.5), cfg.get("backoff_max", 300.0))
        self.path, self.query, self.timeout = str(Path(cfg["path"]).resolve()), cfg["query"], cfg.get("timeout", 5.0)

    def rows(self):
        """The query's rows (at most 10000). Public so the Test button can show what a source would see."""
        con = sqlite3.connect(Path(self.path).as_uri() + "?mode=ro", uri=True, timeout=self.timeout)
        try:
            con.execute("PRAGMA query_only = ON")
            deadline = time.monotonic() + self.timeout
            con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
            return con.execute(self.query).fetchmany(10000)
        finally:
            con.close()

    def fetch(self):
        rows = self.rows()
        by_key = {str(r[0]): r[1] for r in rows if len(r) >= 2}
        values, rejected = {}, []
        for p in self.points.values():
            key = p.meta["sql"]["key"]
            if key not in by_key:
                rejected.append(f"{p.name}: no row for key {key!r}")
                continue
            try:
                values[p.name] = scale(p, number(by_key[key]))
            except ValueError as e:
                rejected.append(f"{p.name}: {sentence(e)}")
        return values, rejected
