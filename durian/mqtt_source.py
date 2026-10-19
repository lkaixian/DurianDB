"""MQTT over TLS: a data source that subscribes to <prefix># and a shared TLS client factory.

Written from the public MQTT 3.1.1/5 standard and the paho-mqtt API, not from Mango.
"""
import json
import math
import queue
import ssl

import paho.mqtt.client as mqtt

from .core import Point, Source


def parse(payload: bytes):
    """Accept a bare number or {"value": number}. Anything else is dropped (untrusted input)."""
    try:
        v = json.loads(payload.decode())
        if isinstance(v, dict):
            v = v.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None  # JSON allows NaN and Infinity; they would break comparisons and the browser's JSON
        return float(v)
    except (UnicodeDecodeError, ValueError):
        return None


def tls_client(c: dict, client_id=""):
    """Configured, unconnected client. `c` has host, port, ca, user, password and optional cert/key.

    PROTOCOL_TLS_CLIENT verifies the server certificate chain and hostname. Never turn that off.
    """
    cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    cl.tls_set(ca_certs=c["ca"], certfile=c.get("cert"), keyfile=c.get("key"), tls_version=ssl.PROTOCOL_TLS_CLIENT)
    cl.username_pw_set(c["user"], c["password"])
    cl.reconnect_delay_set(1, 30)
    return cl


class MqttSource(Source):
    def __init__(self, name, creds, prefix="durian/", cmd_prefix="durian-cmd/", factory=None):
        """`factory(name)` builds a Point for a topic seen for the first time; None drops unknown topics."""
        super().__init__(name)
        self.prefix, self.cmd_prefix, self.factory = prefix, cmd_prefix, factory
        self.q, self.dropped, self.connected = queue.Queue(), 0, False
        self.client = tls_client(creds)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = lambda c, u, f, rc, p: setattr(self, "connected", False)
        self.client.on_message = lambda c, u, m: self.q.put((m.topic, m.payload))
        self.client.connect_async(creds["host"], creds["port"])
        self.client.loop_start()

    def _on_connect(self, c, u, flags, rc, props):
        self.connected = rc == 0
        if rc == 0:  # subscribe on every (re)connect; subscriptions don't survive clean sessions
            c.subscribe(self.prefix + "#", qos=1)

    def poll(self, ts):
        out = {}  # ponytail: latest value per point per tick; keep every sample if the tick rate becomes the limit
        while True:
            try:
                topic, payload = self.q.get_nowait()
            except queue.Empty:
                return out
            name = topic[len(self.prefix):]
            v = parse(payload)
            if not name or v is None:
                self.dropped += 1
                continue
            if name not in self.points:
                if self.factory is None:
                    self.dropped += 1  # unknown topic, like Mango's "unknown topic" event
                    continue
                self.add(self.factory(name))
            out[name] = v

    def write(self, name, value):
        info = self.client.publish(self.cmd_prefix + name, json.dumps({"value": value}), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"publish failed ({info.rc})")

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()
