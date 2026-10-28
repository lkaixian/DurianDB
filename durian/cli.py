"""Command line: python -m durian <run|start|stop|status|restart|logs|supervise|check|backups|restore|passwd|version>. Exit codes: 0 fine, 1 trouble, 2 bad usage.
Also the entry point for a PyInstaller build (main())."""
import argparse
import json
import os
import signal
import subprocess
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from . import VERSION
from .core import pid_alive, sentence


def db_path():
    from .server import paths
    return Path(os.environ.get("DURIAN_DB") or paths()[1] / "durian.db")


def fail(text):
    print(text, file=sys.stderr)
    return 1


def integrity(path):
    """None when the file is a healthy SQLite database, else a plain sentence."""
    try:
        c = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
        try:
            result = c.execute("pragma integrity_check").fetchone()[0]
        finally:
            c.close()
    except Exception as ex:
        return f"The file is damaged or is not a DurianDB database ({sentence(ex) or 'it could not be read'})."
    return None if result == "ok" else f"The file is damaged: {result}."


def check(args):
    path = db_path()
    if not path.is_file():
        return fail(f"The database file {path} does not exist. Set DURIAN_DB or DURIAN_HOME to where the server keeps it.")
    if (bad := integrity(path)):
        return fail(bad)
    c = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        n = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
        print(f"integrity ok\nusers {n('select count(*) from users')}\npoints {n('select count(*) from objects where kind=?', 'point')}\n"
              f"sources {n('select count(*) from objects where kind=?', 'source')}\nhistory rows {n('select count(*) from history')}")
    except Exception as ex:
        return fail(f"The database opens but could not be counted ({sentence(ex) or 'is it a DurianDB database?'}).")
    finally:
        c.close()
    return 0


def backups_dir():
    from .server import paths
    return paths()[1] / "backups"


