# Technical debt

Written 2026-10-27 from a read-only survey of the tree. Every entry names where it is and what it costs. It is
maintained: delete an entry when it is fixed, add one when a shortcut is taken on purpose.

Severity: **high** = can lose data, silently degrade, or block a release. **medium** = costs time, hides bugs, or
makes a claim untrue. **low** = cosmetic or documentation.

## 1. Correctness and silent failure

| # | Debt | Where | Severity | Cost |
|---|---|---|---|---|
| C1 | A failing tick loop is logged and the loop continues; the console keeps showing the last values | `durian/server.py` `run_ticks` | high | The whole data plane can stop with no visible sign. Fix is planned in `docs/12-error-handling.md` §2.1 |
| C2 | Handler and publisher failures are a counter nothing displays | `durian/outputs.py` (`failed`), no API field | high | A webhook, email or Slack route can be dead for weeks |
| C3 | A publisher whose credentials fail to load is dropped with a log line | `durian/outputs.py` `__init__` | high | Values silently stop being published |
| C4 | Failed event inserts are retried with no cap (history has `PENDING_MAX`) | `durian/app.py` event path | high | Unbounded memory growth during a database outage |
| C5 | An `AuthError` from `bootstrap` reaches the top of `main()` as a traceback | `durian/server.py` `main` | medium | An operator sees a stack trace instead of a sentence |
| C6 | `Store.version()` returns 0 on any exception | `durian/store.py` | medium | A corrupt `schema_version` makes `migrate()` replay every migration |
| C7 | A non-object JSON body reaches `.get` and becomes a 500 | `durian/app.py` `handle` | medium | The caller's mistake is reported as a server fault |
| C8 | Config import writes and deletes without a transaction | `durian/maintenance.py` replace mode | high | A failure halfway leaves half a configuration |
| C9 | Config is saved before the reload that activates it | `durian/app.py` config path | medium | A reload failure returns 500 but the change is stored |
| C10 | Modbus `decode` output is never checked for finiteness | `durian/modbus_source.py` | medium | NaN/inf reaches history and comparisons, which are then always false |
| C11 | The source-down alarm says "not connected" and drops the real reason | `durian/core.py` `_watch` | medium | A down alarm is less useful than the source table |
| C12 | A broken calc expression sets points BAD but raises no source-down alarm | `durian/calc.py` | medium | The fault is only a string in the source list |
| C13 | `Auth.failures` overflow clears the whole dict | `durian/auth.py` | low | Drops live lockout state along with made-up names |
| C14 | `Auth.sessions` keeps expired entries until they are next looked up | `durian/auth.py` | low | Memory grows with distinct expired tokens until restart |

## 2. Documented claims that are not true

| # | Claim | Reality | Severity |
|---|---|---|---|
| D1 | README says "the whole test suite passes on both" (SQLite and MariaDB) | CI runs SQLite only; MariaDB is opt-in and unverified in CI | medium |
| D2 | README points at "`ponytail:` comments" in the plural | Exactly one exists (`durian/mqtt_source.py:92`) | low |
| D3 | `docs/research/11` lists SMOOTHNESS/CUSUM as "Not built" and HTTP sender/Slack publishers as "Not built" | All are built | medium |
| D4 | `docs/research/09` lists the drag-and-drop designer as "Not built" | It is built (`web/dashboards.js`) | medium |
| D5 | `docs/research/10` presents its gap table as current | Many rows are done since 2026-10-20 | medium |
| D6 | README says browser checks exist | No browser script runs in CI; only `node --check` | medium |
| D7 | Out-of-service is described as "API only for now" in one place and as a console control elsewhere | The console control exists | low |
| D8 | `graphify-out/` (148 generated files) is tracked in git | Generated output in VCS: noise in every diff that regenerates it | low |

## 3. Architecture

