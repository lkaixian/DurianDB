"""Forecast methods and a rolling-origin backtest, so forecast quality is measured on the point's own history.

Stdlib only, no stored model. Every method has one signature, (history_pairs, step_ts_list) -> a list of
(ts, value, low, high) or None per requested ts (None: the method has no honest answer there). run() cuts the
history at up to `folds` points, fits each method on what came before the cut, predicts the next `horizon`
samples at their real timestamps and scores them against what was actually stored.
"""
import math
import statistics

from . import seasonal
from .forecast import forecast as ols_forecast

MIN_TRAIN = 6  # the same floor forecast.forecast() uses
SUBSAMPLE = 200  # Theil-Sen is O(n^2) in pairs; above this many points an evenly spaced subset is used
Z95 = 1.96
EPS = 1e-9


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _mad_sigma(values):
    med = statistics.median(values)
    return 1.4826 * statistics.median(abs(v - med) for v in values)


def theil_sen(pairs):
    """(slope, intercept) of the line through the median of all pairwise slopes; intercept = median(v - slope * t).

    Up to a third of the points can be wild before the slope moves. Above SUBSAMPLE points an evenly spaced
    subset gives the slope (ponytail: ~20k slopes at most per call), the intercept still uses every point.
    """
    pts = sorted(pairs)
    sub = pts if len(pts) <= SUBSAMPLE else [pts[i * (len(pts) - 1) // (SUBSAMPLE - 1)] for i in range(SUBSAMPLE)]
    slopes = [(v2 - v1) / (t2 - t1) for i, (t1, v1) in enumerate(sub) for t2, v2 in sub[i + 1:] if t2 > t1]
    slope = statistics.median(slopes) if slopes else 0.0
    return slope, statistics.median(v - slope * t for t, v in pts)


def _grid(pairs, want):
    """(last_ts, median step, [steps ahead for each wanted ts]) or None when the history has no spacing."""
    ts = sorted(t for t, _ in pairs)
    gaps = sorted(b - a for a, b in zip(ts, ts[1:]) if b > a)
    if not gaps:
        return None
    step = gaps[len(gaps) // 2]
    return ts[-1], step, [max(1, round((t - ts[-1]) / step)) for t in want]


def naive_last(pairs, want):
    """The last value, with a random-walk band: 1.96 * robust sigma of the step changes * sqrt(steps ahead)."""
    pts = sorted(pairs)
    grid = _grid(pts, want)
    if len(pts) < 2 or grid is None:
        return [None] * len(want)
    sigma = _mad_sigma([b[1] - a[1] for a, b in zip(pts, pts[1:])])
    last = pts[-1][1]
    return [(t, last, last - Z95 * sigma * math.sqrt(k), last + Z95 * sigma * math.sqrt(k)) for t, k in zip(want, grid[2])]


def linear(pairs, want):
    """forecast.forecast()'s least squares and prediction interval, read at the wanted steps."""
    grid = _grid(pairs, want)
    if grid is None:
        return [None] * len(want)
    res = ols_forecast(pairs, max(grid[2]))["items"]
    return [(t, res[k - 1]["value"], res[k - 1]["low"], res[k - 1]["high"]) for t, k in zip(want, grid[2])] if res else [None] * len(want)


def robust(pairs, want):
    """Theil-Sen line; band is 1.96 * 1.4826 * MAD of its residuals.

    ponytail: constant width, no widening with distance; add the regression-style term if coverage far out matters.
    """
    pts = sorted(pairs)
    if len(pts) < MIN_TRAIN:
        return [None] * len(want)
    t0 = pts[-1][0]  # centre time on the last sample so the slope * t product stays small
    slope, icpt = theil_sen([(t - t0, v) for t, v in pts])
    half = Z95 * _mad_sigma([v - (icpt + slope * (t - t0)) for t, v in pts])
    return [(t, icpt + slope * (t - t0), icpt + slope * (t - t0) - half, icpt + slope * (t - t0) + half) for t in want]


def seasonal_naive(pairs, want, tz_offset_s=0):
    """The median of the same hour of the week in the history (seasonal.profile), +- 1.96 sigma; None for an absent slot."""
    prof = seasonal.profile(pairs, tz_offset_s)
    out = []
    for t in want:
        s = prof.get(seasonal.slot_of(t, tz_offset_s))
        half = Z95 * 1.4826 * s["mad"] if s else 0.0
        out.append((t, s["median"], s["median"] - half, s["median"] + half) if s else None)
    return out


METHODS = {"naive_last": naive_last, "linear": linear, "robust": robust, "seasonal_naive": seasonal_naive}


def run(pairs, horizon, folds, tz_offset_s=0):
    """Rolling-origin evaluation: {"methods": {name: {mae, rmse, mape, coverage, n}}, "note"}. Never raises on thin data."""
    pts = sorted((float(t), float(v)) for t, v in pairs if _finite(t) and _finite(v))
    cuts = [c for c in (len(pts) - horizon * (k + 1) for k in range(folds)) if c >= MIN_TRAIN]
    if not cuts:
        return {"methods": {}, "note": f"a backtest needs at least {MIN_TRAIN} samples before a {horizon}-step test window; history has {len(pts)}"}
    errs = {name: [] for name in METHODS}  # (error, actual, inside)
    for cut in cuts:
        train, test = pts[:cut], pts[cut:cut + horizon]
        want = [t for t, _ in test]
        for name, fn in METHODS.items():
            rows = fn(train, want, tz_offset_s) if fn is seasonal_naive else fn(train, want)
            errs[name] += [(r[1] - a, a, r[2] <= a <= r[3]) for r, (_t, a) in zip(rows, test) if r is not None]
    methods = {}
    for name, e in errs.items():
        if e:
            pct = [abs(d) / abs(a) for d, a, _ in e if abs(a) > EPS]
            methods[name] = {"mae": sum(abs(d) for d, _, _ in e) / len(e), "rmse": math.sqrt(sum(d * d for d, _, _ in e) / len(e)),
                             "mape": sum(pct) / len(pct) if pct else None, "coverage": sum(i for _, _, i in e) / len(e), "n": len(e)}
    return {"methods": methods, "note": None if methods else "no method could forecast from this history"}
