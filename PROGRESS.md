# Progress

How far DurianDB has come, what has actually been verified, and what is next. Updated when work lands, not
when it is planned. `TECHNICAL_DEBT.md` records what is still wrong; `docs/12-error-handling.md` records how
failures reach an operator. This file records what exists.

Numbers below were measured from the tree on 2026-10-27, not estimated.

## Where it stands

| Measure | Value |
|---|---|
| Commits | 195 at the time of writing; every fix and feature is its own commit |
| Python modules in `durian/` | 37 |
| Lines of Python in `durian/` | 10,928 |
| Test suites in `tests/` | 65 (`python tests/run_all.py`, all passing) |
| Lines of test code | 8,048 |
| Console: JS, CSS, HTML in `web/` | 2,230 lines across 14 files |
| Browser checks in `tools/` | 11 scripts, all run in CI headlessly (`tools/browser_ci.mjs`) |
| Data source types | 12 — virtual, MQTT over TLS, Sparkplug B over MQTT, Modbus (TCP/RTU/ASCII), SNMP v2c (GET and GETNEXT walk), DNP3 over TCP, BACnet/IP, OPC UA, HTTP JSON, SQL (SQLite), HTTP ingest push, calculated |
| Alarm detectors | 12 — HIGH_LIMIT, LOW_LIMIT, RATE_OF_CHANGE, NO_UPDATE, NO_CHANGE, CHANGE, STATE, RANGE, SMOOTHNESS, CUSUM, ANOMALY, TEXT |
| Event handlers and publishers | webhook, email, Slack, set-point, scheduled report; MQTT publisher, HTTP sender |
| Open debt entries | 13 (0 correctness, 0 untrue claims, 1 architecture, 3 dependencies, 9 untested paths) |
| Research notes | 13 in `docs/research/` plus the clean-room policy and the failure model |

## History by day

Every point is the state of the tree at the last commit of that UTC day, recomputed from git.

| Date | Commits (cumulative) | Modules | Test suites | `durian/` lines | Test lines | What landed |
|---|---|---|---|---|---|---|
| 2026-10-19 | 19 | 9 | 4 | 1,241 | 274 | clean-room policy, research on the vendor's public behaviour, core model, HTTP shell, SQLite history, first console, MQTT over TLS source, Modbus client and simulator |
| 2026-10-20 | 79 | 24 | 27 | 5,048 | 2,702 | authentication and roles, dashboards and the designer, quality and source timestamps, ISA-18.2 alarm states, shelving and suppression, KPIs, safe control, calculated points, reports, mimics, Modbus/HTTP/SQL drivers, ingest and machine tokens, MySQL backend, maintenance |
| 2026-10-21 to 10-26 | 0 | 24 | 27 | 5,048 | 2,702 | no work; the tree was unchanged |
| 2026-10-27 | 195 | 37 | 65 | 10,928 | 8,048 | HTTPS and binding beyond localhost, account disable, operator notes, CI workflow, failure model, debt register, rate-of-change defect fix, dependencies in CI, progress tracker and history page, every gap in the failure model closed with a test, engine lock split, MQTT source-down reason, Modbus finiteness, Rust and Python ANOMALY detectors, unit conversion and tag inheritance, forecast and baseline endpoints, a prediction dashboard widget, export bundles, background jobs, SSO sign-in, a permission matrix, bounded in-memory state, the Sparkplug B codec, README rewrite with CI badges, a Sparkplug B source, an SNMP v2c source with a GETNEXT walk, a DNP3 source, a BACnet/IP source, retrieval of scheduled export bundles, the forecast on a point's own trend page, baseline drift, tests for the offline admin reset, percent-decoded route names, sign-in state in the store, one home per API rule, the `web/app.js` split by page, MariaDB in CI, a real TLS handshake test, corrections to the research notes, an OPC UA source, the tick lease and the escalation guard in the store, a DNP3 console form, delivery tests against a local receiver, a source template per driver, one statement path for both store backends, a route authorization map suite, the browser checks in CI, and an operator-facing sentence for every caught exception |

## Built and verified

"Verified" means a test or check fails if it stops working. Everything in this table is covered by
`tests/run_all.py` unless the Where column says otherwise.

