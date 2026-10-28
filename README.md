# DurianDB

[![tests (3.12–3.14)](https://img.shields.io/github/actions/workflow/status/lkaixian/DurianDB/ci-tests.yml?branch=main&label=tests%20%283.12%E2%80%933.14%29)](https://github.com/lkaixian/DurianDB/actions/workflows/ci-tests.yml)
[![browser checks](https://img.shields.io/github/actions/workflow/status/lkaixian/DurianDB/ci-browser.yml?branch=main&label=browser%20checks)](https://github.com/lkaixian/DurianDB/actions/workflows/ci-browser.yml)
[![MariaDB tests](https://img.shields.io/github/actions/workflow/status/lkaixian/DurianDB/ci-mysql.yml?branch=main&label=MariaDB%20tests)](https://github.com/lkaixian/DurianDB/actions/workflows/ci-mysql.yml)
[![rust core](https://img.shields.io/github/actions/workflow/status/lkaixian/DurianDB/ci-rust.yml?branch=main&label=rust%20core)](https://github.com/lkaixian/DurianDB/actions/workflows/ci-rust.yml)

![Feature stack](docs/feature-stack.svg)

Each badge covers one workflow, and each workflow holds exactly one job, all triggered on every push to `main`:

| Job | What it proves |
|---|---|
| `tests on 3.12 / ubuntu-latest` | the suite on the oldest Python CI supports, on Linux |
| `tests on 3.12 / windows-latest` | the same on Windows, where paths and line endings differ |
| `tests on 3.13 / ubuntu-latest` | the middle Python CI supports, on Linux |
| `tests on 3.13 / windows-latest` | the same on Windows |
| `tests on 3.14 / ubuntu-latest` | the newest Python CI supports, and the version development runs, on Linux |
| `tests on 3.14 / windows-latest` | the same on Windows |
| `tests on MariaDB` | the suite on the MySQL/MariaDB backend, against a MariaDB 11.4 service container |
| `browser checks (headless Chromium)` | all 12 console checks, each against a throwaway database and browser profile |
| `real-time core (experimental)` | the Rust detector crate's tests and clippy |

GitHub shows badges per workflow, not per job; since each job above has its own workflow file, each badge is that job's own result.

DurianDB is an original, clean-room educational reference for a SCADA / IoT data-acquisition platform, written in
Python. It studies how such a platform fits together from public documentation and the black-box behaviour of
Mango (Radix IoT). It contains no Mango code, assets or branding.

It collects values from devices, stores them as a time series, evaluates alarms, lets operators acknowledge and
control points, forwards events, and serves a browser console with dashboards you design yourself.

## Run

```sh
python -m durian.server      # http://127.0.0.1:8765
python tests/run_all.py      # 101 suites, all passing
```

To run the suite on the MySQL/MariaDB backend, start a throwaway MariaDB and point the two test URLs at it
(the suite drops every table in that database first, so never use a real one):

```sh
docker run -d --name durian-test-mariadb \
  -e MARIADB_DATABASE=durian_test -e MARIADB_USER=durian -e MARIADB_PASSWORD=durian \
  -e MARIADB_ROOT_PASSWORD=durian-root -p 127.0.0.1:3307:3306 mariadb:11.4
DURIAN_TEST_STORE_URL=mysql://durian:durian@127.0.0.1:3307/durian_test \
DURIAN_TEST_MYSQL_URL=mysql://durian:durian@127.0.0.1:3307/durian_test \
  python tests/run_all.py
docker rm -f durian-test-mariadb
```

Change `3307` if that port is taken. This check was run against MariaDB 11.4 on 2026-10-27 and passed; the
same URLs, with the container's port mapped to `3306`, run in CI.

The first run prints a generated `admin` password once; `DURIAN_ADMIN_PASSWORD` chooses it instead. Other
settings: `DURIAN_DB`, `DURIAN_PORT`, `DURIAN_HOST`, `DURIAN_TLS_CERT` and `DURIAN_TLS_KEY`, `DURIAN_TICK`
(seconds between polls) and `DURIAN_MQTT_CREDS`.

`requirements.txt` lists the third-party packages. The core server is Python standard library plus
`paho-mqtt` (version 2 or newer) for the MQTT source and publisher. `pymysql` and `pyserial` are only needed
for MySQL/MariaDB and serial Modbus, and `cryptography` for the lab tools and the TLS handshake test. CI runs
the suite on Python 3.12 and 3.13, and runs the MySQL/MariaDB path once on MariaDB 11.4.

The console is served on `127.0.0.1` only. To reach it from another machine, give it a certificate:
`python tools/make_cert.py plant-pc.local` then start with `DURIAN_HOST=0.0.0.0 DURIAN_TLS_CERT=tools/cert.pem
DURIAN_TLS_KEY=tools/cert.key`. It then serves HTTPS only (TLS 1.2 or newer), sends HSTS and marks the session
cookie `Secure`. Without a certificate it refuses to start on any other address rather than put a password on
the network in clear.

## Single-file build

Each release tag publishes an executable for Windows and Linux on the GitHub Releases page, with `SHA256SUMS` to check it
against. It needs no Python: rename it to `durian` (`durian.exe` on Windows), then `durian run` starts the console on
http://127.0.0.1:8765. The database, backups and spool sit beside the executable, or in `DURIAN_HOME` when that is set.
`durian supervise` is the watchdog; the files in `tools/service` start `python -m durian supervise`, so point them at the
executable for a service. To build it yourself, run `python tools/build_exe.py` after `python -m pip install pyinstaller==6.22.3`.

## What it can do

- **Sources.** Virtual generator, MQTT over TLS, Sparkplug B over MQTT, Modbus over TCP, RTU and ASCII, SNMP
  v2c (GET and a bounded GETNEXT walk), DNP3 over TCP, BACnet/IP, OPC UA (SecurityPolicy None), HTTP JSON,
  read-only SQL, and HTTP ingest
  push. The device protocols are read-only: nothing writes to a PLC. Calculated points use a small checked
  expression language, not `eval`.
- **History and quality.** SQLite by default, or MySQL/MariaDB. Time-series history, rollups, interval logging,
  `good`/`uncertain`/`bad` quality, CSV export and import, retention with scheduled purge, and backups.
- **Alarms.** High/low limits, rate of change, no update, no change, change, state, range, smoothness, CUSUM,
  anomaly and text detectors, with deadband and off-delay. ISA-18.2 states, acknowledge (single and many),
  shelving, suppression, out of service (console or API), maintenance windows, journal, KPIs and escalation.
- **Outputs.** Webhook, email, Slack, set-point and scheduled CSV report handlers; an MQTT publisher and an
  HTTP sender for another DurianDB or any JSON endpoint.
- **Console.** Overview and live points, a point page with trend and logbook, history with comparison, reports,
  the alarm journal, labelled config forms with a JSON view, users and roles, audit trail, account page with
  TOTP, personal watch lists, and a dashboard designer with kiosk and public views and mimic widgets.
- **Security.** scrypt passwords, HttpOnly `SameSite=Strict` sessions, an `X-Requested-With` header on every
  write, a strict Content-Security-Policy, login lockout, two-factor sign-in, scoped API tokens, and
  safe-control limits with confirmation and a reason for writes.
- **Integrations.** Node-RED examples and an end-to-end check live in `integrations/node-red/`; values can be
  pushed or pulled over HTTP, MQTT and webhooks.

## Where things live

| Path | Purpose |
|---|---|
| `durian/` | The server: sources, engine, history store, alarms, outputs, API and HTTP shell |
| `web/` | The browser console: overview, dashboards, history, config, users, audit and account |
| `tests/` | `python tests/run_all.py` runs every suite, each in its own process |
| `tools/` | Certificate maker, MQTT lab, sensor simulator, Modbus slave, and the browser checks CI runs |
| `integrations/node-red/` | Example flows and a step-by-step Node-RED guide |
| `docs/` | Clean-room policy, research notes, the failure model and the progress page |
| `rust/` | Experimental port of the detector core; not wired into the server |

## Documentation and limits

- [Progress history](https://github.com/lkaixian/DurianDB/blob/main/docs/progress.html) shows what was built
  each day and the measured size of the tree.
- `TECHNICAL_DEBT.md` lists every known shortcut, defect and untrue claim, with where it is and what it costs.
  `extra_notes.md` holds limits and operational notes that are not defects.
- `docs/12-error-handling.md` records how each failure reaches an operator, and the rule for a new one.
- `PROGRESS.md` records what is built, what is verified and how. `docs/00-clean-room-policy.md` comes first;
  the research notes are `docs/research/01` to `12`, with open questions in `docs/research/99`.
