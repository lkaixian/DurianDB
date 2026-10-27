"""Shared test helper: a fresh store, on SQLite in memory by default or on MySQL/MariaDB when DURIAN_TEST_STORE_URL is set
(that database is emptied first, so point it at a throwaway one)."""
import os

from durian.store import Store

TABLES = ("history", "events", "users", "audit", "objects", "settings", "schema_version", "sessions", "api_tokens",
          "auth_failures", "auth_last_step", "confirm_tokens", "tick_lease", "escalations", "roles")


def new_store():
    url = os.environ.get("DURIAN_TEST_STORE_URL")
    if not url:
        return Store()
    probe = Store(url)
    for t in TABLES:
        probe.run(f"drop table if exists {t}")
    probe.close()
    return Store(url)
