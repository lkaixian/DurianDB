"""The feature inventory is sound and the progress page shows it. Run: python tests/test_features.py"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import features  # noqa: E402

assert features.problems() == [], features.problems()
ids = [e["id"] for e in features.REGISTRY]
assert len(ids) == len(set(ids))
for c, n in features.counts().items():
    assert sum(n.values()) > 0, f"category {c} is empty"
assert features.sync(write=False), "docs/progress.html is stale; run python tools/features.py html"

assert features.main(["svg", "--check"]) == 0, "docs/feature-stack.svg is stale; run python tools/features.py svg"
import xml.etree.ElementTree as ET  # noqa: E402
raw = open(os.path.join(features.ROOT, features.SVG), "rb").read().replace(bytes([13, 10]), bytes([10]))  # a Windows checkout may use CRLF
assert b"\r" not in raw and len(raw) < 60000
svg = ET.fromstring(raw)
ns = "{http://www.w3.org/2000/svg}"
cells = [r for r in svg.iter(ns + "rect") if r.get("data-cell")]
assert len(cells) == len(features.REGISTRY), (len(cells), len(features.REGISTRY))
head = next(svg.iter(ns + "text")).text
assert f"{features.pct(features.total(features.counts()))} ({features.total(features.counts())['implemented']}/" in head, head
assert svg.get("role") == "img" and svg.find(ns + "title") is not None and svg.find(ns + "desc") is not None
assert "feature-stack.svg" in open(os.path.join(features.ROOT, features.PAGE), encoding="utf-8").read()

bad = copy.deepcopy(features.REGISTRY)
bad[0]["evidence"].append("durian/does_not_exist.py")
bad.append(dict(bad[1]))
bad[2]["status"] = "done"
got = features.problems(bad)
assert any("durian/does_not_exist.py does not exist" in s for s in got), got
assert any("used twice" in s for s in got) and any("status 'done' is unknown" in s for s in got), got
print("ok")
