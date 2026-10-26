# DurianDB

An educational reference for how a SCADA / IoT data-acquisition platform fits together, studied from the public documentation and the black-box behavior of Mango (Radix IoT). DurianDB is an independent, original work. It contains no Mango code, assets or branding.

What it does: collects values from data sources (a virtual generator, MQTT over TLS, Modbus over TCP, RTU and ASCII, HTTP JSON, and read-only SQL), stores history as a time series, raises and clears alarms with detectors, lets operators acknowledge alarms and set values, sends events to webhooks, publishes values over MQTT TLS, and shows it all in a browser console, including dashboards you design and publish yourself. Python standard library plus `paho-mqtt` (and `cryptography` for the lab script only).

## Run

```
python -m durian.server            # http://127.0.0.1:8765
```

First run prints the generated `admin` password once. Set `DURIAN_ADMIN_PASSWORD` to choose it. Other settings: `DURIAN_DB`, `DURIAN_PORT`, `DURIAN_HOST`, `DURIAN_TLS_CERT` and `DURIAN_TLS_KEY`, `DURIAN_TICK` (seconds between polls), `DURIAN_MQTT_CREDS`.

**Reaching it from another machine.** It listens on `127.0.0.1` only. To open it on the network, give it a certificate: `python tools/make_cert.py plant-pc.local` writes `tools/cert.pem` and `cert.key`, then `DURIAN_HOST=0.0.0.0 DURIAN_TLS_CERT=tools/cert.pem DURIAN_TLS_KEY=tools/cert.key python -m durian.server` serves HTTPS only (TLS 1.2 or newer), sends HSTS, and marks the session cookie `Secure`. Without a certificate it refuses to start on any other address rather than put a password on the network in clear. A self-signed certificate has to be trusted by the browser, or replaced with one from a certificate authority.

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
| `durian/outputs.py` | Webhook, email and set-point handlers; MQTT publisher; HTTP sender (posts values to another DurianDB or any JSON endpoint) |
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

Passwords use scrypt. Sessions are HttpOnly, SameSite=Strict cookies, and every state-changing request needs an `X-Requested-With` header. Five failed logins lock a name for five minutes. The page uses a strict Content-Security-Policy with no inline script or style. MQTT clients always verify the server certificate and hostname. Webhooks do not follow redirects. Object permissions are role lists that default to `user`, with `superadmin` always allowed. The server speaks plain HTTP on 127.0.0.1 only; on any other address it serves HTTPS with a certificate you supply, and will not start without one.

## Node-RED and other systems

Node-RED can push values with HTTP POST or GET (`/api/ingest/<source>`, scoped ingest token), publish over MQTT with TLS, read values (`/api/value/<point>`) and set points with scoped tokens, and receive DurianDB's alarms through a webhook or MQTT. Example flows, a step-by-step guide and an end-to-end check against a real Node-RED are in `integrations/node-red/`. Create tokens under **Tokens**.

**Alarm states.** Each alarm reports an ISA-18.2 state in `/api/events`: `active_unacked`, `active_acked`, `rtn_unacked` (returned to normal, still waiting for an acknowledgement) or `closed`. HIGH_LIMIT and LOW_LIMIT detectors take an optional `deadband`: once raised, the value must pass the limit by that much to clear, so a value hovering on the limit raises one alarm instead of many.

**Shelving, suppression, out of service.** A user can shelve an alarm for 1 minute to 24 hours with a reason (`POST /api/alarms/<point>/shelve`, `unshelve`): it is still recorded and still needs an acknowledgement, but nothing is sent to webhooks or MQTT until the shelf ends or is lifted. A point can carry `suppress_when: {point, op, value}` to hold its alarms while another point meets a condition (suppression by design). A superadmin can take a point out of service (`out-of-service`, `in-service`, reason required): its detectors stop, open alarms return to normal and nothing is raised until it is back. Held alarms report `held` in `/api/events`. Every change is audited and shelves and out-of-service survive restarts. Suppression is re-checked every tick; the console has Shelve and Unshelve, out of service is API only for now.

**Alarm KPIs.** `GET /api/alarms/kpis?hours=24` (1 to 720) reports, for the points the caller can see: alarms raised and per hour, the peak in any 10 minutes, 10-minute periods with more than 10 alarms (EEMUA 191 flood), the most frequent points, chattering points (3 or more alarms of one point in 10 minutes), median time to acknowledge, alarms active now and active over 24 hours, held alarms, and shelved and out-of-service points. The Overview page shows them under the alarm list.

**Tag fields and safe control.** A point can carry `description`, `eu_low` and `eu_high` (engineering range), and `decimals`. A settable point can add `set_min` and `set_max` (default: the engineering range), `max_step` (largest change from the present value in one write) and `confirm: true`. Writes outside the limits or the step size are refused with 422 before anything is sent to the device. With `confirm`, the first write returns 202 and a one-use token valid for 30 seconds; repeating the request with the same value and `"confirm": token` performs it (the console asks with a dialog). Releasing a point needs neither. The audit trail records the old and new value. `require_reason: true` makes every write carry a `reason` (up to 200 characters), stored in the audit log with the old and new value.

