"""The committed Rust parity vectors must still be what durian/core.py produces.

`tools/rust_parity_vectors.py` rewrites `rust/vectors/*.txt` from the real Python detectors. If a
fixture here is stale, the Rust crate is being compared against a detector the server no longer
runs. Run: python tests/test_rust_parity_vectors.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import rust_parity_vectors as vectors  # noqa: E402

generated = vectors.generate_all()
fixture_dir = ROOT / "rust" / "vectors"
committed = sorted(p.name for p in fixture_dir.glob("*.txt"))
assert committed == sorted(generated), \
    f"vector files out of step with the generator; run python tools/rust_parity_vectors.py ({committed} vs {sorted(generated)})"

for name, text in generated.items():
    path = fixture_dir / name
    actual = path.read_text(encoding="utf-8")
    assert actual == text, f"stale fixture {path}: durian/core.py no longer produces it; run python tools/rust_parity_vectors.py"

print("ok")