| Area | What is built | Where |
|---|---|---|
| Data in | Virtual, MQTT TLS, Sparkplug B (births, deaths, aliases, sequence gaps), Modbus TCP/RTU/ASCII, SNMP v2c GET and a bounded GETNEXT walk, DNP3 over TCP (classes 0–3, flags as quality), BACnet/IP (Who-Is binding, ReadPropertyMultiple), OPC UA (SecurityPolicy None, one Read per poll), HTTP JSON poll, read-only SQL, HTTP push with scoped tokens | `tests/test_modbus*.py`, `test_pull_sources.py`, `test_ingest.py`, `test_sparkplug_source.py`, `test_snmp_source.py`, `test_dnp3_source.py`, `test_bacnet_source.py`, `test_opcua_source.py` |
| Data model | Numeric and text points, engineering range, units, decimals, binary/multistate labels, folders, tags, descriptions | `tests/test_app.py`, `test_text_points.py` |
| Quality and time | GOOD / UNCERTAIN / BAD per value, source timestamps, future-clock and stale rules, out-of-order drop | `tests/test_quality.py` |
| Alarms | 12 detectors with on-delay, off-delay and deadband; ISA-18.2 states; shelving, suppression, out of service, maintenance windows; journal; KPIs; acknowledge-all | `tests/test_core.py`, `test_holds.py`, `test_maintenance.py`, `test_journal.py` |
| Control | Permissioned writes, engineering limits, max step, two-step confirm, reason required, full audit with old and new value | `tests/test_safe_control.py` |
| Sources health | Source-down alarm, heartbeat, poll-duration overrun alarm, per-point rejection text | `tests/test_source_down.py` |
| Calculation | Safe expression language over other points; calc source | `tests/test_calc.py` |
| Reports | Per-point summary with CSV, 31-day window, watch-list and tag narrowing; daily scheduled email | `tests/test_reports.py`, `test_handlers.py` |
| Outbound | Webhook, email (STARTTLS/SSL), Slack, escalation, set-point handler, MQTT publisher, HTTP sender | `tests/test_handlers.py`, `test_http_sender.py` |
| HMI | Overview, point page with trend, forecast and logbook, history with comparison, dashboards (10 widget types), drag-and-drop designer, mimic editor, kiosk and public views | `tests/test_dashboards.py`, `test_prediction_widget.py`; browser scripts for the rest |
| Accounts | scrypt passwords, sessions, role inheritance, lockout, TOTP second factor, password reset by one-time code, account disable, offline admin reset, scoped machine tokens | `tests/test_auth.py`, `test_mfa.py`, `test_disabled.py`, `test_reset.py` |
| Data out | History CSV, reports, one-click and scheduled export bundles, and retrieval of the bundles the scheduler wrote | `tests/test_export.py`, `test_export_retrieve.py`, `test_reports.py` |
| Storage | SQLite or MySQL/MariaDB, versioned migrations, rollups (avg, min, max, last, count, first, sum, delta), retention, backups, buffered writes with a system alarm | `tests/test_store.py`, `test_series.py`, `test_maintenance.py` |
| Transport security | TLS 1.2+ HTTPS with HSTS and `Secure` cookies when a certificate is given; refuses plain HTTP off localhost | `tests/test_tls.py` |
| Integration | Node-RED flows, guide and end-to-end check; REST for values, sets, alarms | `tools/nodered_check.py` |
| API contract | One JSON error envelope, one role check, one unknown-point 404; a route-map suite fails if a route loses its access rule; every caught exception reaches an operator as a sentence | `tests/test_envelope.py`, `test_permissions.py`, `test_route_map.py`, `test_error_sentences.py` |
| CI | Python 3.12/3.13 on Ubuntu and Windows, MariaDB 11.4, the 11 browser checks headlessly in Chromium, and `cargo test` plus `clippy` for the Rust experiment | `.github/workflows/ci.yml` |

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

## Built in the second pass (2026-10-27)

Six tasks in parallel, each with its own test, merged one at a time:

