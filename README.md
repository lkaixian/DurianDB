# DurianDB

An educational reference for how a SCADA / IoT data-acquisition platform fits together, studied from the public documentation and the black-box behavior of Mango (Radix IoT). DurianDB is an independent, original work. It contains no Mango code, assets or branding.

What it does: collects values from data sources (a virtual generator, MQTT over TLS, Modbus over TCP, RTU and ASCII, HTTP JSON, and read-only SQL), stores history as a time series, raises and clears alarms with detectors, lets operators acknowledge alarms and set values, sends events to webhooks, publishes values over MQTT TLS, and shows it all in a browser console, including dashboards you design and publish yourself. Python standard library plus `paho-mqtt` (and `cryptography` for the lab script only).

## Run

```
python -m durian.server            # http://127.0.0.1:8765
```

First run prints the generated `admin` password once. Set `DURIAN_ADMIN_PASSWORD` to choose it. Other settings: `DURIAN_DB`, `DURIAN_PORT`, `DURIAN_TICK` (seconds between polls), `DURIAN_MQTT_CREDS`.

MQTT lab (isolated TLS broker on port 18883, does not touch any broker already running):

```
python tools/mqtt_lab.py
"C:\Program Files\mosquitto\mosquitto.exe" -c lab/mqtt/mosquitto.conf
python tools/sim_publisher.py       # sends durian/mqtt-temp and durian/mqtt-flow
```

## Layout

| Path | Purpose |
|---|---|
| `durian/core.py` | Source, point, detectors (high, low, rate of change, no update), engine |
| `durian/config.py` | Source types (virtual, mqtt), validation, build engine from stored config |
| `durian/mqtt_source.py` | MQTT over TLS source and shared client factory |
| `durian/modbus.py`, `modbus_source.py` | Modbus client (TCP, RTU, ASCII, serial) and the polling source |
| `durian/modbus_sim.py` | Modbus slave for tests: `python -m durian.modbus_sim tcp 1502` |
| `durian/polled.py`, `http_source.py`, `sql_source.py` | Pull-source base, HTTP JSON source, SQLite source |
| `durian/outputs.py` | Webhook handler, MQTT publisher |
| `durian/store.py`, `migrations.py` | SQLite or MySQL/MariaDB: history, rollups, events, users, audit, tokens, config objects, versioned migrations |
| `durian/maintenance.py` | Retention, backups, config export and import, history CSV |
| `durian/ingest.py` | HTTP ingest source (values pushed by Node-RED and others) |
| `durian/auth.py` | scrypt passwords, sessions, role inheritance, login lockout |
| `durian/app.py` | The API as plain calls, with permission checks |
| `durian/server.py` | HTTP shell: cookies, CSRF header, security headers |
| `durian/dashboards.py`, `web/dashboards.js` | Dashboards: widget validators and renderers, designer, kiosk and public views |
| `web/` | Console: overview, dashboards, history, config editor, users, audit, account |
| `tests/` | `python tests/run_all.py` runs every suite |
| `tools/` | MQTT lab, sensor simulator, `browser_check.mjs` and `browser_modbus.mjs` (real browser), `e2e_check.py` (outputs) |
| `docs/` | Clean-room policy and dated research notes |
| `graphify-out/` | Knowledge graph of the research and code |

## Security notes

Passwords use scrypt. Sessions are HttpOnly, SameSite=Strict cookies, and every state-changing request needs an `X-Requested-With` header. Five failed logins lock a name for five minutes. The page uses a strict Content-Security-Policy with no inline script or style. MQTT clients always verify the server certificate and hostname. Webhooks do not follow redirects. Object permissions are role lists that default to `user`, with `superadmin` always allowed. The server speaks plain HTTP on 127.0.0.1 only. Put it behind HTTPS before exposing it anywhere else.

## Node-RED and other systems

Node-RED can push values with HTTP POST or GET (`/api/ingest/<source>`, scoped ingest token), publish over MQTT with TLS, read values (`/api/value/<point>`) and set points with scoped tokens, and receive DurianDB's alarms through a webhook or MQTT. Example flows, a step-by-step guide and an end-to-end check against a real Node-RED are in `integrations/node-red/`. Create tokens under **Tokens**.

**Alarm states.** Each alarm reports an ISA-18.2 state in `/api/events`: `active_unacked`, `active_acked`, `rtn_unacked` (returned to normal, still waiting for an acknowledgement) or `closed`. HIGH_LIMIT and LOW_LIMIT detectors take an optional `deadband`: once raised, the value must pass the limit by that much to clear, so a value hovering on the limit raises one alarm instead of many.

