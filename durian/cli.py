"""Command line: python -m durian <run|supervise|check|backups|restore|passwd|version>. Exit codes: 0 fine, 1 trouble, 2 bad usage.
Also the entry point for a PyInstaller build (main())."""
import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from . import VERSION
from .core import sentence


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


def run(args):
    from . import server
    server.main(safe=args.safe, host=args.host, port=args.port)
    return 0


def supervise(args):
    try:
        from . import supervisor
    except ImportError:
        return fail("The supervisor is not available in this build.")
    return supervisor.main() or 0


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
    sub.add_parser("supervise", help="run the server under the supervisor")
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
    return {"run": run, "supervise": supervise, "check": check, "backups": backups, "restore": restore,
            "passwd": passwd, "version": version}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
