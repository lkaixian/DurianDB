"""Historical backfill at ingest: an explicit old timestamp fills a history gap without moving the point's
`last` backwards, and the live state (last value, ON_CHANGE/INTERVAL logging, detectors) stays on the newest
sample, not on the arrival instant. Run: python tests/test_backfill.py

The trust boundary stays: absurdly old, non-numeric and over-cap batches are refused exactly as the source's
existing validation refuses them, and a refused entry writes no partial row.
"""
import csv
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(who, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, who)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


def put(kind, name, body):
    s, r = call(admin, "PUT", f"/api/config/{kind}/{name}", body)
    assert s == 200, ("config", kind, name, s, r)
    return r


def view(point):
    s, r = call(admin, "GET", "/api/points/" + point)
    assert s == 200, (point, s, r)
    return r


def hist(point, start, end):
    """Exact rows for a window, in time order: (ts, value, quality, svalue). Bad rows are excluded by the
    same rule every history read uses; the backfilled rows below are good or uncertain."""
    return [(r["ts"], r["value"], r["quality"], r["svalue"])
            for r in store.history_range(point, start, end, 20_000)]


# One http_in source and every point first: a config PUT reloads the engine, so no config change after this.
put("source", "edge", {"type": "http_in", "read_roles": ["user"]})
put("point", "bk", {"source": "edge"})
put("point", "bk-cur", {"source": "edge"})
put("point", "bk-txt", {"source": "edge", "datatype": "text"})
put("point", "bk-nc", {"source": "edge", "detectors": [{"kind": "NO_CHANGE", "seconds": 600}]})
put("point", "bk-iv", {"source": "edge", "logging": "INTERVAL", "log_interval": 10, "log_mode": "avg"})
put("point", "bk-nev", {"source": "edge", "logging": "NEVER"})
put("point", "bk-tr", {"source": "edge"})
tok = app.auth.token_identity(
    call(admin, "POST", "/api/tokens", {"name": "edge-in", "scope": "ingest", "source": "edge"})[1]["token"])


def ing(values, method="POST"):
    return call(tok, method, "/api/ingest/edge", {"values": values})


now = time.time()
app.tick(now)  # poll once so every point starts with no last

# ---- 1. an explicit old timestamp against a point that already holds a newer value is backfill, not a refusal
s, r = ing([{"point": "bk-cur", "value": 10, "ts": now - 10}])
assert s == 200 and r["accepted"] == 1, ("the current sample is accepted", s, r)
app.tick(now)
v = view("bk-cur")
assert (v["last"], v["last_ts"]) == (10.0, now - 10), ("the current sample is the point's last", v)

s, r = ing([{"point": "bk-cur", "value": 11, "ts": now - 600}])
assert s == 200 and r["accepted"] == 1 and r["rejected"] == [], (
    "an explicit old timestamp is a backfill entry, not an out-of-order refusal", s, r)
app.tick(now)
assert hist("bk-cur", 0, now + 10) == [(now - 600, 11.0, 0, None), (now - 10, 10.0, 0, None)], (
    "the backfilled row is in history with its exact source timestamp and value", hist("bk-cur", 0, now + 10))
v = view("bk-cur")
assert (v["last"], v["last_ts"]) == (10.0, now - 10), ("backfill must not drag last backwards", v)

# ---- 2. an out-of-order batch: every entry lands, and `last` is the newest timestamp in the batch, not the
# last one in the body. now-900 arrives after now-100, so the old high-water rule would refuse it.
s, r = ing([{"point": "bk", "value": 1, "ts": now - 500},
            {"point": "bk", "value": 2, "ts": now - 400, "quality": "uncertain"},
            {"point": "bk", "value": 3, "ts": now - 100},
            {"point": "bk", "value": 4, "ts": now - 900}])
assert s == 200 and r["accepted"] == 4 and r["rejected"] == [], (
    "an out-of-order batch is backfill: every entry is accepted", s, r)

app.tick(now)
assert hist("bk", 0, now + 10) == [
    (now - 900, 4.0, 0, None), (now - 500, 1.0, 0, None),
    (now - 400, 2.0, 1, None), (now - 100, 3.0, 0, None),
], ("every backfilled entry is a history row with its exact time and quality; `uncertain` is kept", hist("bk", 0, now + 10))
v = view("bk")
assert (v["last"], v["last_ts"]) == (3.0, now - 100), ("last is the newest-timestamp entry, not the last in the body", v)
assert v["quality"] == "good", ("the newest sample decides the live quality", v)

