"""Users, password hashing, sessions, and role-based permission checks.

Model (from docs/research/02 and 03): an object lists the roles allowed to read, edit or set it.
Roles can inherit: superadmin > user > anonymous. An empty list means superadmin only.
"""
import hashlib
import base64
import hmac
import logging
import os
import secrets
import time

log = logging.getLogger("durian.auth")

INHERIT = {"superadmin": {"superadmin", "user", "anonymous"}, "user": {"user", "anonymous"}, "anonymous": {"anonymous"}}
MIN_PASSWORD = 10
LOCK_AFTER, LOCK_SECONDS = 5, 300
RESET_SECONDS = 3600  # an issued reset code works for one hour, once
FAILURE_MAX = 10_000  # failed-sign-in entries kept before a prune
SWEEP_SECONDS = 60  # expired sessions and lockouts are swept at most this often from the sign-in path


def totp(secret, t=None, step=30):
    """RFC 6238 code (SHA-1, 6 digits) for a base32 secret, as authenticator apps compute it. Returns (code, counter)."""
    n = int((time.time() if t is None else t) // step)
    h = hmac.new(base64.b32decode(secret), n.to_bytes(8, "big"), "sha1").digest()
    o = h[-1] & 15
    return f"{(int.from_bytes(h[o:o + 4], 'big') & 0x7fffffff) % 10 ** 6:06d}", n


class ApiError(Exception):
    """A request failure: the status the HTTP layer sends and the sentence the caller reads."""
    def __init__(self, status, message, errors=None):
        super().__init__(message)
        self.status, self.errors = status, errors


class AuthError(ApiError):
    """A credential failure. It is already an ApiError, so no handler has to translate it for HTTP."""
    def __init__(self, message, status=401):
        super().__init__(status, message)


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), h)
    except ValueError:
        return False


def held(roles) -> set:
    """Every role a user effectively holds, following inheritance. Everyone holds anonymous: public means everyone."""
    out = {"anonymous"}
    for r in roles:
        out |= INHERIT.get(r, {r})
    return out


def can(roles_held: set, required) -> bool:
    return "superadmin" in roles_held or bool(roles_held & set(required or []))


def check_password_strength(pw):
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        raise AuthError(f"password must be at least {MIN_PASSWORD} characters", 400)


