"""DurianDB core model: source -> point -> history / detectors / publishers.

Original teaching implementation of the concepts in docs/research. Not derived from Mango code.
"""
import math
import os
import re
import urllib.error
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Optional


_ERRNO_TAG = re.compile(r"\[Errno \d+\] ?")
_CLASS_PREFIX = re.compile(r"\A\w*(?:Error|Exception|Interrupt|Exit): ")


def sentence(ex):
    """One operator-safe line for a caught exception (docs/12 section 4): what happened, in the console's voice.

    Never an exception class name, never an `[Errno n]` tag, never a file path — those belong in the log,
    where the code that caught the exception writes it. The empty string means "this exception says nothing";
    the caller then supplies its own sentence. See tests/test_error_sentences.py, which keeps every other
    operator-facing string honest about it.
    """
    if not isinstance(ex, BaseException):  # None or an unexpected reason object says nothing
        return ""
    if isinstance(ex, OSError) and ex.strerror:  # the strerror is already a sentence and carries no path
        text = ex.strerror
    elif isinstance(ex, urllib.error.HTTPError):
        text = f"the server answered {ex.code} {ex.reason}".rstrip()  # reason is the HTTP phrase, not a class name
    elif isinstance(ex, urllib.error.URLError):
        text = sentence(ex.reason) if ex.reason is not None else "the server could not be reached"
    else:
        text = str(ex)
    text = _CLASS_PREFIX.sub("", _ERRNO_TAG.sub("", " ".join(text.split())))
    return text[:200]


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
    INTERVAL = 3  # one sample every `interval` seconds: the last value, or the average, minimum or maximum of the period


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
        # A non-finite reading is unknown, not evidence of normal: it proves neither that the condition
        # still holds nor that it has returned. Letting it reach a `value > limit` test reads as false and
        # clears a live alarm, which the next good value raises again (RTN/re-raise chatter). Hold state.
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return self._check(point, value, ts)

    def _check(self, point, value, ts) -> Optional[Event]:
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

    def _check(self, point, value, ts):
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
        prev = self.prev
        if prev is not None and ts <= prev[1]:
            # A sample at or before the last one says nothing about the rate: there is no interval to divide by.
            # It must not read as "normal", or an active alarm would clear on a repeated timestamp. Keep the
            # earlier reference point and, if an alarm is up, leave it up: no evidence either way.
            return self.event.message if self.event else None
        self.prev = (value, ts)
        if prev is None:
            return None
        rate = abs(value - prev[0]) / (ts - prev[1])
        return f"changing {rate:.2f}/s > {self.per_second}/s" if rate > self.per_second else None


class NoUpdate(Detector):
    """Event when a point reports nothing for `seconds`; clears on the next value."""
    kind = "NO_UPDATE"

    def __init__(self, seconds, level=Level.WARNING):
        self.seconds, self.level, self.event = seconds, level, None

    def _check(self, point, value, ts):
        if self.event:
            self.event.cleared_ts, self.event = ts, None

    def tick(self, point, ts):
        if self.event is None and point.last_recv is not None and ts - point.last_recv > self.seconds:  # our clock, not the device's
            self.event = Event(point.name, self.level, f"no update for {ts - point.last_recv:.0f}s", ts)
            return self.event


class TextDetector(_Latch):
    """Alarm while a text value equals (mode 'equals') or contains (mode 'contains') one of `values`, ignoring case: a status word such as FAULT."""
    kind = "TEXT"

    def __init__(self, values, mode="equals", level=Level.WARNING, duration=0.0, off_delay=0.0):
        super().__init__(level, duration, off_delay)
        self.values, self.mode = [v.lower() for v in values], mode

    def evaluate(self, point, value, ts):
        t = value.lower()
        hit = any(t == v if self.mode == "equals" else v in t for v in self.values)
        return f"text is {value!r}" if hit else None