# the same rows through the history API: there is no /api/history route; the exact-row read is
# GET /api/export/history (test_app and test_iso_csv pin it), which streams the same store.history_range rows
s, raw = call(admin, "GET", "/api/export/history", point="bk",
              **{"from": "0", "to": str(now + 10)})
assert s == 200, ("history API", s, raw)
rows = list(csv.reader(io.StringIO(raw.data.decode())))
assert [row[2] for row in rows[1:]] == ["4.0", "1.0", "2.0", "3.0"], (
    "the history API serves the backfilled rows in time order", rows)

# ---- 3. a purely-older batch leaves `last` untouched while still filling history
s, r = ing([{"point": "bk", "value": 5, "ts": now - 800},
            {"point": "bk", "value": 6, "ts": now - 700}])
assert s == 200 and r["accepted"] == 2, ("a purely-older batch is still backfill", s, r)
app.tick(now)
v = view("bk")
assert (v["last"], v["last_ts"]) == (3.0, now - 100), ("a purely-older batch does not move last", v)
assert [row[1] for row in hist("bk", 0, now + 10)] == [4.0, 5.0, 6.0, 1.0, 2.0, 3.0], (
    "the older batch fills the gap between now-900 and now-500",
    hist("bk", 0, now + 10))

# ---- 4. text points backfill the same way the module already treats them on ingest (text is accepted on a
# text point, rejected on a numeric one): the value goes to svalue, `last` stays on the newest text
s, r = ing([{"point": "bk-txt", "value": "RUN", "ts": now - 50},
            {"point": "bk-txt", "value": "STOP", "ts": now - 450}])
assert s == 200 and r["accepted"] == 2, ("text backfill is accepted like any text ingest", s, r)
app.tick(now)
rows = hist("bk-txt", 0, now + 10)
assert [(row[0], row[3]) for row in rows] == [(now - 450, "STOP"), (now - 50, "RUN")] and all(row[1] is None for row in rows), (
    "text backfill lands in svalue and `last` is the newest text", rows)
v = view("bk-txt")
assert (v["last"], v["last_ts"]) == ("RUN", now - 50), ("text last is the newest-timestamp text", v)

# ---- 5. trust boundary: what the source already refuses, it keeps refusing, and a refused entry leaves no row
# ts null means "now" today (documented in durian/ingest.py and pinned in tests/test_ingest.py); pin that it
# still means now and never becomes an epoch-0 row
s, r = ing([{"point": "bk-tr", "value": 1, "ts": None}])
assert s == 200 and r["accepted"] == 1, ("a null ts is the documented 'now'", s, r)
app.tick(now)
rows = hist("bk-tr", 0, now + 3600)
assert len(rows) == 1 and rows[0][1] == 1.0 and rows[0][0] >= now - 5, (
    "a null ts is stamped now, never epoch 0", rows)

for bad_ts, label in ((0, "1970"), ("1900-01-01T00:00:00Z", "1900")):
    s, r = ing([{"point": "bk-tr", "value": 2, "ts": bad_ts}])
    assert s == 422 and r["accepted"] == 0, (label, "an absurdly old date is refused", s, r)
    assert "older than this source accepts" in r["rejected"][0]["reason"], (
        label, "the refusal names the source's max age", r)
    assert not [row for row in hist("bk-tr", 0, now + 3600) if row[0] < now - 86400], (
        label, "a refused absurd date writes no row", hist("bk-tr", 0, now + 3600))

s, r = ing([{"point": "bk-tr", "value": 424242, "ts": "yesterday"}])
assert s == 422 and "ts must be epoch seconds or an ISO-8601 text" in r["rejected"][0]["reason"], (
    "a non-numeric timestamp is refused with the property named", s, r)
assert not any(row[1] == 424242.0 for row in hist("bk-tr", 0, now + 3600)), (
    "a refused timestamp writes no partial row", hist("bk-tr", 0, now + 3600))

# one bad entry in a batch is refused by index; the good entry still lands and the bad one leaves no row
s, r = ing([{"point": "bk-tr", "value": 7, "ts": now + 30},
            {"point": "bk-tr", "value": 424243, "ts": "yesterday"}])
