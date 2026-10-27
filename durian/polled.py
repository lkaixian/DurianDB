"""Base class for sources that pull: a background thread calls fetch() every period.

A failure of fetch() as a whole backs the source off (period x scale^failures, capped) and reports it;
points that fetch() could not fill are reported as rejected and the rest still update.
"""
import math
import threading
import time
import urllib.request

from .core import Quality, Source


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # a webhook or poll must not be bounced to another host
        return None


def number(v):
    """A finite number, or ValueError. Booleans count as 1 and 0; text and nulls do not."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    raise ValueError(f"not a finite number: {type(v).__name__}")


def scale(point, v):
    m = point.meta.get("scale") or {}
    return v * m.get("multiplier", 1) + m.get("additive", 0)


class PolledSource(Source):
    def __init__(self, name, period=10.0, backoff_scale=1.5, backoff_max=300.0):
        super().__init__(name)
        self.manages_error = True
        self.period, self.backoff_scale, self.backoff_max = period, backoff_scale, backoff_max
        self.fails, self.next_try, self.connected = 0, 0.0, None
        self._latest, self._lock, self._stop = {}, threading.Lock(), threading.Event()

    def fetch(self):
        """Return ({point name: number}, [rejection messages]). Raise on any failure of the whole fetch."""
        raise NotImplementedError

    def failure_text(self, e):
        """The health message for a failed cycle. A source whose errors are already sentences can drop the class name."""
        return f"{type(e).__name__}: {e}"

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            started = time.monotonic()
            if time.time() >= self.next_try:
                self.cycle()
                self.poll_ms = (time.monotonic() - started) * 1000
            self._stop.wait(max(0.05, self.period - (time.monotonic() - started), self.next_try - time.time()))

    def cycle(self):
        """One fetch. Public so tests can drive it without the thread."""
        try:
            values, rejected = self.fetch()
        except Exception as e:  # anything the transport can raise counts as a failed cycle
            if self._stop.is_set():
                return
            self.fails += 1
            wait = min(self.period * self.backoff_scale ** self.fails, self.backoff_max)
            self.next_try = time.time() + wait
            self.error, self.connected = f"{self.failure_text(e)} (retry in {wait:.0f}s)", False
            with self._lock:
                self._latest.update({n: (None, time.time(), Quality.BAD) for n in self.points})
            return
        if self._stop.is_set():
            return
        self.fails, self.next_try, self.connected = 0, 0.0, True
        self.error = f"{len(rejected)} point(s) rejected: " + "; ".join(rejected[:3]) if rejected else None
        now = time.time()
        with self._lock:
            self._latest.update({n: (v, now, Quality.GOOD) for n, v in values.items()})
            self._latest.update({n: (None, now, Quality.BAD) for n in self.points if n not in values})  # fetch could not fill these

    def poll(self, ts):
        with self._lock:
            out, self._latest = self._latest, {}
        return out
