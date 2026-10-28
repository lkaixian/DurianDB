"""File source: a local text file of name=value / name,value lines polled as points. Run: python tests/test_file_source.py"""
import os
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
f.write_text("temp=21.5\npressure, 3\nlabel=hello world\nbad=nan\ninf=inf\njunk line\n# comment\n\nlong=" + "x" * 256 + "\nctl=a\x07b\nempty=\n", encoding="utf-8")
assert put("source", "f", {"type": "file", "path": str(f), "period": 60})[0] == 200
assert "path" in props(put("source", "g", {"type": "file"})), "path is required"
assert "path" in props(put("source", "g", {"type": "file", "path": 5}))
assert "format" in props(put("source", "g", {"type": "file", "path": str(f), "format": "xml"}))
assert put("source", "g", {"type": "file", "path": str(f), "format": "lines"})[0] == 200
assert call("DELETE", "/api/config/source/g")[0] == 200

# ---- reading
for n, extra in (("temp", {}), ("pressure", {}), ("label", {"datatype": "text"}), ("bad", {}), ("inf", {}), ("absent", {}),
             ("long", {"datatype": "text"}), ("ctl", {"datatype": "text"}), ("empty", {"datatype": "text"})):
    assert put("point", n, {"source": "f", **extra})[0] == 200, n
s = src_of("f"); time.sleep(0.3); s.cycle(); app.tick(time.time() + 1)
v = last()
assert v["temp"] == 21.5 and v["pressure"] == 3.0 and v["label"] == "hello world", v
assert v["bad"] is None and v["inf"] is None and v["absent"] is None, v  # non-finite and missing lines are not values
assert "rejected" in s.error and "bad" in s.error, s.error
assert v["long"] is None and v["ctl"] is None and v["empty"] is None, ("text points obey the 1-255 printable rule", v)
assert "printable" in s.error, s.error  # the message lists only the first few

# ---- missing file: source down, in words, no path or class name
f.unlink()
s.next_try = 0; s.cycle()
assert s.connected is False and s.error and str(tmp) not in s.error and "Error" not in s.error, s.error

# ---- too big a file is refused, not read
f.write_text("temp=1\n" + "x=1\n" * 400000, encoding="utf-8")
s.next_try = 0; s.cycle()
assert s.connected is False and "too large" in s.error, s.error

# ---- a directory (any non-regular file) is refused before it is opened
s2 = src_of("f"); s2.path = str(tmp)
s2.next_try = 0; s2.cycle()
assert s2.connected is False and "regular file" in s2.error, s2.error

# ---- Windows device names are refused at save, with or without extension
if os.name == "nt":
    for dev in ("CON", "nul.txt", "C:\\data\\com1.log", "LPT9", "aux"):
        assert "path" in props(put("source", "g", {"type": "file", "path": dev})), dev
    assert "path" not in props(put("source", "g", {"type": "file", "path": "console.txt"}))
    call("DELETE", "/api/config/source/g")
print("ok")
