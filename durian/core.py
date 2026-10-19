"""DurianDB core model: source -> point -> history / detectors / publishers.

Original teaching implementation of the concepts in docs/research/01-concepts.md.
Not derived from Mango code.
"""
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


@dataclass
class Event:
    point: str
    level: Level
    message: str
    ts: float


@dataclass
class HighLimit:
    """Raises while value is above limit; clears when it returns below."""
    limit: float
    level: Level = Level.WARNING
    active: bool = False

    def check(self, point: "Point", value, ts) -> Optional[Event]:
        over = value > self.limit
        fired = over and not self.active
        self.active = over
        if fired:
            return Event(point.name, self.level, f"{value} > {self.limit}", ts)


@dataclass
class Point:
    name: str
    logging: Logging = Logging.ON_CHANGE
    tags: dict = field(default_factory=dict)
    detectors: list = field(default_factory=list)
    history: list = field(default_factory=list)  # (ts, value); a real store is separate
    last: object = None

    def update(self, value, ts, on_event: Callable[[Event], None]):
        changed = value != self.last
        if self.logging == Logging.ALL or (self.logging == Logging.ON_CHANGE and changed):
            self.history.append((ts, value))
        self.last = value
        for d in self.detectors:
            ev = d.check(self, value, ts)
            if ev:
                on_event(ev)


@dataclass
class Source:
    """A data source owns its points; subclasses implement poll()."""
    name: str
    points: dict = field(default_factory=dict)

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
            for name, value in s.poll(ts).items():
                s.points[name].update(value, ts, self._event)
                for pub in self.publishers:
                    pub(s.points[name], value, ts)


def demo():
    class Fake(Source):
        vals = iter([20, 20, 95, 96, 30])

        def poll(self, ts):
            return {"temp": next(self.vals)}

    src = Fake("plc")
    src.add(Point("temp", detectors=[HighLimit(90, Level.URGENT)]))
    out = []
    eng = Engine()
    eng.sources.append(src)
    eng.publishers.append(lambda p, v, ts: out.append((p.name, v)))
    for t in range(5):
        eng.tick(t)
    assert [v for _, v in src.points["temp"].history] == [20, 95, 96, 30]  # 2nd 20 not logged
    assert len(eng.events) == 1 and eng.events[0].level == Level.URGENT  # fires once, not twice
    assert len(out) == 5
    print("ok")


if __name__ == "__main__":
    demo()
