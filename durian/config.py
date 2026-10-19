"""Source types, config validation, and building a running Engine from stored config.

Validation returns every problem at once as [{property, message}], the way the studied system does.
"""
import json
import math
import random
import re
from pathlib import Path

from .core import Engine, Logging, Point, Source, make_detector
from .mqtt_source import MqttSource

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SOURCE_TYPES = ("virtual", "mqtt")


class Virtual(Source):
    """Generates values: random walk, sine wave, or constant. Settable points hold the value written."""

    def __init__(self, name):
        super().__init__(name)
        self.gens, self.state, self.override = {}, {}, {}

    def poll(self, ts):
        out = {}
        for n, g in self.gens.items():
            if n in self.override:
                out[n] = self.override[n]
                continue
            kind = g.get("kind", "walk")
            if kind == "constant":
                v = g["value"]
            elif kind == "sine":
                v = g.get("offset", 0) + g["amplitude"] * math.sin(2 * math.pi * ts / g["period"])
            else:
                step = g.get("step", 1.0)
                v = self.state.get(n, g.get("start", 0)) + random.uniform(-step, step) + g.get("drift", 0)
                v = min(g.get("max", math.inf), max(g.get("min", -math.inf), v))
                self.state[n] = v
            out[n] = round(v, 3)
        return out

    def write(self, name, value):
        if value is None:
            self.override.pop(name, None)
        else:
            self.override[name] = value


def _roles_ok(v):
    return isinstance(v, list) and all(isinstance(r, str) and 0 < len(r) <= 32 for r in v) and len(v) <= 20


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def load_creds(path):
    c = json.loads(Path(path).read_text())
    for k in ("host", "port", "ca", "user", "password"):
        if k not in c:
            raise ValueError(f"credentials file lacks '{k}'")
    return c


def validate_source(name, b):
    e = []
    add = lambda p, m: e.append({"property": p, "message": m})
    if not NAME.match(str(name)):
        add("name", "use 1 to 64 letters, digits, dot, dash or underscore")
    if b.get("type") not in SOURCE_TYPES:
        add("type", f"must be one of {list(SOURCE_TYPES)}")
    if not isinstance(b.get("enabled", True), bool):
        add("enabled", "must be true or false")
    if not _roles_ok(b.get("read_roles", ["user"])):
        add("read_roles", "must be a list of role names")
    if b.get("type") == "mqtt":
        try:
            load_creds(b.get("creds", ""))
        except (OSError, ValueError, TypeError) as ex:
            add("creds", f"cannot use credentials file: {type(ex).__name__}")
        pre, cmd = b.get("prefix", "durian/"), b.get("cmd_prefix", "durian-cmd/")
        for prop, v in (("prefix", pre), ("cmd_prefix", cmd)):
            if not isinstance(v, str) or not v.endswith("/") or "#" in v or "+" in v:
                add(prop, "must be a topic prefix ending in '/', without wildcards")
        if isinstance(pre, str) and isinstance(cmd, str) and (cmd.startswith(pre) or pre.startswith(cmd)):
            add("cmd_prefix", "must not overlap prefix, or commands would come back in as data")
        if not isinstance(b.get("auto_create", True), bool):
            add("auto_create", "must be true or false")
    return e


def validate_point(name, b, store):
    e = []
    add = lambda p, m: e.append({"property": p, "message": m})
    if not NAME.match(str(name)):
        add("name", "use 1 to 64 letters, digits, dot, dash or underscore")
    src = store.get("source", b.get("source")) if isinstance(b.get("source"), str) else None
    if src is None:
        add("source", "unknown source")
    if b.get("logging", "ON_CHANGE") not in Logging.__members__:
        add("logging", f"must be one of {list(Logging.__members__)}")
    if not (_num(b.get("tolerance", 0)) and b.get("tolerance", 0) >= 0):
        add("tolerance", "must be a number, zero or more")
    tags = b.get("tags", {})
    if not (isinstance(tags, dict) and len(tags) <= 20 and all(isinstance(k, str) and isinstance(v, str)
                                                                and 0 < len(k) <= 64 and len(v) <= 64 for k, v in tags.items())):
        add("tags", "must be up to 20 text key/value pairs")
    if not (isinstance(b.get("unit", ""), str) and len(b.get("unit", "")) <= 16):
        add("unit", "must be text, up to 16 characters")
    if not isinstance(b.get("settable", False), bool):
        add("settable", "must be true or false")
    for p in ("read_roles", "set_roles"):
        if not _roles_ok(b.get(p, [])):
            add(p, "must be a list of role names")
    dets = b.get("detectors", [])
    if not isinstance(dets, list) or len(dets) > 10:
        add("detectors", "must be a list of at most 10")
    else:
        for i, d in enumerate(dets):
            try:
                make_detector(d)
            except (ValueError, TypeError, AttributeError) as ex:
                add(f"detectors[{i}]", str(ex))
    if src and src.get("type") == "virtual":
        g = b.get("generator", {"kind": "walk"})
        kind = g.get("kind") if isinstance(g, dict) else None
        need = {"walk": [], "sine": ["amplitude", "period"], "constant": ["value"]}
        if kind not in need:
            add("generator.kind", "must be walk, sine or constant")
        else:
            for k in need[kind] + [k for k in ("start", "min", "max", "step", "drift", "offset") if k in g]:
                if not _num(g.get(k)):
                    add(f"generator.{k}", "must be a number")
            if kind == "sine" and _num(g.get("period")) and g["period"] <= 0:
                add("generator.period", "must be greater than zero")
    return e


