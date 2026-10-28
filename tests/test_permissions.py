"""Every route the dispatcher serves, and who may call it. Run: python tests/test_permissions.py

The route list is parsed from App.handle and from every handler method named in App.DATA_ROUTES and
App.ADMIN_ROUTES, so a newly added branch fails this test until it has a permission opinion here, whichever
table or handler it was added to. Four caller shapes are checked for every route: anonymous, a plain user, a
custom role (ops) that the point's read_roles does not include, and a superadmin. Machine-token scopes are
checked separately: a scoped token must never reach an administrative route.

Some routes are public on purpose and the table says so: GET /api/me is what the console asks before sign-in,
and GET /api/health is the unauthenticated liveness probe tools/nodered_check.py waits on. List endpoints do
not 401 an anonymous caller; they return an empty list, because the product promise is "anonymous sees
nothing", and a point the caller may not read is reported as 404 so its existence is not leaked.
"""
import ast
import inspect
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ROUTE, ApiError, App  # noqa: E402
from durian.auth import hash_password, held  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

W = "*"            # any value
N = "<name>"       # any non-None path segment

# ---- the canonical shape of a dispatcher branch: (methods, a, b, c)
INGEST = (W, "ingest", N, W)
VALUE = ("GET", "value", N, None)
ME = ("GET", "me", None, None)
HEALTH = ("GET", "health", None, None)
JOBS = ("GET", "jobs", N, None)
POINTS = ("GET", "points", None, W)
FORECAST = ("GET", "points", N, "forecast")
BASELINE = ("GET", "points", N, "baseline")
DRIFT = ("GET", "points", N, "drift")
POINT = ("GET", "points", N, None)
NOTES = (("GET", "POST"), "notes", N, None)
REPORTS = ("GET", "reports", "summary", W)
SET_POINT = ("POST", "points", W, "set")
SERIES = ("GET", "series", W, W)
EXPORT_POINTS = ("GET", "export", "points", W)
IMPORT_POINTS = ("POST", "import", "points", W)
EXPORT_HISTORY = ("GET", "export", "history", W)
EXPORT_BUNDLE = ("GET", "export", "bundle", W)
EXPORT_MANIFEST = ("GET", "export", "manifest", W)
EVENTS_SEARCH = ("GET", "events", "search", None)
EVENTS = ("GET", "events", None, None)
TEST_SOURCE = ("POST", "test", "source", W)
ALARMS = (W, "alarms", W, W)
ACK_MANY = ("POST", "events", "ack-many", None)
ACK = ("POST", "events", W, "ack")
SOURCES = ("GET", "sources", None, W)
WATCHLISTS = ("GET", "watchlists", None, W)
MYLISTS = (("DELETE", "PUT"), "mylists", N, None)
DASHBOARDS = (W, "dashboards", W, None)
MFA = ("POST", "mfa", frozenset({"setup", "enable", "disable"}), None)
PASSWORD = ("POST", "password", None, W)
CONFIG = (W, "config", N, W)
USERS_RESET = ("POST", "users", N, "reset")
USERS = (W, "users", W, W)
OUTPUTS = ("GET", "outputs", None, W)
AUDIT = ("GET", "audit", W, W)
SSO = ("GET", "auth", "sso", None)
SSO_PUT = ("PUT", "auth", "sso", None)
SSO_DELETE = ("DELETE", "auth", "sso", None)
TOKENS = (W, "tokens", W, None)
DATABASE = (W, "database", W, W)
EXPORTS = ("GET", "database", "exports", None)
EXPORT_FILE = ("GET", "database", "exports", N)
EXPORT_CONFIG = ("GET", "export", None, W)
IMPORT_CONFIG = ("POST", "import", None, W)
SQL = ("POST", "sql", None, W)
IMPORT_HISTORY = ("POST", "import", "history", W)


