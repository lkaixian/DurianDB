"""Application logic: runtime engine, persistence loop, and the API as plain (status, payload) calls.

The HTTP layer in server.py only translates requests into App.handle() calls.
"""
import json
import math
import re
import time

from .auth import Auth, AuthError, can, check_password_strength, hash_password, verify_password
from .config import (build_engine, validate_point, validate_source, validate_watchlist)
from .core import Level
from .outputs import Outputs, validate_handler, validate_publisher
from .store import ROLLUPS

KINDS = ("source", "point", "handler", "publisher", "watchlist")
ROUTE = re.compile(r"^/api/(?P<a>[a-z-]+)(?:/(?P<b>[^/]+))?(?:/(?P<c>[^/]+))?$")


class ApiError(Exception):
    def __init__(self, status, message, errors=None):
        super().__init__(message)
        self.status, self.errors = status, errors


class App:
    def __init__(self, store):
        self.store, self.auth = store, Auth(store)
        self.engine, self.outputs, self.open_events = None, None, []
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
                self.store.clear_event(ev.id, now)
            self.open_events = []
            self.engine = build_engine(self.store)
            self.outputs = Outputs(self.store)
            self.engine.publishers.append(self.outputs.on_value)

    def tick(self, ts=None):
        with self.store.lock:
            ts = ts or time.time()
            self.engine.tick(ts)
            rows = [(p.name, t, v) for s in self.engine.sources for p in s.points.values() for t, v in p.take_unsaved()]
            if rows:
                self.store.add_history(rows)
            new, self.engine.events = self.engine.events, []
            for ev in new:
                ev.id = self.store.add_event(ev)
                self.open_events.append(ev)
                self.outputs.on_event(ev, "raised")
            still = []
            for ev in self.open_events:
                if ev.cleared_ts is None:
                    still.append(ev)
                else:
                    self.store.clear_event(ev.id, ev.cleared_ts)
                    self.outputs.on_event(ev, "cleared")
            self.open_events = still

    def points(self):
        return {p.name: (p, s) for s in self.engine.sources for p in s.points.values()}

    # ---- views
    def point_view(self, p, s):
        cfg = p.meta.get("detector_cfg", [])
        limit = lambda kind: next((d["limit"] for d in cfg if d.get("kind") == kind), None)
        return {"name": p.name, "source": s.name, "unit": p.meta.get("unit", ""), "tags": p.tags, "last": p.last,
                "last_ts": p.last_ts, "settable": p.meta.get("settable", False),
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
            self.store.audit(ident["name"], "set", f"{p.name}={v}")
            return 200, {"ok": True}
        if (method, a) == ("GET", "series"):
            return 200, self.series(ident, query)
        if (method, a, c) == ("GET", "events", None) and b is None:
            ok = self.visible_points(ident)
            rows = [e for e in self.store.events(300) if e["point"] in ok][:100]
            for e in rows:
                e["level_name"] = Level(e["level"]).name
            return 200, rows
        if (method, a, c) == ("POST", "events", "ack"):
            need(user)
            ev = self.store.q("select point from events where id=?", (int(b) if b.isdigit() else -1,))
            if not ev or ev[0]["point"] not in self.visible_points(ident):
                raise ApiError(404, "unknown event")
            n = self.store.ack_event(int(b), ident["name"], time.time())
            self.store.audit(ident["name"], "ack", f"event {b}")
            return (200 if n else 409), {"acked": n}
        if (method, a) == ("GET", "sources") and b is None:
            return 200, [self.source_view(s) for s in self.engine.sources
                         if can(ident["roles"], s.meta.get("read_roles", ["user"]))]
        if (method, a) == ("GET", "watchlists") and b is None:
            return 200, [w for w in self.store.list("watchlist") if can(ident["roles"], w.get("read_roles", ["user"]))]
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
        raise ApiError(404, "not found")

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
