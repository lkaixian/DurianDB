"""An MQTT source that cannot connect says why, in its own error and in the source-down alarm.
Run: python tests/test_mqtt_health.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import durian.mqtt_source as M  # noqa: E402
from durian.core import SOURCE, Engine, Point, Source  # noqa: E402


class Fake:
    def connect_async(self, *a, **k): pass

    def loop_start(self): pass

    def loop_stop(self): pass

    def disconnect(self): pass

    def subscribe(self, *a, **k): pass


M.tls_client = lambda creds, client_id="": Fake()
src = M.MqttSource("broker", {"host": "mqtt.example", "port": 8883, "ca": "x", "user": "u", "password": "p"})
assert src.connected is False and "mqtt.example:8883" in src.error, src.error

src._on_connect(Fake(), None, None, 5, None)  # not authorised
assert src.connected is False and "refused" in src.error and "5" in src.error, src.error
src._on_connect(Fake(), None, None, 0, None)
assert src.connected is True and src.error is None

src._on_disconnect(None, None, None, 7, None)
assert src.connected is False and "lost" in src.error and "7" in src.error, src.error
src._on_disconnect(None, None, None, 0, None)  # the clean close we asked for is not a failure
assert "closed" in src.error, src.error

# and the engine's source-down alarm carries that reason instead of the generic "not connected"
src._on_connect(Fake(), None, None, 0, None)
src.add(Point("t"))
e = Engine()
e.sources.append(src)
src.manages_error = True
src._on_disconnect(None, None, None, 7, None)
src.down_after = 0
e.tick(100.0)
assert e.events[0].point == SOURCE + "broker" and "lost" in e.events[0].message, e.events[0].message
src._on_connect(Fake(), None, None, 0, None)
e.tick(101.0)
assert e.events[0].cleared_ts == 101.0

print("ok")
