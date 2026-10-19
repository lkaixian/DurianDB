"""DurianDB core model: source -> point -> history / detectors / publishers.

Original teaching implementation of the concepts in docs/research. Not derived from Mango code.
"""
import math
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Optional


class Level(IntEnum):
    NONE = 0
    INFORMATION = 1
    IMPORTANT = 2
    WARNING = 3
    URGENT = 4
    CRITICAL = 5
    LIFE_SAFETY = 6


class Logging(IntEnum):
    NEVER = 0
    ON_CHANGE = 1
    ALL = 2


class Quality(IntEnum):
    """How far a value can be trusted. BAD keeps the last good value but is never alarmed on, logged as a reading or charted."""
    GOOD = 0
    UNCERTAIN = 1  # the value arrived but something about it is doubtful (a device clock in the future, a sender's own flag)
    BAD = 2  # the source is down or could not supply this point


SOURCE = "(source) "  # events about a data source use this prefix plus the source name as their "point"
FUTURE_SLACK = 60  # seconds a source clock may run ahead before its timestamp is not believed


@dataclass
class Event:
    point: str
    level: Level
    message: str
    ts: float
    cleared_ts: Optional[float] = None  # set when the condition returns to normal
    id: Optional[int] = None  # row id once stored
    held: Optional[str] = None  # "shelved" or "suppressed" while the alarm is recorded but not announced


def alarm_state(acked_ts, cleared_ts):
    """ISA-18.2 state of a stored alarm. 'closed' is the old Normal: back to normal and acknowledged."""
    if cleared_ts is None:
        return "active_acked" if acked_ts is not None else "active_unacked"
    return "closed" if acked_ts is not None else "rtn_unacked"


class Detector:
    kind = ""

    event = since = ok_since = None

    def reset(self):  # forget any alarm in progress (the point went out of service)
        self.event = self.since = self.ok_since = None

    def check(self, point, value, ts) -> Optional[Event]:  # a value arrived
        return None

    def tick(self, point, ts) -> Optional[Event]:  # time passed, maybe with no value
        return None


class _Latch(Detector):
    """Raises one event when a condition has held for `duration` seconds; clears when it stops."""

    def __init__(self, level=Level.WARNING, duration=0.0, off_delay=0.0):
        self.level, self.duration, self.off_delay = level, duration, off_delay  # on-delay: condition must hold `duration`; off-delay: must be absent `off_delay` to clear
        self.since, self.event, self.ok_since = None, None, None

    def evaluate(self, point, value, ts) -> Optional[str]:
        raise NotImplementedError

    def check(self, point, value, ts):
        msg = self.evaluate(point, value, ts)
        if msg is None:
            if self.event and self.off_delay:
                self.ok_since = ts if self.ok_since is None else self.ok_since
                if ts - self.ok_since < self.off_delay:
                    return None  # back to normal, but not for long enough yet
            self.since = self.ok_since = None
            if self.event:
                self.event.cleared_ts, self.event = ts, None
            return None
        self.ok_since = None
        if self.since is None:
            self.since = ts
        if self.event is None and ts - self.since >= self.duration:
            self.event = Event(point.name, self.level, msg, ts)
            return self.event


class HighLimit(_Latch):
    kind = "HIGH_LIMIT"

    def __init__(self, limit, level=Level.WARNING, duration=0.0, deadband=0.0, off_delay=0.0):
        super().__init__(level, duration, off_delay)
        self.limit, self.deadband = limit, deadband  # once raised, the value must pass the limit by `deadband` to clear, so a value hovering on the limit does not chatter

    def evaluate(self, point, value, ts):
        edge = self.limit - self.deadband if self.event else self.limit
        return f"{value} > {self.limit}" if value > edge else None


class LowLimit(HighLimit):
    kind = "LOW_LIMIT"

    def evaluate(self, point, value, ts):
        edge = self.limit + self.deadband if self.event else self.limit
        return f"{value} < {self.limit}" if value < edge else None