| # | Debt | Where | Severity | Cost |
|---|---|---|---|---|
| A1 | `App` is a 1025-line class with one `handle()` if-chain over `(method, a, b, c)` | `durian/app.py` | medium | Every new route widens the same function; no route table |
| A2 | "unknown point" 404 written inline 7 times while a `_404()` helper exists | `durian/app.py` | low | Two ways to say the same thing |
| A3 | `AuthError` → `ApiError` conversion repeated in 4 places, then to HTTP in a 5th | `durian/app.py`, `durian/server.py` | low | A new auth exception must be handled in each |
| A4 | Two permission idioms: `can()` and inline `"superadmin" in ident["roles"]` (7 sites) | `durian/auth.py`, `durian/app.py` | medium | Role logic drifts between routes |
| A5 | `if not isinstance(body, dict)` guard repeated 6 times (and missed in one place, C7) | `durian/app.py` | low | The one that was missed proved the pattern is fragile |
| A6 | One `RLock` held across the whole tick and `reload()`, shared with request-path writes | `durian/store.py`, `durian/app.py` | high | A slow tick serialises API requests; the console stalls with the engine |
| A7 | Backup runs synchronously on the request thread | `durian/app.py` → `durian/maintenance.py` | medium | An HTTP request can block for the whole copy |
| A8 | `POST /api/test/source` opens live connections on the request thread | `durian/testsource.py` | low | A hanging device hangs a console request |
| A9 | All state is in-process: sessions, lockout counters, reset codes, TOTP replay guard, confirm tokens, escalation set | `durian/auth.py`, `durian/app.py` | high | Two server processes would double-tick, double-purge and double-escalate, and accept a replayed TOTP code. This is why "no HA" is in the known limits |
| A10 | `web/app.js` is 788 lines in one file | `web/app.js` | low | One page's change risks another's |
| A11 | Two near-identical ISO-time lambdas and four CSV-writer setups | `durian/app.py`, `durian/maintenance.py`, `durian/pointcsv.py` | low | Small, easy to unify |
| A12 | SQLite and MySQL diverge through hand-written branches, including `cast(ts/? as int)` vs `floor(ts/?)` | `durian/store.py` | medium | The MySQL path is the least tested code in the repo |

## 4. Dependencies and environment

| # | Debt | Severity | Cost |
|---|---|---|---|
| E1 | No `requirements.txt` or `pyproject.toml`; CI pip-installs by hand | medium | No pinned versions, no recorded dependency set |
| E2 | `paho-mqtt` must be ≥ 2.0 (`CallbackAPIVersion.VERSION2`) and nothing says so | medium | An old install fails at runtime, not at install |
| E3 | `pymysql` is a hidden runtime dependency for MySQL: unlisted, not in CI | medium | The MySQL path cannot fail in CI, only in production |
| E4 | `pyserial` is an optional lazy import, not installed in CI | low | Serial Modbus tests cannot run there |
| E5 | CI tests Python 3.12 and 3.13; local development is 3.14 | low | The version actually used most is untested |
| E6 | `tools/make_cert.py` and `tools/mqtt_lab.py` need `cryptography`, which CI installs but no test uses | low | Wasted CI time, unclear requirement |

## 5. Untested paths

| # | Path | How it is tested now | Severity |
|---|---|---|---|
| U1 | Modbus TCP/RTU/ASCII | Simulator, and RTU only over a `socket://` URL — never a real COM port or device | medium |
| U2 | MQTT source and publisher | A lab broker started by hand; no test file covers the source | medium |
| U3 | Email, Slack, escalation delivery | A fake SMTP server; Slack only validates config | medium |
| U4 | HTTPS serving | `tests/test_tls.py` checks the cookie flag and the refusal paths, not a real handshake | low |
| U5 | MariaDB / MySQL | Opt-in, absent from CI | medium |
| U6 | Every browser check (`tools/browser_*.mjs`, 12 scripts) | Manual, headless Edge on port 9222 | medium |
| U7 | Newer console work (2FA screen, report handler form) | `node --check` and unit tests only | low |
| U8 | `durian/reset.py` (the offline admin reset CLI) | No test file | low |

## 6. Deliberate, not debt

These are choices, recorded so nobody "fixes" them by accident:

- A config change rebuilds the engine, so open alarms close and detectors restart.
- One value per point per MQTT poll (the latest), not every message (`durian/mqtt_source.py:92`).
- Pulled values carry the tick time, not the read time.
- No process or script event handlers: they would run arbitrary code on the server.
- No OPC UA, BACnet, DNP3 or SNMP drivers.
- Plain HTTP on localhost; anything else needs a certificate.
- Reset codes and TOTP pending secrets live in memory, so a restart voids them.

## 7. How this list is kept

When a shortcut is taken on purpose, it gets a `ponytail:` comment naming the ceiling and the upgrade path, and
an entry here. When an entry is fixed, the entry is deleted in the same commit. A debt entry without a
`file:line` is not an entry: it is a complaint.
