"""OPC UA source: Hello/Acknowledge, channel and session activation, one Read, statuses, and every failure path.

The server is a TCP socket on 127.0.0.1 speaking crafted UA-TCP messages; nothing leaves the machine.
Run: python tests/test_opcua_source.py"""
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import opcua_source as O  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.core import Quality  # noqa: E402
from _support import new_store  # noqa: E402

# ---- node id, address and status codec, including the forms a human is likely to mistype
for text, node in (("i=85", (0, "i", 85)), ("ns=2;i=85", (2, "i", 85)),
                   ("ns=2;s=Temperature", (2, "s", "Temperature")), ("s=Name", (0, "s", "Name"))):
    assert O.parse_node_id(text) == node, text
for bad in ("", "ns=2", "ns=2;x=1", "ns=65536;i=1", "ns=2;i=4294967296", "ns=2;s=",
            7, "g=abc", "b=abc"):
    try:
        O.parse_node_id(bad)
        raise AssertionError(bad)
    except O.OpcuaError:
        pass
for bad in ("", "127.0.0.1", ":14360", "host:0", "host:70000", "[::1]:4840", 7):
    try:
        O.parse_address(bad)
        raise AssertionError(bad)
    except O.OpcuaError:
        pass
assert O.parse_address("10.0.0.5:14360") == ("10.0.0.5", 14360)
assert O.status_name(0) == "Good" and O.status_name(0x80340000) == "BadNodeIdUnknown"
assert O.status_name(0xDEADBEEF).startswith("status 0x")
assert O.status_quality(0) == Quality.GOOD
assert O.status_quality(0x40920000) == Quality.UNCERTAIN
assert O.status_quality(0x80340000) == Quality.BAD

# ---- the fake server's wire builders
CHANNEL = 7
EPOCH = 1704067200  # 2024-01-01T00:00:00Z, for a DateTime value


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def string(text):
    if text is None:
        return struct.pack("<i", -1)
    raw = text.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def bstring(raw):
    if raw is None:
        return struct.pack("<i", -1)
    return struct.pack("<i", len(raw)) + raw


def node_id(namespace, ident):
    if namespace == 0 and ident <= 0xFF:
        return bytes([0x00, ident])
    if namespace <= 0xFF and ident <= 0xFFFF:
        return bytes([0x01, namespace]) + struct.pack("<H", ident)
    return b"\x02" + struct.pack("<HI", namespace, ident)


def ack():
    body = struct.pack("<IIIII", 0, O.MAX_MESSAGE, O.MAX_MESSAGE, O.MAX_MESSAGE, 1)
    return b"ACKF" + struct.pack("<I", 8 + len(body)) + body


def message(mtype, request_id, body):
    payload = struct.pack("<III", CHANNEL, 1, request_id) + body
    return mtype + b"F" + struct.pack("<I", 8 + len(payload)) + payload


def rheader(result=0):
    return struct.pack("<qII", 0, 1, result) + b"\x00" + struct.pack("<i", 0) + b"\x00\x00\x00"


def service_reply(response_id, *fields):
    return node_id(0, response_id) + rheader() + b"".join(fields)


def read_header(reader):
    """Consume a request header, so the test can see what follows it."""
    reader.node_id()  # authentication token
    reader.i64()
    handle = reader.u32()
    reader.u32()  # returnDiagnostics
    reader.string("the audit entry id")
    reader.u32()  # timeoutHint
    reader.extension_object()
    return handle


def open_reply(request_id):
    return message(b"OPN", request_id, service_reply(
        O.OPEN_SECURE_CHANNEL_RESPONSE, struct.pack("<I", 0),
        struct.pack("<IIqI", CHANNEL, 1, 0, O.CHANNEL_LIFETIME_MS), bstring(b"")))


