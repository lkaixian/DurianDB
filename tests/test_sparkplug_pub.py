"""Sparkplug B publisher: NBIRTH on connect, DBIRTH for the current values, DDATA for a change, read back
by the codec and by the existing source. Run: python tests/test_sparkplug_pub.py

Layer 1 needs no broker: the publisher writes onto a fake paho client and durian.sparkplug_codec, the same
decoder the Sparkplug source uses, reads every payload back, so the wire and the topics are proven without a
socket.
Layer 2 runs only when a plain MQTT broker answers 127.0.0.1:1883: the real Outputs publisher feeds a value
and the real SparkplugSource reads it back. No broker is an honest SKIP with its reason, never a silent pass.
"""
import os
import random
import socket
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from durian.core import Point  # noqa: E402
from durian.sparkplug_codec import Metric, decode_payload  # noqa: E402
from durian.sparkplug_publisher import SparkplugPublisher  # noqa: E402


class Fake:
    """A paho client stand-in: construction, publishing and stopping never touch a socket."""

    def __init__(self):
        self.published, self.on_connect = [], None

    def connect_async(self, *a, **k): pass

    def loop_start(self): pass

    def loop_stop(self): pass

    def disconnect(self): pass

    def subscribe(self, *a, **k): pass

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))
        return types.SimpleNamespace(rc=0)


def published(fake, index=-1):
    """(topic, decoded payload) of one recorded publication."""
    topic, raw, _ = fake.published[index]
    return topic, decode_payload(raw)


# ---- layer 1: no broker
fake = Fake()
pub = SparkplugPublisher(group="Plant", node="line1", device="pump", client=fake)
pub.on_connect(fake, None, None, 0, None)  # a connect is what births the node

assert [t for t, _, _ in fake.published] == ["spBv1.0/Plant/NBIRTH/line1"], \
    ("a connect must birth the node, and a device with no current values yet has nothing to birth",
     [t for t, _, _ in fake.published])
topic, nb = published(fake)
assert topic == "spBv1.0/Plant/NBIRTH/line1", f"the NBIRTH topic must be spBv1.0/<group>/NBIRTH/<node>, got {topic!r}"
assert nb.seq == 0, f"the first message of a session must carry seq 0, got {nb.seq!r}"
bd = [m for m in nb.metrics if m.name == "bdSeq"]
assert len(bd) == 1 and bd[0].datatype == 4 and bd[0].value == 1, \
    f"NBIRTH must declare bdSeq as an Int64 starting the first session at 1, got {bd!r}"

T = 1_700_000_000_000  # milliseconds, the Sparkplug unit
pub.data([Metric(name="temp", datatype=10, value=21.5, timestamp=T),
          Metric(name="speed", datatype=9, value=1450.0, timestamp=T + 1000)])
topic, dd = published(fake)
assert topic == "spBv1.0/Plant/DDATA/line1/pump", f"a device change must use spBv1.0/<group>/DDATA/<node>/<device>, got {topic!r}"
assert dd.seq == 1, f"DDATA follows the NBIRTH with the next sequence number, got {dd.seq!r}"
got = {m.name: (m.datatype, m.value, m.timestamp) for m in dd.metrics}
assert got == {"temp": (10, 21.5, T), "speed": (9, 1450.0, T + 1000)}, \
    f"metric names, datatypes, values and timestamps must survive the encode/decode round trip, got {got!r}"

pub.data([Metric(name="temp", datatype=10, value=22.0, timestamp=T + 5000)])
topic, dd = published(fake)
assert topic == "spBv1.0/Plant/DDATA/line1/pump" and dd.seq == 2, (topic, dd.seq)
assert [(m.name, m.value, m.timestamp) for m in dd.metrics] == [("temp", 22.0, T + 5000)], dd.metrics

# ---- the sequence number is a byte: it counts up to 255 and wraps to 0 without a gap
first = len(fake.published)
last_value = None
for i in range(254):  # seq 2 was just sent, so 254 more passes through 255 and wraps to 0
    last_value = 23.0 + i
    pub.data([Metric(name="temp", datatype=10, value=last_value, timestamp=T + 6000 + i)])
seqs = [decode_payload(raw).seq for _, raw, _ in fake.published[first:]]
assert seqs == [(3 + i) % 256 for i in range(254)], \
    f"the sequence must increment 0..255 and wrap, expected a consecutive run ending in 0, got {seqs[:4]}...{seqs[-2:]}"