assert s == 200 and r["accepted"] == 1 and r["rejected"][0]["index"] == 1, (
    "a mixed batch refuses the bad entry by index", s, r)
assert not any(row[1] == 424243.0 for row in hist("bk-tr", 0, now + 3600)), (
    "the refused entry writes no partial row", hist("bk-tr", 0, now + 3600))

# the batch-size cap is checked in parse_payload before anything is queued: 1001 entries is a 400, not a
# partial write of the entries that happened to validate
before = hist("bk-tr", 0, now + 3600)
s, r = ing([{"point": "bk-tr", "value": i, "ts": now + 40} for i in range(1001)])
assert s == 400 and "between 1 and 1000" in r["error"], (
    "a batch past MAX_BATCH is refused whole, with the cap named", s, r)
assert hist("bk-tr", 0, now + 3600) == before, ("the over-cap batch writes no partial rows", hist("bk-tr", 0, now + 3600))

# ---- 6. live evaluation ignores backfill: current `last`, ON_CHANGE/INTERVAL logging and the NO_CHANGE
# detector stay on the newest sample, not on the old values arriving now
s, r = ing([{"point": "bk-nc", "value": 5, "ts": now - 10}])
assert s == 200 and r["accepted"] == 1, ("the live sample", s, r)
s, r = ing([{"point": "bk-nc", "value": 6, "ts": now - 500},
            {"point": "bk-nc", "value": 4, "ts": now - 900}])
assert s == 200 and r["accepted"] == 2, ("the detector point's backfill", s, r)
app.tick(now)
v = view("bk-nc")
assert (v["last"], v["last_ts"]) == (5.0, now - 10), ("backfill does not become the current value", v)
assert hist("bk-nc", 0, now + 10) == [
    (now - 900, 4.0, 0, None), (now - 500, 6.0, 0, None), (now - 10, 5.0, 0, None),
], ("ON_CHANGE logs every backfilled value and the live value once", hist("bk-nc", 0, now + 10))
nc = next(d for d in app.points()["bk-nc"][0].detectors if d.kind == "NO_CHANGE")
assert (nc.ref, nc.changed, nc.event) == (5.0, now - 10, None), (
    "backfill does not move the no-change reference or its clock", (nc.ref, nc.changed, nc.event))
assert not any(e["point"] == "bk-nc" for e in store.events()), (
    "old values do not fake a frozen value now", store.events())

# an INTERVAL point must not fold backfill into the current period: the period is the live samples only
s, r = ing([{"point": "bk-iv", "value": 10, "ts": now - 20}])
assert s == 200 and r["accepted"] == 1, ("the interval point's first live sample", s, r)
s, r = ing([{"point": "bk-iv", "value": 100, "ts": now - 500},
            {"point": "bk-iv", "value": 200, "ts": now - 400}])
assert s == 200 and r["accepted"] == 2, ("the interval point's backfill", s, r)
s, r = ing([{"point": "bk-iv", "value": 20, "ts": now - 10}])
assert s == 200 and r["accepted"] == 1, ("the interval closes on the live sample", s, r)
app.tick(now)
assert hist("bk-iv", 0, now + 10) == [
    (now - 500, 100.0, 0, None), (now - 400, 200.0, 0, None), (now - 10, 15.0, 0, None),
], ("backfill lands raw while the interval row averages only the live samples (10+20)/2", hist("bk-iv", 0, now + 10))
v = view("bk-iv")
assert (v["last"], v["last_ts"]) == (20.0, now - 10), ("the interval point's last is the newest live sample", v)

# a NEVER point logs no row, live or backfilled: the owner's policy is not breakable from the network
s, r = ing([{"point": "bk-nev", "value": 1, "ts": now - 10}])
assert s == 200 and r["accepted"] == 1, ("the never point's live sample is accepted", s, r)
app.tick(now)
s, r = ing([{"point": "bk-nev", "value": 2, "ts": now - 500}])
assert s == 200 and r["accepted"] == 1, ("an explicit old timestamp on a never point is accepted", s, r)
app.tick(now)
assert hist("bk-nev", 0, now + 10) == [], (
    "a never-logging point keeps no row, live or backfilled", hist("bk-nev", 0, now + 10))

print("ok")
