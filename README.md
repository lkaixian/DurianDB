# DurianDB

An educational reference for how a SCADA / IoT data-acquisition platform fits together, studied from the public documentation and the black-box behavior of Mango (Radix IoT). DurianDB is an independent, original work. It contains no Mango code, assets or branding.

What it does: collects values from data sources (a configurable virtual generator, and MQTT over TLS), stores history as a time series, raises and clears alarms with detectors, lets operators acknowledge alarms and set values, sends events to webhooks, publishes values over MQTT TLS, and shows it all in a browser console. Python standard library plus `paho-mqtt` (and `cryptography` for the lab script only).

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
| `durian/outputs.py` | Webhook handler, MQTT publisher |
| `durian/store.py` | SQLite: history, rollups, events, users, audit, config objects |
| `durian/auth.py` | scrypt passwords, sessions, role inheritance, login lockout |
| `durian/app.py` | The API as plain calls, with permission checks |
| `durian/server.py` | HTTP shell: cookies, CSRF header, security headers |
| `web/` | Console: overview, history, config editor, users, audit, account |
| `tests/` | `python tests/test_core.py` (also `test_series`, `test_auth`, `test_app`) |
| `tools/` | MQTT lab, simulator, `browser_check.mjs` (real browser), `e2e_check.py` (outputs) |
| `docs/` | Clean-room policy and dated research notes |
| `graphify-out/` | Knowledge graph of the research and code |

## Security notes

Passwords use scrypt. Sessions are HttpOnly, SameSite=Strict cookies, and every state-changing request needs an `X-Requested-With` header. Five failed logins lock a name for five minutes. The page uses a strict Content-Security-Policy with no inline script or style. MQTT clients always verify the server certificate and hostname. Webhooks do not follow redirects. Object permissions are role lists that default to `user`, with `superadmin` always allowed. The server speaks plain HTTP on 127.0.0.1 only. Put it behind HTTPS before exposing it anywhere else.

## Known limits (deliberate, see `ponytail:` comments and docs)

- Any config change rebuilds the engine. Open alarms are closed and detectors start over, so a still-breached point can raise again. Held set-values survive.
- One value per point per poll from MQTT (the latest), not every message.
- The virtual source is a teaching generator, not a protocol driver. Modbus, OPC and the rest are not implemented.
- History is never purged, and there is no clustering or high availability.

## Status and research

See `docs/00-clean-room-policy.md` first. Notes: `docs/research/01` to `05`. Open questions: `docs/research/99-open-questions.md`.
