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

bad = copy.deepcopy(features.REGISTRY)
bad[0]["evidence"].append("durian/does_not_exist.py")
bad.append(dict(bad[1]))
bad[2]["status"] = "done"
got = features.problems(bad)
assert any("durian/does_not_exist.py does not exist" in s for s in got), got
assert any("used twice" in s for s in got) and any("status 'done' is unknown" in s for s in got), got
print("ok")