| Area | What is built | Test |
|---|---|---|
| Data modelling | `display_unit` on a point with conversions for temperature, pressure, flow, length, mass and ratio; tags and unit inherited from folder then source, resolved in one place | `test_units.py` |
| AI/ML | EWMA baseline, robust-z score, least-squares forecast with a widening 95% band, `GET /api/points/<n>/forecast` and `/baseline`, and a `prediction` dashboard widget that draws the band and says when no honest forecast exists | `test_forecast.py`, `test_prediction_widget.py` |
| Data extraction | one ZIP of history CSV per point, the event journal, the config and a manifest; downloadable or written once a day with `export_keep` pruning | `test_export.py` |
| Background work | a `Jobs` worker so a database copy or a live source test no longer runs on the request thread | `test_jobs.py` |
| Failure model | a failed calculation raises one `(source)` alarm; an internal-error counter and one alarm per repeatedly failing route; every in-memory buffer capped with a count | `test_calc_health.py`, `test_internal_errors.py`, `test_bounded_state.py` |
| Authentication | SSO sign-in through OpenID Connect: authorization code with PKCE, RS256 verified from the provider JWKS with only the standard library, same sessions and roles as a password | `test_oidc.py`, `test_auth_state.py` |
| Security | a permission matrix that parses the dispatcher and fails when a route is added without an opinion; it found one route that confirmed an administrative area to any signed-in caller | `test_permissions.py` |
| Input | the Sparkplug B payload codec: protobuf wire format, 30 datatypes, unknown fields skipped, every refusal a sentence; no broker or source yet | `test_sparkplug_codec.py` |
| Structure | one ISO formatter and one CSV writer, output byte-identical to before | `test_iso_csv.py` |

Remaining open items are tracked in `TECHNICAL_DEBT.md` (14 entries).

## Built in the fourth pass (2026-10-27)

Five tasks in parallel, each in its own worktree with its own test, merged one at a time with the full suite
run after every merge:

| Area | What is built | Test |
|---|---|---|
| Input | DNP3 over TCP, read-only: CRC-16/DNP link frames, transport segmentation, application fragments, class 0 plus class 1/2/3 event polls, flags as quality, unknown groups skipped | `test_dnp3_source.py` |
| Input | BACnet/IP, read-only: Who-Is/I-Am binding, ReadPropertyMultiple with ReadProperty as the fallback, segmented ComplexACK reassembly, one erroring object goes bad without failing the poll | `test_bacnet_source.py` |
| Input | the SNMP source gains a bounded GETNEXT walk, as an alternative to a fixed OID list | `test_snmp_source.py` |
| AI/ML | baseline drift between two windows: a two-sample robust-z of the medians, verdict steady/drifting/unknown, on the point page | `test_drift.py` |
| Security | sessions, lockout counters, the TOTP replay guard and write confirmations live in migration 9, so two processes agree; the database failing falls back to per-process state with one warning sentence | `test_shared_state.py` |
| Security | the TLS floor is proven by a real handshake with a generated certificate: version floor, HSTS, and a `Secure` + `HttpOnly` session cookie over HTTPS | `test_tls.py` |
| Structure | one home for the unknown-point 404, the `AuthError` translation, the role check and the JSON-body rule; `web/app.js` split into one script per page | `test_envelope.py`, `test_permissions.py` |
| Storage | a read inside `atomic()` no longer commits on MySQL; the MySQL path runs in CI on MariaDB 11.4, which is how the defect was found | `test_config_rollback.py`, CI `mysql` job |

## Next

Features the reference platform has and DurianDB does not (see `docs/research/11-mango-feature-analysis.md`),
plus the work the owner asked for on 2026-10-27:

| Item | State |
|---|---|
| Authentication beyond passwords | TOTP, reset, disable, tokens and SSO/OIDC (authorization code with PKCE, RS256 verified without a dependency) done |
| Data extraction | Reports, CSV in and out, history CSV, SQL source, REST, one-click and scheduled export bundles done |
| Data modelling | Calculated points, engineering ranges, unit conversion and tag inheritance done |
| AI and machine learning | ANOMALY detector, EWMA baseline, robust-z score, short-horizon forecast and the prediction widget done; no learned model, by choice |
| Extensive data input | 12 source types done, including DNP3, BACnet/IP and OPC UA (all read-only); the Sparkplug B codec and source are written and tested |
| Rust real-time core | Experimental port of the detector layer plus the ANOMALY detector and bounded Summary statistics, 32 tests; not wired into the server |
| Progress tracking | this file, and `docs/progress.html` |
