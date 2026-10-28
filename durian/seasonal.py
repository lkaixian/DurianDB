"""Seasonal baseline: a robust expected value per hour-of-week, and a z-score of a value against its own slot.

Stdlib only, no stored model: the profile is rebuilt from the (ts, value) pairs handed in. Slots are 168
(Monday 00:00 is dow 0, hour 0) in the clock of `tz_offset_s` seconds east of UTC; the routes pass the
server's current local offset, so a daylight-saving change inside the window shifts old samples by an hour
(ponytail: fixed offset, per-sample zoneinfo if that ever matters). A slot with fewer than MIN_SLOT_SAMPLES
samples is absent, never guessed. Centre is the median, scale is 1.4826 * MAD, as in forecast.score().
"""
import math

MIN_SLOT_SAMPLES = 3
UNUSUAL_Z = 3.0  # the same absolute-z verdict as the ANOMALY detector and forecast.drift


def slot_of(ts, tz_offset_s=0):
    """(dow, hour) of a timestamp; dow 0 is Monday (the epoch began on a Thursday)."""
    t = int(ts + tz_offset_s)
    return (t // 86400 + 3) % 7, t // 3600 % 24


def _median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def profile(pairs, tz_offset_s=0):
    """{(dow, hour): {"median", "mad", "n"}} for every slot with at least MIN_SLOT_SAMPLES finite samples."""
    slots = {}
    for ts, value in pairs:
        if _finite(ts) and _finite(value):
            slots.setdefault(slot_of(ts, tz_offset_s), []).append(float(value))
    out = {}
    for slot, vals in slots.items():
        if len(vals) >= MIN_SLOT_SAMPLES:
            med = _median(vals)
            out[slot] = {"median": med, "mad": _median([abs(v - med) for v in vals]), "n": len(vals)}
    return out


def score_against(prof, ts, value, tz_offset_s=0):
    """{"z", "expected", "slot", "reason"} for `value` at `ts`; z is None, with a reason, when no honest score exists.

    z = |value - median| / (1.4826 * MAD) using only the value's own hour-of-week slot.
    """
    slot = slot_of(ts, tz_offset_s) if _finite(ts) else None
    out = {"z": None, "expected": None, "slot": slot, "reason": None}
    if slot is None or not _finite(value):
        out["reason"] = "the value or its time is not a finite number"
    elif slot not in prof:
        out["reason"] = f"history has fewer than {MIN_SLOT_SAMPLES} samples for this hour of the week"
    else:
        s = prof[slot]
        out["expected"] = s["median"]
        if s["mad"] == 0:
            out["reason"] = "the samples for this hour of the week are all equal, so there is no spread to judge by"
        else:
            out["z"] = abs(value - s["median"]) / (1.4826 * s["mad"])
    return out
