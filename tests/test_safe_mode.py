"""Safe mode: database, login and admin API only; no source or publisher starts. Run: python tests/test_safe_mode.py"""
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import server  # noqa: E402
from durian.app import App  # noqa: E402
from durian.config import build_engine  # noqa: E402
from durian import config  # noqa: E402
from _support import new_store  # noqa: E402

started = []


class Boom:  # a source whose start would crash or hang
    def __init__(self, *a, **k):
        started.append("built")

    def start(self):
        started.append("started")
        raise RuntimeError("would hang")


def serve(app):
    server.app = app
    httpd = server.make_server("127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def health(httpd, token):
    req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/health", headers={"Authorization": "Bearer " + token})
    import json
    return json.loads(urllib.request.urlopen(req).read())


store = new_store()
store.put("source", "bad", {"type": "http_json", "url": "http://127.0.0.1:9/x", "interval": 1})
config.HttpJsonSource = Boom
# normal mode builds and starts the bad source; safe mode must not even build it
App.safe = True
app = App(store)
app.auth.bootstrap("Safe-mode-pw-1")
assert started == [], started
assert app.engine.sources == [] and not app.engine.startable
app.reload()  # a config save also reloads
assert started == [] and app.engine.sources == []
tok = app.login({"username": "admin", "password": "Safe-mode-pw-1"})
ident = app.auth.identity(tok)
status, body = app.handle("GET", "/api/health", {}, None, ident)
assert status == 200 and body["safe"] is True, body
status, body = app.handle("GET", "/api/config/source", {}, None, ident)
assert status == 200, body
app.stop()
App.safe = False
try:
    build_engine(store)  # control: normal mode does touch the source
except Exception:
    pass
assert "built" in started
print("ok")