# ---- parsing the dispatcher, so a route cannot be added without an entry below
def _const(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return frozenset(_const(e) for e in node.elts)
    return W  # a named non-empty set, e.g. KINDS


def _shape(test):
    """(methods, a, b, c) for one top-level `if` of a dispatcher function."""
    got = {}

    def walk(n):
        if isinstance(n, ast.BoolOp):
            for value in n.values:
                walk(value)
        elif isinstance(n, ast.Compare):
            left, comp = n.left, n.comparators[0]
            if isinstance(left, ast.Tuple) and isinstance(comp, ast.Tuple):
                for name, value in zip(left.elts, comp.elts):
                    if isinstance(name, ast.Name):
                        got[name.id] = _const(value)
            elif isinstance(left, ast.Name):
                if isinstance(n.ops[0], ast.Eq):
                    got[left.id] = _const(comp)
                elif isinstance(n.ops[0], ast.Is) and isinstance(comp, ast.Constant) and comp.value is None:
                    got[left.id] = None
                elif isinstance(n.ops[0], ast.In):
                    got[left.id] = _const(comp) if isinstance(comp, (ast.Tuple, ast.List, ast.Set)) else N
        elif isinstance(n, ast.Name) and n.id in ("b", "c"):  # a truthy path segment
            got[n.id] = N

    walk(test)
    methods = got.get("method", W)
    if isinstance(methods, frozenset):
        methods = tuple(sorted(methods))
    return (methods, got.get("a", W), got.get("b", W), got.get("c", W))


def _is_route_test(test):
    """A dispatcher branch compares the method tuple or `a`; a call that merely passes `method` is not one."""
    for node in ast.walk(test):
        if isinstance(node, ast.Compare):
            left = node.left
            if isinstance(left, ast.Tuple) and any(isinstance(e, ast.Name) and e.id == "method" for e in left.elts):
                return True
            if isinstance(left, ast.Name) and left.id in ("method", "a") and any(isinstance(op, ast.Eq) for op in node.ops):
                return True
    return False


def _shapes_in(source):
    """Every top-level branch of one dispatcher function that names a method or an `a` value."""
    fn = ast.parse(textwrap.dedent(source)).body[0]
    return {_shape(node.test) for node in fn.body if isinstance(node, ast.If) and _is_route_test(node.test)}


def dispatcher_shapes():
    """Every route shape App.handle itself or a handler it dispatches to serves."""
    shapes = _shapes_in(inspect.getsource(App.handle))
    for name in set(App.DATA_ROUTES.values()) | set(App.ADMIN_ROUTES.values()):
        shapes |= _shapes_in(inspect.getsource(getattr(App, name)))
    return shapes


def R(shape, method, path, body=None, *, anon, viewer, ops, admin, query=None, anon_empty=False):
    return {"shape": shape, "method": method, "path": path, "body": body, "query": query or {},
            "anon": anon, "viewer": viewer, "ops": ops, "admin": admin, "anon_empty": anon_empty}


# The seeded store: one source (demo), numeric points, one dashboard; then one text point, one HTTP-in
# source, one handler and one manual maintenance window, all created through the same admin routes.
store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
store.add_user("viewer", hash_password("viewer-password-1"), ["user"])
store.add_user("ops", hash_password("ops-password-1234"), ["ops"])

# Hand-built identities, exactly what Auth.identity/token_identity produce.
ANON = {"name": None, "roles": {"anonymous"}}
VIEWER = {"name": "viewer", "roles": {"anonymous", "user"}}
OPS = {"name": "ops", "roles": {"anonymous", "ops"}}  # a configured role, not inherited from `user`
ADMIN = {"name": "admin", "roles": {"anonymous", "user", "superadmin"}}
WHO = {"anon": ANON, "viewer": VIEWER, "ops": OPS, "admin": ADMIN}


def call(ident, method, path, body=None, query=None):
    try:
        return app.handle(method, path, query or {}, body, ident)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


def setup(method, path, body=None, **query):
    status, payload = call(ADMIN, method, path, body, query)
    assert status in (200, 201), (method, path, status, payload)
    return payload


setup("PUT", "/api/config/source/inbox", {"type": "http_in", "read_roles": ["user"]})
setup("PUT", "/api/config/point/status-text", {"source": "demo", "datatype": "text", "read_roles": ["ops"],
                                               "generator": {"kind": "constant", "value": "RUN"}})
setup("PUT", "/api/config/handler/hook", {"type": "webhook", "url": "https://example.test/hook"})
setup("PUT", "/api/config/maintenance/maint", {"mode": "suppress", "scope": {"points": ["boiler-temp"]},
                                               "schedule": {"type": "manual", "active": False}, "reason": "probe"})
setup("PUT", "/api/config/watchlist/line", {"tags": {"area": "line"}})
DASH = {"title": "Probe", "read_roles": ["user"], "widgets": [
    {"id": "a", "type": "value", "point": "boiler-temp", "x": 0, "y": 0, "w": 3, "h": 2}]}
setup("PUT", "/api/dashboards/probe-edit", DASH)
setup("PUT", "/api/dashboards/probe-dash", DASH)
setup("PUT", "/api/dashboards/status-board", {**DASH, "read_roles": ["ops"]})
JID = app.jobs.submit("permission probe", lambda: None, "admin")

ROWS = [
    # ingest: only a scoped ingest token pushes values, never a browser session
    R(INGEST, "POST", "/api/ingest/inbox", {"point": "probe", "value": 1},
      anon=401, viewer=403, ops=403, admin=403),
    # value / point detail: a point the caller may not read is 404, not an existence hint
    R(VALUE, "GET", "/api/value/boiler-temp", anon=404, viewer=200, ops=404, admin=200),
    R(VALUE, "GET", "/api/value/status-text", anon=404, viewer=404, ops=200, admin=200),
    R(POINT, "GET", "/api/points/boiler-temp", anon=404, viewer=200, ops=404, admin=200),
    R(POINT, "GET", "/api/points/status-text", anon=404, viewer=404, ops=200, admin=200),
    R(FORECAST, "GET", "/api/points/boiler-temp/forecast", anon=404, viewer=200, ops=404, admin=200),
    R(FORECAST, "GET", "/api/points/status-text/forecast", anon=404, viewer=404, ops=400, admin=400),
    R(BASELINE, "GET", "/api/points/boiler-temp/baseline", anon=404, viewer=200, ops=404, admin=200),
    R(DRIFT, "GET", "/api/points/boiler-temp/drift", anon=404, viewer=200, ops=404, admin=200),
    R(DRIFT, "GET", "/api/points/status-text/drift", anon=404, viewer=404, ops=400, admin=400),
    R(POINTS, "GET", "/api/points", anon=200, viewer=200, ops=200, admin=200, anon_empty=True),
    # me is asked before sign-in (the console needs the anonymous identity); health is the public liveness probe
    R(ME, "GET", "/api/me", anon=200, viewer=200, ops=200, admin=200),
    R(HEALTH, "GET", "/api/health", anon=200, viewer=200, ops=200, admin=200),
    # jobs: its owner or a superadmin, and never anonymous
    R(JOBS, "GET", f"/api/jobs/{JID}", anon=403, viewer=403, ops=403, admin=200),
    R(JOBS, "GET", "/api/jobs/999999", anon=404, viewer=404, ops=404, admin=404),
    # notes: read follows the point, writing needs the user role
    R(NOTES, "GET", "/api/notes/boiler-temp", anon=404, viewer=200, ops=404, admin=200),
    R(NOTES, "POST", "/api/notes/boiler-temp", {"text": "probe"}, anon=404, viewer=201, ops=404, admin=201),
    R(NOTES, "POST", "/api/notes/status-text", {"text": "probe"}, anon=404, viewer=404, ops=403, admin=201),
    # reports are filtered by the points the caller may read
    R(REPORTS, "GET", "/api/reports/summary", anon=200, viewer=200, ops=200, admin=200, anon_empty=True),
    # set point: visibility, then the point's set_roles
    R(SET_POINT, "POST", "/api/points/pump-setpoint/set", {"value": 41},
      anon=404, viewer=200, ops=404, admin=200),
    R(SET_POINT, "POST", "/api/points/boiler-temp/set", {"value": 41},
      anon=404, viewer=403, ops=404, admin=403),
    R(SERIES, "GET", "/api/series", anon=404, viewer=200, ops=404, admin=200, query={"point": "boiler-temp"}),
    R(SERIES, "GET", "/api/series", anon=404, viewer=404, ops=400, admin=400, query={"point": "status-text"}),
    # configuration exports and imports are superadmin only; history CSV follows point visibility
    R(EXPORT_POINTS, "GET", "/api/export/points", anon=401, viewer=403, ops=403, admin=200),
    R(IMPORT_POINTS, "POST", "/api/import/points", anon=401, viewer=403, ops=403, admin=400),
    R(EXPORT_HISTORY, "GET", "/api/export/history", anon=404, viewer=200, ops=404, admin=200,
      query={"point": "boiler-temp"}),
    R(EXPORT_BUNDLE, "GET", "/api/export/bundle", anon=401, viewer=403, ops=403, admin=200),
    R(EXPORT_MANIFEST, "GET", "/api/export/manifest", anon=401, viewer=403, ops=403, admin=200),
    # alarm journal and acknowledgements
    R(EVENTS_SEARCH, "GET", "/api/events/search", anon=200, viewer=200, ops=200, admin=200, anon_empty=True),
    R(EVENTS, "GET", "/api/events", anon=200, viewer=200, ops=200, admin=200, anon_empty=True),
    R(ACK_MANY, "POST", "/api/events/ack-many", {"ids": [1]}, anon=401, viewer=200, ops=403, admin=200),
    R(ACK, "POST", "/api/events/1/ack", anon=401, viewer=404, ops=403, admin=404),
    # the source test opens the connection the settings describe: admin only
    R(TEST_SOURCE, "POST", "/api/test/source", {"type": "virtual"},
      anon=401, viewer=403, ops=403, admin=200, query={"wait": "1"}),
    # shelving is for users, out-of-service for a superadmin; both follow point visibility
    R(ALARMS, "GET", "/api/alarms", anon=200, viewer=200, ops=200, admin=200, anon_empty=True),
    R(ALARMS, "GET", "/api/alarms/kpis", anon=200, viewer=200, ops=200, admin=200, anon_empty=True),
    R(ALARMS, "POST", "/api/alarms/boiler-temp/shelve", {"minutes": 5, "reason": "probe"},
      anon=401, viewer=200, ops=404, admin=200),
    R(ALARMS, "POST", "/api/alarms/status-text/shelve", {"minutes": 5, "reason": "probe"},
      anon=401, viewer=404, ops=403, admin=200),
    R(ALARMS, "POST", "/api/alarms/boiler-temp/out-of-service", {"reason": "probe"},
      anon=401, viewer=403, ops=404, admin=200),
    # sources and watch lists follow their read roles; personal lists belong to their owner
    R(SOURCES, "GET", "/api/sources", anon=200, viewer=200, ops=200, admin=200, anon_empty=True),
    R(WATCHLISTS, "GET", "/api/watchlists", anon=200, viewer=200, ops=200, admin=200, anon_empty=True),
    R(MYLISTS, "PUT", "/api/mylists/probe-{who}", {"points": ["boiler-temp"]},
      anon=401, viewer=200, ops=403, admin=200),
    R(MYLISTS, "DELETE", "/api/mylists/probe-{who}", anon=401, viewer=200, ops=403, admin=200),
    # dashboards: read follows read_roles, edit follows owner/edit_roles, create needs designer or superadmin
    R(DASHBOARDS, "GET", "/api/dashboards", anon=200, viewer=200, ops=200, admin=200, anon_empty=True),
    R(DASHBOARDS, "GET", "/api/dashboards/plant-overview", anon=404, viewer=200, ops=404, admin=200),
    R(DASHBOARDS, "GET", "/api/dashboards/status-board", anon=404, viewer=404, ops=200, admin=200),
    R(DASHBOARDS, "PUT", "/api/dashboards/probe-edit", DASH, anon=401, viewer=403, ops=404, admin=200),
    R(DASHBOARDS, "PUT", "/api/dashboards/made-by-{who}", DASH, anon=401, viewer=403, ops=403, admin=200),
    R(DASHBOARDS, "DELETE", "/api/dashboards/probe-dash", anon=401, viewer=403, ops=404, admin=200),
    # a second factor and a password change are for one's own account; anyone signed in may ask
    R(MFA, "POST", "/api/mfa/setup", anon=401, viewer=200, ops=200, admin=200),
    R(PASSWORD, "POST", "/api/password", {"old": "wrong", "new": "probe-password-1"},
      anon=401, viewer=403, ops=403, admin=403),
    # everything below here is administration: superadmin only
    R(CONFIG, "GET", "/api/config/point", anon=401, viewer=403, ops=403, admin=200),
    R(CONFIG, "GET", "/api/config/point/boiler-temp", anon=401, viewer=403, ops=403, admin=200),
    R(CONFIG, "PUT", "/api/config/watchlist/perm-{who}", {"tags": {"area": "line"}},
      anon=401, viewer=403, ops=403, admin=200),
    R(CONFIG, "DELETE", "/api/config/watchlist/perm-{who}", anon=401, viewer=403, ops=403, admin=200),
    R(USERS_RESET, "POST", "/api/users/viewer/reset", anon=401, viewer=403, ops=403, admin=200),
    R(USERS, "GET", "/api/users", anon=401, viewer=403, ops=403, admin=200),
    R(USERS, "PUT", "/api/users/probe-user", {"password": "probe-password-1", "roles": ["user"]},
      anon=401, viewer=403, ops=403, admin=200),
    R(USERS, "DELETE", "/api/users/probe-user", anon=401, viewer=403, ops=403, admin=200),
    R(OUTPUTS, "GET", "/api/outputs", anon=401, viewer=403, ops=403, admin=200),
    R(SQL, "POST", "/api/sql", {"query": "select 1"}, anon=401, viewer=403, ops=403, admin=200),
    R(AUDIT, "GET", "/api/audit", anon=401, viewer=403, ops=403, admin=200),
    # sign-in through a provider: the status is public (the sign-in page asks before anyone is signed in),
    # but configuring it is administration and the block carries a client secret
    R(SSO, "GET", "/api/auth/sso", anon=200, viewer=200, ops=200, admin=200),
    R(SSO_PUT, "PUT", "/api/auth/sso", {"issuer": "https://idp.example.test", "client_id": "durian",
                                        "redirect_uri": "https://durian.example.test/api/auth/sso/callback"},
      anon=401, viewer=403, ops=403, admin=200),
    R(SSO_DELETE, "DELETE", "/api/auth/sso", anon=401, viewer=403, ops=403, admin=200),
    R(TOKENS, "GET", "/api/tokens", anon=401, viewer=403, ops=403, admin=200),
    R(TOKENS, "POST", "/api/tokens", {"name": "probe-token", "scope": "read"},
      anon=401, viewer=403, ops=403, admin=201),
    R(TOKENS, "PUT", "/api/tokens/probe-token", {"disabled": True}, anon=401, viewer=403, ops=403, admin=200),
    R(TOKENS, "DELETE", "/api/tokens/probe-token", anon=401, viewer=403, ops=403, admin=200),
    R(DATABASE, "GET", "/api/database", anon=401, viewer=403, ops=403, admin=200),
    R(DATABASE, "PUT", "/api/database/settings", {"retention_days": 365},
      anon=401, viewer=403, ops=403, admin=200),
    R(EXPORTS, "GET", "/api/database/exports", anon=401, viewer=403, ops=403, admin=200),
    R(EXPORT_FILE, "GET", "/api/database/exports/durian-export-19700101-000000.zip",
      anon=401, viewer=403, ops=403, admin=404),
    R(EXPORT_CONFIG, "GET", "/api/export", anon=401, viewer=403, ops=403, admin=200),
    R(IMPORT_CONFIG, "POST", "/api/import", {"format": "durian-config", "version": 1, "objects": {}},
      anon=401, viewer=403, ops=403, admin=200),
    R(IMPORT_HISTORY, "POST", "/api/import/history", "1700000000,1.5\n",
      anon=401, viewer=403, ops=403, admin=200, query={"point": "boiler-temp"}),
]


# ---- the table must cover every branch the dispatcher has, and every row must be a real route
missing = dispatcher_shapes() - {row["shape"] for row in ROWS}
stale = {row["shape"] for row in ROWS} - dispatcher_shapes()
assert not missing, "the dispatcher has route branch(es) with no permission opinion here: " + ", ".join(sorted(map(str, missing)))
assert not stale, "this table names route branch(es) the dispatcher no longer has: " + ", ".join(sorted(map(str, stale)))


def _seg(shape_value, got):
    if shape_value == W:
        return True
    if shape_value == N:
        return got is not None
    if isinstance(shape_value, frozenset):
        return got in shape_value
    return got == shape_value


def _no_data(payload):
    if isinstance(payload, list):
        return payload == []
    if isinstance(payload, dict):
        return payload.get("items") == [] or payload.get("total") == 0 or payload.get("alarms") == 0
    return False


for row in ROWS:
    methods, sa, sb, sc = row["shape"]
    path = row["path"].format(who="x")
    m = ROUTE.match(path)
    assert m, f"not a route ROUTE can serve: {path}"
    assert methods == W or row["method"] in methods, (row["method"], methods)
    assert sa in (W, m["a"]), (path, sa, m["a"])
    assert _seg(sb, m["b"]), (path, sb, m["b"])
    assert _seg(sc, m["c"]), (path, sc, m["c"])
    for who, ident in WHO.items():
        want = row[who]
        status, payload = call(ident, row["method"], row["path"].format(who=who), row["body"], row["query"])
        ok = status in want if isinstance(want, tuple) else status == want
        assert ok, f"{who} {row['method']} {path}: got {status}, wanted {want}: {payload}"
        if who == "anon" and row["anon_empty"]:
            assert _no_data(payload), (path, payload)


# ---- an unknown route is 404 for everyone, and a route the caller cannot see is also 404, never 403
for who, ident in WHO.items():
    for method, path in (("GET", "/api/does-not-exist"), ("DELETE", "/api/definitely-not-a-route"),
                         ("GET", "/api/.."), ("GET", "/not-api-at-all")):
        status, payload = call(ident, method, path)
        assert status == 404, (who, method, path, status, payload)
for who, path in (("anon", "/api/points/status-text"), ("viewer", "/api/points/status-text"),
                  ("anon", "/api/value/status-text"), ("viewer", "/api/value/status-text"),
                  ("viewer", "/api/notes/status-text"), ("viewer", "/api/dashboards/status-board"),
                  ("ops", "/api/dashboards/plant-overview")):
    assert call(WHO[who], "GET", path, query={"point": "status-text"})[0] == 404, (who, path)


# ---- one role check: superadmin inherits every role, so can() agrees with the inline tests it replaced for every
# identity Auth.identity hands a handler; a hand-built identity holding only superadmin passes a user-only route too
assert held(["superadmin"]) == {"superadmin", "user", "anonymous"}
assert call({"name": "admin", "roles": {"superadmin"}}, "POST", "/api/events/ack-many", {"ids": [1]})[0] == 200


# ---- machine tokens: the scope path in handle() must keep every administrative route out of reach
READ = app.auth.token_identity(app.auth.create_token("perm-read", "viewer", "read", days=1))
WRITE = app.auth.token_identity(app.auth.create_token("perm-write", "viewer", "write", days=1))
INGEST_TOKEN = app.auth.token_identity(app.auth.create_token("perm-ingest", "viewer", "ingest", "inbox", days=1))
ADMIN_READ = app.auth.token_identity(app.auth.create_token("perm-admin-read", "admin", "read", days=1))
assert READ["scope"] == "read" and WRITE["scope"] == "write" and INGEST_TOKEN["scope"] == "ingest"

ADMIN_ROUTES = [
    ("GET", "/api/users", None, {}), ("GET", "/api/config/point", None, {}),
    ("GET", "/api/audit", None, {}), ("POST", "/api/sql", {"query": "select 1"}, {}), ("GET", "/api/database", None, {}),
    ("GET", "/api/database/exports", None, {}),
    ("GET", "/api/database/exports/durian-export-19700101-000000.zip", None, {}),
    ("GET", "/api/export", None, {}), ("GET", "/api/export/points", None, {}),
    ("GET", "/api/export/bundle", None, {}), ("GET", "/api/export/manifest", None, {}),
    ("GET", "/api/tokens", None, {}), ("GET", "/api/outputs", None, {}),
    ("POST", "/api/tokens", {"name": "nope", "scope": "read"}, {}),
    ("PUT", "/api/config/point/x", {}, {}),
    ("POST", "/api/import", {"format": "durian-config", "version": 1, "objects": {}}, {}),
    ("POST", "/api/import/points", None, {}),
    ("POST", "/api/test/source", {"type": "virtual"}, {"wait": "1"}),
    ("POST", "/api/users/x/reset", None, {}),
    ("DELETE", "/api/dashboards/probe-dash", None, {}),
    ("PUT", "/api/mylists/nope", {"points": []}, {}),
    ("POST", "/api/notes/boiler-temp", {"text": "nope"}, {}),
    ("POST", "/api/events/1/ack", None, {}),
    ("POST", "/api/alarms/boiler-temp/shelve", {"minutes": 5, "reason": "nope"}, {}),
    ("POST", "/api/points/pump-setpoint/set", {"value": 42}, {}),
]
for token in (READ, ADMIN_READ):
    for method, path, body, query in ADMIN_ROUTES:
        status, payload = call(token, method, path, body, query)
        assert status == 403, f"{token['scope']} token reached {method} {path}: {status} {payload}"
assert call(READ, "GET", "/api/points")[0] == 200
assert call(READ, "GET", "/api/export/history", query={"point": "boiler-temp"})[0] == 200

# a write token may set points and read data, and nothing else
assert call(WRITE, "POST", "/api/points/pump-setpoint/set", {"value": 42})[0] == 200
assert call(WRITE, "GET", "/api/points")[0] == 200
for method, path, body, query in ADMIN_ROUTES:
    if (method, path) == ("POST", "/api/points/pump-setpoint/set"):
        continue
    status, payload = call(WRITE, method, path, body, query)
    assert status == 403, f"write token reached {method} {path}: {status} {payload}"

# an ingest token reaches exactly its one source, and no other route at all
assert call(INGEST_TOKEN, "GET", "/api/points")[0] == 403
assert call(INGEST_TOKEN, "GET", "/api/me")[0] == 403
assert call(INGEST_TOKEN, "POST", "/api/ingest/demo", {"point": "probe", "value": 1})[0] == 403
assert call(INGEST_TOKEN, "POST", "/api/ingest/inbox", {"point": "probe", "value": 1})[0] in (200, 422)
assert call(INGEST_TOKEN, "POST", "/api/config/point/x", {})[0] == 403

print("ok")
