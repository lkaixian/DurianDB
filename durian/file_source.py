"""File source: a local text file of `name=value` or `name,value` lines, re-read each cycle. A point takes the line of its own name.

Numbers are finite or the point is rejected; a text point takes the rest of the line as it is. The path is
operator-configured (config PUT is authenticated); the file is read whole only if it is under MAX_BYTES.
"""
import math
import os

from .polled import PolledSource, number, scale

MAX_BYTES, MAX_LINES = 1_000_000, 10000


def validate_source(b, add):
    if not (isinstance(b.get("path"), str) and 0 < len(b["path"]) <= 255):
        add("path", "give the path of the text file to read, up to 255 characters")
    if b.get("format", "lines") != "lines":
        add("format", 'must be "lines"')
    if "period" in b and not (isinstance(b["period"], (int, float)) and not isinstance(b["period"], bool)
                              and math.isfinite(b["period"]) and 1 <= b["period"] <= 86400):
        add("period", "must be a number from 1 to 86400")


class FileSource(PolledSource):
    def __init__(self, name, cfg):
        super().__init__(name, cfg.get("period", 10.0), cfg.get("backoff_scale", 1.5), cfg.get("backoff_max", 300.0))
        self.path = cfg["path"]

    def fetch(self):
        if os.path.getsize(self.path) > MAX_BYTES:
            raise ValueError(f"the file is too large (over {MAX_BYTES} bytes)")
        with open(self.path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()[:MAX_LINES]
        found = {}
        for ln in lines:
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                k, sep, v = ln.partition("=" if "=" in ln else ",")
                if sep:
                    found[k.strip()] = v.strip()
        values, rejected = {}, []
        for p in self.points.values():
            if p.name not in found:
                rejected.append(f"{p.name}: no line for it")
            elif p.text:
                values[p.name] = found[p.name]
            else:
                try:
                    values[p.name] = scale(p, number(float(found[p.name])))
                except ValueError:
                    rejected.append(f"{p.name}: not a finite number")
        return values, rejected