class RateOfChange(_Latch):
    """Event when the value moves faster than `per_second` units per second."""
    kind = "RATE_OF_CHANGE"

    def __init__(self, per_second, level=Level.WARNING, duration=0.0, off_delay=0.0):
        super().__init__(level, duration, off_delay)
        self.per_second, self.prev = per_second, None

    def evaluate(self, point, value, ts):
        prev, self.prev = self.prev, (value, ts)
        if prev is None or ts <= prev[1]:
            return None
        rate = abs(value - prev[0]) / (ts - prev[1])
        return f"changing {rate:.2f}/s > {self.per_second}/s" if rate > self.per_second else None


class NoUpdate(Detector):
    """Event when a point reports nothing for `seconds`; clears on the next value."""
    kind = "NO_UPDATE"

    def __init__(self, seconds, level=Level.WARNING):
        self.seconds, self.level, self.event = seconds, level, None

    def check(self, point, value, ts):
        if self.event:
            self.event.cleared_ts, self.event = ts, None

    def tick(self, point, ts):
        if self.event is None and point.last_recv is not None and ts - point.last_recv > self.seconds:  # our clock, not the device's
            self.event = Event(point.name, self.level, f"no update for {ts - point.last_recv:.0f}s", ts)
            return self.event


def state_label(point, value):
    """Text for a binary or multistate value from the point's `states` map ({"0": "Stopped", "1": "Running"}), else the number."""
    labels = point.meta.get("states") or {}
    key = str(int(value)) if float(value).is_integer() else str(value)
    return labels.get(key, f"{value:g}" if isinstance(value, float) else str(value))


class StateDetector(_Latch):
    """Alarm while the value is one of `states`: for example a pump that should never be 'Fault', or a valve that must not be 'Open'."""
    kind = "STATE"

    def __init__(self, states, level=Level.WARNING, duration=0.0, off_delay=0.0):
        super().__init__(level, duration, off_delay)
        self.states = {float(x) for x in states}

    def evaluate(self, point, value, ts):
        return f"state is {state_label(point, value)}" if value in self.states else None


class RangeDetector(_Latch):
    """Alarm while the value is outside [low, high] (mode 'outside'), or inside it (mode 'inside')."""
    kind = "RANGE"

    def __init__(self, low, high, mode="outside", level=Level.WARNING, duration=0.0, off_delay=0.0):
        super().__init__(level, duration, off_delay)
        self.low, self.high, self.mode = low, high, mode

    def evaluate(self, point, value, ts):
        inside = self.low <= value <= self.high
        if self.mode == "outside":
            return None if inside else f"{value:g} is outside {self.low:g} to {self.high:g}"
        return f"{value:g} is inside {self.low:g} to {self.high:g}" if inside else None


class ChangeDetector(Detector):
    """One informational event each time the value changes by more than `tolerance`. It returns to normal at once, so it only needs acknowledging."""
    kind = "CHANGE"

    def __init__(self, tolerance=0.0, level=Level.INFORMATION):
        self.tolerance, self.level, self.prev = tolerance, level, None

    def reset(self):
        self.prev = None

    def check(self, point, value, ts):
        prev, self.prev = self.prev, value
        if prev is not None and abs(value - prev) > self.tolerance:
            return Event(point.name, self.level, f"changed from {state_label(point, prev)} to {state_label(point, value)}", ts, cleared_ts=ts)


class NoChange(Detector):
    """Alarm when the value has not moved by more than `tolerance` for `seconds` while updates keep arriving: a frozen sensor or a stuck output."""
    kind = "NO_CHANGE"

    def __init__(self, seconds, tolerance=0.0, level=Level.WARNING):
        self.seconds, self.tolerance, self.level = seconds, tolerance, level
        self.ref = self.changed = self.event = None

    def reset(self):
        self.ref = self.changed = self.event = None

    def check(self, point, value, ts):
        if self.ref is None or abs(value - self.ref) > self.tolerance:
            self.ref, self.changed = value, ts
            if self.event:
                self.event.cleared_ts, self.event = ts, None

    def tick(self, point, ts):
        if self.event is None and self.changed is not None and point.quality != Quality.BAD and ts - self.changed > self.seconds:
            self.event = Event(point.name, self.level, f"value has not changed for {ts - self.changed:.0f}s", ts)
            return self.event