class Auth:
    def __init__(self, store, session_seconds=8 * 3600):
        self.store, self.ttl = store, session_seconds
        self.resets = {}  # user -> (sha256 of the code, expires); in memory on purpose, so a restart voids it
        self.pending = {}  # user -> secret not yet confirmed; in memory too (TECHNICAL_DEBT "deliberate")
        # The store holds sessions, lockouts, spent TOTP steps and confirm tokens so a second server process sees
        # the same state. These dicts are the fallback used only while the store cannot answer.
        self.sessions, self.failures, self.last_step = {}, {}, {}
        self.swept = 0.0  # when sweep() last ran, so the sign-in path can cap how often it sweeps
        self._warned = False  # the one warning that shared state has degraded to this process
        self._dummy = hash_password("not-a-real-password")

    def degraded(self, ex):
        """One honest sentence when shared state cannot be read or written. The server keeps working with the
        process-local state it had before; the warning says what a second process cannot see."""
        if not self._warned:
            self._warned = True
            log.warning("the database did not answer, so sessions, lockout counters, the TOTP replay guard and "
                        "confirm tokens are kept in this server process only and a second server process will not "
                        "see them; restore the database to share them again")
            log.debug("shared sign-in state is unavailable: %s", ex)

    # ---- shared state accessors, each falling back to process memory only while the store cannot answer
    def _failure(self, name):
        try:
            row = self.store.failure(name)
            return (row["attempts"], row["locked_until"] or 0) if row else (0, 0)
        except Exception as ex:
            self.degraded(ex)
            return self.failures.get(name, (0, 0))

    def _set_failure(self, name, count, until):
        try:
            self.store.set_failure(name, count, until)
            self.failures.pop(name, None)
            return
        except Exception as ex:
            self.degraded(ex)
        self.failures[name] = (count, until)

    def _clear_failure(self, name):
        try:
            self.store.delete_failure(name)
        except Exception as ex:
            self.degraded(ex)
        self.failures.pop(name, None)

    def _failure_count(self):
        try:
            return self.store.failure_count()
        except Exception as ex:
            self.degraded(ex)
            return len(self.failures)

    def _add_session(self, token, name, expires):
        try:
            self.store.add_session(token, name, expires)
            self.sessions.pop(token, None)
        except Exception as ex:
            self.degraded(ex)
            self.sessions[token] = (name, expires)

    def _session_of(self, token):
        try:
            row = self.store.session(token)
            return (row["name"], row["expires"]) if row else (None, 0)
        except Exception as ex:
            self.degraded(ex)
            return self.sessions.get(token, (None, 0))

    def _forget_session(self, token):
        try:
            self.store.delete_session(token)
        except Exception as ex:
            self.degraded(ex)
        self.sessions.pop(token, None)

    def _claim_step(self, name, step):
        try:
            return self.store.claim_step(name, step)
        except Exception as ex:
            self.degraded(ex)
            if step > self.last_step.get(name, 0):
                self.last_step[name] = step
                return True
            return False

    def bootstrap(self, password=None):
        """Create the first admin. Returns a generated password to show once, or None if nothing was made."""
        if self.store.users():
            return None
        generated = None if password else secrets.token_urlsafe(12)
        pw = password or generated
        check_password_strength(pw)
        self.store.add_user("admin", hash_password(pw), ["superadmin"])
        return generated

    def check_code(self, name, secret, code):
        """True when `code` is the current code, or the one before or after (clock drift). Each time step is accepted once."""
        code = code.strip() if isinstance(code, str) else ""
        for d in (0, -1, 1):
            want, n = totp(secret, time.time() + d * 30)
            if hmac.compare_digest(want, code) and self._claim_step(name, n):
                return True
        return False

    def mfa_setup(self, name):
        self.pending[name] = base64.b32encode(os.urandom(20)).decode()
        return {"secret": self.pending[name], "uri": f"otpauth://totp/DurianDB:{name}?secret={self.pending[name]}&issuer=DurianDB"}

    def mfa_enable(self, name, code):
        secret = self.pending.get(name)
        if not secret or not self.check_code(name, secret, code):
            raise AuthError("that code does not match; check the phone clock and try again", 400)
        self.store.run("update users set totp=? where name=?", (secret, name))
        self.pending.pop(name)
        self.store.audit(name, "mfa.enable")

    def _prune_failures(self):
        """Before made-up names can grow `failures`, drop what cannot matter: names with no account and lockouts
        whose window has passed. A real account's live lockout must survive a flood, so only then, and only while
        still over the cap, are the oldest unlocked entries dropped."""
        try:
            self.store.prune_failures(FAILURE_MAX)
            return
        except Exception as ex:
            self.degraded(ex)
        try:
            known = {u["name"] for u in self.store.users()}
        except Exception:
            known = None  # the store is down: clean only what this process can still see
        now = time.time()
        for name in list(self.failures):
            user = name[6:] if isinstance(name, str) and name.startswith("reset:") else name
            count, until = self.failures[name]
            unknown = known is not None and not (isinstance(user, str) and user in known)
            if unknown or (until and until <= now):
                del self.failures[name]
        excess = len(self.failures) - FAILURE_MAX
        if excess > 0:  # only real, still-live state is left: the oldest goes, never one that is locked out now
            for name in list(self.failures):
                if excess <= 0:
                    break
                if self.failures[name][1] <= now:
                    del self.failures[name]
                    excess -= 1

    def sweep(self, now=None):
        """Drop sessions, lockouts and confirmations that have expired. Called once a minute by maintenance, and
        (capped by SWEEP_SECONDS) when a session is created. The store is the source of truth, so this cleans the
        state every process shares; the dicts are cleaned too while the store cannot answer."""
        now = now or time.time()
        try:
            self.store.purge_sessions(now)
            self.store.purge_failures(now)
            self.store.purge_confirms(now)
        except Exception as ex:
            self.degraded(ex)
            for token in [t for t, (_, exp) in self.sessions.items() if exp <= now]:
                del self.sessions[token]
            for name in [n for n, (count, until) in self.failures.items() if until and until <= now]:
                del self.failures[name]
        self.swept = now

    def login(self, name, pw, code=None):
        count, until = self._failure(name)
        if until > time.time():
            raise AuthError("too many failed attempts, try again later", 429)
        user = self.store.user(name) if isinstance(name, str) else None
        ok = verify_password(pw if isinstance(pw, str) else "", user["hash"] if user else self._dummy) and user
        ok = ok and not user.get("disabled")  # the same message as a wrong password: a disabled account is not advertised
        if ok and user.get("totp"):
            if code is None:  # right password: ask for the second factor without counting it as a failure
                raise AuthError("authentication code required")
            ok = self.check_code(name, user["totp"], code)
        if not ok:
            if self._failure_count() > FAILURE_MAX:  # unknown names must not grow this without bound
                self._prune_failures()
            count += 1
            self._set_failure(name, count, time.time() + LOCK_SECONDS if count >= LOCK_AFTER else 0)
            if user:  # only real accounts are audited: a stream of made-up names must not fill the trail
                self.store.audit(name, "login.locked" if count == LOCK_AFTER else "login.failed", f"attempt {count}")
            raise AuthError("bad username, password or code")
        self._clear_failure(name)
        self.store.audit(name, "login")
        return self._session(name)

    def _session(self, name):
        """Open a session for an account that has already been allowed in."""
        token = secrets.token_urlsafe(32)
        self._add_session(token, name, time.time() + self.ttl)
        if time.time() - self.swept > SWEEP_SECONDS:  # cheap cap: at most one sweep a minute inline, no thread
            self.sweep()
        return token

    def login_sso(self, name):
        """A verified SSO identity opening a session. The same lockout and disable rules as a password sign-in apply."""
        _count, until = self._failure(name)
        if until > time.time():
            raise AuthError("too many failed attempts, try again later", 429)
        user = self.store.user(name)
        if not user or user.get("disabled"):
            raise AuthError("that account is disabled" if user else "no such account", 403)
        self._clear_failure(name)
        return self._session(name)

    def issue_reset(self, name):
        """A one-time code an admin hands to a user who cannot sign in. Only its hash is kept, in memory, so a restart voids it."""
        code = "rst_" + secrets.token_urlsafe(9)
        self.resets[name] = (hashlib.sha256(code.encode()).hexdigest(), time.time() + RESET_SECONDS)
        return code

    def redeem_reset(self, name, code):
        """Spend a reset code. Wrong guesses count toward the same lockout as passwords. Signs the user out everywhere and lifts any lockout."""
        key = "reset:" + str(name)
        count, until = self._failure(key)
        if until > time.time():
            raise AuthError("too many failed attempts, try again later", 429)
        want, exp = self.resets.get(name, ("", 0))
        if not (isinstance(code, str) and exp > time.time() and hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), want)):
            self._set_failure(key, count + 1, time.time() + LOCK_SECONDS if count + 1 >= LOCK_AFTER else 0)
            raise AuthError("that reset code is wrong or has expired", 400)
        self.resets.pop(name)
        self._clear_failure(key)
        self._clear_failure(name)
        self.end_sessions(name)

    def end_sessions(self, name):
        """Sign a person out everywhere: used on a password reset and when an administrator disables the account."""
        try:
            tokens = self.store.sessions_for(name)
        except Exception as ex:
            self.degraded(ex)
            tokens = [t for t, (u, _) in self.sessions.items() if u == name]
        for t in tokens:
            self._forget_session(t)

    def logout(self, token):
        name = self._session_of(token)[0]
        if name:
            self.store.audit(name, "logout")
        self._forget_session(token)

    # ---- machine tokens: for Node-RED, scripts and other systems. Scoped, expiring, revocable, stored only as a hash.
    TOKEN_SCOPES = ("read", "write", "ingest")  # read: look at data. write: also set points. ingest: push values into one source only.

    def create_token(self, name, owner, scope, source=None, days=None):
        """Returns the token. It is shown once and cannot be recovered: only its hash is kept."""
        token = "dtk_" + secrets.token_urlsafe(32)
        self.store.add_token(name, hashlib.sha256(token.encode()).hexdigest(), owner, scope, source,
                             time.time() + days * 86400 if days else None)
        return token

    def token_identity(self, token):
        if not (isinstance(token, str) and token.startswith("dtk_") and len(token) < 100):
            return None
        row = self.store.token_by_hash(hashlib.sha256(token.encode()).hexdigest())
        if not row or row["disabled"] or (row["expires"] and row["expires"] < time.time()):
            return None
        user = self.store.user(row["owner"])
        if not user or user.get("disabled"):  # a disabled account's tokens stop working at once
            return None
        if time.time() - (row["last_used"] or 0) > 60:  # at most one write a minute per token
            self.store.touch_token(row["id"], time.time())
        return {"name": row["owner"], "roles": held(user["roles"]), "scope": row["scope"], "source": row["source"], "token": row["name"]}

    def identity(self, token):
        """{'name': str|None, 'roles': set}. Unknown or expired tokens are anonymous."""
        name, exp = self._session_of(token)
        if name and exp > time.time():
            try:
                user = self.store.user(name)
            except Exception as ex:  # the store is down: deny rather than guess, but keep the session for when it returns
                self.degraded(ex)
                return {"name": None, "roles": {"anonymous"}}
            if user and not user.get("disabled"):
                return {"name": name, "roles": held(user["roles"])}
        self._forget_session(token)
        return {"name": None, "roles": {"anonymous"}}
