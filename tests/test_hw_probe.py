"""Hardware probe: absence of a lab device is the normal state, and it must be a clear SKIP.

No hardware is needed: DURIAN_HW_CONFIG points the probe at a temporary config, so this test owns the file.
Run: python tests/test_hw_probe.py"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tools" / "hw_probe.py"
PROTOCOLS = ("com", "smtp", "snmp", "sparkplug", "dnp3", "bacnet", "opcua")


def run(config_dir, *args):
    env = {**os.environ, "DURIAN_HW_CONFIG": str(Path(config_dir) / "hw.json")}
    return subprocess.run([sys.executable, str(PROBE), *args], capture_output=True, text=True, env=env, timeout=30)


def no_traceback(r):
    assert "Traceback" not in r.stdout + r.stderr, r.stdout + r.stderr


with tempfile.TemporaryDirectory() as tmp:
    # ---- no config file at all: every protocol SKIPs with a reason, exit 0
    r = run(tmp)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    no_traceback(r)
    for name in PROTOCOLS:
        assert f"SKIP {name}: no config for it in" in r.stdout, r.stdout
    assert "0 ran, 7 skipped, 0 failed" in r.stdout, r.stdout

    # ---- an empty object is the same honest state
    (Path(tmp) / "hw.json").write_text("{}")
    r = run(tmp)
    assert r.returncode == 0 and "0 ran, 7 skipped, 0 failed" in r.stdout, r.stdout
    no_traceback(r)

    # ---- selecting one protocol probes only that one
    r = run(tmp, "com")
    assert r.returncode == 0 and "SKIP com:" in r.stdout, r.stdout
    assert "0 ran, 1 skipped, 0 failed" in r.stdout and "SKIP snmp" not in r.stdout, r.stdout

    # ---- malformed JSON: one clear sentence, not a traceback
    (Path(tmp) / "hw.json").write_text("{ this is not json")
    r = run(tmp)
    assert r.returncode != 0, (r.returncode, r.stdout)
    assert "ERROR:" in r.stdout and "JSON" in r.stdout, r.stdout
    assert str(Path(tmp) / "hw.json") in r.stdout, r.stdout
    no_traceback(r)

    # ---- a section of the wrong shape is a config error too, never a crash later
    (Path(tmp) / "hw.json").write_text(json.dumps({"com": ["COM7"]}))
    r = run(tmp)
    assert r.returncode != 0 and "ERROR:" in r.stdout and "com" in r.stdout, r.stdout
    no_traceback(r)

    # ---- a config that names a device but omits its address is a FAIL with a sentence, exit 1
    (Path(tmp) / "hw.json").write_text(json.dumps({"com": {}}))
    r = run(tmp, "com")
    assert r.returncode == 1, (r.returncode, r.stdout)
    assert "FAIL com:" in r.stdout and "port" in r.stdout, r.stdout
    assert "0 ran, 0 skipped, 1 failed" in r.stdout, r.stdout
    no_traceback(r)

print("ok")
