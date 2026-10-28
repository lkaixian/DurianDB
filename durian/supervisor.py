"""Watchdog: runs the server as a child, restarts it when it exits or stops answering /api/health.

After a crash loop it restarts the child once in safe mode (DURIAN_SAFE=1: no sources) and stays there.
Run: durian supervise   (or python -m durian.supervisor)
"""
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


def default_cmd(frozen=None):
    return [sys.executable, "run"] if (getattr(sys, "frozen", False) if frozen is None else frozen) \
        else [sys.executable, "-m", "durian.server"]


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + "supervisor: " + msg, file=sys.stderr, flush=True)


def alert(url, text):
    """POST {"text": text} to an http(s) URL. Never raises; True when delivered."""
    try:
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("the alert address must start with http:// or https://")
        req = urllib.request.Request(url, json.dumps({"text": text}).encode(), {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).close()
        return True
    except Exception as ex:
        log(f"could not send the alert ({ex})")
        return False


def _probe(url, timeout=2):
    try:  # ponytail: https health checks skip certificate checks (it is our own child on localhost)
        ctx = ssl._create_unverified_context() if url.startswith("https") else None
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
            return r.status == 200
    except Exception:
        return False


def _inherit():
    """Hand our redirected stdout/stderr to the child. Without a console (a detached start) Windows gives it nothing otherwise."""
    try:
        return {"stdout": sys.stdout.fileno(), "stderr": sys.stderr.fileno()}
    except (AttributeError, OSError, ValueError):
        return {}


def _stop_child(p, graceful=0):
    """graceful: seconds to let the child leave on its own first (it watches the same stop file), so it releases its lease."""
    if p.poll() is None and graceful:
        try:
            p.wait(graceful)
        except subprocess.TimeoutExpired:
            pass
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def run(cmd, stop, health_interval=10.0, hang_after=3, startup_grace=60.0, crash_limit=5,
        crash_window=600.0, backoff_cap=60.0, healthy_reset=300.0, probe_timeout=2.0, stop_file=None):
    """Supervise `cmd` until `stop` (a threading.Event) is set."""
    db = os.environ.get("DURIAN_DB")
    status_path = (Path(db).parent if db else Path(__file__).resolve().parent.parent) / "supervisor.json"
    host = os.environ.get("DURIAN_HOST", "127.0.0.1")
    host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    scheme = "https" if os.environ.get("DURIAN_TLS_CERT") else "http"
    url = f"{scheme}://{host}:{os.environ.get('DURIAN_PORT', 8765)}/api/health"
    st = {"state": "starting", "restarts": 0, "last_exit": None, "safe": False, "since": time.time()}
    alert_url = os.environ.get("DURIAN_ALERT_URL")

    def save(state):
        if state != st["state"]:
            st["state"], st["since"] = state, time.time()
        try:
            tmp = status_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(st))
            os.replace(tmp, status_path)
        except OSError as ex:
            log(f"could not write {status_path} ({ex})")

    def tell(text):
        log(text)
        if alert_url:
            alert(alert_url, text)

    failures, backoff = [], 1.0
    while not stop.is_set():
        env = dict(os.environ, **({"DURIAN_SAFE": "1"} if st["safe"] else {}))
        child = subprocess.Popen(cmd, env=env, **_inherit())
        log(f"started the server (pid {child.pid}){' in safe mode' if st['safe'] else ''}")
        save("safe" if st["safe"] else "running")
        born, healthy_since, bad, ever, reason = time.time(), None, 0, False, None
        while reason is None and not stop.wait(health_interval):
            now = time.time()
            if child.poll() is not None:
                reason = f"the server exited with code {child.returncode}"
            elif _probe(url, probe_timeout):
                ever, bad = True, 0
                healthy_since = healthy_since or now
            else:
                bad, healthy_since = bad + 1, None
                if ever and bad >= hang_after:
                    reason = f"the server stopped answering health checks ({bad} in a row) and was hung"
                elif not ever and now - born > startup_grace:
                    reason = f"the server did not become healthy within {startup_grace:g} seconds and was hung"
        was_healthy = healthy_since is not None and time.time() - healthy_since >= healthy_reset
        _stop_child(child, graceful=10 if stop_file and stop_file.exists() else 0)
        if stop.is_set():
            break
        st["last_exit"], st["restarts"] = reason, st["restarts"] + 1
        if was_healthy:
            backoff = 1.0
        now = time.time()
        failures = [t for t in failures + [now] if now - t < crash_window]
        if not st["safe"] and len(failures) >= crash_limit:
            st["safe"], backoff = True, 1.0
            tell(f"The server failed {len(failures)} times in {crash_window / 60:g} minutes ({reason}). "
                 "Restarting it in safe mode with no data sources; fix the configuration, then restart the supervisor.")
        else:
            tell(f"{reason}; restarting in {backoff:g} seconds.")
        save("backoff")
        stop.wait(min(backoff, backoff_cap))
        backoff = min(backoff * 2, backoff_cap)
    save("stopped")
    log("stopped")


def _env(name, default, cast=float):
    try:
        return cast(os.environ.get("DURIAN_SUP_" + name, default))
    except ValueError:
        sys.exit(f"DURIAN_SUP_{name} must be a number.")


def main(argv=None):
    """Knobs: DURIAN_SUP_HEALTH_INTERVAL, _HANG_AFTER, _STARTUP_GRACE, _CRASH_LIMIT; argv: --name value overrides."""
    knobs = {"health_interval": _env("HEALTH_INTERVAL", 10), "hang_after": _env("HANG_AFTER", 3, int),
             "startup_grace": _env("STARTUP_GRACE", 60), "crash_limit": _env("CRASH_LIMIT", 5, int)}
    a = list(sys.argv[1:] if argv is None else argv)
    while a:
        key = a[0].lstrip("-").replace("-", "_")
        if len(a) < 2 or not a[0].startswith("--") or key not in knobs:
            sys.exit("Usage: durian supervise [--health-interval S] [--hang-after N] [--startup-grace S] [--crash-limit N]")
        try:
            knobs[key] = type(knobs[key])(a[1])
        except ValueError:
            sys.exit(f"{a[0]} must be a number.")
        a = a[2:]
    stop = threading.Event()
    from . import server
    server.stop_file().unlink(missing_ok=True)  # a stop request left by an earlier run must not stop this one
    server.watch_stop_file(stop.set)
    import signal
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: stop.set())
    run(default_cmd(), stop, stop_file=server.stop_file(), **knobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
