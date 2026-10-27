"""One downloadable bundle per export: history CSVs, the alarm journal, the configuration and a manifest, in a ZIP.

The same bundle can be written on a schedule by Maintenance, so it is built from the store alone. Nothing here
truncates: a request that would make a bundle too large to hold in memory is refused with a sentence, because a
silently shortened export is a lie.
"""
import datetime as dt
import io
import json
import re
import time
import zipfile

from .core import Level, Quality, alarm_state
from .csvfmt import csv_bytes, iso_millis
from .maintenance import KINDS

DURIAN_VERSION = "1.0"  # the product version a recipient of the bundle can quote; bump when the layout changes
MANIFEST = "manifest.json"
MAX_DAYS = 90
MAX_POINTS = 500
MAX_ROWS = 200_000  # good samples across every history file in one bundle
MAX_EVENTS = 50_000


class ExportError(ValueError):
    """The bundle cannot be made as asked. The message is a sentence the operator can act on."""


def _safe(name):
    """A point name made safe for a zip member and CSV file name: no separator, no '..', no leading dot."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name))
    s = re.sub(r"\.{2,}", "_", s)
    return s.strip("._-")[:80] or "point"


def _plan(store, days, points, now):
    """Check the request and decide what the bundle will hold. Everything that can be refused is refused here."""
    now = now or time.time()
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ExportError("days must be a whole number of days, 1 or more")
    if days > MAX_DAYS:
        raise ExportError(f"a bundle may cover at most {MAX_DAYS} days")
    keep = store.get_setting("retention_days", 365)
    if isinstance(keep, int) and keep > 0 and days > keep:
        raise ExportError(f"a bundle may cover at most {keep} days, which is the retention setting")

    configured = [p["name"] for p in store.list("point")]
    if points is None:
        selected = configured
    else:
        wanted = [p for p in points if isinstance(p, str) and p]
        missing = [p for p in wanted if p not in set(configured)]
        if missing:
            raise ExportError("unknown point" + ("s" if len(missing) > 1 else "") + ": " + ", ".join(missing[:5]))
        selected = sorted(dict.fromkeys(wanted))
    if len(selected) > MAX_POINTS:
        raise ExportError(f"a bundle may hold at most {MAX_POINTS} points, {len(selected)} were selected")

    files, owners = {}, {}
    for name in selected:
        member = "history/" + _safe(name) + ".csv"
        if member in owners:
            raise ExportError(f"points '{owners[member]}' and '{name}' would share the file name {member}; rename one")
        owners[member] = name
        files[name] = member
    return {"now": now, "days": days, "start": now - days * 86400, "end": now, "names": selected, "files": files}


def _summary(schema_version, plan, rows, objects):
    return {"format": "durian-export-bundle", "version": 1, "durian_version": DURIAN_VERSION,
            "schema_version": schema_version, "created": plan["now"],
            "window": {"from": plan["start"], "to": plan["end"], "days": plan["days"]},
            "points": {"count": len(plan["names"]), "names": plan["names"], "files": plan["files"]},
            "contents": {"history": list(plan["files"].values()), "events": "events.csv", "config": "config.json", "manifest": MANIFEST},
            "rows": rows, "objects": objects}


def preview(store, *, days=7, points=None, now=None):
    """What a bundle would contain, checked but not built: the window, the points and the config object counts."""
    plan = _plan(store, days, points, now)
    return _summary(store.version(), plan, None, {k: len(store.list(k)) for k in KINDS})


def build(store, *, days=7, points=None, now=None):
    """(zip bytes, download file name, manifest). Raises ExportError when the request cannot be honoured."""
    plan = _plan(store, days, points, now)
    counts, total = {}, 0
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in plan["names"]:
            remaining = MAX_ROWS - total
            rows = store.history_range(name, plan["start"], plan["end"], remaining + 1)
            if len(rows) > remaining:
                raise ExportError(f"the bundle would hold more than {MAX_ROWS} samples; narrow the window or choose fewer points")
            z.writestr(plan["files"][name], csv_bytes(
                ["timestamp_utc", "value", "quality"],
                ([iso_millis(r["ts"]), r["svalue"] if r["svalue"] is not None else repr(float(r["value"])), Quality(r["quality"]).name.lower()]
                 for r in rows)))
            counts[name], total = len(rows), total + len(rows)

        events, event_total = store.events_search(None, 0, "all", plan["start"], plan["end"], None, MAX_EVENTS + 1, 0)
        if event_total > MAX_EVENTS:
            raise ExportError(f"the bundle would hold more than {MAX_EVENTS} alarms; narrow the window")
        z.writestr("events.csv", csv_bytes(
            ["time_utc", "point", "level", "message", "state", "cleared"],
            ([iso_millis(e["ts"]), e["point"], Level(e["level"]).name, e["message"],
              alarm_state(e["acked_ts"], e["cleared_ts"]), "" if e["cleared_ts"] is None else iso_millis(e["cleared_ts"])]
             for e in sorted(events, key=lambda r: (r["ts"], r["id"])))))

        objects = {k: store.list(k) for k in KINDS}
        config = {"format": "durian-config", "version": 1, "exported_at": plan["now"], "objects": objects}
        z.writestr("config.json", json.dumps(config, indent=2).encode())

        manifest = _summary(store.version(), plan,
                            {"history": total, "history_by_point": counts, "events": len(events)},
                            {k: len(v) for k, v in objects.items()})
        z.writestr(MANIFEST, json.dumps(manifest, indent=2).encode())
    stamp = dt.datetime.fromtimestamp(plan["now"], dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return buf.getvalue(), f"durian-export-{stamp}.zip", manifest
