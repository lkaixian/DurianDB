# DurianDB: where we are, what is left, what comes next

Written 2026-10-27. Measured from the tree, not estimated. `PROGRESS.md` records what exists,
`TECHNICAL_DEBT.md` what is still wrong, `docs/12-error-handling.md` how a failure reaches an operator.
This file is the plan that reads the three together.

```
python tests/run_all.py     # all suites, SQLite in memory
python -m durian.server     # console on http://localhost:8080
```

## 1. What we have done

| Measure | Value |
|---|---|
| Commits on `main` | 127, at the time of writing |
| Python modules in `durian/` | 32, 7,125 lines |
| Test suites in `tests/` | 53, 5,292 lines, all passing |
| Console | 2,106 lines of JS/CSS/HTML in `web/` |
| Data source types | 6: virtual, MQTT over TLS, Modbus (TCP/RTU/ASCII), HTTP JSON, SQL, HTTP ingest |
| Alarm detectors | 12, with on/off delay, deadband and ISA-18.2 states |
| Open debt entries | 25 (see `TECHNICAL_DEBT.md`) |

Built and covered by tests: authentication (scrypt, sessions, roles, lockout, TOTP, reset, disable, scoped
machine tokens), safe control (limits, step, two-step confirm, audit), alarms and shelving, maintenance
windows, calculated points, reports and scheduled email, dashboards and the designer, mimics, history with
rollups, retention and backups, SQLite and MySQL storage, TLS serving, and CI on four Python jobs plus the
Rust crate.

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

## 2. What we need to do

Open defects, in the order that hurts. From `TECHNICAL_DEBT.md`; none of these is a feature.

| Item | Why it matters | Size |
|---|---|---|
| A9: all state in-process (sessions, lockout, TOTP replay guard, confirm tokens, escalation, SSO state) | blocks any second server process; this is the "no HA" limit | large |
| U1–U8: untested paths | real COM ports, a real broker, MariaDB in CI, the browser checks, the reset CLI | medium each |
| D3–D5, D8: documented claims that are not true, and `graphify-out/` in git | research notes drift from the tree; generated files in every diff | small each |
| E2–E6: dependency claims and the 3.12/3.13-only CI | local is 3.14, the version most used is untested; one Windows 3.12 job is red intermittently, cause not yet known | small |
| A1/A10: one 1,000-line `App` and an 854-line `app.js` | every new route widens the same function | medium |

## 3. What we plan to do

The owner's feature list, kept in the order it can actually be finished. Each item is one commit or a small
run of commits, pushed as it lands.

| Item | Next concrete step | Why in this order |
|---|---|---|
| Sparkplug B source | wire `durian/sparkplug_codec.py` into a `Source` over the existing MQTT client: birth/death, sequence gaps as sentences, one point per node/device/metric | the hard part (the codec) is done and tested; this is the driver around it |
| Forecasting, on the chart | draw the forecast band on the point's own trend page, not only on a dashboard widget | the endpoint and widget exist; this is the same data in the place an operator looks first |
| Data extraction: scheduled bundles | an HTTP download for bundles the scheduler wrote, beside the one built on request | packaging is done; this is retrieval |
| Architecture: A9 shared state | move sessions, lockout and confirms into the store so two processes could share them | the largest remaining blocker to HA; needs its own design pass |
| Extensive input: OPC UA, BACnet, DNP3, SNMP | one driver at a time behind the existing `Source` interface, each with a simulator to test against | each is a new dependency and a new simulator |
| Hardening continues | finish the open defects in §2 as they are found, each with its test | the failure model says a new failure gets a test before it gets a fix |
| Rust real-time core | the crate stays an experiment until the Python detector layer stops being fast enough | not wired into the server; keep it zero-dependency and tested in CI |

## 4. How work lands

- One feature or fix per commit, pushed immediately; the message says what failed and what an operator sees.
- Every non-trivial change leaves one runnable check in `tests/`; the full suite must pass before the push.
- Python is done here; `rust/` changes go through a subagent and its own commit.
- `docs/12-error-handling.md` §3 decides where a new failure belongs before any code is written.
- Never read vendor jars or source; vendor folders stay out of git; `.claude/` is never committed.
