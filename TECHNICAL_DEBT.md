# Technical debt

Written 2026-10-27 from a read-only survey of the tree. Every entry names where it is and what it costs. It is
maintained: delete an entry when it is fixed, add one when a shortcut is taken on purpose.

Severity: **high** = can lose data, silently degrade, or block a release. **medium** = costs time, hides bugs, or
makes a claim untrue. **low** = cosmetic or documentation.

## 1. Correctness and silent failure

| # | Defect | Where | Severity |
|---|---|---|---|

No open entries. Deliberate leniency: the "test this source" button (`durian/testsource.py:35`) still validates
without the known-key guard, so a legacy config with a dead key tests fine (the key is refused on save and import).

C12, C13 and C14 were closed on 2026-10-27; what they cost is now covered by
`test_calc_health.py` and `test_auth_state.py`.

## 1a. Fixed on 2026-10-27, kept for the record

C1–C11 and A6 were closed with a test each, so their entries were removed above. The tick loop, handler and
publisher delivery, the event buffer cap, config-import rollback, config-save rollback, the request envelope,
the schema version, Modbus finiteness, the MQTT source-down reason and the engine lock are covered by
`test_tick_health.py`, `test_output_health.py`, `test_event_buffer.py`, `test_config_rollback.py`,
`test_envelope.py`, `test_modbus_finite.py`, `test_mqtt_health.py` and `test_engine_lock.py`.

Later the same day, and from the same survey: C12 (a failed calculation now raises one source-down alarm,
`test_calc_health.py`), C13 and C14 (lockout state survives a flood of made-up names, expired sessions are
swept, `test_auth_state.py`), A7 and A8 (backups and source tests run on a worker, `test_jobs.py`), A11
(one ISO formatter and one CSV writer, byte-identical output, `test_iso_csv.py`), the unbounded in-memory
buffers (`test_bounded_state.py`), and D1, D2, D6, D7 with the README rewrite. E1 was already true when it
was written: `requirements.txt` exists.

In the same pass, A2–A5 and A10: the unknown-point 404, the `AuthError` translation, the role check and the
JSON-body rule each became one function every route calls (`test_envelope.py`, `test_permissions.py`), and
`web/app.js` was split into one script per page. A9's security half moved into the store (`test_shared_state.py`).
A fifth pass closed the rest of A13 (a DNP3 console form) and A9's scheduling: the tick lease and the
escalation guard are in the store too, so a second process neither double-ticks nor double-escalates. The
delivery tests narrowed U3 (Slack, the webhook and the HTTP sender now reach a local receiver instead of only
being validated) and found that a failed delivery showed the operator an exception class name, which is now a
sentence (`durian/outputs.py`).

A sixth pass closed A12: `durian/store.py` now builds every statement once. One `_exec` path serves single and
batched statements on either driver, one `_upsert` sits behind settings, config objects and lockouts, and one
`_int_div` spells the integer-second buckets rollups use. A branch survives only where the databases genuinely
differ — their table catalogs, `DELETE ... LIMIT`, conflict clauses MySQL has no `where` for, sizes and
descriptions — and each says why. `test_store.py` checks the shared path, the bucketing on uneven edges and a
failed statement on every backend. The same pass put the 11 browser checks in CI (`browser_ci.mjs`, which also
repaired `browser_modbus` and `browser_mimic`), added `tests/test_route_map.py` so a route cannot lose its
access rule, and replaced the last raw exception text an operator could see with `core.sentence()`
(`tests/test_error_sentences.py`).

## 2. Documented claims that are not true

No open entries. D3, D4, D5 and D8 were closed on 2026-10-27: the research notes now describe what is built, and
`graphify-out/` is untracked and ignored.

## 3. Architecture

No open entries. A9 was closed on 2026-10-28 as deliberate, not debt: what stays per process
(password-reset codes, pending TOTP secrets, OIDC pending sign-ins) is exactly what must die on a
restart — sharing them in the store would extend the lifetime of a code an operator voided by
rebooting. It is now recorded in §6 instead.

## 4. Dependencies and environment

No open entries. E4, E5 and E6 were closed on 2026-10-28, each proven before it was claimed: E4 and E6
were stale — `pyserial>=3.5` and `cryptography>=42` have been in `requirements.txt` since 83aaf6a and
15e01ef, every CI job installs from that file, and the serial suites genuinely run there (with the
import shadowed out, `test_tls.py` exits with one sentence and a pip hint, which is the right answer
for a declared dependency). E5: the CI matrix now includes 3.14, and the full suite (66) passes on
3.14.4 locally before the push.

## 5. Untested paths

