"""The route tables and the access rules must not drift apart. Run: python tests/test_route_map.py

Every name in App.DATA_ROUTES and App.ADMIN_ROUTES must be a real method on App, the administration areas
and the administration handlers must name the same thing, every administration area must answer 403 to a
signed-in non-admin and 401 to an anonymous caller while an unknown area answers 404 to both (a 403 there
would confirm the route exists), no machine token may reach an administration area even where the data
plane also serves that area, and the anonymous-facing administration-shaped data routes are listed below
so that adding one is a visible change. GET /api/health is the console's own health probe and stays
reachable without a login.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _support import new_store  # noqa: E402
from durian.app import ADMIN_AREAS, ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402

store = new_store()
app = App(store)
assert app.auth.bootstrap("route-map-admin-1") is None
store.add_user("viewer", hash_password("viewer-password-1"), ["user"])

ANON = {"name": None, "roles": {"anonymous"}}
VIEWER = {"name": "viewer", "roles": {"anonymous", "user"}}


def call(ident, method, path, body=None, query=None):
    try:
        return app.handle(method, path, query or {}, body, ident)
    except ApiError as e:
        return e.status, {"error": str(e)}


# ---- 1. a typo in a route table must fail a test, not 404 at runtime
for kind, table in (("data", App.DATA_ROUTES), ("administration", App.ADMIN_ROUTES)):
    for area, handler in table.items():
        assert hasattr(app, handler), f"the {kind} route '{area}' names App.{handler}, which is not a method on App"

# ---- 2. the administration areas and the administration handlers name the same thing
for complaint, names in (("administration area with no handler", ADMIN_AREAS - set(App.ADMIN_ROUTES)),
                         ("handler for an area that is not an administration area", set(App.ADMIN_ROUTES) - ADMIN_AREAS)):
    assert not names, complaint + ": " + ", ".join(sorted(names))

# ---- 3. every administration area refuses a non-admin and an anonymous caller; an unknown area says 404 to both
for area in sorted(ADMIN_AREAS):
    for path in (f"/api/{area}", f"/api/{area}/x"):
        status, payload = call(VIEWER, "GET", path)
        assert status == 403, f"a signed-in non-admin GET {path} got {status}, not 403: {payload}"
        status, payload = call(ANON, "GET", path)
        assert status == 401, f"an anonymous GET {path} got {status}, not 401: {payload}"
for path in ("/api/not-an-area", "/api/not-an-area/x"):
    for who, ident in (("an anonymous caller", ANON), ("a signed-in non-admin", VIEWER)):
        status, payload = call(ident, "GET", path)
        assert status == 404, f"{who} GET {path} got {status}, not 404: answering 403 would confirm the route exists. {payload}"

# ---- 4. a machine token never reaches an administration area, not even one the data plane also serves
TOKENS = [app.auth.token_identity(app.auth.create_token(name, owner, scope, source, days=1))
          for name, owner, scope, source in (("map-read", "viewer", "read", None),
                                             ("map-write", "viewer", "write", None),
                                             ("map-ingest", "viewer", "ingest", "demo"),
                                             ("map-admin-read", "admin", "read", None))]
assert all(TOKENS), "a token created a moment ago did not resolve to an identity"
MACHINE_PROBES = [("GET", f"/api/{area}{suffix}", None) for area in sorted(ADMIN_AREAS) for suffix in ("", "/x")]
MACHINE_PROBES += [  # the administration shapes of the areas the data plane also serves
    ("GET", "/api/export/points", None), ("GET", "/api/export/bundle", None), ("GET", "/api/export/manifest", None),
    ("POST", "/api/import/points", None), ("POST", "/api/import", {"format": "durian-config", "version": 1, "objects": {}}),
    ("PUT", "/api/auth/sso", {"issuer": "https://idp.example.test"}), ("DELETE", "/api/auth/sso", None),
    ("PUT", "/api/auth/ldap", {"url": "ldaps://dir.example.test"}), ("DELETE", "/api/auth/ldap", None),
]
for token in TOKENS:
    for method, path, body in MACHINE_PROBES:
        status, payload = call(token, method, path, body)
        assert status == 403, f"a {token['scope']} token reached {method} {path} with {status}, not 403: {payload}"

# ---- 5. the anonymous surface is listed here, so a new anonymous administration-shaped route is a visible change
assert call(ANON, "GET", "/api/health")[0] == 200, "the console's health probe must stay reachable without a login"
ADMIN_SHAPED = {"auth", "export", "import"}  # the data-plane areas that are also administration
assert {a for a in App.DATA_ROUTES if a in ADMIN_AREAS} == ADMIN_SHAPED, \
    "a data route now shares a name with an administration area: decide, here, what an anonymous caller may reach"
ANON_REACHABLE = {("GET", "/api/auth/sso")}  # the sign-in page asks this before anyone has signed in
ANON_PROBES = [("GET", "/api/auth/sso", None), ("GET", "/api/auth", None), ("GET", "/api/auth/x", None),
               ("GET", "/api/export", None), ("GET", "/api/export/points", None), ("GET", "/api/export/bundle", None),
               ("GET", "/api/export/manifest", None), ("POST", "/api/import/points", None),
               ("POST", "/api/import", {"format": "durian-config", "version": 1, "objects": {}})]
unprobed = ANON_REACHABLE - {(m, p) for m, p, _ in ANON_PROBES}
assert not unprobed, "the anonymous list names a route these probes never ask: " + ", ".join(sorted(map(str, unprobed)))
for method, path, body in ANON_PROBES:
    status, payload = call(ANON, method, path, body)
    if (method, path) in ANON_REACHABLE:
        assert status == 200, f"the public route {method} {path} got {status}, not 200: {payload}"
    else:
        assert status in (401, 403, 404), \
            f"an anonymous {method} {path} got {status}, which no listed public route explains: {payload}"

print("ok")