**Two-factor sign-in and config audit.** Any user can turn on a TOTP second factor (RFC 6238, works with any authenticator app) on the Account page: `POST /api/mfa/setup`, then `/api/mfa/enable` with a code, and `/api/mfa/disable` with the password. Login then needs `code` as well; each code works once, and a wrong code counts toward the lockout. An admin clears a lost phone with `reset_mfa: true` on the user. The secret is stored in the database as base32, since the server must read it to check codes. Every config save is audited as `config.put kind/name (key: before -> after)`.

**Scheduled reports.** A handler of `type: report` takes the same `to`, `from`, `smtp` and `creds` as an email handler, plus `hour` (0-23, server local time), optional `hours` (window, default 24) and optional `watchlist`. Once a day from that hour it emails the points summary as a CSV attachment. A failed send is not retried until the next day.

**Source-down alarm and heartbeat.** Every source raises an URGENT alarm named `(source) <name>` when its link is lost or its poll fails for `down_after` seconds (default 10, so a blip or start-up does not alarm). A source can also set `heartbeat_seconds`: if no good value arrives from any of its points for that long, it counts as down, which catches an HTTP or MQTT sender that went quiet. While a source is down its points are marked `bad`. The alarm clears when data returns, follows the usual acknowledge flow, is visible to anyone who may read the source, and the Source health table shows the reason.

**Calculated points.** A source of type `calc` holds points with `calc: {expression}`, for example `p("boiler-temp") * 1.8 + 32`. The language is small and checked, not `eval`: numbers, `p("point name")`, `+ - * / %`, comparisons, `and`/`or`/`not`, `a if cond else b`, and `abs`, `min`, `max`, `round`, `sqrt`. Calculations run after every other source on each tick, so they see the same tick's inputs. A calculated point is `bad` when an input is missing or bad (and uncertain when an input is), and a failing calculation is named in the source's error. Calculated points log, alarm and chart like any other.

**Reports.** `GET /api/reports/summary` (`hours`, or `from` and `to` as epoch seconds, up to 31 days; narrow with `watchlist` or `tag=key:value`; `format=csv` downloads) returns one row per point you can see: samples, min, max, average, first and last good value, bad samples and alarms in the window. Bad samples are counted but never enter a figure. At most 200 points per report. The Reports page shows the table and the download.

**Mimics.** The dashboard widget `mimic` draws a process picture from a fixed set of shapes (`rect`, `line`, `circle`, `text`, `value`) on a canvas of up to 2000 by 2000, at most 80 shapes. A shape can follow a point with `point` and tone `rules` (normal, warn, alarm), which change its outline, dash and fill. Alarm and warn states are also written into the label and the hover text, and bad or uncertain values fade the shape, so colour is never the only signal. Author text is only ever inserted as text, never as markup.

**Sign-in diagnostics.** Logins, logouts, failed attempts and lockouts of real accounts are written to the audit trail (made-up user names are not, so they cannot fill it). The console logs itself out after 30 minutes without a click, key press or scroll; its constant refreshing does not count as activity. Sessions also end after 8 hours whatever happens.

**Off-delay.** Besides `duration` (the condition must hold that long before the alarm), HIGH_LIMIT, LOW_LIMIT and RATE_OF_CHANGE detectors take `off_delay`: the value must stay normal that many seconds before the alarm returns to normal, and a relapse restarts the wait. Together with `deadband` this keeps a noisy signal from raising a string of alarms.

**Form editors.** Config pages for sources, points, handlers, publishers and watch lists are labelled forms: the right fields for each source type, hints under each field, the server's message under the field it names, and detector rows you can add and remove, and (for administrators) a Take out of service / Return to service control on each point. The full settings stay one click away under "Advanced" as JSON, and the form and the JSON box stay in step. Visual direction is in `DESIGN.md`; `tools/browser_forms.mjs` checks the editors in a real browser.

**Test settings.** The source editor's Test settings button (administrators) tries the settings once without saving: an HTTP JSON source lists every numeric value it found with the path to use on a point, a SQL source shows its first rows, and a Modbus source checks that the device answers. `POST /api/test/source`.

**Console layout.** A left sidebar groups the pages (Monitor, Configure, System, signed-in account) and collapses to a row on a phone; the top bar always shows how many alarms still wait for acknowledgement, as text. Overview is a full-width alarm table with the live points as tiles below it.

**Point page.** Click a point name (overview, alarms, reports) to open `#point/<name>`: the current value with its quality, source and times, a trend with a window picker, the alarms recorded for that point (acknowledge, shelve and unshelve right there), the Set form with its limits and confirm step, the point's alarm limits and range, and an Edit settings link for administrators. `GET /api/points/<name>` and `GET /api/events?point=<name>` feed it.

**Personal watch lists.** On the overview, "Save view as my watch list" keeps the points shown as a list only its owner can see (`PUT/DELETE /api/mylists/<name>`, listed in `/api/watchlists` with `mine: true`). Shared watch lists stay an administrator's Config item. `tools/browser_mylists.mjs` checks it in a browser.

