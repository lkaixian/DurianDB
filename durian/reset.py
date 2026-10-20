"""Set a new password for a user straight in the database, for when nobody can sign in. Run: python -m durian.reset admin

Prints a new random password. Also turns off the user's two-factor sign-in. Needs access to the database, which is the point.
"""
import os
import secrets
import sys
from pathlib import Path

from .auth import hash_password
from .store import Store


def main(name):
    store = Store(os.environ.get("DURIAN_DB", str(Path(__file__).resolve().parent.parent / "durian.db")))
    if not store.user(name):
        sys.exit(f"no user named {name}")
    pw = secrets.token_urlsafe(12)
    store.set_hash(name, hash_password(pw))
    store.run("update users set totp=null where name=?", (name,))
    store.audit("system", "password.reset", f"{name}, from the command line")
    print(f"New password for {name}: {pw}
Restart the server if the account was locked out.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "admin")