def state_label(point, value):
    """Text for a binary or multistate value from the point's `states` map ({"0": "Stopped", "1": "Running"}), else the number."""
    if isinstance(value, str):
        return value
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

    def _check(self, point, value, ts):
        prev, self.prev = self.prev, value
        if prev is not None and ((prev != value) if isinstance(value, str) else abs(value - prev) > self.tolerance):
            return Event(point.name, self.level, f"changed from {state_label(point, prev)} to {state_label(point, value)}", ts, cleared_ts=ts)


class NoChange(Detector):
    """Alarm when the value has not moved by more than `tolerance` for `seconds` while updates keep arriving: a frozen sensor or a stuck output."""
    kind = "NO_CHANGE"

    def __init__(self, seconds, tolerance=0.0, level=Level.WARNING):
        self.seconds, self.tolerance, self.level = seconds, tolerance, level
        self.ref = self.changed = self.event = None

    def reset(self):
        self.ref = self.changed = self.event = None

    def _check(self, point, value, ts):
        if self.ref is None or abs(value - self.ref) > self.tolerance:
            self.ref, self.changed = value, ts
            if self.event:
                self.event.cleared_ts, self.event = ts, None

    def tick(self, point, ts):
        if self.event is None and self.changed is not None and point.quality != Quality.BAD and ts - self.changed > self.seconds:
            self.event = Event(point.name, self.level, f"value has not changed for {ts - self.changed:.0f}s", ts)
            return self.event


class Smoothness(_Latch):
    """Alarm while the last `samples` values scatter by more than `limit` (standard deviation): a noisy sensor, a hunting loop, a loose wire."""
    kind = "SMOOTHNESS"

    def __init__(self, limit, samples=10, level=Level.WARNING, duration=0.0, off_delay=0.0):
        super().__init__(level, duration, off_delay)
        self.limit, self.window = limit, deque(maxlen=samples)

    def reset(self):
        super().reset()
        self.window.clear()

    def evaluate(self, point, value, ts):
        self.window.append(value)
        if len(self.window) < self.window.maxlen:
            return None
        mean = sum(self.window) / len(self.window)
        sd = math.sqrt(sum((x - mean) ** 2 for x in self.window) / len(self.window))
        return f"scatter {sd:.3g} > {self.limit:g} over {len(self.window)} samples" if sd > self.limit else None


class Anomaly(_Latch):
    """Alarm when a value is further than `z_limit` standard deviations from the mean of the `window` samples before it.
    The sample is judged against the window it left, not the one it joins: a spike inside its own window raises the
    deviation it is measured by, which is how a lone 13 among tens looks mild. While raised, it clears only below
    the limit minus `deadband`, so a value sitting on the edge does not chatter."""
    kind = "ANOMALY"

    def __init__(self, z_limit, window=20, level=Level.WARNING, duration=0.0, off_delay=0.0, deadband=0.5):
        super().__init__(level, duration, off_delay)
        self.z_limit, self.deadband, self.window = z_limit, deadband, deque(maxlen=window)

    def reset(self):
        super().reset()
        self.window.clear()

    def evaluate(self, point, value, ts):
        prior, n = self.window, len(self.window)
        if n < 3:  # fewer than three samples have no spread worth measuring
            self.window.append(value)
            return None
        mean = sum(prior) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in prior) / n)
        self.window.append(value)  # after the verdict, so this sample cannot widen the spread that judges it
        if sd == 0:  # a flat window has no scale to divide by
            return None
        z = abs(value - mean) / sd
        edge = max(self.z_limit - self.deadband, 0.0) if self.event else self.z_limit
        if z <= edge:
            return None
        return f"{value:g} is {z:.2f} sd from mean {mean:.3g} over {n} samples"


class Cusum(_Latch):
    """Cumulative-sum drift alarm: adds up how far the value sits from `target` beyond a `slack` allowance, and alarms when that total passes `threshold`.
    It catches a small steady offset that no single limit would, and the total drains back once the value returns to target."""
    kind = "CUSUM"

    def __init__(self, target, threshold, slack=0.0, level=Level.WARNING, duration=0.0, off_delay=0.0):
        super().__init__(level, duration, off_delay)
        self.target, self.threshold, self.slack, self.hi, self.lo = target, threshold, slack, 0.0, 0.0

    def reset(self):
        super().reset()
        self.hi = self.lo = 0.0

    def evaluate(self, point, value, ts):
        self.hi = max(0.0, self.hi + value - self.target - self.slack)
        self.lo = max(0.0, self.lo + self.target - value - self.slack)
        if self.hi > self.threshold:
            return f"drifting above {self.target:g} (sum {self.hi:.3g} > {self.threshold:g})"
        if self.lo > self.threshold:
            return f"drifting below {self.target:g} (sum {self.lo:.3g} > {self.threshold:g})"


