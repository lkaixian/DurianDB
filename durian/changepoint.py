"""Automatic change-point detection: where did the level of a series shift, without a target or fixed windows.

Stdlib only. Binary segmentation, best split first: a segment is split where one straight-line fit per side
beats one fit for the whole, and the split is kept only when the SSE saved exceeds a penalty of
4 * ln(n) * sigma^2. Lines (not means) per side mean a steady ramp is a trend and costs nothing, while a step
still does. sigma is a robust MAD estimate on first differences, so a few shifts do not inflate it.
"""
import heapq
import math
import statistics

MAX_N = 5000  # the caller buckets longer history down; beyond this the answer is refused, not slow
MAX_CHANGES = 10


def _sigma(v):
    d = [b - a for a, b in zip(v, v[1:])]
    med = statistics.median(d)
    s = 1.4826 * statistics.median(abs(x - med) for x in d) / math.sqrt(2)
    return max(s, 1e-6 * (max(v) - min(v)) + 1e-12)  # noiseless data: a floor, never a zero penalty


def detect(values, min_seg=5, penalty=None):
    """Mean-shift change points in `values`: [{index, before_mean, after_mean, shift, strength}] by index.

    `index` is the first sample of the new level. `strength` is the SSE saved divided by the penalty, so
    anything returned is above 1. `penalty` overrides the default (an absolute SSE). Raises ValueError past MAX_N.
    """
    n = len(values)
    if n > MAX_N:
        raise ValueError(f"at most {MAX_N} samples, got {n}")
    if n < 2 * min_seg or n < 4:
        return []
    mu = sum(values) / n
    y = [x - mu for x in values]
    if penalty is None:
        penalty = 4 * math.log(n) * _sigma(y) ** 2
    # prefix sums of 1, x, y, xx, xy, yy so any segment's line-fit SSE is O(1)
    P = [(0.0,) * 5]
    for i, v in enumerate(y):
        s = P[-1]
        P.append((s[0] + i, s[1] + v, s[2] + i * i, s[3] + i * v, s[4] + v * v))

    def sse(a, b):
        m = b - a
        sx, sy, sxx, sxy, syy = (P[b][k] - P[a][k] for k in range(5))
        vx = sxx - sx * sx / m
        return max(0.0, syy - sy * sy / m - ((sxy - sx * sy / m) ** 2 / vx if vx > 0 else 0.0))

    def best(a, b):
        """(gain, k) of the best split of [a, b), or None when no side can reach min_seg."""
        if b - a < 2 * min_seg:
            return None
        whole = sse(a, b)
        g, k = max((whole - sse(a, k) - sse(k, b), k) for k in range(a + min_seg, b - min_seg + 1))
        return (-g, k, a, b)

    heap, cuts = [], []
    first = best(0, n)
    if first:
        heap.append(first)
    while heap and len(cuts) < MAX_CHANGES:
        ng, k, a, b = heapq.heappop(heap)
        if -ng <= penalty:
            break  # best-first: nothing left can clear the penalty
        cuts.append((k, -ng))
        for lo, hi in ((a, k), (k, b)):
            c = best(lo, hi)
            if c:
                heapq.heappush(heap, c)
    cuts.sort()
    edges = [0] + [k for k, _ in cuts] + [n]
    means = [sum(values[a:b]) / (b - a) for a, b in zip(edges, edges[1:])]
    return [{"index": k, "before_mean": means[i], "after_mean": means[i + 1], "shift": means[i + 1] - means[i],
             "strength": g / penalty} for i, (k, g) in enumerate(cuts)]