**Mimic editor.** A mimic widget in the dashboard designer has a shape editor: a row per shape with its geometry, label and point, nudge buttons and add/remove, kept in step with the JSON box and the live preview. `tools/browser_mimic.mjs` checks it.

**Config lists.** Each Config tab opens as a dense table (sources with their live state, points with live value and quality, handlers, publishers, watch lists), with a filter box once there are more than five rows. A row opens its editor, which has a way back to the list. Source health is now part of the source list.

**Mango-inspired features** (studied from the public documentation, see `docs/research/11-mango-feature-analysis.md`):
- Sortable list and report tables (click a heading, again to reverse; numbers sort as numbers).
- Binary and multistate points: `states: {"0": "Stopped", "1": "Running"}` shows labels instead of numbers, and a settable point gets a select instead of a number box.
- More alarm conditions: `STATE` (alarm while in listed states), `RANGE` (outside or inside a range), `CHANGE` (an informational event on every change) `NO_CHANGE` (value stuck for N seconds while updates keep arriving), `SMOOTHNESS` (scatter over the last N samples above a limit) and `CUSUM` (a small steady drift from a target, summed beyond a slack). `TEXT` alarms on alphanumeric points. A point with `datatype: "text"` (virtual, HTTP in or MQTT sources; on MQTT the payload is the bare text, a JSON string or `{"value": "..."}`) holds a short string, is logged on change into `history.svalue`, and has no numeric rollups or trend. Dashboard value and table widgets show it, and tone rules on it use `==` or `!=` with a text value. A gauge cannot show text.
- Interval logging (`logging: INTERVAL`, `log_interval`, `log_mode` instant, avg, min or max) and the rollups first, sum and delta (last minus first of each bucket, for counters and meters).
- Event handlers beyond the webhook: `email` (SMTP with none, STARTTLS or SSL; the password lives in a credentials file, not in the settings; optional "also when back to normal") and `setpoint` (write a value when an alarm is raised, and a value or a release when it returns to normal). A set-point handler obeys the target's limits and step size and is audited as `handler <name>`, but skips the confirm step. Every handler can be limited with `scope` (points, tags, sources).
- Poll diagnostics: the source list shows how long the last poll took, and a poll slower than its period raises an URGENT `(source) <name>` alarm until polls fit again.
- Points as a spreadsheet: Export CSV and Import CSV on the Points list (`GET /api/export/points`, `POST /api/import/points`). One row per point with the common settings in columns and the rest as JSON in `extra`; an import is checked row by row, shows every problem with its line, and writes nothing until you apply it.
- Alarm journal: the Alarms page lists every alarm you may see, newest first, filtered by state, level, time and a text search, paged 50 at a time, with a CSV download (`GET /api/events/search`).
- History can compare: pick up to five more points under "Compare with" to draw them on one chart (each line has its own dash pattern and a legend).
- Point folders: a point's `folder` (for example `Plant/Boiler`) groups the overview tiles under headings, shows in the points list and CSV, and `?folder=` (or the Folder box on the overview) selects a folder and everything below it.
- Acknowledge all: a button above the alarm tables acknowledges every unacknowledged alarm shown after one confirmation (`POST /api/events/ack-many`, up to 200 ids, only alarms you may see). An optional "Alarm sound" toggle in the top bar beeps when the unacknowledged count goes up; it is off until someone turns it on.
- Maintenance windows (config kind `maintenance`): once, weekly (may cross midnight) or switched on by hand, for chosen points, tags or sources. During a window alarms are held, acknowledged at once, or not recorded at all. Ending a window announces alarms that are still active.

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

**Slack, escalation and query lists.** A handler of `type: slack` posts each alarm as `{text}` to an incoming-webhook URL (`on_clear` also posts the return to normal). An email handler with `escalate_after` (minutes) and `escalate_to` mails that second list once if the alarm is still active and unacknowledged, and audits it as `alarm.escalate`. Escalation is checked once a minute. A watch list can be a query: `name_like` (pattern with `*` and `?`) and/or `text_like` (text in the description), combined with `tags` when given.

**Disabling an account.** On the Users page, Disable signs the account out at once, stops its API tokens and refuses its next sign-in (with the same message as a wrong password, so the console does not confirm which accounts exist). The roles and password are kept; Enable brings it back. An administrator cannot disable their own account. Both changes are audited.

**Password reset.** An admin presses Reset password on the Users page (`POST /api/users/<name>/reset`) and gives the user a one-time code that works for an hour. The user enters it with a new password under Forgot your password? on the sign-in page (`POST /api/reset`), which signs them out everywhere and lifts a lockout. Wrong codes count toward the lockout. Codes are kept as hashes in memory, so a restart voids them. If nobody can sign in, `python -m durian.reset admin` sets a new random password straight in the database.

**Visual dashboard designer.** The designer is a live canvas. Drag a widget to move it, drag its corner to resize it, drag a type from the palette onto the canvas (or press it) to add one. A widget dropped onto another pushes it down. The side panel edits the selected widget's title and plain settings (point names autocomplete), and Position and size has exact fields. Arrow keys move the focused widget and Shift with arrows resizes it. The Code tab edits the whole dashboard as JSON; Apply or switching back to Visual loads it onto the canvas.
