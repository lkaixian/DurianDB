"""Haystack source: a read-only client of a Project Haystack server. Each cycle it POSTs the `read` op (a Hayson grid
body, as the public Haystack Ops documentation specifies) for a list of ids or a filter and takes each entity's `curVal`. A point takes the entity whose `dis` (or, lacking one,
whose id) equals the point's name. Nothing is ever written to the server.
"""

from . import http_source
from .core import sentence
from .polled import number, scale


def validate_source(b, add):
    http_source.validate_source(b, add)
    ids, flt = b.get("ids"), b.get("filter")
    if (ids is None) == (flt is None):
        add("ids", "give either ids (a list of entity ids) or filter (a Haystack filter), not both and not neither")
    elif ids is not None and not (isinstance(ids, list) and 0 < len(ids) <= 100 and all(isinstance(i, str) and 0 < len(i) <= 128 for i in ids)):
        add("ids", "must be a list of 1 to 100 entity ids")
    elif flt is not None and not (isinstance(flt, str) and 0 < len(flt) <= 500):
        add("filter", "must be filter text up to 500 characters")


def _text(v):
    """A ref or string as bare text: Hayson refs are {"_kind": "ref", "val": ...}."""
    return v.get("val") if isinstance(v, dict) else v


class HaystackSource(http_source.HttpJsonSource):
    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.url = cfg["url"].rstrip("/") + "/read"
        grid = {"_kind": "grid", "meta": {"ver": "3.0"}}
        if "ids" in cfg:
            self.body = {**grid, "cols": [{"name": "id"}], "rows": [{"id": {"_kind": "ref", "val": i.lstrip("@")}} for i in cfg["ids"]]}
        else:
            self.body = {**grid, "cols": [{"name": "filter"}], "rows": [{"filter": cfg["filter"]}]}

    def fetch(self):
        rows = self.document().get("rows", [])  # no matches is an empty grid
        if not isinstance(rows, list):
            raise ValueError("the reply was not a Haystack grid")
        by_name = {}
        for r in rows:
            if isinstance(r, dict):
                n = _text(r.get("dis")) or _text(r.get("id"))
                if isinstance(n, str):
                    by_name[n] = r.get("curVal")
        values, rejected = {}, []
        for p in self.points.values():
            if p.name not in by_name:
                rejected.append(f"{p.name}: not in the reply")
                continue
            v = by_name[p.name]
            try:
                values[p.name] = scale(p, number(v["val"] if isinstance(v, dict) and v.get("_kind") == "number" else v))
            except ValueError as e:
                rejected.append(f"{p.name}: {sentence(e)}")
        return values, rejected