DETECTORS = {c.kind: c for c in (HighLimit, LowLimit, RateOfChange, NoUpdate, StateDetector, RangeDetector, ChangeDetector, NoChange)}
LATCHING = ("HIGH_LIMIT", "LOW_LIMIT", "RATE_OF_CHANGE", "STATE", "RANGE")  # these take duration (on-delay) and off_delay


def make_detector(cfg: dict) -> Detector:
    """Build a detector from a config dict. Raises ValueError naming the first problem."""
    kind = cfg.get("kind")
    if kind not in DETECTORS:
        raise ValueError(f"kind must be one of {sorted(DETECTORS)}")

    def num(key, default=None, lo=None):
        v = cfg.get(key, default)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or (lo is not None and v < lo):
            raise ValueError(f"{key} must be a number" + (f", {lo:g} or more" if lo is not None else ""))
        return v

    try:
        level = Level[str(cfg.get("level", "INFORMATION" if kind == "CHANGE" else "WARNING")).upper()]
    except KeyError:
        raise ValueError(f"level must be one of {[l.name for l in Level]}")
    late = {"duration": float(num("duration", 0, 0)), "off_delay": float(num("off_delay", 0, 0))} if kind in LATCHING else {}
    if kind in ("HIGH_LIMIT", "LOW_LIMIT"):
        return DETECTORS[kind](num("limit"), level, deadband=float(num("deadband", 0, 0)), **late)
    if kind == "RATE_OF_CHANGE":
        return RateOfChange(num("per_second"), level, **late)
    if kind == "NO_UPDATE":
        return NoUpdate(num("seconds"), level)
    if kind == "STATE":
        st = cfg.get("states")
        if not (isinstance(st, list) and 1 <= len(st) <= 20 and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in st)):
            raise ValueError("states must be a list of 1 to 20 numbers")
        return StateDetector(st, level, **late)
    if kind == "RANGE":
        low, high = num("low"), num("high")
        if low >= high:
            raise ValueError("high must be greater than low")
        if cfg.get("mode", "outside") not in ("outside", "inside"):
            raise ValueError("mode must be outside or inside")
        return RangeDetector(low, high, cfg.get("mode", "outside"), level, **late)
    if kind == "CHANGE":
        return ChangeDetector(float(num("tolerance", 0, 0)), level)
    return NoChange(num("seconds", lo=1), float(num("tolerance", 0, 0)), level)


@dataclass
class Point:
    name: str
    logging: Logging = Logging.ON_CHANGE
    tolerance: float = 0.0  # ON_CHANGE ignores moves of this size or less
    tags: dict = field(default_factory=dict)
    detectors: list = field(default_factory=list)
    cache: deque = field(default_factory=lambda: deque(maxlen=100))  # recent (ts, value), bounded
    unsaved: list = field(default_factory=list)  # logged (ts, value, quality) waiting for the store
    last: object = None
    last_ts: Optional[float] = None  # when the source says the value was taken
    last_recv: Optional[float] = None  # when we received it
    quality: int = Quality.GOOD
    in_service: bool = True  # False: detectors are not evaluated (set by an administrator, see App)
    meta: dict = field(default_factory=dict)  # presentation and access data: unit, source, roles, settable
    _logged: object = None
    _logged_q: int = Quality.GOOD

    def update(self, value, ts, on_event: Callable[[Event], None], quality=Quality.GOOD, recv=None):
        """`ts` is the source's time for the value, `recv` ours (defaults to ts). A BAD update only marks the point."""
        if quality == Quality.BAD:
            if value is None:
                value = self.last
            if self.logging != Logging.NEVER and value is not None and (self.logging == Logging.ALL or self._logged_q != quality):
                self._logged_q = quality
                self.unsaved.append((ts, value, int(quality)))
            self.quality = quality
            return
        log = self.logging == Logging.ALL or (
            self.logging == Logging.ON_CHANGE and (self._logged is None or self._logged_q == Quality.BAD or abs(value - self._logged) > self.tolerance))
        if log:
            self._logged, self._logged_q = value, Quality.GOOD
            self.cache.append((ts, value))
            self.unsaved.append((ts, value, int(quality)))
        self.last, self.last_ts, self.last_recv, self.quality = value, ts, ts if recv is None else recv, quality
        for d in self.detectors if self.in_service else ():
            ev = d.check(self, value, ts)
            if ev:
                on_event(ev)

    def take_unsaved(self):
        out, self.unsaved = self.unsaved, []
        return out


