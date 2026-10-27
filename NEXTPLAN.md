# DurianDB: where we are, what is left, what comes next

Written 2026-10-27. Measured from the tree, not estimated. `PROGRESS.md` records what exists,
`TECHNICAL_DEBT.md` what is still wrong, `docs/12-error-handling.md` how a failure reaches an operator.
This file is the plan that reads the three together.

```
python tests/run_all.py     # all suites, SQLite in memory
python -m durian.server     # console on http://127.0.0.1:8765
```

## 1. What we have done

| Measure | Value |
|---|---|
| Commits on `main` | 170, at the time of writing |
| Python modules in `durian/` | 36, 9,791 lines |
| Test suites in `tests/` | 62, 7,272 lines, all passing |
| Console | 1,964 lines of JS/CSS/HTML in `web/` |
| Data source types | 11: virtual, MQTT over TLS, Sparkplug B over MQTT, Modbus (TCP/RTU/ASCII), SNMP v2c, DNP3, BACnet/IP, HTTP JSON, SQL, HTTP ingest, calculated |
| Alarm detectors | 12, with on/off delay, deadband and ISA-18.2 states |
| Open debt entries | 12 (see `TECHNICAL_DEBT.md`) |

Built and covered by tests: authentication (scrypt, sessions, roles, lockout, TOTP, reset, disable, scoped
machine tokens), safe control (limits, step, two-step confirm, audit), alarms and shelving, maintenance
windows, calculated points, reports and scheduled email, dashboards and the designer, mimics, history with
rollups, retention and backups, SQLite and MySQL storage, TLS serving, and CI on four Python jobs plus the
Rust crate. The second pass added Sparkplug B and SNMP sources, retrieval of scheduled export bundles, the
forecast on a point's own trend page, and a fix so a point name holding a slash can be opened over HTTP.

### The failure work of 2026-10-27, gap by gap

`docs/12-error-handling.md` §2 listed eleven ways the system could fail without telling an operator.
All eleven are now closed, each with a test that fails if it reopens:

| # | Gap | Closed by | Test |
|---|---|---|---|
| 1 | A failing tick loop was invisible | three consecutive failures raise one URGENT `(system)` alarm naming the exception; a good tick clears it | `test_tick_health.py` |
| 2 | Handler delivery failures were invisible | per-target failed/last-error/last-ok, `/api/outputs`, a Delivery column | `test_output_health.py` |
| 3 | Publishers that never start were invisible | they are held as faults with their reason, on the same surface | `test_output_health.py` |
| 4 | Event retry could grow without bound | capped at 5,000 with a count, and one announcement of what the outage cost | `test_event_buffer.py` |
| 5 | The source-down alarm dropped the real reason | MQTT connect/disconnect reasons are carried into the alarm | `test_mqtt_health.py` |
| 6 | Config import had no rollback | one transaction around the whole apply | `test_config_rollback.py` |
| 7 | A config saved before it could start stayed saved | a failed reload is rolled back to the previous body, and says so | `test_config_rollback.py` |
| 8 | A non-object JSON body became a 500 | one envelope guard; login and reset too | `test_envelope.py` |
| 9 | A corrupt schema version replayed every migration | "no table" and "unreadable table" are now different answers | `test_envelope.py` |
| 10 | NaN from Modbus entered history | non-finite readings, including scaling overflow, are rejected points | `test_modbus_finite.py` |
| 11 | A weak first-run password crashed with a traceback | it exits with a sentence | `test_tls.py` (same path) |

One architecture item is also closed: the engine and the database no longer share one lock, so a slow tick
does not stall the console (`test_engine_lock.py`).

### The fourth pass, in one paragraph