def backups(args):
    d = backups_dir()
    files = sorted((p for p in d.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []
    if not files:
        print(f"No backups in {d}.")
    for p in files:
        st = p.stat()
        print(f"{p.name}  {st.st_size} bytes  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}")
    return 0


def restore(args):
    src, dest = Path(args.file), db_path()
    if not src.is_file():
        return fail(f"The backup file {src} does not exist.")
    if (bad := integrity(src)):
        return fail(f"Not restored: {bad}")
    if dest.is_file():
        try:  # an exclusive lock fails while a server (or any other process) holds the database
            c = sqlite3.connect(dest, timeout=0.5, isolation_level=None)
            try:
                c.execute("pragma locking_mode=exclusive")
                c.execute("begin exclusive")
                c.execute("select count(*) from sqlite_master").fetchone()
                c.execute("rollback")
            finally:
                c.close()
        except sqlite3.Error:
            return fail("Not restored: the database is in use. Stop the server first, then run restore again.")
        keep = dest.with_name(dest.name + ".before-restore")
        shutil.copy2(dest, keep)
        print(f"The current database was copied to {keep}.")
    for suffix in ("-wal", "-shm"):  # leftovers of the old database would be replayed onto the restored one
        dest.with_name(dest.name + suffix).unlink(missing_ok=True)
    try:
        shutil.copy2(src, dest)
    except OSError as ex:
        return fail(f"The backup could not be copied into place ({sentence(ex) or 'check the folder permissions'}).")
    print(f"Restored {src.name} to {dest}.")
    return 0


STOP_WAIT = 20  # seconds `durian stop` lets the server leave on its own before killing it
START_WAIT = 30
LOG_CAP = 5 * 1024 * 1024


def files():
    """(pid file, stop file, log file) in the data folder."""
    from .server import paths
    d = paths()[1]
    return d / "durian.pid", d / "durian.stop", d / "durian.log"


def child_command(args):
    """What `durian start` launches: the supervisor (it restarts the server), or the bare server. Frozen builds run
    themselves again, like the supervisor does."""
    frozen = getattr(sys, "frozen", False)
    if args.no_supervise:
        return [sys.executable, "run"] if frozen else [sys.executable, "-m", "durian.server"]
    return [sys.executable, "supervise"] if frozen else [sys.executable, "-m", "durian", "supervise"]


def read_pid_file():
    try:
        return json.loads(files()[0].read_text())
    except (OSError, ValueError):
        return None


def url_of(info, path=""):
    host = "127.0.0.1" if info["host"] in ("0.0.0.0", "", "::") else info["host"]
    return f"{'https' if info['tls'] else 'http'}://{host}:{info['port']}{path}"


def gone(pid):
    """True once the process has ended (a child of this process is reaped first, or it would linger as a zombie)."""
    if os.name != "nt":
        try:
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass
    return not pid_alive(pid)


def log_tail(n):
    try:
        return "\n".join(files()[2].read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def start(args):
    from . import supervisor
    pidf, stopf, logf = files()
    info = read_pid_file()
    if info and pid_alive(info["pid"]):
        return fail(f"DurianDB is already running (pid {info['pid']}, {url_of(info)}). Use durian status, or durian restart.")
    pidf.unlink(missing_ok=True)
    stopf.unlink(missing_ok=True)
    if logf.exists() and logf.stat().st_size >= LOG_CAP:
        os.replace(logf, logf.with_name(logf.name + ".1"))
    host = args.host or os.environ.get("DURIAN_HOST", "127.0.0.1")
    port = int(args.port or os.environ.get("DURIAN_PORT", 8765))
    safe = args.safe or os.environ.get("DURIAN_SAFE") == "1"
    env = dict(os.environ, DURIAN_HOST=host, DURIAN_PORT=str(port), PYTHONUNBUFFERED="1", **({"DURIAN_SAFE": "1"} if safe else {}))
    kw = {"creationflags": 0x00000008 | 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}  # DETACHED_PROCESS|NEW_PROCESS_GROUP|NO_WINDOW
    try:
        with open(logf, "ab") as out:
            child = subprocess.Popen(child_command(args), env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out, **kw)
    except OSError as ex:
        return fail(f"DurianDB could not be started ({sentence(ex) or 'check the log folder'}).")
    info = {"pid": child.pid, "port": port, "host": host, "tls": bool(env.get("DURIAN_TLS_CERT")), "started": time.time(), "safe": safe}
    pidf.write_text(json.dumps(info))
    end = time.time() + START_WAIT
    while time.time() < end:
        if child.poll() is not None:
            pidf.unlink(missing_ok=True)
            return fail(f"DurianDB exited right after starting (code {child.returncode}). Last lines of {logf}:\n{log_tail(10)}")
        if supervisor._probe(url_of(info, "/api/health")):
            print(f"DurianDB is running at {url_of(info)} (pid {child.pid}). Logs: durian logs -f. Stop it: durian stop.")
            return 0
        time.sleep(0.25)
    kill(child.pid)
    pidf.unlink(missing_ok=True)
    return fail(f"DurianDB did not answer within {START_WAIT} seconds, so it was stopped. Last lines of {logf}:\n{log_tail(10)}")


def spool_rows():
    try:
        with open(db_path().parent / "spool.ndjson", "rb") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def status(args):
    info = read_pid_file()
    if not info:
        print("DurianDB is stopped (no durian.pid in the data folder).")
        return 1
    if not pid_alive(info["pid"]):
        print(f"DurianDB is stopped: durian.pid is stale (pid {info['pid']} is gone). durian start replaces it.")
        return 1
    from . import supervisor
    up = supervisor._probe(url_of(info, "/api/health"))
    secs = int(time.time() - info["started"])
    print(f"DurianDB is {'running' if up else 'running but not answering'} (pid {info['pid']})\nurl {url_of(info)}\n"
          f"up {secs // 3600}h {secs % 3600 // 60}m {secs % 60}s\nsafe mode {'yes' if info['safe'] else 'no'}\nspool rows {spool_rows()}")
    return 0 if up else 1


def kill(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)  # /T: the supervisor's server too
    else:
        try:  # the child leads its own session (start_new_session), so the group is the supervisor and its server
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            os.kill(pid, signal.SIGKILL)
    end = time.time() + 5
    while time.time() < end and not gone(pid):
        time.sleep(0.1)


def stop(args):
    pidf, stopf, _ = files()
    info = read_pid_file()
    if not info:
        print("DurianDB is not running.")
        return 0
    pid = info["pid"]
    if not pid_alive(pid):
        pidf.unlink(missing_ok=True)
        print(f"DurianDB is not running; the stale durian.pid (pid {pid}) was removed.")
        return 0
    stopf.write_text("stop\n")
    end = time.time() + STOP_WAIT
    while time.time() < end and not gone(pid):
        time.sleep(0.2)
    if gone(pid):
        msg = f"DurianDB stopped cleanly (pid {pid})."
    else:
        kill(pid)
        msg = f"DurianDB did not stop within {STOP_WAIT} seconds, so it was killed (pid {pid}); its tick lease is taken over by the next start."
    pidf.unlink(missing_ok=True)
    stopf.unlink(missing_ok=True)
    print(msg)
    return 0 if gone(pid) else fail("The process is still alive; end it from Task Manager or with kill -9.")


def restart(args):
    return stop(args) or start(args)


def logs(args):
    logf = files()[2]
    if not logf.is_file():
        return fail(f"There is no log yet ({logf}); durian start writes it.")
    print(log_tail(args.n))
    if args.follow:
        try:
            with open(logf, "rb") as f:
                f.seek(0, 2)
                while True:
                    chunk = f.read()
                    if chunk:
                        sys.stdout.write(chunk.decode(errors="replace"))
                        sys.stdout.flush()
                    else:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    return 0


def run(args):
    from . import server
    if sys.stdout.isatty():
        print("Running in the foreground; Ctrl+C stops it. To run it in the background and keep your prompt: durian start")
    server.main(safe=args.safe, host=args.host, port=args.port)
    return 0


def supervise(args):
    try:
        from . import supervisor
    except ImportError:
        return fail("The supervisor is not available in this build.")
    return supervisor.main(args.knobs) or 0


def passwd(args):
    os.environ.setdefault("DURIAN_DB", str(db_path()))
    from . import reset
    reset.main(args.user)  # exits with a sentence on trouble
    return 0


def version(args):
    print(f"DurianDB {VERSION}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="durian")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="start the server (default)")
    r.add_argument("--safe", action="store_true", help="database, login and admin API only; nothing is polled or published")
    r.add_argument("--host")
    r.add_argument("--port", type=int)
    sub.add_parser("supervise", help="run the server under the supervisor").add_argument("knobs", nargs=argparse.REMAINDER)
    for name, text in (("start", "run in the background, under the supervisor"), ("restart", "stop, then start")):
        b = sub.add_parser(name, help=text)
        b.add_argument("--safe", action="store_true")
        b.add_argument("--no-supervise", action="store_true", help="run the bare server, no restart on failure")
        b.add_argument("--host")
        b.add_argument("--port", type=int)
    sub.add_parser("status", help="is the background server running")
    sub.add_parser("stop", help="stop the background server")
    lg = sub.add_parser("logs", help="show the end of durian.log")
    lg.add_argument("-n", type=int, default=50)
    lg.add_argument("-f", "--follow", action="store_true")
    sub.add_parser("check", help="integrity check and row counts")
    sub.add_parser("backups", help="list backups")
    sub.add_parser("restore", help="replace the database with a backup (server stopped)").add_argument("file")
    sub.add_parser("passwd", help="set a new random password for a user").add_argument("user")
    sub.add_parser("version", help="print the version")
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    if args.cmd is None:
        args = p.parse_args(["run", *(argv or [])])
    return {"run": run, "supervise": supervise, "start": start, "stop": stop, "status": status,
            "restart": restart, "logs": logs, "check": check, "backups": backups, "restore": restore,
            "passwd": passwd, "version": version}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
