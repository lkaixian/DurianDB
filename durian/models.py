"""Persisted per-point models: an EWMA baseline and the hour-of-week profile, trained daily and kept across restarts.

A model lives in the settings table as `model:<point>` (which the SQL console cannot read; it holds no secrets).
Reads are bounded like the seasonal route: WINDOW_DAYS * 96 rows at 900 s buckets, at most 5760.
"""
import time

from . import forecast, seasonal

WINDOW_DAYS, BUCKET, ALPHA = 14, 900, 0.3
BATCH, BUDGET_S = 25, 20.0  # points examined per scheduled call, and the seconds one call may spend


def train(store, point, now):
    """Build and store the model, or None for an unknown, text or thin-data point."""
    cfg = store.get("point", point)
    if cfg is None or cfg.get("datatype") == "text":
        return None
    tz = time.localtime(now).tm_gmtoff
    pairs = [(r["ts"], r["value"]) for r in store.series(point, now - WINDOW_DAYS * 86400, BUCKET, "avg")]
    prof = seasonal.profile(pairs, tz)
    if len(pairs) < 2 or not prof:
        return None
    b = forecast.baseline(pairs, ALPHA)
    model = {"point": point, "trained_at": now, "samples": len(pairs), "window_days": WINDOW_DAYS, "tz": tz,
             "baseline": {"mean": b["mean"], "sigma": b["sigma"]},
             "seasonal": {"slots": [[d, h, s["median"], s["mad"], s["n"]] for (d, h), s in sorted(prof.items())],
                          "coverage": len(prof) / 168}}
    store.set_setting(f"model:{point}", model)
    return model


def get(store, point):
    return store.get_setting(f"model:{point}")


def drop(store, point):
    store.run("delete from settings where skey=?", (f"model:{point}",))


def stale(model, now, max_age_s):
    return now - model["trained_at"] > max_age_s


def profile(model):
    """The stored slots back in seasonal.profile() shape."""
    return {(d, h): {"median": m, "mad": mad, "n": n} for d, h, m, mad, n in model["seasonal"]["slots"]}


def retrain_batch(store, now):
    """Train up to BATCH points after the stored cursor. Returns True when the list is finished, False to resume."""
    t0, cursor = time.monotonic(), store.get_setting("models_cursor")
    names = sorted(p["name"] for p in store.list("point") if cursor is None or p["name"] > cursor)
    for done, name in enumerate(names):
        if done and (done >= BATCH or time.monotonic() - t0 > BUDGET_S):
            store.set_setting("models_cursor", names[done - 1])
            return False
        train(store, name, now)
    store.set_setting("models_cursor", None)
    return True
