"""System metrics source: the host's own vitals as ordinary points. Run: python tests/test_system_source.py

The contract this pins for the future implementation:
- a source of type "system" is a known source type, and a non-text "path" option is refused by name;
- one engine tick publishes GOOD numeric points about this process: free bytes on the disk and this
  process's own CPU seconds, with cpu count present only when the platform reports one;
- the point names carry the "sys-" prefix, so an operator can tell the server's vitals from plant data;
- every tick advances last_ts and the values land in history.
Windows-safe by design: everything asserted here exists on every platform (shutil.disk_usage,
time.process_time, os.cpu_count). os.getloadavg and /proc memory are POSIX-only and deliberately
not part of this contract.
"""
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from _support import new_store  # noqa: E402

PREFIX = "sys-"  # the repo's demo points are lowercase kebab-case (boiler-temp); these are "sys-<what>"
DISK, CPU, CPUS = PREFIX + "disk-free-bytes", PREFIX + "process-cpu-seconds", PREFIX + "cpu-count"

app = App(new_store())
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


put = lambda name, body: call("PUT", f"/api/config/source/{name}", body)
props = lambda r: {e["property"] for e in r[1].get("errors", [])}
views = lambda: {p["name"]: p for p in call("GET", "/api/points")[1]}

# ---- config: "system" is an accepted source type today's SOURCE_TYPES does not know
r = put("sys", {"type": "system"})
assert r[0] == 200, f"a system source must be accepted, got {r}"
# path is the optional directory to measure; anything but text must be refused with the property named
r = put("sys", {"type": "system", "path": 17})
assert r[0] == 422 and "path" in props(r), f"a non-text path must be refused naming 'path', got {r}"
assert put("sys", {"type": "system"})[0] == 200  # restore the valid config the engine test runs on

# ---- one tick is enough to publish the vitals, with no point config written by hand
src = next(s for s in app.engine.sources if s.name == "sys")
assert src.meta.get("type") == "system", src.meta
t1 = time.time()
app.tick(t1)
first = views()

for name in (DISK, CPU):  # the two metrics that need no platform-specific seam
    assert name in first, f"a system source must publish {name}, got {sorted(first)}"
    v = first[name]
    assert v["source"] == "sys", v
    assert v["quality"] == "good", f"{name} is {v['quality']}, not good, right after a tick: {v}"
    assert isinstance(v["last"], (int, float)) and not isinstance(v["last"], bool) and math.isfinite(v["last"]), v
assert first[DISK]["last"] > 0, f"free disk bytes must be positive, got {first[DISK]['last']}"
assert first[CPU]["last"] >= 0, f"process CPU seconds cannot be negative, got {first[CPU]['last']}"

if os.cpu_count() is not None:  # present only where the platform reports a count
    assert CPUS in first, f"this platform reports a CPU count, so {CPUS} must be published"
    count = first[CPUS]["last"]
    assert isinstance(count, (int, float)) and not isinstance(count, bool) and math.isfinite(count) and count >= 1, count

# ---- a second tick advances last_ts and fills history (point cache and the stored series)
t2 = t1 + 1.0
app.tick(t2)
second = views()
for name in (DISK, CPU):
    assert second[name]["last_ts"] > first[name]["last_ts"], f"{name} last_ts did not advance: {first[name]['last_ts']} -> {second[name]['last_ts']}"
    assert second[name]["quality"] == "good", f"{name} went {second[name]['quality']} on the second tick"
    assert second[name]["history"], f"{name} logged no history in /api/points: {second[name]}"
    items = call("GET", "/api/series", point=name, window=3600, bucket=1)[1]["items"]
    assert items, f"{name} has no stored history rows"
print("ok")
