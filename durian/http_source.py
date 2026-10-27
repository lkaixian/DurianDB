"""HTTP JSON source: fetch one JSON document per cycle and read numbers out of it by path (a.b[2].c).

Redirects are never followed, responses are size-capped, and TLS is verified (optionally against a private CA).
"""
import json
import math
import re
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

from .core import sentence
from .polled import NoRedirect, PolledSource, number, scale

PATH_OK = re.compile(r"^(?:[A-Za-z0-9_-]+|\[\d+\])(?:\.[A-Za-z0-9_-]+|\[\d+\])*$")
TOKENS = re.compile(r"[^.\[\]]+|\[\d+\]")
HEADER = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def dig(data, path):
    cur = data
    for tok in TOKENS.findall(path):
        if tok.startswith("["):
            cur = cur[int(tok[1:-1])]
        elif isinstance(cur, dict):
            cur = cur[tok]
        else:
            raise KeyError(tok)
    return cur


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_source(b, add):
    u = urllib.parse.urlparse(b.get("url", "")) if isinstance(b.get("url"), str) else None
    if not u or u.scheme not in ("http", "https") or not u.hostname:
        add("url", "must be an http or https URL")
    h = b.get("headers", {})
    if not (isinstance(h, dict) and len(h) <= 10 and all(isinstance(k, str) and HEADER.match(k) and isinstance(v, str)
                                                         and len(v) <= 512 and not re.search(r"[\r\n]", v) for k, v in h.items())):
        add("headers", "must be up to 10 header names with single-line text values")
    for k in ("token_file", "ca"):
        if k in b and not (isinstance(b[k], str) and Path(b[k]).is_file()):
            add(k, "must be the path of an existing file")
    for k, lo, hi in (("timeout", 0.5, 60), ("period", 1, 86400), ("max_bytes", 1024, 10_000_000), ("backoff_max", 1, 86400)):
        if k in b and not (_num(b[k]) and lo <= b[k] <= hi):
            add(k, f"must be a number from {lo} to {hi}")
    if "backoff_scale" in b and not (_num(b["backoff_scale"]) and 1 < b["backoff_scale"] <= 10):
        add("backoff_scale", "must be greater than 1 and at most 10")


def validate_point(b, add):
    h = b.get("http")
    if not (isinstance(h, dict) and isinstance(h.get("path"), str) and PATH_OK.match(h["path"])):
        add("http.path", "a point on an HTTP JSON source needs a path such as sensors.temp or items[0].value")


class HttpJsonSource(PolledSource):
    def __init__(self, name, cfg):
        super().__init__(name, cfg.get("period", 10.0), cfg.get("backoff_scale", 1.5), cfg.get("backoff_max", 300.0))
        self.url, self.headers = cfg["url"], dict(cfg.get("headers", {}))
        self.token_file, self.timeout, self.max_bytes = cfg.get("token_file"), cfg.get("timeout", 5.0), cfg.get("max_bytes", 1_000_000)
        handlers = [NoRedirect()]
        if cfg.get("ca"):
            handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=cfg["ca"])))
        self.opener = urllib.request.build_opener(*handlers)

    def document(self):
        """One fetch of the JSON document. Public so the Test button can show what a source would see."""
        headers = {"Accept": "application/json", **self.headers}
        if self.token_file and "Authorization" not in headers:  # read each time so a rotated token is picked up
            headers["Authorization"] = "Bearer " + Path(self.token_file).read_text().strip()
        with self.opener.open(urllib.request.Request(self.url, headers=headers), timeout=self.timeout) as r:
            raw = r.read(self.max_bytes + 1)
        if len(raw) > self.max_bytes:
            raise ValueError("response larger than max_bytes")
        try:
            return json.loads(raw)
        except ValueError as e:
            raise ValueError(f"the reply was not JSON: {sentence(e)}") from e

    def fetch(self):
        data = self.document()
        values, rejected = {}, []
        for p in self.points.values():
            path = p.meta["http"]["path"]
            try:
                values[p.name] = scale(p, number(dig(data, path)))
            except KeyError:
                rejected.append(f"{p.name}: {path} is not in the reply")
            except (IndexError, TypeError):
                rejected.append(f"{p.name}: {path} does not reach a value in the reply")
            except ValueError as e:
                rejected.append(f"{p.name}: {sentence(e)}")
        return values, rejected
