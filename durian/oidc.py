"""Sign-in through an OpenID Connect provider, alongside passwords.

Stdlib only. The authorization-code flow uses PKCE (S256); the ID token is a compact JWS
whose RS256 signature is checked by hand: RSA PKCS#1 v1.5 with SHA-256 is modular
exponentiation plus the EMSA-PKCS1-v1_5 padding check.

The provider's endpoints are discovered from <issuer>/.well-known/openid-configuration
and cached. A provider that cannot be reached is a sentence, never a traceback. State,
nonce and the PKCE verifier live here in memory, single use, and are never logged or
written to the audit trail.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from .core import sentence

DISCOVERY_TTL = 3600
JWKS_TTL = 3600
STATE_SECONDS = 600  # a sign-in started more than ten minutes ago is stale
CLOCK_SKEW = 60  # seconds of tolerance on the ID token's exp
TIMEOUT = 8
STATE_MAX = 10_000  # pending sign-ins kept before the oldest are dropped (a start endpoint must not grow without bound)
ROLE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
USER_NAME = ROLE  # the same shape App.users() accepts for a local account
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
SETTINGS = ("issuer", "client_id", "client_secret", "redirect_uri", "groups_claim", "role_map", "auto_provision")


class OIDCError(Exception):
    """A refused sign-in, already worded for the person at the browser."""

    def __init__(self, message, status=401):
        super().__init__(message)
        self.status = status


def _b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64e(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _host(url):
    try:
        return urllib.parse.urlsplit(url).hostname or url
    except ValueError:
        return "the provider"


def _localhost(host):
    return host in ("localhost", "127.0.0.1", "::1") or (isinstance(host, str) and host.endswith(".localhost"))


def _url_ok(value, allow_http_on_localhost=True):
    """An absolute http(s) URL with a host; plain HTTP only for localhost, as everywhere else in DurianDB."""
    if not (isinstance(value, str) and 0 < len(value) <= 2000 and value.isprintable()):
        return False
    try:
        u = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.netloc or u.fragment:
        return False
    if u.username or u.password:
        return False
    if u.scheme == "http" and not (allow_http_on_localhost and _localhost(u.hostname)):
        return False
    return True


def validate_sso(body):
    """The stored `sso` settings block, checked the same way every other config object is: [{property, message}]."""
    if not isinstance(body, dict):
        return [{"property": "body", "message": "must be an object"}]
    errors = []

    def add(prop, message):
        errors.append({"property": prop, "message": message})

    for key in sorted(set(body) - set(SETTINGS)):
        add(key, f"unknown setting, expected one of {sorted(SETTINGS)}")
    if not _url_ok(body.get("issuer")):
        add("issuer", "must be the provider's https issuer URL (http is allowed only on localhost)")
    if not (isinstance(body.get("client_id"), str) and 0 < len(body["client_id"]) <= 200 and body["client_id"].isprintable()):
        add("client_id", "must be the client ID the provider issued")
    secret = body.get("client_secret")
    if secret is not None and not (isinstance(secret, str) and len(secret) <= 500 and secret.isprintable()):
        add("client_secret", "must be text of up to 500 characters, or leave it out for a PKCE-only provider")
    if not _url_ok(body.get("redirect_uri")):
        add("redirect_uri", "must be this server's https callback URL (http is allowed only on localhost)")
    claim = body.get("groups_claim", "groups")
    if not (isinstance(claim, str) and 0 < len(claim) <= 100 and claim.isprintable()):
        add("groups_claim", "must be the claim name that carries the provider groups")
    mapping = body.get("role_map", {})
    if not isinstance(mapping, dict):
        add("role_map", "must be an object mapping provider groups to DurianDB roles")
    else:
        for group, role in mapping.items():
            if not (isinstance(group, str) and 0 < len(group) <= 100 and group.isprintable()):
                add(f"role_map.{group}", "a provider group name must be text of 1 to 100 characters")
            if not (isinstance(role, str) and ROLE.match(role)):
                add(f"role_map.{group}", "must name a role using letters, digits, dot, dash or underscore")
    if not isinstance(body.get("auto_provision", False), bool):
        add("auto_provision", "must be true or false")
    return errors


# ---- RSA PKCS#1 v1.5 with SHA-256, written out so token checking needs no dependency

def rsa_pkcs1v15_sha256(n, e, signature, message):
    """True when `signature` is RSASSA-PKCS1-v1_5 over `message` with the public key (n, e)."""
    if not (isinstance(n, int) and isinstance(e, int) and n > 0 and e > 0) or not isinstance(signature, bytes):
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    k = (n.bit_length() + 7) // 8
    em = pow(s, e, n).to_bytes(k, "big")
    tail = SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if len(em) < len(tail) + 11 or em[:2] != b"\x00\x01":
        return False
    sep = len(em) - len(tail) - 1
    if em[2:sep] != b"\xff" * (sep - 2) or em[sep] != 0:
        return False
    return hmac.compare_digest(em[sep + 1:], tail)


def _jwk_parts(key):
    if not isinstance(key, dict) or key.get("kty") != "RSA":
        return None
    try:
        n = int.from_bytes(_b64d(key["n"]), "big")
        e = int.from_bytes(_b64d(key["e"]), "big")
    except (KeyError, TypeError, ValueError):
        return None
    return (n, e) if n > 0 and e > 0 else None


def local_identity(store, cfg, claims):
    """The local account name for a verified identity. Auto-provision only when configured and a group maps to a role."""
    name = claims.get("preferred_username")
    if not (isinstance(name, str) and USER_NAME.match(name)):
        raise OIDCError("the provider did not give a user name DurianDB can use (preferred_username)", 403)
    if store.user(name):
        return name
    if not cfg.get("auto_provision"):
        raise OIDCError(f"there is no DurianDB account named {name}; ask an administrator to create one", 403)
    groups = claims.get(cfg.get("groups_claim", "groups"))
    groups = groups if isinstance(groups, list) else [groups] if isinstance(groups, str) else []
    mapping = cfg.get("role_map") or {}
    roles = sorted({mapping[g] for g in groups if isinstance(g, str) and g in mapping})
    if not roles:
        raise OIDCError("the provider did not place this account in a group that maps to a DurianDB role", 403)
    from .auth import hash_password  # local import keeps this module free of an import cycle at load time
    store.add_user(name, hash_password(secrets.token_urlsafe(32)), roles)
    return name


class OIDC:
    """Discovery cache, pending sign-ins and ID-token verification. One instance per App, so a pending sign-in is
    process-local: the provider's callback must return to the process that started it. Sessions, lockouts, the TOTP
    replay guard and confirmations are shared in the store (TECHNICAL_DEBT A9)."""

    def __init__(self, timeout=TIMEOUT, clock=time.time):
        self.timeout, self.clock = timeout, clock
        self._discovery = {}  # issuer -> (fetched_at, document)
        self._jwks = {}  # jwks_uri -> (fetched_at, keys)
        self.pending = {}  # state -> (nonce, verifier, expires)
        self.used_nonces = {}  # nonce -> expires

    # ---- HTTP, always translated into a sentence
    def _get_json(self, url):
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "DurianDB"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    @staticmethod
    def _reason(ex):
        if isinstance(ex, urllib.error.HTTPError):
            return f"HTTP {ex.code}"
        if isinstance(ex, urllib.error.URLError):
            return sentence(ex.reason) or "the connection failed"
        return "the provider did not answer"

    def discovery(self, cfg):
        issuer = cfg["issuer"].rstrip("/")
        cached = self._discovery.get(issuer)
        now = self.clock()
        if cached and now - cached[0] < DISCOVERY_TTL:
            return cached[1]
        try:
            doc = self._get_json(issuer + "/.well-known/openid-configuration")
        except Exception as ex:  # unreachable, TLS, bad JSON: all the same sentence
            if cached:  # keep using the last good document; a later call tries again
                return cached[1]
            raise OIDCError(f"the sign-in provider at {_host(issuer)} could not be reached: {self._reason(ex)}", 502)
        if not isinstance(doc, dict) or not isinstance(doc.get("issuer"), str) or doc["issuer"].rstrip("/") != issuer:
            raise OIDCError("the sign-in provider returned a discovery document for a different issuer", 502)
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not _url_ok(doc.get(key)):
                raise OIDCError(f"the sign-in provider's discovery document has no usable {key}", 502)
        self._discovery[issuer] = (now, doc)
        return doc

    def _keys(self, uri, refresh=False):
        cached = self._jwks.get(uri)
        now = self.clock()
        if cached and not refresh and now - cached[0] < JWKS_TTL:
            return cached[1]
        try:
            doc = self._get_json(uri)
        except Exception as ex:
            raise OIDCError(f"the sign-in provider's key set could not be fetched: {self._reason(ex)}", 502)
        keys = doc.get("keys") if isinstance(doc, dict) else None
        if not isinstance(keys, list):
            raise OIDCError("the sign-in provider's key set was not a list of keys", 502)
        self._jwks[uri] = (now, keys)
        return keys

    # ---- authorization-code flow with PKCE
    def _prune(self, now):
        for state in [s for s, v in self.pending.items() if v[2] <= now]:
            del self.pending[state]
        for nonce in [n for n, exp in self.used_nonces.items() if exp <= now]:
            del self.used_nonces[nonce]
        excess = len(self.pending) - STATE_MAX
        if excess > 0:  # dict order is insertion order: a flood of started sign-ins cannot exhaust memory
            for state in list(self.pending)[:excess]:
                del self.pending[state]

    def authorize_url(self, cfg):
        """The 302 target. The state, nonce and verifier are generated here and held server-side, single use."""
        doc = self.discovery(cfg)
        now = self.clock()
        self._prune(now)
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        self.pending[state] = (nonce, verifier, now + STATE_SECONDS)
        challenge = _b64e(hashlib.sha256(verifier.encode()).digest())
        query = {"response_type": "code", "client_id": cfg["client_id"], "redirect_uri": cfg["redirect_uri"],
                 "scope": "openid email profile", "state": state, "nonce": nonce,
                 "code_challenge": challenge, "code_challenge_method": "S256"}
        endpoint = doc["authorization_endpoint"]
        return endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode(query)

    def exchange(self, cfg, code, verifier):
        """Swap the code for an ID token. Only called after the state has been checked and spent."""
        doc = self.discovery(cfg)
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": cfg["redirect_uri"],
                "client_id": cfg["client_id"], "code_verifier": verifier}
        if cfg.get("client_secret"):
            form["client_secret"] = cfg["client_secret"]
        req = urllib.request.Request(doc["token_endpoint"], data=urllib.parse.urlencode(form).encode(),
                                     headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded",
                                              "User-Agent": "DurianDB"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as ex:
            detail = None
            try:
                detail = json.loads(ex.read()).get("error")
            except (ValueError, AttributeError):
                pass
            raise OIDCError("the provider refused the sign-in code" + (f": {detail}" if isinstance(detail, str) else ""), 502)
        except Exception as ex:
            raise OIDCError(f"the sign-in provider's token endpoint could not be reached: {self._reason(ex)}", 502)
        try:
            doc2 = json.loads(raw)
        except ValueError:
            raise OIDCError("the sign-in provider's token response was not JSON", 502)
        if isinstance(doc2, dict) and doc2.get("error"):
            raise OIDCError(f"the provider refused the sign-in code: {doc2['error']}", 502)
        token = doc2.get("id_token") if isinstance(doc2, dict) else None
        if not isinstance(token, str):
            raise OIDCError("the sign-in provider did not return an ID token", 502)
        return token

    def complete(self, cfg, code, state, error=None):
        """State is checked and spent before the token endpoint is touched, as it must be."""
        now = self.clock()
        if not isinstance(state, str) or not state:
            raise OIDCError("this sign-in response has no state; start the sign-in again", 400)
        entry = self.pending.pop(state, None)
        if entry is None or entry[2] <= now:
            raise OIDCError("that sign-in link has expired or was already used; start again", 400)
        if error:
            raise OIDCError(f"the provider did not complete the sign-in: {str(error)[:100]}", 400)
        if not (isinstance(code, str) and 0 < len(code) <= 4000):
            raise OIDCError("the sign-in provider did not return a code", 400)
        nonce, verifier, _ = entry
        return self.verify(cfg, self.exchange(cfg, code, verifier), nonce)

    # ---- ID-token verification, by hand
    def _parse(self, token):
        parts = token.split(".") if isinstance(token, str) else []
        if len(parts) != 3:
            raise OIDCError("the sign-in response is not a compact JWS token", 401)
        try:
            header, claims = json.loads(_b64d(parts[0])), json.loads(_b64d(parts[1]))
            signature = _b64d(parts[2])
        except (ValueError, TypeError):
            raise OIDCError("the sign-in response is not a readable token", 401)
        if not (isinstance(header, dict) and isinstance(claims, dict)):
            raise OIDCError("the sign-in response is not a readable token", 401)
        return header, claims, (parts[0] + "." + parts[1]).encode(), signature

    def _key(self, uri, kid):
        keys = self._keys(uri)
        match = [k for k in keys if isinstance(k, dict) and k.get("kid") == kid and k.get("kty") == "RSA"]
        if not match:  # maybe the provider rotated its keys: re-fetch once
            match = [k for k in self._keys(uri, refresh=True) if isinstance(k, dict) and k.get("kid") == kid and k.get("kty") == "RSA"]
        if not match:
            raise OIDCError("the sign-in provider used a signing key DurianDB does not know", 401)
        parts = _jwk_parts(match[0])
        if parts is None:
            raise OIDCError("the sign-in provider's signing key is not a usable RSA key", 401)
        return parts

    def verify(self, cfg, token, nonce):
        """Every refusal is a sentence. Checks the signature first, then the claims, then marks the nonce spent."""
        now = self.clock()
        doc = self.discovery(cfg)
        header, claims, signing_input, signature = self._parse(token)
        if header.get("alg") != "RS256":
            raise OIDCError("the provider signed the sign-in with an algorithm DurianDB does not accept (RS256 is required)", 401)
        n, e = self._key(doc["jwks_uri"], header.get("kid"))
        if not rsa_pkcs1v15_sha256(n, e, signature, signing_input):
            raise OIDCError("the sign-in response's signature is not valid", 401)
        if claims.get("iss") != doc["issuer"]:
            raise OIDCError("the sign-in response is from a different issuer", 401)
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if cfg["client_id"] not in audiences:
            raise OIDCError("the sign-in response was issued for a different client", 401)
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or isinstance(exp, bool):
            raise OIDCError("the sign-in response has no expiry", 401)
        if now > exp + CLOCK_SKEW:
            raise OIDCError("the sign-in response has expired; start again", 401)
        got = claims.get("nonce")
        if not (isinstance(got, str) and hmac.compare_digest(got.encode("utf-8"), nonce.encode("utf-8"))):
            raise OIDCError("the sign-in response does not match this sign-in request", 401)
        if got in self.used_nonces:
            raise OIDCError("that sign-in response has already been used", 401)
        email = claims.get("email")
        if email is not None:
            supported = "email_verified" in (doc.get("claims_supported") or [])
            if claims.get("email_verified") is not True and (claims.get("email_verified") is False or supported):
                raise OIDCError("the provider says this email address is not verified", 401)
        self._prune(now)
        self.used_nonces[got] = now + STATE_SECONDS
        return claims
