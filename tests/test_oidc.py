"""Single sign-on through an OpenID Connect provider, against a fake provider on 127.0.0.1. Run: python tests/test_oidc.py

The RSA key is generated here (1024-bit, deliberately small: this is a test key, never a production one)
and is used by both the provider's signing code and the module's hand-written verifier.
"""
import base64
import hashlib
import http.client
import json
import logging
import random
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian import server as server_mod  # noqa: E402
from durian.oidc import OIDC, OIDCError, rsa_pkcs1v15_sha256, validate_sso  # noqa: E402
from _support import new_store  # noqa: E402

DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
SMALL_PRIMES = (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97)


# ---- a 1024-bit RSA key, generated deterministically so the suite is reproducible and fast (test only)
def _prime(bits, rng):
    while True:
        n = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if any(n % p == 0 for p in SMALL_PRIMES):
            continue
        d, s = n - 1, 0
        while d % 2 == 0:
            d, s = d // 2, s + 1
        for _ in range(16):
            a = rng.randrange(2, n - 1)
            x = pow(a, d, n)
            if x in (1, n - 1):
                continue
            for _ in range(s - 1):
                x = x * x % n
                if x == n - 1:
                    break
            else:
                break  # composite
        else:
            return n


def _generate_key(bits=1024):
    rng, e = random.Random(20261027), 65537
    while True:
        p, q = _prime(bits // 2, rng), _prime(bits // 2, rng)
        if p == q or (p * q).bit_length() != bits:
            continue
        phi = (p - 1) * (q - 1)
        if phi % e:
            return {"n": p * q, "e": e, "d": pow(e, -1, phi)}


def _sign(key, message):
    k = (key["n"].bit_length() + 7) // 8
    tail = DIGEST_INFO + hashlib.sha256(message).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(tail) - 3) + b"\x00" + tail
    return pow(int.from_bytes(em, "big"), key["d"], key["n"]).to_bytes(k, "big")


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt(header, claims, key):
    head, body = _b64(json.dumps(header).encode()), _b64(json.dumps(claims).encode())
    return f"{head}.{body}.{_b64(_sign(key, f'{head}.{body}'.encode()))}"


