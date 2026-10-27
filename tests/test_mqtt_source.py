"""Plain MQTT source: topic mapping, payload shapes, quality, commands and the paho 2.0 guard, with no broker.
Run: python tests/test_mqtt_source.py"""
import importlib
import json
import os
import queue
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paho.mqtt.client as paho_client  # noqa: E402
import durian.mqtt_source as M  # noqa: E402
from durian.core import Engine, Point, Quality  # noqa: E402

CREDS = {"host": "broker.example", "port": 8883, "ca": "ca.pem", "user": "u", "password": "p"}


class Fake:
    """A paho client stand-in: construction, polling and stopping never touch a socket."""

    def __init__(self):
        self.connect_async_args, self.subscribed, self.published = None, [], []
        self.loop_stopped = self.disconnected = False
        self.publish_rc = 0

    def connect_async(self, host, port): self.connect_async_args = (host, port)

    def loop_start(self): pass

    def loop_stop(self): self.loop_stopped = True

    def disconnect(self): self.disconnected = True

    def subscribe(self, topic, qos=0): self.subscribed.append((topic, qos))

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))
        return types.SimpleNamespace(rc=self.publish_rc)


def source(prefix="durian/", cmd_prefix="durian-cmd/", factory=None):
    return M.MqttSource("plant", CREDS, prefix, cmd_prefix, factory, client=Fake())


def feed(src, topic, payload):
    src._on_message(None, None, types.SimpleNamespace(topic=topic, payload=payload))


# ---- a bare number and {"value": ...} map the topic under the prefix to a point, auto-created by the factory
made = []
src = source(factory=lambda n: made.append(n) or Point(n))
assert src.poll(99.0) == {}
feed(src, "durian/line1/temp", b"21.5")
assert src.poll(100.0) == {"line1/temp": (21.5, 100.0, Quality.GOOD)}
assert made == ["line1/temp"] and "line1/temp" in src.points
feed(src, "durian/line1/temp", b'{"value": 22}')
assert src.poll(101.0) == {"line1/temp": (22.0, 101.0, Quality.GOOD)}
feed(src, "durian/line1/pump/speed", b'{"value": 1450}')
assert src.poll(102.0) == {"line1/pump/speed": (1450.0, 102.0, Quality.GOOD)}
assert src.dropped == 0, src.dropped

# ---- a message on the prefix itself has no point name and is dropped and counted
feed(src, "durian/", b"1")
assert src.poll(103.0) == {} and src.dropped == 1, src.dropped

# ---- text points read through parse_text; empty or non-text payloads are dropped and counted
src = source(factory=lambda n: Point(n))
src.add(Point("status", text=True))
feed(src, "durian/status", b'"Running"')
assert src.poll(100.0) == {"status": ("Running", 100.0, Quality.GOOD)}
feed(src, "durian/status", b'{"value": "Stopped"}')
assert src.poll(101.0) == {"status": ("Stopped", 101.0, Quality.GOOD)}
feed(src, "durian/status", b"")
feed(src, "durian/status", b'{"value": 5}')
assert src.poll(102.0) == {} and src.dropped == 2, src.dropped

# ---- a numeric point does not quietly accept text
src = source(factory=lambda n: Point(n))
feed(src, "durian/temp", b'"Running"')
assert src.poll(100.0) == {} and src.dropped == 1, src.dropped

# ---- JSON NaN and Infinity, booleans, nulls, objects without a value and plain garbage are dropped and counted
src = source(factory=lambda n: Point(n))
for bad in (b"NaN", b"Infinity", b"-Infinity", b"true", b'{"value": null}', b'{"other": 1}', b"not json", b"\xff", b""):
    feed(src, "durian/bad", bad)
assert src.poll(100.0) == {} and src.dropped == 9, src.dropped

