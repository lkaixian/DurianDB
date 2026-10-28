# Technical debt

What is still wrong, as of 2026-10-28 (HEAD after `ea129d2`). Fixed entries are deleted, not kept; `git log` has the
history. **22 open**, none high severity, no correctness defect. Add an entry (with a `file:line` and a `ponytail:`
comment in the code) when a shortcut is taken on purpose; delete it in the commit that fixes it.

Severity: **high** can lose data or block a release (none open). **medium** hides bugs or makes a claim untrue.
**low** is cosmetic or small.

## Open items

| Id | Area | What is wrong | Where | Sev | Next step |
|---|---|---|---|---|---|
| U1 | Modbus TCP/RTU/ASCII | Simulator only; RTU only over a `socket://` URL, never a real COM port or device | `durian/modbus_source.py` | medium | Run `tools/hw_probe.py` against a device |
| U3 | Email, Slack, escalation | Fake SMTP server and a local HTTP receiver; never a real external server | `durian/outputs.py` | medium | Send to a real mailbox and Slack workspace |
| U9 | Sparkplug B source | Decoded payloads fed directly; no real broker or edge node | `durian/sparkplug_source.py` | medium | Probe a real edge node |
| U10 | DNP3 source | In-process fake outstation only | `durian/dnp3_source.py` | medium | Probe a real outstation |
| U11 | BACnet/IP source | In-process fake device on UDP only | `durian/bacnet_source.py` | medium | Probe a real device |
| U12 | OPC UA source | Fake server only; only SecurityPolicy None exists | `durian/opcua_source.py` | medium | Probe a real server |
| U13 | eGauge, Haystack, POP3 | Fake servers only. Only eGauge `inst` XML and the Haystack `read` op were checked against public docs; POP3 `starttls` and Haystack auth beyond Bearer never ran | `pop3_source.py:120`, `haystack_source.py:27`, `egauge_source.py:5` | medium | Run against a real meter, server and mailbox |
| U14 | LDAP sign-in | Fake directory on plain `ldap://` localhost; `ldaps://` never ran | `durian/ldap.py:106` | medium | Bind against a real directory over TLS |
| W1 | POP3 | `security` defaults to `ssl` (was plaintext) and a saved `none` on a non-loopback host fails revalidation, so an old config must be edited. The ReDoS guard refuses more than 2 open-ended repeats, so common `Temp:\s*([-+]?\d+(?:\.\d+)?)` is refused though the 2000-character cap would keep it fast (not timed) | `pop3_source.py:21`, `:37`, `:56-59`, `:116` | medium | Time the refused patterns; relax the guard or document bounded rewrites |
| W2 | TREND_LIMIT | Sample window is memory-only, so every restart or reload relearns it and cannot alarm until it refills. UNCERTAIN values enter the fit (BAD ones do not). The 4-standard-error bar (0 of 300 noise seeds alarmed) also makes a shallow real trend alarm late or never | `core.py:371`, `:402`, `:525` | medium | Tune on a real plant series; persist or warm the window |
| W3 | Forecast band | Robust interval is a constant width at every horizon, so it understates uncertainty far ahead; backtest coverage is for one-step folds | `backtest.py:72`, `app.py:581` | medium | Widen with horizon; backtest multi-step |
| W5 | LDAP timing | `test_ldap_auth.py` swaps the dummy hash for `not-a-hash`, so equalised sign-in time is never measured. A directory outage answers 502 where everything else answers 401 (documented ceiling) | `auth.py:92`, `:271-276`, `tests/test_ldap_auth.py:70` | medium | Measure local, LDAP and unknown-name sign-in time with the real hash |
| W6 | MySQL/MariaDB | Never run there: the SQL console's byte caps and read-only path, the `performance_schema`/`sys` additions to `SECRET`, atomic migration v11, partial-backup cleanup. Only SQLite ran them | `store.py:481`, `:519-522`, `migrations.py:77` | medium | Extend the MariaDB CI job to these tests |
| V1 | Windows exe | Unsigned, so SmartScreen warns on first run. `SHA256SUMS` proves the file matches the build, not who built it | `tools/build_exe.py:40`, `release.yml` | medium | Authenticode certificate in the release job |
| V3 | Service install | Windows installer registers a startup task running `durian supervise`, not a Services-panel entry; neither it nor the systemd unit was ever run on a machine | `tools/service/install-windows-service.ps1`, `durian.service` | medium | Run both on a clean host |
| V4 | Supervisor | SIGTERM path (stop child, then exit) untested end to end: the handler only sets an event and tests drive `run()` with their own | `supervisor.py:166-168` | medium | Send a real signal in a subprocess test |
| V5 | Spool | History only. Events and alarm state the database refuses are not spooled; bounded at 50 MB, oldest dropped (counted in `spool.dropped`) | `spool.py:19`, `app.py:523` | medium | Spool events too, or document the loss |
| V6 | Health | `/api/health` `ok` ignores the spool: rows waiting or dropped show in the `spool` block but do not turn `ok` false | `app.py:955-959` | medium | Fold spool rows/dropped into `ok` |
| U8 | SNMP source | In-process fake agent only | `durian/snmp_source.py` | low | Probe a real device |
| W4 | Model retraining | Runs once a day at `models_hour` (default 02:00); the stored profile serves only a request for the trained 14-day window under 8 days old, any other `days` recomputes | `maintenance.py:24`, `:205`, `models.py:10`, `app.py:755` | low | Accept; revisit if recompute cost shows |
| W7 | Config keys | Dashboards accept unknown keys (no `dashboard` kind in `KNOWN_KEYS`); the guard is per kind, not per source type | `config.py:49`, `app.py:1519` | low | Add a dashboard key list |
| V2 | Executable size | `requirements.txt` mixes runtime and dev dependencies, so `cryptography` (dev tools; pymysql imports it optionally) may be bundled; not measured | `requirements.txt:7`, `tools/build_exe.py:17` | low | Split runtime and dev requirements; measure |

## Deliberate, not debt

Recorded so nobody "fixes" these by accident.

| Choice | Reason |
|---|---|
| A config change rebuilds the engine (open alarms close, detectors restart) | Simplest correct reload |
| One value per point per MQTT poll; pulled values carry tick time | Bounded work per tick |
| No process or script event handlers | They would run arbitrary code on the server |
| No OPC UA encryption, Browse or subscriptions; SNMP v2c only; DNP3 and BACnet read-only; Sparkplug B source read-only | No source writes to a device |
| OPC DA declined | Needs Windows COM/DCOM, not stdlib |
| Plain HTTP only on localhost | Anything else needs a certificate |
| Reset codes, pending TOTP secrets and OIDC sign-ins live in memory | They must die on restart |
| Line-protocol ingest ignores tags and has no `precision` (nanoseconds only) | `ingest.py:104`, `:120` |
| "Test this source" skips the known-key guard | A legacy config with a dead key still tests; it is refused on save and import (`testsource.py:35`) |
