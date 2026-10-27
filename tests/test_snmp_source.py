"""SNMP v2c source: BER round trip, config validation, typed values, and every failure path over a fake agent.

The agent is a UDP socket on 127.0.0.1 answering crafted BER; nothing leaves the machine.
Run: python tests/test_snmp_source.py"""
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import snmp_source as S  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.core import Point, Quality  # noqa: E402
from _support import new_store  # noqa: E402

# ---- BER round trip, including a negative integer and a multi-byte OID component
for oid in ("1.3.6.1.2.1.1.5.0", "1.3.6.1.4.1.9999.1.0", "2.1"):
    assert S.decode_oid(S.encode_oid(oid)) == oid
assert S._int(-1) == b"\xff" and S._int(-129) == b"\xff\x7f" and S._int(128) == b"\x00\x80"
for bad in ("", "1", "1.3.x", "3.1", "1.40"):
    try:
        S.encode_oid(bad)
        raise AssertionError(bad)
    except S.SnmpError:
        pass

# ---- a fake agent: it parses each GET for its request id and OIDs, then answers per the current mode
INT_OID = "1.3.6.1.2.1.1.7.0"
COUNT_OID = "1.3.6.1.2.1.2.2.1.10.1"
GAUGE_OID = "1.3.6.1.2.1.2.2.1.5.1"
TICKS_OID = "1.3.6.1.2.1.1.3.0"
TEXT_OID = "1.3.6.1.2.1.1.5.0"
OIDVAL_OID = "1.3.6.1.2.1.1.2.0"
IP_OID = "1.3.6.1.2.1.4.20.1.1.1"
NULL_OID = "1.3.6.1.2.1.1.4.0"
SEQ_OID = "1.3.6.1.2.1.1.6.0"
UNKNOWN_OID = "1.3.6.1.2.1.1.8.0"

# ---- a small table for GETNEXT: three rows of one column, then the end of the MIB view
WALK = "1.3.6.1.2.1.31.1.1.1.6"
TABLE = [
    ("1.3.6.1.2.1.31.1.1.1.6.1.0", S._tlv(S.COUNTER32, (111).to_bytes(4, "big"))),
    ("1.3.6.1.2.1.31.1.1.1.6.2.0", S._tlv(S.COUNTER32, (222).to_bytes(4, "big"))),
    ("1.3.6.1.2.1.31.1.1.1.6.3.0", S._tlv(S.COUNTER32, (333).to_bytes(4, "big"))),
]

VALUES = {
    INT_OID: S._tlv(S.INTEGER, S._int(-42)),
    COUNT_OID: S._tlv(S.COUNTER32, (123456).to_bytes(4, "big")),
    GAUGE_OID: S._tlv(S.GAUGE32, (7).to_bytes(4, "big")),
    TICKS_OID: S._tlv(S.TIMETICKS, (987654).to_bytes(4, "big")),
    TEXT_OID: S._tlv(S.OCTET_STRING, b"router-1"),
    OIDVAL_OID: S._tlv(S.OBJECT_ID, S.encode_oid("1.3.6.1.4.1.8072.3.2.10")),
    IP_OID: S._tlv(S.IP_ADDRESS, bytes([192, 0, 2, 7])),
    NULL_OID: S._tlv(S.NULL, b""),
    SEQ_OID: S._tlv(S.SEQUENCE, b""),
    UNKNOWN_OID: S._tlv(0x9F, b"\x01"),
}


