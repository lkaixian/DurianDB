"""Maintenance windows: planned periods when alarms for chosen sources, points or tags are held, dropped or auto-acknowledged.

A window is a config object: {enabled, mode, scope: {points, tags, sources}, schedule: {type: once|weekly|manual, ...}, reason}.
Weekly schedules use the server's local time and may cross midnight (start later than end).
"""
import datetime as dt
import re

MODES = ("suppress", "drop", "acknowledge")  # suppress: recorded, not announced. drop: not recorded. acknowledge: recorded, acknowledged at once, not announced.
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _minutes(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def active(m, now):
    """Is the window in effect at epoch time `now`?"""
    if not m.get("enabled", True):
        return False
    s = m.get("schedule") or {}
    kind = s.get("type")
    if kind == "manual":
        return bool(m.get("active"))
    if kind == "once":
        return s["from"] <= now < s["to"]
    if kind == "weekly":
        t = dt.datetime.fromtimestamp(now)
        minute, start, end = t.hour * 60 + t.minute, _minutes(s["start"]), _minutes(s["end"])
        if start < end:
            return t.weekday() in s["days"] and start <= minute < end
        # crosses midnight: the evening part belongs to a listed day, the morning part to the day after one
        return (t.weekday() in s["days"] and minute >= start) or ((t.weekday() - 1) % 7 in s["days"] and minute < end)
    return False


def covers(m, name, tags=None, source=None):
    """Does the window's scope include this point (by name, tags or source)? A source alarm is named with the source's name."""
    sc = m.get("scope") or {}
    if name in sc.get("points", []) or (source is not None and source in sc.get("sources", [])):
        return True
    want = sc.get("tags") or {}
    return bool(want) and all((tags or {}).get(k) == v for k, v in want.items())


def validate(name, b, name_re):
    errs = []
    add = lambda p, msg: errs.append({"property": p, "message": msg})
    if not name_re.match(str(name)):
        add("name", "use 1 to 64 letters, digits, dot, dash or underscore")
    if not isinstance(b.get("enabled", True), bool):
        add("enabled", "must be true or false")
    if b.get("mode") not in MODES:
        add("mode", f"must be one of {list(MODES)}")
    if not (isinstance(b.get("reason", ""), str) and len(b.get("reason", "")) <= 200):
        add("reason", "must be text, up to 200 characters")
    sc = b.get("scope")
    if not isinstance(sc, dict):
        add("scope", "give at least one of points, tags or sources")
    else:
        for k in ("points", "sources"):
            if k in sc and not (isinstance(sc[k], list) and len(sc[k]) <= 200 and all(isinstance(x, str) and 0 < len(x) <= 64 for x in sc[k])):
                add(f"scope.{k}", "must be a list of names")
        t = sc.get("tags", {})
        if not (isinstance(t, dict) and len(t) <= 20 and all(isinstance(k, str) and isinstance(v, str) for k, v in t.items())):
            add("scope.tags", "must be text key/value pairs")
        if not (sc.get("points") or sc.get("sources") or sc.get("tags")):
            add("scope", "give at least one of points, tags or sources")
    s = b.get("schedule")
    if not isinstance(s, dict) or s.get("type") not in ("once", "weekly", "manual"):
        add("schedule.type", "must be once, weekly or manual")
    elif s["type"] == "once":
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
        if not (num(s.get("from")) and num(s.get("to")) and s["from"] < s["to"]):
            add("schedule.from", "give a start and an end time (epoch seconds) with the start first")
    elif s["type"] == "weekly":
        if not (isinstance(s.get("days"), list) and s["days"] and all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in s["days"])):
            add("schedule.days", "choose days from Mon (0) to Sun (6)")
        if not (isinstance(s.get("start"), str) and HHMM.match(s["start"]) and isinstance(s.get("end"), str) and HHMM.match(s["end"]) and s["start"] != s["end"]):
            add("schedule.start", "give a start and an end as HH:MM, not the same")
    if not isinstance(b.get("active", False), bool):
        add("active", "must be true or false")
    return errs
