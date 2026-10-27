"""Sparkplug B over MQTT: a data source that reads births, deaths and change data.

A Sparkplug edge node publishes its whole metric set once (NBIRTH / DBIRTH) and afterwards only what
changed (NDATA / DDATA); its MQTT last will says when it is gone (NDEATH). This source turns that into
ordinary DurianDB points, one per <node>/<device>/<metric> or <node>/<metric>, so alarms, history and
publishing need no second data model.

Topics follow the specification's namespace spBv1.0/<group>/<message type>/<node>[/<device>]. Each
part is a whole topic level: the specification allows the underscore inside an id, so ids are never
split on it. Payload bytes are decoded by durian.sparkplug_codec, which imports no MQTT client; this
module only adds the session, alias and point bookkeeping around it.
"""
import math
import queue

from .core import FUTURE_SLACK, Quality, Source
from .mqtt_source import tls_client
from .sparkplug_codec import decode_payload

NUMBER_TYPES = frozenset(range(1, 11)) | {13}  # the whole-number, floating-point and DateTime datatypes
TEXT_TYPES = frozenset({12, 14, 15})  # String, Text and UUID
NODE_MESSAGES = frozenset({"NBIRTH", "NDEATH", "NDATA"})
DEVICE_MESSAGES = frozenset({"DBIRTH", "DDEATH", "DDATA"})


def _when(metric, ts):
    """The metric's own time in seconds when it is a sane millisecond value, else this tick's time."""
    t = metric.timestamp
    if isinstance(t, int) and not isinstance(t, bool) and t > 0:
        t /= 1000.0
        if t <= ts + FUTURE_SLACK:  # a clock far ahead is not evidence of when the value was taken
            return t
    return ts


def _bd_seq(payload):
    """The bdSeq metric's value, which tells one publisher session from the next."""
    for m in payload.metrics:
        if m.name == "bdSeq" and isinstance(m.value, int) and not isinstance(m.value, bool):
            return m.value
    return None