def create_reply(request_id):
    return message(b"MSG", request_id, service_reply(
        O.CREATE_SESSION_RESPONSE, node_id(1, 1234), node_id(1, 5678), struct.pack("<d", 60.0),
        bstring(b"nonce"), bstring(b""), struct.pack("<i", 0), struct.pack("<i", 0),
        string(None), bstring(None), struct.pack("<I", O.MAX_MESSAGE)))


def activate_reply(request_id):
    return message(b"MSG", request_id, service_reply(
        O.ACTIVATE_SESSION_RESPONSE, bstring(b"nonce"), struct.pack("<i", 0), struct.pack("<i", 0)))


def close_reply(request_id):
    return message(b"MSG", request_id, service_reply(O.CLOSE_SESSION_RESPONSE))


def read_reply(request_id, results):
    return message(b"MSG", request_id, service_reply(
        O.READ_RESPONSE, struct.pack("<i", len(results)), b"".join(results)))


def v_bool(value):
    return bytes([1, 1 if value else 0])


def v_i32(value):
    return bytes([6]) + struct.pack("<i", value)


def v_u32(value):
    return bytes([7]) + struct.pack("<I", value)


def v_float(value):
    return bytes([10]) + struct.pack("<f", value)


def v_double(value):
    return bytes([11]) + struct.pack("<d", value)


def v_string(value):
    return bytes([12]) + string(value)


def v_datetime(seconds):
    return bytes([13]) + struct.pack("<q", int((seconds + O.EPOCH_1601_TO_1970) * 10_000_000))


def v_bytes(raw):
    return bytes([15]) + bstring(raw)


def dv(variant):
    return bytes([0x01]) + variant


def dv_status(code):
    return bytes([0x02]) + struct.pack("<I", code)


def dv_both(variant, code):
    return bytes([0x03]) + variant + struct.pack("<I", code)


GOOD = {
    (2, "i", 1): dv(v_double(21.5)),
    (2, "i", 2): dv(v_i32(-42)),
    (2, "i", 3): dv(v_string("Supply air")),
    (2, "i", 4): dv(v_bool(True)),
    (2, "i", 5): dv(v_datetime(EPOCH)),
    (2, "i", 6): dv(v_bytes(b"\x01\x02\x03")),
    (2, "i", 7): dv(v_float(3.25)),
    (2, "i", 8): dv(v_u32(7)),
    (2, "i", 9): dv(v_datetime(EPOCH)),
}


