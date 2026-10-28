"""File source: a local text file of name=value / name,value lines polled as points. Run: python tests/test_file_source.py"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from _support import new_store  # noqa: E402

tmp = Path(tempfile.mkdtemp())
app = App(new_store())
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


put = lambda kind, name, body: call("PUT", f"/api/config/{kind}/{name}", body)
props = lambda r: {e["property"] for e in r[1].get("errors", [])}
src_of = lambda n: next(s for s in app.engine.sources if s.name == n)
last = lambda: {p["name"]: p["last"] for p in call("GET", "/api/points")[1]}

# ---- config
f = tmp / "in.txt"
f.write_text("temp=21.5\npressure, 3\nlabel=hello world\nbad=nan\ninf=inf\njunk line\n# comment\n\n", encoding="utf-8")
assert put("source", "f", {"type": "file", "path": str(f), "period": 60})[0] == 200
assert "path" in props(put("source", "g", {"type": "file"})), "path is required"
assert "path" in props(put("source", "g", {"type": "file", "path": 5}))
assert "format" in props(put("source", "g", {"type": "file", "path": str(f), "format": "xml"}))
assert put("source", "g", {"type": "file", "path": str(f), "format": "lines"})[0] == 200
assert call("DELETE", "/api/config/source/g")[0] == 200

# ---- reading
for n, extra in (("temp", {}), ("pressure", {}), ("label", {"datatype": "text"}), ("bad", {}), ("inf", {}), ("absent", {})):
    assert put("point", n, {"source": "f", **extra})[0] == 200, n
s = src_of("f"); time.sleep(0.3); s.cycle(); app.tick(time.time() + 1)
v = last()
assert v["temp"] == 21.5 and v["pressure"] == 3.0 and v["label"] == "hello world", v
assert v["bad"] is None and v["inf"] is None and v["absent"] is None, v  # non-finite and missing lines are not values
assert "rejected" in s.error and "bad" in s.error, s.error

# ---- missing file: source down, in words, no path or class name
f.unlink()
s.next_try = 0; s.cycle()
assert s.connected is False and s.error and str(tmp) not in s.error and "Error" not in s.error, s.error

# ---- too big a file is refused, not read
f.write_text("temp=1\n" + "x=1\n" * 400000, encoding="utf-8")
s.next_try = 0; s.cycle()
assert s.connected is False and "too large" in s.error, s.error
print("ok")
