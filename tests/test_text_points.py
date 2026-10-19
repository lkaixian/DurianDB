"""Alphanumeric points: validation, HTTP ingest, change logging, TEXT alarms, set, history export. Run: python tests/test_text_points.py"""
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
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, who=admin, **q):
    try:
        return app.handle(method, path, q, body, who)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


put = lambda kind, name, body: call("PUT", f"/api/config/{kind}/{name}", body)
props = lambda r: {e["property"] for e in r[1]["errors"]}

assert put("source", "plc", {"type": "http_in", "read_roles": ["user"]})[0] == 200
assert put("point", "mode", {"source": "plc", "datatype": "text", "detectors": [{"kind": "TEXT", "values": ["fault", "trip"], "mode": "contains", "level": "URGENT"}]})[0] == 200
assert put("point", "num", {"source": "plc"})[0] == 200

# ---- validation
assert "datatype" in props(put("point", "bad1", {"source": "plc", "datatype": "blob"}))
assert "logging" in props(put("point", "bad2", {"source": "plc", "datatype": "text", "logging": "INTERVAL", "log_interval": 5}))
assert put("source", "calc1", {"type": "calc"})[0] == 200 and "datatype" in props(put("point", "bad6", {"source": "calc1", "datatype": "text", "calc": {"expression": "1"}}))
assert "generator" in props(put("point", "bad7", {"source": "demo", "datatype": "text", "generator": {"kind": "sine", "amplitude": 1, "period": 5}}))

# ---- ingest: text goes to text points only
tok = call("POST", "/api/tokens", {"name": "t", "scope": "ingest", "source": "plc"})[1]["token"]
ident = app.auth.token_identity(tok)
t0 = time.time()
r = call("POST", "/api/ingest/plc", {"values": [{"point": "mode", "value": "RUN"}, {"point": "num", "value": "RUN"}, {"point": "mode", "value": 5}]}, who=ident)
assert r[0] == 200 and r[1]["accepted"] == 1 and len(r[1]["rejected"]) == 2, r
app.tick(t0 + 1)
assert call("GET", "/api/points/mode")[1]["last"] == "RUN" and call("GET", "/api/points/mode")[1]["datatype"] == "text"
assert call("GET", "/api/value/mode")[1]["value"] == "RUN"

# ---- change logging and TEXT alarm
for i, v in enumerate(["RUN", "RUN", "Overload TRIP", "RUN"]):
    call("POST", "/api/ingest/plc", {"point": "mode", "value": v, "ts": t0 + 2 + i}, who=ident)
    app.tick(t0 + 2 + i)
rows = store.history_range("mode", 0, t0 + 100, 100)
assert [r["svalue"] for r in rows] == ["RUN", "Overload TRIP", "RUN"] and all(r["value"] is None for r in rows), rows
evs = [e for e in store.events() if e["point"] == "mode"]
assert len(evs) == 1 and "TRIP" in evs[0]["message"] and evs[0]["cleared_ts"] is not None, evs

# ---- series is refused, the history export carries the text
assert call("GET", "/api/series", point="mode")[0] == 400
s, r = call("GET", "/api/export/history", point="mode", **{"from": 0, "to": t0 + 100})
assert s == 200 and b"Overload TRIP" in r.data, r

# ---- a settable virtual text point
assert put("point", "recipe", {"source": "demo", "datatype": "text", "settable": True, "set_roles": ["user"], "generator": {"kind": "constant", "value": "A"}})[0] == 200
app.reload()
app.tick(time.time())
assert call("POST", "/api/points/recipe/set", {"value": "B"})[0] == 200 and call("POST", "/api/points/recipe/set", {"value": 3})[0] == 400
app.tick(time.time() + 1)
assert call("GET", "/api/points/recipe")[1]["last"] == "B"
print("ok")
