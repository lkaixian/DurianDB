# DurianDB

| Check | Status |
|---|---|
| Whole workflow (`ci.yml`, `main`) | [![CI](https://github.com/lkaixian/DurianDB/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/lkaixian/DurianDB/actions/workflows/ci.yml) |
| tests, Python 3.12 / Ubuntu | [![python 3.12 ubuntu](https://img.shields.io/github/actions/workflow/status/lkaixian/DurianDB/ci.yml?branch=main&job=tests%20on%203.12%20%2F%20ubuntu-latest&label=python%203.12%20ubuntu)](https://github.com/lkaixian/DurianDB/actions/workflows/ci.yml) |
| tests, Python 3.12 / Windows | [![python 3.12 windows](https://img.shields.io/github/actions/workflow/status/lkaixian/DurianDB/ci.yml?branch=main&job=tests%20on%203.12%20%2F%20windows-latest&label=python%203.12%20windows)](https://github.com/lkaixian/DurianDB/actions/workflows/ci.yml) |
| tests, Python 3.13 / Ubuntu | [![python 3.13 ubuntu](https://img.shields.io/github/actions/workflow/status/lkaixian/DurianDB/ci.yml?branch=main&job=tests%20on%203.13%20%2F%20ubuntu-latest&label=python%203.13%20ubuntu)](https://github.com/lkaixian/DurianDB/actions/workflows/ci.yml) |
| tests, Python 3.13 / Windows | [![python 3.13 windows](https://img.shields.io/github/actions/workflow/status/lkaixian/DurianDB/ci.yml?branch=main&job=tests%20on%203.13%20%2F%20windows-latest&label=python%203.13%20windows)](https://github.com/lkaixian/DurianDB/actions/workflows/ci.yml) |
| real-time core (experimental) | [![rust core](https://img.shields.io/github/actions/workflow/status/lkaixian/DurianDB/ci.yml?branch=main&job=real-time%20core%20%28experimental%29&label=rust%20core)](https://github.com/lkaixian/DurianDB/actions/workflows/ci.yml) |

DurianDB is an original, clean-room educational reference for a SCADA / IoT data-acquisition platform, written in
Python. It studies how such a platform fits together from public documentation and the black-box behaviour of
Mango (Radix IoT). It contains no Mango code, assets or branding.

It collects values from devices, stores them as a time series, evaluates alarms, lets operators acknowledge and
control points, forwards events, and serves a browser console with dashboards you design yourself.

## Run

```sh
python -m durian.server      # http://127.0.0.1:8765
python tests/run_all.py      # 47 suites, all passing
```

The first run prints a generated `admin` password once; `DURIAN_ADMIN_PASSWORD` chooses it instead. Other
settings: `DURIAN_DB`, `DURIAN_PORT`, `DURIAN_HOST`, `DURIAN_TLS_CERT` and `DURIAN_TLS_KEY`, `DURIAN_TICK`
(seconds between polls) and `DURIAN_MQTT_CREDS`.

`requirements.txt` lists the third-party packages. The core server is Python standard library plus
`paho-mqtt` (version 2 or newer) for the MQTT source and publisher. `pymysql` and `pyserial` are only needed
for MySQL/MariaDB and serial Modbus, and `cryptography` only for the lab tools. CI runs the suite on Python
3.12 and 3.13.

The console is served on `127.0.0.1` only. To reach it from another machine, give it a certificate:
`python tools/make_cert.py plant-pc.local` then start with `DURIAN_HOST=0.0.0.0 DURIAN_TLS_CERT=tools/cert.pem
DURIAN_TLS_KEY=tools/cert.key`. It then serves HTTPS only (TLS 1.2 or newer), sends HSTS and marks the session
cookie `Secure`. Without a certificate it refuses to start on any other address rather than put a password on
the network in clear.

## What it can do

- **Sources.** Virtual generator, MQTT over TLS, Modbus over TCP, RTU and ASCII, HTTP JSON, read-only SQL, and
  HTTP ingest push. Calculated points use a small checked expression language, not `eval`.
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
| `tools/` | Certificate maker, MQTT lab, sensor simulator, Modbus slave and manual browser checks |
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
  the research notes are `docs/research/01` to `10`, with open questions in `docs/research/99`.
