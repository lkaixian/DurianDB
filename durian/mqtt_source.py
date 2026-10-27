"""MQTT over TLS: a data source that subscribes to <prefix># and a shared TLS client factory.

Written from the public MQTT 3.1.1/5 standard and the paho-mqtt API, not from Mango.
"""
import json
import math
import queue
import ssl

import paho.mqtt.client as mqtt

from .core import Point, Quality, Source


def _require_api_v2(mod):
    """paho-mqtt 1.x has no CallbackAPIVersion, so refuse it here with a sentence instead of an AttributeError later."""
    if not hasattr(mod, "CallbackAPIVersion"):
        raise ImportError("DurianDB needs paho-mqtt 2.0 or newer for its MQTT source and publisher; "
                          "upgrade with 'pip install --upgrade \"paho-mqtt>=2.0\"' and restart the server")


_require_api_v2(mqtt)  # a machine without paho still fails at the import above, with its usual message


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


def parse_text(payload: bytes):
    """Text for an alphanumeric point: a JSON string, {"value": string}, or the bare payload. Printable, 1 to 255 characters, else None."""
    try:
        s = payload.decode()
    except UnicodeDecodeError:
        return None
    try:
        v = json.loads(s)
        s = v.get("value") if isinstance(v, dict) else v if isinstance(v, str) else s
    except ValueError:
        pass
    return s if isinstance(s, str) and 1 <= len(s) <= 255 and s.isprintable() else None


def stamp(payload: bytes):
    """(source time, quality) from a {"value", "ts", "quality"} payload; either is None when absent or unusable."""
    from .ingest import parse_quality, parse_time  # ingest imports core only; deferred to keep this module's import light
    try:
        d = json.loads(payload.decode())
    except (UnicodeDecodeError, ValueError):
        return None, Quality.GOOD
    if not isinstance(d, dict):
        return None, Quality.GOOD
    try:
        t = parse_time(d["ts"]) if "ts" in d else None
    except ValueError:
        t = None
    try:
        return t, parse_quality(d.get("quality"))
    except ValueError:
        return t, Quality.UNCERTAIN  # a quality we cannot read is itself a reason to doubt


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
    QUEUE_MAX = 10_000  # ponytail: one cap for the source; the engine drains it every tick

    def __init__(self, name, creds=None, prefix="durian/", cmd_prefix="durian-cmd/", factory=None, client=None):
        """`factory(name)` builds a Point for a topic seen for the first time; None drops unknown topics.
        `client` replaces the paho client, so a test needs no broker."""
        super().__init__(name)
        self.prefix, self.cmd_prefix, self.factory = prefix, cmd_prefix, factory
        self.manages_error = True  # the paho callbacks report the link state, so a tick must not clear it
        self.q, self.dropped, self.connected = queue.Queue(maxsize=self.QUEUE_MAX), 0, False
        self.error = f"not connected to {creds['host']}:{creds['port']} yet" if creds else None
        self.client = client or tls_client(creds)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        if creds is not None:
            self.client.connect_async(creds["host"], creds["port"])
            self.client.loop_start()

    def _on_message(self, c, u, m):
        """The broker's thread: never block it. A full queue drops the message and counts it on the source health table."""
        try:
            self.q.put_nowait((m.topic, m.payload))
        except queue.Full:
            self.dropped += 1

    def _on_connect(self, c, u, flags, rc, props):
        self.connected = rc == 0
        self.error = None if rc == 0 else f"the broker refused the connection ({rc})"
        if rc == 0:  # subscribe on every (re)connect; subscriptions don't survive clean sessions
            c.subscribe(self.prefix + "#", qos=1)

    def _on_disconnect(self, c, u, flags, rc, props):
        self.connected = False
        self.error = f"the connection to the broker was lost ({rc})" if rc else "the connection to the broker was closed"

    def poll(self, ts):
        out = {}  # ponytail: latest value per point per tick; keep every sample if the tick rate becomes the limit
        while True:
            try:
                topic, payload = self.q.get_nowait()
            except queue.Empty:
                return out
            name = topic[len(self.prefix):]
            v = parse_text(payload) if getattr(self.points.get(name), "text", False) else parse(payload)
            if not name or v is None:
                self.dropped += 1
                continue
            if name not in self.points:
                if self.factory is None or len(self.points) >= self.AUTO_POINTS_MAX:  # an endless supply of topics must not grow the source
                    self.dropped += 1  # unknown topic, like Mango's "unknown topic" event
                    continue
                self.add(self.factory(name))
            t, q = stamp(payload)
            out[name] = (v, t or ts, q)

    def write(self, name, value):
        info = self.client.publish(self.cmd_prefix + name, json.dumps({"value": value}), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"could not publish {name} to the broker (error {info.rc}); the command was not sent")

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()
