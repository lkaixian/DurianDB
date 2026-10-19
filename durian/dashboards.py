"""Dashboards: declarative pages made of widgets on a grid. No author-written HTML or script.

A widget type is one validator here plus one renderer in web/dashboards.js. That pair is the whole extension
mechanism, and it is installed by whoever runs the server, never uploaded through the UI.
Studied behavior and the reasons for the differences are in docs/research/09.
"""
import math
import re

from .store import ALL_ROLLUPS

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
TONES = ("normal", "warn", "alarm")  # fixed styles: the page keeps one accent colour and a strict content policy
OPS = (">", "<", ">=", "<=", "==")
WINDOWS = (300, 3600, 86400, 604800)
MAX_WIDGETS, MAX_ROW = 60, 200
VALIDATORS = {}


def widget(kind):
    def register(fn):
        VALIDATORS[kind] = fn
        return fn
    return register


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _text(v, n):
    return isinstance(v, str) and len(v) <= n


class Ctx:
    """Collects errors and warnings under a property path such as widgets[2].point."""

    def __init__(self):
        self.errors, self.warnings, self.points = [], [], set()

    def err(self, prop, msg):
        self.errors.append({"property": prop, "message": msg})

    def warn(self, prop, msg):
        self.warnings.append({"property": prop, "message": msg})

    def point(self, w, p, key="point"):
        if not _text(w.get(key), 64) or not w.get(key):
            self.err(f"{p}.{key}", "must be a point name")
        else:
            self.points.add(w[key])

    def tone_rules(self, w, p):
        rules = w.get("rules", [])
        if not isinstance(rules, list) or len(rules) > 10:
            return self.err(f"{p}.rules", "must be a list of at most 10")
        for i, r in enumerate(rules):
            if not (isinstance(r, dict) and r.get("op") in OPS and _num(r.get("value")) and r.get("tone") in TONES):
                self.err(f"{p}.rules[{i}]", f"needs op ({', '.join(OPS)}), a numeric value, and tone ({', '.join(TONES)})")


@widget("value")
def _value(w, p, c):
    c.point(w, p)
    c.tone_rules(w, p)
    if "decimals" in w and not (isinstance(w["decimals"], int) and 0 <= w["decimals"] <= 6):
        c.err(f"{p}.decimals", "must be 0 to 6")
    if "stale_after" in w and not (_num(w["stale_after"]) and 1 <= w["stale_after"] <= 86400):
        c.err(f"{p}.stale_after", "must be 1 to 86400 seconds")


@widget("gauge")
def _gauge(w, p, c):
    c.point(w, p)
    lo, hi = w.get("min"), w.get("max")
    if not (_num(lo) and _num(hi) and lo < hi):
        c.err(f"{p}.min", "min and max must be numbers with min below max")
    zones = w.get("zones", [])
    if not isinstance(zones, list) or len(zones) > 8:
        return c.err(f"{p}.zones", "must be a list of at most 8")
    for i, z in enumerate(zones):
        if not (isinstance(z, dict) and _num(z.get("from")) and _num(z.get("to")) and z["from"] < z["to"] and z.get("tone") in TONES):
            c.err(f"{p}.zones[{i}]", f"needs numeric from below to, and tone ({', '.join(TONES)})")


@widget("chart")
def _chart(w, p, c):
    pts = w.get("points")
    if not (isinstance(pts, list) and 1 <= len(pts) <= 6 and all(_text(x, 64) and x for x in pts)):
        c.err(f"{p}.points", "must be 1 to 6 point names")
    else:
        c.points.update(pts)
    if w.get("window", 3600) not in WINDOWS:
        c.err(f"{p}.window", f"must be one of {list(WINDOWS)} seconds")
    if w.get("rollup", "avg") not in ALL_ROLLUPS:
        c.err(f"{p}.rollup", f"must be one of {', '.join(ALL_ROLLUPS)}")


@widget("alarms")
def _alarms(w, p, c):
    pts = w.get("points")
    if pts is not None:
        if not (isinstance(pts, list) and len(pts) <= 30 and all(_text(x, 64) for x in pts)):
            c.err(f"{p}.points", "must be a list of point names")
        else:
            c.points.update(pts)
    if "tag" in w and not (_text(w["tag"], 130) and ":" in w["tag"]):
        c.err(f"{p}.tag", "must look like area:boiler")
    if not (isinstance(w.get("limit", 10), int) and 1 <= w.get("limit", 10) <= 50):
        c.err(f"{p}.limit", "must be 1 to 50")
    if w.get("state", "open") not in ("open", "all"):
        c.err(f"{p}.state", "must be open or all")


@widget("table")
def _table(w, p, c):
    given = [k for k in ("tag", "watchlist", "points") if k in w]
    if len(given) != 1:
        return c.err(f"{p}.tag", "give exactly one of tag, watchlist or points")
    k = given[0]
    if k == "points":
        if not (isinstance(w[k], list) and 1 <= len(w[k]) <= 40 and all(_text(x, 64) and x for x in w[k])):
            c.err(f"{p}.points", "must be 1 to 40 point names")
        else:
            c.points.update(w[k])
    elif not (_text(w[k], 130) and w[k] and (k != "tag" or ":" in w[k])):
        c.err(f"{p}.{k}", "must look like area:boiler" if k == "tag" else "must be a watch list name")


MIMIC_SHAPES = {"rect": ("x", "y", "w", "h"), "line": ("x1", "y1", "x2", "y2"), "circle": ("cx", "cy", "r"), "text": ("x", "y"), "value": ("x", "y")}


