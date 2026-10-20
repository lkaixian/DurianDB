"""Users, password hashing, sessions, and role-based permission checks.

Model (from docs/research/02 and 03): an object lists the roles allowed to read, edit or set it.
Roles can inherit: superadmin > user > anonymous. An empty list means superadmin only.
"""
import hashlib
import base64
import hmac
import os
import secrets
import time

INHERIT = {"superadmin": {"superadmin", "user", "anonymous"}, "user": {"user", "anonymous"}, "anonymous": {"anonymous"}}
MIN_PASSWORD = 10
LOCK_AFTER, LOCK_SECONDS = 5, 300


def totp(secret, t=None, step=30):
    """RFC 6238 code (SHA-1, 6 digits) for a base32 secret, as authenticator apps compute it. Returns (code, counter)."""
    n = int((time.time() if t is None else t) // step)
    h = hmac.new(base64.b32decode(secret), n.to_bytes(8, "big"), "sha1").digest()
    o = h[-1] & 15
    return f"{(int.from_bytes(h[o:o + 4], 'big') & 0x7fffffff) % 10 ** 6:06d}", n


class AuthError(Exception):
    def __init__(self, message, status=401):
        super().__init__(message)
        self.status = status


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
        self.pending, self.last_step = {}, {}  # user -> secret not yet confirmed; user -> last accepted time step (a code works once)
        self.sessions, self.failures = {}, {}  # token -> (user, expires); user -> (count, locked_until)
        self._dummy = hash_password("not-a-real-password")

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
            if hmac.compare_digest(want, code) and n > self.last_step.get(name, 0):
                self.last_step[name] = n
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

    def login(self, name, pw, code=None):
        count, until = self.failures.get(name, (0, 0))
        if until > time.time():
            raise AuthError("too many failed attempts, try again later", 429)
        user = self.store.user(name) if isinstance(name, str) else None
        ok = verify_password(pw if isinstance(pw, str) else "", user["hash"] if user else self._dummy) and user
        if ok and user.get("totp"):
            if code is None:  # right password: ask for the second factor without counting it as a failure
                raise AuthError("authentication code required")
            ok = self.check_code(name, user["totp"], code)
        if not ok:
            if len(self.failures) > 10_000:  # unknown names must not grow this without bound
                self.failures.clear()
            count += 1
            self.failures[name] = (count, time.time() + LOCK_SECONDS if count >= LOCK_AFTER else 0)
            if user:  # only real accounts are audited: a stream of made-up names must not fill the trail
                self.store.audit(name, "login.locked" if count == LOCK_AFTER else "login.failed", f"attempt {count}")
            raise AuthError("bad username, password or code")
        self.failures.pop(name, None)
        self.store.audit(name, "login")
        token = secrets.token_urlsafe(32)
        self.sessions[token] = (name, time.time() + self.ttl)
        return token

    def logout(self, token):
        name = self.sessions.pop(token, (None,))[0]
        if name:
            self.store.audit(name, "logout")

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
        if not user:
            return None
        if time.time() - (row["last_used"] or 0) > 60:  # at most one write a minute per token
            self.store.touch_token(row["id"], time.time())
        return {"name": row["owner"], "roles": held(user["roles"]), "scope": row["scope"], "source": row["source"], "token": row["name"]}

    def identity(self, token):
        """{'name': str|None, 'roles': set}. Unknown or expired tokens are anonymous."""
        name, exp = self.sessions.get(token, (None, 0))
        if name and exp > time.time():
            user = self.store.user(name)
            if user:
                return {"name": name, "roles": held(user["roles"])}
        self.sessions.pop(token, None)
        return {"name": None, "roles": {"anonymous"}}
