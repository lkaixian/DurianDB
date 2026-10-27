"""Event handlers (webhook, email, set point) and publishers (MQTT over TLS), built from stored config.

Delivery never blocks the engine: everything goes through a bounded queue and one worker thread.
A handler may be limited to some points, tags or sources with `scope`, like a maintenance window.
"""
import email.message
import json
import logging
import queue
import re
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import windows
from .config import NAME, load_creds
from .core import SOURCE, Level, Quality, sentence, state_label
from .mqtt_source import tls_client
from .polled import NoRedirect

log = logging.getLogger("durian.outputs")

opener = urllib.request.build_opener(NoRedirect)
ADDRESS = re.compile(r"^[^@\s<>,;]{1,64}@[^@\s<>,;]{1,255}$")
HANDLER_TYPES = ("webhook", "email", "setpoint", "report", "slack")
SECURITY = ("none", "starttls", "ssl")
PUBLISHER_TYPES = ("mqtt", "http")
DELIVERY_TIMEOUT = 5


def _http_url(u):
    u = urllib.parse.urlparse(u) if isinstance(u, str) else None
    return bool(u and u.scheme in ("http", "https") and u.hostname)


def _reason(ex, timeout=DELIVERY_TIMEOUT):
    """The sentence an operator reads when a delivery did not work, never an exception class name,
    an errno tag or a file path (docs/12 section 4)."""
    if isinstance(ex, urllib.error.HTTPError):
        return f"the receiver answered {ex.code} {ex.reason}".rstrip()
    if isinstance(ex, urllib.error.URLError):
        r = ex.reason
        if isinstance(r, TimeoutError):
            return f"the receiver did not answer within {timeout}s"
        if isinstance(r, ConnectionRefusedError):
            return "the connection was refused"
        return sentence(r) or "the receiver could not be reached"
    if isinstance(ex, TimeoutError):
        return f"the receiver did not answer within {timeout}s"
    if isinstance(ex, ConnectionRefusedError):
        return "the connection was refused"
    if isinstance(ex, smtplib.SMTPResponseException):  # the server's own text can be bytes and full of codes
        return f"the mail server answered {ex.smtp_code}"
    if isinstance(ex, smtplib.SMTPException):
        return sentence(ex) or "the mail server did not take the message"
    if isinstance(ex, OSError):
        return sentence(ex) or "the connection failed"
    return sentence(ex) or "delivery failed"


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_emailgroup(name, b):
    """A named recipient list, so several handlers can share one address book entry."""
    e, add = [], lambda p, m: e.append({"property": p, "message": m})
    if not (isinstance(name, str) and NAME.match(name)):
        add("name", "use 1 to 64 letters, digits, dot, dash or underscore")
    a = b.get("addresses")
    if not (isinstance(a, list) and 1 <= len(a) <= 20 and all(isinstance(x, str) and ADDRESS.match(x) for x in a)):
        add("addresses", "must be 1 to 20 email addresses")
    return e


