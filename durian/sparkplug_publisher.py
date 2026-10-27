"""Sparkplug B publishing: birth the node and device once, then send only what changed.

The caller owns the MQTT client (it connects and runs the loop); this class only speaks the Sparkplug
wire. A connect is always a new session: bdSeq moves on, so the source sees the new session, and the
sequence number restarts at 0. The wire bytes come from durian.sparkplug_codec, the same codec the
Sparkplug source decodes with; nothing here re-implements protobuf.

Declared non-goals for now: no NDEATH/last will, no metric aliases, no STATE topic, and no de-duplication
of a DDATA that repeats the previous value. A rebirth carries every cached value, which is what the source
needs after a new session.
"""
import time

from .sparkplug_codec import Metric, Payload, encode_payload

BDSEQ = 4  # Int64, the datatype of the birth/death sequence metric (spec, Appendix A)
DOUBLE = 10
STRING = 12


class SparkplugPublisher:
    def __init__(self, group, node, device=None, client=None):
        """`client` is a paho client the caller already owns; without one a plain client is made, unconnected."""
        if client is None:
            import paho.mqtt.client as mqtt
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.group, self.node, self.device = group, node, device
        self.client = client
        self.client.on_connect = self.on_connect
        self.bd_seq = 0  # the first session is 1
        self.seq = 0
        self.values = {}  # metric name -> latest Metric sent, so a rebirth has the current values

    def on_connect(self, client, userdata, flags, rc, props=None):
        """rc 0 is a new session: rebirth. A refused connection publishes nothing."""
        if rc != 0:
            return
        self.bd_seq += 1
        self.seq = 0
        birth = [Metric(name="bdSeq", datatype=BDSEQ, value=self.bd_seq)]
        self._send("NBIRTH", birth if self.device else birth + list(self.values.values()))
        if self.device and self.values:
            self._send("DBIRTH", list(self.values.values()))

    def data(self, metrics):
        """Publish a change; the latest value per name is kept for the next DBIRTH."""
        for m in metrics:
            if m.name is not None:
                self.values[m.name] = m
        self._send("DDATA" if self.device else "NDATA", metrics)

    def _topic(self, kind):
        if kind in ("DBIRTH", "DDATA"):
            return f"spBv1.0/{self.group}/{kind}/{self.node}/{self.device}"
        return f"spBv1.0/{self.group}/{kind}/{self.node}"

    def _send(self, kind, metrics):
        payload = encode_payload(Payload(seq=self.seq, timestamp=int(time.time() * 1000), metrics=metrics))
        self.seq = (self.seq + 1) % 256
        self.client.publish(self._topic(kind), payload, qos=1)
