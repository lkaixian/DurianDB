"""DurianDB server: stdlib HTTP + SQLite around durian.core. Run: python -m durian.server"""
import json
import os
import random
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .core import Engine, HighLimit, Level, Point, Source
from .mqtt_source import MqttSource

WEB = Path(__file__).resolve().parent.parent / "web"
DB = Path(__file__).resolve().parent.parent / "durian.db"
LIMITS = {"boiler-temp": (90.0, Level.URGENT), "line-pressure": (7.5, Level.WARNING),
          "mqtt-temp": (90.0, Level.URGENT)}


class Simulated(Source):
    # ponytail: random-walk stand-in for a real protocol; add real sources behind Source.poll
    def __init__(self):
        super().__init__("simulated")
        self.v = {"boiler-temp": 87.0, "line-pressure": 6.0}
        for name, (lim, lvl) in LIMITS.items():
            self.add(Point(name, detectors=[HighLimit(lim, lvl)]))

    def poll(self, ts):
        for k in self.v:
            self.v[k] = max(0.0, self.v[k] + random.uniform(-1.5, 1.8) * (1 if k == "boiler-temp" else 0.15))
        return {k: round(v, 2) for k, v in self.v.items()}


lock = threading.Lock()
db = sqlite3.connect(DB, check_same_thread=False)
db.executescript("""
create table if not exists history(point text, ts real, value real);
create index if not exists h_idx on history(point, ts);
create table if not exists events(id integer primary key, point text, level int,
  message text, ts real, acked_ts real);
""")
engine = Engine()
engine.sources.append(Simulated())
CREDS = Path(os.environ.get("DURIAN_MQTT_CREDS", WEB.parent / "lab" / "mqtt" / "creds.json"))
if CREDS.exists():  # TLS broker from tools/mqtt_lab.py; skipped when not set up
    c = json.loads(CREDS.read_text())
    engine.sources.append(MqttSource("mqtt", c["host"], c["port"], c["ca"], c["user"], c["password"],
                                     detectors={"mqtt-temp": [HighLimit(*LIMITS["mqtt-temp"])]}))
stored = {"history": {}, "events": 0}


def all_points():
    return {n: p for s in engine.sources for n, p in s.points.items()}


def tick():
    while True:
        with lock:
            engine.tick(time.time())
            for n, p in all_points().items():
                done = stored["history"].get(n, 0)
                for ts, v in p.history[done:]:
                    db.execute("insert into history values (?,?,?)", (n, ts, v))
                stored["history"][n] = len(p.history)
            for e in engine.events[stored["events"]:]:
                db.execute("insert into events(point,level,message,ts) values (?,?,?,?)",
                           (e.point, int(e.level), e.message, e.ts))
            stored["events"] = len(engine.events)
            db.commit()
        time.sleep(2)


def rows(sql, args=()):
    cur = db.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


ROLLUPS = {"avg": "avg(value)", "min": "min(value)", "max": "max(value)", "count": "count(*)"}


def series(point, since, bucket, rollup):
    """Bucketed history. Bucket start time is returned as ts. Caller validates all arguments."""
    if rollup == "last":
        return rows("select b * ? as ts, value from (select cast(ts / ? as int) b, value, "
                    "row_number() over (partition by cast(ts / ? as int) order by ts desc) rn "
                    "from history where point = ? and ts >= ?) where rn = 1 order by b",
                    (bucket, bucket, bucket, point, since))
    return rows(f"select cast(ts / ? as int) * ? as ts, {ROLLUPS[rollup]} as value from history "
                "where point = ? and ts >= ? group by cast(ts / ? as int) order by ts",
                (bucket, bucket, point, since, bucket))


def series_request(query):
    q = {k: v[0] for k, v in parse_qs(query).items()}
    point, rollup = q.get("point"), q.get("rollup", "avg")
    if point not in all_points():
        return 400, {"error": "unknown point"}
    if rollup != "last" and rollup not in ROLLUPS:
        return 400, {"error": "rollup must be one of avg, min, max, last, count"}
    try:
        window = int(q.get("window", 3600))
        bucket = int(q.get("bucket", max(1, window // 120)))
    except ValueError:
        return 400, {"error": "window and bucket must be whole seconds"}
    if not 10 <= window <= 7 * 86400 or not 1 <= bucket <= window:
        return 400, {"error": "window must be 10 s to 7 days and bucket between 1 s and the window"}
    items = series(point, time.time() - window, bucket, rollup)
    return 200, {"point": point, "rollup": rollup, "window": window, "bucket": bucket, "items": items}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        with lock:
            if url.path == "/api/series":
                return self.send(*series_request(url.query))
            if self.path == "/api/points":
                out = []
                for n, p in all_points().items():
                    h = rows("select ts, value from history where point=? order by ts desc limit 60", (n,))[::-1]
                    out.append({"name": n, "last": p.last, "limit": LIMITS.get(n, (None,))[0], "history": h})
                return self.send(200, out)
            if self.path == "/api/events":
                return self.send(200, rows("select * from events order by acked_ts is not null, level desc, ts desc limit 100"))
        if self.path in ("/", "/index.html"):
            return self.send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        parts = self.path.strip("/").split("/")  # api/events/<id>/ack
        if len(parts) == 4 and parts[:2] == ["api", "events"] and parts[3] == "ack" and parts[2].isdigit():
            with lock:
                cur = db.execute("update events set acked_ts=? where id=? and acked_ts is null",
                                 (time.time(), int(parts[2])))
                db.commit()
            return self.send(200 if cur.rowcount else 404, {"acked": cur.rowcount})
        self.send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=tick, daemon=True).start()
    print("DurianDB on http://127.0.0.1:8765")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