class TrendLimit(_Latch):
    """Predictive alarm: raises while the fitted trend of the last `window` (time, value) samples would reach `limit` within `horizon` seconds,
    before the value gets there (once it has crossed, HIGH_LIMIT / LOW_LIMIT own the alarm). The slope is the Theil-Sen median of pairwise slopes
    over at most the latest 100 samples; it counts only when it is over 4 standard errors of the least-squares slope, so noise around a flat line
    rarely alarms, and only when that holds for window/5 samples in a row. Once raised it holds until the projection is past 1.25 x `horizon` or the trend stops. The window is in memory only: a restart relearns it."""
    kind = "TREND_LIMIT"

    def __init__(self, limit, direction, horizon, window=20, level=Level.WARNING, duration=0.0, off_delay=0.0):
        super().__init__(level, duration, off_delay)
        self.limit, self.sign, self.horizon, self.window, self.hits = limit, 1 if direction == "above" else -1, horizon, deque(maxlen=window), 0

    def reset(self):
        super().reset()
        self.window.clear()
        self.hits = 0

    def evaluate(self, point, value, ts):
        w = self.window
        if w and ts <= w[-1][0]:  # no time has passed: a repeat says nothing about the slope, and must not clear or raise
            return self.event.message if self.event else None
        w.append((ts, value))
        msg = self._trend(value)
        self.hits = self.hits + 1 if msg else 0
        # a chance slope in noise is gone a sample or two later; a real trend persists, so a fresh alarm needs a streak
        return msg if msg and (self.event or self.hits >= max(1, w.maxlen // 5)) else None

    def _trend(self, value):
        w = self.window
        if len(w) < w.maxlen or (value - self.limit) * self.sign >= 0:
            return None
        pts = list(w)[-100:]
        slopes = sorted((y2 - y1) / (t2 - t1) for i, (t1, y1) in enumerate(pts) for t2, y2 in pts[i + 1:])
        m = len(slopes)
        slope = (slopes[m // 2] + slopes[(m - 1) // 2]) / 2 * self.sign  # positive = heading toward the limit
        if slope <= 0:
            return None
        n, mt, my = len(pts), sum(t for t, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)
        sxx = sum((t - mt) ** 2 for t, _ in pts)
        b = sum((t - mt) * (y - my) for t, y in pts) / sxx
        sse = sum((y - my - b * (t - mt)) ** 2 for t, y in pts)
        if slope <= 4 * math.sqrt(sse / (n - 2) / sxx):
            return None
        eta = abs(self.limit - value) / slope
        if eta > self.horizon * (1.25 if self.event else 1):
            return None
        when = f"{eta:.0f} s" if eta < 90 else f"{eta / 60:.0f} min" if eta < 5400 else f"{eta / 3600:.1f} h"
        return f"will reach {self.limit:g} in about {when} at the current rate"


DETECTORS = {c.kind: c for c in (HighLimit, LowLimit, RateOfChange, NoUpdate, StateDetector, RangeDetector, ChangeDetector, NoChange, Smoothness, Cusum, TextDetector, Anomaly, TrendLimit)}
LATCHING = ("HIGH_LIMIT", "LOW_LIMIT", "RATE_OF_CHANGE", "STATE", "RANGE", "SMOOTHNESS", "CUSUM", "TEXT", "ANOMALY", "TREND_LIMIT")  # these take duration (on-delay) and off_delay


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
    if kind == "TEXT":
        vs = cfg.get("values")
        if not (isinstance(vs, list) and 1 <= len(vs) <= 20 and all(isinstance(x, str) and 1 <= len(x) <= 64 for x in vs)):
            raise ValueError("values must be a list of 1 to 20 texts of up to 64 characters")
        if cfg.get("mode", "equals") not in ("equals", "contains"):
            raise ValueError("mode must be equals or contains")
        return TextDetector(vs, cfg.get("mode", "equals"), level, **late)
    if kind == "SMOOTHNESS":
        n = cfg.get("samples", 10)
        if not isinstance(n, int) or isinstance(n, bool) or not 3 <= n <= 1000:
            raise ValueError("samples must be a whole number from 3 to 1000")
        return Smoothness(num("limit", lo=0), n, level, **late)
    if kind == "CUSUM":
        return Cusum(num("target"), num("threshold", lo=0), float(num("slack", 0, 0)), level, **late)
    if kind == "ANOMALY":
        n = cfg.get("window", 20)
        if not isinstance(n, int) or isinstance(n, bool) or not 3 <= n <= 1000:
            raise ValueError("window must be a whole number from 3 to 1000")
        return Anomaly(num("z_limit", lo=0.1), n, level, deadband=float(num("deadband", 0.5, 0)), **late)
    if kind == "TREND_LIMIT":
        limit = num("limit")
        if cfg.get("direction") not in ("above", "below"):
            raise ValueError("direction must be above or below")
        h, n = num("horizon"), cfg.get("window", 20)
        if not 60 <= h <= 86400:
            raise ValueError("horizon must be from 60 to 86400 seconds")
        if not isinstance(n, int) or isinstance(n, bool) or not 5 <= n <= 1000:
            raise ValueError("window must be a whole number from 5 to 1000")
        return TrendLimit(limit, cfg["direction"], h, n, level, **late)
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
    unsaved_lost: int = 0  # samples dropped past UNSAVED_MAX, added to the app's lost-samples count
    UNSAVED_MAX: int = 10_000  # ponytail: one per-point cap; a per-source share if one source starves the rest
    last: object = None
    last_ts: Optional[float] = None  # when the source says the value was taken
    last_recv: Optional[float] = None  # when we received it
    quality: int = Quality.GOOD
    in_service: bool = True  # False: detectors are not evaluated (set by an administrator, see App)
    meta: dict = field(default_factory=dict)  # presentation and access data: unit, source, roles, settable
    text: bool = False  # an alphanumeric point: the value is a short string, logged on change, with no numeric rollups or trend
    interval: float = 0.0  # seconds between samples when logging is INTERVAL
    interval_mode: str = "instant"  # instant, avg, min or max
    _logged: object = None
    _logged_q: int = Quality.GOOD
    _period: list = field(default_factory=lambda: [0, 0.0, math.inf, -math.inf])  # count, sum, min, max of the current interval: constant size
    _period_start: Optional[float] = None

    def _queue_sample(self, sample):
        """Keep the newest UNSAVED_MAX samples: a source that floods one tick, or ticks that keep failing, must not grow this."""
        self.unsaved.append(sample)
        if len(self.unsaved) > self.UNSAVED_MAX:
            drop = len(self.unsaved) - self.UNSAVED_MAX
            del self.unsaved[:drop]
            self.unsaved_lost += drop

    def update(self, value, ts, on_event: Callable[[Event], None], quality=Quality.GOOD, recv=None, backfill=False):
        """`ts` is the source's time for the value, `recv` ours (defaults to ts). A BAD update only marks the point.
        A `backfill` is an explicitly old sample: it fills history only, so it never becomes last and detectors,
        interval periods and ON_CHANGE filtering keep seeing live samples alone."""
        if backfill:
            if value is not None and self.logging != Logging.NEVER:
                self._queue_sample((ts, value, int(quality)))
            return
        if quality == Quality.BAD:
            if value is None:
                value = self.last
            if self.logging != Logging.NEVER and value is not None and (self.logging == Logging.ALL or self._logged_q != quality):
                self._logged_q = quality
                self._queue_sample((ts, value, int(quality)))
            self.quality = quality
            return
        log, logged = self.logging == Logging.ALL or (
            self.logging == Logging.ON_CHANGE and (self._logged is None or self._logged_q == Quality.BAD or (value != self._logged if self.text else abs(value - self._logged) > self.tolerance))), value
        if self.logging == Logging.INTERVAL:
            p = self._period
            p[0], p[1], p[2], p[3] = p[0] + 1, p[1] + value, min(p[2], value), max(p[3], value)
            if self._period_start is None:
                self._period_start = ts
            if ts - self._period_start >= self.interval:  # the period is over: one sample, stamped with its end
                logged = {"instant": value, "avg": p[1] / p[0], "min": p[2], "max": p[3]}[self.interval_mode]
                log, self._period, self._period_start = True, [0, 0.0, math.inf, -math.inf], ts
        if log:
            self._logged, self._logged_q = logged, Quality.GOOD
            self.cache.append((ts, logged))
            self._queue_sample((ts, logged, int(quality)))
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
    AUTO_POINTS_MAX = 10_000  # ponytail: one cap per source for points auto-created from a topic or pushed name; a configurable limit if a real site needs more
    name: str
    points: dict = field(default_factory=dict)
    error: Optional[str] = None  # last poll failure, None when healthy
    manages_error: bool = False  # True for sources that poll in a thread and set `error` themselves
    error_is_down: bool = False  # a managing source whose `error` is a real fault, not just a note on the points
    meta: dict = field(default_factory=dict)  # type and read roles, filled by config.build_engine
    heartbeat: Optional[float] = None  # seconds of silence (no good value from any point) after which the source counts as down
    down_after: float = 10.0  # a failure must last this long before the source-down alarm, so a start-up or a blip does not alarm
    down_reason: Optional[str] = None  # why the source is down right now, None when it is not
    last_data: Optional[float] = None
    period: Optional[float] = None  # seconds between polls, for sources that poll
    poll_ms: Optional[float] = None  # how long the last poll took
    overruns: int = 0  # polls that took longer than the period
    poll_no: int = 0  # cycles run since this source was built; the first is poll 1
    _down_since: Optional[float] = None

    def add(self, point: Point) -> Point:
        self.points[point.name] = point
        return point

    def due(self, point: Point) -> bool:
        """Whether this poll is one of the point's scan slots: poll 1, then 1+n, 1+2n for a scan of n.

        A source starts at poll 1 when it is built, so a reload gives every point a fresh value and the
        phase needs nothing carried across the reload."""
        return (self.poll_no - 1) % point.meta.get("scan", 1) == 0

    def poll(self, ts):  # returns {point name: value}
        raise NotImplementedError


class Engine:
    EVENT_MAX = 5_000  # ponytail: one cap for the whole tick; a per-source cap if one noisy source must not starve the others

    def __init__(self):
        self.sources, self.events, self.publishers = [], [], []
        self.events_lost = 0  # alarms dropped past EVENT_MAX in one tick; the app announces the count
        self.down = {}  # source name -> its open source-down Event
        self.slow = {}  # source name -> its open poll-overrun Event
        self.startable = []  # sources built but not started yet; see start_sources()
        self._prev_tick = None  # to notice the wall clock stepping back

    def start_sources(self):
        """Start this engine's background pollers. build_engine leaves them unstarted so the caller can finish its
        own store work first, and a second call does nothing: a source whose thread is already running must not get
        a second one."""
        sources, self.startable = self.startable, []
        for s in sources:
            try:
                s.start()
            except Exception as e:  # the same degradation build_engine gives a source that could not be built
                s.error = f"cannot start: {sentence(e) or 'check its settings'}"

    def _event(self, ev: Event):
        self.events.append(ev)
        if len(self.events) > self.EVENT_MAX:  # the app drains this every tick; a tick that raises must not let it grow for ever
            drop = len(self.events) - self.EVENT_MAX
            del self.events[:drop]
            self.events_lost += drop

    def _overrun(self, s, ts):
        """A poll that takes longer than its period means readings are older than configured: say so until polls fit again."""
        ev, late = self.slow.get(s.name), bool(s.period and s.poll_ms is not None and s.poll_ms / 1000 > s.period)
        if late and ev is None:
            s.overruns += 1
            self.slow[s.name] = ev = Event(SOURCE + s.name, Level.URGENT, f"poll took {s.poll_ms / 1000:.1f}s, longer than its {s.period:g}s period", ts)
            self._event(ev)
        elif not late and ev is not None:
            ev.cleared_ts = ts
            del self.slow[s.name]

    def _watch(self, s, ts):
        self._overrun(s, ts)
        """Source-down alarm: the link is lost, the source failed, or nothing good arrived within its heartbeat."""
        if s.last_data is None:
            s.last_data = ts
        reason = None
        if getattr(s, "connected", None) is False or (s.error and (not s.manages_error or getattr(s, "error_is_down", False))):
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
                # A source whose error is its fault already said it in the operator's language; do not wrap it.
                own_error = getattr(s, "error_is_down", False) and bool(s.error)
                self.down[s.name] = ev = Event(SOURCE + s.name, Level.URGENT, reason if own_error else f"source down: {reason}", ts)
                self._event(ev)
                if not own_error:  # such a source already marked its points BAD when it failed
                    for p in s.points.values():  # what we hold is no longer being refreshed
                        p.update(None, ts, self._event, Quality.BAD)

    def tick(self, ts):
        if self._prev_tick is not None and ts < self._prev_tick:  # NTP stepped the clock back: polled samples would all look old
            for s in self.sources:
                for p in s.points.values():
                    p.last_ts = None
            self._event(Event(SOURCE + "clock", Level.INFORMATION, f"the system clock stepped back {self._prev_tick - ts:.0f}s", ts))
        self._prev_tick = ts
        for s in self.sources:
            try:
                values = s.poll(ts)
                if not s.manages_error:  # background sources report their own failures
                    s.error = None
            except Exception as e:  # one broken source must not stop the others
                s.error, values = sentence(e) or "this source did not answer", {}
            if isinstance(values, dict):  # {name: value} or {name: (value, source_time[, quality])}
                values = [(n, *(v if isinstance(v, tuple) else (v,))) for n, v in values.items()]
            for name, value, *rest in values:  # a list of (name, value[, time[, quality[, backfill]]]) keeps several samples of one point
                t, q = (rest[0] if rest else ts), (rest[1] if len(rest) > 1 else Quality.GOOD)
                # ponytail: a 4th positional element would silently mean backfill; typed samples or a source capability flag if another source grows one
                bf = bool(rest[2]) if len(rest) > 2 else False  # ingest-only: an explicit old sample fills history only
                p = s.points.get(name)
                if p is None:  # removed by a config change while the value was in flight
                    continue
                if not math.isfinite(t):  # NaN would disable the out-of-order guard and write a NULL-time row
                    t, q = ts, max(q, Quality.UNCERTAIN)
                if q != Quality.BAD:
                    if t > ts + FUTURE_SLACK:  # a clock that runs ahead: keep the value, stamp it with our time, say it is doubtful
                        t, q = ts, max(q, Quality.UNCERTAIN)
                    elif not bf and p.last_ts is not None and t < p.last_ts:  # older than what we already have
                        continue
                if q != Quality.BAD:
                    s.last_data = ts
                p.update(value, t, self._event, q, ts, backfill=bf)
                if not bf and p.last is not None:
                    for pub in self.publishers:
                        pub(p, p.last, t)
            self._watch(s, ts)
            for p in list(s.points.values()):
                for d in p.detectors if p.in_service else ():
                    ev = d.tick(p, ts)
                    if ev:
                        self._event(ev)


def pid_alive(pid):
    """False only when the process is known to be gone; "cannot tell" counts as alive."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            k.OpenProcess.restype = ctypes.c_void_p
            h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return ctypes.GetLastError() != 87  # 87: no such pid; access denied (5) means it exists
            try:
                code = ctypes.c_ulong()
                return not k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code)) or code.value == 259  # STILL_ACTIVE
            finally:
                k.CloseHandle(ctypes.c_void_p(h))
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True
