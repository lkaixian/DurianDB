"""Users, password hashing, sessions, and role-based permission checks.

Model (from docs/research/02 and 03): an object lists the roles allowed to read, edit or set it.
Roles can inherit: superadmin > user > anonymous. An empty list means superadmin only.
"""
import hashlib
import hmac
import os
import secrets
import time

INHERIT = {"superadmin": {"superadmin", "user", "anonymous"}, "user": {"user", "anonymous"}, "anonymous": {"anonymous"}}
MIN_PASSWORD = 10
LOCK_AFTER, LOCK_SECONDS = 5, 300


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
    """Every role a user effectively holds, following inheritance."""
    out = set()
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

    def login(self, name, pw):
        count, until = self.failures.get(name, (0, 0))
        if until > time.time():
            raise AuthError("too many failed attempts, try again later", 429)
        user = self.store.user(name) if isinstance(name, str) else None
        ok = verify_password(pw if isinstance(pw, str) else "", user["hash"] if user else self._dummy) and user
        if not ok:
            if len(self.failures) > 10_000:  # unknown names must not grow this without bound
                self.failures.clear()
            count += 1
            self.failures[name] = (count, time.time() + LOCK_SECONDS if count >= LOCK_AFTER else 0)
            raise AuthError("bad username or password")
        self.failures.pop(name, None)
        token = secrets.token_urlsafe(32)
        self.sessions[token] = (name, time.time() + self.ttl)
        return token

    def logout(self, token):
        self.sessions.pop(token, None)

    def identity(self, token):
        """{'name': str|None, 'roles': set}. Unknown or expired tokens are anonymous."""
        name, exp = self.sessions.get(token, (None, 0))
        if name and exp > time.time():
            user = self.store.user(name)
            if user:
                return {"name": name, "roles": held(user["roles"])}
        self.sessions.pop(token, None)
        return {"name": None, "roles": {"anonymous"}}
