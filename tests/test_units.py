"""Unit conversion, display units, and tags/units inherited from a folder then a source. Run: python tests/test_units.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import units  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.core import Quality  # noqa: E402
from durian.config import validate_source  # noqa: E402
from _support import new_store  # noqa: E402

# ---- the conversion table: offsets matter
assert abs(units.convert(100, "C", "F") - 212) < 1e-9 and abs(units.convert(0, "C", "F") - 32) < 1e-9
assert abs(units.convert(300, "K", "C") - 26.85) < 1e-9 and units.convert(0, "C", "K") == 273.15
assert abs(units.convert(32, "F", "C")) < 1e-9
# ---- same dimension, and different dimensions refused
assert abs(units.convert(1, "bar", "kPa") - 100) < 1e-9 and abs(units.convert(1, "m", "ft") - 1 / 0.3048) < 1e-9
assert units.convert(50, "%", "0-1") == 0.5 and units.convert(10, "m", "m") == 10
assert units.compatible("C", "F") and units.compatible("L/s", "gpm") and not units.compatible("C", "psi")
for bad in (("C", "psi"), ("banana", "C"), ("C", "banana")):
    try:
        units.convert(1, *bad)
        raise AssertionError(bad)
    except ValueError as e:
        assert str(e).strip(), e
try:
    units.convert(1, "C", "psi")
except ValueError as e:
    assert "different kinds of quantity" in str(e), e
assert units.format(1.234) == "1.23" and units.format(212.0, "F", True) == "212 F"

# ---- a source defaults tags and a unit; a folder overrides them; the point wins
source = {"type": "virtual", "enabled": True, "default_unit": "C",
          "default_tags": {"site": "source", "area": "source", "extra": "source"},
          "folders": {"Plant/Boiler": {"unit": "F", "tags": {"area": "folder", "zone": "north"}}}}
assert validate_source("s", source) == []

store = new_store()
store.put("source", "s", source)
store.put("source", "bare", {"type": "virtual", "enabled": True})
store.put("point", "inherits", {"source": "s", "folder": "Plant/Boiler", "tags": {"area": "point", "note": "own"},
                                "generator": {"kind": "constant", "value": 100}})
store.put("point", "source-unit", {"source": "s", "generator": {"kind": "constant", "value": 10}})
store.put("point", "own-unit", {"source": "s", "unit": "K", "generator": {"kind": "constant", "value": 300}})
store.put("point", "shown", {"source": "s", "unit": "C", "display_unit": "F", "generator": {"kind": "constant", "value": 100}})
store.put("point", "no-show", {"source": "s", "unit": "C", "generator": {"kind": "constant", "value": 20}})
store.put("point", "never", {"source": "s", "unit": "C", "display_unit": "F", "generator": {"kind": "constant", "value": 30}})
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


# ---- before any poll: no value, so no display value even though a display unit is set
v = call("GET", "/api/value/never")[1]
assert v["value"] is None and v["unit"] == "C" and v["display_unit"] == "F" and v["display_value"] is None, v

app.tick(1000.0)
pts = {p["name"]: p for p in call("GET", "/api/points")[1]}

# point > folder > source for every tag key; folder > source for the inherited unit
assert pts["inherits"]["tags"] == {"site": "source", "extra": "source", "area": "point", "zone": "north", "note": "own"}, pts["inherits"]["tags"]
assert pts["inherits"]["unit"] == "F" and pts["source-unit"]["unit"] == "C" and pts["own-unit"]["unit"] == "K"
assert {p["name"] for p in call("GET", "/api/points", tag="zone:north")[1]} == {"inherits"}
assert {p["name"] for p in call("GET", "/api/points", tag="area:source")[1]} == {"source-unit", "own-unit", "shown", "no-show", "never"}

# ---- display unit and value, on the list and on the single-value endpoint
assert abs(pts["shown"]["display_value"] - 212) < 1e-9 and pts["shown"]["display_unit"] == "F"
assert pts["no-show"]["display_unit"] == "" and pts["no-show"]["display_value"] is None
v = call("GET", "/api/value/shown")[1]
assert abs(v["display_value"] - 212) < 1e-9 and v["display_unit"] == "F" and v["unit"] == "C" and v["tags"]["site"] == "source"

# ---- stale and BAD held values are not offered as a display value
shown = app.points()["shown"][0]
shown.update(100.0, 1001.0, lambda ev: None, Quality.UNCERTAIN)
v = call("GET", "/api/value/shown")[1]
assert v["quality"] == "uncertain" and v["value"] == 100.0 and v["display_value"] is None, v
shown.update(None, 1002.0, lambda ev: None, Quality.BAD)
v = call("GET", "/api/value/shown")[1]
assert v["quality"] == "bad" and v["value"] == 100.0 and v["display_value"] is None, v

# ---- a display unit must be known, compatible, and only on a point that has a unit
s, r = call("PUT", "/api/config/point/garbage", {"source": "s", "unit": "C", "display_unit": "banana",
                                                 "generator": {"kind": "constant", "value": 1}})
assert s == 422 and any(e["property"] == "display_unit" for e in r["errors"]), r
s, r = call("PUT", "/api/config/point/garbage", {"source": "s", "unit": "C", "display_unit": "psi",
                                                 "generator": {"kind": "constant", "value": 1}})
assert s == 422 and any(e["property"] == "display_unit" for e in r["errors"]), r
s, r = call("PUT", "/api/config/point/garbage", {"source": "bare", "display_unit": "F",
                                                 "generator": {"kind": "constant", "value": 1}})
assert s == 422 and any(e["property"] == "display_unit" and "set unit first" in e["message"] for e in r["errors"]), r
assert call("PUT", "/api/config/point/good", {"source": "s", "unit": "C", "display_unit": "F",
                                              "generator": {"kind": "constant", "value": 1}})[0] == 200
print("ok")
