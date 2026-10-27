"""Application logic: runtime engine, persistence loop, and the API as plain (status, payload) calls.

The HTTP layer in server.py only translates requests into App.handle() calls.
"""
import csv
import datetime as dt
import logging
import fnmatch
import io
import json
import math
import re
import secrets
import threading
import time
from collections import Counter
from pathlib import Path

from .auth import Auth, AuthError, can, check_password_strength, hash_password, verify_password
from . import notes, pointcsv, units, windows
from .config import (build_engine, resolve, roles_ok, validate_maintenance, validate_point, validate_source, validate_watchlist)
from .core import SOURCE, Event, Level, Quality, alarm_state, state_label
from .dashboards import validate_dashboard
from .forecast import baseline as ewma_baseline, forecast as forecast_series
from .ingest import HttpInSource, parse_payload
from .jobs import Jobs
from .maintenance import Maintenance, Raw
from .testsource import test_source
from .outputs import Outputs, validate_handler, validate_publisher
from .store import ALL_ROLLUPS

log = logging.getLogger("durian.app")

KINDS = ("source", "point", "handler", "publisher", "watchlist", "maintenance")
ROUTE = re.compile(r"^/api/(?P<a>[a-z-]+)(?:/(?P<b>[^/]+))?(?:/(?P<c>[^/]+))?$")


class ApiError(Exception):
    def __init__(self, status, message, errors=None):
        super().__init__(message)
        self.status, self.errors = status, errors


SYSTEM = "(system)"  # events about DurianDB itself rather than a point, visible to superadmins only
PENDING_MAX = 200_000
EVENT_PENDING_MAX = 5_000  # alarms waiting for a database that failed: a storm during an outage must not exhaust memory
CONFIRM_SECONDS = 30
TICK_ALARM_AFTER = 3  # consecutive failed polls before a (system) alarm is raised: one blip is not news



def watch_match(w, name, tags, description=""):
    """Does a watch list's rule (tags, and/or a name pattern and description text) select this point? All the rule's parts must hold."""
    if not (w.get("tags") or w.get("name_like") or w.get("text_like")):
        return False
    return (all(tags.get(k) == v for k, v in w.get("tags", {}).items())
            and (not w.get("name_like") or fnmatch.fnmatchcase(name.lower(), w["name_like"].lower()))
            and (not w.get("text_like") or w["text_like"].lower() in description.lower()))


def _diff(old, new):
    """What a config save changed, as 'key: before -> after' (a new object is just 'created'). Config holds credential file paths, never secrets."""
    if old is None:
        return " (created)"
    ch = [f"{k}: {json.dumps(old.get(k))[:60]} -> {json.dumps(new.get(k))[:60]}" for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]
    return " (" + "; ".join(ch)[:500] + ")" if ch else " (no change)"


