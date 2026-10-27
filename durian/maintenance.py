"""Keeping the database healthy: retention, backups, config export/import, history CSV.

Everything here works on a Store and is called by the app. Nothing runs unless it is asked to or the schedule says so.
"""
import csv
import datetime as dt
import io
import json
import math
import queue
import re
import threading
import time
from pathlib import Path

DEFAULTS = {"retention_days": 365, "purge_hour": 3, "backup_hour": -1, "backup_keep": 7,
            "export_hour": -1, "export_keep": 7}
LIMITS = {"retention_days": (0, 36500), "purge_hour": (0, 23), "backup_hour": (-1, 23), "backup_keep": (1, 365),
          "export_hour": (-1, 23), "export_keep": (1, 365)}
BACKUP_NAME = re.compile(r"^durian-\d{8}-\d{6}\.db$")
EXPORT_NAME = re.compile(r"^durian-export-\d{8}-\d{6}\.zip$")
KINDS = ("source", "point", "handler", "publisher", "watchlist", "maintenance", "dashboard")
CSV_MAX_ROWS, IMPORT_MAX_ROWS = 200_000, 50_000
EPOCH_MIN = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc).timestamp()


class Raw:
    """A response that is not JSON: bytes with a content type and optionally a download name."""

    def __init__(self, data, ctype, filename=None):
        self.data, self.ctype, self.filename = data, ctype, filename


class Overlay:
    """A store as it would look after an import, so every imported object is checked against the others."""

    def __init__(self, store, objects):
        self.store, self.objects = store, objects

    def get(self, kind, name):
        mine = self.objects.get(kind, {})
        return mine[name] if name in mine else self.store.get(kind, name)

    def list(self, kind):
        merged = {o["name"]: o for o in self.store.list(kind)}
        merged.update({n: {"name": n, **b} for n, b in self.objects.get(kind, {}).items()})
        return sorted(merged.values(), key=lambda o: o["name"])


