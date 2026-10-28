"""eGauge source: polls a meter's instantaneous XML and yields each register as a point named by its register name.

Request: GET http(s)://host[:port]/cgi-bin/egauge?inst. Reply (checked against the vendor's published XML API
document): <data><ts>..</ts><r t="type" n="register name"><v>cumulative</v><i>average rate over the last second</i></r>...</data>;
the point reads <i>. Only the auth scheme (digest or basic) is not confirmed against a real meter.
Meters normally want a login: optional `creds` file {"user","password"}, answered with HTTP digest or basic as the meter asks.
The XML comes off the network, so any DOCTYPE or entity declaration is refused before it can expand; redirects are not followed.
"""
import json
import math
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from xml.parsers import expat
from pathlib import Path

from .core import sentence
from .polled import NoRedirect, PolledSource, number, scale

MAX_BYTES = 1_000_000


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def safe_parse(raw):
    def refuse(*a):
        raise ValueError("the reply declares a DOCTYPE or entity, which is not allowed")
    guard = expat.ParserCreate()  # first pass only refuses DOCTYPE/entities; the tree is then built with ElementTree
    guard.StartDoctypeDeclHandler = guard.EntityDeclHandler = refuse
    try:
        guard.Parse(raw, True)
        return ET.fromstring(raw)
    except (expat.ExpatError, ET.ParseError) as e:
        raise ValueError("the reply was not valid XML") from e


def validate_source(b, add):
    if not (isinstance(b.get("host"), str) and 0 < len(b["host"]) <= 253):
        add("host", "give the meter's host name or address")
    if "port" in b and not (isinstance(b["port"], int) and not isinstance(b["port"], bool) and 1 <= b["port"] <= 65535):
        add("port", "must be 1 to 65535")
    if not isinstance(b.get("tls", False), bool):
        add("tls", "must be true or false")
    for k in ("creds", "ca"):
        if k in b and not (isinstance(b[k], str) and Path(b[k]).is_file()):
            add(k, "must be the path of an existing file")
    for k, lo, hi in (("timeout", 0.5, 60), ("period", 1, 86400), ("backoff_max", 1, 86400)):
        if k in b and not (_num(b[k]) and lo <= b[k] <= hi):
            add(k, f"must be a number from {lo} to {hi}")
    if "backoff_scale" in b and not (_num(b["backoff_scale"]) and 1 < b["backoff_scale"] <= 10):
        add("backoff_scale", "must be greater than 1 and at most 10")


class EgaugeSource(PolledSource):
    def __init__(self, name, cfg):
        super().__init__(name, cfg.get("period", 10.0), cfg.get("backoff_scale", 1.5), cfg.get("backoff_max", 300.0))
        scheme = "https" if cfg.get("tls") else "http"
        port = f":{cfg['port']}" if cfg.get("port") else ""
        self.url, self.timeout, self.creds = f"{scheme}://{cfg['host']}{port}/cgi-bin/egauge?inst", cfg.get("timeout", 5.0), cfg.get("creds")
        self.ca = cfg.get("ca")

    def registers(self):
        handlers = [NoRedirect()]
        if self.ca:
            handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=self.ca)))
        if self.creds:  # read each fetch so a changed password is picked up
            c = json.loads(Path(self.creds).read_text())
            pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            pm.add_password(None, self.url, c["user"], c["password"])
            handlers += [urllib.request.HTTPDigestAuthHandler(pm), urllib.request.HTTPBasicAuthHandler(pm)]
        with urllib.request.build_opener(*handlers).open(urllib.request.Request(self.url), timeout=self.timeout) as r:
            raw = r.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("the reply is too large")
        return {r.get("n"): r.findtext("i") for r in safe_parse(raw).iter("r") if r.get("n")}

    def fetch(self):
        found, values, rejected = self.registers(), {}, []
        for p in self.points.values():
            if p.name not in found:
                rejected.append(f"{p.name}: the meter has no register of that name")
                continue
            try:
                values[p.name] = scale(p, number(float(found[p.name])))
            except (ValueError, TypeError) as e:
                rejected.append(f"{p.name}: {sentence(e) or 'not a finite number'}")
        return values, rejected
