"""HTTP shell around durian.app. Run: python -m durian.server

Cookies are HttpOnly and SameSite=Strict, and every state-changing request must carry
X-Requested-With: durian (CSRF). It listens on 127.0.0.1 only. To reach it from another machine, set
DURIAN_HOST=0.0.0.0 and DURIAN_TLS_CERT/DURIAN_TLS_KEY to a certificate and key; the server then serves
HTTPS only and sends HSTS. Plain HTTP off localhost is refused, so a password never crosses the network in clear.
"""
import json
import logging
import os
import re
import ssl
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .app import ApiError, App
from .auth import AuthError
from .config import seed
from .maintenance import Raw
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/dashboards.js": ("dashboards.js", "text/javascript; charset=utf-8"), "/admin.js": ("admin.js", "text/javascript; charset=utf-8"), "/forms.js": ("forms.js", "text/javascript; charset=utf-8"),
          "/account.js": ("account.js", "text/javascript; charset=utf-8"), "/overview.js": ("overview.js", "text/javascript; charset=utf-8"), "/point.js": ("point.js", "text/javascript; charset=utf-8"), "/history.js": ("history.js", "text/javascript; charset=utf-8"),
          "/alarms.js": ("alarms.js", "text/javascript; charset=utf-8"), "/reports.js": ("reports.js", "text/javascript; charset=utf-8"), "/config.js": ("config.js", "text/javascript; charset=utf-8"), "/users.js": ("users.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
CSP = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
COOKIE = "durian_session"
MAX_BODY, MAX_IMPORT = 1_000_000, 5_000_000
log = logging.getLogger("durian")
app: App = None
TLS = False  # set by main() when the server is given a certificate; the session cookie is then marked Secure


def route_shape(method, path):
    """Method plus the first two path segments: names a route, never the point or token in it (a counter must not
    become a way to guess data)."""
    return method + " /" + "/".join([p for p in path.split("/") if p][:2])


def session_cookie(token, max_age):
    """The session cookie. Secure under HTTPS, so a browser never returns it over plain HTTP."""
    return f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}" + ("; Secure" if TLS else "")