def validate_watchlist(name, b):
    e, add = [], lambda p, m: e.append({"property": p, "message": m})
    if not NAME.match(str(name)):
        add("name", "use 1 to 64 letters, digits, dot, dash or underscore")
    tags, pts = b.get("tags", {}), b.get("points", [])
    if not (isinstance(tags, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in tags.items())):
        add("tags", "must be text key/value pairs")
    if not (isinstance(pts, list) and all(isinstance(p, str) for p in pts)):
        add("points", "must be a list of point names")
    if not tags and not pts:
        add("tags", "give tags to match or a list of points")
    if not _roles_ok(b.get("read_roles", ["user"])):
        add("read_roles", "must be a list of role names")
    return e


def make_point(name, b, source_name):
    p = Point(name, logging=Logging[b.get("logging", "ON_CHANGE")], tolerance=float(b.get("tolerance", 0)),
              tags=dict(b.get("tags", {})), detectors=[make_detector(d) for d in b.get("detectors", [])])
    p.meta = {"source": source_name, "unit": b.get("unit", ""), "settable": bool(b.get("settable", False)),
              "read_roles": b.get("read_roles", ["user"]), "set_roles": b.get("set_roles", []),
              "detector_cfg": b.get("detectors", [])}
    return p


def build_engine(store):
    """Engine from stored config. A source that fails to build is kept as an unhealthy stub, not fatal."""
    eng = Engine()
    points = store.list("point")
    for s in store.list("source"):
        if not s.get("enabled", True):
            continue
        mine = {p["name"]: p for p in points if p.get("source") == s["name"]}
        try:
            if s["type"] == "virtual":
                src = Virtual(s["name"])
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
                    src.gens[n] = p.get("generator", {"kind": "walk"})
            else:
                auto = (lambda n: make_point(n, {}, s["name"])) if s.get("auto_create", True) else None
                src = MqttSource(s["name"], load_creds(s["creds"]), s.get("prefix", "durian/"),
                                 s.get("cmd_prefix", "durian-cmd/"), auto)
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
        except Exception as ex:
            src = Source(s["name"])
            src.error = f"cannot start: {type(ex).__name__}"
        src.meta = {"type": s.get("type"), "read_roles": s.get("read_roles", ["user"])}
        eng.sources.append(src)
    return eng


def seed(store, lab_creds=None):
    """First-run demo config. Safe to call every start: it only writes when no source exists."""
    if store.list("source"):
        return False
    store.put("source", "demo", {"type": "virtual", "enabled": True, "read_roles": ["user"]})
    store.put("point", "boiler-temp", {"source": "demo", "unit": "C", "tags": {"area": "boiler"}, "tolerance": 0.2,
              "generator": {"kind": "walk", "start": 82, "min": 60, "max": 110, "step": 1.6, "drift": 0.15},
              "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT", "duration": 4}]})
    store.put("point", "line-pressure", {"source": "demo", "unit": "bar", "tags": {"area": "line"},
              "generator": {"kind": "sine", "offset": 6, "amplitude": 2, "period": 180},
              "detectors": [{"kind": "HIGH_LIMIT", "limit": 7.5, "level": "WARNING"}]})
    store.put("point", "pump-setpoint", {"source": "demo", "unit": "%", "tags": {"area": "line"}, "settable": True,
              "set_roles": ["user"], "generator": {"kind": "constant", "value": 40}, "logging": "ON_CHANGE"})
    if lab_creds and Path(lab_creds).exists():
        store.put("source", "lab-mqtt", {"type": "mqtt", "enabled": True, "read_roles": ["user"], "creds": str(lab_creds),
                  "prefix": "durian/", "cmd_prefix": "durian-cmd/", "auto_create": True})
        store.put("point", "mqtt-temp", {"source": "lab-mqtt", "unit": "C", "tags": {"area": "boiler", "via": "mqtt"},
                  "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"},
                                {"kind": "NO_UPDATE", "seconds": 30, "level": "WARNING"}]})
    return True