def validate_handler(name, b, store=None):
    e, add = [], lambda p, m: e.append({"property": p, "message": m})
    kind = b.get("type")
    if kind not in HANDLER_TYPES:
        add("type", f"must be one of {list(HANDLER_TYPES)}")
    if b.get("min_level", "WARNING") not in Level.__members__:
        add("min_level", f"must be one of {list(Level.__members__)}")
    if not isinstance(b.get("enabled", True), bool):
        add("enabled", "must be true or false")
    sc = b.get("scope")
    if sc is not None and not (isinstance(sc, dict) and all(isinstance(sc.get(k, []), list) and all(isinstance(x, str) for x in sc.get(k, [])) for k in ("points", "sources"))
                               and isinstance(sc.get("tags", {}), dict)):
        add("scope", "must be {points: [...], tags: {...}, sources: [...]}; leave it out for every alarm")
    if kind in ("webhook", "slack"):  # slack posts {text} to an incoming-webhook URL
        if not _http_url(b.get("url")):
            add("url", "must be an http or https URL")
    elif kind in ("email", "report"):  # a report is an email sent on a schedule instead of on an alarm
        to = b.get("to")
        if not (isinstance(to, list) and 1 <= len(to) <= 20 and all(isinstance(a, str) and ADDRESS.match(a) for a in to)):
            add("to", "must be 1 to 20 email addresses")  # ponytail: groups are additive, so `to` stays required; loosen this to allow group-only handlers
        if not (isinstance(b.get("from"), str) and ADDRESS.match(b["from"])):
            add("from", "must be an email address")
        s = b.get("smtp")
        if not (isinstance(s, dict) and isinstance(s.get("host"), str) and s["host"] and _num(s.get("port", 25)) and 1 <= s.get("port", 25) <= 65535 and s.get("security", "none") in SECURITY):
            add("smtp", f"must be {{host, port, security: {', '.join(SECURITY)}}}")
        if "creds" in b and not (isinstance(b["creds"], str) and Path(b["creds"]).is_file()):
            add("creds", "must be the path of a JSON file with user and password")
        if not isinstance(b.get("on_clear", False), bool):
            add("on_clear", "must be true or false")
        if kind == "email" and ("escalate_after" in b or "escalate_to" in b):  # tell more people when an alarm stays unacknowledged
            if not (isinstance(b.get("escalate_after"), int) and not isinstance(b["escalate_after"], bool) and 1 <= b["escalate_after"] <= 1440):
                add("escalate_after", "must be a whole number of minutes from 1 to 1440")
            es = b.get("escalate_to")
            if not (isinstance(es, list) and 1 <= len(es) <= 20 and all(isinstance(a, str) and ADDRESS.match(a) for a in es)):
                add("escalate_to", "must be 1 to 20 email addresses")
        if kind == "email" and b.get("groups"):  # extra recipients come from named address lists, never from a client
            g = b["groups"]
            if not (isinstance(g, list) and 1 <= len(g) <= 20 and all(isinstance(x, str) and NAME.match(x) for x in g)):
                add("groups", "must be a list of 1 to 20 email group names")
            elif store is not None:
                missing = [x for x in g if store.get("emailgroup", x) is None]
                for x in missing:
                    add("groups", f"there is no email group named '{x}'")
        if kind == "email":  # optional subject/body templates; unknown placeholders stay literal when sent
            if "subject" in b and not (isinstance(b["subject"], str) and 1 <= len(b["subject"]) <= 200 and "\n" not in b["subject"] and "\r" not in b["subject"]):
                add("subject", "must be one line of text, up to 200 characters")
            if "body" in b and not (isinstance(b["body"], str) and 1 <= len(b["body"]) <= 2000):
                add("body", "must be text, up to 2000 characters")
        if kind == "report":
            if not (isinstance(b.get("hour"), int) and not isinstance(b["hour"], bool) and 0 <= b["hour"] <= 23):
                add("hour", "must be a whole hour from 0 to 23 (server local time)")
            if "hours" in b and not (isinstance(b["hours"], int) and not isinstance(b["hours"], bool) and 1 <= b["hours"] <= 744):
                add("hours", "must be a whole number of hours from 1 to 744")
            if "watchlist" in b and not (isinstance(b["watchlist"], str) and 0 < len(b["watchlist"]) <= 64):
                add("watchlist", "must be the name of a shared watch list")
    elif kind == "setpoint":
        if not (isinstance(b.get("point"), str) and 0 < len(b["point"]) <= 64):
            add("point", "name the point to set")
        if not _num(b.get("value")):
            add("value", "give the number to write when the alarm is raised")
        if "clear_value" in b and not _num(b["clear_value"]):
            add("clear_value", "must be a number")
        if not isinstance(b.get("release_on_clear", False), bool):
            add("release_on_clear", "must be true or false")
    return e


