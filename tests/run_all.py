"""Run every test file in its own process. Usage: python tests/run_all.py

A failure also becomes a GitHub Actions annotation when running in CI, so the check run names the test and
quotes its output: the step log itself needs a token to read, an annotation does not.
"""
import os
import subprocess
import sys
from pathlib import Path

failed = []
for t in sorted(Path(__file__).parent.glob("test_*.py")):
    r = subprocess.run([sys.executable, str(t)], capture_output=True, text=True)
    ok = r.returncode == 0 and r.stdout.strip().endswith("ok")
    print(("PASS " if ok else "FAIL ") + t.name)
    if not ok:
        failed.append(t.name)
        output = (r.stdout + r.stderr)[-1500:]
        print(output)
        if os.environ.get("GITHUB_ACTIONS"):  # one annotation carries the reason out of a private log
            print(f"::error title={t.name}::" + output.replace("\r", "").replace("%", "%25").replace("\n", "%0A")[-800:])
sys.exit(1 if failed else 0)