# ---- quality and the source timestamp come from {"value", "ts", "quality"}; an unreadable quality is UNCERTAIN
src = source(factory=lambda n: Point(n))
feed(src, "durian/level", b'{"value": 7.5, "ts": 1700000000, "quality": "uncertain"}')
assert src.poll(100.0) == {"level": (7.5, 1700000000.0, Quality.UNCERTAIN)}
feed(src, "durian/level-ms", b'{"value": 1, "ts": 1700000000000, "quality": "BAD"}')
assert src.poll(101.0) == {"level-ms": (1.0, 1700000000.0, Quality.BAD)}
feed(src, "durian/level-num", b'{"value": 2, "ts": 1700000001, "quality": 1}')
assert src.poll(102.0) == {"level-num": (2.0, 1700000001.0, Quality.UNCERTAIN)}
feed(src, "durian/level-silly", b'{"value": 3, "quality": "silly"}')
assert src.poll(103.0) == {"level-silly": (3.0, 103.0, Quality.UNCERTAIN)}
feed(src, "durian/level-bad-ts", b'{"value": 4, "ts": "yesterday"}')
assert src.poll(104.0) == {"level-bad-ts": (4.0, 104.0, Quality.GOOD)}

# ---- an unknown topic is dropped when there is no factory, or once AUTO_POINTS_MAX is reached
src = source()
feed(src, "durian/a", b"1")
assert src.poll(100.0) == {} and src.dropped == 1 and "a" not in src.points

made = []
src = source(factory=lambda n: made.append(n) or Point(n))
src.AUTO_POINTS_MAX = 2
for name in ("a", "b", "c"):
    feed(src, f"durian/{name}", b"1")
assert set(src.poll(100.0)) == {"a", "b"} and made == ["a", "b"] and src.dropped == 1, (made, src.dropped)

# ---- a full queue drops on the broker's thread and counts, rather than blocking it
src = source()
src.q = queue.Queue(maxsize=2)
for i in range(3):
    feed(src, f"durian/t{i}", b"1")
assert src.q.qsize() == 2 and src.dropped == 1, (src.q.qsize(), src.dropped)

# ---- connect and disconnect set `connected` and an operator sentence; a reconnect subscribes again
src = source()
assert src.connected is False and "broker.example:8883" in src.error, src.error
assert src.client.connect_async_args == ("broker.example", 8883)
src._on_connect(src.client, None, None, 0, None)
assert src.connected is True and src.error is None
assert src.client.subscribed == [("durian/#", 1)], src.client.subscribed
src._on_disconnect(src.client, None, None, 7, None)
assert src.connected is False and "lost" in src.error and "7" in src.error, src.error
src._on_connect(src.client, None, None, 5, None)
assert src.connected is False and "refused" in src.error and "5" in src.error, src.error
assert "Exception" not in src.error
src._on_connect(src.client, None, None, 0, None)
src._on_disconnect(src.client, None, None, 0, None)
assert "closed" in src.error, src.error

# ---- write publishes a JSON value under cmd_prefix, and a broker error raises an actionable sentence
src = source(cmd_prefix="plant-cmd/")
src.write("pump-setpoint", 40)
assert src.client.published == [("plant-cmd/pump-setpoint", json.dumps({"value": 40}), 1)], src.client.published
src.client.publish_rc = 4
try:
    src.write("pump-setpoint", 41)
except RuntimeError as ex:
    message = str(ex)
else:
    raise AssertionError("a failed publish was not reported")
assert "publish" in message and "4" in message and "Exception" not in message, message

# ---- stop stops the loop and disconnects
src = source()
src.stop()
assert src.client.loop_stopped and src.client.disconnected

# ---- the engine consumes poll(ts): a BAD-quality payload marks the point without inventing a value
src = source(factory=lambda n: Point(n))
engine = Engine()
engine.sources.append(src)
feed(src, "durian/line1/temp", b'{"value": 21.5, "quality": "uncertain"}')
engine.tick(100.0)
point = src.points["line1/temp"]
assert point.last == 21.5 and point.last_ts == 100.0 and point.quality == Quality.UNCERTAIN, (point.last, point.last_ts, point.quality)
feed(src, "durian/line1/temp", b'{"value": 99, "quality": "bad"}')
engine.tick(101.0)
assert point.last == 21.5 and point.quality == Quality.BAD, (point.last, point.quality)

# ---- an old paho (1.x) is refused at import with a sentence naming the requirement, not an AttributeError
saved = paho_client.CallbackAPIVersion
del paho_client.CallbackAPIVersion
try:
    try:
        importlib.reload(M)
    except ImportError as ex:
        message = str(ex)
    else:
        message = ""
finally:
    paho_client.CallbackAPIVersion = saved
    importlib.reload(M)  # leave the module usable for anything that runs after this block
assert "paho-mqtt 2.0" in message and "upgrade" in message.lower(), message
assert "AttributeError" not in message and "Exception" not in message, message

print("ok")