def parse_request(data):
    """(PDU tag, request id, OIDs) from a GET or GETNEXT datagram, so the fake agent can echo the id."""
    tag, body, _ = S._read_tlv(data, 0, "the request")
    assert tag == S.SEQUENCE
    pos = 0
    _, version, pos = S._read_tlv(body, pos, "the version")
    assert S._int_value(version, "the version") == 1
    _, _, pos = S._read_tlv(body, pos, "the community")
    tag, pdu, pos = S._read_tlv(body, pos, "the request PDU")
    assert tag in (S.GET_REQUEST, S.GET_NEXT)
    pos = 0
    _, raw, pos = S._read_tlv(pdu, pos, "the request id")
    request_id = S._int_value(raw, "the request id")
    for what in ("the error status", "the error index"):
        _, _, pos = S._read_tlv(pdu, pos, what)
    _, binds, pos = S._read_tlv(pdu, pos, "the variable bindings")
    oids, p = [], 0
    while p < len(binds):
        _, bind, p = S._read_tlv(binds, p, "a variable binding")
        _, oid_raw, _ = S._read_tlv(bind, 0, "an OID")
        oids.append(S.decode_oid(oid_raw))
    return tag, request_id, oids


def response_binds(request_id, binds, status=0, index=0):
    """A GET response datagram carrying explicit (OID, encoded value) bindings."""
    body = b"".join(S._tlv(S.SEQUENCE, S._tlv(S.OBJECT_ID, S.encode_oid(o)) + v) for o, v in binds)
    pdu = S._tlv(S.GET_RESPONSE, S._tlv(S.INTEGER, S._int(request_id)) + S._tlv(S.INTEGER, S._int(status))
                 + S._tlv(S.INTEGER, S._int(index)) + S._tlv(S.SEQUENCE, body))
    return S._tlv(S.SEQUENCE, S._tlv(S.INTEGER, S._int(1)) + S._tlv(S.OCTET_STRING, b"public") + pdu)


def response(request_id, oids, status=0, index=0):
    """A GET response datagram echoing oids, with VALUES for every OID."""
    return response_binds(request_id, [(o, VALUES.get(o, S._tlv(S.NULL, b""))) for o in oids], status, index)


def next_bind(oid):
    """(OID, encoded value) for the first table row after `oid`, or an endOfMibView at the end."""
    key = S._oid_key(oid)
    for row, value in TABLE:
        if S._oid_key(row) > key:
            return row, value
    return oid, S._tlv(S.END_OF_MIB_VIEW, b"")


class Agent:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.mode = "good"
        self.drops = 0
        self.requests = []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(65535)
            except OSError:
                return
            tag, request_id, oids = parse_request(data)
            self.requests.append((tag, request_id, oids))
            reply = self._reply(tag, request_id, oids)
            if reply is not None:
                try:
                    self.sock.sendto(reply, addr)
                except OSError:
                    return

    def _reply(self, tag, request_id, oids):
        if self.mode == "silent":
            return None
        if self.mode == "error":
            return response(request_id, oids, status=2, index=1)
        if self.mode == "wrong-id":
            return response(request_id + 1, oids)
        if self.mode == "malformed":
            return b"\x30\x82\xff\xff"
        if tag == S.GET_NEXT:
            if self.mode == "cap":  # a tree that never ends, so the walk reaches its cap
                return response_binds(request_id, [(oids[0] + ".1", S._tlv(S.INTEGER, S._int(1)))])
            if self.mode == "drop":  # answer the first row, then go quiet, so the walk times out mid-way
                self.drops += 1
                return None if self.drops > 1 else response_binds(request_id, [next_bind(oids[0])])
            return response_binds(request_id, [next_bind(oids[0])])
        return response(request_id, oids)


agent = Agent()

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

r = put("source", "poll", {"type": "snmp", "host": "", "port": 0, "community": "", "timeout": 0.5, "period": 0.1,
                           "backoff_scale": 1})
assert r[0] == 422 and {"host", "port", "community", "timeout", "period", "backoff_scale"} <= props(r), props(r)
assert put("source", "poll", {"type": "snmp"})[0] == 422
SRC = {"type": "snmp", "host": "127.0.0.1", "port": agent.port, "community": "public", "timeout": 1, "period": 60}
assert put("source", "poll", SRC)[0] == 200

