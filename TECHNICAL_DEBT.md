# Technical debt

Written 2026-10-27 from a read-only survey of the tree. Every entry names where it is and what it costs. It is
maintained: delete an entry when it is fixed, add one when a shortcut is taken on purpose.

Severity: **high** = can lose data, silently degrade, or block a release. **medium** = costs time, hides bugs, or
makes a claim untrue. **low** = cosmetic or documentation.

## 1. Correctness and silent failure

| # | Defect | Where | Severity |
|---|---|---|---|
| C18 | A `ts` of `NaN` sails through every time guard: `json.loads` accepts the literal, `parse_time` has no finiteness check (the value check at `ingest.py:115` covers values only), and both comparisons in the submit chain are false against NaN. The entry is accepted, and NaN becomes the point's high-water mark and `last_ts` — from then on the out-of-order rule silently never fires for that point. Confirmed by runtime repro on the 2026-10-28 F8 review, not inferred: `submit` accepted `ts=NaN` and a following older value that should have been refused went through too. The shape predates F8 (F8 edited exactly this chain); the fix is one `math.isfinite` refusal beside the value check, RED first. | `durian/ingest.py:124-131`, `durian/core.py:621` | medium |
| C19 | Replayable backfill rows: before F8 a `(point, ts)` already received could never be stored twice, because the older-or-equal entry was refused. Now a repeat backfill is accepted and lands as its own row, and `rollups`/`history_summary`/forecasts double-count it. Accepted knowingly (the F8 contract pins the accept path; a dedup there would pin the old refusal back); the fix shape is a unique index on `(point, ts)` or an insert-where-not-exists at the store seam, with a RED that replays the same backfill twice. Found by the F8 security review, 2026-10-28. | `durian/store.py:172-174`, `durian/migrations.py:17-18` | medium |
| C15 | `validate_point` has no known-key check and the config PUT stores the raw body, so a typo'd or junk setting on a point is silently accepted and persisted — a mistyped `max_step` or `set_min` drops the safety limit without a word. Related (C16): the same route's value sentinel leaks. Found 2026-10-28 by the F4 RED probe; the `min_write_gap` field itself is validated since the F4 wave, but a typo'd known key (`max_stepp`) is still stored and silently ignored, and unknown keys on any kind persist. Fix shape: one known-key guard at the shared PUT path with per-kind key lists. | `durian/config.py:412`, `durian/app.py:1508` | high |
| C16 | A text set-point request whose body omits `value` writes the literal string `"missing"`: the route's default sentinel passes the printable-text check, so the point takes a nonsense value that looks like a real one. Found by the F4 security review as a pre-existing sibling of that path (static trace; runtime confirmation owed with the fix's RED test). | `durian/app.py:751-753` | medium |

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

## 7. How this list is kept

When a shortcut is taken on purpose, it gets a `ponytail:` comment naming the ceiling and the upgrade path, and
an entry here. When an entry is fixed, the entry is deleted in the same commit. A debt entry without a
`file:line` is not an entry: it is a complaint.