**Shelving, suppression, out of service.** A user can shelve an alarm for 1 minute to 24 hours with a reason (`POST /api/alarms/<point>/shelve`, `unshelve`): it is still recorded and still needs an acknowledgement, but nothing is sent to webhooks or MQTT until the shelf ends or is lifted. A point can carry `suppress_when: {point, op, value}` to hold its alarms while another point meets a condition (suppression by design). A superadmin can take a point out of service (`out-of-service`, `in-service`, reason required): its detectors stop, open alarms return to normal and nothing is raised until it is back. Held alarms report `held` in `/api/events`. Every change is audited and shelves and out-of-service survive restarts. Suppression is re-checked every tick; the console has Shelve and Unshelve, out of service is API only for now.

**Alarm KPIs.** `GET /api/alarms/kpis?hours=24` (1 to 720) reports, for the points the caller can see: alarms raised and per hour, the peak in any 10 minutes, 10-minute periods with more than 10 alarms (EEMUA 191 flood), the most frequent points, chattering points (3 or more alarms of one point in 10 minutes), median time to acknowledge, alarms active now and active over 24 hours, held alarms, and shelved and out-of-service points. The Overview page shows them under the alarm list.

**Tag fields and safe control.** A point can carry `description`, `eu_low` and `eu_high` (engineering range), and `decimals`. A settable point can add `set_min` and `set_max` (default: the engineering range), `max_step` (largest change from the present value in one write) and `confirm: true`. Writes outside the limits or the step size are refused with 422 before anything is sent to the device. With `confirm`, the first write returns 202 and a one-use token valid for 30 seconds; repeating the request with the same value and `"confirm": token` performs it (the console asks with a dialog). Releasing a point needs neither. The audit trail records the old and new value.

**Source-down alarm and heartbeat.** Every source raises an URGENT alarm named `(source) <name>` when its link is lost or its poll fails for `down_after` seconds (default 10, so a blip or start-up does not alarm). A source can also set `heartbeat_seconds`: if no good value arrives from any of its points for that long, it counts as down, which catches an HTTP or MQTT sender that went quiet. While a source is down its points are marked `bad`. The alarm clears when data returns, follows the usual acknowledge flow, is visible to anyone who may read the source, and the Source health table shows the reason.

**Data quality.** Every value carries the source's own timestamp and a quality: `good`, `uncertain` or `bad`. A source that is down or cannot fill a point marks it `bad`: the last value is held, no alarm is evaluated on it, and charts, CSV export and rollups skip it (the history table keeps the row with its quality). A device clock more than a minute ahead, or a sender that says `"quality": "uncertain"`, makes the value `uncertain`. Values older than the newest one already received for a point are dropped. A "no update" alarm counts from when DurianDB received a value, not from the device's clock. Senders may add `ts` and `quality` to ingest and MQTT payloads.

## Database

SQLite by default, MySQL or MariaDB with `DURIAN_DB=mysql://user:password@host:3306/database` (the whole test suite passes on both). Schema changes are versioned migrations that upgrade an existing database in place. Under **Database**: size and health, retention (global, and per point with `retention_days`), scheduled purge and backups (SQLite), configuration export and import with a preview, and CSV export and import of a point's history. If the database fails, samples wait in a buffer and a system alarm is raised.

## Dashboards

Open **Dashboards** in the console. The seeded `plant-overview` shows every widget type. Create your own with **New dashboard** (needs the `designer` or `superadmin` role): add widgets, set their place on the grid, edit each widget's settings, watch the live preview, save. A dashboard is plain JSON, so it can be copied between servers. Public means the role `anonymous` (superadmin only), and the data on it stays behind its own point permissions. To add a widget type, see the last section of `docs/research/09-dashboards.md`.

## Known limits (deliberate, see `ponytail:` comments and docs)

- Any config change rebuilds the engine. Open alarms are closed and detectors start over, so a still-breached point can raise again. Held set-values survive.
- One value per point per poll from MQTT (the latest), not every message.
- Drivers: Modbus (tested against a simulator and over a socket-backed serial URL, not a real port or device), HTTP JSON, SQLite. No OPC, BACnet, DNP3 or SNMP (see `docs/research/08`).
- Pulled values carry the engine's tick time, not the moment they were read, and a fast poll keeps only the latest value per tick.
- History is never purged, and there is no clustering or high availability.

## Status and research

See `docs/00-clean-room-policy.md` first. Notes: `docs/research/01` to `10` (10 is the SCADA baseline and gap analysis that sets the roadmap) (06 compares the enterprise and regular editions, 07 is Modbus, 08 is HTTP and SQL, 09 is dashboards and extension). Open questions: `docs/research/99-open-questions.md`.
