"""DurianDB server: stdlib HTTP + SQLite around durian.core. Run: python -m durian.server"""
import json
import random
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import Engine, HighLimit, Level, Point, Source

WEB = Path(__file__).resolve().parent.parent / "web"
DB = Path(__file__).resolve().parent.parent / "durian.db"
LIMITS = {"boiler-temp": (90.0, Level.URGENT), "line-pressure": (7.5, Level.WARNING)}


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
src = Simulated()
engine.sources.append(src)
stored = {"history": {n: 0 for n in src.points}, "events": 0}


def tick():
    while True:
        with lock:
            engine.tick(time.time())
            for n, p in src.points.items():
                for ts, v in p.history[stored["history"][n]:]:
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


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        with lock:
            if self.path == "/api/points":
                out = []
                for n, p in src.points.items():
                    h = rows("select ts, value from history where point=? order by ts desc limit 60", (n,))[::-1]
                    out.append({"name": n, "last": p.last, "limit": LIMITS[n][0], "history": h})
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