@widget("mimic")
def _mimic(w, p, c):
    """A process picture drawn from a fixed set of shapes. Shapes can follow a point: tone rules restyle them, a value shape prints the number."""
    if not (_num(w.get("width")) and _num(w.get("height")) and 50 <= w["width"] <= 2000 and 50 <= w["height"] <= 2000):
        c.err(f"{p}.width", "width and height must be numbers from 50 to 2000")
    shapes = w.get("shapes")
    if not (isinstance(shapes, list) and 1 <= len(shapes) <= 80):
        return c.err(f"{p}.shapes", "must be a list of 1 to 80 shapes")
    for i, s in enumerate(shapes):
        sp = f"{p}.shapes[{i}]"
        if not isinstance(s, dict) or s.get("type") not in MIMIC_SHAPES:
            c.err(f"{sp}.type", f"must be one of {list(MIMIC_SHAPES)}")
            continue
        for k in MIMIC_SHAPES[s["type"]]:
            if not (_num(s.get(k)) and -10000 <= s[k] <= 10000 and (k not in ("w", "h", "r") or s[k] >= 0)):
                c.err(f"{sp}.{k}", "must be a number (not negative for a size)")
        if s["type"] == "text" and not (_text(s.get("text"), 100) and s["text"]):
            c.err(f"{sp}.text", "must be 1 to 100 characters of plain text")
        if "label" in s and not _text(s["label"], 100):
            c.err(f"{sp}.label", "must be up to 100 characters of plain text")
        if s["type"] == "value" or "point" in s:
            c.point(s, sp)
        if "rules" in s:
            c.tone_rules(s, sp)
        if "decimals" in s and not (isinstance(s["decimals"], int) and 0 <= s["decimals"] <= 6):
            c.err(f"{sp}.decimals", "must be 0 to 6")


@widget("setpoint")
def _setpoint(w, p, c):
    c.point(w, p)


@widget("text")
def _text_widget(w, p, c):
    if not (_text(w.get("text"), 2000) and w["text"]):
        c.err(f"{p}.text", "must be 1 to 2000 characters of plain text")


@widget("status")
def _status(w, p, c):
    if "source" in w and not (_text(w["source"], 64) and w["source"]):
        c.err(f"{p}.source", "must be a source name, or leave it out for all sources")


def validate_dashboard(name, b, store, roles_ok):
    """Return (errors, warnings). `roles_ok(list)` checks a role list; `store` supplies points for warnings."""
    c = Ctx()
    if not NAME.match(str(name)):
        c.err("name", "use 1 to 64 letters, digits, dot, dash or underscore")
    if not (_text(b.get("title", ""), 80) and b.get("title")):
        c.err("title", "give a title of up to 80 characters")
    cols = b.get("columns", 12)
    if not (isinstance(cols, int) and not isinstance(cols, bool) and 4 <= cols <= 24):
        c.err("columns", "must be 4 to 24")
        cols = 12
    if not (_num(b.get("refresh", 2)) and 1 <= b.get("refresh", 2) <= 3600):
        c.err("refresh", "must be 1 to 3600 seconds")
    for k in ("read_roles", "edit_roles"):
        if not roles_ok(b.get(k, [])):
            c.err(k, "must be a list of role names")
    ws = b.get("widgets", [])
    if not isinstance(ws, list) or len(ws) > MAX_WIDGETS:
        c.err("widgets", f"must be a list of at most {MAX_WIDGETS}")
        ws = []
    ids, grid = set(), {}
    for i, w in enumerate(ws):
        p = f"widgets[{i}]"
        if not isinstance(w, dict):
            c.err(p, "must be an object")
            continue
        if not (isinstance(w.get("id"), str) and re.match(r"^[A-Za-z0-9_-]{1,32}$", w["id"])) or w["id"] in ids:
            c.err(f"{p}.id", "must be a unique short id of letters, digits, dash or underscore")
        ids.add(w.get("id"))
        if not _text(w.get("title", ""), 60):
            c.err(f"{p}.title", "must be up to 60 characters")
        if w.get("type") not in VALIDATORS:
            c.err(f"{p}.type", f"must be one of {sorted(VALIDATORS)}")
        else:
            VALIDATORS[w["type"]](w, p, c)
        x, y, wd, h = (w.get(k) for k in ("x", "y", "w", "h"))
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (x, y, wd, h)) or x < 0 or y < 0 or wd < 1 or h < 1:
            c.err(f"{p}.x", "x, y, w and h must be whole numbers, w and h at least 1")
        elif x + wd > cols or y + h > MAX_ROW or h > 20:
            c.err(f"{p}.w", f"must fit the {cols}-column grid (x + w at most {cols}, h at most 20)")
        else:
            cells = [(cx, cy) for cx in range(x, x + wd) for cy in range(y, y + h)]
            clash = next((grid[cell] for cell in cells if cell in grid), None)
            if clash is not None:
                c.err(f"{p}.x", f"overlaps widget {ws[clash].get('id')}")
            for cell in cells:
                grid.setdefault(cell, i)
    # warnings: things that are legal but probably not what the author meant
    known = {p["name"]: p for p in store.list("point")}
    public = "anonymous" in b.get("read_roles", [])
    for pt in sorted(c.points):
        if pt not in known:
            c.warn("widgets", f"point '{pt}' is not configured (it will show no data until it exists)")
        elif public and "anonymous" not in known[pt].get("read_roles", ["user"]):
            c.warn("read_roles", f"dashboard is public but point '{pt}' is not readable by anonymous, so its widgets will show no access")
    return c.errors, c.warnings