| # | Path | How it is tested now | Severity |
|---|---|---|---|
| U1 | Modbus TCP/RTU/ASCII | Simulator, and RTU only over a `socket://` URL — never a real COM port or device | medium |
| U3 | Email, Slack, escalation delivery | A fake SMTP server, and a local HTTP receiver for Slack, the webhook and the HTTP sender; never a real external server | medium |
| U8 | SNMP source | An in-process fake agent answers crafted datagrams; never a real device | low |
| U9 | Sparkplug B source | Decoded payloads are fed directly; never a real broker or a real edge node | medium |
| U10 | DNP3 source | An in-process fake outstation on a socket; never a real outstation | medium |
| U11 | BACnet/IP source | An in-process fake device on a UDP socket; never a real device | medium |
| U12 | OPC UA source | An in-process fake server; never a real server, and only SecurityPolicy None is implemented | medium |
| U13 | eGauge, Haystack and POP3 sources (`durian/egauge_source.py:79`, `durian/haystack_source.py:37`, `durian/pop3_source.py:81`) | Fake servers only. eGauge parses the documented `inst` XML and Haystack the `read` op, the only parts checked against public docs; POP3 `starttls`/`ssl` (`pop3_source.py:85-88`) and Haystack authentication beyond a Bearer token were never run | medium |
| U14 | LDAP sign-in (`durian/ldap.py:92`) | A fake directory over plain `ldap://` on localhost; `ldaps://` (`ldap.py:100`) never ran | medium |

### 5a. Left open by the 2026-10-28 work

Seven entries, each a cost or an unrun path the M1-M7 and hardening commits knowingly left. Same severity scale.

| # | Defect | Where | Severity |
|---|---|---|---|
| W1 | POP3 breaking changes. `security` now defaults to `ssl` (it used to be plaintext), and a saved `none` on a non-loopback host fails revalidation, so an old config must be edited before it loads. The pattern guard is a conservative ReDoS filter: more than `MAX_UNBOUNDED = 2` open-ended repeats is refused, so the common `Temp:\s*([-+]?\d+(?:\.\d+)?)` (three: `\s*`, `\d+`, `\d+`) is refused although the 2000-character match cap would keep it fast (not timed). The operator must rewrite it with bounded repeats such as `\d{1,6}` | `durian/pop3_source.py:21` (caps), `:37-48` (`_unsafe`), `:56-59` (security rule), `:116` (default) | medium |
| W2 | TREND_LIMIT keeps its sample window in memory only, so every restart or config reload (which rebuilds the engine) relearns it and cannot alarm until the window refills. BAD quality is excluded by `Point.update` before any detector runs, not by the caller; an UNCERTAIN value is not excluded and enters the fit. The significance bar was raised from 3 to 4 standard errors to stop noise alarms (0 of 300 noise seeds), which also makes a genuinely shallow trend alarm later or not at all | `durian/core.py:365` (memory-only note), `:370` (window), `:401` (4x bar), `:524-531` (BAD returns before detectors), `:548` (UNCERTAIN reaches them) | medium |
| W3 | The robust forecast interval is a constant width (1.96 x 1.4826 x MAD of the fit residuals) at every horizon, so it understates the uncertainty far ahead of the data. The coverage figure in the backtest is for one-step-ahead folds, not for the far end of a long horizon | `durian/backtest.py:72-83` (`robust`), `durian/app.py:555-559` (`forecast_view`) | medium |
| W4 | Model retraining is hour-granular: the schedule runs once a day at `models_hour` (default 02:00 local) and the stored profile is only used by the seasonal routes when the request asks for the same window it was trained on, 14 days (`WINDOW_DAYS`), and it is under 8 days old; any other `days` recomputes from history | `durian/maintenance.py:24` (default), `:204-207` (hour gate), `durian/models.py:10`, `durian/app.py:706-708` | low |
| W5 | LDAP timing equalisation is untested: `test_ldap_auth.py` replaces the dummy hash with the string `not-a-hash` (so no scrypt work is spent), and no test measures sign-in time for a local, an LDAP and an unknown name. A directory outage still answers 502 where everything else answers 401, so an outsider can tell an outage from a wrong password; that difference is a documented ceiling | `durian/auth.py:92` (dummy), `:269-274` (ceiling and 502), `tests/test_ldap_auth.py:70` | medium |
| W6 | Never run on MySQL/MariaDB: the SQL console's byte caps and read-only path, the `performance_schema` and `sys` additions to `SECRET`, the atomic migration v11 (`s.atomic()` around the delete and re-insert) and the partial-backup cleanup. Only SQLite ran them | `durian/store.py:477-478` (`SECRET`), `:514-515` (console), `durian/migrations.py:77-90` (v11) | medium |
| W7 | Dashboards accept unknown keys: `KNOWN_KEYS` has no `dashboard` kind and the validator list exempts it, so a typo in a dashboard is stored and ignored. The guard is per kind, not per source type: a source of one type still accepts another type's setting (`port` on a virtual source) | `durian/config.py:47-64` (`KNOWN_KEYS`), `durian/app.py:1470` (dashboard exemption) | low |

