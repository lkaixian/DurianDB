"""Run every test file in its own process. Usage: python tests/run_all.py"""
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
        print((r.stdout + r.stderr)[-1500:])
sys.exit(1 if failed else 0)
