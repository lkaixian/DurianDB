"""Generate the Rust crate's detector parity vectors from the REAL Python detectors.

The Rust crate `rust/` is only a useful experiment if it runs the same state machine as
`durian.core`. This tool drives the Python detectors (the reference the server ships) over fixed
streams and writes plain-text fixtures to `rust/vectors/`. `rust/tests/parity.rs` replays them
against the Rust `Detector` with no dependencies; `tests/test_rust_parity_vectors.py` fails the
Python suite when a committed fixture no longer matches what `durian/core.py` produces.

Usage: python tools/rust_parity_vectors.py
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from durian import core  # noqa: E402

VECTOR_DIR = ROOT / "rust" / "vectors"

# Each op is ("C", ts, value) for a value arriving, or ("T", now, last_recv) for time passing.
# Expected tokens are what the Python detector did on that operation:
#   NORMAL  - no transition, no alarm up
#   ACTIVE  - no transition, an alarm is up
#   RAISED  - this operation raised an alarm
#   CLEARED - this operation cleared the alarm it was holding
VECTORS = {
    "high_limit": {
        "kind": "HIGH_LIMIT",
        "config": {"limit": 10.0, "deadband": 2.0},
        "duration": 0.0, "off_delay": 0.0,
        "ops": [("C", 0, 5), ("C", 1, 11), ("C", 2, 12), ("C", 3, 9.5), ("C", 4, 7.9)],
    },
    "low_limit": {
        "kind": "LOW_LIMIT",
        "config": {"limit": 2.0, "deadband": 0.5},
        "duration": 0.0, "off_delay": 0.0,
        "ops": [("C", 0, 3), ("C", 1, 1), ("C", 2, 2.2), ("C", 3, 2.6)],
    },
    # on-delay: the condition must hold for `duration` before the alarm exists
    "high_limit_on_delay": {
        "kind": "HIGH_LIMIT",
        "config": {"limit": 10.0, "deadband": 0.0},
        "duration": 5.0, "off_delay": 0.0,
        "ops": [("C", 0, 11), ("C", 4, 11), ("C", 5, 11), ("C", 6, 5)],
    },
    # off-delay: the condition must be absent for `off_delay` before the alarm clears
    "high_limit_off_delay": {
        "kind": "HIGH_LIMIT",
        "config": {"limit": 10.0, "deadband": 0.0},
        "duration": 0.0, "off_delay": 4.0,
        "ops": [("C", 0, 11), ("C", 1, 5), ("C", 2, 5), ("C", 4, 5), ("C", 5, 5)],
    },
    "rate_of_change": {
        "kind": "RATE_OF_CHANGE",
        "config": {"per_second": 2.0},
        "duration": 5.0, "off_delay": 0.0,
        # A repeated or backdated timestamp carries no interval. With an alarm up it holds (neither
        # raises nor clears); with no alarm up it is "condition false", which resets the on-delay, so
        # (8, 40) is not the 5s of held condition the earlier rate at (3, 20) started.
        "ops": [("C", 0, 10), ("C", 3, 20), ("C", 3, 25), ("C", 8, 40),
                ("C", 13, 80), ("C", 18, 120), ("C", 18, 130), ("C", 19, 130), ("C", 20, 130)],
    },
    "no_change": {
        "kind": "NO_CHANGE",
        "config": {"seconds": 5.0, "tolerance": 0.1},
        "duration": 0.0, "off_delay": 0.0,
        "ops": [("C", 0, 1), ("T", 4, 4), ("T", 6, 6), ("C", 7, 2)],
    },
    "change": {
        "kind": "CHANGE",
        "config": {"tolerance": 0.5},
        "duration": 0.0, "off_delay": 0.0,
        "ops": [("C", 0, 1), ("C", 1, 1.2), ("C", 2, 2), ("C", 3, 2.4)],
    },
    # on-delay and off-delay on a second kind
    "state": {
        "kind": "STATE",
        "config": {"states": [2.0]},
        "duration": 3.0, "off_delay": 2.0,
        "ops": [("C", 0, 2), ("C", 3, 2), ("C", 4, 1), ("C", 5, 1), ("C", 6, 1)],
    },
    "range": {
        "kind": "RANGE",
        "config": {"low": 0.0, "high": 10.0, "inside": False},
        "duration": 0.0, "off_delay": 0.0,
        "ops": [("C", 0, 5), ("C", 1, 11), ("C", 2, 12), ("C", 3, 10)],
    },
    "smoothness": {
        "kind": "SMOOTHNESS",
        "config": {"limit": 5.0, "samples": 4},
        "duration": 0.0, "off_delay": 0.0,
        "ops": [("C", i, 10) for i in range(4)]
        + [("C", 4, 100)] + [("C", i, 10) for i in range(5, 9)],
    },
    "cusum": {
        "kind": "CUSUM",
        "config": {"target": 10.0, "slack": 0.0, "threshold": 5.0},
        "duration": 0.0, "off_delay": 0.0,
        "ops": [("C", 0, 12), ("C", 1, 12), ("C", 2, 12), ("C", 3, 12), ("C", 4, 5)],
    },
    "anomaly": {
        "kind": "ANOMALY",
        "config": {"window": 8.0, "z_limit": 3.0},
        "duration": 0.0, "off_delay": 0.0,
        "ops": [("C", 0, 10), ("C", 1, 11), ("C", 2, 10), ("C", 3, 11), ("C", 4, 13), ("C", 5, 10)],
    },
    "no_update": {
        "kind": "NO_UPDATE",
        "config": {"seconds": 60.0},
        "duration": 0.0, "off_delay": 0.0,
        "ops": [("T", 10, 10), ("T", 100, 10), ("C", 101, 1), ("T", 150, 101)],
    },
    # The NaN boundary. A non-finite sample is unknown, not evidence of normality: both sides
    # reject it and hold the live alarm (RAISED, then ACTIVE twice), which pins the parity.
    "high_limit_nonfinite": {
        "kind": "HIGH_LIMIT",
        "config": {"limit": 10.0, "deadband": 0.0},
        "duration": 0.0, "off_delay": 0.0,
        "ops": [("C", 0, 11), ("C", 1, float("nan")), ("C", 2, 11)],
    },
}


def fnum(value):
    """A token both Python and Rust parse as this float."""
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return repr(float(value))


def build(spec):
    """Build the real Python detector, through the same `make_detector` the server uses."""
    cfg = dict(spec["config"])
    cfg.update(kind=spec["kind"], duration=spec["duration"], off_delay=spec["off_delay"])
    if spec["kind"] == "RANGE":
        cfg["mode"] = "inside" if cfg.pop("inside") else "outside"
    for whole in ("samples", "window"):  # the config validators require ints for these
        if whole in cfg:
            cfg[whole] = int(cfg[whole])
    return core.make_detector(cfg)


def run(spec):
    """Drive the detector and return one state token per operation."""
    detector = build(spec)
    point = core.Point("p", meta={"states": {"2": "Fault"}})
    tokens = []
    for op, ts, arg in spec["ops"]:
        was_active = detector.event is not None
        if op == "C":
            point.last_recv = ts  # what Point.update does before the detectors run
            point.quality = core.Quality.GOOD
            event = detector.check(point, arg, ts)
        else:
            point.last_recv = arg  # the tick's own clock reading for the last good value
            point.quality = core.Quality.GOOD
            event = detector.tick(point, ts)
        is_active = detector.event is not None
        if event is not None:
            tokens.append("RAISED")
        elif was_active and not is_active:
            tokens.append("CLEARED")
        elif is_active:
            tokens.append("ACTIVE")
        else:
            tokens.append("NORMAL")
    return tokens


def render(spec):
    lines = [f"kind {spec['kind']}"]
    config = [f"{k}={fnum(v)}" for k, v in spec["config"].items() if not isinstance(v, list)]
    if spec["kind"] == "STATE":
        config.append("states=" + ",".join(fnum(v) for v in spec["config"]["states"]))
    lines.append("config " + " ".join(config))
    lines.append(f"delays {fnum(spec['duration'])} {fnum(spec['off_delay'])}")
    lines.append("samples")
    lines.extend(f"{op} {fnum(ts)} {fnum(arg)}" for op, ts, arg in spec["ops"])
    lines.append("expected")
    tokens = run(spec)
    assert len(tokens) == len(spec["ops"]), spec
    lines.extend(tokens)
    return "\n".join(lines) + "\n"


def generate_all():
    return {f"{name}.txt": render(spec) for name, spec in VECTORS.items()}


def main():
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in generate_all().items():
        path = VECTOR_DIR / name
        path.write_text(text, encoding="utf-8", newline="\n")
        tokens = text.split("expected\n", 1)[1].split()
        print(f"{name}: {len(tokens)} samples, tokens {' '.join(tokens)}")


if __name__ == "__main__":
    main()
