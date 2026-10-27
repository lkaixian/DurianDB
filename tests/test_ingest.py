"""Machine tokens and HTTP ingest, at the API level and through the real HTTP server. Run: python tests/test_ingest.py"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import server  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402
from durian.config import seed  # noqa: E402
from durian.ingest import parse_payload, parse_time  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
app = App(store)
app.auth.bootstrap("admin-password-123")
store.add_user("viewer", hash_password("viewer-password-1"), ["user"])
store.add_user("ops", hash_password("ops-password-1234"), ["ops"])
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))
viewer = app.auth.identity(app.auth.login("viewer", "viewer-password-1"))


def call(who, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, who)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


put = lambda kind, name, body: call(admin, "PUT", f"/api/config/{kind}/{name}", body)
props = lambda r: {e["property"] for e in r[1]["errors"]}

# ---- parsing
assert parse_time(1700000000) == 1700000000 and parse_time(1700000000500) == 1700000000.5 and parse_time("1700000000") == 1700000000
assert parse_time("2023-11-14T22:13:20Z") == 1700000000 and parse_time("2023-11-14T22:13:20") == 1700000000
for bad in (True, None, "yesterday", [1]):
    try:
        parse_time(bad)
        raise AssertionError(bad)
    except ValueError:
        pass
assert parse_payload({"point": "a", "value": 1, "ts": 5}, {}) == [("a", 1, 5, 0, None)]
assert parse_payload({"values": [{"point": "a", "value": 1}, {"point": "b", "value": 2}]}, {})[1] == ("b", 2, None, 1, None)
assert parse_payload({"a": 1, "b": 2}, {}) == [("a", 1, None, 0, None), ("b", 2, None, 1, None)]
assert parse_payload(None, {"point": "a", "value": "3.5", "ts": "9"}) == [("a", "3.5", "9", 0, None)]
for bad in (None, {}, [], "x", {"values": "x"}, {"values": [1]}, {"values": []}, {"values": [{"point": "a", "value": 1}] * 1001}):
    try:
        parse_payload(bad, {})
        raise AssertionError(bad)
    except ValueError:
        pass

# ---- tokens: validation, shown once, only a hash is stored
assert put("source", "nodered", {"type": "http_in", "read_roles": ["user"]})[0] == 200
for n in ("temp", "level"):
    assert put("point", n, {"source": "nodered", "unit": "C"})[0] == 200
assert "settable" in props(put("point", "bad", {"source": "nodered", "settable": True}))
assert "auto_create" in props(put("source", "x", {"type": "http_in", "auto_create": "yes"}))
r = call(admin, "POST", "/api/tokens", {"name": "t", "scope": "nope", "owner": "ghost", "days": 0})
assert r[0] == 422 and {"scope", "owner", "days"} <= props(r)
assert "source" in props(call(admin, "POST", "/api/tokens", {"name": "t", "scope": "ingest"}))
assert "source" in props(call(admin, "POST", "/api/tokens", {"name": "t", "scope": "ingest", "source": "demo"}))        # not an http_in source
assert "source" in props(call(admin, "POST", "/api/tokens", {"name": "t", "scope": "read", "source": "nodered"}))
assert call(viewer, "POST", "/api/tokens", {"name": "t", "scope": "read"})[0] == 403
s, r = call(admin, "POST", "/api/tokens", {"name": "flow-in", "scope": "ingest", "source": "nodered"})
ING = r["token"]
assert s == 201 and ING.startswith("dtk_") and len(ING) > 40 and "shown once" in r["note"]
assert call(admin, "POST", "/api/tokens", {"name": "flow-in", "scope": "read"})[0] == 409
READ = call(admin, "POST", "/api/tokens", {"name": "flow-read", "scope": "read", "owner": "viewer"})[1]["token"]
WRITE = call(admin, "POST", "/api/tokens", {"name": "flow-write", "scope": "write", "owner": "viewer"})[1]["token"]
ADMINREAD = call(admin, "POST", "/api/tokens", {"name": "admin-read", "scope": "read"})[1]["token"]
SHORT = call(admin, "POST", "/api/tokens", {"name": "short", "scope": "read", "days": 1})[1]["token"]
listing = call(admin, "GET", "/api/tokens")[1]
assert {t["name"] for t in listing} == {"flow-in", "flow-read", "flow-write", "admin-read", "short"} and all("token" not in t and "token_hash" not in t for t in listing)
assert not any(ING in json.dumps(row) for row in store.q("select * from api_tokens"))                                     # only a hash is stored
assert not any(ING in json.dumps(a) for a in store.audit_log(50))                                                      # and the audit log never sees it
tok = lambda t: app.auth.token_identity(t)
assert tok(ING)["scope"] == "ingest" and tok(ING)["source"] == "nodered" and tok(READ)["name"] == "viewer" and tok("dtk_nonsense") is None and tok("x") is None and tok(None) is None
assert tok(SHORT) and not (lambda: (store.run("update api_tokens set expires=? where name='short'", (time.time() - 1,)), tok(SHORT))[1])()  # expired

# ---- scopes: what each kind of token may do
I, R, W, AR = tok(ING), tok(READ), tok(WRITE), tok(ADMINREAD)
assert call(I, "GET", "/api/points")[0] == 403 and call(I, "GET", "/api/me")[0] == 403 and call(I, "POST", "/api/ingest/demo", {"a": 1})[0] == 403  # one source only
assert call(R, "GET", "/api/points")[0] == 200 and call(R, "GET", "/api/me")[1]["name"] == "viewer"
assert call(R, "POST", "/api/points/pump-setpoint/set", {"value": 1})[0] == 403 and call(R, "POST", "/api/ingest/nodered", {"temp": 1})[0] == 403
assert call(R, "PUT", "/api/config/point/x", {})[0] == 403 and call(AR, "GET", "/api/config/point")[0] == 403 and call(AR, "GET", "/api/users")[0] == 403  # an admin's read token is still only read
assert call(AR, "GET", "/api/audit")[0] == 403 and call(AR, "POST", "/api/tokens", {"name": "z", "scope": "read"})[0] == 403 and call(AR, "GET", "/api/database")[0] == 403
assert call(W, "POST", "/api/points/pump-setpoint/set", {"value": 61})[0] == 200
assert any("via token flow-write" in a["detail"] for a in store.audit_log(5))
assert call(W, "POST", "/api/points/boiler-temp/set", {"value": 1})[0] == 403                                          # not settable: owner's rules still apply
assert call(W, "POST", "/api/ingest/nodered", {"temp": 1})[0] == 403 and call(W, "DELETE", "/api/dashboards/plant-overview")[0] == 403
assert call(admin, "POST", "/api/ingest/nodered", {"temp": 1})[0] == 403                                               # a browser session cannot push values

# ---- ingest: every body shape, with the reply saying what happened
now = time.time()
ing = lambda body=None, who=I, method="POST", **q: call(who, method, "/api/ingest/nodered", body, **q)
s, r = ing({"point": "temp", "value": 21.5}); assert s == 200 and r == {"accepted": 1, "rejected": []}
batch = [{"point": "temp", "value": 22.0, "ts": now + 5}, {"point": "level", "value": "7.5"}, {"point": "ghost", "value": 1},
         {"point": "temp", "value": "abc"}, {"point": "level", "value": float("inf")}, {"point": "bad name", "value": 1},
         {"point": "level", "value": True, "ts": now + 3600}, {"point": "temp", "value": 5.0, "ts": now - 50}]
s, r = ing({"values": batch})
reasons = {x["index"]: x["reason"] for x in r["rejected"]}
assert s == 200 and r["accepted"] == 3 and set(reasons) == {2, 3, 4, 5, 6}, (r["accepted"], reasons)
assert "unknown point" in reasons[2] and "number" in reasons[3] and "finite" in reasons[4] and "name of 1 to 64" in reasons[5]
assert "future" in reasons[6]                                                                                           # an explicit old ts (index 7) is backfill: accepted, history only (tests/test_backfill.py:79)
s, r = ing({"temp": 23.0, "level": 8.0}); assert s == 200 and r["accepted"] == 2
s, r = ing({"point": "temp", "value": 1, "ts": now - 30 * 86400}); assert s == 422 and "older than this source accepts" in r["rejected"][0]["reason"]
s, r = ing({"point": "temp", "value": 99, "ts": 1}); assert s == 422                                                   # epoch-old is past the source's max age, refused
assert ing({"nothing": "useful", "values": 3})[0] == 400 and ing(None)[0] == 400 and ing([1, 2])[0] == 400
s, r = ing(None, method="GET", point="level", value="9.5"); assert s == 200 and r["accepted"] == 1                    # the GET form, for tools that can only GET
assert ing(None, method="GET")[0] == 400 and call(I, "GET", "/api/ingest/ghost", None, point="a", value="1")[0] == 403
app.tick(time.time() + 5)
v = {p["name"]: p["last"] for p in call(admin, "GET", "/api/points")[1]}
assert v["temp"] == 23.0 and v["level"] == 9.5, v
assert call(R, "GET", "/api/value/temp")[1]["value"] == 23.0 and call(R, "GET", "/api/value/ghost")[0] == 404
assert call(R, "GET", "/api/value/temp")[1]["quality"] == "good"
src = next(s for s in app.engine.sources if s.name == "nodered")
assert src.dropped >= 5 and call(admin, "GET", "/api/sources")[1][-1]["dropped"] >= 5

# source timestamps are kept, and several samples of one point in one request all reach the history
t0 = time.time() + 20
ing({"values": [{"point": "temp", "value": float(i), "ts": t0 + i} for i in range(1, 6)]})
app.tick(t0 + 10)
hist = store.q("select ts, value from history where point='temp' and ts >= ? order by ts", (t0,))
assert [h["value"] for h in hist] == [1.0, 2.0, 3.0, 4.0, 5.0] and abs(hist[0]["ts"] - (t0 + 1)) < 1e-6

# auto-create makes points on first sight, only with names that pass the check
assert put("source", "open", {"type": "http_in", "auto_create": True})[0] == 200
OPEN = call(admin, "POST", "/api/tokens", {"name": "open-in", "scope": "ingest", "source": "open"})[1]["token"]
r = call(tok(OPEN), "POST", "/api/ingest/open", {"values": [{"point": "fresh", "value": 1}, {"point": "../x", "value": 2}]})
assert r[1]["accepted"] == 1 and r[1]["rejected"][0]["index"] == 1
app.tick(time.time() + 30)
assert "fresh" in {p["name"] for p in call(admin, "GET", "/api/points")[1]}

# disabling and deleting a token, and the account it acts as
assert call(admin, "PUT", "/api/tokens/flow-in", {"disabled": True})[0] == 200 and tok(ING) is None
assert call(admin, "PUT", "/api/tokens/flow-in", {"disabled": False})[0] == 200 and tok(ING)
assert call(admin, "PUT", "/api/tokens/flow-in", {"disabled": "x"})[0] == 422 and call(admin, "PUT", "/api/tokens/nope", {"disabled": True})[0] == 404
assert call(admin, "DELETE", "/api/users/viewer")[0] == 200 and tok(READ) is None and tok(WRITE) is None                  # tokens do not outlive their owner
assert call(admin, "DELETE", "/api/tokens/flow-in")[0] == 200 and tok(ING) is None and call(admin, "DELETE", "/api/tokens/flow-in")[0] == 404

# ---- through the real HTTP server, the way Node-RED's http request node talks
server.app = app
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{httpd.server_port}"
NEW = call(admin, "POST", "/api/tokens", {"name": "http-in", "scope": "ingest", "source": "nodered"})[1]["token"]
NEWREAD = call(admin, "POST", "/api/tokens", {"name": "http-read", "scope": "read"})[1]["token"]


def http(method, path, body=None, token=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), e.headers


s, r, h = http("POST", "/api/ingest/nodered", {"point": "temp", "value": 31.5}, NEW)
assert s == 200 and r["accepted"] == 1                                                                                 # no X-Requested-With needed with a token
assert http("GET", "/api/ingest/nodered?point=level&value=4.25", token=NEW)[1]["accepted"] == 1                        # GET with query parameters
s, r, h = http("POST", "/api/ingest/nodered", {"point": "temp", "value": 1}, "dtk_wrong")
assert s == 401 and h["WWW-Authenticate"] == "Bearer"
assert http("POST", "/api/ingest/nodered", {"point": "temp", "value": 1})[0] == 403                                    # no token and no CSRF header: refused
assert http("POST", "/api/ingest/nodered", {"point": "temp", "value": 1}, headers={"X-Requested-With": "durian"})[0] == 401   # anonymous cannot push
assert http("GET", "/api/points", token=NEW)[0] == 403 and http("GET", "/api/value/temp", token=NEWREAD)[1]["name"] == "temp"
assert http("POST", "/api/ingest/nodered", token=NEW)[0] == 400                                                         # no body
req = urllib.request.Request(BASE + "/api/ingest/nodered", method="POST", data=b"not json", headers={"Authorization": "Bearer " + NEW})
try:
    urllib.request.urlopen(req, timeout=10)
    raise AssertionError("bad JSON must be refused")
except urllib.error.HTTPError as e:
    assert e.code == 400
httpd.shutdown()
print("ok")
