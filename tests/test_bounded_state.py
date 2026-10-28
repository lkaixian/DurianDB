"""Every runtime buffer has a cap, a count of what it dropped, and drops an expired entry before a live one.
Run: python tests/test_bounded_state.py"""
import sys
import json
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import durian.app as A  # noqa: E402
import durian.ingest as I  # noqa: E402
import durian.mqtt_source as M  # noqa: E402
from _support import new_store  # noqa: E402
from durian.app import App  # noqa: E402
from durian.core import Event, Level, Logging, Point, Source  # noqa: E402

admin = {"name": "admin", "roles": {"superadmin"}}


# ---- Engine.events: one tick cannot raise an unlimited number of alarms
app = App(new_store())
app.engine.EVENT_MAX = 2
for i in range(5):
    app.engine._event(Event("p", Level.WARNING, f"a{i}", 1.0))
assert [e.message for e in app.engine.events] == ["a3", "a4"], app.engine.events  # the oldest go, the newest stay
assert app.engine.events_lost == 3, app.engine.events_lost

app.tick(10.0)  # drains the tick and says in the journal what the cap cost
assert app.engine.events == [] and app.engine.events_lost == 0
sys_ev = [e for e in app.store.events(limit=10) if e["point"] == "(system)"]
assert len(sys_ev) == 1 and "3 alarm(s) were dropped" in sys_ev[0]["message"], sys_ev


# ---- Point.unsaved: a flooded tick keeps the newest samples and counts the rest as lost history
p = Point("p", logging=Logging.ALL)
p.UNSAVED_MAX = 5
for i in range(12):
    p.update(float(i), float(i), lambda e: None)
assert p.unsaved_lost == 7 and [v for _, v, _ in p.take_unsaved()] == [7.0, 8.0, 9.0, 10.0, 11.0], p.unsaved_lost


# ---- INTERVAL logging aggregates in constant space, however many samples one interval holds
q = Point("q", logging=Logging.INTERVAL, interval=1e9, interval_mode="avg")
for i in range(5000):
    q.update(float(i), float(i), lambda e: None)
assert q._period == [5000, 12_497_500.0, 0.0, 4999.0] and len(q._period) == 4, q._period


class Feed(Source):
    def poll(self, ts):
        return [("p", float(i), ts + i) for i in range(12)]  # one history row per (point, ts), so distinct times


app2 = App(new_store())
feed = Feed("feed")
feed.add(Point("p", logging=Logging.ALL))
feed.points["p"].UNSAVED_MAX = 5
app2.engine.sources.append(feed)
app2.tick(1000.0)
assert app2.lost == 7, app2.lost
assert app2.handle("GET", "/api/health", {}, None, admin)[1]["lost_samples"] == 7
assert [r["value"] for r in app2.store.recent("p", 20)] == [7.0, 8.0, 9.0, 10.0, 11.0]


# ---- confirm tokens: expired first, then the oldest live one; the caller is simply asked for a new token
A.CONFIRM_MAX = 3
app3 = App(new_store())
ident = {"name": "op", "roles": {"user"}}
app3.store.run("insert into confirm_tokens(token, body, expires) values (?,?,?)",
               ("expired", json.dumps(["op", "p", 0]), time.time() - 5))
toks = [app3.confirm_token(ident, "p", i) for i in range(3)]
rows = app3.store.q("select token from confirm_tokens")
assert "expired" not in {r["token"] for r in rows} and len(rows) == 3, rows
tok3 = app3.confirm_token(ident, "p", 99)
rows = {r["token"] for r in app3.store.q("select token from confirm_tokens")}
assert len(rows) == 3 and toks[0] not in rows and tok3 in rows, rows
assert all(t in rows for t in toks[1:]), "a live token is never dropped before an expired one"
assert app3.confirms_lost == 1, app3.confirms_lost


# ---- escalations: a closed alarm needs no memory of having been escalated
app4 = App(new_store())
app4.open_events = [Event("p", Level.WARNING, "up", 1.0, id=1), Event("p", Level.WARNING, "up", 2.0, id=3)]
app4.escalated = {("mail", 1), ("mail", 2), ("mail", 3)}
app4.escalate(now=1000.0)
assert app4.escalated == {("mail", 1), ("mail", 3)}, app4.escalated


