"""MQTT-over-TLS data source: subscribes to <prefix># and turns each topic into a point.

Written from the public MQTT 3.1.1/5 standard and the paho-mqtt API, not from Mango.
"""
import json
import queue
import ssl

import paho.mqtt.client as mqtt

from .core import Point, Source


def parse(payload: bytes):
    """Accept a bare number or {"value": number}. Anything else is dropped (untrusted input)."""
    try:
        text = payload.decode()
        v = json.loads(text)
        if isinstance(v, dict):
            v = v.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)
    except (UnicodeDecodeError, ValueError):
        return None


class MqttSource(Source):
    def __init__(self, name, host, port, ca, user, password, prefix="durian/", detectors=None):
        super().__init__(name)
        self.prefix, self.detectors = prefix, detectors or {}
        self.q, self.dropped, self.connected = queue.Queue(), 0, False
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        # PROTOCOL_TLS_CLIENT verifies the server cert chain and hostname; never disable that.
        self.client.tls_set(ca_certs=ca, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self.client.username_pw_set(user, password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = lambda c, u, f, rc, p: setattr(self, "connected", False)
        self.client.on_message = lambda c, u, m: self.q.put((m.topic, m.payload))
        self.client.reconnect_delay_set(1, 30)
        self.client.connect_async(host, port)
        self.client.loop_start()

    def _on_connect(self, c, u, flags, rc, props):
        self.connected = rc == 0
        if rc == 0:  # subscribe on every (re)connect; subscriptions don't survive clean sessions
            c.subscribe(self.prefix + "#", qos=1)

    def poll(self, ts):
        out = {}  # ponytail: latest value per point per tick; keep every sample if tick rate becomes the limit
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
                self.add(Point(name, detectors=list(self.detectors.get(name, []))))
            out[name] = v

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()
