"""File source: a local text file of `name=value` or `name,value` lines, re-read each cycle. A point takes the line of its own name.

Numbers are finite or the point is rejected; a text point takes the rest of the line as it is. The path is
operator-configured (config PUT is authenticated); the file is read whole only if it is under MAX_BYTES.
"""
import math
import os
import re
import stat

from .polled import PolledSource, number, scale

MAX_BYTES, MAX_LINES = 1_000_000, 10000
_DEVICE = re.compile(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", re.I)  # Windows device names, with or without extension


def validate_source(b, add):
    if not (isinstance(b.get("path"), str) and 0 < len(b["path"]) <= 255):
        add("path", "give the path of the text file to read, up to 255 characters")
    elif os.name == "nt" and _DEVICE.fullmatch(re.split(r"[/\\]", b["path"])[-1]):
        add("path", "a device name such as CON or NUL is not a file")
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
        if not stat.S_ISREG(os.stat(self.path).st_mode):  # before open(): a FIFO or device would block the poll thread
            raise ValueError("the path is not a regular file")
        with open(self.path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(MAX_BYTES + 1)  # bounded however the file grows after the stat
        if len(text) > MAX_BYTES:
            raise ValueError(f"the file is too large (over {MAX_BYTES} bytes)")
        lines = text.splitlines()[:MAX_LINES]
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
            elif p.text:  # same rule as ingest/set
                if 1 <= len(found[p.name]) <= 255 and found[p.name].isprintable():
                    values[p.name] = found[p.name]
                else:
                    rejected.append(f"{p.name}: text must be 1 to 255 printable characters")
            else:
                try:
                    values[p.name] = scale(p, number(float(found[p.name])))
                except ValueError:
                    rejected.append(f"{p.name}: not a finite number")
        return values, rejected
