"""BACnet/IP source: Who-Is/I-Am binding, ReadPropertyMultiple, segmentation, errors and failures.

A fake device is a UDP socket on 127.0.0.1 answering crafted BACnet/IP frames; nothing leaves the machine.
Run: python tests/test_bacnet_source.py"""
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian import bacnet_source as B  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.core import Quality  # noqa: E402
from _support import new_store  # noqa: E402

DEVICE = 777

# ---- the objects the fake device exposes: (object type, instance, property, encoded value)
GOOD = [
    (0, 1, 85, B._app_real(21.5)),                        # analogInput 1 presentValue
    (0, 1, 77, B._app_text("Supply air")),                # analogInput 1 objectName
    (3, 2, 85, B._app_enumerated(1)),                     # binaryInput 2 presentValue
    (2, 3, 111, B._app(8, b"\x04\x40")),                  # analogValue 3 statusFlags: fault
    (2, 3, 117, B._app_enumerated(62)),                   # analogValue 3 units
    (19, 4, 85, B._app_enumerated(3)),                    # multiStateValue 4 presentValue
]
BAD = (0, 1, 85)
VALUES = {(t, i, p): v for t, i, p, v in GOOD}


def _apdu(apdu):
    return B._bvlc(B._npdu(apdu))


def iam(device):
    apdu = (bytes([0x10, 0x00]) + B._app_object_id(8, device) + B._app_unsigned(480)
            + B._app_enumerated(3) + B._app_unsigned(999))
    return _apdu(apdu)


def rpm_body(results):
    """The body of a ReadPropertyMultiple-ACK, without the APDU header."""
    body = b""
    for object_type, instance, prop, value in results:
        body += (B._context(0, B._object_id(object_type, instance)) + B._open(1)
                 + B._context_unsigned(2, prop) + B._open(4) + value + B._close(4) + B._close(1))
    return body


def rpm_ack(invoke, results=GOOD):
    return _apdu(bytes([0x30, invoke, 0x0E]) + rpm_body(results))


def rpm_ack_error(invoke, object_type, instance, prop, error_class=1, error_code=31):
    body = (B._context(0, B._object_id(object_type, instance)) + B._open(1)
            + B._context_unsigned(2, prop) + B._open(5)
            + B._app_enumerated(error_class) + B._app_enumerated(error_code) + B._close(5) + B._close(1))
    return _apdu(bytes([0x30, invoke, 0x0E]) + body)


def rp_ack(invoke, value):
    return _apdu(bytes([0x30, invoke, 0x0C]) + value)


def error_pdu(invoke, service, error_class=1, error_code=31):
    return _apdu(bytes([0x50, invoke, service]) + B._app_enumerated(error_class) + B._app_enumerated(error_code))


def reject_pdu(invoke, reason=2):
    return _apdu(bytes([0x60, invoke, reason]))


def parse_rpm(body):
    """[(object type, instance, [property ids])] from a ReadPropertyMultiple request body."""
    out, pos = [], 0
    while pos < len(body):
        number, context, lvt, after = B._read_tag(body, pos)
        assert context and number == 0 and lvt == 4
        raw = body[after:after + 4]
        object_type, instance = int.from_bytes(raw, "big") >> 22, int.from_bytes(raw, "big") & B.MAX_INSTANCE
        pos = after + 4
        number, context, lvt, after = B._read_tag(body, pos)
        assert context and number == 1 and lvt == 6
        pos, props = after, []
        while True:
            number, context, lvt, after = B._read_tag(body, pos)
            if context and number == 1 and lvt == 7:
                pos = after
                break
            assert context and number == 0
            raw, pos = B._content(body, after, lvt)
            props.append(int.from_bytes(raw, "big") if raw else 0)
        out.append((object_type, instance, props))
    return out


def parse_rp(body):
    """(object type, instance, property id) from a ReadProperty request body."""
    number, context, lvt, after = B._read_tag(body, 0)
    assert context and number == 0 and lvt == 4
    raw = body[after:after + 4]
    object_type, instance = int.from_bytes(raw, "big") >> 22, int.from_bytes(raw, "big") & B.MAX_INSTANCE
    number, context, lvt, after = B._read_tag(body, after + 4)
    assert context and number == 1
    raw, _ = B._content(body, after, lvt)
    return object_type, instance, int.from_bytes(raw, "big") if raw else 0


