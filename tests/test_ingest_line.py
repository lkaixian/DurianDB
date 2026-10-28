"""InfluxDB line protocol at ingest: POST /api/ingest/<source>/line takes text lines
`measurement[,tag=..] field=value[,field2=value2] [timestamp_ns]` and routes every field through the same
guard path as the JSON ingest. Run: python tests/test_ingest_line.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402
from _support import new_store  # noqa: E402

store = new_store()
seed(store)
app = App(store)
assert app.auth.bootstrap("admin-password-123") is None
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(who, method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, who)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


def put(kind, name, body):
    s, r = call(admin, "PUT", f"/api/config/{kind}/{name}", body)
    assert s == 200, ("config", kind, name, s, r)


put("source", "lp", {"type": "http_in", "read_roles": ["user"]})
for n in ("cpu.usage", "cpu.idle", "temp", "flag.on", "ev.count", "pump.state", "old.x", "tag.v"):
    put("point", n, {"source": "lp"})
put("point", "pump.name", {"source": "lp", "datatype": "text"})
put("source", "other", {"type": "http_in"})
tok = app.auth.token_identity(call(admin, "POST", "/api/tokens", {"name": "lp-in", "scope": "ingest", "source": "lp"})[1]["token"])
tok2 = app.auth.token_identity(call(admin, "POST", "/api/tokens", {"name": "ot-in", "scope": "ingest", "source": "other"})[1]["token"])
rd = app.auth.token_identity(call(admin, "POST", "/api/tokens", {"name": "rd", "scope": "read"})[1]["token"])


def line(text, who=tok, src="lp"):
    return call(who, "POST", f"/api/ingest/{src}/line", text)


now = time.time()
app.tick(now)
ns = int((now - 5) * 1e9)

# ---- fields map to <measurement>.<field>; a lone `value` field is just <measurement>; tags are ignored; i/u/t/f/ns
s, r = line(f"cpu,host=a\\ b usage=0.5,idle=9i {ns}\ntemp,loc=x value=21.5 {ns}\nflag on=t {ns}\nev count=7u {ns}\n"
            f'pump,site=1 state=false,name="RUN, ok" {ns}\n# comment\n\n')
assert s == 200 and r["accepted"] == 7 and r["rejected"] == [], ("good lines", s, r)
app.tick(now)
for name, want in (("cpu.usage", 0.5), ("cpu.idle", 9.0), ("temp", 21.5), ("flag.on", 1.0), ("ev.count", 7.0),
                   ("pump.state", 0.0), ("pump.name", "RUN, ok")):
    s, v = call(admin, "GET", "/api/points/" + name)
    assert s == 200 and v["last"] == want and abs(v["last_ts"] - (now - 5)) < 1e-3, (name, want, v)

# ---- bad lines are refused by line number and reason; good lines in the same body still land
s, r = line(f"tag v=1 {ns + 1000}\nnofields\ntag v=abc\ntag v=1 notatime\nmissing x=1\ntag v=nan\ntag v=2 {int((now + 9999) * 1e9)}\n")
assert s == 200 and r["accepted"] == 1, ("one good line", s, r)
assert [x["index"] for x in r["rejected"]] == [2, 3, 4, 5, 6, 7], ("refused by 1-based line number", r)
assert "unknown point" in r["rejected"][3]["reason"] and r["rejected"][3]["point"] == "missing.x", r
assert "future" in r["rejected"][5]["reason"], ("the shared guards apply", r)

s, r = line("tag v=1 " + "9" * 400 + f"\ntag v=1 {ns + 3000}")
assert s == 200 and r["accepted"] == 1 and [x["index"] for x in r["rejected"]] == [1], ("400-digit timestamp is a rejected line, not a 500", s, r)

s, r = line("garbage")
assert s == 422 and r["accepted"] == 0 and r["rejected"][0]["index"] == 1, ("all bad is 422", s, r)

# ---- backfill and max age use the existing path
s, r = line(f"tag v=3 {int((now - 600) * 1e9)}")
assert s == 200 and r["accepted"] == 1, ("old explicit ts is a backfill", s, r)
s, r = line("old x=1 1000000000")
assert s == 422 and "older than this source accepts" in r["rejected"][0]["reason"], r

# ---- MAX_BATCH
s, r = line("\n".join(f"tag v={i} {ns + 2000 + i}" for i in range(1001)))
assert s == 400 and "between 1 and 1000" in r["error"], (s, r)
s, r = line("")
assert s == 400, (s, r)

# ---- auth: wrong-source token, read token, browser session, anonymous
assert line("tag v=1", tok2)[0] == 403
assert line("tag v=1", rd)[0] == 403
assert line("tag v=1", admin)[0] == 403
assert line("tag v=1", {"name": None, "roles": {"anonymous"}})[0] == 401
s, r = call(tok, "GET", "/api/ingest/lp/line")
assert s == 400, ("GET is not a line body", s, r)
print("ok")
