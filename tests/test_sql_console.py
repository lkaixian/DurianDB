"""POST /api/sql: a read-only SQL console for superadmins. Run: python tests/test_sql_console.py

One SELECT, session auth only, audited. Everything else is refused by name and the database stays as it was;
the tables that hold hashes and tokens are never readable, not even through a clever select.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _support import new_store  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.auth import hash_password  # noqa: E402

store = new_store()
app = App(store)
assert app.auth.bootstrap("sql-admin-password-1") is None
store.add_user("viewer", hash_password("viewer-password-1"), ["user"])
store.ddl("create table if not exists probe(id {int}, name {key})")
store.run("insert into probe(id, name) values (1, 'a')")
for i in range(1500):
    store.run("insert into history(point, ts, value) values ('p', ?, 1)", (float(i),))

ADMIN = {"name": "admin", "roles": {"anonymous", "user", "superadmin"}}
VIEWER = {"name": "viewer", "roles": {"anonymous", "user"}}
TOKEN = app.auth.token_identity(app.auth.create_token("sql-tok", "admin", "write", days=1))


def sql(ident, query):
    try:
        return app.handle("POST", "/api/sql", {}, {"query": query}, ident)
    except ApiError as e:
        return e.status, {"error": str(e)}


def snapshot():
    return store.q("select count(*) n from probe")[0]["n"], store.q("select count(*) n from users")[0]["n"], \
        store.q("select count(*) n from history")[0]["n"]


# admin reads
status, out = sql(ADMIN, "  -- note\n select id, name from probe ")
assert status == 200 and out["rows"] == [{"id": 1, "name": "a"}] and out["columns"] == ["id", "name"], (status, out)
# non-admin and machine token
assert sql(VIEWER, "select 1")[0] == 403
assert sql(TOKEN, "select 1")[0] == 403
assert sql({"name": None, "roles": {"anonymous"}}, "select 1")[0] == 401

# refused by name, and nothing changed
before = snapshot()
BAD = {
    "insert": "insert into probe values (2,'b')", "update": "update probe set name='z'", "delete": "delete from probe",
    "drop": "drop table probe", "stacked": "select 1; drop table probe", "stacked-trailing": "select 1;",
    "pragma": "pragma table_info(probe)", "attach": "attach database 'x.db' as x", "with": "with t as (select 1) select * from t",
    "select-into": "select * into outfile '/tmp/x' from probe", "comment-hide": "select 1 /* ; */ ",
    "empty": "   -- nothing", "load": "select load_extension('x')",
}
for label, query in BAD.items():
    status, out = sql(ADMIN, query)
    assert status in (400, 403) and "error" in out, (label, status, out)
    assert "Traceback" not in out["error"] and "sqlite3" not in out["error"], (label, out)
assert snapshot() == before, "a refused statement changed the database"
# the bad statements are named
assert "pragma" in sql(ADMIN, BAD["pragma"])[1]["error"].lower()
assert "attach" in sql(ADMIN, BAD["attach"])[1]["error"].lower()

# secret tables, directly and through a subquery, join or alias
for query in ("select * from users", "select name from (select * from users)", "select * from probe, api_tokens",
              "select * from sessions", "select * from auth_failures", "select * from confirm_tokens",
              "select * from \"users\"", "select * from `users`", "select * from main.users", "SELECT * FROM Users",
              "select * from auth_last_step", "select * from lockouts", "select * from totp", "select * from reset", "select svalue from settings"):
    status, out = sql(ADMIN, query)
    assert status == 403 and "rows" not in out, (query, status, out)
assert "users" in sql(ADMIN, "select * from users")[1]["error"]

# a statement that fails comes back as a sentence
status, out = sql(ADMIN, "select * from no_such_table")
assert status == 400 and "no_such_table" in out["error"] and "OperationalError" not in out["error"], out

# the row cap
status, out = sql(ADMIN, "select * from history")
assert status == 200 and len(out["rows"]) == 1000 and out["truncated"] is True, (status, len(out.get("rows", [])))
assert sql(ADMIN, "select 1 as one")[1]["truncated"] is False

# the audit row
log = store.audit_log()
assert any(r["action"] == "sql.query" and r["user"] == "admin" and "select id, name from probe" in r["detail"] for r in log), log
assert all(len(r["detail"]) <= 500 for r in log)
sql(ADMIN, "select '" + "x" * 2000 + "'")
assert max(len(r["detail"]) for r in store.audit_log() if r["action"] == "sql.query") <= 500

# response size: blobs become hex, big cells are cut, a huge value fails as a sentence, total output is capped
status, out = sql(ADMIN, "select zeroblob(4) as b")
assert status == 200 and out["rows"] == [{"b": "00000000"}], (status, out)
status, out = sql(ADMIN, "select hex(zeroblob(450000000)) as h")
assert status == 400 and "error" in out, status
status, out = sql(ADMIN, "select hex(zeroblob(40000)) as h from history")
assert status == 200 and len(out["rows"][0]["h"]) == 65536 and out["truncated"] is True, (status, len(out.get("rows", [])))
assert sum(len(r["h"]) for r in out["rows"]) <= 4_000_000 + 65536 and len(out["rows"]) < 100
assert sql(ADMIN, "select 1 as one")[0] == 200  # the length limit was restored
assert store.db.getlimit(__import__("sqlite3").SQLITE_LIMIT_LENGTH) > 100_000_000

print("ok")