# ---- internal error routes: stale routes first, then the oldest live one, never the route being recorded
A.INTERNAL_ROUTES_MAX = 3
app5 = App(new_store())
now = 10_000.0
app5.internal_seen["stale"] = [now - A.INTERNAL_ALARM_SECONDS - 1]
for route in ("r0", "r1", "r2"):
    app5.record_internal_error(route, ValueError("boom"), now=now)
assert "stale" not in app5.internal_seen and set(app5.internal_seen) == {"r0", "r1", "r2"}, app5.internal_seen
assert app5.internal_routes_lost == 0, "an expired route goes without evicting a live one"
app5.record_internal_error("r3", ValueError("boom"), now=now)
assert len(app5.internal_seen) == 3 and "r0" not in app5.internal_seen and "r3" in app5.internal_seen
assert app5.internal_routes_lost == 1, app5.internal_routes_lost


# ---- deleting a point removes the state kept under its name
store = new_store()
from durian.config import seed  # noqa: E402

seed(store)
app6 = App(store)
app6.auth.bootstrap("admin-password-123")
who = app6.auth.identity(app6.auth.login("admin", "admin-password-123"))


def call(method, path, body=None):
    return app6.handle(method, path, {}, body, who)


assert call("PUT", "/api/config/point/ghost", {"source": "demo", "settable": True})[0] == 200
assert call("POST", "/api/alarms/ghost/shelve", {"minutes": 5, "reason": "testing"})[0] == 200
app6.store.put("override", "ghost", {"value": 1})
from durian import notes  # noqa: E402

notes.add(app6.store, "ghost", "a note", "admin")
app6.pending = [("ghost", 1.0, 1.0, 0), ("boiler-temp", 2.0, 2.0, 0)]
assert call("DELETE", "/api/config/point/ghost")[0] == 200
assert app6.store.get("alarmhold", "ghost") is None and app6.holds.get("ghost") is None
assert app6.store.get("override", "ghost") is None
assert notes.for_point(app6.store, "ghost") == []
assert [r[0] for r in app6.pending] == ["boiler-temp"], app6.pending


# ---- MQTT: the broker's thread never grows a queue without end, and an endless supply of topics cannot grow the point map
class Fake:
    def connect_async(self, *a, **k): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass
    def subscribe(self, *a, **k): pass


M.tls_client = lambda creds, client_id="": Fake()
M.MqttSource.QUEUE_MAX = 3
mq = M.MqttSource("broker", {"host": "h", "port": 1, "ca": "x", "user": "u", "password": "p"})
for i in range(5):
    mq._on_message(None, None, types.SimpleNamespace(topic=f"t{i}", payload=b"1"))
assert mq.q.qsize() == 3 and mq.dropped == 2, (mq.q.qsize(), mq.dropped)

while not mq.q.empty():
    mq.q.get_nowait()
mq.prefix, mq.points, mq.dropped = "", {}, 0
mq.factory = lambda name: Point(name)
mq.AUTO_POINTS_MAX = 2
for name in ("a", "b", "c"):
    mq.q.put_nowait((name, b"1"))
out = mq.poll(100.0)
assert set(out) == {"a", "b"} and set(mq.points) == {"a", "b"} and mq.dropped == 1, (out, mq.points, mq.dropped)


# ---- HTTP ingest: a queue the engine has not drained refuses more with a sentence, and new names are capped
ing = I.HttpInSource("in", auto_create=True, factory=lambda name: Point(name))
ing.MAX_PENDING = 5
accepted, rejected = 0, []
for i in range(8):
    got, no = ing.submit([("p", float(i), None, 0, None)], now=1000.0 + i)
    accepted += got
    rejected += no
assert len(ing._q) == 5 and accepted == 5 and ing.dropped == 3, (len(ing._q), accepted, ing.dropped)
assert all("waiting for the engine" in r["reason"] for r in rejected), rejected
assert len(ing.poll(1010.0)) == 5 and "p" in ing.points

ing2 = I.HttpInSource("in2", auto_create=True, factory=lambda name: Point(name))
ing2.AUTO_POINTS_MAX = 2
results = [ing2.submit([(name, 1, None, 0, None)], now=2000.0) for name in ("a", "b", "c")]
assert [got for got, _ in results] == [1, 1, 0] and "limit of 2 points" in results[2][1][0]["reason"], results
assert ing2.poll(2010.0) and set(ing2.points) == {"a", "b"}, ing2.points

print("ok")
