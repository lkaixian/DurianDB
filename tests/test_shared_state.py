"""Sign-in state lives in the store, so a second server process sees it. Run: python tests/test_shared_state.py

Two Store objects are built over the same database; that is what two server processes would see. The same account,
session, lockout, TOTP step and confirm token must mean the same thing to both.

Set DURIAN_TEST_STORE_URL=mysql://user:pass@host:port/db to run the same checks against MySQL/MariaDB (the database
is emptied first, so point it at a throwaway one).
"""
import base64
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import App  # noqa: E402
from durian.auth import LOCK_AFTER, Auth, AuthError, hash_password, totp  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.core import Event, Level  # noqa: E402
from durian.store import Store  # noqa: E402
from _support import TABLES, new_store  # noqa: E402


def two_stores():
    """Two connections to one database: a file for SQLite, or the throwaway database in DURIAN_TEST_STORE_URL."""
    url = os.environ.get("DURIAN_TEST_STORE_URL")
    if url:
        probe = Store(url)
        for t in TABLES:
            probe.run(f"drop table if exists {t}")
        probe.close()
        return Store(url), Store(url)
    path = Path(tempfile.mkdtemp()) / "shared.db"
    return Store(str(path)), Store(str(path))


def fails(fn, status):
    try:
        fn()
    except AuthError as e:
        assert e.status == status, (e.status, status)
        return
    raise AssertionError("expected AuthError")


sa, sb = two_stores()
seed(sa)
point = sa.get("point", "pump-setpoint")
sa.put("point", "pump-setpoint", {**point, "confirm": True})
a, b = App(sa), App(sb)
assert a.auth.bootstrap("admin-password-123") is None

# ---- one process issues the session, the other accepts it; logout on the first revokes it on the second
token = a.login({"username": "admin", "password": "admin-password-123"})
ident = a.auth.identity(token)
assert ident["name"] == "admin" and b.auth.identity(token)["name"] == "admin"

sa.add_session("expired-by-hand", "admin", time.time() - 1)
b.auth.sweep()
assert sa.session("expired-by-hand") is None, "the other process can purge an expired session"

# ---- a lockout counted by one process stops the other, for the right password too
sa.add_user("bob", hash_password("bob-password-123"), ["user"])
for _ in range(LOCK_AFTER):
    fails(lambda: a.auth.login("bob", "bad-bad-bad"), 401)
assert sa.failure("bob")["attempts"] == LOCK_AFTER
fails(lambda: b.auth.login("bob", "bob-password-123"), 429)

# ---- a TOTP code accepted by one process cannot be replayed to the other
sa.add_user("carol", hash_password("carol-password-123"), ["user"])
secret = base64.b32encode(os.urandom(20)).decode()
sa.run("update users set totp=? where name=?", (secret, "carol"))
code = totp(secret, time.time())[0]
assert a.auth.login("carol", "carol-password-123", code)
fails(lambda: b.auth.login("carol", "carol-password-123", code), 401)

# ---- a write confirmation is spent once across processes
status, first = a.handle("POST", "/api/points/pump-setpoint/set", {}, {"value": 41}, ident)
assert status == 202 and first["confirm"], (status, first)
status, second = b.handle("POST", "/api/points/pump-setpoint/set", {}, {"value": 41, "confirm": first["confirm"]}, ident)
assert status == 200, (status, second)
status, third = a.handle("POST", "/api/points/pump-setpoint/set", {}, {"value": 41, "confirm": first["confirm"]}, ident)
assert status == 202 and third.get("confirm"), "a spent confirmation must ask for a new token"

a.auth.logout(token)
assert b.auth.identity(token)["name"] is None, "logout on one process must revoke the session on the other"

# ---- the store unavailable: fall back to this process's old maps, with one honest warning
st = new_store()
auth = Auth(st)
assert auth.bootstrap("admin-password-123") is None
auth.swept = time.time()  # do not let the inline sweep hide the failure under test


def boom(*args, **kwargs):
    raise RuntimeError("the database did not answer")


st.add_session = st.session = st.delete_session = st.sessions_for = boom
records = []


class Catch(logging.Handler):
    def emit(self, record):
        records.append(record)


handler, logger = Catch(), logging.getLogger("durian.auth")
logger.addHandler(handler)
try:
    fallback_token = auth.login("admin", "admin-password-123")
    assert auth.identity(fallback_token)["name"] == "admin"
finally:
    logger.removeHandler(handler)
warnings = [r.getMessage() for r in records if r.levelno == logging.WARNING]
assert auth._warned and len(warnings) == 1, records
assert "this server process only" in warnings[0] and "database" in warnings[0], warnings

