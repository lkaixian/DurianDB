# Technical debt

Written 2026-10-27 from a read-only survey of the tree. Every entry names where it is and what it costs. It is
maintained: delete an entry when it is fixed, add one when a shortcut is taken on purpose.

Severity: **high** = can lose data, silently degrade, or block a release. **medium** = costs time, hides bugs, or
makes a claim untrue. **low** = cosmetic or documentation.

## 1. Correctness and silent failure

No open entries. C12, C13 and C14 were closed on 2026-10-27; what they cost is now covered by
`test_calc_health.py` and `test_auth_state.py`.

## 1a. Fixed on 2026-10-27, kept for the record

C1–C11 and A6 were closed with a test each, so their entries were removed above. The tick loop, handler and
publisher delivery, the event buffer cap, config-import rollback, config-save rollback, the request envelope,
the schema version, Modbus finiteness, the MQTT source-down reason and the engine lock are covered by
`test_tick_health.py`, `test_output_health.py`, `test_event_buffer.py`, `test_config_rollback.py`,
`test_envelope.py`, `test_modbus_finite.py`, `test_mqtt_health.py` and `test_engine_lock.py`.

Later the same day, and from the same survey: C12 (a failed calculation now raises one source-down alarm,
`test_calc_health.py`), C13 and C14 (lockout state survives a flood of made-up names, expired sessions are
swept, `test_auth_state.py`), A7 and A8 (backups and source tests run on a worker, `test_jobs.py`), A11
(one ISO formatter and one CSV writer, byte-identical output, `test_iso_csv.py`), the unbounded in-memory
buffers (`test_bounded_state.py`), and D1, D2, D6, D7 with the README rewrite. E1 was already true when it
was written: `requirements.txt` exists.

## 2. Documented claims that are not true

No open entries. D3, D4, D5 and D8 were closed on 2026-10-27: the research notes now describe what is built, and
`graphify-out/` is untracked and ignored.

## 3. Architecture

| # | Debt | Where | Severity | Cost |
|---|---|---|---|---|
| A1 | `App` is a 1025-line class with one `handle()` if-chain over `(method, a, b, c)` | `durian/app.py` | medium | Every new route widens the same function; no route table |
| A2 | "unknown point" 404 written inline 7 times while a `_404()` helper exists | `durian/app.py` | low | Two ways to say the same thing |
| A3 | `AuthError` → `ApiError` conversion repeated in 4 places, then to HTTP in a 5th | `durian/app.py`, `durian/server.py` | low | A new auth exception must be handled in each |
| A4 | Two permission idioms: `can()` and inline `"superadmin" in ident["roles"]` (7 sites) | `durian/auth.py`, `durian/app.py` | medium | Role logic drifts between routes |
| A5 | `if not isinstance(body, dict)` guard repeated 6 times (and missed in one place, C7) | `durian/app.py` | low | The one that was missed proved the pattern is fragile |
| A9 | All state is in-process: sessions, lockout counters, reset codes, TOTP replay guard, confirm tokens, escalation set | `durian/auth.py`, `durian/app.py` | high | Two server processes would double-tick, double-purge and double-escalate, and accept a replayed TOTP code. This is why "no HA" is in the known limits |
| A10 | `web/app.js` is 854 lines in one file | `web/app.js` | low | One page's change risks another's |
| A12 | SQLite and MySQL diverge through hand-written branches, including `cast(ts/? as int)` vs `floor(ts/?)` | `durian/store.py` | medium | The MySQL path is the least tested code in the repo |

## 4. Dependencies and environment

| # | Debt | Severity | Cost |
|---|---|---|---|
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
