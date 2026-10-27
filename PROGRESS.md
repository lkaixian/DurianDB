# Progress

How far DurianDB has come, what has actually been verified, and what is next. Updated when work lands, not
when it is planned. `TECHNICAL_DEBT.md` records what is still wrong; `docs/12-error-handling.md` records how
failures reach an operator. This file records what exists.

Numbers below were measured from the tree on 2026-10-27, not estimated.

## Where it stands

| Measure | Value |
|---|---|
| Commits | 99 at the time of writing; every fix and feature is its own commit |
| Python modules in `durian/` | 25 |
| Lines of Python in `durian/` | 5,338 |
| Test suites in `tests/` | 40 (`python tests/run_all.py`, all passing) |
| Lines of test code | 3,440 |
| Console: JS, CSS, HTML in `web/` | 2,037 lines across 6 files |
| Browser checks in `tools/` | 11 scripts (manual, not in CI) |
| Data source types | 6 — virtual, MQTT over TLS, Modbus (TCP/RTU/ASCII), HTTP JSON, SQL (SQLite), HTTP ingest push |
| Alarm detectors | 12 — HIGH_LIMIT, LOW_LIMIT, RATE_OF_CHANGE, NO_UPDATE, NO_CHANGE, CHANGE, STATE, RANGE, SMOOTHNESS, CUSUM, ANOMALY, TEXT |
| Event handlers and publishers | webhook, email, Slack, set-point, scheduled report; MQTT publisher, HTTP sender |
| Open debt entries | 36 (3 correctness, 8 untrue claims, 11 architecture, 6 dependencies, 8 untested paths) |
| Research notes | 12 in `docs/research/` plus the clean-room policy and the failure model |

## History by day

Every point is the state of the tree at the last commit of that UTC day, recomputed from git.

| Date | Commits | Modules | Test suites | `durian/` lines | Test lines | What landed |
|---|---|---|---|---|---|---|
| 2026-10-19 | 34 | 9 | 4 | 1,241 | 274 | clean-room policy, research on the vendor's public behaviour, core model, HTTP shell, SQLite history, first console, MQTT over TLS source, Modbus client and simulator |
| 2026-10-20 | 60 | 24 | 27 | 5,048 | 2,702 | authentication and roles, dashboards and the designer, quality and source timestamps, ISA-18.2 alarm states, shelving and suppression, KPIs, safe control, calculated points, reports, mimics, Modbus/HTTP/SQL drivers, ingest and machine tokens, MySQL backend, maintenance |
| 2026-10-21 to 10-26 | 0 | 24 | 27 | 5,048 | 2,702 | no work; the tree was unchanged |
| 2026-10-27 | 20 | 25 | 40 | 5,338 | 3,440 | HTTPS and binding beyond localhost, account disable, operator notes, CI workflow, failure model, debt register, rate-of-change defect fix, dependencies in CI, progress tracker and history page, every gap in the failure model closed with a test, engine lock split, MQTT source-down reason, Modbus finiteness, Rust anomaly detector, Python ANOMALY detector |

## Built and verified

"Verified" means a test or check fails if it stops working. Everything in this table is covered by
`tests/run_all.py` unless the Where column says otherwise.

