"""Auth keeps live state under a flood, and lets expired state go. Run: python tests/test_auth_state.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import durian.auth as authmod  # noqa: E402
from durian.auth import Auth, AuthError, hash_password  # noqa: E402
from _support import new_store  # noqa: E402


def fails(fn, status):
    try:
        fn()
    except AuthError as e:
        assert e.status == status, (e.status, status)
        return e
    raise AssertionError("expected AuthError")


# ---- a flood of made-up names must not throw away a real account's live lockout state
st = new_store()
a = Auth(st)
assert a.bootstrap("admin-password-123") is None
st.add_user("viewer", hash_password("viewer-password-1"), ["user"])
a._dummy = "not-a-hash"  # made-up names fail instantly; the scrypt cost is not what this test is about

for _ in range(4):  # four wrong attempts: counted, not locked yet
    fails(lambda: a.login("viewer", "bad"), 401)
assert st.failure("viewer")["attempts"] == 4, st.failure("viewer")

old_max, authmod.FAILURE_MAX = authmod.FAILURE_MAX, 100  # a smaller cap keeps the flood quick
try:
    for i in range(300):  # a flood just past the cap
        fails(lambda n=f"made-up-{i}": a.login(n, "x"), 401)
finally:
    authmod.FAILURE_MAX = old_max
assert st.failure_count() <= 102, st.failure_count()  # the flood stayed near the cap, not at 300
a._prune_failures()
assert st.failure_count() == 1, st.failure_count()  # the made-up names go, the real entry stays
assert st.failure("viewer")["attempts"] == 4, st.failure("viewer")

fails(lambda: a.login("viewer", "bad"), 401)  # the fifth wrong attempt locks the real account
assert st.failure("viewer")["attempts"] == 5 and st.failure("viewer")["locked_until"] > time.time(), st.failure("viewer")
fails(lambda: a.login("viewer", "viewer-password-1"), 429)  # still locked, right password or not

# ---- if even the known entries are over the cap, the oldest unlocked entry goes and a lockout stays
old_max = authmod.FAILURE_MAX
authmod.FAILURE_MAX = 3
try:
    b = Auth(new_store())
    for name in ("first", "locked-user", "second", "third"):
        b.store.add_user(name, "not-a-hash", ["user"])
    b.store.set_failure("first", 1, 0)
    b.store.set_failure("locked-user", 5, time.time() + 60)
    b.store.set_failure("second", 1, 0)
    b.store.set_failure("third", 1, 0)
    b.store.set_failure("gone", 5, time.time() - 1)  # an expired lockout is dropped first
    b.store.set_failure("made-up", 1, 0)  # no such user: dropped first too
    b._prune_failures()
    rows = {r["name"]: r for r in b.store.q("select name, attempts, locked_until from auth_failures")}
    assert "gone" not in rows and "made-up" not in rows, rows
    assert "locked-user" in rows, "a current lockout is never dropped"
    assert "first" not in rows and "second" in rows, "the oldest unlocked entry goes"
    assert len(rows) == 3, rows
finally:
    authmod.FAILURE_MAX = old_max

# ---- sweep removes expired sessions and lockouts, and keeps live ones
st2 = new_store()
c = Auth(st2)
assert c.bootstrap("admin-password-123") is None
now = time.time()
st2.add_session("live", "admin", now + 60)
st2.add_session("dead", "admin", now - 1)
st2.set_failure("locked", 5, now + 60)
st2.set_failure("expired", 5, now - 1)
st2.set_failure("failed", 2, 0)
assert c.identity("dead")["name"] is None  # an expired token is anonymous before it is swept too
assert c.identity("live")["name"] == "admin"
st2.add_session("dead-sweep", "admin", now - 1)

c.sweep(now)
assert st2.session("dead-sweep") is None and st2.session("live") is not None, st2.session("dead-sweep")
assert st2.failure("expired") is None and st2.failure("locked") is not None and st2.failure("failed") is not None
assert c.identity("dead")["name"] is None and c.identity("live")["name"] == "admin"

# ---- creating a session sweeps too, at most once per SWEEP_SECONDS (no thread needed)
st2.add_session("dead2", "admin", time.time() - 1)
c.swept = 0
c.login("admin", "admin-password-123")
assert st2.session("dead2") is None, "a sign-in sweep must not leave an expired token behind"
assert c.swept > 0

print("ok")