class SparkplugSource(Source):
    PREFIX = "spBv1.0/"
    QUEUE_MAX = 10_000  # ponytail: one cap for the source; the engine drains it every tick

    def __init__(self, name, creds=None, group="Plant", factory=None, client=None):
        """`creds` holds the MQTT TLS settings; `client` replaces the paho client, so a test needs no broker."""
        super().__init__(name)
        self.group, self.factory = group, factory
        self.manages_error = True  # the paho callbacks report the link state, so a tick must not clear it
        self.q, self.dropped, self.gaps, self.connected = queue.Queue(maxsize=self.QUEUE_MAX), 0, 0, False
        self.error = f"not connected to {creds['host']}:{creds['port']} yet" if creds else None
        self.client = client or tls_client(creds)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        if creds is not None:
            self.client.connect_async(creds["host"], creds["port"])
            self.client.loop_start()
        self._bd_seq = {}  # node -> the bdSeq of the session its births belong to
        self._seq = {}  # node -> the last sequence number seen from it
        self._aliases = {}  # (node, device or None) -> {alias: metric name} from the matching birth
        self._dead_nodes = set()  # a node after NDEATH, until its next NBIRTH
        self._dead_devices = set()  # a (node, device) pair after DDEATH, until its next DBIRTH

    def _on_message(self, c, u, m):
        """The broker's thread: never block it. A full queue drops the message and counts it."""
        self.feed(m.topic, m.payload)

    def feed(self, topic, payload):
        """Queue one message without a broker, for tests and other message feeds."""
        try:
            self.q.put_nowait((topic, payload))
        except queue.Full:
            self.dropped += 1

    def _on_connect(self, c, u, flags, rc, props):
        self.connected = rc == 0
        self.error = None if rc == 0 else f"the broker refused the connection ({rc})"
        if rc == 0:  # subscribe on every (re)connect; subscriptions don't survive clean sessions
            c.subscribe(self.PREFIX + self.group + "/#", qos=1)

    def _on_disconnect(self, c, u, flags, rc, props):
        self.connected = False
        self.error = f"the connection to the broker was lost ({rc})" if rc else "the connection to the broker was closed"

    def poll(self, ts):
        """Drain the messages the broker thread queued; a bad one is data loss, not a reason to stop."""
        out = {}
        while True:
            try:
                topic, payload = self.q.get_nowait()
            except queue.Empty:
                return out
            try:
                self._message(topic, payload, ts, out)
            except Exception:  # a malformed or unknown message must never escape into the engine
                self.dropped += 1

    def _message(self, topic, raw, ts, out):
        parts = topic.split("/") if isinstance(topic, str) else []
        if len(parts) not in (4, 5) or parts[0] != "spBv1.0" or parts[1] != self.group:
            self.dropped += 1
            return
        kind, node = parts[2].upper(), parts[3]
        device = parts[4] if len(parts) == 5 else None
        if kind not in (NODE_MESSAGES if device is None else DEVICE_MESSAGES):
            self.dropped += 1
            return
        if node in self._dead_nodes and kind != "NBIRTH":
            self.dropped += 1  # nothing revives a dead node except its next birth
            return
        if device is not None and (node, device) in self._dead_devices and kind != "DBIRTH":
            self.dropped += 1
            return
        payload = decode_payload(raw)
        if kind == "NBIRTH":
            self._birth(payload, node, None, ts, out)
        elif kind == "DBIRTH":
            self._birth(payload, node, device, ts, out)
        elif kind == "NDEATH":
            self._node_death(payload, node, ts, out)
        elif kind == "DDEATH":
            self._sequence(node, payload.seq)
            self._device_death(node, device, ts, out)
        else:  # NDATA or DDATA
            self._sequence(node, payload.seq)
            self._data(payload, node, device, ts, out)

    def _birth(self, payload, node, device, ts, out):
        if device is None:
            self._dead_nodes.discard(node)  # any birth revives the node; data alone never does
            bd = _bd_seq(payload)
            new_session = node not in self._bd_seq or (bd is not None and bd != self._bd_seq[node])
            if new_session:
                # A new bdSeq starts a new session: its sequence restarts, so nothing was lost. The birth
                # itself consumes the first sequence number of that session.
                self._bd_seq[node] = bd
                self._dead_devices = {k for k in self._dead_devices if k[0] != node}
                self._aliases = {k: v for k, v in self._aliases.items() if k[0] != node}
                if payload.seq is None:
                    self._seq.pop(node, None)
                else:
                    self._seq[node] = payload.seq
            else:
                self._sequence(node, payload.seq)
        else:
            self._sequence(node, payload.seq)
            self._dead_devices.discard((node, device))
        aliases = {m.alias: m.name for m in payload.metrics if m.name is not None and m.alias is not None}
        self._aliases[(node, device)] = aliases  # a birth replaces the names the data messages may use
        self._data(payload, node, device, ts, out)

    def _data(self, payload, node, device, ts, out):
        for m in payload.metrics:
            self._value(node, device, m, ts, out)

    def _value(self, node, device, metric, ts, out):
        name = metric.name
        if name is None:
            name = self._aliases.get((node, device), {}).get(metric.alias)
            if name is None:  # an alias means nothing without the birth that declared it
                self.dropped += 1
                return
        if metric.is_null or metric.value is None:
            self.dropped += 1
            return
        text = metric.datatype in TEXT_TYPES
        if text:
            value = metric.value
            if not isinstance(value, str) or not 1 <= len(value) <= 255 or not value.isprintable():
                self.dropped += 1
                return
        elif metric.datatype in NUMBER_TYPES:
            value = metric.value
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                self.dropped += 1  # booleans and non-finite floats have no point representation here
                return
            value = float(value)
        else:
            self.dropped += 1  # bytes, datasets, templates and packed arrays stay on the wire
            return
        key = f"{node}/{device}/{name}" if device is not None else f"{node}/{name}"
        point = self.points.get(key)
        if point is None:
            if self.factory is None or len(self.points) >= self.AUTO_POINTS_MAX:
                self.dropped += 1
                return
            point = self.add(self.factory(key))
            point.text = text  # the metric's datatype, not a config default, says how the value is read
        elif point.text != text:  # a metric that changed type would break history and alarms
            self.dropped += 1
            return
        out[key] = (value, _when(metric, ts), Quality.GOOD)

    def _sequence(self, node, seq):
        """Count a skipped or backwards sequence number and say so, without ever naming an exception."""
        if seq is None or isinstance(seq, bool) or not isinstance(seq, int):
            return
        last = self._seq.get(node)
        if last is not None and seq != (last + 1) % 256:
            self.gaps += 1
            self.error = (f"Sparkplug sequence gap for {self.group}/{node}: expected {(last + 1) % 256} "
                          f"but received {seq}; messages may have been lost, check the node's link to the broker")
        self._seq[node] = seq

    def _node_death(self, payload, node, ts, out):
        bd = _bd_seq(payload)
        current = self._bd_seq.get(node)
        if bd is not None and current is not None and bd != current:
            return  # a last will from an older session does not kill the session that replaced it
        self._dead_nodes.add(node)
        self._seq.pop(node, None)
        self._aliases = {k: v for k, v in self._aliases.items() if k[0] != node}
        self._dead_devices = {k for k in self._dead_devices if k[0] != node}
        self._bad(f"{node}/", ts, out)

    def _device_death(self, node, device, ts, out):
        self._dead_devices.add((node, device))
        self._aliases.pop((node, device), None)
        self._bad(f"{node}/{device}/", ts, out)

    def _bad(self, prefix, ts, out):
        """Mark every point under a prefix not-GOOD, without inventing a value for it."""
        for name in self.points:
            if name.startswith(prefix):
                out[name] = (None, ts, Quality.BAD)

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()