### 5b. Left open by the safe-mode, spool, supervisor and executable work

Seven entries, same severity scale.

| # | Defect | Where | Severity |
|---|---|---|---|
| V1 | The Windows executable is unsigned, so SmartScreen warns on first run. The release workflow publishes `SHA256SUMS`, which proves the file matches the build, not who built it | `tools/build_exe.py:40-42` (digest only), `.github/workflows/release.yml` | medium |
| V2 | `cryptography` is bundled into the executable (about 14.6 MB total) because `requirements.txt` mixes runtime and dev dependencies (it is there for `tools/make_cert.py`, `tools/mqtt_lab.py` and `tests/test_tls.py`); the server does not need it. A split into runtime and dev files would shrink the build | `requirements.txt:7`, `tools/build_exe.py:18-19` | low |
| V3 | The Windows installer registers a startup scheduled task running `python -m durian supervise`, not a Services-panel entry (`sc.exe create` needs a service-aware binary), and it was never run on a machine. The systemd unit was not run either | `tools/service/install-windows-service.ps1:3-4`, `tools/service/durian.service` | medium |
| V4 | The supervisor's SIGTERM path (stop the child, then exit) is untested end to end: the handler only sets an event, and the tests drive `run()` with their own event rather than a real signal | `durian/supervisor.py:149-152` | medium |
| V5 | The spool covers history only. Events and alarm state the database refuses are not spooled, so a database outage can still lose them. It is bounded at 50 MB and drops the oldest segment (counted in `/api/health` `spool.dropped`) | `durian/spool.py:19` (`max_bytes`), `durian/app.py:503-510` (`_spool_pending`) | medium |
| V6 | `/api/health` `ok` ignores the spool: rows waiting or dropped appear in the `spool` block but do not turn `ok` false, so a monitor watching only `ok` sees a healthy server while data is being held on disk or lost. | `durian/app.py:935,939-940` | medium |
| V7 | The Linux build and the release workflow are unverified: only the Windows executable was built and smoke-tested (locally), and `release.yml` (tag `v*` publishes both binaries and `SHA256SUMS`) has not run on GitHub, so the matrix, the upload and the checksum step are untested until the first CI run | `.github/workflows/release.yml:22` (matrix), `tools/smoke_exe.py` | medium |

U6 and U7 were closed on 2026-10-28: the twelve browser checks all run in CI, every one of them now
exits 1 on a problem it did not provoke (each of the four that used to only prove it ran lists its
own intentional 4xx requests and fails on anything else; `browser_dashboard`'s designer steps were
rewritten against the current `#pv`/`#df` markup), and `browser_account.mjs` drives the 2FA screen
and the report-handler form end to end, wrong code and refused save included. The injection proof
ran against every repaired file before it landed.

## 6. Deliberate, not debt

These are choices, recorded so nobody "fixes" them by accident:

- A config change rebuilds the engine, so open alarms close and detectors restart.
- One value per point per MQTT poll (the latest), not every message (`durian/mqtt_source.py:92`).
- Pulled values carry the tick time, not the read time.
- No process or script event handlers: they would run arbitrary code on the server.
- No OPC UA encryption: only `SecurityPolicy None`, no Browse and no subscriptions. SNMP is v2c GET and a
  bounded GETNEXT walk (no v3, no traps, no table model); DNP3 is a read-only master over TCP; BACnet/IP is
  read-only. None of the four writes to a device.
- The Sparkplug B source is read-only: no command topics, no STATE topic and no rebirth request, so a point cannot be written over it.
- Plain HTTP on localhost; anything else needs a certificate.
- Reset codes and TOTP pending secrets live in memory, so a restart voids them.
- The admin SQL console's MySQL/MariaDB branch (`durian/store.py:514-515`: `max_execution_time`, `start transaction read only`) has never been run; only the SQLite branch is tested (`tests/test_sql_console.py`).
- Line-protocol ingest ignores tags and has no `precision` parameter: timestamps are always nanoseconds (`durian/ingest.py:104`, `durian/ingest.py:120`).
- OPC DA is declined: it needs Windows COM/DCOM, which is not stdlib.

## 7. How this list is kept

When a shortcut is taken on purpose, it gets a `ponytail:` comment naming the ceiling and the upgrade path, and
an entry here. When an entry is fixed, the entry is deleted in the same commit. A debt entry without a
`file:line` is not an entry: it is a complaint.