class Handler(BaseHTTPRequestHandler):
    def send(self, code, payload, ctype="application/json", extra=()):
        extra = list(extra)
        if isinstance(payload, Raw):  # a file download
            ctype = payload.ctype
            if payload.filename:
                extra.append(("Content-Disposition", 'attachment; filename="' + re.sub(r"[^A-Za-z0-9._-]", "_", payload.filename) + '"'))
            payload = payload.data
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(data))), ("X-Content-Type-Options", "nosniff"),
                     ("Content-Security-Policy", CSP), ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"),
                     ("Cache-Control", "no-store"), *([("Strict-Transport-Security", "max-age=31536000")] if TLS else []), *extra):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location, extra=()):
        """A 302 to the provider, or back to the console after a sign-in. No body, nothing cached."""
        self.send_response(302)
        for k, v in (("Location", location), ("Content-Length", "0"), ("Cache-Control", "no-store"),
                     ("Referrer-Policy", "no-referrer"), *extra):
            self.send_header(k, v)
        self.end_headers()

    def token(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c[COOKIE].value if COOKIE in c else None

    def serve(self, method):
        url = urlparse(self.path)
        if method == "GET" and url.path in STATIC:
            name, ctype = STATIC[url.path]
            return self.send(200, (WEB / name).read_bytes(), ctype)
        if not url.path.startswith("/api/"):
            return self.send(404, {"error": "not found"})
        auth = self.headers.get("Authorization", "")
        bearer = auth[7:].strip() if auth[:7].lower() == "bearer " else None
        if method != "GET" and not bearer and self.headers.get("X-Requested-With") != "durian":  # a token is not sent by browsers on their own
            return self.send(403, {"error": "missing X-Requested-With header"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.send(400, {"error": "bad Content-Length"})
        if length > (MAX_IMPORT if url.path.startswith("/api/import") else MAX_BODY):
            return self.send(413, {"error": "body too large"})
        body = None
        if length:
            raw = self.rfile.read(length)
            if self.headers.get("Content-Type", "").startswith("text/"):  # CSV uploads arrive as text
                try:
                    body = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    return self.send(400, {"error": "text must be UTF-8"})
            else:
                try:
                    body = json.loads(raw)
                except ValueError:
                    return self.send(400, {"error": "body must be JSON"})
        try:
            if (method, url.path) == ("POST", "/api/login"):
                token = app.login(body)
                ident = app.auth.identity(token)
                cookie = session_cookie(token, app.auth.ttl)
                return self.send(200, {"name": ident["name"], "roles": sorted(ident["roles"])}, extra=[("Set-Cookie", cookie)])
            if (method, url.path) == ("POST", "/api/reset"):
                app.reset_password(body)
                return self.send(200, {"ok": True})
            if (method, url.path) == ("POST", "/api/logout"):
                app.auth.logout(self.token())
                return self.send(200, {"ok": True}, extra=[("Set-Cookie", session_cookie("", 0))])
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if (method, url.path) == ("GET", "/api/auth/sso/start"):
                return self.redirect(app.sso_start())
            if (method, url.path) == ("GET", "/api/auth/sso/callback"):
                token = app.sso_callback(query)  # state is checked before the provider's token endpoint is called
                return self.redirect("/", extra=[("Set-Cookie", session_cookie(token, app.auth.ttl))])
            if bearer:  # a presented token is used or refused, never silently replaced by a cookie session
                ident = app.auth.token_identity(bearer)
                if ident is None:
                    return self.send(401, {"error": "invalid, expired or disabled token"}, extra=[("WWW-Authenticate", "Bearer")])
            else:
                ident = app.auth.identity(self.token())
            status, payload = app.handle(method, url.path, query, body, ident)
            self.send(status, payload)
        except ApiError as ex:  # includes AuthError: a credential failure already speaks the same language
            self.send(ex.status, {"error": str(ex), **({"errors": ex.errors} if ex.errors else {})})
        except Exception as ex:
            log.exception("unhandled error")
            app.record_internal_error(route_shape(method, url.path), ex)
            self.send(500, {"error": "internal error"})

    do_GET = lambda self: self.serve("GET")
    do_POST = lambda self: self.serve("POST")
    do_PUT = lambda self: self.serve("PUT")
    do_DELETE = lambda self: self.serve("DELETE")

    def log_message(self, *a):
        pass


def run_ticks(seconds):
    while True:
        try:
            app.tick()
        except Exception:
            log.exception("tick failed")
        time.sleep(seconds)


def run_maintenance():
    while True:  # purge and backup run on their schedule, once a day each, checked every minute
        time.sleep(60)
        try:
            app.maint.tick()
            app.auth.sweep()
            app.send_reports()
            app.escalate()
        except Exception:
            log.exception("maintenance failed")


def make_server(host, port, cert=None, key=None):
    """The console server, bound and listening. With a certificate its socket speaks TLS 1.2 or newer only."""
    httpd = ThreadingHTTPServer((host, port), Handler)
    if cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return httpd


def main():
    global app, TLS
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    host = os.environ.get("DURIAN_HOST", "127.0.0.1")
    cert, key = os.environ.get("DURIAN_TLS_CERT"), os.environ.get("DURIAN_TLS_KEY")
    if cert and not key:
        raise SystemExit("DURIAN_TLS_CERT needs DURIAN_TLS_KEY")
    TLS = bool(cert)
    store = Store(os.environ.get("DURIAN_DB", str(ROOT / "durian.db")))
    seeded = seed(store, os.environ.get("DURIAN_MQTT_CREDS", ROOT / "lab" / "mqtt" / "creds.json"))
    app = App(store, ROOT / "backups")
    try:
        pw = app.auth.bootstrap(os.environ.get("DURIAN_ADMIN_PASSWORD"))
    except AuthError as ex:  # a weak DURIAN_ADMIN_PASSWORD is a sentence, not a stack trace
        raise SystemExit(f"cannot create the first administrator: {ex}")
    if pw:
        print(f"First run: log in as 'admin' with password {pw} (shown once; change it after logging in).")
    if seeded:
        print("Seeded demo configuration. Edit it under Config in the console.")
    threading.Thread(target=run_ticks, args=(float(os.environ.get("DURIAN_TICK", 2)),), daemon=True).start()
    threading.Thread(target=run_maintenance, daemon=True).start()
    port = int(os.environ.get("DURIAN_PORT", 8765))
    httpd = make_server(host, port, cert, key)
    scheme = "https" if cert else "http"
    if not cert and host not in ("127.0.0.1", "::1", "localhost"):
        raise SystemExit(f"Refusing to serve plain HTTP on {host}: set DURIAN_TLS_CERT and DURIAN_TLS_KEY, or use 127.0.0.1.")
    print(f"DurianDB on {scheme}://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