def validate_publisher(name, b, store):
    e, add = [], lambda p, m: e.append({"property": p, "message": m})
    kind = b.get("type")
    if kind not in PUBLISHER_TYPES:
        add("type", f"must be one of {list(PUBLISHER_TYPES)}")
    if kind == "http":  # posts {point, value, ts, quality} as JSON, the body another DurianDB's /api/ingest takes
        if not _http_url(b.get("url")):
            add("url", "must be an http or https URL")
        if "creds" in b:
            try:
                if not isinstance(json.loads(Path(b["creds"]).read_text()).get("token"), str):
                    raise ValueError
            except (OSError, ValueError, TypeError, AttributeError):
                add("creds", "must be the path of a JSON file with a token, sent as a Bearer token")
        if "resend" in b and not (_num(b["resend"]) and 1 <= b["resend"] <= 86400):
            add("resend", "must be a number of seconds from 1 to 86400")
    elif kind == "mqtt":
        try:
            load_creds(b.get("creds", ""))
        except (OSError, ValueError, TypeError) as ex:
            log.warning("publisher credentials file %s rejected: %s", b.get("creds"), ex)
            add("creds", f"cannot use credentials file: {sentence(ex) or 'it could not be read'}")
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
    """`resolve(point name)` returns (tags, source name) so a handler's scope can be checked; `setter(point, value, who)` performs a set-point handler's write."""

    def __init__(self, store, resolve=None, setter=None):
        self.handlers = [h for h in store.list("handler") if h.get("enabled", True)]
        self.groups = {g["name"]: g["addresses"] for g in store.list("emailgroup")}  # ponytail: snapshot per Outputs; any config save rebuilds it, so no live lookup
        self.last_value = {}  # point -> last value label, so a template can say {value}; ponytail: bounded by the point count
        self.resolve, self.setter = resolve or (lambda name: ({}, None)), setter
        self.q = queue.Queue(maxsize=1000)
        self.failed = 0  # every delivery that did not work, for the health view
        self.status = {}  # target name -> {kind, failed, last_error, last_ok}; a dead route must not be invisible
        self.publishers, self.senders = [], []  # MQTT clients, and HTTP senders {url, names, headers, resend, sent}
        for p in store.list("publisher"):
            if not p.get("enabled", True):
                continue
            if p.get("type") == "http":
                try:
                    tok = json.loads(Path(p["creds"]).read_text())["token"] if p.get("creds") else None
                except (OSError, ValueError, KeyError) as ex:
                    log.warning("publisher %s not started: unreadable credentials: %s", p["name"], ex)
                    self._note(p["name"], "publisher", "the credentials file could not be read; it must be a JSON file holding a token")
                    continue
                self.senders.append({"name": p["name"], "url": p["url"], "names": set(p["points"]), "resend": p.get("resend", 60), "sent": {},
                                     "headers": {"Authorization": "Bearer " + tok} if tok else {}})
                continue
            try:
                cl = tls_client(load_creds(p["creds"]))
                c = load_creds(p["creds"])
                cl.connect_async(c["host"], c["port"])
                cl.loop_start()
                self.publishers.append((cl, p["prefix"], set(p["points"])))
            except Exception as ex:
                log.warning("publisher %s not started: %s: %s", p["name"], type(ex).__name__, ex)
                self._note(p["name"], "publisher", f"the publisher could not start: {sentence(ex) or 'check the broker address and credentials'}")
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.worker.start()

    def on_value(self, point, value, ts):
        self.last_value[point.name] = state_label(point, value)
        for s in self.senders:  # send a point's value when it changes, and again after `resend` seconds so the receiver sees it is alive
            if ("*" in s["names"] or point.name in s["names"]) and point.quality != Quality.BAD:
                now = time.monotonic()
                last = s["sent"].get(point.name)
                if last is None or last[0] != value or now - last[1] >= s["resend"]:
                    s["sent"][point.name] = (value, now)
                    self._put(("webhook", s["url"], {"point": point.name, "value": value, "ts": ts, "quality": Quality(point.quality).name.lower()}, s["headers"]), s["name"])
        for cl, prefix, names in self.publishers:
            if "*" in names or point.name in names:
                cl.publish(prefix + point.name, json.dumps({"value": value, "ts": ts, "quality": Quality(point.quality).name.lower()}), qos=1)

    def _in_scope(self, h, ev):
        sc = h.get("scope")
        if not sc:
            return True
        if ev.point.startswith(SOURCE):
            return windows.covers({"scope": sc}, ev.point, source=ev.point[len(SOURCE):])
        tags, source = self.resolve(ev.point)
        return windows.covers({"scope": sc}, ev.point, tags, source)

    def _note(self, name, kind, error=None, ok=False):
        """Remember how one handler or publisher is doing, so a route that has been dead for a week is visible. `error` is the sentence the operator reads."""
        st = self.status.setdefault(name, {"kind": kind, "failed": 0, "failing": 0, "last_error": None, "last_ok": None, "since": None})
        if ok:
            st["last_ok"], st["failing"], st["last_error"], st["since"] = time.time(), 0, None, None
            return
        self.failed += 1
        st["failed"] += 1
        st["failing"] += 1
        st["last_error"] = (error or "delivery failed")[:300]
        st["since"] = st["since"] or time.time()

    def faults(self):
        """Targets whose deliveries are failing right now: a route that is working again is not a fault."""
        return [{"name": n, **st} for n, st in sorted(self.status.items()) if st["failing"]]

    def _put(self, item, target=None):
        try:
            self.q.put_nowait((target, item))
        except queue.Full:
            self._note(target or item[0], "output", "the delivery queue is full")
            log.warning("output queue full, dropping %s", item[0])

    def on_event(self, ev, kind):
        for h in self.handlers:
            if ev.level < Level[h.get("min_level", "WARNING")] or not self._in_scope(h, ev):
                continue
            try:
                self._dispatch(h, ev, kind)
            except Exception as ex:  # one broken handler must not stop the other alarms in this tick
                self._note(h["name"], "output", f"the handler could not run: {sentence(ex) or 'check its settings'}")
                log.warning("handler %s failed: %s: %s", h["name"], type(ex).__name__, ex)

    def _dispatch(self, h, ev, kind):
        t = h.get("type", "webhook")
        if t == "webhook":
            self._put(("webhook", h["url"], {"event": kind, "point": ev.point, "level": ev.level.name, "message": ev.message, "ts": ev.ts, "cleared_ts": ev.cleared_ts}), h["name"])
        elif t == "slack" and (kind == "raised" or h.get("on_clear")):
            self._put(("webhook", h["url"], {"text": f"{'' if kind == 'raised' else 'Back to normal: '}[{ev.level.name}] {ev.point}: {ev.message}"}), h["name"])
        elif t == "report":
            return  # sent on its schedule, not by alarms
        elif t == "email" and (kind == "raised" or h.get("on_clear")):
            self._put(("mail", h, self.message(h, ev, kind)), h["name"])
        elif t == "setpoint" and self.setter:
            if kind == "raised":
                self._put(("set", h["point"], float(h["value"]), f"handler {h['name']}"), h["name"])
            elif "clear_value" in h:
                self._put(("set", h["point"], float(h["clear_value"]), f"handler {h['name']}"), h["name"])
            elif h.get("release_on_clear"):
                self._put(("set", h["point"], None, f"handler {h['name']}"), h["name"])

    def recipients(self, h):
        """`to` plus every named group's addresses, in that order, each address once; case-insensitive, first spelling kept."""
        out = list(h.get("to", []))
        for g in h.get("groups", []):
            out.extend(self.groups.get(g, []))
        keep, seen = [], set()
        for a in out:
            key = a.casefold()
            if key not in seen:
                seen.add(key)
                keep.append(a)
        return keep

    def _fill(self, text, ev):
        """One pass of the six documented placeholders; ponytail: no template engine, `{value}` is the last published value at send time, and {unknown} stays literal."""
        tags, source = self.resolve(ev.point)
        which = {"point": ev.point, "value": self.last_value.get(ev.point, ""), "level": ev.level.name,
                 "time": f"{ev.ts:.0f}", "message": ev.message,
                 "source": ev.point[len(SOURCE):] if ev.point.startswith(SOURCE) else (source or "")}
        return re.sub(r"\{(\w+)\}", lambda m: str(which[m.group(1)]) if m.group(1) in which else m.group(0), text)

    def message(self, h, ev, kind):
        m = email.message.EmailMessage()
        m["From"], m["To"] = h["from"], ", ".join(self.recipients(h))
        m["Subject"] = self._subject(h, ev, kind)
        body = self._fill(h["body"], ev) if h.get("body") else (
            f"{'Alarm raised' if kind == 'raised' else 'Alarm returned to normal'}\n\nPoint: {ev.point}\nLevel: {ev.level.name}\nDetail: {ev.message}\n"
            f"Raised at (epoch seconds): {ev.ts:.0f}\n")
        m.set_content(body)
        return m

    def _subject(self, h, ev, kind):
        """Every subject, template or default, is built here; a state label or message may carry a CR/LF a header cannot."""
        s = self._fill(h["subject"], ev) if h.get("subject") else f"[{ev.level.name}] {ev.point}: {ev.message}"
        return (s + ("" if kind == "raised" else " (back to normal)")).replace("\r", "").replace("\n", "")

    def send_report(self, h, csv_bytes, hours):
        m = email.message.EmailMessage()
        m["From"], m["To"], m["Subject"] = h["from"], ", ".join(h["to"]), f"DurianDB report: last {hours} h"
        m.set_content(f"Summary of the last {hours} hours is attached as CSV.\n")
        m.add_attachment(csv_bytes, maintype="text", subtype="csv", filename="report.csv")
        self._put(("mail", h, m), h["name"])

    def escalate(self, h, ev):
        """Mail the handler's escalate_to list about an alarm nobody has acknowledged."""
        m = self.message({**h, "to": h["escalate_to"], "groups": []}, ev, "raised")  # escalation stays its own list
        m.replace_header("Subject", "[ESCALATED] " + m["Subject"])
        self._put(("mail", h, m), h["name"])

    def _send_mail(self, h, m):
        s = h["smtp"]
        creds = json.loads(Path(h["creds"]).read_text()) if h.get("creds") else {}
        cls = smtplib.SMTP_SSL if s.get("security") == "ssl" else smtplib.SMTP
        kw = {"context": ssl.create_default_context()} if s.get("security") == "ssl" else {}
        with cls(s["host"], int(s.get("port", 25)), timeout=10, **kw) as c:
            if s.get("security") == "starttls":
                c.starttls(context=ssl.create_default_context())
            if creds.get("user"):
                c.login(creds["user"], creds.get("password", ""))
            c.send_message(m)

    def _work(self):
        while True:
            target, item = self.q.get()
            try:
                if item[0] == "stop":
                    return
                if item[0] == "webhook":
                    _, url, body, *extra = item
                    opener.open(urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json", **(extra[0] if extra else {})}), timeout=DELIVERY_TIMEOUT).read(1024)
                elif item[0] == "mail":
                    self._send_mail(*item[1:])
                elif item[0] == "set":
                    self.setter(*item[1:])
            except (urllib.error.URLError, OSError, ValueError, smtplib.SMTPException) as ex:
                self._note(target or item[0], "output", _reason(ex))
                log.warning("%s delivery failed: %s: %s", item[0], type(ex).__name__, ex)
            except Exception as ex:  # a handler bug must not stop the worker
                self._note(target or item[0], "output", f"the delivery crashed: {sentence(ex) or 'the handler could not run'}")
                log.warning("%s delivery crashed: %s: %s", item[0], type(ex).__name__, ex)
            else:
                if target:
                    self._note(target, "output", ok=True)

    def stop(self):
        self.q.put((None, ("stop",)))
        for cl, _, _ in self.publishers:
            cl.loop_stop()
            cl.disconnect()