# ---- fake provider
class Provider(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path, base = urlparse(self.path).path, self.server.base
        if path == "/.well-known/openid-configuration":
            return self._send(200, {"issuer": base, "authorization_endpoint": base + "/authorize",
                                    "token_endpoint": base + "/token", "jwks_uri": base + "/jwks",
                                    "claims_supported": ["iss", "aud", "exp", "nonce", "preferred_username",
                                                         "email", "email_verified", "groups"]})
        if path == "/jwks":
            key = self.server.key
            size = (key["n"].bit_length() + 7) // 8
            return self._send(200, {"keys": [{"kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256",
                                              "n": _b64(key["n"].to_bytes(size, "big")),
                                              "e": _b64(key["e"].to_bytes(3, "big"))}]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/token":
            return self._send(404, {"error": "not found"})
        self.server.token_hits += 1
        length = int(self.headers.get("Content-Length") or 0)
        self.server.last_form = parse_qs(self.rfile.read(length).decode())
        self._send(200, {"id_token": self.server.id_token, "token_type": "Bearer"})

    def log_message(self, *a):
        pass


def _provider(key):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    srv.base, srv.key, srv.id_token, srv.token_hits, srv.last_form = f"http://127.0.0.1:{srv.server_port}", key, "", 0, {}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _begin(app):
    q = parse_qs(urlparse(app.sso_start()).query)
    assert q["code_challenge_method"] == ["S256"] and q["response_type"] == ["code"]
    return q["state"][0], q["nonce"][0], q["code_challenge"][0]


def _claims(srv, nonce, **over):
    claims = {"iss": srv.base, "aud": "durian-client", "exp": time.time() + 300, "iat": time.time(),
              "nonce": nonce, "preferred_username": "operator", "email": "operator@example.com",
              "email_verified": True, "groups": ["operators"]}
    claims.update(over)
    return claims


def _token(srv, key, nonce, **over):
    return _jwt({"alg": "RS256", "typ": "JWT", "kid": "k1"}, _claims(srv, nonce, **over), key)


def _refused(fn, status, words):
    try:
        fn()
    except (OIDCError, ApiError) as ex:
        assert ex.status == status, (ex.status, status, str(ex))
        assert words in str(ex), (words, str(ex))
        return
    raise AssertionError(f"not refused, expected {words}")


# ==================================================================== config validation
good = {"issuer": "https://login.example.com/tenant", "client_id": "durian", "redirect_uri": "https://db.example.com/api/auth/sso/callback",
        "groups_claim": "groups", "role_map": {"ops": "user"}, "auto_provision": False}
assert validate_sso(good) == []
assert validate_sso({"issuer": "http://example.com", "client_id": "x", "redirect_uri": "https://db/cb"})[0]["property"] == "issuer"
assert validate_sso({"issuer": "http://127.0.0.1:9000", "client_id": "x", "redirect_uri": "http://localhost:9000/cb"}) == []
assert any(e["property"] == "redirect_uri" for e in validate_sso({**good, "redirect_uri": "http://example.com/cb"}))
assert any(e["property"] == "client_id" for e in validate_sso({**good, "client_id": ""}))
assert any(e["property"] == "auto_provision" for e in validate_sso({**good, "auto_provision": "yes"}))
assert any(e["property"] == "role_map.ops" for e in validate_sso({**good, "role_map": {"ops": "not a role"}}))
assert any(e["property"] == "surprise" for e in validate_sso({**good, "surprise": 1}))
assert validate_sso("not an object")[0]["property"] == "body"

# ---- the RSA verifier itself: good signature, changed message, changed key, rubbish signature
key = _generate_key()
assert key["n"].bit_length() == 1024  # test key: small on purpose, generated above
message = b"header.payload"
sig = _sign(key, message)
assert rsa_pkcs1v15_sha256(key["n"], key["e"], sig, message)
assert not rsa_pkcs1v15_sha256(key["n"], key["e"], sig, b"header.payload!")
assert not rsa_pkcs1v15_sha256(key["n"], key["e"], sig[:-1] + bytes([sig[-1] ^ 1]), message)
assert not rsa_pkcs1v15_sha256(key["n"], key["e"] + 2, sig, message)
assert not rsa_pkcs1v15_sha256(key["n"], key["e"], b"", message)

# ==================================================================== the flow
srv = _provider(key)
try:
    store = new_store()
    app = App(store)
    app.auth.bootstrap("admin-password-123")
    store.add_user("operator", hash_password("operator-password-1"), ["user"])
    cfg = {"issuer": srv.base, "client_id": "durian-client",
           "redirect_uri": srv.base + "/api/auth/sso/callback",
           "groups_claim": "groups", "role_map": {"operators": "user"}, "auto_provision": False}
    store.set_setting("sso", cfg)
    assert app.sso_public() == {"configured": True, "issuer": srv.base}
    assert store.get_setting("sso")["client_id"] == "durian-client"
    admin = {"name": "admin", "roles": {"superadmin"}}
    try:
        app.handle("PUT", "/api/auth/sso", {}, {**cfg, "issuer": "http://example.com"}, admin)
        raise AssertionError("a non-localhost http issuer was accepted")
    except ApiError as ex:
        assert ex.status == 422 and ex.errors[0]["property"] == "issuer"
    assert app.handle("PUT", "/api/auth/sso", {}, cfg, admin)[0] == 200
    assert "secret" not in json.dumps(app.handle("GET", "/api/auth/sso", {}, None, {"name": None, "roles": {"anonymous"}})[1])

    # ---- a full sign-in sets a session with the local account's roles
    state, nonce, challenge = _begin(app)
    verifier = app.oidc.pending[state][1]
    assert challenge == _b64(hashlib.sha256(verifier.encode()).digest())
    assert app.oidc.pending[state][0] == nonce
    srv.id_token = _token(srv, key, nonce)
    session = app.sso_callback({"code": "good", "state": state})
    ident = app.auth.identity(session)
    assert ident["name"] == "operator" and "user" in ident["roles"] and "superadmin" not in ident["roles"]
    assert srv.last_form["code_verifier"] == [verifier] and srv.last_form["redirect_uri"] == [cfg["redirect_uri"]]
    assert srv.last_form["grant_type"] == ["authorization_code"]

    # the audit trail names the mapped user and the provider, never the state, nonce or code
    row = next(r for r in store.audit_log() if r["action"] == "login.sso")
    assert row["user"] == "operator" and row["detail"] == f"provider {srv.base}"
    trail = json.dumps(store.audit_log())
    assert state not in trail and nonce not in trail and "good" not in row["detail"]

    # ---- a replayed state is refused, and the token endpoint is not touched again
    before = srv.token_hits
    _refused(lambda: app.sso_callback({"code": "again", "state": state}), 400, "expired or was already used")
    assert srv.token_hits == before

    # ---- a state that was never issued, or has gone stale, never reaches the provider either
    srv.id_token = _token(srv, key, "unused")
    _refused(lambda: app.sso_callback({"code": "x", "state": "made-up"}), 400, "expired or was already used")
    stale, stale_nonce, _ = _begin(app)
    app.oidc.pending[stale] = (stale_nonce, app.oidc.pending[stale][1], time.time() - 1)
    _refused(lambda: app.sso_callback({"code": "x", "state": stale}), 400, "expired or was already used")
    assert srv.token_hits == before

    # ---- refusals, each with its own sentence
    state, nonce2, _ = _begin(app)
    good_token = _jwt({"alg": "RS256", "typ": "JWT", "kid": "k1"}, _claims(srv, nonce2), key)
    srv.id_token = good_token[:-4] + ("AAAA" if not good_token.endswith("AAAA") else "BBBB")
    _refused(lambda: app.sso_callback({"code": "c", "state": state}), 401, "signature is not valid")

    def attempt(over=None, words="refused", status=401, header=None, nonce_override=None):
        state, this_nonce, _ = _begin(app)
        srv.id_token = _jwt(header or {"alg": "RS256", "typ": "JWT", "kid": "k1"},
                            _claims(srv, nonce_override or this_nonce, **(over or {})), key)
        _refused(lambda: app.sso_callback({"code": "c", "state": state}), status, words)

    attempt({"aud": "another-client"}, "different client")
    attempt({"exp": time.time() - 120}, "expired")
    attempt({"iss": "https://elsewhere.example"}, "different issuer")
    attempt({"email_verified": False}, "not verified")
    attempt(nonce_override="a-different-nonce", words="does not match")
    attempt(header={"alg": "HS256", "typ": "JWT", "kid": "k1"}, words="algorithm")
    attempt(header={"alg": "RS256", "typ": "JWT", "kid": "unknown"}, words="signing key")

    # ---- a nonce works once even when the state machinery is bypassed
    nonce3 = "nonce-used-once-3"
    once = _token(srv, key, nonce3)
    assert app.oidc.verify(cfg, once, nonce3)["preferred_username"] == "operator"
    _refused(lambda: app.oidc.verify(cfg, once, nonce3), 401, "already been used")

    # ---- auto-provision off: an unknown identity is refused
    state, nonce4, _ = _begin(app)
    srv.id_token = _token(srv, key, nonce4, preferred_username="stranger")
    _refused(lambda: app.sso_callback({"code": "c", "state": state}), 403, "no DurianDB account named stranger")

    # ---- a disabled local account cannot sign in through SSO
    store.add_user("blocked", hash_password("blocked-password-1"), ["user"])
    store.set_disabled("blocked", True)
    state, nonce5, _ = _begin(app)
    srv.id_token = _token(srv, key, nonce5, preferred_username="blocked")
    _refused(lambda: app.sso_callback({"code": "c", "state": state}), 403, "disabled")

    # ---- auto-provision on: a mapped group creates the account, and never superadmin unless mapped
    store.set_setting("sso", {**cfg, "auto_provision": True})
    state, nonce6, _ = _begin(app)
    srv.id_token = _token(srv, key, nonce6, preferred_username="newbie")
    session = app.sso_callback({"code": "c", "state": state})
    ident = app.auth.identity(session)
    assert ident["name"] == "newbie" and "user" in ident["roles"] and "superadmin" not in ident["roles"]
    assert store.user("newbie")["roles"] == ["user"]

    # a group that maps to nothing is still refused
    state, nonce7, _ = _begin(app)
    srv.id_token = _token(srv, key, nonce7, preferred_username="outsider", groups=["guests"])
    _refused(lambda: app.sso_callback({"code": "c", "state": state}), 403, "maps to a DurianDB role")

    # ---- neither the state nor the nonce reaches a log
    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = Capture()
    logging.getLogger().addHandler(handler)
    try:
        state, nonce8, _ = _begin(app)
        srv.id_token = _token(srv, key, nonce8)
        app.sso_callback({"code": "secret-code", "state": state})
    finally:
        logging.getLogger().removeHandler(handler)
    assert not any(state in line or nonce8 in line or "secret-code" in line for line in captured)

    # ---- the two routes: start is a 302 to the provider, callback is a 302 to / with the session cookie
    server_mod.app, server_mod.TLS = app, False
    web = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", web.server_port, timeout=5)
        conn.request("GET", "/api/auth/sso/start")
        r = conn.getresponse()
        location = r.getheader("Location")
        assert r.status == 302 and location.startswith(srv.base + "/authorize?") and "code_challenge_method=S256" in location
        state, nonce9, _ = _begin(app)
        srv.id_token = _token(srv, key, nonce9)
        conn.request("GET", "/api/auth/sso/callback?" + urlencode({"code": "route-code", "state": state}))
        r = conn.getresponse()
        cookie = r.getheader("Set-Cookie")
        assert r.status == 302 and r.getheader("Location") == "/" and cookie.startswith("durian_session=") and "HttpOnly" in cookie
        conn.close()
    finally:
        web.shutdown()
        web.server_close()

    # ---- a provider that cannot be reached is a sentence, not a traceback
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead = probe.getsockname()[1]
    probe.close()
    _refused(lambda: OIDC(timeout=1).discovery({"issuer": f"http://127.0.0.1:{dead}"}), 502, "could not be reached")

    # ---- the stored block can be removed, and then SSO is simply not offered
    assert app.handle("DELETE", "/api/auth/sso", {}, None,
                      {"name": "admin", "roles": {"superadmin"}})[0] == 200
    assert app.sso_public() == {"configured": False, "issuer": None}
    _refused(lambda: app.sso_start(), 404, "not configured")
finally:
    srv.shutdown()
    srv.server_close()

print("ok")
