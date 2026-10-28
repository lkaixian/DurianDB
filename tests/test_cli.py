"""Command line: paths when frozen, version, check, backups, restore. Run: python tests/test_cli.py"""
import contextlib
import io
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import cli, server  # noqa: E402
from durian.store import Store  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (err.write(str(e.code)) and 1)
    return code or 0, out.getvalue() + err.getvalue()


tmp = Path(tempfile.mkdtemp())
os.environ.pop("DURIAN_HOME", None)

# frozen paths: web from the bundle, data beside the executable, DURIAN_HOME wins
web, home = server.paths()
assert web == server.ROOT / "web" and home == server.ROOT
sys.frozen, sys._MEIPASS = True, str(tmp / "bundle")
try:
    sys.executable, real_exe = str(tmp / "app" / "durian.exe"), sys.executable
    web, home = server.paths()
    assert web == tmp / "bundle" / "web" and home == tmp / "app", (web, home)
    os.environ["DURIAN_HOME"] = str(tmp / "data")
    assert server.paths()[1] == tmp / "data"
finally:
    sys.executable = real_exe
    del sys.frozen, sys._MEIPASS
    os.environ.pop("DURIAN_HOME", None)

db = tmp / "durian.db"
os.environ["DURIAN_DB"] = str(db)
os.environ["DURIAN_HOME"] = str(tmp)
s = Store(str(db))
s.put("source", "a", {"type": "virtual"})
s.put("point", "p1", {"source": "a"})
s.close()

code, out = run("version")
assert code == 0 and out.strip().split()[-1], out
code, out = run("check")
assert code == 0 and "users" in out and "points" in out and "sources" in out and "history" in out, out
os.environ["DURIAN_DB"] = str(tmp / "missing.db")
code, out = run("check")
assert code == 1 and "Traceback" not in out and "Error" not in out, out
junk = tmp / "junk.db"
junk.write_bytes(b"not a database" * 100)
os.environ["DURIAN_DB"] = str(junk)
code, out = run("check")
assert code == 1 and "Error" not in out, out
os.environ["DURIAN_DB"] = str(db)

code, out = run("backups")
assert code == 0, out
(tmp / "backups").mkdir()
good = tmp / "backups" / "durian-20260101-000000.db"
src = sqlite3.connect(db)
dst = sqlite3.connect(good)
src.backup(dst)
dst.close()
src.close()
code, out = run("backups")
assert code == 0 and good.name in out, out

# restore: refused while a connection holds the database; refused for a damaged file; works otherwise
holder = sqlite3.connect(db, isolation_level=None)
holder.execute("begin exclusive") if False else holder.execute("begin immediate")
code, out = run("restore", str(good))
assert code == 1 and "in use" in out, out
holder.execute("rollback")
holder.close()
code, out = run("restore", str(junk))
assert code == 1 and "damaged" in out, out
c = sqlite3.connect(db)
c.execute("delete from objects")
c.commit()
c.close()
code, out = run("restore", str(good))
assert code == 0, out
assert (tmp / "durian.db.before-restore").is_file()
c = sqlite3.connect(db)
assert c.execute("select count(*) from objects where kind='point'").fetchone()[0] == 1
c.close()
code, out = run("restore", str(tmp / "nope.db"))
assert code == 1 and "Error" not in out, out
code, out = run("bogus")
assert code == 2, out
print("ok")
