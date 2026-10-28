"""HTTP ingest source: devices and flow tools (Node-RED, scripts) push values to DurianDB over HTTP.

Accepted bodies for POST /api/ingest/<source>, and the same names as query parameters for GET:
    {"point": "temp", "value": 21.5, "ts": 1700000000.5}          one value, ts optional (epoch seconds or ISO-8601)
    add "quality": "good" | "uncertain" | "bad" (or 0, 1, 2) when the sender knows a reading is doubtful; default good
    {"values": [{"point": "temp", "value": 21.5, "ts": ...}, ...]} a batch
    {"temp": 21.5, "pressure": 3.1}                                  a flat map of point name to value, time is now
The device's own timestamp is kept when it is plausible. An explicit timestamp older than the newest one already
received for a point is a backfill: it lands in history at its own time and never becomes the point's current
value. The reply says exactly what was accepted and what was not and why.
"""
import datetime as dt
import math
import re
import threading
import time

from .core import FUTURE_SLACK, Quality, Source

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_BATCH = 1000


def parse_time(v):
    if isinstance(v, bool):
        raise ValueError("ts must be epoch seconds or an ISO-8601 text")
    if isinstance(v, (int, float)):
        t = float(v)
        if t > 1e11:  # milliseconds, as many devices and Node-RED send
            t /= 1000.0
        return t
    if isinstance(v, str):
        s = v.strip()
        try:
            return parse_time(float(s))
        except ValueError:
            try:
                d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError as e:
                raise ValueError("ts must be epoch seconds or an ISO-8601 text") from e
            return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()
    raise ValueError("ts must be epoch seconds or an ISO-8601 text")


def parse_quality(v):
    """A Quality from its name or number; None (not sent) is good. ValueError for anything else."""
    if v is None:
        return Quality.GOOD
    try:
        return Quality[v.strip().upper()] if isinstance(v, str) and not v.strip().isdigit() else Quality(int(v))
    except (KeyError, ValueError, TypeError):
        raise ValueError("quality must be good, uncertain or bad")


def parse_payload(body, query):
    """Return a list of (point, value, ts-or-None, original index) candidates, or raise ValueError."""
    if isinstance(body, dict) and "values" in body:
        items = body["values"]
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise ValueError("values must be a list of objects with point and value")
    elif isinstance(body, dict) and "point" in body:
        items = [body]
    elif isinstance(body, dict) and body:
        items = [{"point": k, "value": v} for k, v in body.items()]
    elif not body and "point" in query:
        items = [{k: query[k] for k in ("point", "value", "ts", "quality") if k in query}]
    else:
        raise ValueError("send {point, value}, {values: [...]} or a map of point name to value")
    if not 1 <= len(items) <= MAX_BATCH:
        raise ValueError(f"send between 1 and {MAX_BATCH} values per request")
    return [(i.get("point"), i.get("value"), i.get("ts"), n, i.get("quality")) for n, i in enumerate(items)]


def _split(s, sep):
    """Split on sep where it is neither backslash-escaped nor inside double quotes."""
    out, cur, q, esc = [], "", False, False
    for ch in s:
        if esc:
            cur, esc = cur + ch, False
        elif ch == "\\":
            cur, esc = cur + ch, True
        elif ch == '"':
            cur, q = cur + ch, not q
        elif ch == sep and not q:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur]


def _unesc(s):
    return re.sub(r"\\(.)", r"\1", s)


def _line_value(v):
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return _unesc(v[1:-1])
    if v.lower() in ("t", "true", "f", "false"):
        return 1.0 if v.lower()[0] == "t" else 0.0
    return float(v[:-1] if re.fullmatch(r"[+-]?\d+[iu]", v) else v)