class Device:
    """A fake BACnet/IP device: it answers Who-Is, then answers the requests according to `mode`."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.mode = "good"
        self.requests = []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(65535)
            except OSError:
                return
            try:
                msg = B.decode_datagram(data)
            except B.BacnetError:
                continue
            self.requests.append(msg)
            for reply in self._replies(msg):
                try:
                    self.sock.sendto(reply, addr)
                except OSError:
                    return

    def _replies(self, msg):
        if msg["kind"] == "who-is":
            return [iam(self.device)]
        if msg["kind"] == "segment-ack":
            return []
        if self.mode == "silent":
            return []
        if msg["kind"] == "confirmed" and msg["service"] == B.SERVICE_READ_PROPERTY_MULTIPLE:
            if self.mode == "wrong-id":
                return [rpm_ack((msg["invoke"] + 1) % 256)]
            if self.mode == "fallback":
                return [reject_pdu(msg["invoke"])]
            if self.mode == "segmented":
                return self._segments(msg["invoke"])
            if self.mode == "malformed-then-good":
                return [b"\x81\x0a\x00\x40", rpm_ack(msg["invoke"])]
            return [rpm_ack(msg["invoke"])]
        if msg["kind"] == "confirmed" and msg["service"] == B.SERVICE_READ_PROPERTY:
            key = parse_rp(msg["body"])
            if self.mode == "fallback" and key == BAD:
                return [error_pdu(msg["invoke"], B.SERVICE_READ_PROPERTY)]
            return [rp_ack(msg["invoke"], VALUES[key])]
        return []

    def _segments(self, invoke):
        """Split one RPM ACK body into three segments, as a segmented device would."""
        body = rpm_body(GOOD)
        cuts = (0, len(body) // 3, 2 * len(body) // 3, len(body))
        chunks = [body[cuts[i]:cuts[i + 1]] for i in range(3)]
        out = []
        for i, chunk in enumerate(chunks):
            flags = 0x3C if i < 2 else 0x38  # segmented, more follows until the last
            out.append(_apdu(bytes([flags, invoke, i, 1, 0x0E]) + chunk))
        return out

    @property
    def device(self):
        return DEVICE + (1 if self.mode == "iam-other" else 0)


device = Device()

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

r = put("source", "poll", {"type": "bacnet", "bacnet": {"address": "", "device": -1},
                           "timeout": 0.5, "period": 0.1, "backoff_scale": 1})
assert r[0] == 422 and {"bacnet.address", "bacnet.device", "timeout", "period", "backoff_scale"} <= props(r), props(r)
assert put("source", "poll", {"type": "bacnet"})[0] == 422, "a BACnet source needs its bacnet section"
assert put("source", "poll", {"type": "bacnet", "bacnet": {"address": "127.0.0.1:0", "device": 1}})[0] == 422
SRC = {"type": "bacnet", "bacnet": {"address": f"127.0.0.1:{device.port}", "device": DEVICE},
       "timeout": 1, "period": 60}
assert put("source", "poll", SRC)[0] == 200

POINTS = {
    "ai-temp": ({"object_type": "analogInput", "instance": 1, "property": "presentValue"}, None),
    "ai-name": ({"object_type": "analogInput", "instance": 1, "property": "objectName"}, "text"),
    "bi-run": ({"object_type": "binaryInput", "instance": 2, "property": "presentValue"}, None),
    "av-flags": ({"object_type": "analogValue", "instance": 3, "property": "statusFlags"}, None),
    "av-units": ({"object_type": "analogValue", "instance": 3, "property": "units"}, None),
    "ms-state": ({"object_type": "multiStateValue", "instance": 4, "property": "presentValue"}, None),
}
assert "bacnet" in props(put("point", "p", {"source": "poll"}))
assert "bacnet.object_type" in props(put("point", "p", {"source": "poll", "bacnet": {
    "object_type": "thermometer", "instance": 1, "property": "presentValue"}}))
assert "bacnet.instance" in props(put("point", "p", {"source": "poll", "bacnet": {
    "object_type": "analogInput", "instance": B.MAX_INSTANCE + 1, "property": "presentValue"}}))
assert "bacnet.property" in props(put("point", "p", {"source": "poll", "bacnet": {
    "object_type": "analogInput", "instance": 1, "property": "temperature"}}))
assert "settable" in props(put("point", "p", {"source": "poll", "bacnet": {
    "object_type": "analogInput", "instance": 1, "property": "presentValue"}, "settable": True}))
for name, (section, datatype) in POINTS.items():
    body = {"source": "poll", "bacnet": section, **({"datatype": datatype} if datatype else {})}
    assert put("point", name, body)[0] == 200, (name, put("point", name, body))

src = next(s for s in app.engine.sources if s.name == "poll")
assert isinstance(src, B.BacnetSource)
deadline = time.monotonic() + 5
while src.connected is None and time.monotonic() < deadline:  # the background thread's first cycle
    time.sleep(0.02)


def acks(n=3, timeout=3.0):
    """The SegmentACKs the device has read. They travel on the same socket as the reply, so the poll can return
    before the device thread has read them: wait for them instead of assuming they arrived."""
    deadline = time.monotonic() + timeout
    while True:
        got = [m for m in device.requests if m["kind"] == "segment-ack"]
        if len(got) >= n or time.monotonic() >= deadline:
            return got
        time.sleep(0.01)


def good_poll():
    """Run one poll in good mode and return the point values after the tick."""
    device.mode = "good"
    src._peer, src.timeout = None, 1
    device.requests.clear()
    src.cycle()
    app.tick(time.time())
    return last()


# ---- Who-Is/I-Am binding, then one ReadPropertyMultiple for every object
values = good_poll()
assert device.requests[0]["kind"] == "who-is", device.requests
assert src._peer is not None and src._peer[1] == device.port
rpm = [m for m in device.requests if m["kind"] == "confirmed" and m["service"] == B.SERVICE_READ_PROPERTY_MULTIPLE]
assert len(rpm) == 1, "one ReadPropertyMultiple per poll"
objects = parse_rpm(rpm[0]["body"])
assert sorted(objects) == [(0, 1, [77, 85]), (2, 3, [111, 117]), (3, 2, [85]), (19, 4, [85])], objects
assert not [m for m in device.requests if m["kind"] == "confirmed" and m["service"] == B.SERVICE_READ_PROPERTY]
assert values["ai-temp"] == 21.5 and values["ai-name"] == "Supply air" and values["bi-run"] == 1.0, values
assert values["av-flags"] == 2.0 and values["av-units"] == 62.0 and values["ms-state"] == 3.0, values
assert src.connected is True and src.error is None, src.error
assert src.points["ai-temp"].quality == Quality.GOOD and src.points["ai-name"].quality == Quality.GOOD

# ---- a segmented ComplexACK arrives in three pieces; the client acknowledges each and reassembles
device.mode = "segmented"
src._peer, device.requests = None, []
src.cycle()
app.tick(time.time() + 1)
got = acks()
assert len(got) == 3, ("each segment is acknowledged", got)
values = last()
assert values["ai-temp"] == 21.5 and values["ai-name"] == "Supply air" and values["ms-state"] == 3.0, values
assert src.connected is True and src.error is None, src.error

# ---- a reply carrying another request's invoke id is ignored, and the source says why
device.mode = "wrong-id"
src._peer, src.timeout, device.requests = None, 0.1, []
src.cycle()
app.tick(time.time() + 1)
assert src.connected is False and "answered request" in src.error and "BacnetError" not in src.error, src.error
assert src.points["ai-temp"].quality == Quality.BAD

# ---- a device that refuses ReadPropertyMultiple falls back to ReadProperty; an Error PDU bad-points one object
device.mode = "fallback"
src._peer, src.timeout, device.requests = None, 1, []
src.cycle()
app.tick(time.time() + 2)
reads = [m for m in device.requests if m["kind"] == "confirmed" and m["service"] == B.SERVICE_READ_PROPERTY]
assert len(reads) == len(POINTS), ("one ReadProperty per point after the refusal", reads)
values = last()
assert src.points["ai-temp"].quality == Quality.BAD, "the bad object is bad, not the poll"
assert values["ai-name"] == "Supply air" and values["bi-run"] == 1.0 and values["ms-state"] == 3.0, values
assert src.connected is True and "point(s) rejected" in src.error, src.error
assert "ai-temp" in src.error and "no such object" in src.error, src.error
assert src.points["ai-temp"].quality == Quality.BAD and src.points["ai-name"].quality == Quality.GOOD

# ---- a timeout is one sentence naming the device, and the source is unhealthy
device.mode = "silent"
src._peer, src.timeout, device.requests = None, 0.1, []
started = time.monotonic()
src.cycle()
app.tick(time.time() + 2)
assert time.monotonic() - started < 2.0
assert src.connected is False and "did not answer" in src.error and "BacnetError" not in src.error, src.error
assert src.points["ai-temp"].quality == Quality.BAD and src.points["ai-name"].quality == Quality.BAD

# ---- the configured device instance is not on the network: say so, and name the instance that did answer
device.mode = "iam-other"
src._peer, src.timeout, device.requests = None, 0.2, []
src.cycle()
assert src.connected is False and f"device instance {DEVICE}" in src.error, src.error
assert str(DEVICE + 1) in src.error and "check the instance number" in src.error, src.error
assert "BacnetError" not in src.error

# ---- a malformed datagram is ignored, not fatal: the valid reply behind it still updates the points
device.mode = "malformed-then-good"
src._peer, src.timeout, device.requests = None, 1, []
src.cycle()
app.tick(time.time() + 3)
values = last()
assert values["ai-temp"] == 21.5 and values["ai-name"] == "Supply air", values
assert src.connected is True and src.error is None, src.error

# ---- recovery: a good poll clears the fault
device.mode = "good"
src._peer, src.timeout = None, 1
src.cycle()
app.tick(time.time() + 4)
assert src.connected is True and src.fails == 0 and src.error is None, src.error
assert last()["ai-temp"] == 21.5 and src.points["ai-temp"].quality == Quality.GOOD
print("ok")