class Server:
    """A fake OPC UA server: it answers the handshake, then the Read according to `mode`."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.mode = "good"
        self.requests = []  # (type, request id, service id, nodes)
        self.hello_url = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            conn.settimeout(5)
            try:
                self._loop(conn)
            except (EOFError, OSError):
                return

    def _loop(self, conn):
        while True:
            head = recv_exact(conn, 8)
            mtype, size = head[:3], int.from_bytes(head[4:8], "little")
            rest = recv_exact(conn, size - 8)
            if mtype == b"HEL":
                n = int.from_bytes(rest[20:24], "little", signed=True)
                self.hello_url = rest[24:24 + n].decode("utf-8") if n > 0 else ""
                self.requests.append(("HEL", 0, 0, None))
                conn.sendall(ack())
            elif mtype == b"OPN":
                request_id = int.from_bytes(rest[8:12], "little")
                self.requests.append(("OPN", request_id, 0, None))
                conn.sendall(open_reply(request_id))
            elif mtype == b"MSG":
                _, _, request_id = struct.unpack("<III", rest[:12])
                reader = O._Reader(rest[12:])
                service = reader.node_id()[2]
                read_header(reader)
                nodes = None
                if service == O.READ_REQUEST:
                    reader.f64()
                    reader.u32()
                    nodes = []
                    for _ in range(reader.count("nodes to read")):
                        nodes.append(reader.node_id())
                        reader.u32()
                        reader.string()
                        reader.u16()
                        reader.string()
                self.requests.append(("MSG", request_id, service, nodes))
                if service == O.CREATE_SESSION_REQUEST:
                    conn.sendall(create_reply(request_id))
                elif service == O.ACTIVATE_SESSION_REQUEST:
                    conn.sendall(activate_reply(request_id))
                elif service == O.READ_REQUEST:
                    self._answer_read(conn, request_id, nodes)
                elif service == O.CLOSE_SESSION_REQUEST:
                    conn.sendall(close_reply(request_id))
            elif mtype == b"CLO":
                return

    def _answer_read(self, conn, request_id, nodes):
        if self.mode == "silent":
            return
        results = [self._result(node) for node in nodes]
        if self.mode == "wrong-id":
            conn.sendall(read_reply(request_id + 1, [dv(v_double(999.0)) for _ in nodes]))
        elif self.mode == "malformed":
            conn.sendall(message(b"MSG", request_id, b"\xff\xff"))
        conn.sendall(read_reply(request_id, results))

    def _result(self, node):
        if self.mode == "bad-status":
            if node == (2, "i", 8):
                return dv_status(0x80340000)  # BadNodeIdUnknown
            if node == (2, "i", 2):
                return dv_both(v_i32(-42), 0x40920000)  # UncertainLastUsableValue
        return GOOD.get(node, dv_status(0x80340000))


server = Server()

# ---- config validation: address, path, policy, node id, and the read-only rule
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

r = put("source", "poll", {"type": "opcua",
                           "opcua": {"address": "", "path": "has space", "security_policy": "Basic256Sha256"},
                           "timeout": 0.5, "period": 0.1, "backoff_scale": 1})
assert r[0] == 422 and {"opcua.address", "opcua.path", "opcua.security_policy", "timeout", "period",
                        "backoff_scale"} <= props(r), props(r)
assert put("source", "poll", {"type": "opcua"})[0] == 422, "an OPC UA source needs its opcua section"
assert put("source", "poll", {"type": "opcua", "opcua": {"address": "127.0.0.1"}})[0] == 422
assert put("source", "poll", {"type": "opcua", "opcua": {"address": "127.0.0.1:70000"}})[0] == 422
assert "opcua.security_mode" in props(put("source", "poll", {
    "type": "opcua", "opcua": {"address": "127.0.0.1:14360", "security_mode": "Sign"}}))
try:
    O.OpcuaSource("direct", {"opcua": {"address": "127.0.0.1:14360", "security_policy": "Basic256Sha256"}})
    raise AssertionError("a secure policy must be refused")
except O.OpcuaError as e:
    assert "only SecurityPolicy None" in str(e)

SRC = {"type": "opcua", "opcua": {"address": f"127.0.0.1:{server.port}", "path": "plant",
                                  "security_policy": "None"}, "timeout": 1, "period": 60}
assert put("source", "poll", SRC)[0] == 200

POINTS = {
    "ai-temp": ({"node": "ns=2;i=1"}, None),
    "ai-count": ({"node": "ns=2;i=2"}, None),
    "ai-name": ({"node": "ns=2;i=3"}, "text"),
    "bi-run": ({"node": "ns=2;i=4"}, None),
    "dt-start": ({"node": "ns=2;i=5"}, None),
    "blob": ({"node": "ns=2;i=6"}, "text"),
    "ai-float": ({"node": "ns=2;i=7"}, None),
    "bad-node": ({"node": "ns=2;i=8"}, None),
    "dt-text": ({"node": "ns=2;i=9"}, "text"),
}
assert "opcua" in props(put("point", "p", {"source": "poll"}))
assert "opcua.node" in props(put("point", "p", {"source": "poll", "opcua": {"node": "bogus"}}))
assert "opcua.node" in props(put("point", "p", {"source": "poll", "opcua": {"node": 7}}))
assert "settable" in props(put("point", "p", {"source": "poll", "opcua": {"node": "ns=2;i=1"}, "settable": True}))
for name, (section, datatype) in POINTS.items():
    body = {"source": "poll", "opcua": section, **({"datatype": datatype} if datatype else {})}
    assert put("point", name, body)[0] == 200, (name, put("point", name, body))

src = next(s for s in app.engine.sources if s.name == "poll")
assert isinstance(src, O.OpcuaSource)
time.sleep(0.4)  # let the background thread's first cycle finish before driving one ourselves


def poll(mode, timeout=1.0):
    """Run one poll in a server mode and tick the engine, so qualities land on the points."""
    server.mode, src.timeout = mode, timeout
    server.requests.clear()
    src.cycle()
    app.tick(time.time())


# ---- Hello/Acknowledge, OpenSecureChannel, CreateSession, ActivateSession, one Read, CloseSession
poll("good")
requests = list(server.requests)
assert requests[0][0] == "HEL", requests
assert server.hello_url == f"opc.tcp://127.0.0.1:{server.port}/plant", server.hello_url
assert requests[1][0] == "OPN", "the channel opens after the Acknowledge"
services = [r[2] for r in requests if r[0] == "MSG"]
assert services == [O.CREATE_SESSION_REQUEST, O.ACTIVATE_SESSION_REQUEST, O.READ_REQUEST,
                    O.CLOSE_SESSION_REQUEST], services
reads = [r for r in requests if r[0] == "MSG" and r[2] == O.READ_REQUEST]
assert len(reads) == 1, "one Read per poll"
assert set(reads[0][3]) == {(2, "i", n) for n in range(1, 10)}, reads[0][3]

values = last()
assert values["ai-temp"] == 21.5 and values["ai-count"] == -42.0 and values["ai-float"] == 3.25, values
assert values["ai-name"] == "Supply air" and values["bi-run"] == 1.0, values
assert values["dt-start"] == float(EPOCH), values
assert values["dt-text"] == "2024-01-01T00:00:00+00:00", values
assert values["blob"] == "binary value (3 bytes)" and values["bad-node"] == 7.0, values
assert src.connected is True and src.error is None, src.error
assert all(src.points[name].quality == Quality.GOOD for name in POINTS)

# ---- a status-only Bad result is a bad point, an Uncertain value is kept as uncertain, the poll survives
poll("bad-status")
values = last()
assert src.connected is True and "point(s) rejected" in src.error, src.error
assert "BadNodeIdUnknown" in src.error and "UncertainLastUsableValue" in src.error, src.error
assert src.points["bad-node"].quality == Quality.BAD
assert src.points["ai-count"].quality == Quality.UNCERTAIN and values["ai-count"] == -42.0, values
assert src.points["ai-temp"].quality == Quality.GOOD and values["ai-temp"] == 21.5, values

# ---- a timeout is one sentence naming the server, and the source is unhealthy
server.mode, src.timeout = "silent", 0.5
server.requests.clear()
started = time.monotonic()
src.cycle()
assert time.monotonic() - started < 2.0
app.tick(time.time() + 1)
assert src.connected is False and "did not answer" in src.error and "OpcuaError" not in src.error, src.error
assert src.points["ai-temp"].quality == Quality.BAD and src.points["ai-name"].quality == Quality.BAD

# ---- a reply carrying another request's id is ignored: the wrong values never reach the points
poll("wrong-id")
assert src.connected is True and src.error is None, src.error
values = last()
assert values["ai-temp"] == 21.5 and values["ai-name"] == "Supply air", values

# ---- a malformed message is ignored rather than crashing: the valid reply behind it still updates
poll("malformed")
assert src.connected is True and src.error is None, src.error
assert last()["ai-temp"] == 21.5 and last()["ai-name"] == "Supply air"

# ---- recovery: a good poll after a failed one clears the fault and the BAD quality
poll("good")
assert src.connected is True and src.fails == 0 and src.error is None, src.error
assert last()["ai-temp"] == 21.5 and src.points["ai-temp"].quality == Quality.GOOD
print("ok")