Two more SCADA drivers, both read-only and stdlib-only, tested against in-process fake peers: DNP3 over TCP
(`durian/dnp3_source.py`) and BACnet/IP (`durian/bacnet_source.py`), alongside a GETNEXT walk in the SNMP
source. Sign-in state left process memory — sessions, lockout counters, the TOTP replay guard and write
confirmations now live in migration 9, so two server processes agree and a second process cannot accept a
code the first already accepted (`test_shared_state.py`). The repeated rules in the API got one home each and
`web/app.js` became one file per page. The MySQL path runs in CI on MariaDB 11.4, which immediately found a
real defect: a read inside `atomic()` committed the transaction and left a failed import behind. And the TLS
floor is proven by a real handshake rather than asserted.

## 2. What we need to do

Open defects, in the order that hurts. From `TECHNICAL_DEBT.md`; none of these is a feature.

| Item | Why it matters | Size |
|---|---|---|
| U6: the browser checks run in CI, but four of them only prove they ran, and `browser_dashboard`'s designer steps still use selectors the console no longer has | a green job does not yet mean every console flow is exercised | small |
| U1, U3, U8–U12: untested paths | a real COM port, real external email/Slack delivery, and every device protocol against real hardware | medium each |
| E4–E6: dependency claims and the 3.12/3.13-only CI | local is 3.14, the version most used is untested | small |

## 3. What we plan to do

The owner's feature list, kept in the order it can actually be finished. Each item is one commit or a small
run of commits, pushed as it lands.

| Item | Next concrete step | Why in this order |
|---|---|---|
| Hardening continues | finish the open defects in §2 as they are found, each with its test | the failure model says a new failure gets a test before it gets a fix |
| Console forms | the out-of-service and off-delay controls noted in the roadmap, and the 2FA and report-handler screens that only `node --check` covers (U7) | a driver an operator cannot configure is half a driver |
| Browser checks that assert | repair `browser_dashboard`'s designer section and make its four observational checks fail on a console error they did not provoke | the job should be able to go red for the right reason |
| Device protocols against hardware | point one driver at a real device on the lab network and record what it reads | every protocol so far is proven against an in-process fake |
| Rust real-time core | the crate stays an experiment until the Python detector layer stops being fast enough | not wired into the server; keep it zero-dependency and tested in CI |

Done in this pass: one statement path for both store backends (A12 closed: `_exec`, `_upsert` and `_int_div`
are shared, and each branch that still differs says why), a route authorization map suite
(`tests/test_route_map.py`), the 11 browser checks running headlessly in CI (`tools/browser_ci.mjs`, with
`browser_modbus` and `browser_mimic` repaired after the console changed under them), `core.sentence()` so every
caught exception reaches an operator as a sentence (`tests/test_error_sentences.py`), and four more SMOOTHNESS
tests in the Rust crate.

Done in the pass before: the OPC UA source (`durian/opcua_source.py`), the tick lease and the escalation guard
in the store (so a second process neither double-ticks nor double-escalates), a DNP3 console form, delivery
tests that reach a local receiver for Slack, the webhook and the HTTP sender, a source template for every
driver, and a one-sentence error for a failed delivery.

Done in the previous pass: the DNP3 source (`durian/dnp3_source.py`), the BACnet/IP source
(`durian/bacnet_source.py`), the SNMP GETNEXT walk, sign-in state in the store, one home per API rule, the
`web/app.js` split by page, MariaDB in CI, and a real TLS handshake test.

Done in the previous pass: the Sparkplug B source (`durian/sparkplug_source.py`), the forecast band on the
point's own trend page, retrieval of scheduled export bundles, tests for the offline admin reset, and
percent-decoded route names so a slash-named point can be opened.

## 4. How work lands

- One feature or fix per commit, pushed immediately; the message says what failed and what an operator sees.
- Every non-trivial change leaves one runnable check in `tests/`; the full suite must pass before the push.
- Python is done here; `rust/` changes go through a subagent and its own commit.
- `docs/12-error-handling.md` §3 decides where a new failure belongs before any code is written.
- Never read vendor jars or source; vendor folders stay out of git; `.claude/` is never committed.