POINTS = {
    "num-int": (INT_OID, None), "text-int": (INT_OID, "text"),
    "num-count": (COUNT_OID, None), "num-gauge": (GAUGE_OID, None), "num-ticks": (TICKS_OID, None),
    "text-name": (TEXT_OID, "text"), "text-oid": (OIDVAL_OID, "text"), "text-ip": (IP_OID, "text"),
    "bad-null": (NULL_OID, None), "bad-seq": (SEQ_OID, None), "bad-unknown": (UNKNOWN_OID, None),
}
for n, (oid, datatype) in POINTS.items():
    body = {"source": "poll", "snmp": {"address": oid}, **({"datatype": datatype} if datatype else {})}
    assert put("point", n, body)[0] == 200, (n, put("point", n, body))

assert "snmp" in props(put("point", "p", {"source": "poll"}))
assert "snmp.address" in props(put("point", "p", {"source": "poll", "snmp": {"address": "1.3.bad"}}))
assert "snmp.address" in props(put("point", "p", {"source": "poll", "snmp": {"address": 7}}))
assert "snmp.walk" in props(put("point", "p", {"source": "poll", "snmp": {"walk": "1.3.bad"}})), "a non-numeric walk is refused"
assert "snmp.walk" in props(put("point", "p", {"source": "poll", "snmp": {"walk": 7}}))
assert "snmp" in props(put("point", "p", {"source": "poll", "snmp": {"address": INT_OID, "walk": WALK}}))
assert "snmp" in props(put("point", "p", {"source": "poll", "snmp": {}}))
assert "settable" in props(put("point", "p", {"source": "poll", "snmp": {"address": INT_OID}, "settable": True}))

# ---- a good poll: one request for every OID, every type decoded, unreadable values dropped and counted
src = next(s for s in app.engine.sources if s.name == "poll")
time.sleep(0.4)  # let the background thread's first cycle finish before we drive one ourselves
before = len(agent.requests)
src.cycle()
assert len(agent.requests) - before == 1, "one request per poll"
_, request_id, oids = agent.requests[-1]
assert len(oids) == len(set(oids)) == 10 and set(oids) == set(VALUES), oids  # int is shared by two points, sent once
app.tick(time.time())
v = last()
assert v["num-int"] == -42.0 and v["text-int"] == "-42", v
assert v["num-count"] == 123456.0 and v["num-gauge"] == 7.0 and v["num-ticks"] == 987654.0, v
assert v["text-name"] == "router-1" and v["text-oid"] == "1.3.6.1.4.1.8072.3.2.10" and v["text-ip"] == "192.0.2.7", v
assert v["bad-null"] is None and v["bad-seq"] is None and v["bad-unknown"] is None, v
assert src.connected is True and "3 point(s) rejected" in src.error, src.error
assert "bad-null" in src.error and "no value" in src.error and "unsupported type" in src.error, src.error
assert src.points["num-int"].quality == Quality.GOOD and src.points["bad-null"].quality == Quality.BAD

# ---- a noSuchName error PDU: the device is reachable, the points are refused, nothing raises
agent.mode = "error"
src.cycle()
app.tick(time.time() + 1)
assert src.connected is True and "no object" in src.error and "point(s) rejected" in src.error, src.error
assert src.points["num-int"].quality == Quality.BAD and src.points["text-name"].quality == Quality.BAD

# ---- a wrong request-id is source health, not a crash and not a bad point
agent.mode = "wrong-id"
src.cycle()
assert src.connected is False and "answered request" in src.error and "SnmpError" not in src.error, src.error
assert src.points["num-int"].quality == Quality.BAD

# ---- a malformed reply is source health too
agent.mode = "malformed"
src.cycle()
assert src.connected is False and "reply" in src.error and "SnmpError" not in src.error, src.error

