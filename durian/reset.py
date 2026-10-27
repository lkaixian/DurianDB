"""Set a new password for a user straight in the database, for when nobody can sign in. Run: python -m durian.reset admin

Prints a new random password. Also turns off the user's two-factor sign-in. Needs access to the database, which is the point.
"""
import os
import secrets
import sys
from pathlib import Path

from .auth import hash_password
from .store import Store


def database_url():
    return os.environ.get("DURIAN_DB", str(Path(__file__).resolve().parent.parent / "durian.db"))


def database_path(url):
    """The file a URL points at, or None for a database this tool cannot check as a file (MySQL, in memory)."""
    if url.startswith("mysql://") or url == ":memory:":
        return None
    return url[len("sqlite:///"):] if url.startswith("sqlite:///") else url


def main(name):
    url = database_url()
    path = database_path(url)
    if path is not None and not Path(path).is_file():
        sys.exit(f"The database file {path} does not exist. Point DURIAN_DB at the database the server uses.")
    try:
        store = Store(url)
        user = store.user(name)
    except Exception as e:
        sys.exit(f"The database could not be read: {e}. Check DURIAN_DB and that the database file is not damaged.")
    if not user:
        sys.exit(f"There is no user named {name}. Check the spelling against the users in the database.")
    pw = secrets.token_urlsafe(12)
    try:
        with store.atomic():  # one failed step must not leave a half-reset account
            store.set_hash(name, hash_password(pw))
            store.run("update users set totp=null where name=?", (name,))
            store.audit("system", "password.reset", f"{name}, from the command line")  # never the password itself
    except Exception as e:
        sys.exit(f"The password could not be changed: {e}. The database may be read-only or locked by another process.")
    print(f"New password for {name}: {pw}\nRestart the server if the account was locked out.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "admin")
