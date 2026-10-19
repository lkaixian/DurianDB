"""Application logic: runtime engine, persistence loop, and the API as plain (status, payload) calls.

The HTTP layer in server.py only translates requests into App.handle() calls.
"""
import datetime as dt
import json
import math
import re
import time
from collections import Counter
from pathlib import Path

from .auth import Auth, AuthError, can, check_password_strength, hash_password, verify_password
from .config import (build_engine, roles_ok, validate_point, validate_source, validate_watchlist)
from .core import Event, Level, Quality, alarm_state
from .dashboards import validate_dashboard
from .ingest import HttpInSource, parse_payload
from .maintenance import Maintenance, Raw
from .outputs import Outputs, validate_handler, validate_publisher
from .store import ROLLUPS

KINDS = ("source", "point", "handler", "publisher", "watchlist")
ROUTE = re.compile(r"^/api/(?P<a>[a-z-]+)(?:/(?P<b>[^/]+))?(?:/(?P<c>[^/]+))?$")


class ApiError(Exception):
    def __init__(self, status, message, errors=None):
        super().__init__(message)
        self.status, self.errors = status, errors


SYSTEM = "(system)"  # events about DurianDB itself rather than a point, visible to superadmins only
PENDING_MAX = 200_000


class App:
    def __init__(self, store, backup_dir=None):
        self.store, self.auth = store, Auth(store)
        self.maint = Maintenance(store, backup_dir or Path("backups"))
        self.engine, self.outputs, self.open_events = None, None, []
        self.holds = {}  # point name -> {kind: shelved|oos, user, reason, created, until}; kept in the objects table so they survive restarts
        self.pending, self.lost, self.unstored, self.sys_event = [], 0, [], None  # history and events waiting for a database that failed
        self.reload()

    # ---- runtime
    def reload(self):
        """Rebuild the engine from stored config. Detector state is lost, so open events are closed."""
        with self.store.lock:
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
            self.outputs = Outputs(self.store)
            self.engine.publishers.append(self.outputs.on_value)
            self.holds = {h["name"]: h for h in self.store.list("alarmhold")}
            for name, (p, _) in self.points().items():
                p.in_service = self.holds.get(name, {}).get("kind") != "oos"

    def tick(self, ts=None):
        with self.store.lock:
            ts = ts or time.time()
            self.engine.tick(ts)
            self.pending.extend((p.name, t, v, q) for s in self.engine.sources for p in s.points.values() for t, v, q in p.take_unsaved())
            self._flush_history(ts)
            new, self.engine.events = self.unstored + self.engine.events, []
            self.unstored = []
            for i, ev in enumerate(new):
                try:
                    ev.held = self._hold_for(ev.point, self.points(), ts)
                    ev.id = self.store.add_event(ev)
                except Exception:  # the database is unavailable: keep the rest and try again next tick
                    self.unstored = new[i:]
                    break
                self.open_events.append(ev)
                if ev.held is None:
                    self.outputs.on_event(ev, "raised")
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

    def _hold_for(self, point, pts, ts):
        """Why an alarm on this point is recorded but not announced right now, or None."""
        h = self.holds.get(point)
        if h and h["kind"] == "shelved" and h["until"] > ts:
            return "shelved"
        sw = pts[point][0].meta.get("suppress_when") if point in pts else None
        other = pts.get(sw["point"], (None,))[0] if sw else None
        if other is not None and other.last is not None and other.quality != Quality.BAD:
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

    # ---- views
    def point_view(self, p, s):
        cfg = p.meta.get("detector_cfg", [])
        limit = lambda kind: next((d["limit"] for d in cfg if d.get("kind") == kind), None)
        return {"name": p.name, "source": s.name, "unit": p.meta.get("unit", ""), "tags": p.tags, "last": p.last,
                "last_ts": p.last_ts, "received": p.last_recv, "quality": Quality(p.quality).name.lower(), "settable": p.meta.get("settable", False),
                "limits": {"high": limit("HIGH_LIMIT"), "low": limit("LOW_LIMIT")},
                "history": [{"ts": t, "value": v} for t, v in list(p.cache)[-60:]]}

    def visible_points(self, ident):
        return {n: (p, s) for n, (p, s) in self.points().items() if can(ident["roles"], p.meta.get("read_roles", ["user"]))}

    # ---- auth
    def login(self, body):
        body = body or {}
        return self.auth.login(body.get("username"), body.get("password"))

    # ---- API
    def handle(self, method, path, query, body, ident):
        m = ROUTE.match(path)
        if not m:
            raise ApiError(404, "not found")
        a, b, c = m["a"], m["b"], m["c"]
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
            return 200, {"name": p.name, "value": p.last, "ts": p.last_ts, "quality": Quality(p.quality).name.lower(), "unit": p.meta.get("unit", ""), "tags": p.tags, "source": s.name}
        if (method, a, b, c) == ("GET", "me", None, None):
            return 200, {"name": ident["name"], "roles": sorted(ident["roles"])}
        if (method, a, b, c) == ("GET", "health", None, None):
            return 200, {"ok": True}
        if (method, a) == ("GET", "points") and b is None:
            pts = self.visible_points(ident)
            tag, wl = query.get("tag"), query.get("watchlist")
            if wl:
                w = self.store.get("watchlist", wl)
                if not w or not can(ident["roles"], w.get("read_roles", ["user"])):
                    raise ApiError(404, "unknown watch list")
                pts = {n: ps for n, ps in pts.items()
                       if n in w.get("points", []) or (w.get("tags") and all(ps[0].tags.get(k) == v for k, v in w["tags"].items()))}
            if tag:
                k, _, v = tag.partition(":")
                pts = {n: ps for n, ps in pts.items() if ps[0].tags.get(k) == v}
            return 200, [self.point_view(p, s) for p, s in pts.values()]
        if (method, a, c) == ("POST", "points", "set"):
            p, s = self.points().get(b, (None, None))
            if p is None or not can(ident["roles"], p.meta.get("read_roles", ["user"])):
                raise ApiError(404, "unknown point")
            need(p.meta.get("settable") and can(ident["roles"], p.meta.get("set_roles", [])))
            v = (body or {}).get("value", "missing")
            if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)):
                raise ApiError(400, "value must be a number, or null to release the point")
            if not hasattr(s, "write"):
                raise ApiError(400, "this source cannot write")
            try:
                with self.store.lock:
                    s.write(p.name, v)
            except RuntimeError as ex:
                raise ApiError(502, str(ex))
            if hasattr(s, "override"):  # keep held values across engine rebuilds and restarts
                self.store.put("override", p.name, {"value": v}) if v is not None else self.store.delete("override", p.name)
            self.store.audit(ident["name"], "set", f"{p.name}={v}" + (f" via token {ident['token']}" if scope else ""))
            return 200, {"ok": True}
        if (method, a) == ("GET", "series"):
            return 200, self.series(ident, query)
        if (method, a, b) == ("GET", "export", "history"):
            return self.history_csv(ident, query)
        if (method, a, c) == ("GET", "events", None) and b is None:
            ok = self.visible_points(ident)
            rows = [e for e in self.store.events(300) if e["point"] in ok or (e["point"] == SYSTEM and admin)][:100]
            for e in rows:
                e["level_name"], e["state"] = Level(e["level"]).name, alarm_state(e["acked_ts"], e["cleared_ts"])
            return 200, rows
        if a == "alarms":
            return self.alarm_holds(method, b, c, body, ident, query)
        if (method, a, c) == ("POST", "events", "ack"):
            need(user)
            ev = self.store.q("select point from events where id=?", (int(b) if b.isdigit() else -1,))
            if not ev or not (ev[0]["point"] in self.visible_points(ident) or (ev[0]["point"] == SYSTEM and admin)):
                raise ApiError(404, "unknown event")
            n = self.store.ack_event(int(b), ident["name"], time.time())
            self.store.audit(ident["name"], "ack", f"event {b}")
            return (200 if n else 409), {"acked": n}
        if (method, a) == ("GET", "sources") and b is None:
            return 200, [self.source_view(s) for s in self.engine.sources
                         if can(ident["roles"], s.meta.get("read_roles", ["user"]))]
        if (method, a) == ("GET", "watchlists") and b is None:
            return 200, [w for w in self.store.list("watchlist") if can(ident["roles"], w.get("read_roles", ["user"]))]
        if a == "dashboards" and c is None:
            return self.dashboards(method, b, body, ident)
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
        if a == "users":
            return self.users(method, b, body, ident)
        if (method, a) == ("GET", "audit"):
            return 200, self.store.audit_log()
        if a == "tokens" and c is None:
            return self.tokens(method, b, body, ident)
        if a == "database":
            return self.database(method, b, c, body, ident)
        if (method, a) == ("GET", "export") and b is None:
            doc = json.dumps(self.maint.export_config(), indent=2).encode()
            return 200, Raw(doc, "application/json", "durian-config-" + dt.date.today().isoformat() + ".json")
        if (method, a) == ("POST", "import") and b is None:
            return self.import_config(query, body, ident)
        if (method, a, b) == ("POST", "import", "history"):
            return self.import_history(query, body, ident)
        raise ApiError(404, "not found")

    # ---- machine access: scoped tokens, and values pushed in over HTTP
    READ_AREAS = {"me", "health", "points", "value", "series", "events", "sources", "dashboards", "watchlists"}

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
            return 200, self.alarm_kpis(pts, "superadmin" in ident["roles"], int(h) if h.isdigit() else 24)
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
        with self.store.lock:
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

    def alarm_kpis(self, pts, admin, hours, now=None):
        """Alarm system performance over the last `hours`, in the spirit of ISA-18.2 / EEMUA 191. Only points the caller can see."""
        now = now or time.time()
        hours = hours if isinstance(hours, (int, float)) and 1 <= hours <= 720 else 24
        rows = [e for e in self.store.events_for_kpis(now - hours * 3600) if e["point"] in pts or (e["point"] == SYSTEM and admin)]
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
    def database(self, method, what, name, body, ident):
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
            try:
                r = self.maint.backup()
            except NotImplementedError as e:
                raise ApiError(501, str(e))
            self.store.audit(ident["name"], "database.backup", r["name"])
            return 200, r
        if (method, what) == ("GET", "backups") and name:
            p = self.maint.backup_file(name)
            if p is None:
                raise ApiError(404, "unknown backup")
            self.store.audit(ident["name"], "database.download", name)
            return 200, Raw(p.read_bytes(), "application/octet-stream", name)
        raise ApiError(404, "not found")

    def import_config(self, query, doc, ident):
        validators = {"source": lambda n, b, v: validate_source(n, b), "point": lambda n, b, v: validate_point(n, b, v),
                      "handler": lambda n, b, v: validate_handler(n, b), "publisher": lambda n, b, v: validate_publisher(n, b, v),
                      "watchlist": lambda n, b, v: validate_watchlist(n, b),
                      "dashboard": lambda n, b, v: validate_dashboard(n, b, v, roles_ok)[0]}
        apply_ = query.get("apply") == "1"  # a plain call is always a dry run
        errs, summary = self.maint.import_config(doc, validators, query.get("mode", "merge"), not apply_, ident["name"])
        if errs:
            raise ApiError(422, "validation failed", errs)
        if apply_:
            self.store.audit(ident["name"], "config.import", json.dumps({k: v for k, v in summary.items() if any(v.values())}))
            self.reload()
        return 200, {"applied": apply_, "summary": summary}

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
                "connected": getattr(s, "connected", None), "dropped": getattr(s, "dropped", None)}

    def series(self, ident, q):
        point = q.get("point")
        if point not in self.visible_points(ident):
            raise ApiError(404, "unknown point")
        rollup = q.get("rollup", "avg")
        if rollup != "last" and rollup not in ROLLUPS:
            raise ApiError(400, "rollup must be one of avg, min, max, last, count")
        try:
            window = int(q.get("window", 3600))
            bucket = int(q.get("bucket", max(1, window // 120)))
        except ValueError:
            raise ApiError(400, "window and bucket must be whole seconds")
        if not 10 <= window <= 7 * 86400 or not 1 <= bucket <= window:
            raise ApiError(400, "window must be 10 s to 7 days and bucket between 1 s and the window")
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
                    "watchlist": lambda: validate_watchlist(name, body)}[kind]()
            if errs:
                raise ApiError(422, "validation failed", errs)
            self.store.put(kind, name, body)
            self.store.audit(ident["name"], "config.put", f"{kind}/{name}")
            self.reload()
            return 200, {"saved": name}
        if method == "DELETE":
            if kind == "source" and any(p.get("source") == name for p in self.store.list("point")):
                raise ApiError(409, "delete or move this source's points first")
            if not self.store.delete(kind, name):
                self._404()
            self.store.audit(ident["name"], "config.delete", f"{kind}/{name}")
            self.reload()
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