def parse_line(text):
    """InfluxDB line protocol -> (candidates for HttpInSource.submit, rejected). Index is the 1-based line number.
    Tags are ignored. ValueError when there are no lines or more than MAX_BATCH."""
    cands, rejected = [], []
    for n, raw in enumerate(text.splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = [p for p in _split(raw, " ") if p]
        try:
            if not 2 <= len(parts) <= 3:
                raise ValueError("expected: measurement[,tags] field=value[,...] [timestamp_ns]")
            meas = _unesc(_split(parts[0], ",")[0])
            fields = [_split(f, "=") for f in _split(parts[1], ",")]
            if not meas or any(len(f) != 2 or not f[0] for f in fields):
                raise ValueError("expected: measurement[,tags] field=value[,...] [timestamp_ns]")
            ts = int(parts[2]) / 1e9 if len(parts) == 3 else None
            vals = [(_unesc(k), _line_value(v)) for k, v in fields]
        except ValueError as e:
            rejected.append({"index": n, "point": None, "reason": f"line {n}: {e}" if "expected" in str(e) else f"line {n}: bad field value or timestamp"})
            continue
        cands += [(meas if (k, len(vals)) == ("value", 1) else f"{meas}.{k}", v, ts, n, None) for k, v in vals]
    if not 1 <= len(cands) + len(rejected) <= MAX_BATCH:
        raise ValueError(f"send between 1 and {MAX_BATCH} values per request")
    return cands, rejected


class HttpInSource(Source):
    MAX_PENDING = 10_000  # ponytail: one cap for the whole source; a per-point share if one sender floods the rest

    def __init__(self, name, auto_create=False, factory=None, max_age_days=7):
        super().__init__(name)
        self.auto_create, self.factory, self.max_age = auto_create, factory, max_age_days * 86400
        self.dropped = 0  # refused values, shown on the source health table
        self._q, self._high, self._pending, self._lock = [], {}, set(), threading.Lock()

    def submit(self, candidates, now=None):
        """Validate candidates from any thread. Returns (accepted count, [{point, reason}]). Points are created later, on the engine thread."""
        now, rejected, accepted = now or time.time(), [], 0
        with self._lock:
            for point, value, ts, idx, *extra in candidates:
                def no(reason):
                    rejected.append({"index": idx, "point": point if isinstance(point, str) else None, "reason": reason})
                    self.dropped += 1
                if not (isinstance(point, str) and NAME.match(point)):
                    no("point must be a name of 1 to 64 letters, digits, dot, dash or underscore")
                    continue
                if point not in self.points:
                    if not self.auto_create:
                        no("unknown point (add it under Config, or turn on auto_create for this source)")
                        continue
                    if len(self.points) + len(self._pending) >= self.AUTO_POINTS_MAX:  # an endless supply of new names must not grow the source
                        no(f"this source has reached its limit of {self.AUTO_POINTS_MAX} points")
                        continue
                    self._pending.add(point)
                if getattr(self.points.get(point), "text", False):  # an alphanumeric point takes a short printable string
                    if not (isinstance(value, str) and 1 <= len(value) <= 255 and value.isprintable()):
                        no("value must be printable text of 1 to 255 characters")
                        continue
                else:
                    if isinstance(value, str):
                        try:
                            value = float(value)  # query strings and some tools send numbers as text
                        except ValueError:
                            no("value must be a number")
                            continue
                    if isinstance(value, bool):
                        value = 1.0 if value else 0.0
                    if not isinstance(value, (int, float)) or not math.isfinite(value):
                        no("value must be a finite number")
                        continue
                try:
                    q = parse_quality(*extra)
                except ValueError as e:
                    no(str(e))
                    continue
                try:  # no timestamp means "now", but never earlier than the newest value, so a sender whose clock runs ahead is not refused
                    t = max(now, self._high.get(point, 0) + 0.001) if ts is None else parse_time(ts)
                except ValueError as e:
                    no(str(e))
                    continue
                if not math.isfinite(t):  # NaN compares false against every guard below, so it would be accepted and poison the high-water mark forever
                    no("timestamp must be a finite number of epoch seconds")
                elif t > now + FUTURE_SLACK:
                    no("timestamp is in the future, check the sender's clock")
                elif t < now - self.max_age:
                    no("timestamp is older than this source accepts")
                else:
                    if len(self._q) >= self.MAX_PENDING:  # the engine has not drained it: refuse now rather than grow for ever
                        no("too many values are waiting for the engine; try again")
                        continue
                    # Older than the newest received, or older than one earlier in this batch: a gap in history, not a
                    # refusal. The trailing flag tells the engine to log it without touching the live state.
                    backfill = t <= self._high.get(point, 0)
                    if not backfill:
                        self._high[point] = t
                    item = (point, value if isinstance(value, str) else float(value), t, q)
                    self._q.append(item + (True,) if backfill else item)
                    accepted += 1
        return accepted, rejected

    def poll(self, ts):
        with self._lock:
            out, self._q, self._pending = self._q, [], set()
        kept = []
        for item in out:
            name = item[0]
            if name not in self.points:
                if self.factory is None or len(self.points) >= self.AUTO_POINTS_MAX:  # guard: a queued name must not outgrow the cap
                    self.dropped += 1
                    continue
                self.add(self.factory(name))  # created here, on the engine's own thread
            kept.append(item)
        return kept
