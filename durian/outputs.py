"""Event handlers (webhook) and publishers (MQTT over TLS), built from stored config.

Delivery never blocks the engine: webhooks go through a bounded queue and one worker thread.
"""
import json
import logging
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request

from .config import load_creds
from .core import Level, Quality
from .mqtt_source import tls_client
from .polled import NoRedirect

log = logging.getLogger("durian.outputs")


opener = urllib.request.build_opener(NoRedirect)


def validate_handler(name, b):
    e, add = [], lambda p, m: e.append({"property": p, "message": m})
    if b.get("type") != "webhook":
        add("type", "must be 'webhook'")
    u = urllib.parse.urlparse(b.get("url", "")) if isinstance(b.get("url"), str) else None
    if not u or u.scheme not in ("http", "https") or not u.hostname:
        add("url", "must be an http or https URL")
    if b.get("min_level", "WARNING") not in Level.__members__:
        add("min_level", f"must be one of {list(Level.__members__)}")
    if not isinstance(b.get("enabled", True), bool):
        add("enabled", "must be true or false")
    return e


def validate_publisher(name, b, store):
    e, add = [], lambda p, m: e.append({"property": p, "message": m})
    if b.get("type") != "mqtt":
        add("type", "must be 'mqtt'")
    try:
        load_creds(b.get("creds", ""))
    except (OSError, ValueError, TypeError) as ex:
        add("creds", f"cannot use credentials file: {type(ex).__name__}")
    pre = b.get("prefix", "durian-out/")
    if not isinstance(pre, str) or not pre.endswith("/") or "#" in pre or "+" in pre:
        add("prefix", "must be a topic prefix ending in '/', without wildcards")
    else:  # publishing into a prefix a source subscribes to would feed our own output back in
        for s in store.list("source"):
            if s.get("type") == "mqtt":
                other = s.get("prefix", "durian/")
                if pre.startswith(other) or other.startswith(pre):
                    add("prefix", f"overlaps the prefix of source '{s['name']}'")
    pts = b.get("points")
    if not (isinstance(pts, list) and pts and all(isinstance(p, str) for p in pts)):
        add("points", "list the point names to publish, or ['*'] for all")
    if not isinstance(b.get("enabled", True), bool):
        add("enabled", "must be true or false")
    return e


class Outputs:
    def __init__(self, store):
        self.webhooks = [h for h in store.list("handler") if h.get("enabled", True)]
        self.q = queue.Queue(maxsize=1000)
        self.publishers = []
        for p in store.list("publisher"):
            if not p.get("enabled", True):
                continue
            try:
                cl = tls_client(load_creds(p["creds"]))
                c = load_creds(p["creds"])
                cl.connect_async(c["host"], c["port"])
                cl.loop_start()
                self.publishers.append((cl, p["prefix"], set(p["points"])))
            except Exception as ex:
                log.warning("publisher %s not started: %s", p["name"], type(ex).__name__)
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.worker.start()

    def on_value(self, point, value, ts):
        for cl, prefix, names in self.publishers:
            if "*" in names or point.name in names:
                cl.publish(prefix + point.name, json.dumps({"value": value, "ts": ts, "quality": Quality(point.quality).name.lower()}), qos=1)

    def on_event(self, ev, kind):
        for h in self.webhooks:
            if ev.level >= Level[h.get("min_level", "WARNING")]:
                body = {"event": kind, "point": ev.point, "level": ev.level.name, "message": ev.message,
                        "ts": ev.ts, "cleared_ts": ev.cleared_ts}
                try:
                    self.q.put_nowait((h["url"], body))
                except queue.Full:
                    log.warning("webhook queue full, dropping event for %s", ev.point)

    def _work(self):
        while True:
            url, body = self.q.get()
            if url is None:
                return
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                opener.open(req, timeout=5).read(1024)
            except (urllib.error.URLError, OSError, ValueError) as ex:
                log.warning("webhook failed: %s", type(ex).__name__)

    def stop(self):
        self.q.put((None, None))
        for cl, _, _ in self.publishers:
            cl.loop_stop()
            cl.disconnect()
