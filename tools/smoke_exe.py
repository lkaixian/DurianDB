"""Smoke test a built executable. Usage: python tools/smoke_exe.py <path to durian or durian.exe>

Uses a throwaway DURIAN_HOME and free ports. Checks: `version` prints the version in durian/__init__.py; `run`
answers /api/health with 200 within 30 seconds and serves the console at / (so the web assets are bundled);
`run --safe` reports "safe": true. Exit 0 when all pass, otherwise 1 with a plain sentence.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADMIN_PASSWORD = "smoke-test-admin-123"


class Failed(Exception):
    pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def env_for(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith("DURIAN_")}  # nothing from the developer's shell
    env.update(DURIAN_HOME=str(home), DURIAN_HOST="127.0.0.1", DURIAN_ADMIN_PASSWORD=ADMIN_PASSWORD)
    return env


def tail(path):
    try:
        return path.read_text(errors="replace")[-1000:].strip()
    except OSError:
        return ""


def get(url):
    with urllib.request.urlopen(url, timeout=3) as r:  # HTTPError (a non-2xx answer) is an OSError too
        return r.status, r.read().decode("utf-8", "replace")


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop(p):
    if p.poll() is None:
        if os.name == "nt":  # the onefile bootloader is the parent of the real server; taskkill /T takes both
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        else:
            p.terminate()
    try:
        p.wait(15)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()


@contextmanager
def serve(exe, home, args, name):
    """Start `exe run ARGS` on a free port. Yields (port, process, log path); always stops the process after."""
    port, log = free_port(), home / f"{name}.log"
    with log.open("w") as out:
        p = subprocess.Popen([str(exe), "run", *args], env=dict(env_for(home), DURIAN_PORT=str(port)),
                             stdout=out, stderr=subprocess.STDOUT)
    try:
        yield port, p, log
    finally:
        stop(p)
        for _ in range(20):  # the server must release its port: a survivor would hold the database open
            if not port_open(port):
                break
            time.sleep(0.25)
        else:
            raise Failed(f"the server is still listening on port {port} after it was stopped.")


def wait_health(p, port, log, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        if p.poll() is not None:
            raise Failed(f"the server stopped on its own with exit code {p.returncode}. Last output: {tail(log)}")
        try:
            status, body = get(f"http://127.0.0.1:{port}/api/health")
            if status == 200:
                return json.loads(body)
        except OSError:
            pass  # not listening yet
        if time.monotonic() > deadline:
            raise Failed(f"the server did not answer /api/health with 200 within {timeout} seconds. Last output: {tail(log)}")
        time.sleep(0.5)


def check_version(exe, home):
    version = re.search(r'^VERSION = "([^"]+)"', (ROOT / "durian" / "__init__.py").read_text(), re.M).group(1)
    try:
        r = subprocess.run([str(exe), "version"], capture_output=True, text=True, env=env_for(home), timeout=60)
    except subprocess.TimeoutExpired:
        raise Failed("`version` did not finish within 60 seconds.")
    if r.returncode != 0 or r.stdout.strip() != f"DurianDB {version}":
        raise Failed(f"`version` printed {r.stdout.strip()!r} with exit code {r.returncode}; expected 'DurianDB {version}'.")


def check_run(exe, home):
    with serve(exe, home, [], "run") as (port, p, log):
        if wait_health(p, port, log).get("safe") is not False:
            raise Failed("`run` reported safe mode on /api/health; it should run the full server.")
        try:
            status, body = get(f"http://127.0.0.1:{port}/")
        except OSError as ex:
            raise Failed(f"the console page at / could not be fetched ({ex}).")
        if status != 200 or "DurianDB Console" not in body:
            raise Failed("the console page at / is missing or is not the DurianDB console, so the web assets are not bundled.")


def check_safe(exe, home):
    with serve(exe, home, ["--safe"], "run-safe") as (port, p, log):
        if wait_health(p, port, log).get("safe") is not True:
            raise Failed('`run --safe` did not report "safe": true on /api/health.')


def main(argv):
    if len(argv) != 2:
        print("Usage: python tools/smoke_exe.py <path to the durian executable>", file=sys.stderr)
        return 2
    exe = Path(argv[1]).resolve()
    if not exe.is_file():
        print(f"The executable {exe} does not exist. Build it with tools/build_exe.py first.", file=sys.stderr)
        return 1
    home = Path(tempfile.mkdtemp(prefix="durian-smoke-"))
    try:
        check_version(exe, home)
        check_run(exe, home)
        check_safe(exe, home)
    except Failed as ex:
        print(f"Smoke test failed: {ex}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print(f"Smoke test passed for {exe.name}: version, health, console page and safe mode.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
