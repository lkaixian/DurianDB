"""DurianDB core model: source -> point -> history / detectors / publishers.

Original teaching implementation of the concepts in docs/research. Not derived from Mango code.
"""
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


FUTURE_SLACK = 60  # seconds a source clock may run ahead before its timestamp is not believed


@dataclass
class Event:
    point: str
    level: Level
    message: str
    ts: float
    cleared_ts: Optional[float] = None  # set when the condition returns to normal
    id: Optional[int] = None  # row id once stored


class Detector:
    kind = ""

    def check(self, point, value, ts) -> Optional[Event]:  # a value arrived
        return None

    def tick(self, point, ts) -> Optional[Event]:  # time passed, maybe with no value
        return None


class _Latch(Detector):
    """Raises one event when a condition has held for `duration` seconds; clears when it stops."""

    def __init__(self, level=Level.WARNING, duration=0.0):
        self.level, self.duration = level, duration
        self.since, self.event = None, None

    def evaluate(self, point, value, ts) -> Optional[str]:
        raise NotImplementedError

    def check(self, point, value, ts):
        msg = self.evaluate(point, value, ts)
        if msg is None:
            self.since = None
            if self.event:
                self.event.cleared_ts, self.event = ts, None
            return None
        if self.since is None:
            self.since = ts
        if self.event is None and ts - self.since >= self.duration:
            self.event = Event(point.name, self.level, msg, ts)
            return self.event


class HighLimit(_Latch):
    kind = "HIGH_LIMIT"

    def __init__(self, limit, level=Level.WARNING, duration=0.0):
        super().__init__(level, duration)
        self.limit = limit

    def evaluate(self, point, value, ts):
        return f"{value} > {self.limit}" if value > self.limit else None


class LowLimit(HighLimit):
    kind = "LOW_LIMIT"

    def evaluate(self, point, value, ts):
        return f"{value} < {self.limit}" if value < self.limit else None


class RateOfChange(_Latch):
    """Event when the value moves faster than `per_second` units per second."""
    kind = "RATE_OF_CHANGE"

    def __init__(self, per_second, level=Level.WARNING, duration=0.0):
        super().__init__(level, duration)
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


DETECTORS = {c.kind: c for c in (HighLimit, LowLimit, RateOfChange, NoUpdate)}
_ARG = {"HIGH_LIMIT": "limit", "LOW_LIMIT": "limit", "RATE_OF_CHANGE": "per_second", "NO_UPDATE": "seconds"}


def make_detector(cfg: dict) -> Detector:
    """Build a detector from a config dict: {kind, limit|per_second|seconds, level, duration}."""
    kind = cfg.get("kind")
    if kind not in DETECTORS:
        raise ValueError(f"kind must be one of {sorted(DETECTORS)}")
    arg = _ARG[kind]
    if not isinstance(cfg.get(arg), (int, float)) or isinstance(cfg.get(arg), bool):
        raise ValueError(f"{arg} must be a number")
    try:
        level = Level[str(cfg.get("level", "WARNING")).upper()]
    except KeyError:
        raise ValueError(f"level must be one of {[l.name for l in Level]}")
    duration = cfg.get("duration", 0)
    if not isinstance(duration, (int, float)) or duration < 0:
        raise ValueError("duration must be a number of seconds, zero or more")
    extra = {} if kind == "NO_UPDATE" else {"duration": float(duration)}
    return DETECTORS[kind](cfg[arg], level, **extra)


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
        for d in self.detectors:
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

    def add(self, point: Point) -> Point:
        self.points[point.name] = point
        return point

    def poll(self, ts):  # returns {point name: value}
        raise NotImplementedError


class Engine:
    def __init__(self):
        self.sources, self.events, self.publishers = [], [], []

    def _event(self, ev: Event):
        self.events.append(ev)

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
                p.update(value, t, self._event, q, ts)
                if p.last is not None:
                    for pub in self.publishers:
                        pub(p, p.last, t)
            for p in list(s.points.values()):
                for d in p.detectors:
                    ev = d.tick(p, ts)
                    if ev:
                        self._event(ev)
