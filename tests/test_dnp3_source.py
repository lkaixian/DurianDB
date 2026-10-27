"""DNP3 source: CRC, segmented replies, class 0 and event polls, quality, and every failure path.

The outstation is a TCP socket on 127.0.0.1 answering crafted DNP3 frames; nothing leaves the machine.
Run: python tests/test_dnp3_source.py"""
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import dnp3_source as D  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.core import Point, Quality  # noqa: E402
from _support import new_store  # noqa: E402

# ---- codec round trip: the published CRC check value, and a corrupted block is refused
assert D.crc16(b"123456789") == 0xEA82
assert D.crc16(b"") == 0xFFFF
packet = D.frame(0xC4, 1024, 1, b"\xc0\x01hello")
assert D.crc16(packet[:8]) == int.from_bytes(packet[8:10], "little")
assert packet[:2] == D.START and packet[2] == len(b"\xc0\x01hello") + 5
assert D.frame(0x44, 1, 1024, b"\xc0" + b"x" * 32)[2] == 38  # two blocks of 16 plus a transport byte
for bad in ("", "127.0.0.1", ":20000", "host:0", "host:70000", 7):
    try:
        D.parse_address(bad)
        raise AssertionError(bad)
    except D.Dnp3Error:
        pass

OUTSTATION = 1024


# ---- a fake outstation: one request per TCP connection, answered with crafted link frames
def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def read_request(sock):
    """(control, dest, src, user data) from a link frame this test's master sent."""
    head = recv_exact(sock, 10)
    assert head[:2] == D.START
    assert D.crc16(head[:8]) == int.from_bytes(head[8:10], "little")
    user, remaining = b"", head[2] - 5
    while remaining:
        n = min(16, remaining)
        raw = recv_exact(sock, n + 2)
        assert D.crc16(raw[:n]) == int.from_bytes(raw[n:], "little")
        user += raw[:n]
        remaining -= n
    return head[3], int.from_bytes(head[4:6], "little"), int.from_bytes(head[6:8], "little"), user


def app_response(objects, iin=b"\x00\x00"):
    return bytes([0xC0, D.RESPONSE]) + iin + objects


def link_frames(app, segments=1):
    """One or more link frames carrying the application response, split into transport segments."""
    size = (len(app) + segments - 1) // segments
    chunks = [app[i:i + size] for i in range(0, len(app), size)]
    out = b""
    for i, chunk in enumerate(chunks):
        control = (0x80 if i == 0 else 0) | (0x40 if i == len(chunks) - 1 else 0) | (i & 0x3F)
        out += D.frame(0x44, D.MASTER_ADDRESS, OUTSTATION, bytes([control]) + chunk)
    return out


def static_objects(items):
    """One 1-octet start-stop header per (group, variation, start, payloads) item."""
    out = b""
    for group, variation, start, payloads in items:
        out += bytes([group, variation, 0x00, start & 0xFF, (start + len(payloads) - 1) & 0xFF])
        out += b"".join(payloads)
    return out


def event_objects(items):
    """One 1-octet-count, 1-octet-index header per (group, variation, [(index, payload)]) item."""
    out = b""
    for group, variation, rows in items:
        out += bytes([group, variation, 0x17, len(rows) & 0xFF])
        out += b"".join(bytes([index & 0xFF]) + payload for index, payload in rows)
    return out


class Outstation:
    def __init__(self, address=OUTSTATION):
        self.address = address
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(8)
        self.mode = "good"
        self.static = b""
        self.events = b""
        self.segments = 1
        self.app_fragments = None  # when set, one application fragment per link frame
        self.requests = []  # (group, variation, qualifier) of every read we received
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                with conn:
                    self._serve(conn)
            except (OSError, EOFError, AssertionError):
                pass

    def _serve(self, conn):
        _, dest, src, user = read_request(conn)
        assert dest == self.address and src == D.MASTER_ADDRESS
        app = user[1:]
        assert app[0] == 0xC0 and app[1] == D.READ
        self.requests.append((app[2], app[3], app[4]))
        if self.app_fragments:
            conn.sendall(b"".join(D.frame(0x44, D.MASTER_ADDRESS, OUTSTATION, bytes([0xC0 | (i & 0x3F)]) + part)
                                  for i, part in enumerate(self.app_fragments)))
            return
        reply = self._reply(app[3])
        raw = bytearray(link_frames(reply, self.segments))
        if self.mode == "bad-crc":
            raw[-1] ^= 0xFF  # the last data block's CRC no longer matches
        conn.sendall(bytes(raw))

    def _reply(self, variation):
        if self.mode == "denied":
            return app_response(b"", iin=b"\x00\x01")  # function not supported
        if self.mode == "unknown":
            return app_response(bytes([7, 1, 0x06]))  # a group the driver does not read
        return app_response(self.static if variation == 1 else self.events)