@dataclass
class Source:
    """A data source owns its points; subclasses implement poll()."""
    name: str
    points: dict = field(default_factory=dict)
    error: Optional[str] = None  # last poll failure, None when healthy
    manages_error: bool = False  # True for sources that poll in a thread and set `error` themselves
    meta: dict = field(default_factory=dict)  # type and read roles, filled by config.build_engine
    heartbeat: Optional[float] = None  # seconds of silence (no good value from any point) after which the source counts as down
    down_after: float = 10.0  # a failure must last this long before the source-down alarm, so a start-up or a blip does not alarm
    down_reason: Optional[str] = None  # why the source is down right now, None when it is not
    last_data: Optional[float] = None
    _down_since: Optional[float] = None

    def add(self, point: Point) -> Point:
        self.points[point.name] = point
        return point

    def poll(self, ts):  # returns {point name: value}
        raise NotImplementedError


class Engine:
    def __init__(self):
        self.sources, self.events, self.publishers = [], [], []
        self.down = {}  # source name -> its open source-down Event

    def _event(self, ev: Event):
        self.events.append(ev)

    def _watch(self, s, ts):
        """Source-down alarm: the link is lost, the source failed, or nothing good arrived within its heartbeat."""
        if s.last_data is None:
            s.last_data = ts
        reason = None
        if getattr(s, "connected", None) is False or (s.error and not s.manages_error):
            reason = s.error or "not connected"
        elif s.heartbeat and ts - s.last_data > s.heartbeat:
            reason = f"no data for {ts - s.last_data:.0f}s (heartbeat {s.heartbeat:g}s)"
        s.down_reason, ev = None, self.down.get(s.name)
        if reason is None:
            s._down_since = None
            if ev:
                ev.cleared_ts = ts
                del self.down[s.name]
            return
        if s._down_since is None:
            s._down_since = ts
        if ts - s._down_since >= s.down_after:
            s.down_reason = reason
            if ev is None:
                self.down[s.name] = ev = Event(SOURCE + s.name, Level.URGENT, f"source down: {reason}", ts)
                self._event(ev)
                for p in s.points.values():  # what we hold is no longer being refreshed
                    p.update(None, ts, self._event, Quality.BAD)

    def tick(self, ts):
        for s in self.sources:
            try:
                values = s.poll(ts)
                if not s.manages_error:  # background sources report their own failures
                    s.error = None
            except Exception as e:  # one broken source must not stop the others
                s.error, values = f"{type(e).__name__}: {e}", {}
            if isinstance(values, dict):  # {name: value} or {name: (value, source_time[, quality])}
                values = [(n, *(v if isinstance(v, tuple) else (v,))) for n, v in values.items()]
            for name, value, *rest in values:  # a list of (name, value[, time[, quality]]) keeps several samples of one point
                t, q = (rest[0] if rest else ts), (rest[1] if len(rest) > 1 else Quality.GOOD)
                p = s.points.get(name)
                if p is None:  # removed by a config change while the value was in flight
                    continue
                if q != Quality.BAD:
                    if t > ts + FUTURE_SLACK:  # a clock that runs ahead: keep the value, stamp it with our time, say it is doubtful
                        t, q = ts, max(q, Quality.UNCERTAIN)
                    elif p.last_ts is not None and t < p.last_ts:  # older than what we already have
                        continue
                if q != Quality.BAD:
                    s.last_data = ts
                p.update(value, t, self._event, q, ts)
                if p.last is not None:
                    for pub in self.publishers:
                        pub(p, p.last, t)
            self._watch(s, ts)
            for p in list(s.points.values()):
                for d in p.detectors if p.in_service else ():
                    ev = d.tick(p, ts)
                    if ev:
                        self._event(ev)
