"""The prediction widget: validation, save/load round-trip and the forecast answer it draws. Run: python tests/test_prediction_widget.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
store.put("point", "mode", {"source": "demo", "datatype": "text", "generator": {"kind": "constant", "value": "RUN"}})
app = App(store)
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, query=None):
    try:
        return app.handle(method, path, query or {}, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


props = lambda body: {e["property"] for e in body["errors"]}
P = lambda **k: {"id": "p", "type": "prediction", "point": "boiler-temp", "x": 0, "y": 0, "w": 6, "h": 4, **k}
D = lambda *ws: {"title": "T", "widgets": list(ws)}

# ---- the defaults are enough, and the whole field set is accepted
assert call("PUT", "/api/dashboards/pred-defaults", D(P()))[0] == 200
widget = P(hours=12, bucket=120, show_band=False, title="Next 12 hours")
assert call("PUT", "/api/dashboards/pred", D(widget))[0] == 200

# ---- save and load round-trips the widget unchanged
assert call("GET", "/api/dashboards/pred")[1]["widgets"] == [widget]

# ---- an unknown point is refused under the point property
s, r = call("PUT", "/api/dashboards/pred", D(P(point="ghost")))
assert s == 422 and "widgets[0].point" in props(r), r
assert any("not configured" in e["message"] for e in r["errors"]), r

# ---- a text point has no numeric forecast: refused under the point property
s, r = call("PUT", "/api/dashboards/pred", D(P(point="mode")))
assert s == 422 and "widgets[0].point" in props(r), r
assert any("text" in e["message"] and "forecast" in e["message"] for e in r["errors"]), r

# ---- hours and bucket stay inside the interval the chart and the endpoint can draw
for bad in (0, 49, -1, True, 6.5):
    s, r = call("PUT", "/api/dashboards/pred", D(P(hours=bad)))
    assert s == 422 and "widgets[0].hours" in props(r), (bad, r)
for bad in (9, 3601, True):
    s, r = call("PUT", "/api/dashboards/pred", D(P(bucket=bad)))
    assert s == 422 and "widgets[0].bucket" in props(r), (bad, r)
assert call("PUT", "/api/dashboards/pred", D(P(show_band="yes")))[0] == 422

# ---- the forecast answer the widget consumes: stored rows, forecast rows and their interval
now = time.time()
store.add_history([("boiler-temp", now - (39 - i) * 60.0, 10.0 + 0.5 * i) for i in range(40)])
s, r = call("GET", "/api/points/boiler-temp/forecast", query={"hours": "6", "bucket": "60"})
assert s == 200 and r["point"] == "boiler-temp" and r["unit"] == "C", r
assert r["hours"] == 6 and r["bucket"] == 60 and r["note"] is None, r
assert len(r["points"]) == 40, len(r["points"])
assert len(r["forecast"]) == 360, len(r["forecast"])
assert all(set(row) == {"ts", "value", "low", "high"} and row["low"] <= row["value"] <= row["high"] for row in r["forecast"])
assert [p["ts"] for p in r["points"]] == sorted(p["ts"] for p in r["points"])

# ---- too little history is a note and no forecast rows: the widget draws the stored line or its empty state
s, r = call("GET", "/api/points/line-pressure/forecast", query={"hours": "6", "bucket": "60"})
assert s == 200 and r["points"] == [] and r["forecast"] == [] and "at least 6" in r["note"], r

print("ok")