# ---- the same fallback keeps confirmations single-use while the store cannot answer
app_store = new_store()
app = App(app_store)
app_store.add_confirm = app_store.take_confirm = boom
op = {"name": "op", "roles": {"user"}}
fallback_confirm = app.confirm_token(op, "pump-setpoint", 1)
assert fallback_confirm in app.confirms
assert app.confirmed(op, "pump-setpoint", 1, fallback_confirm)
assert not app.confirmed(op, "pump-setpoint", 1, fallback_confirm)

# ---- an alarm escalated by one process is not escalated again by the other over the same store
sa2, sb2 = two_stores()
a2, b2 = App(sa2), App(sb2)
sa2.put("handler", "esc", {"type": "email", "from": "alarms@example.com", "to": ["ops@example.com"],
                           "escalate_after": 1, "escalate_to": ["boss@example.com"]})
ev = Event("hot", Level.WARNING, "95 > 90", time.time())
ev.id = sa2.add_event(ev)
a2.open_events, b2.open_events = [ev], [ev]
sent = []
a2.outputs._put = b2.outputs._put = lambda item, target=None: sent.append(item)
a2.escalate(now=ev.ts + 120)
assert len(sent) == 1 and sent[0][0] == "mail", sent
b2.escalate(now=ev.ts + 130)
assert len(sent) == 1, "a second process must not escalate the same alarm again"
ev.cleared_ts = ev.ts + 200
sa2.clear_event(ev.id, ev.cleared_ts)
a2.escalate(now=ev.ts + 300)
assert sa2.q("select handler, event_id from escalations") == [], "a closed alarm needs no memory of being escalated"

# ---- one process owns the tick; the other says so once and does not poll
sa3, sb3 = two_stores()
a3, b3 = App(sa3), App(sb3)
records = []
handler = Catch()
app_log = logging.getLogger("durian.app")
app_log.addHandler(handler)
try:
    a3.tick(time.time())
    assert sa3.lease()["owner"] == a3.lease_owner and a3.lease_held, sa3.lease()
    b3.tick(time.time())
    assert not b3.lease_held, "the second process must not tick while the first holds the lease"
    b3.tick(time.time())  # it says so once, not once per tick
finally:
    app_log.removeHandler(handler)
warnings = [r.getMessage() for r in records if r.levelno == logging.WARNING]
assert len(warnings) == 1 and "tick lease" in warnings[0] and "not polling" in warnings[0], warnings

# a slow tick gets a lease covering three of them, or a second process could take over between renewals
a3.tick_seconds = 20
a3.tick(time.time() + 1)
assert sa3.lease()["owner"] == a3.lease_owner
assert sa3.lease()["expires"] > time.time() + 55, sa3.lease()
a3.tick_seconds = 2.0

# ---- a dead owner's lease expires, so the survivor takes over without waiting for it to return
sa3.run("update tick_lease set expires=? where id=1", (time.time() - 1,))
b3.tick(time.time())
assert sa3.lease()["owner"] == b3.lease_owner and b3.lease_held, sa3.lease()
a3.tick(time.time())  # and the old owner is now refused
assert not a3.lease_held

# ---- a clean shutdown releases the lease at once
b3.stop()
assert sa3.lease() is None and not b3.lease_held
a3.tick(time.time())
assert sa3.lease()["owner"] == a3.lease_owner, "the survivor can take a released lease at once"

# ---- the store cannot hold the shared engine state: tick anyway, with one honest warning
st2 = new_store()
app2 = App(st2)
records = []
handler = Catch()
app_log = logging.getLogger("durian.app")
app_log.addHandler(handler)
st2.take_lease = boom
st2.claim_escalation = st2.purge_escalations = boom
try:
    app2.tick(time.time())
    assert app2.lease_held, "a database that cannot hold the lease must not stop this server polling"
    st2.put("handler", "esc", {"type": "email", "from": "alarms@example.com", "to": ["ops@example.com"],
                               "escalate_after": 1, "escalate_to": ["boss@example.com"]})
    ev2 = Event("hot", Level.WARNING, "95 > 90", time.time())
    ev2.id = st2.add_event(ev2)
    app2.open_events = [ev2]
    app2.outputs._put = lambda item, target=None: None
    app2.escalate(now=ev2.ts + 120)  # falls back to this process's set
    assert ("esc", ev2.id) in app2.escalated
finally:
    app_log.removeHandler(handler)
warnings = [r.getMessage() for r in records if r.levelno == logging.WARNING]
assert len(warnings) == 1 and "this server process only" in warnings[0], warnings

print("ok")