class Maintenance:
    def __init__(self, store, backup_dir, export_dir=None):
        self.store, self.backup_dir = store, Path(backup_dir)
        self.export_dir = Path(export_dir) if export_dir else self.backup_dir.parent / "exports"
        self._exports, self._export_thread = queue.Queue(), None

    # ---- settings
    def settings(self):
        return {k: self.store.get_setting(k, d) for k, d in DEFAULTS.items()}

    def update_settings(self, body):
        errors = []
        if not isinstance(body, dict):
            return [{"property": "body", "message": "must be an object"}]
        for k, v in body.items():
            lo, hi = LIMITS.get(k, (None, None))
            if lo is None:
                errors.append({"property": k, "message": f"unknown setting, expected one of {sorted(DEFAULTS)}"})
            elif not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
                errors.append({"property": k, "message": f"must be a whole number from {lo} to {hi}"})
        if not errors:
            for k, v in body.items():
                self.store.set_setting(k, v)
        return errors

    # ---- retention
    def purge(self, now=None, budget=20.0):
        """Delete history past its retention. Per-point retention_days overrides the global setting (0 keeps forever)."""
        now, t0, deleted = now or time.time(), time.monotonic(), 0
        days = self.settings()["retention_days"]
        override = {p["name"]: p["retention_days"] for p in self.store.list("point") if isinstance(p.get("retention_days"), int)}
        jobs = [(now - d * 86400, n, ()) for n, d in override.items() if d > 0]
        if days > 0:
            jobs.append((now - days * 86400, None, tuple(override)))
        for cutoff, point, skip in jobs:
            while True:
                n = self.store.purge_history(cutoff, point=point, except_points=skip)
                deleted += n
                if n == 0:
                    break
                if time.monotonic() - t0 > budget:
                    self._record("purge", now, deleted)
                    return {"deleted": deleted, "complete": False}
        self._record("purge", now, deleted)
        return {"deleted": deleted, "complete": True}

    def _record(self, what, now, detail):
        self.store.set_setting(f"last_{what}", {"ts": now, "detail": detail})

    # ---- backups (SQLite file databases)
    def supported(self):
        return self.store.kind == "sqlite" and self.store.path != ":memory:"

    def backup(self, now=None):
        if not self.supported():
            raise NotImplementedError("in-app backups support file-based SQLite. Use your database's own backup tooling.")
        now = now or time.time()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        name = "durian-" + dt.datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S") + ".db"
        size = self.store.backup_to(str(self.backup_dir / name))
        for old in self.backups()[self.settings()["backup_keep"]:]:
            (self.backup_dir / old["name"]).unlink(missing_ok=True)
        self._record("backup", now, name)
        return {"name": name, "bytes": size}

    def backups(self):
        if not self.backup_dir.is_dir():
            return []
        files = [p for p in self.backup_dir.iterdir() if BACKUP_NAME.match(p.name)]
        return [{"name": p.name, "bytes": p.stat().st_size, "ts": p.stat().st_mtime} for p in sorted(files, reverse=True)]

    def backup_file(self, name):
        if not BACKUP_NAME.match(name or ""):
            return None  # the name is checked against a fixed pattern, so it can never reach outside the backup folder
        p = self.backup_dir / name
        return p if p.is_file() else None

    # ---- scheduled export bundles
    def exports(self):
        if not self.export_dir.is_dir():
            return []
        files = [p for p in self.export_dir.iterdir() if EXPORT_NAME.match(p.name)]
        return [{"name": p.name, "bytes": p.stat().st_size, "ts": p.stat().st_mtime} for p in sorted(files, reverse=True)]

    def export_bundle(self, now=None):
        """Build one bundle and prune older ones. Called by the worker, so the schedule never blocks purge or backup."""
        from . import export  # local import: export reads KINDS from this module
        now = now or time.time()
        days = self.settings()["retention_days"]
        days = min(7, days) if days > 0 else 7  # never ask for a window the retention setting forbids
        data, name, manifest = export.build(self.store, days=days, now=now)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        (self.export_dir / name).write_bytes(data)
        for old in self.exports()[self.settings()["export_keep"]:]:
            (self.export_dir / old["name"]).unlink(missing_ok=True)
        self._record("export", now, {"name": name, "bytes": len(data), "points": manifest["points"]["count"],
                                     "rows": manifest["rows"]["history"], "events": manifest["rows"]["events"]})
        return {"name": name, "bytes": len(data)}

    def _export_loop(self):
        while True:
            now = self._exports.get()
            try:
                self.export_bundle(now)
            except Exception as e:  # a failed bundle is reported in status, not only written to the log
                message = str(e).strip() or "the bundle could not be built"
                self._record("export", now, {"error": message})
                self.store.audit("system", "export.failed", f"{type(e).__name__}: {e}")
            finally:
                self._exports.task_done()

    def _queue_export(self, now):
        """One long-lived worker thread: a large bundle must not hold up the purge and backup in the timer thread."""
        if self._export_thread is None:
            self._export_thread = threading.Thread(target=self._export_loop, daemon=True, name="durian-export")
            self._export_thread.start()
        self._exports.put(now)

    def wait_export(self):
        """Wait until every queued bundle has been attempted (tests, and a caller that wants the schedule to finish)."""
        if self._export_thread is not None:
            self._exports.join()

    # ---- schedule: call once a minute
    def tick(self, now=None):
        now = now or time.time()
        local = dt.datetime.fromtimestamp(now)
        s, today, did = self.settings(), local.strftime("%Y-%m-%d"), []
        jobs = (("purge", s["purge_hour"], lambda: self.purge(now)),
                ("backup", s["backup_hour"], lambda: self.backup(now)),
                ("export", s["export_hour"], lambda: self._queue_export(now)))
        for what, hour, fn in jobs:
            last = self.store.get_setting(f"last_{what}_day")
            if hour >= 0 and local.hour >= hour and last != today and (what != "backup" or self.supported()):
                self.store.set_setting(f"last_{what}_day", today)  # first, so a failure is retried tomorrow, not every minute
                try:
                    fn()
                    did.append(what)
                except Exception as e:
                    self.store.audit("system", f"{what}.failed", f"{type(e).__name__}: {e}")
        return did

    def status(self, pending=0, lost=0):
        st = self.store
        return {"backend": st.kind, "location": st.describe(), "schema_version": st.version(), "size_bytes": st.size_bytes(),
                "counts": st.table_counts(), "history": st.history_stats(), "settings": self.settings(),
                "last_purge": st.get_setting("last_purge"), "last_backup": st.get_setting("last_backup"),
                "last_export": st.get_setting("last_export"), "exports": self.exports(),
                "backups_supported": self.supported(), "backups": self.backups(), "buffered_samples": pending, "lost_samples": lost}

    # ---- config export and import
    def export_config(self):
        return {"format": "durian-config", "version": 1, "exported_at": time.time(),
                "objects": {k: self.store.list(k) for k in KINDS}}

    def import_config(self, doc, validators, mode="merge", dry_run=True, owner=None):
        """Check every object against the others, then (unless dry_run) apply. Returns (errors, summary)."""
        errors, add = [], lambda p, m: errors.append({"property": p, "message": m})
        if not (isinstance(doc, dict) and doc.get("format") == "durian-config" and doc.get("version") == 1 and isinstance(doc.get("objects"), dict)):
            return [{"property": "format", "message": "not a durian-config version 1 document"}], None
        if mode not in ("merge", "replace"):
            return [{"property": "mode", "message": "must be merge or replace"}], None
        objs, total = {}, 0
        for kind, items in doc["objects"].items():
            if kind not in KINDS:
                add(f"objects.{kind}", f"unknown kind, expected one of {list(KINDS)}")
                continue
            if not isinstance(items, list):
                add(f"objects.{kind}", "must be a list")
                continue
            for i, o in enumerate(items):
                total += 1
                if not (isinstance(o, dict) and isinstance(o.get("name"), str)):
                    add(f"objects.{kind}[{i}]", "every object needs a name")
                    continue
                objs.setdefault(kind, {})[o["name"]] = {k: v for k, v in o.items() if k != "name"}
        if total > 5000:
            return [{"property": "objects", "message": "too many objects (limit 5000)"}], None
        view = Overlay(self.store, objs)  # keep checking the objects we could read, so one run shows every problem
        for kind in KINDS:
            for name, body in objs.get(kind, {}).items():
                for e in validators[kind](name, body, view):
                    add(f"{kind}/{name}.{e['property']}", e["message"])
        if errors:
            return errors, None
        summary = {k: {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0} for k in KINDS}
        order = ("source", "point", "handler", "publisher", "watchlist", "maintenance", "dashboard")
        with self.store.atomic():  # a failure halfway leaves the stored configuration exactly as it was
            for kind in order:
                for name, body in objs.get(kind, {}).items():
                    if kind == "dashboard":
                        body = {**body, "owner": body.get("owner", owner)}
                    have = self.store.get(kind, name)
                    summary[kind]["unchanged" if have == body else "updated" if have is not None else "created"] += 1
                    if not dry_run and have != body:
                        self.store.put(kind, name, body)
            if mode == "replace":
                for kind in reversed(order):  # points before the sources they belong to
                    keep = set(objs.get(kind, {}))
                    for o in self.store.list(kind):
                        if o["name"] not in keep:
                            summary[kind]["deleted"] += 1
                            if not dry_run:
                                self.store.delete(kind, o["name"])
        return [], summary

    # ---- history as CSV
    def export_history(self, point, start, end):
        rows = self.store.history_range(point, start, end, CSV_MAX_ROWS + 1)
        if len(rows) > CSV_MAX_ROWS:
            return None
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["timestamp_utc", "epoch_seconds", "value"])
        for r in rows:
            w.writerow([dt.datetime.fromtimestamp(r["ts"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", f"{r['ts']:.3f}", r["svalue"] if r["svalue"] is not None else repr(float(r["value"]))])
        return out.getvalue().encode()

    def import_history(self, point, text, now=None):
        """CSV of epoch seconds or ISO-8601 timestamps and a value. Bad rows are skipped and counted."""
        now, good, skipped, first_error = now or time.time(), [], 0, None
        for n, row in enumerate(csv.reader(io.StringIO(text)), 1):
            if len(good) + skipped >= IMPORT_MAX_ROWS:
                return {"imported": 0, "skipped": skipped, "error": f"too many rows (limit {IMPORT_MAX_ROWS})"}
            if len(row) < 2:
                skipped += 1
                first_error = first_error or f"line {n}: needs a time and a value"
                continue
            try:
                t = _parse_time(row[-2] if len(row) >= 3 and _is_time(row[-2]) else row[0])
                v = float(row[-1])
                if not (math.isfinite(v) and EPOCH_MIN <= t <= now + 86400):
                    raise ValueError("outside the accepted time range or not a finite number")
                good.append((point, t, v))
            except ValueError as e:
                if n == 1 and not good:
                    continue  # a header line
                skipped += 1
                first_error = first_error or f"line {n}: {e}"
        for i in range(0, len(good), 5000):
            self.store.add_history(good[i:i + 5000])
        return {"imported": len(good), "skipped": skipped, "first_error": first_error}


def _is_time(s):
    try:
        _parse_time(s)
        return True
    except ValueError:
        return False


def _parse_time(s):
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()
