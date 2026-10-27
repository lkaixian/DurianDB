"""Password reset by admin-issued code, and the offline CLI. Run: python tests/test_reset.py"""
import gc
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import reset as reset_cli  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.auth import AuthError, hash_password, verify_password  # noqa: E402
from durian.store import Store  # noqa: E402
from _support import new_store  # noqa: E402

app = App(new_store())
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, who=admin):
    try:
        return app.handle(method, path, {}, body, who)
    except ApiError as e:
        return e.status, {"error": str(e)}


assert call("PUT", "/api/users/bob", {"roles": ["user"], "password": "bob-password-123"})[0] == 200
bob_tok = app.auth.login("bob", "bob-password-123")
bob = app.auth.identity(bob_tok)
assert call("POST", "/api/users/bob/reset", who=bob)[0] == 403 and call("POST", "/api/users/nobody/reset")[0] == 404
code = call("POST", "/api/users/bob/reset")[1]["code"]


def reset(**k):
    try:
        app.reset_password({"username": "bob", "code": code, "new": "brand-new-pass-1", **k})
        return 200
    except ApiError as e:
        return e.status


assert reset(code="rst_wrong") == 400 and reset(new="short") == 400   # a weak password does not spend the code
assert reset() == 200 and reset() == 400                              # works once
assert app.auth.identity(bob_tok)["name"] is None                      # old sessions are gone
assert app.auth.login("bob", "brand-new-pass-1")
try:
    app.auth.login("bob", "bob-password-123")
    raise SystemExit("old password still works")
except AuthError:
    pass

# five wrong codes lock guessing, even for the right code; an expired code fails
code = call("POST", "/api/users/bob/reset")[1]["code"]
for _ in range(5):
    reset(code="rst_nope")
assert reset() == 429
app.store.run("delete from auth_failures")
app.auth.resets["bob"] = (app.auth.resets["bob"][0], time.time() - 1)
assert reset() == 400

# ---- durian/reset.py: the offline CLI for when nobody can sign in.

ROOT = Path(__file__).resolve().parent.parent


def seed(path):
    """A database file with bob (second factor on) and carol, plus data the reset must not touch."""
    s = Store(str(path))
    s.add_user("bob", hash_password("bob-password-123"), ["user"])
    s.run("update users set totp=? where name=?", ("JBSWY3DPEHPK3PXP", "bob"))
    s.add_user("carol", hash_password("carol-password-123"), ["superadmin"])
    s.run("insert into events(point, level, message, ts) values (?,?,?,?)", ("pump", 1, "down", time.time()))
    s.put("dashboards", "plant", {"title": "Plant"})
    s.set_setting("site", "north")
    s.close()


def snapshot(path):
    s = Store(str(path))
    out = {"counts": s.table_counts(), "bob": s.user("bob"), "carol": s.user("carol"),
           "event": s.events(1)[0]["message"], "plant": s.get("dashboards", "plant"), "site": s.get_setting("site")}
    s.close()
    return out


def run_cli(name, db):
    """Call durian.reset.main in this process. Returns (exit status, stdout, error sentence)."""
    buf, saved = StringIO(), os.environ.get("DURIAN_DB")
    os.environ["DURIAN_DB"] = str(db)
    try:
        with redirect_stdout(buf):
            reset_cli.main(name)
        return 0, buf.getvalue(), ""
    except SystemExit as e:  # sys.exit(a sentence) carries that sentence as its code
        return (e.code if isinstance(e.code, int) else 1), buf.getvalue(), str(e.code)
    finally:
        if saved is None:
            del os.environ["DURIAN_DB"]
        else:
            os.environ["DURIAN_DB"] = saved
        gc.collect()  # a failed Store() leaves its connection in a reference cycle; release it before Windows cleans up


with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "durian.db"
    seed(db)
    before = snapshot(db)

    # An unknown name is refused in a sentence, and nothing in the store changes.
    status, out, err = run_cli("nobody", db)
    assert status == 1 and out == ""
    assert err.startswith("There is no user named nobody.") and "Traceback" not in err
    assert snapshot(db)["counts"] == before["counts"]

    # A missing database file is a sentence, not a traceback, and must not be silently created.
    missing = Path(tmp) / "absent.db"
    status, out, err = run_cli("bob", missing)
    assert status == 1 and out == "" and "does not exist" in err and str(missing) in err
    assert not missing.exists()

    # A file that exists but is not a database is also a sentence, not a traceback.
    broken = Path(tmp) / "broken.db"
    broken.write_bytes(b"not a sqlite database")
    status, out, err = run_cli("bob", broken)
    assert status == 1 and out == "" and "could not be read" in err and "Traceback" not in err

    # The real thing: a new password, the second factor off, one audit entry, everything else untouched.
    status, out, err = run_cli("bob", db)
    assert status == 0 and err == ""
    m = re.search(r"New password for bob: (\S+)", out)
    assert m and "Restart the server if the account was locked out." in out
    pw = m.group(1)

    after = snapshot(db)
    bob, was = after["bob"], before["bob"]
    assert verify_password(pw, bob["hash"]) and not verify_password("bob-password-123", bob["hash"])
    assert bob["totp"] is None and was["totp"] is not None
    assert bob["roles"] == was["roles"] and bob["created"] == was["created"] and bob["disabled"] == was["disabled"]
    assert after["carol"] == before["carol"]
    assert after["event"] == before["event"] and after["plant"] == before["plant"] and after["site"] == before["site"]
    for table in ("users", "history", "events", "objects"):
        assert after["counts"][table] == before["counts"][table]
    assert after["counts"]["audit"] == before["counts"]["audit"] + 1
    s = Store(str(db))
    entry = [r for r in s.audit_log(10) if r["action"] == "password.reset"]
    s.close()
    assert len(entry) == 1 and entry[0]["user"] == "system" and "bob" in entry[0]["detail"]
    assert pw not in entry[0]["detail"]  # a password must never reach the audit trail

# The documented entry point, in its own process, against a temporary database.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "durian.db"
    seed(db)
    r = subprocess.run([sys.executable, "-m", "durian.reset", "bob"], cwd=str(ROOT),
                       env={**os.environ, "DURIAN_DB": str(db)}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "New password for bob:" in r.stdout and "Restart the server if the account was locked out." in r.stdout
    pw = r.stdout.split("New password for bob: ")[1].splitlines()[0].strip()
    s = Store(str(db))
    assert verify_password(pw, s.user("bob")["hash"]) and s.user("bob")["totp"] is None
    s.close()

print("ok")
