"""Ordered schema migrations. Each runs once, in order, and the version is recorded.

Never edit a migration that has shipped: add a new one. DDL uses {pk} {key} {text} {real} {int} placeholders that
the store fills in for the database in use, so one definition serves SQLite and MySQL/MariaDB.
"""


def _index(s, sql):
    try:
        s.ddl(sql)
    except Exception as e:  # an index that already exists is fine (databases from before migrations existed)
        if not any(w in str(e).lower() for w in ("exist", "duplicate")):
            raise


def v1_baseline(s):
    s.ddl("create table if not exists history(point {key}, ts {real}, value {real})")
    _index(s, "create index h_idx on history(point, ts)")
    s.ddl("create table if not exists events(id {pk}, point {key}, level {int}, message {text}, ts {real}, "
          "cleared_ts {real}, acked_ts {real}, acked_by {key})")
    s.ddl("create table if not exists users(name {key} primary key, hash {key} not null, roles {text} not null, created {real})")
    s.ddl("create table if not exists audit(id {pk}, ts {real}, user {key}, action {key}, detail {text})")
    s.ddl("create table if not exists objects(kind {key}, name {key}, body {text} not null, primary key(kind, name))")


def v2_settings(s):
    s.ddl("create table if not exists settings(skey {key} primary key, svalue {text} not null)")


def v3_api_tokens(s):
    # only a hash of each token is stored: a leaked database does not leak working credentials
    s.ddl("create table if not exists api_tokens(id {pk}, name {key} not null unique, token_hash {key} not null unique, owner {key} not null, "
          "scope {key} not null, source {key}, created {real}, expires {real}, last_used {real}, disabled {int} not null default 0)")


def v4_history_quality(s):
    s.ddl("alter table history add column quality {int} not null default 0")  # 0 good, 1 uncertain, 2 bad (core.Quality)


def v5_event_held(s):
    s.ddl("alter table events add column held {key}")  # shelved or suppressed while still active; null when announced


def v6_history_text(s):
    s.ddl("alter table history add column svalue {text}")  # the value of an alphanumeric point; value is null on those rows


def v7_user_totp(s):
    s.ddl("alter table users add column totp {text}")  # base32 secret of an enabled second factor; null when off


def v8_user_disabled(s):
    s.ddl("alter table users add column disabled {int} not null default 0")  # 1: the account cannot sign in and its tokens stop working


MIGRATIONS = [(1, "baseline", v1_baseline), (2, "settings", v2_settings), (3, "api tokens", v3_api_tokens),
              (4, "history quality", v4_history_quality), (5, "event held", v5_event_held), (6, "history text", v6_history_text), (7, "user totp", v7_user_totp),
              (8, "user disabled", v8_user_disabled)]
