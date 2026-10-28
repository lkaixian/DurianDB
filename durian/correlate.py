"""Correlation between two aligned series, stdlib only. A pair with a non-finite side is dropped; fewer than
MIN_PAIRS left, or a side with no spread, gives None (an answer, never a ZeroDivisionError)."""
import math

MIN_PAIRS = 10


def _pairs(xs, ys):
    return [(x, y) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y)]


def _r(pairs):
    if len(pairs) < MIN_PAIRS:
        return None
    mx, my = sum(x for x, _ in pairs) / len(pairs), sum(y for _, y in pairs) / len(pairs)
    sxx, syy = sum((x - mx) ** 2 for x, _ in pairs), sum((y - my) ** 2 for _, y in pairs)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    if sxx <= 1e-18 * len(pairs) * mx * mx or syy <= 1e-18 * len(pairs) * my * my or sxx == 0 or syy == 0:
        return None  # no spread: a constant series correlates with nothing
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def _ranks(vs):
    order = sorted(range(len(vs)), key=vs.__getitem__)
    out, i = [0.0] * len(vs), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vs[order[j + 1]] == vs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1  # ties share their mean rank
        i = j + 1
    return out


def pearson(xs, ys):
    return _r(_pairs(xs, ys))


def spearman(xs, ys):
    pairs = _pairs(xs, ys)
    return _r(list(zip(_ranks([x for x, _ in pairs]), _ranks([y for _, y in pairs])))) if pairs else None


def best_lag(xs, ys, max_lag):
    """(lag, r at lag 0, r at lag). Lag k > 0 pairs xs[i] with ys[i + k], so y trails x by k buckets. Ties go to the smaller |lag|."""
    r0 = pearson(xs, ys)
    best = (0, r0)
    for k in sorted(range(-max_lag, max_lag + 1), key=abs):
        r = _r(_pairs(xs[:len(xs) - k], ys[k:]) if k >= 0 else _pairs(xs[-k:], ys[:len(ys) + k]))
        if r is not None and (best[1] is None or abs(r) > abs(best[1]) + 1e-12):
            best = (k, r)
    return (None, None, None) if r0 is None and best[1] is None else (best[0], r0, best[1])
