"""Calculated points. Run: python tests/test_calc.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.calc import evaluate, parse, refs  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

VALUES = {"a": 4.0, "b-2": 10.0, "mode": 1.0}


def read(name):
    if name not in VALUES:
        raise LookupError(name)
    return VALUES[name]


def ev(expr):
    return evaluate(parse(expr), read)


# ---- arithmetic, functions, logic
assert ev('p("a") * 1.8 + 32') == 39.2 and ev('p("b-2") % 3') == 1.0 and ev("-(2 + 3) * 2") == -10.0
assert ev('max(p("a"), p("b-2")) - min(p("a"), 1)') == 9.0 and ev('sqrt(p("a")) + abs(-1)') == 3.0 and ev("round(2.6)") == 3.0
assert ev('1 if p("mode") > 0 and p("a") < 5 else 0') == 1.0 and ev('p("a") > 5 or p("a") == 4') == 1.0 and ev('not p("mode")') == 0.0
assert ev('1 < p("a") < 5') == 1.0 and ev('1 < p("a") < 4') == 0.0
assert refs(parse('p("a") + p("b-2") * p("a")')) == ["a", "b-2"]

# ---- what must be refused
for bad in ("", "x" * 501, "1 +", "p(1)", 'p("a", "b")', "a + 1", "__import__('os')", 'p("a").real', "2 ** 3", "[1]", "lambda: 1", "'text'.upper()",
            'abs(x=1)', "True", "None", 'p(*["a"])', "(1).__class__", "(yield)"):
    try:
        parse(bad)
        raise AssertionError(bad)
    except ValueError:
        pass
for no_result in ("1 / 0", 'p("ghost")', "sqrt(-1)", '"a"', "1e308 * 10"):
    try:
        evaluate(parse(no_result), read)
        raise AssertionError(no_result)
    except (ArithmeticError, ValueError, LookupError):
        pass

# ---- through the app: ordering, quality, validation
store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None):
    try:
        return app.handle(method, path, {}, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


assert call("PUT", "/api/config/point/fixed", {"source": "demo", "generator": {"kind": "constant", "value": 10}})[0] == 200
assert call("PUT", "/api/config/source/calcs", {"type": "calc"})[0] == 200
assert call("PUT", "/api/config/point/double", {"source": "calcs", "calc": {"expression": 'p("fixed") * 2'}, "unit": "x"})[0] == 200
assert call("PUT", "/api/config/point/ghosted", {"source": "calcs", "calc": {"expression": 'p("nothing") + 1'}})[0] == 200
assert call("PUT", "/api/config/point/hotcalc", {"source": "calcs", "calc": {"expression": 'p("fixed") * 20'},
                                                 "detectors": [{"kind": "HIGH_LIMIT", "limit": 100}]})[0] == 200  # calculated points alarm like any other
for body in ({"source": "calcs"}, {"source": "calcs", "calc": {"expression": "1 +"}}, {"source": "calcs", "calc": {"expression": 'p("selfref")'}},
             {"source": "calcs", "calc": {"expression": "1"}, "settable": True}):
    assert call("PUT", "/api/config/point/selfref", body)[0] == 422, body
now = time.time()
app.tick(now)  # one tick: the calculation already sees fixed's value from this same tick
pts = {p["name"]: p for p in call("GET", "/api/points")[1]}
assert pts["double"]["last"] == 20.0 and pts["double"]["quality"] == "good"
assert pts["ghosted"]["last"] is None and pts["ghosted"]["quality"] == "bad"
assert "ghosted: nothing has no good value" in next(s for s in call("GET", "/api/sources")[1] if s["name"] == "calcs")["error"]
assert any(e["point"] == "hotcalc" for e in call("GET", "/api/events")[1])
print("ok")