| Area | What is built | Where |
|---|---|---|
| Data in | Virtual, MQTT TLS, Modbus TCP/RTU/ASCII, HTTP JSON poll, read-only SQL, HTTP push with scoped tokens | `tests/test_modbus*.py`, `test_pull_sources.py`, `test_ingest.py` |
| Data model | Numeric and text points, engineering range, units, decimals, binary/multistate labels, folders, tags, descriptions | `tests/test_app.py`, `test_text_points.py` |
| Quality and time | GOOD / UNCERTAIN / BAD per value, source timestamps, future-clock and stale rules, out-of-order drop | `tests/test_quality.py` |
| Alarms | 12 detectors with on-delay, off-delay and deadband; ISA-18.2 states; shelving, suppression, out of service, maintenance windows; journal; KPIs; acknowledge-all | `tests/test_core.py`, `test_holds.py`, `test_maintenance.py`, `test_journal.py` |
| Control | Permissioned writes, engineering limits, max step, two-step confirm, reason required, full audit with old and new value | `tests/test_safe_control.py` |
| Sources health | Source-down alarm, heartbeat, poll-duration overrun alarm, per-point rejection text | `tests/test_source_down.py` |
| Calculation | Safe expression language over other points; calc source | `tests/test_calc.py` |
| Reports | Per-point summary with CSV, 31-day window, watch-list and tag narrowing; daily scheduled email | `tests/test_reports.py`, `test_handlers.py` |
| Outbound | Webhook, email (STARTTLS/SSL), Slack, escalation, set-point handler, MQTT publisher, HTTP sender | `tests/test_handlers.py`, `test_http_sender.py` |
| HMI | Overview, point page with trend and logbook, history with comparison, dashboards (9 widget types), drag-and-drop designer, mimic editor, kiosk and public views | `tests/test_dashboards.py`; browser scripts for the rest |
| Accounts | scrypt passwords, sessions, role inheritance, lockout, TOTP second factor, password reset by one-time code, account disable, offline admin reset, scoped machine tokens | `tests/test_auth.py`, `test_mfa.py`, `test_disabled.py`, `test_reset.py` |
| Storage | SQLite or MySQL/MariaDB, versioned migrations, rollups (avg, min, max, last, count, first, sum, delta), retention, backups, buffered writes with a system alarm | `tests/test_store.py`, `test_series.py`, `test_maintenance.py` |
| Transport security | TLS 1.2+ HTTPS with HSTS and `Secure` cookies when a certificate is given; refuses plain HTTP off localhost | `tests/test_tls.py` |
| Integration | Node-RED flows, guide and end-to-end check; REST for values, sets, alarms | `tools/nodered_check.py` |
| CI | Python 3.12/3.13 on Ubuntu and Windows, plus `cargo test` and `clippy` for the Rust experiment | `.github/workflows/ci.yml` |

## Security work, in the order it was done

1. scrypt password hashing, HttpOnly + SameSite=Strict session cookies, `X-Requested-With` required on every
   write, strict Content-Security-Policy with no inline script or style.
2. Role lists per object type with `superadmin` always allowed, machine tokens scoped to points and actions.
3. Five failed logins lock a name; lockout and failed sign-ins are audited; made-up names are not, so the
   audit trail cannot be filled by a stranger.
4. MQTT always verifies the broker certificate and hostname; webhooks do not follow redirects; SMTP passwords
   live in a credential file, not in the config.
5. Session idle timeout of 30 minutes in the console, 8-hour absolute session life, and sign-out everywhere
   on a password reset or an account disable.
6. TOTP second factor with single-use codes, and a password reset that needs a live administrator.
7. TLS 1.2+ serving with HSTS and a `Secure` cookie flag, and a refusal to serve plain HTTP on any address
   other than localhost.

## Closed failure gaps (2026-10-27)

Every gap `docs/12-error-handling.md` §2 listed is closed, each with a test that fails if it reopens:

1. A failing tick loop raises a `(system)` URGENT alarm at the third consecutive failure — `test_tick_health.py`.
2. Handler and publisher health is per target, on `/api/outputs` and in the console — `test_output_health.py`.
3. Publishers that cannot start are held as faults with their reason — `test_output_health.py`.
4. The event retry buffer is capped, counts what it drops, and says so on recovery — `test_event_buffer.py`.
5. Config import is one transaction, and a config that cannot start is rolled back — `test_config_rollback.py`.
6. Envelope faults are sentences: non-object bodies, a corrupt schema version, a weak first-run password —
   `test_envelope.py`, `test_tls.py`.
7. Non-finite Modbus readings are rejected points — `test_modbus_finite.py`.
8. The source-down alarm names the MQTT reason — `test_mqtt_health.py`.
9. The engine no longer shares a lock with the database, so a slow tick does not stall the console —
   `test_engine_lock.py`.

Remaining open items are tracked in `TECHNICAL_DEBT.md` (36 entries).

## Next

Features the reference platform has and DurianDB does not (see `docs/research/11-mango-feature-analysis.md`),
plus the work the owner asked for on 2026-10-27:

| Item | State |
|---|---|
| Authentication beyond passwords | TOTP, reset, disable, tokens done; SSO/OIDC not started |
| Data extraction | Reports, CSV in and out, history CSV, SQL source, REST done; scheduled export bundles not started |
| Data modelling | Calculated points and engineering ranges done; units conversion and tag inheritance not started |
| AI and machine learning | Statistical outlier detection done (ANOMALY detector, in Python and in the Rust crate); EWMA baseline, forecasting and the AI dashboard not started |
| Extensive data input | 6 source types done; OPC UA, BACnet, DNP3, SNMP, Sparkplug B not started |
| Rust real-time core | Experimental port of the detector layer plus the ANOMALY detector and bounded Summary statistics, 28 tests; not wired into the server |
| Progress tracking | this file, and `docs/progress.html` |