class App:
    def __init__(self, store, backup_dir=None):
        self.store, self.auth = store, Auth(store)
        self.maint = Maintenance(store, backup_dir or Path("backups"))
        self.jobs = Jobs()  # slow work runs off the request thread; GET /api/jobs/<id> reports it
        self.engine, self.outputs, self.open_events = None, None, []
        self.engine_lock = threading.RLock()  # guards the engine and its runtime state, not the database: a slow tick must not stall a query
        self.escalated = set()  # (handler, event id) already escalated
        self.confirms = {}  # token -> ((user, point, value), expiry) for writes waiting for their second step
        self.windows = []  # maintenance windows, see windows.py
        self.holds = {}  # point name -> {kind: shelved|oos, user, reason, created, until}; kept in the objects table so they survive restarts
        self.pending, self.lost, self.unstored, self.sys_event = [], 0, [], None  # history and events waiting for a database that failed
        self.events_lost = 0  # alarms dropped because the database refused writes for too long; announced when it recovers
        self.tick_failures, self.tick_error, self.tick_ok_ts = 0, None, None  # the poll loop failing is otherwise invisible
        self.tick_event = None  # id of the (system) alarm that says the loop is failing
        self.reload()

    # ---- runtime
    def reload(self):
        """Rebuild the engine from stored config. Detector state is lost, so open events are closed."""
        with self.engine_lock:
            if self.outputs:
                self.outputs.stop()
            for s in (self.engine.sources if self.engine else []):
                getattr(s, "stop", lambda: None)()
            now = time.time()
            for ev in self.open_events:
                if ev.id is not None:
                    self.store.clear_event(ev.id, now)
            self.open_events = []
            self.engine = build_engine(self.store)
            self.outputs = Outputs(self.store, self.scope_of, self.handler_write)
            self.engine.publishers.append(self.outputs.on_value)
            self.holds = {h["name"]: h for h in self.store.list("alarmhold")}
            self.folder_defaults = {f["name"]: f for f in self.store.list("folder")}  # folder tags and unit, if any are configured
            self.load_windows()
            for name, (p, _) in self.points().items():
                p.in_service = self.holds.get(name, {}).get("kind") != "oos"

    def tick(self, ts=None):
        """One poll of every source, then store what it produced. Failures are counted: after three in a row the
        engine is not running at all, and that must reach an operator instead of staying in the log."""
        try:
            self._tick(ts)
        except Exception as e:
            self.tick_error = f"{type(e).__name__}: {e}"
            self.tick_failures += 1
            if self.tick_failures >= TICK_ALARM_AFTER:
                self._tick_alarm(self.tick_error)
            raise
        self.tick_failures, self.tick_error, self.tick_ok_ts = 0, None, ts or time.time()
        self._tick_alarm(None)

    def _sys_alarm(self, attr, message):
        """Raise or clear one (system) alarm, kept on `attr` (its event id). Raising again while it is up does nothing."""
        try:
            if message is None:
                ev = getattr(self, attr)
                if ev is not None:
                    if ev > 0:
                        self.store.clear_event(ev, time.time())
                    setattr(self, attr, None)
                return
            if getattr(self, attr) is None:
                setattr(self, attr, self.store.add_event(Event(SYSTEM, Level.URGENT, message, time.time())))
        except Exception:
            setattr(self, attr, None if message is None else -1)  # the database is down too; /api/health still says so

    def _tick_alarm(self, error):
        """Raise, keep, or clear the (system) alarm that says the poll loop is failing."""
        self._sys_alarm("tick_event", None if error is None else f"the poll loop is failing: {error}")

    def _tick(self, ts):
        with self.engine_lock:
            ts = ts or time.time()
            self.engine.tick(ts)
            self.pending.extend((p.name, t, v, q) for s in self.engine.sources for p in s.points.values() for t, v, q in p.take_unsaved())
            self._flush_history(ts)
            new, self.engine.events = self.unstored + self.engine.events, []
            self.unstored, stored = [], False
            for i, ev in enumerate(new):
                try:
                    pts = self.points()
                    w = self.window_for(ev.point, pts, ts)
                    if w and w["mode"] == "drop":
                        continue  # planned work: not recorded at all
                    ev.held = self._hold_for(ev.point, pts, ts)
                    ev.id = self.store.add_event(ev)
                    if w and w["mode"] == "acknowledge" and ev.held == "maintenance":
                        self.store.ack_event(ev.id, f"maintenance: {w['name']}", ts)
                except Exception:  # the database is unavailable: keep the rest and try again next tick
                    self.unstored = new[i:]
                    if len(self.unstored) > EVENT_PENDING_MAX:  # an alarm storm during an outage must not exhaust memory
                        self.events_lost += len(self.unstored) - EVENT_PENDING_MAX
                        del self.unstored[:len(self.unstored) - EVENT_PENDING_MAX]
                        log.warning("dropped %d unstored alarm(s): the database has been unavailable too long", self.events_lost)
                    break
                if not stored:
                    stored = True
                self.open_events.append(ev)
                if ev.held is None:
                    self.outputs.on_event(ev, "raised")
            if stored and self.events_lost:  # now that writes work, say what the outage cost. It cannot be said while they fail.
                self.store.add_event(Event(SYSTEM, Level.URGENT, f"{self.events_lost} alarm(s) were dropped while the database was unavailable", ts))
                self.events_lost = 0
            self._apply_holds(ts)
            still = []
            for ev in self.open_events:
                if ev.cleared_ts is None:
                    still.append(ev)
                    continue
                try:
                    self.store.clear_event(ev.id, ev.cleared_ts)
                except Exception:
                    still.append(ev)
                    continue
                if ev.held is None:
                    self.outputs.on_event(ev, "cleared")
            self.open_events = still

    # ---- safe control
    def perform_write(self, p, s, v, who, note=""):
        """The one place a value is written to a source: used by operators and by set-point handlers alike."""
        try:
            with self.engine_lock:
                s.write(p.name, v)
        except RuntimeError as ex:
            raise ApiError(502, str(ex))
        if hasattr(s, "override"):  # keep held values across engine rebuilds and restarts
            self.store.put("override", p.name, {"value": v}) if v is not None else self.store.delete("override", p.name)
        self.store.audit(who, "set", f"{p.name}: {p.last} -> {v}{note}")

    def handler_write(self, point, v, who):
        """A set-point event handler's write. It meets the same rules as an operator's (settable, limits, step size) except the confirm step."""
        with self.engine_lock:
            p, s = self.points().get(point, (None, None))
            try:
                if p is None or not p.meta.get("settable") or not hasattr(s, "write"):
                    raise ApiError(400, "not a settable point")
                self.check_setpoint(p, v)
                self.perform_write(p, s, v, who)
            except ApiError as e:
                self.store.audit(who, "set.refused", f"{point}: {e}")

    def scope_of(self, name):
        p, s = self.points().get(name, (None, None))
        return (self.resolve_point(p, s)[0] if p else {}), (s.name if s else None)  # inherited tags count for handlers too

    @staticmethod
    def set_limit(p, which):
        """Lowest or highest value a write may carry: the point's own limit, else its engineering range."""
        v = p.meta.get("set_" + which)
        return v if v is not None else p.meta.get("eu_low" if which == "min" else "eu_high")

    def check_setpoint(self, p, v):
        if v is None or isinstance(v, str):  # releasing a point is always allowed; a text value has no range
            return
        lo, hi, step = self.set_limit(p, "min"), self.set_limit(p, "max"), p.meta.get("max_step")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise ApiError(422, f"{p.name} accepts values from {lo if lo is not None else 'any'} to {hi if hi is not None else 'any'}")
        if step is not None and p.last is not None and abs(v - p.last) > step:
            raise ApiError(422, f"{p.name} may change by at most {step} per write (now {p.last}); step towards the target")

    def confirm_token(self, ident, point, v):
        now = time.time()
        self.confirms = {k: x for k, x in self.confirms.items() if x[1] > now}  # drop expired
        tok = secrets.token_urlsafe(12)
        self.confirms[tok] = ((ident["name"], point, v), now + CONFIRM_SECONDS)
        return tok

    def confirmed(self, ident, point, v, tok):
        """True once, for the same user, point and value that asked for the token, while it is fresh."""
        got = self.confirms.pop(tok, None) if isinstance(tok, str) else None
        return bool(got and got[1] > time.time() and got[0] == (ident["name"], point, v))

    def load_windows(self):
        self.windows = self.store.list("maintenance")

    def window_for(self, point, pts, ts):
        """The first maintenance window in effect that covers this point (or source alarm), or None."""
        for m in self.windows:
            if windows.active(m, ts):
                if point.startswith(SOURCE):
                    hit = windows.covers(m, point, source=point[len(SOURCE):])
                else:
                    p, s = pts.get(point, (None, None))
                    hit = windows.covers(m, point, self.resolve_point(p, s)[0] if p else None, s.name if s else None)
                if hit:
                    return m
        return None

    def _hold_for(self, point, pts, ts):
        """Why an alarm on this point is recorded but not announced right now, or None."""
        h = self.holds.get(point)
        if h and h["kind"] == "shelved" and h["until"] > ts:
            return "shelved"
        if self.window_for(point, pts, ts):
            return "maintenance"
        sw = pts[point][0].meta.get("suppress_when") if point in pts else None
        other = pts.get(sw["point"], (None,))[0] if sw else None
        if other is not None and other.last is not None and not other.text and other.quality != Quality.BAD:
            if {">": other.last > sw["value"], "<": other.last < sw["value"], "==": other.last == sw["value"]}[sw["op"]]:
                return "suppressed"  # suppression by design: the condition that makes this alarm meaningless holds
        return None

    def _apply_holds(self, ts):
        """Expire shelves, then move open alarms into or out of the held state. Leaving it announces the alarm if it is still active."""
        for name in [n for n, h in self.holds.items() if h["kind"] == "shelved" and h["until"] <= ts]:
            self.store.delete("alarmhold", name)
            del self.holds[name]
            self.store.audit("(system)", "shelve.expired", name)
        pts = self.points()
        for ev in self.open_events:
            want = self._hold_for(ev.point, pts, ts)
            if want == ev.held or ev.id is None:
                continue
            try:
                self.store.hold_event(ev.id, want)
            except Exception:
                continue
            was, ev.held = ev.held, want
            if was and not want and ev.cleared_ts is None:
                self.outputs.on_event(ev, "raised")

    def _flush_history(self, ts):
        """Write buffered samples. A failing database never loses data silently: samples wait (up to a cap) and an alarm is raised."""
        try:
            while self.pending:
                self.store.add_history(self.pending[:5000])
                del self.pending[:5000]
        except Exception as e:
            if len(self.pending) > PENDING_MAX:
                self.lost += len(self.pending) - PENDING_MAX
                del self.pending[:len(self.pending) - PENDING_MAX]
            if self.sys_event is None:
                try:
                    self.sys_event = self.store.add_event(Event(SYSTEM, Level.URGENT, f"history is not being saved: {type(e).__name__}", ts))
                except Exception:
                    self.sys_event = -1  # cannot even record it; the database page still shows the buffer
            return
        if self.sys_event is not None:
            if self.sys_event > 0:
                self.store.clear_event(self.sys_event, ts)
            self.sys_event = None

    def points(self):
        return {p.name: (p, s) for s in self.engine.sources for p in s.points.values()}

    def resolve_point(self, p, s):
        """Resolved tags, unit, display unit and display value for one point. Every list, page, report and tag filter
        reads this one place, so inherited values cannot drift between them. A display value is offered only for a
        good numeric reading: a stale or BAD held value must not be shown converted as if it were current."""
        tags, unit = resolve(p.tags, p.meta.get("unit", ""), p.meta.get("folder", ""), s.meta, getattr(self, "folder_defaults", None))
        display = p.meta.get("display_unit", "")
        display = display if display and units.compatible(unit, display) else ""
        value = None
        if display and not p.text and p.last is not None and p.quality == Quality.GOOD and not isinstance(p.last, bool):
            try:
                value = units.convert(p.last, unit, display)
            except (TypeError, ValueError):  # a stored value can still be text or a unit can have changed under it
                value = None
        return tags, unit, display, value

    # ---- views
    def point_view(self, p, s):
        cfg = p.meta.get("detector_cfg", [])
        limit = lambda kind: next((d["limit"] for d in cfg if d.get("kind") == kind), None)
        tags, unit, display_unit, display_value = self.resolve_point(p, s)
        return {"name": p.name, "source": s.name, "unit": unit, "tags": tags, "last": p.last,
                "display_unit": display_unit, "display_value": display_value,
                "datatype": "text" if p.text else "number", "description": p.meta.get("description", ""), "decimals": p.meta.get("decimals"),
                "folder": p.meta.get("folder", ""), "states": p.meta.get("states"), "text": state_label(p, p.last) if p.meta.get("states") and p.last is not None else None,
                "eu": {"low": p.meta.get("eu_low"), "high": p.meta.get("eu_high")},
                "set_limits": {"min": self.set_limit(p, "min"), "max": self.set_limit(p, "max"), "max_step": p.meta.get("max_step"), "confirm": bool(p.meta.get("confirm")), "reason_required": bool(p.meta.get("require_reason"))} if p.meta.get("settable") else None,
                "last_ts": p.last_ts, "received": p.last_recv, "quality": Quality(p.quality).name.lower(), "settable": p.meta.get("settable", False),
                "limits": {"high": limit("HIGH_LIMIT"), "low": limit("LOW_LIMIT")},
                "history": [{"ts": t, "value": v} for t, v in list(p.cache)[-60:]]}

    def visible_points(self, ident):
        return {n: (p, s) for n, (p, s) in self.points().items() if can(ident["roles"], p.meta.get("read_roles", ["user"]))}

    @staticmethod
    def _int_param(q, name, default, lo, hi):
        raw = q.get(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ApiError(422, "validation failed", [{"property": name, "message": f"{name} must be a whole number"}])
        if not lo <= value <= hi:
            raise ApiError(422, "validation failed", [{"property": name, "message": f"{name} must be from {lo} to {hi}"}])
        return value

    def forecast_view(self, ident, point, q):
        """GET /api/points/<name>/forecast?hours=6&bucket=60: stored points (the truth) plus forecast rows."""
        p, _s = self.visible_points(ident).get(point, (None, None))
        if p is None:
            raise ApiError(404, "unknown point")
        hours = self._int_param(q, "hours", 6, 1, 168)
        bucket = self._int_param(q, "bucket", 60, 1, 86400)
        steps = hours * 3600 // bucket
        if not 1 <= steps <= 2000:
            raise ApiError(422, "validation failed", [{"property": "bucket", "message":
                f"bucket must give 1 to 2000 forecast points over {hours} h; it gives {steps}"}])
        if p.text:
            raise ApiError(400, "a text point has no numeric forecast")
        history = self.store.series(point, time.time() - hours * 3600, bucket, "avg")
        result = forecast_series([(r["ts"], r["value"]) for r in history], steps)
        return 200, {"point": point, "unit": p.meta.get("unit", ""), "hours": hours, "bucket": bucket,
                     "points": history, "forecast": result["items"], "note": result["note"]}

    def baseline_view(self, ident, point, q):
        """GET /api/points/<name>/baseline?window=200: an EWMA baseline over the last `window` good samples."""
        p, _s = self.visible_points(ident).get(point, (None, None))
        if p is None:
            raise ApiError(404, "unknown point")
        window = self._int_param(q, "window", 200, 2, 10000)
        if p.text:
            raise ApiError(400, "a text point has no numeric baseline")
        pairs = [(r["ts"], r["value"]) for r in self.store.recent(point, window)]
        try:
            base = ewma_baseline(pairs, 0.3)
        except ValueError as e:  # too little history is an answer with a note, not an error
            return 200, {"point": point, "window": window, "baseline": None, "note": str(e)}
        return 200, {"point": point, "window": window, "baseline": base}

    # ---- auth
    def login(self, body):
        body = body if isinstance(body, dict) else {}
        return self.auth.login(body.get("username"), body.get("password"), body.get("code"))

    def reset_password(self, body):
        """POST /api/reset {username, code, new}: spend a code from an admin and choose a new password. Needs no sign-in."""
        body = body if isinstance(body, dict) else {}
        name = body.get("username")
        try:
            check_password_strength(body.get("new"))  # before the code is spent, so a weak password can be retried
            if not (isinstance(name, str) and self.store.user(name)):
                raise AuthError("that reset code is wrong or has expired", 400)
            self.auth.redeem_reset(name, body.get("code"))
        except AuthError as ex:
            raise ApiError(ex.status, str(ex))
        self.set_password(name, body["new"])
        self.store.audit(name, "password.reset.done")

    # ---- API
    def handle(self, method, path, query, body, ident):
        m = ROUTE.match(path)
        if not m:
            raise ApiError(404, "not found")
        a, b, c = m["a"], m["b"], m["c"]
        if body is not None and not isinstance(body, (dict, str)):  # a JSON body is an object; imports arrive as text
            raise ApiError(400, "body must be a JSON object")
        admin = "superadmin" in ident["roles"]
        user = "user" in ident["roles"]

        def need(ok):
            if not ok:
                raise ApiError(403 if ident["name"] else 401, "not allowed" if ident["name"] else "log in first")

        scope = ident.get("scope")  # set only for machine tokens: they get the data plane, never administration
        if scope and not self._scope_allows(scope, ident, method, a, b, c):
            raise ApiError(403, f"this {scope} token does not allow that")
        if a == "ingest" and b:
            return self.ingest(method, b, query, body, ident)
        if (method, a) == ("GET", "value") and b and c is None:
            p, s = self.visible_points(ident).get(b, (None, None))
            if p is None:
                raise ApiError(404, "unknown point")
            tags, unit, display_unit, display_value = self.resolve_point(p, s)
            return 200, {"name": p.name, "value": p.last, "ts": p.last_ts, "quality": Quality(p.quality).name.lower(), "unit": unit,
                         "tags": tags, "source": s.name, "display_unit": display_unit, "display_value": display_value}
        if (method, a, b, c) == ("GET", "me", None, None):
            return 200, {"name": ident["name"], "roles": sorted(ident["roles"]), "mfa": bool(ident["name"] and (self.store.user(ident["name"]) or {}).get("totp"))}
        if (method, a, b, c) == ("GET", "health", None, None):
            # The data plane's own state. A stalled loop is the one failure a console cannot otherwise see.
            faults = self.outputs.faults() if self.outputs else []
            return 200, {"ok": self.tick_failures == 0 and not faults, "tick_failures": self.tick_failures, "tick_error": self.tick_error,
                         "tick_ok_ts": self.tick_ok_ts, "buffered_samples": len(self.pending), "lost_samples": self.lost,
                         "unstored_events": len(self.unstored), "events_lost": self.events_lost, "failed_deliveries": self.outputs.failed if self.outputs else 0,
                         "faults": faults}
        if (method, a, c) == ("GET", "jobs", None) and b:
            rec = self.jobs.get(int(b) if b.isdigit() else -1)
            if rec is None:
                raise ApiError(404, "unknown job")
            if "superadmin" not in ident["roles"] and rec.get("owner") != ident["name"]:
                raise ApiError(403, "not allowed")
            return 200, {k: rec.get(k) for k in ("id", "name", "state", "result", "error", "started", "finished")}
        if (method, a) == ("GET", "points") and b is None:
            return 200, [self.point_view(p, s) for p, s in self.select_points(ident, query).values()]
        if (method, a, c) == ("GET", "points", "forecast") and b:
            return self.forecast_view(ident, b, query)
        if (method, a, c) == ("GET", "points", "baseline") and b:
            return self.baseline_view(ident, b, query)
        if (method, a, c) == ("GET", "points", None) and b:
            p, s = self.visible_points(ident).get(b, (None, None))
            if p is None:
                raise ApiError(404, "unknown point")
            return 200, {**self.point_view(p, s), "source_type": s.meta.get("type"), "source_down": s.down_reason,
                         "detectors": p.meta.get("detector_cfg", []), "hold": self.holds.get(b),
                         "maintenance": [{"name": m["name"], "mode": m["mode"]} for m in self.windows if windows.active(m, time.time()) and windows.covers(m, b, self.resolve_point(p, s)[0], s.name)]}
        if a == "notes" and b and c is None and method in ("GET", "POST"):
            p0, _s = self.visible_points(ident).get(b, (None, None))
            if p0 is None:
                raise ApiError(404, "unknown point")
            if method == "GET":
                return 200, notes.for_point(self.store, b)
            need(user)
            errs = notes.validate(body)
            if errs:
                raise ApiError(422, "validation failed", errs)
            n = notes.add(self.store, b, body["text"], ident["name"])
            self.store.audit(ident["name"], "note.add", b)
            return 201, n
        if (method, a, b) == ("GET", "reports", "summary"):
            return self.report_summary(ident, query)
        if (method, a, c) == ("POST", "points", "set"):
            p, s = self.points().get(b, (None, None))
            if p is None or not can(ident["roles"], p.meta.get("read_roles", ["user"])):
                raise ApiError(404, "unknown point")
            need(p.meta.get("settable") and can(ident["roles"], p.meta.get("set_roles", [])))
            v = (body or {}).get("value", "missing")
            if p.text:
                if v is not None and not (isinstance(v, str) and 1 <= len(v) <= 255 and v.isprintable()):
                    raise ApiError(400, "value must be printable text of 1 to 255 characters, or null to release the point")
            elif v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)):
                raise ApiError(400, "value must be a number, or null to release the point")
            if not hasattr(s, "write"):
                raise ApiError(400, "this source cannot write")
            self.check_setpoint(p, v)
            reason = (body or {}).get("reason")
            if reason is not None and not (isinstance(reason, str) and len(reason) <= 200 and reason.isprintable()):
                raise ApiError(400, "reason must be printable text of up to 200 characters")
            reason = (reason or "").strip()
            if p.meta.get("require_reason") and not reason:
                raise ApiError(422, f"{p.name} needs a reason for every write: send reason")
            if p.meta.get("confirm") and v is not None and not self.confirmed(ident, p.name, v, (body or {}).get("confirm")):
                return 202, {"confirm": self.confirm_token(ident, p.name, v), "expires_in": CONFIRM_SECONDS,
                             "message": f"Repeat the request with this confirm token to set {p.name} to {v}"}
            self.perform_write(p, s, v, ident["name"], (f" via token {ident['token']}" if scope else "") + (f" reason: {reason}" if reason else ""))
            return 200, {"ok": True}
        if (method, a) == ("GET", "series"):
            return 200, self.series(ident, query)
        if (method, a, b) == ("GET", "export", "points"):
            need(admin)
            return 200, Raw(pointcsv.export(self.store.list("point")), "text/csv; charset=utf-8", "points.csv")
        if (method, a, b) == ("POST", "import", "points"):
            need(admin)
            return self.import_points(query, body, ident)
        if (method, a, b) == ("GET", "export", "history"):
            return self.history_csv(ident, query)
        if (method, a, b, c) == ("GET", "events", "search", None):
            return self.event_journal(ident, query)
        if (method, a, c) == ("GET", "events", None) and b is None:
            ok = self.event_points(ident)
            point = query.get("point")
            rows = [e for e in self.store.events(300 if not point else 50, point) if e["point"] in ok][:100]
            for e in rows:
                e["level_name"], e["state"] = Level(e["level"]).name, alarm_state(e["acked_ts"], e["cleared_ts"])
            return 200, rows
        if (method, a, b) == ("POST", "test", "source"):
            need(admin)
            if query.get("wait") == "1":  # scripts and old callers keep the synchronous answer
                return 200, test_source(body)
            jid = self.jobs.submit("test source", lambda: test_source(body), ident["name"])
            return 202, {"job": jid, "state": "running", "message": f"the source test is running; ask GET /api/jobs/{jid}"}
        if a == "alarms":
            return self.alarm_holds(method, b, c, body, ident, query)
        if (method, a, b, c) == ("POST", "events", "ack-many", None):
            need(user)
            ids = (body or {}).get("ids")
            if not (isinstance(ids, list) and 1 <= len(ids) <= 200 and all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
                raise ApiError(400, "send {ids: [1 to 200 alarm ids]}")
            ok, now, n = self.event_points(ident), time.time(), 0
            for row in self.store.q(f"select id, point from events where id in ({', '.join('?' * len(ids))})", ids):
                if row["point"] in ok:  # an id you cannot see is skipped, not an error
                    n += self.store.ack_event(row["id"], ident["name"], now)
            self.store.audit(ident["name"], "ack", f"{n} alarm(s) at once")
            return 200, {"acked": n}
        if (method, a, c) == ("POST", "events", "ack"):
            need(user)
            ev = self.store.q("select point from events where id=?", (int(b) if b.isdigit() else -1,))
            if not ev or ev[0]["point"] not in self.event_points(ident):
                raise ApiError(404, "unknown event")
            n = self.store.ack_event(int(b), ident["name"], time.time())
            self.store.audit(ident["name"], "ack", f"event {b}")
            return (200 if n else 409), {"acked": n}
        if (method, a) == ("GET", "sources") and b is None:
            return 200, [self.source_view(s) for s in self.engine.sources
                         if can(ident["roles"], s.meta.get("read_roles", ["user"]))]
        if (method, a) == ("GET", "watchlists") and b is None:
            return 200, [{**w, "mine": w.get("owner") == ident["name"]} for w in self.store.list("watchlist") if self.watch_visible(ident, w)]
        if a == "mylists" and b and c is None and method in ("PUT", "DELETE"):
            need(user)
            return self.my_list(method, b, body, ident)
        if a == "dashboards" and c is None:
            return self.dashboards(method, b, body, ident)
        if a == "mfa" and method == "POST" and b in ("setup", "enable", "disable") and c is None:  # a second sign-in factor (TOTP) for your own account
            need(ident["name"])
            body = body or {}
            u = self.store.user(ident["name"])
            try:
                if b == "setup":
                    if u["totp"]:
                        raise ApiError(409, "already enabled; disable it first")
                    return 200, self.auth.mfa_setup(ident["name"])
                if b == "enable":
                    self.auth.mfa_enable(ident["name"], body.get("code"))
                    return 200, {"ok": True}
                if not verify_password(str(body.get("password", "")), u["hash"]):
                    raise ApiError(403, "password is wrong")
                self.store.run("update users set totp=null where name=?", (ident["name"],))
                self.store.audit(ident["name"], "mfa.disable")
                return 200, {"ok": True}
            except AuthError as ex:
                raise ApiError(ex.status, str(ex))
        if (method, a) == ("POST", "password") and b is None:
            need(ident["name"])
            body = body or {}
            u = self.store.user(ident["name"])
            if not verify_password(str(body.get("old", "")), u["hash"]):
                raise ApiError(403, "current password is wrong")
            self.set_password(ident["name"], body.get("new"))
            return 200, {"ok": True}

        # everything below is administration
        need(admin)
        if a == "config" and b in KINDS:
            return self.config(method, b, c, body, ident)
        if (method, a, c) == ("POST", "users", "reset") and b:  # a one-time code for a user who cannot sign in
            if not self.store.user(b):
                self._404()
            self.store.audit(ident["name"], "password.reset.issue", b)
            return 200, {"code": self.auth.issue_reset(b), "expires_in": 3600}
        if a == "users":
            return self.users(method, b, body, ident)
        if (method, a, b) == ("GET", "outputs", None):
            # Every handler and publisher with how its deliveries are going: a dead route must not be invisible.
            st = self.outputs.status if self.outputs else {}
            rows = []
            for kind in ("handler", "publisher"):
                for o in self.store.list(kind):
                    s = st.get(o["name"], {})
                    rows.append({"name": o["name"], "kind": kind, "type": o.get("type"), "enabled": o.get("enabled", True),
                                 "failed": s.get("failed", 0), "last_error": s.get("last_error"), "last_ok": s.get("last_ok"),
                                 "since": s.get("since")})
            return 200, {"failed_deliveries": self.outputs.failed if self.outputs else 0, "targets": rows}
        if (method, a) == ("GET", "audit"):
            return 200, self.store.audit_log()
        if a == "tokens" and c is None:
            return self.tokens(method, b, body, ident)
        if a == "database":
            return self.database(method, b, c, body, ident, query)
        if (method, a) == ("GET", "export") and b is None:
            doc = json.dumps(self.maint.export_config(), indent=2).encode()
            return 200, Raw(doc, "application/json", "durian-config-" + dt.date.today().isoformat() + ".json")
        if (method, a) == ("POST", "import") and b is None:
            return self.import_config(query, body, ident)
        if (method, a, b) == ("POST", "import", "history"):
            return self.import_history(query, body, ident)
        raise ApiError(404, "not found")

    # ---- machine access: scoped tokens, and values pushed in over HTTP
    READ_AREAS = {"me", "health", "points", "value", "series", "events", "sources", "dashboards", "watchlists", "reports"}

    @classmethod
    def _scope_allows(cls, scope, ident, method, a, b, c):
        if scope == "ingest":
            return a == "ingest" and b == ident["source"]  # an ingest token can reach exactly one source, nothing else
        if method == "GET":
            return a in cls.READ_AREAS or (a == "export" and b == "history")
        return scope == "write" and (method, a, c) == ("POST", "points", "set")

    def ingest(self, method, source, query, body, ident):
        if ident.get("scope") != "ingest":  # also keeps browser sessions out: a page could otherwise be made to push values
            raise ApiError(403 if ident.get("name") else 401, "pushing values needs an ingest token, not a browser session")
        s = next((x for x in self.engine.sources if x.name == source and isinstance(x, HttpInSource)), None)
        if s is None:
            raise ApiError(404, "unknown ingest source")
        try:
            candidates = parse_payload(body if method == "POST" else None, query)
        except ValueError as e:
            raise ApiError(400, str(e))
        accepted, rejected = s.submit(candidates)
        return (200 if accepted else 422), {"accepted": accepted, "rejected": rejected}

    def tokens(self, method, name, body, ident):
        now = time.time()
        if method == "GET" and name is None:
            return 200, [{**t, "state": "disabled" if t["disabled"] else "expired" if t["expires"] and t["expires"] < now else "active"}
                         for t in self.store.tokens()]
        if method == "POST" and name is None:
            b = body if isinstance(body, dict) else {}
            errs, add = [], lambda p, m: errs.append({"property": p, "message": m})
            tname, owner, scope, source, days = b.get("name"), b.get("owner") or ident["name"], b.get("scope"), b.get("source"), b.get("days")
            if not (isinstance(tname, str) and re.match(r"^[A-Za-z0-9_.-]{1,64}$", tname)):
                add("name", "use 1 to 64 letters, digits, dot, dash or underscore")
            if scope not in Auth.TOKEN_SCOPES:
                add("scope", f"must be one of {list(Auth.TOKEN_SCOPES)}")
            if not (isinstance(owner, str) and self.store.user(owner)):
                add("owner", "must be an existing user: the token acts with that user's roles")
            src = self.store.get("source", source) if isinstance(source, str) else None
            if scope == "ingest" and not (src and src.get("type") == "http_in"):
                add("source", "an ingest token needs the name of a configured http_in source")
            if scope != "ingest" and source is not None:
                add("source", "only ingest tokens are tied to a source")
            if days is not None and not (isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 3650):
                add("days", "must be 1 to 3650, or leave out for a token that does not expire")
            if errs:
                raise ApiError(422, "validation failed", errs)
            if any(t["name"] == tname for t in self.store.tokens()):
                raise ApiError(409, "a token with that name exists")
            token = self.auth.create_token(tname, owner, scope, source if scope == "ingest" else None, days)
            self.store.audit(ident["name"], "token.create", f"{tname} ({scope}, owner {owner})")
            return 201, {"name": tname, "token": token, "note": "Copy it now. It is shown once and only its hash is kept."}
        if name is None:
            raise ApiError(400, "give a token name in the URL")
        if method == "PUT":
            if not (isinstance(body, dict) and isinstance(body.get("disabled"), bool)):
                raise ApiError(422, "validation failed", [{"property": "disabled", "message": "must be true or false"}])
            if not self.store.set_token_disabled(name, body["disabled"]):
                raise ApiError(404, "unknown token")
            self.store.audit(ident["name"], "token.disable" if body["disabled"] else "token.enable", name)
            return 200, {"name": name, "disabled": body["disabled"]}
        if method == "DELETE":
            if not self.store.delete_token(name):
                raise ApiError(404, "unknown token")
            self.store.audit(ident["name"], "token.delete", name)
            return 200, {"deleted": name}
        raise ApiError(405, "method not allowed")

    # ---- shelving and out of service
    def alarm_holds(self, method, point, action, body, ident, query=None):
        """GET /api/alarms lists holds. POST /api/alarms/<point>/shelve|unshelve (users), out-of-service|in-service (superadmin)."""
        pts = self.visible_points(ident)
        if (method, point, action) == ("GET", None, None):
            return 200, [h for h in self.holds.values() if h["name"] in pts]
        if (method, point, action) == ("GET", "kpis", None):
            h = (query or {}).get("hours", "24")
            return 200, self.alarm_kpis(pts, "superadmin" in ident["roles"], int(h) if h.isdigit() else 24, extra=self.event_points(ident))
        if method != "POST" or action not in ("shelve", "unshelve", "out-of-service", "in-service"):
            raise ApiError(404, "not found")
        if not ident["name"]:
            raise ApiError(401, "log in first")
        if point not in pts:
            raise ApiError(404, "unknown point")
        if action in ("out-of-service", "in-service"):
            if "superadmin" not in ident["roles"]:
                raise ApiError(403, "only a superadmin can take a point out of service")
        elif "user" not in ident["roles"]:
            raise ApiError(403, "not allowed")
        body, now, cur = body if isinstance(body, dict) else {}, time.time(), self.holds.get(point)
        reason, p = str(body.get("reason", "")).strip(), pts[point][0]
        if action in ("shelve", "out-of-service"):
            if not 3 <= len(reason) <= 200:
                raise ApiError(422, "give a reason of 3 to 200 characters")
            if action == "shelve":
                m = body.get("minutes")
                if not (isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 1440):
                    raise ApiError(422, "minutes must be a whole number from 1 to 1440")
                if cur and cur["kind"] == "oos":
                    raise ApiError(409, "this point is out of service")
                h = {"kind": "shelved", "user": ident["name"], "reason": reason, "created": now, "until": now + m * 60}
            else:
                h = {"kind": "oos", "user": ident["name"], "reason": reason, "created": now, "until": None}
        elif not cur or cur["kind"] != ("shelved" if action == "unshelve" else "oos"):
            raise ApiError(409, "nothing to release")
        with self.engine_lock:
            if action in ("shelve", "out-of-service"):
                self.store.put("alarmhold", point, h)
                self.holds[point] = {"name": point, **h}
            else:
                self.store.delete("alarmhold", point)
                del self.holds[point]
            p.in_service = self.holds.get(point, {}).get("kind") != "oos"
            if action == "out-of-service":
                for d in p.detectors:
                    d.reset()
                for ev in self.open_events:  # no longer in alarm: the next tick stores the return to normal
                    if ev.point == point and ev.cleared_ts is None:
                        ev.cleared_ts = now
            self._apply_holds(now)
        self.store.audit(ident["name"], "alarm." + action, f"{point}: {reason}" if reason else point)
        return 200, {"point": point, "action": action}

    def event_journal(self, ident, q):
        """GET /api/events/search: ?state=all|unacked|active|closed &level=WARNING &hours=24 &q=text &limit=50 &offset=0 &format=csv"""
        state = q.get("state", "all")
        if state not in ("all", "unacked", "active", "closed"):
            raise ApiError(400, "state must be all, unacked, active or closed")
        try:
            level = Level[q["level"].upper()] if q.get("level") else Level.NONE
            hours = float(q["hours"]) if q.get("hours") else None
            limit, offset = min(int(q.get("limit", 50)), 200 if q.get("format") != "csv" else 5000), max(int(q.get("offset", 0)), 0)
        except (KeyError, ValueError):
            raise ApiError(400, "level must be an alarm level name; hours, limit and offset must be numbers")
        allowed = self.event_points(ident)
        if len(allowed) > 900:  # too many for an IN list: ask for everything and filter here
            rows, total = self.store.events_search(None, int(level), state, time.time() - hours * 3600 if hours else None, None, q.get("q"), 100000, 0)
            rows = [e for e in rows if e["point"] in allowed]
            total, rows = len(rows), rows[offset:offset + limit]
        else:
            rows, total = self.store.events_search(allowed, int(level), state, time.time() - hours * 3600 if hours else None, None, q.get("q"), limit, offset)
        for e in rows:
            e["level_name"], e["state"] = Level(e["level"]).name, alarm_state(e["acked_ts"], e["cleared_ts"])
        if q.get("format") != "csv":
            return 200, {"items": rows, "total": total, "offset": offset, "limit": limit}
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        iso = lambda t: "" if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        w.writerow(["raised_utc", "point", "level", "message", "state", "held", "acknowledged_by", "acknowledged_utc", "returned_to_normal_utc"])
        for e in rows:
            w.writerow([iso(e["ts"]), e["point"], e["level_name"], e["message"], e["state"], e["held"] or "", e["acked_by"] or "", iso(e["acked_ts"]), iso(e["cleared_ts"])])
        return 200, Raw(out.getvalue().encode(), "text/csv; charset=utf-8", "alarms.csv")

    def event_points(self, ident):
        """Names an identity may see alarms for: its points, the sources it may read, and DurianDB itself for a superadmin."""
        ok = set(self.visible_points(ident))
        ok |= {SOURCE + s.name for s in self.engine.sources if can(ident["roles"], s.meta.get("read_roles", ["user"]))}
        return ok | ({SYSTEM} if "superadmin" in ident["roles"] else set())

    def alarm_kpis(self, pts, admin, hours, now=None, extra=()):
        """Alarm system performance over the last `hours`, in the spirit of ISA-18.2 / EEMUA 191. Only points the caller can see."""
        now = now or time.time()
        hours = hours if isinstance(hours, (int, float)) and 1 <= hours <= 720 else 24
        rows = [e for e in self.store.events_for_kpis(now - hours * 3600) if e["point"] in pts or e["point"] in extra or (e["point"] == SYSTEM and admin)]
        inwin = [e for e in rows if e["ts"] >= now - hours * 3600]
        buckets = Counter(int(e["ts"] // 600) for e in inwin)  # 10-minute buckets: the unit EEMUA 191 counts alarm floods in
        per_point, chatter = Counter(e["point"] for e in inwin), Counter()
        for (_, p), n in Counter((int(e["ts"] // 600), e["point"]) for e in inwin).items():
            if n >= 3:
                chatter[p] += 1  # three or more alarms of one point inside 10 minutes
        waits = sorted(e["acked_ts"] - e["ts"] for e in inwin if e["acked_ts"] is not None)
        active = [e for e in rows if e["cleared_ts"] is None]
        return {"hours": hours, "alarms": len(inwin), "per_hour": round(len(inwin) / hours, 2),
                "peak_per_10min": max(buckets.values(), default=0),
                "flood_10min_periods": sum(1 for n in buckets.values() if n > 10),  # more than 10 alarms in 10 minutes
                "top_points": [{"point": p, "alarms": n} for p, n in per_point.most_common(5)],
                "chattering_points": [{"point": p, "periods": n} for p, n in chatter.most_common(5)],
                "median_seconds_to_acknowledge": waits[len(waits) // 2] if waits else None,
                "unacknowledged_now": sum(1 for e in rows if e["acked_ts"] is None),
                "active_now": len(active), "stale_now": sum(1 for e in active if e["ts"] < now - 86400),  # active for over 24 h
                "held_now": sum(1 for e in active if e["held"]),
                "shelved_points": sum(1 for h in self.holds.values() if h["kind"] == "shelved" and h["name"] in pts),
                "out_of_service_points": sum(1 for h in self.holds.values() if h["kind"] == "oos" and h["name"] in pts)}

    # ---- database health, retention, backups, import and export (superadmin)
    def database(self, method, what, name, body, ident, query):
        if method == "GET" and what is None:
            return 200, self.maint.status(len(self.pending), self.lost)
        if (method, what) == ("PUT", "settings"):
            errs = self.maint.update_settings(body)
            if errs:
                raise ApiError(422, "validation failed", errs)
            self.store.audit(ident["name"], "database.settings", json.dumps(body))
            return 200, self.maint.settings()
        if (method, what) == ("POST", "purge"):
            r = self.maint.purge()
            self.store.audit(ident["name"], "database.purge", f"{r['deleted']} rows")
            return 200, r
        if (method, what) == ("POST", "backup"):
            if query.get("wait") == "1":  # scripts and old callers keep the synchronous answer
                try:
                    r = self.maint.backup()
                except NotImplementedError as e:
                    raise ApiError(501, str(e))
                self.store.audit(ident["name"], "database.backup", r["name"])
                return 200, r
            jid = self.jobs.submit("backup", lambda: self._backup(ident["name"]), ident["name"])
            return 202, {"job": jid, "state": "running", "message": f"the backup is running; ask GET /api/jobs/{jid}"}
        if (method, what) == ("GET", "backups") and name:
            p = self.maint.backup_file(name)
            if p is None:
                raise ApiError(404, "unknown backup")
            self.store.audit(ident["name"], "database.download", name)
            return 200, Raw(p.read_bytes(), "application/octet-stream", name)
        raise ApiError(404, "not found")

    def _backup(self, who):
        """The backup job, run off the request thread and audited when it finishes."""
        r = self.maint.backup()
        self.store.audit(who, "database.backup", r["name"])
        return r

    def import_config(self, query, doc, ident):
        validators = {"source": lambda n, b, v: validate_source(n, b), "point": lambda n, b, v: validate_point(n, b, v),
                      "handler": lambda n, b, v: validate_handler(n, b), "publisher": lambda n, b, v: validate_publisher(n, b, v),
                      "watchlist": lambda n, b, v: validate_watchlist(n, b), "maintenance": lambda n, b, v: validate_maintenance(n, b),
                      "dashboard": lambda n, b, v: validate_dashboard(n, b, v, roles_ok)[0]}
        apply_ = query.get("apply") == "1"  # a plain call is always a dry run
        errs, summary = self.maint.import_config(doc, validators, query.get("mode", "merge"), not apply_, ident["name"])
        if errs:
            raise ApiError(422, "validation failed", errs)
        if apply_:
            self.store.audit(ident["name"], "config.import", json.dumps({k: v for k, v in summary.items() if any(v.values())}))
            self.reload()
        return 200, {"applied": apply_, "summary": summary}

    def import_points(self, query, text, ident):
        """Points from a spreadsheet. Every row is checked first; a plain call is a dry run, ?apply=1 writes and reloads once."""
        if not isinstance(text, str):
            raise ApiError(400, "send the CSV as text (Content-Type text/csv)")
        rows, errs = pointcsv.parse(text)
        for line, (name, body) in enumerate(rows, 2):
            for e in validate_point(name, body, self.store):
                errs.append({"line": line, "point": name, "property": e["property"], "message": e["message"]})
        if errs:
            raise ApiError(422, "validation failed", errs[:100])
        have = {p["name"] for p in self.store.list("point")}
        summary = {"created": sum(1 for n, _ in rows if n not in have), "updated": sum(1 for n, _ in rows if n in have)}
        if query.get("apply") == "1":
            with self.engine_lock:
                for name, body in rows:
                    self.store.put("point", name, body)
                self.store.audit(ident["name"], "points.import", json.dumps(summary))
                self.reload()
        return 200, {"applied": query.get("apply") == "1", **summary}

    @staticmethod
    def watch_visible(ident, w):
        """A shared watch list follows its read roles; a personal one (it has an owner) is seen only by that user."""
        return w.get("owner") == ident["name"] if w.get("owner") else can(ident["roles"], w.get("read_roles", ["user"]))

    MAX_MY_LISTS = 50

    def my_list(self, method, name, body, ident):
        """A user's own watch list, kept in the same table as the shared ones: only its owner can change or see it."""
        old = self.store.get("watchlist", name)
        if old is not None and old.get("owner") != ident["name"]:
            raise ApiError(409, "a watch list with that name already exists, choose another name")
        if method == "DELETE":
            if old is None:
                raise ApiError(404, "no such watch list")
            self.store.delete("watchlist", name)
            return 200, {"deleted": name}
        if not isinstance(body, dict):
            raise ApiError(400, "send {tags, points}")
        errs = validate_watchlist(name, {"tags": body.get("tags", {}), "points": body.get("points", [])})
        if errs:
            raise ApiError(422, "invalid watch list", errs)
        if old is None and sum(w.get("owner") == ident["name"] for w in self.store.list("watchlist")) >= self.MAX_MY_LISTS:
            raise ApiError(409, f"at most {self.MAX_MY_LISTS} personal watch lists")
        self.store.put("watchlist", name, {"owner": ident["name"], "read_roles": [], "tags": body.get("tags", {}), "points": body.get("points", [])[:500]})
        return 200, {"saved": name}

    def select_points(self, ident, query):
        """Points the caller may see, narrowed by ?watchlist= and ?tag=key:value."""
        pts = self.visible_points(ident)
        tag, wl, folder = query.get("tag"), query.get("watchlist"), query.get("folder")
        if folder:  # a folder and everything below it
            pts = {n: ps for n, ps in pts.items() if (ps[0].meta.get("folder", "") + "/").startswith(folder.strip("/") + "/")}
        if wl:
            w = self.store.get("watchlist", wl)
            if not w or not self.watch_visible(ident, w):
                raise ApiError(404, "unknown watch list")
            pts = {n: ps for n, ps in pts.items() if n in w.get("points", [])
                   or watch_match(w, n, self.resolve_point(ps[0], ps[1])[0], ps[0].meta.get("description", ""))}
        if tag:
            k, _, v = tag.partition(":")
            pts = {n: ps for n, ps in pts.items() if self.resolve_point(ps[0], ps[1])[0].get(k) == v}
        return pts

    REPORT_MAX_POINTS = 200
    SYSTEM = {"name": "system", "roles": {"superadmin", "user", "anonymous"}}

    def escalate(self, now=None):
        """Call once a minute. An alarm still active and unacknowledged `escalate_after` minutes after it was raised is mailed to a
        handler's escalate_to list, once. Held (shelved, suppressed) alarms are not announced, so they do not escalate."""
        now = now or time.time()
        for h in self.store.list("handler"):
            if h.get("type") != "email" or not h.get("enabled", True) or "escalate_after" not in h:
                continue
            for ev in self.open_events:
                key = (h["name"], ev.id)
                if (ev.id is None or ev.held or ev.cleared_ts is not None or key in self.escalated or now - ev.ts < h["escalate_after"] * 60
                        or ev.level < Level[h.get("min_level", "WARNING")] or not self.outputs._in_scope(h, ev)):
                    continue
                if self.store.q("select acked_ts from events where id=?", (ev.id,))[0]["acked_ts"] is None:
                    self.escalated.add(key)
                    self.outputs.escalate(h, ev)
                    self.store.audit("system", "alarm.escalate", f"{ev.point} -> {h['name']}")

    def send_reports(self, now=None):
        """Email each report handler's CSV summary once a day, from its hour on. Call once a minute. Returns the names sent."""
        now = now or time.time()
        local, sent = dt.datetime.fromtimestamp(now), []
        for h in self.store.list("handler"):
            key = f"last_report_{h['name']}"
            if h.get("type") != "report" or not h.get("enabled", True) or local.hour < h["hour"] or self.store.get_setting(key) == local.strftime("%Y-%m-%d"):
                continue
            self.store.set_setting(key, local.strftime("%Y-%m-%d"))  # first, so a failure waits for tomorrow instead of retrying every minute
            q = {"hours": h.get("hours", 24), "format": "csv", **({"watchlist": h["watchlist"]} if h.get("watchlist") else {})}
            try:
                self.outputs.send_report(h, self.report_summary(self.SYSTEM, q)[1].data, q["hours"])
                sent.append(h["name"])
            except ApiError as e:
                self.store.audit("system", "report.failed", f"{h['name']}: {e}")
        return sent

    def report_summary(self, ident, query):
        """One row per point for a time window: samples, min, max, average, first, last, bad samples and alarms. ?format=csv downloads it."""
        now = time.time()
        try:
            end = float(query.get("to", now))
            start = float(query.get("from", end - float(query.get("hours", 24)) * 3600))
        except ValueError:
            raise ApiError(400, "from, to and hours are numbers")
        if not 0 < end - start <= 31 * 86400:
            raise ApiError(400, "the window must be positive and at most 31 days")
        pts = self.select_points(ident, query)
        if len(pts) > self.REPORT_MAX_POINTS:
            raise ApiError(413, f"{len(pts)} points selected, narrow it to {self.REPORT_MAX_POINTS} with a watch list or tag")
        alarms, items = self.store.alarm_counts(start, end), []
        for n in sorted(pts):
            p = pts[n][0]
            r = self.store.history_summary(n, start, end)
            items.append({"point": n, "unit": self.resolve_point(p, pts[n][1])[1], "description": p.meta.get("description", ""), "samples": r["n"],
                          "min": r["lo"], "max": r["hi"], "avg": r["mean"], "first": r.get("first"), "last": r.get("last"),
                          "bad_samples": r["bad"], "alarms": alarms.get(n, 0)})
        doc = {"from": start, "to": end, "items": items}
        if query.get("format") != "csv":
            return 200, doc
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        w.writerow(["point", "unit", "description", "samples", "min", "max", "avg", "first", "last", "bad_samples", "alarms", "from", "to"])
        for i in items:
            w.writerow([i["point"], i["unit"], i["description"], i["samples"], *(("" if i[k] is None else repr(float(i[k]))) for k in ("min", "max", "avg", "first", "last")),
                        i["bad_samples"], i["alarms"], iso(start), iso(end)])
        return 200, Raw(out.getvalue().encode(), "text/csv; charset=utf-8", "report.csv")

    def history_csv(self, ident, query):
        point = query.get("point")
        if point not in self.visible_points(ident):
            raise ApiError(404, "unknown point")
        now = time.time()
        try:
            start, end = float(query.get("from", now - 86400)), float(query.get("to", now))
        except ValueError:
            raise ApiError(400, "from and to are epoch seconds")
        data = self.maint.export_history(point, start, end)
        if data is None:
            raise ApiError(413, "too many rows for one file, narrow the time range")
        return 200, Raw(data, "text/csv; charset=utf-8", f"{point}.csv")

    def import_history(self, query, text, ident):
        point = query.get("point")
        if not isinstance(text, str) or point not in {p["name"] for p in self.store.list("point")} | set(self.points()):
            raise ApiError(400, "send CSV text and name an existing point")
        r = self.maint.import_history(point, text)
        self.store.audit(ident["name"], "history.import", f"{point}: {r.get('imported', 0)} rows")
        return (200 if not r.get("error") else 400), r

    # ---- dashboards: viewing follows read roles, editing follows owner, edit roles or superadmin
    MAX_DASHBOARDS = 200

    @staticmethod
    def _can_edit(ident, d):
        return bool(ident["name"]) and (d.get("owner") == ident["name"] or can(ident["roles"], d.get("edit_roles", [])))

    @staticmethod
    def _can_read(ident, d):
        return can(ident["roles"], d.get("read_roles", ["user"])) or App._can_edit(ident, d)

    def dashboards(self, method, name, body, ident):
        if method == "GET" and name is None:
            return 200, [{"name": d["name"], "title": d.get("title", d["name"]), "owner": d.get("owner"),
                          "public": "anonymous" in d.get("read_roles", []), "editable": self._can_edit(ident, d)}
                         for d in self.store.list("dashboard") if self._can_read(ident, d)]
        if name is None:
            raise ApiError(400, "give a dashboard name in the URL")
        d = self.store.get("dashboard", name)
        if d is not None and not self._can_read(ident, d):
            d = None  # hidden, same as missing
        if method == "GET":
            if d is None:
                raise ApiError(404, "unknown dashboard")
            return 200, {**d, "name": name, "editable": self._can_edit(ident, d)}
        if not ident["name"]:
            raise ApiError(401, "log in first")
        if method == "PUT":
            if not isinstance(body, dict):
                raise ApiError(400, "body must be a JSON object")
            if d is not None and not self._can_edit(ident, d):
                raise ApiError(403, "not allowed to edit this dashboard")
            if d is None and self.store.get("dashboard", name) is not None:
                raise ApiError(404, "unknown dashboard")  # exists but hidden from this user: do not reveal it
            if d is None and not ("superadmin" in ident["roles"] or "designer" in ident["roles"]):
                raise ApiError(403, "creating dashboards needs the designer role")
            if d is None and len(self.store.list("dashboard")) >= self.MAX_DASHBOARDS:
                raise ApiError(409, "too many dashboards")
            body = {k: v for k, v in body.items() if k not in ("owner", "name", "editable")}
            errs, warns = validate_dashboard(name, body, self.store, roles_ok)
            if "anonymous" in body.get("read_roles", []) and "superadmin" not in ident["roles"]:
                errs.append({"property": "read_roles", "message": "only a superadmin can publish a dashboard to anonymous visitors"})
            if errs:
                raise ApiError(422, "validation failed", errs)
            self.store.put("dashboard", name, {**body, "owner": d.get("owner") if d else ident["name"]})
            self.store.audit(ident["name"], "dashboard.put", name)
            return 200, {"saved": name, "warnings": warns}
        if method == "DELETE":
            if d is None:
                raise ApiError(404, "unknown dashboard")
            if not self._can_edit(ident, d):
                raise ApiError(403, "not allowed to delete this dashboard")
            self.store.delete("dashboard", name)
            self.store.audit(ident["name"], "dashboard.delete", name)
            return 200, {"deleted": name}
        raise ApiError(405, "method not allowed")

    def source_view(self, s):
        return {"name": s.name, "type": s.meta.get("type"), "points": len(s.points), "error": s.error,
                "down": s.down_reason, "poll_ms": s.poll_ms, "overruns": s.overruns, "connected": getattr(s, "connected", None), "dropped": getattr(s, "dropped", None)}

    def series(self, ident, q):
        point = q.get("point")
        if point not in self.visible_points(ident):
            raise ApiError(404, "unknown point")
        rollup = q.get("rollup", "avg")
        if rollup not in ALL_ROLLUPS:
            raise ApiError(400, f"rollup must be one of {', '.join(ALL_ROLLUPS)}")
        try:
            window = int(q.get("window", 3600))
            bucket = int(q.get("bucket", max(1, window // 120)))
        except ValueError:
            raise ApiError(400, "window and bucket must be whole seconds")
        if not 10 <= window <= 7 * 86400 or not 1 <= bucket <= window:
            raise ApiError(400, "window must be 10 s to 7 days and bucket between 1 s and the window")
        if self.points()[point][0].text:
            raise ApiError(400, "a text point has no numeric series; use the history export")
        items = self.store.series(point, time.time() - window, bucket, rollup)
        return {"point": point, "rollup": rollup, "window": window, "bucket": bucket, "items": items}

    def config(self, method, kind, name, body, ident):
        if method == "GET":
            return 200, self.store.list(kind) if name is None else (self.store.get(kind, name) or self._404())
        if name is None:
            raise ApiError(400, "give a name in the URL")
        if method == "PUT":
            if not isinstance(body, dict):
                raise ApiError(400, "body must be a JSON object")
            errs = {"source": lambda: validate_source(name, body), "point": lambda: validate_point(name, body, self.store),
                    "handler": lambda: validate_handler(name, body),
                    "publisher": lambda: validate_publisher(name, body, self.store),
                    "watchlist": lambda: validate_watchlist(name, body), "maintenance": lambda: validate_maintenance(name, body)}[kind]()
            if errs:
                raise ApiError(422, "validation failed", errs)
            old = self.store.get(kind, name)
            self.store.put(kind, name, body)
            try:
                self.load_windows() if kind == "maintenance" else self.reload()  # a window changes no engine object, so open alarms are left alone
            except Exception as ex:  # a config the server cannot run must not be left stored
                if old is None:
                    self.store.delete(kind, name)
                else:
                    self.store.put(kind, name, old)
                try:
                    self.reload()
                except Exception:
                    log.exception("reload failed after rolling back %s/%s", kind, name)
                raise ApiError(409, f"{kind} '{name}' was not saved: it could not be started ({type(ex).__name__}: {ex}). The previous version is still in place.")
            self.store.audit(ident["name"], "config.put", f"{kind}/{name}" + _diff(old, body))
            return 200, {"saved": name}
        if method == "DELETE":
            if kind == "source" and any(p.get("source") == name for p in self.store.list("point")):
                raise ApiError(409, "delete or move this source's points first")
            if not self.store.delete(kind, name):
                self._404()
            self.store.audit(ident["name"], "config.delete", f"{kind}/{name}")
            self.load_windows() if kind == "maintenance" else self.reload()
            return 200, {"deleted": name}
        raise ApiError(405, "method not allowed")

    def users(self, method, name, body, ident):
        if method == "GET" and name is None:
            return 200, self.store.users()
        if name is None:
            raise ApiError(400, "give a user name in the URL")
        if method == "PUT":
            body = body or {}
            roles = body.get("roles")
            if not (isinstance(roles, list) and roles and all(isinstance(r, str) and 0 < len(r) <= 32 for r in roles)):
                raise ApiError(422, "validation failed", [{"property": "roles", "message": "give at least one role"}])
            if not re.match(r"^[A-Za-z0-9_.-]{1,32}$", name):
                raise ApiError(422, "validation failed", [{"property": "name", "message": "use letters, digits, dot, dash, underscore"}])
            if name == ident["name"] and "superadmin" not in roles:
                raise ApiError(409, "you cannot remove your own superadmin role")
            if self.store.user(name):
                if body.get("password"):
                    self.set_password(name, body["password"])
                if body.get("reset_mfa"):  # lost phone: the admin clears the second factor
                    self.store.run("update users set totp=null where name=?", (name,))
                    self.store.audit(ident["name"], "mfa.reset", name)
                if isinstance(body.get("disabled"), bool):  # an account that must stop working now, e.g. someone who left
                    if body["disabled"] and name == ident["name"]:
                        raise ApiError(409, "you cannot disable your own account")
                    self.store.set_disabled(name, body["disabled"])
                    if body["disabled"]:
                        self.auth.end_sessions(name)
                    self.store.audit(ident["name"], "user.disabled" if body["disabled"] else "user.enabled", name)
                self.store.run("update users set roles=? where name=?", (json.dumps(roles), name))
            else:
                try:
                    check_password_strength(body.get("password"))
                except AuthError as ex:
                    raise ApiError(422, "validation failed", [{"property": "password", "message": str(ex)}])
                self.store.add_user(name, hash_password(body["password"]), roles)
            self.store.audit(ident["name"], "user.put", name)
            return 200, {"saved": name}
        if method == "DELETE":
            if name == ident["name"]:
                raise ApiError(409, "you cannot delete yourself")
            if not self.store.delete_user(name):
                self._404()
            self.store.run("delete from api_tokens where owner=?", (name,))  # a token must not outlive the account it acts as
            self.store.audit(ident["name"], "user.delete", name)
            return 200, {"deleted": name}
        raise ApiError(405, "method not allowed")

    def set_password(self, name, pw):
        try:
            check_password_strength(pw)
        except AuthError as ex:
            raise ApiError(422, "validation failed", [{"property": "password", "message": str(ex)}])
        self.store.set_hash(name, hash_password(pw))
        self.store.audit(name, "password.change")

    @staticmethod
    def _404():
        raise ApiError(404, "not found")
