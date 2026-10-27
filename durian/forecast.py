"""Baselines, robust anomaly scores and a short-horizon forecast over history already in the store.

Stdlib only. Nothing here keeps a model: every answer is derived from the (ts, value) pairs handed in, so a
restart loses no state and a point needs no trained file. The ANOMALY detector in core.py judges a live deque;
score() is the same statistic over stored history, with median/MAD instead of mean/sd.
"""
import math


def ewma(values, alpha):
    """Running mean and EWMA of the squared residual: (mean, sigma).

    mean <- alpha * value + (1 - alpha) * mean
    var  <- alpha * (value - previous mean)^2 + (1 - alpha) * var

    A constant series gives sigma 0; a step is absorbed within roughly 1/alpha samples. The residual uses the
    mean before the sample, so one wild value cannot hide inside the mean it is measured against.
    """
    if not (isinstance(alpha, (int, float)) and not isinstance(alpha, bool) and 0 < alpha <= 1):
        raise ValueError("alpha must be greater than 0 and at most 1")
    it = iter(values)
    try:
        mean = float(next(it))
    except StopIteration:
        raise ValueError("ewma needs at least one value")
    var = 0.0
    for value in it:
        prev, mean = mean, alpha * value + (1 - alpha) * mean
        err = value - prev
        var = alpha * err * err + (1 - alpha) * var
    return mean, math.sqrt(var)


def baseline(pairs, alpha):
    """From [(ts, value)]: {"mean", "sigma", "count", "updated"}. Raises ValueError with a sentence for bad input."""
    if not (isinstance(alpha, (int, float)) and not isinstance(alpha, bool) and 0 < alpha <= 1):
        raise ValueError("alpha must be greater than 0 and at most 1")
    if len(pairs) < 2:
        raise ValueError(f"a baseline needs at least 2 samples; history has {len(pairs)}")
    values, updated = [], None
    for ts, value in pairs:
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)):
            raise ValueError("history holds a value that is not a finite number")
        values.append(float(value))
        updated = ts if updated is None or ts > updated else updated
    mean, sigma = ewma(values, alpha)
    return {"mean": mean, "sigma": sigma, "count": len(values), "updated": updated}


def _weekly_seasonality(ts, residuals):
    """Average detrended residual per weekday of the bucket start, for daily buckets only. Empty when too thin."""
    by_day = {}
    for t, r in zip(ts, residuals):
        by_day.setdefault(int(t // 86400) % 7, []).append(r)
    return {day: sum(rs) / len(rs) for day, rs in by_day.items() if len(rs) >= 2}


def forecast(pairs, ahead, *, min_samples=6):
    """Forecast `ahead` steps past the last pair: {"items": [{ts, value, low, high}], "note", "sigma"}.

    Linear least squares on (t, value):
        slope     = sum((t - mean(t)) * (v - mean(v))) / sum((t - mean(t))^2)
        intercept = mean(v) - slope * mean(t)
    The half width is 1.96 * sigma * sqrt(1 + 1/n + (t0 - mean(t))^2 / sum((t - mean(t))^2)), the usual
    regression prediction interval: it widens as the forecast reaches further from the data. For a daily bucket
    the series' own weekday averages are added to the trend. When none of that is honest, items is empty and
    note says why rather than inventing a number.
    """
    if not (isinstance(ahead, int) and not isinstance(ahead, bool) and ahead >= 1):
        raise ValueError("ahead must be a whole number of steps, 1 or more")
    pts = sorted((t, v) for t, v in pairs)  # history is normally ordered; sorting makes the fit independent of it
    n = len(pts)
    out = lambda note, sigma=None: {"items": [], "note": note, "sigma": sigma}
    if n < min_samples:
        return out(f"a forecast needs at least {min_samples} samples; history has {n}")
    if any(not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)) for _, v in pts):
        return out("history holds a value that is not a finite number, so there is no forecast")
    ts, vs = [float(t) for t, _ in pts], [float(v) for _, v in pts]
    if len(set(vs)) == 1:
        return out("history is flat: every sample has the same value, so there is no trend to forecast")
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    if not gaps:
        return out("the samples share one timestamp, so there is no trend to forecast")
    gaps.sort()
    step = gaps[len(gaps) // 2]

    tbar, vbar = sum(ts) / n, sum(vs) / n
    sxx = sum((t - tbar) ** 2 for t in ts)
    slope = sum((t - tbar) * (v - vbar) for t, v in zip(ts, vs)) / sxx if sxx else float("nan")
    if not (math.isfinite(slope) and math.isfinite(sxx)) or sxx == 0:
        return out("the trend cannot be fitted from these samples")
    intercept = vbar - slope * tbar
    residuals = [v - (intercept + slope * t) for t, v in zip(ts, vs)]
    sigma = math.sqrt(sum(r * r for r in residuals) / (n - 2)) if n > 2 else 0.0
    if not math.isfinite(sigma):
        return out("the spread of the samples cannot be measured, so there is no forecast")

    season = _weekly_seasonality(ts, residuals) if abs(step - 86400) < 1 else {}  # a daily bucket has a weekday shape
    last = ts[-1]
    items = []
    for i in range(1, ahead + 1):
        t0 = last + step * i
        value = intercept + slope * t0 + season.get(int(t0 // 86400) % 7, 0.0)
        half = 1.96 * sigma * math.sqrt(1 + 1 / n + (t0 - tbar) ** 2 / sxx)
        items.append({"ts": t0, "value": value, "low": value - half, "high": value + half})
    return {"items": items, "note": None, "sigma": sigma}


def score(pairs, value):
    """Robust z of `value` against the window in pairs: {"z", "baseline", "samples"}, or None.

    This is the history-side twin of core.Anomaly: the same absolute-z verdict, z = |value - centre| / scale,
    judged against the window the sample leaves. Anomaly uses mean and population sd; here the centre is the
    median and the scale is 1.4826 * MAD (median absolute deviation), so a few spikes cannot drag the baseline
    toward themselves. As in Anomaly, fewer than three samples or a scale of zero means no verdict, not a
    division by zero.
    """
    vals = [float(v) for _, v in pairs]
    n = len(vals)
    if n < 3 or not (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)):
        return None
    ordered = sorted(vals)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    devs = sorted(abs(v - median) for v in vals)
    mad = devs[mid] if n % 2 else (devs[mid - 1] + devs[mid]) / 2
    if mad == 0:
        return None
    return {"z": abs(value - median) / (1.4826 * mad), "baseline": median, "samples": n}