# ---- a reconnect is a new session: bdSeq moves on, the sequence restarts at 0, and the DBIRTH carries the
# current values (not the ones from the first birth)
before = len(fake.published)
pub.on_connect(fake, None, None, 0, None)
new = fake.published[before:]
assert [t for t, _, _ in new] == ["spBv1.0/Plant/NBIRTH/line1", "spBv1.0/Plant/DBIRTH/line1/pump"], \
    f"a reconnect must republish the node and device births, got {[t for t, _, _ in new]!r}"
nb, db = decode_payload(new[0][1]), decode_payload(new[1][1])
assert nb.seq == 0 and db.seq == 1, f"a new session restarts the sequence at 0 and 1, got {nb.seq!r} and {db.seq!r}"
assert [m.value for m in nb.metrics if m.name == "bdSeq"] == [2], \
    f"bdSeq must advance so the source sees a new session, got {[m.value for m in nb.metrics if m.name == 'bdSeq']!r}"
got = {m.name: (m.value, m.timestamp) for m in db.metrics}
assert got == {"temp": (last_value, T + 6000 + 253), "speed": (1450.0, T + 1000)}, \
    f"the rebirth's DBIRTH must carry the current values and their timestamps, got {got!r}"


# ---- layer 2: a real broker when one is listening, otherwise an honest SKIP
def broker_up():
    try:
        socket.create_connection(("127.0.0.1", 1883), timeout=0.3).close()
        return True
    except OSError:
        return False


if not broker_up():
    print("SKIP broker layer: no MQTT broker answers 127.0.0.1:1883")
else:
    import paho.mqtt.client as mqtt
    import durian.outputs as O
    from _support import new_store
    from durian.app import App
    from durian.core import Quality
    from durian.sparkplug_source import SparkplugSource

    def wait(pred, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end and not pred():
            time.sleep(0.05)
        return pred()

    # A plaintext client for the local broker: the TLS factory needs a CA, and this broker layer is about the
    # Sparkplug wire, not TLS (tests/test_mqtt_source.py and test_tls.py already cover the TLS path).
    O.tls_client = lambda creds, client_id="": mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id=client_id or "durian-pub-" + str(random.randrange(1 << 30)))

    with tempfile.TemporaryDirectory() as folder:
        creds = Path(folder) / "creds.json"
        creds.write_text('{"host": "127.0.0.1", "port": 1883, "ca": "unused", "user": "u", "password": "p"}')

        # The subscriber comes first, so the NBIRTH is not published before the subscription is live.
        sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="durian-sub-" + str(random.randrange(1 << 30)))
        src = SparkplugSource("plant", group="Plant", factory=lambda n: Point(n), client=sub)
        sub.connect("127.0.0.1", 1883)
        sub.loop_start()
        assert wait(lambda: src.connected), ("the source never connected to the local broker", src.error)
        time.sleep(0.3)  # let the broker process the SUBSCRIBE before the publisher births

        store = new_store()
        store.put("source", "demo", {"type": "virtual", "enabled": True})
        store.put("point", "durian/line1/temp", {"source": "demo"})
        store.put("publisher", "sp", {"type": "sparkplug", "creds": str(creds), "group": "Plant",
                                      "node": "durian", "device": "line1", "points": ["*"]})
        app = App(store)

        seen = {}

        def pump():
            seen.update(src.poll(time.time()))
            return seen

        assert wait(lambda: "durian/bdSeq" in pump()), \
            ("no NBIRTH reached the source; it must birth on connect", src.error, src.dropped)
        point = app.points()["durian/line1/temp"][0]
        app.outputs.on_value(point, 21.5, time.time())
        assert wait(lambda: pump().get("durian/line1/temp", (None,))[0] == 21.5), \
            ("the value the publisher sent never came back through the Sparkplug source", seen, src.error)
        value, ts, quality = seen["durian/line1/temp"]
        assert value == 21.5 and quality == Quality.GOOD and abs(ts - time.time()) < 60, (value, ts, quality)
        assert src.gaps == 0, f"the birth and the data must be consecutive, the source counted {src.gaps} gap(s)"

        app.outputs.stop()
        src.stop()

print("ok")