def analog(v):
    return struct.pack("<i", v) + b"\x01"


def analog_flags(v, flags):
    return struct.pack("<i", v) + bytes([flags])


station = Outstation()
station.static = static_objects([
    (30, 1, 0, [analog(i) for i in range(9)]),  # analog inputs 0..8
    (20, 1, 1, [struct.pack("<I", 1234) + b"\x01"]),  # counter 1
    (1, 1, 2, [b"\x81"]),  # binary input 2, on and online
    (40, 1, 3, [analog(250)]),  # analog output 3
])
def events(device_ms):
    return event_objects([
        (32, 1, [(5, analog(42)), (6, analog_flags(66, 0x05)), (7, analog_flags(77, 0x03))]),  # analog events
        (32, 3, [(8, struct.pack("<i", 88) + b"\x01" + struct.pack("<Q", device_ms)[:6])]),  # with a device time
    ])


station.events = events(int(time.time() * 1000))

# ---- config validation: every problem at once
app = App(new_store())
app.auth.bootstrap("admin-password-123")
admin = app.auth.identity(app.auth.login("admin", "admin-password-123"))


def call(method, path, body=None, **q):
    try:
        return app.handle(method, path, q, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


put = lambda kind, name, body: call("PUT", f"/api/config/{kind}/{name}", body)
props = lambda r: {e["property"] for e in r[1]["errors"]}
last = lambda: {p["name"]: p["last"] for p in call("GET", "/api/points")[1]}

r = put("source", "poll", {"type": "dnp3", "dnp3": {"address": "", "outstation": -1}, "timeout": 0.5,
                           "period": 0.1, "backoff_scale": 1})
assert r[0] == 422 and {"dnp3.address", "dnp3.outstation", "timeout", "period", "backoff_scale"} <= props(r), props(r)
assert "dnp3" in props(put("source", "poll", {"type": "dnp3"}))
SRC = {"type": "dnp3", "dnp3": {"address": f"127.0.0.1:{station.port}", "outstation": OUTSTATION},
       "timeout": 1, "period": 60}
assert put("source", "poll", SRC)[0] == 200

assert "dnp3.group" in props(put("point", "p", {"source": "poll", "dnp3": {"group": 99, "index": 0}}))
assert "dnp3.index" in props(put("point", "p", {"source": "poll", "dnp3": {"group": 30, "index": 70000}}))
assert "dnp3" in props(put("point", "p", {"source": "poll"}))
assert "settable" in props(put("point", "p", {"source": "poll", "dnp3": {"group": 30, "index": 0}, "settable": True}))

for name, (group, index) in {"analog": (30, 0), "counter": (20, 1), "binary": (1, 2), "output": (40, 3),
                             "event": (30, 5), "lost": (30, 6), "restart": (30, 7), "time": (30, 8)}.items():
    assert put("point", name, {"source": "poll", "dnp3": {"group": group, "index": index}})[0] == 200

# ---- the first poll is class 0: one read, static values for every configured point
src = next(s for s in app.engine.sources if s.name == "poll")
deadline = time.monotonic() + 3
while src.connected is None and time.monotonic() < deadline:
    time.sleep(0.02)
assert (60, 1, 0x06) in station.requests, station.requests  # group 60, class 0, all objects
app.tick(time.time())
v = last()
assert v["analog"] == 0.0 and v["counter"] == 1234.0 and v["binary"] == 1.0 and v["output"] == 250.0, v
assert src.connected is True and src.points["analog"].quality == Quality.GOOD

# ---- later ticks read classes 1, 2 and 3; an event updates the static point it belongs to
EVENT_MS = int(time.time() * 1000)
station.events = events(EVENT_MS)  # stamped now, so it is newer than the class 0 value just read
before = len(station.requests)
src.cycle()
assert station.requests[before:] == [(60, 2, 0x06), (60, 3, 0x06), (60, 4, 0x06)], station.requests[before:]
app.tick(time.time() + 1)
v = last()
assert v["event"] == 42.0 and v["restart"] == 77.0 and v["time"] == 88.0, v
assert v["lost"] == 6.0, "a BAD quality keeps the last good value, it does not take the event's value"
assert src.points["event"].quality == Quality.GOOD
assert src.points["lost"].quality == Quality.BAD, "communication lost maps to BAD"
assert src.points["restart"].quality == Quality.UNCERTAIN, "a restart maps to UNCERTAIN"
assert abs(src.points["time"].last_ts - EVENT_MS / 1000.0) < 1e-6, src.points["time"].last_ts

# ---- a transport reply split across three segments is reassembled before it is parsed
saved_static, saved_segments = station.static, station.segments
station.static = static_objects([(30, 1, 0, [analog(i * 3) for i in range(20)])])
station.segments = 3
seg = D.Dnp3Source("seg", {"dnp3": {"address": f"127.0.0.1:{station.port}", "outstation": OUTSTATION},
                           "timeout": 1, "period": 60})
seg.add(Point("deep", meta={"dnp3": {"group": 30, "index": 19}}))
seg.cycle()
value, _, quality = seg.poll(time.time())["deep"]
assert value == 57.0 and quality == Quality.GOOD and seg.connected is True, (value, quality, seg.error)
station.static, station.segments = saved_static, saved_segments

# ---- an answer split into two application fragments is parsed as one object stream
station.app_fragments = [
    bytes([0x80, D.RESPONSE]) + b"\x00\x00" + static_objects([(30, 1, 0, [analog(11)])]),
    bytes([0x40, D.RESPONSE]) + b"\x00\x00" + static_objects([(30, 1, 1, [analog(22)])]),
]
frag = D.Dnp3Source("frag", {"dnp3": {"address": f"127.0.0.1:{station.port}", "outstation": OUTSTATION},
                             "timeout": 1, "period": 60})
frag.add(Point("first", meta={"dnp3": {"group": 30, "index": 0}}))
frag.add(Point("second", meta={"dnp3": {"group": 30, "index": 1}}))
frag.cycle()
got = frag.poll(time.time())
assert got["first"][0] == 11.0 and got["second"][0] == 22.0 and frag.connected is True, got
station.app_fragments = None

# ---- a bad CRC is the source's health sentence, not an exception and not a bad point
station.mode = "bad-crc"
src.cycle()
assert src.connected is False and "CRC" in src.error and "Dnp3Error" not in src.error, src.error
app.tick(time.time() + 3)
assert src.points["analog"].quality == Quality.BAD

# ---- recovery re-reads class 0, so every point leaves BAD instead of waiting for an event
station.mode = "good"
src.cycle()
assert src.connected is True and src.fails == 0 and src.error is None, src.error
app.tick(time.time() + 4)
assert last()["analog"] == 0.0 and src.points["analog"].quality == Quality.GOOD

# ---- a refused read is a sentence too: the device is reachable but will not answer the class
station.mode = "denied"
src.cycle()
assert src.connected is False and "refused" in src.error and "Dnp3Error" not in src.error, src.error
station.mode = "good"
src.cycle()  # recover again, so the next cycle is an event poll
assert src.connected is True and src.error is None, src.error

# ---- an unknown group is skipped with a sentence; the poll itself still succeeds
station.mode = "unknown"
before = len(station.requests)
src.cycle()
assert src.connected is True and "group 7" in src.error and "does not read" in src.error, src.error
assert "Dnp3Error" not in src.error
assert station.requests[before:] == [(60, 2, 0x06), (60, 3, 0x06), (60, 4, 0x06)], station.requests[before:]

# ---- recovery: a good event poll clears the note and applies the events again
station.mode = "good"
src.cycle()
assert src.connected is True and src.fails == 0 and src.error is None, src.error
app.tick(time.time() + 5)
assert last()["event"] == 42.0 and src.points["event"].quality == Quality.GOOD
print("ok")
