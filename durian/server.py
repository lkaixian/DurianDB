"""HTTP shell around durian.app. Run: python -m durian.server

Cookies are HttpOnly and SameSite=Strict, and every state-changing request must carry
X-Requested-With: durian (CSRF). Serve behind HTTPS if it is ever reachable beyond this machine.
"""
import json
import logging
import os
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .app import ApiError, App
from .auth import AuthError
from .config import seed
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/dashboards.js": ("dashboards.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
CSP = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
COOKIE = "durian_session"
MAX_BODY = 1_000_000
log = logging.getLogger("durian")
app: App = None


class Handler(BaseHTTPRequestHandler):
    def send(self, code, payload, ctype="application/json", extra=()):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(data))), ("X-Content-Type-Options", "nosniff"),
                     ("Content-Security-Policy", CSP), ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"),
                     ("Cache-Control", "no-store"), *extra):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

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
        if method != "GET" and self.headers.get("X-Requested-With") != "durian":
            return self.send(403, {"error": "missing X-Requested-With header"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.send(400, {"error": "bad Content-Length"})
        if length > MAX_BODY:
            return self.send(413, {"error": "body too large"})
        body = None
        if length:
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                return self.send(400, {"error": "body must be JSON"})
        try:
            if (method, url.path) == ("POST", "/api/login"):
                token = app.login(body)
                ident = app.auth.identity(token)
                cookie = f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={app.auth.ttl}"
                return self.send(200, {"name": ident["name"], "roles": sorted(ident["roles"])}, extra=[("Set-Cookie", cookie)])
            if (method, url.path) == ("POST", "/api/logout"):
                app.auth.logout(self.token())
                return self.send(200, {"ok": True}, extra=[("Set-Cookie", f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")])
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            status, payload = app.handle(method, url.path, query, body, app.auth.identity(self.token()))
            self.send(status, payload)
        except AuthError as ex:
            self.send(ex.status, {"error": str(ex)})
        except ApiError as ex:
            self.send(ex.status, {"error": str(ex), **({"errors": ex.errors} if ex.errors else {})})
        except Exception:
            log.exception("unhandled error")
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


def main():
    global app
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    store = Store(os.environ.get("DURIAN_DB", str(ROOT / "durian.db")))
    seeded = seed(store, os.environ.get("DURIAN_MQTT_CREDS", ROOT / "lab" / "mqtt" / "creds.json"))
    app = App(store)
    pw = app.auth.bootstrap(os.environ.get("DURIAN_ADMIN_PASSWORD"))
    if pw:
        print(f"First run: log in as 'admin' with password {pw} (shown once; change it after logging in).")
    if seeded:
        print("Seeded demo configuration. Edit it under Config in the console.")
    threading.Thread(target=run_ticks, args=(float(os.environ.get("DURIAN_TICK", 2)),), daemon=True).start()
    port = int(os.environ.get("DURIAN_PORT", 8765))
    print(f"DurianDB on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
