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
assert a.failures["viewer"][0] == 4, a.failures

for i in range(10_100):  # a flood just past the cap
    fails(lambda n=f"made-up-{i}": a.login(n, "x"), 401)
assert len(a.failures) < 200, len(a.failures)  # the made-up names were pruned away, not the real entry
assert a.failures.get("viewer", (0, 0))[0] == 4, a.failures

fails(lambda: a.login("viewer", "bad"), 401)  # the fifth wrong attempt locks the real account
assert a.failures["viewer"][0] == 5 and a.failures["viewer"][1] > time.time(), a.failures
fails(lambda: a.login("viewer", "viewer-password-1"), 429)  # still locked, right password or not

# ---- if even a dict of real entries is over the cap, the oldest unlocked entry goes and a lockout stays
old_max = authmod.FAILURE_MAX
authmod.FAILURE_MAX = 3
try:
    b = Auth(new_store())
    for name in ("first", "locked-user", "second", "third"):
        b.store.add_user(name, "not-a-hash", ["user"])
    b.failures = {"first": (1, 0), "locked-user": (5, time.time() + 60), "second": (1, 0), "third": (1, 0)}
    b.failures["gone"] = (5, time.time() - 1)  # an expired lockout is dropped first
    b.failures["made-up"] = (1, 0)  # no such user: dropped first too
    b._prune_failures()
    assert "gone" not in b.failures and "made-up" not in b.failures, b.failures
    assert "locked-user" in b.failures, "a current lockout is never dropped"
    assert "first" not in b.failures and "second" in b.failures, "the oldest unlocked entry goes"
    assert len(b.failures) == 3, b.failures
finally:
    authmod.FAILURE_MAX = old_max

# ---- sweep removes expired sessions and lockouts, and keeps live ones
st2 = new_store()
c = Auth(st2)
assert c.bootstrap("admin-password-123") is None
now = time.time()
c.sessions["live"] = ("admin", now + 60)
c.sessions["dead"] = ("admin", now - 1)
c.failures["locked"] = (5, now + 60)
c.failures["expired"] = (5, now - 1)
c.failures["failed"] = (2, 0)
assert c.identity("dead")["name"] is None  # an expired token is anonymous before it is swept too
assert c.identity("live")["name"] == "admin"

c.sweep(now)
assert "dead" not in c.sessions and "live" in c.sessions, c.sessions
assert "expired" not in c.failures and "locked" in c.failures and "failed" in c.failures, c.failures
assert c.identity("dead")["name"] is None and c.identity("live")["name"] == "admin"

# ---- creating a session sweeps too, at most once per SWEEP_SECONDS (no thread needed)
c.sessions["dead2"] = ("admin", time.time() - 1)
c.swept = 0
c.login("admin", "admin-password-123")
assert "dead2" not in c.sessions, "a sign-in sweep must not leave an expired token behind"
assert c.swept > 0

print("ok")