# ---- timeout: one retry, a short timeout, and no hang
agent.mode = "silent"
src.timeout = 0.1
before = len(agent.requests)
started = time.monotonic()
src.cycle()
elapsed = time.monotonic() - started
assert len(agent.requests) - before == 2, "the request is retried once, no more"
assert src.connected is False and "did not answer within 0.1s" in src.error and "SnmpError" not in src.error, src.error
assert elapsed < 2.0, elapsed

# ---- an unreachable host: the socket may time out or report the closed port, but it is still one sentence
probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
probe.bind(("127.0.0.1", 0))
dead_port = probe.getsockname()[1]
probe.close()
dead = S.SnmpSource("dead", {"host": "127.0.0.1", "port": dead_port, "community": "public", "timeout": 0.1})
dead.add(Point("x", meta={"snmp": {"address": INT_OID}}))
started = time.monotonic()
dead.cycle()
assert time.monotonic() - started < 2.0
assert dead.connected is False and any(m in dead.error for m in ("cannot reach", "cannot send to", "did not answer within 0.1s")), dead.error
assert "SnmpError" not in dead.error


def walker(points, timeout=1.0):
    """A source holding only walk points, so a walk test costs no exact GETs."""
    s = S.SnmpSource("walker", {"host": "127.0.0.1", "port": agent.port, "community": "public", "timeout": timeout})
    for name, walk in points:
        s.add(Point(name, meta={"snmp": {"walk": walk}}))
    return s


# ---- a normal walk: GETNEXT one row per step until endOfMibView, mapped to the first leaf under the subtree
agent.mode = "good"
w = walker([("out-octets", WALK)])
before = len(agent.requests)
w.cycle()
value, _, quality = w.poll(time.time())["out-octets"]
assert value == 111.0 and quality == Quality.GOOD, (value, quality)
steps = agent.requests[before:]
assert len(steps) == 4, "three rows plus the endOfMibView step"
assert all(tag == S.GET_NEXT for tag, _, _ in steps), steps
assert steps[0][2] == [WALK] and steps[-1][2] == ["1.3.6.1.2.1.31.1.1.1.6.3.0"], steps
assert w.connected is True and w.error is None, w.error

# ---- a subtree ending early is normal: walking the last row's root stops when the next OID leaves it
agent.mode = "good"
w2 = walker([("row-two", WALK + ".2")])
before = len(agent.requests)
w2.cycle()
assert w2.poll(time.time())["row-two"][0] == 222.0
assert len(agent.requests) - before == 2, "row .2.0, then row .3.0 shows the subtree has ended"
assert w2.connected is True and w2.error is None, w2.error

# ---- a huge tree stops at the cap and says so on the source, without losing the first value
saved, S.WALK_LIMIT = S.WALK_LIMIT, 5
try:
    agent.mode = "cap"
    w3 = walker([("deep", "1.3.6.1.2.1.99")])
    before = len(agent.requests)
    w3.cycle()
    assert w3.poll(time.time())["deep"][0] == 1.0
    assert len(agent.requests) - before == 5, "the walk stops at the cap"
    assert w3.connected is True and "cap" in w3.error and "SnmpError" not in w3.error, w3.error
finally:
    S.WALK_LIMIT = saved

# ---- a timeout mid-walk keeps the rows already read and reports the sentence, one retry and no hang
agent.mode = "drop"
agent.drops = 0
w4 = walker([("out-octets", WALK)], timeout=0.1)
started = time.monotonic()
w4.cycle()
assert time.monotonic() - started < 2.0
assert w4.poll(time.time())["out-octets"][0] == 111.0, "the first row survives the timeout"
assert w4.connected is True and "did not answer within 0.1s" in w4.error and "SnmpError" not in w4.error, w4.error

# ---- recovery: the next good poll clears the fault and refreshes the values
agent.mode = "good"
src.timeout = 2
src.cycle()
app.tick(time.time() + 2)
assert src.connected is True and src.fails == 0 and "point(s) rejected" in src.error, src.error
assert last()["num-int"] == -42.0 and src.points["num-int"].quality == Quality.GOOD
print("ok")
