# 12 Graceful failure: every way DurianDB can fail, and what the operator sees

Written 2026-10-27, after an audit of the current code (`durian/`, `web/`). Section 1 is what exists,
section 2 is the audit's eleven gaps and how each was closed, section 3 is the rule that decides where a new
failure belongs, and section 4 is the wording it should use.

The principle: **a failure must reach a human, name the thing that failed, and not be worse than the failure.**
Silently degraded is the worst state — the console looks healthy while values are stale.

## 1. What exists today

### 1.1 The request path

| Failure | Handled at | Operator sees |
|---|---|---|
| Wrong or expired session | `server.py` `AuthError` | the sign-in page, or a 401 with a message |
| Bad `Content-Length` | `server.py:77` | 400 `bad Content-Length` |
| Body over 1 MB (5 MB for imports) | `server.py:80` | 413 `body too large` |
| Non-UTF8 text body | `server.py:86` | 400 `text must be UTF-8` |
| Malformed JSON | `server.py:91` | 400 `body must be JSON` |
| Missing `X-Requested-With` on a write | `server.py:74` | 403, names the header |
| Validation failure | `config.py`, `outputs.py`, `windows.py`, `dashboards.py` return `[{property, message}]` | 422, each message under its field |
| Unexpected exception | `server.py:120` | 500 `internal error`; the traceback goes to the log only |

### 1.2 The data plane

| Failure | Detected by | Operator sees |
|---|---|---|
| A pull source dies | `PolledSource.cycle` catches everything, backs off, marks its points BAD | URGENT `(source) <name>` alarm after `down_after`, plus the reason in the source list |
| One point cannot be read | `fetch()` returns a rejection list | `error` text on the source (`N point(s) rejected: ...`, first three) |
| A poll overruns its period | `Engine._overrun` | URGENT `(source) <name>` alarm until polls fit |
| No data inside the heartbeat | `Engine._watch` | URGENT source-down alarm |
| A value is stale, from the future, or flagged | `Engine.tick` | held as the last value, marked `uncertain`, no alarm on it |
| A detector fires | `core.Detector` | the alarm itself, in the journal and on the point page |
| History write fails | `App` buffer | `(system)` URGENT alarm, `buffered_samples` / `lost_samples` on the Database page |
| A calc expression fails | `calc.py` per point | the point goes BAD, the expression error is the source's `error` |
| A handler cannot deliver | `Outputs._work` | **nothing** — `failed` is a counter no page shows |
| A publisher cannot start | `Outputs.__init__` | **nothing** — `log.warning` only |
| Retention or backup fails | `maintenance.tick` | an audit row; the purge or backup HTTP call returns 500 |

## 2. Gaps found in the audit, and how they were closed

All eleven were real defects, ordered here by what would have hurt an operator most. Each is now closed and
each has a test that fails if it reopens. Section 1.2 above describes the table as it reads today.

| # | Gap | Closed by | Test |
|---|---|---|---|
| 1 | A failing tick loop was invisible: `app.tick()` raised, the loop logged and continued, and the console kept showing the last values | three consecutive failures raise one `(system)` URGENT alarm naming the exception; a good tick clears it and starts the count again | `test_tick_health.py` |
| 2 | Delivery failures were invisible: a webhook, email or Slack route could be dead for weeks | per-target failed / last-error / last-ok, `GET /api/outputs`, and a Delivery column on the Handlers and Publishers pages | `test_output_health.py` |
| 3 | Publishers that never start were invisible | a publisher with unreadable credentials is held as a fault with its reason, on the same surface | `test_output_health.py` |
| 4 | The event write path could grow without bound during an alarm storm | capped at `EVENT_PENDING_MAX`, with a count, and one `(system)` alarm saying what the outage cost once writes work again | `test_event_buffer.py` |
| 5 | The source-down alarm said "not connected" and dropped the reason | the MQTT connect and disconnect callbacks carry the broker and reason code, and `manages_error` stops the engine clearing it | `test_mqtt_health.py` |
| 6 | Config import had no rollback | the whole apply runs inside `Store.atomic()` | `test_config_rollback.py` |
| 7 | A saved config whose reload failed stayed saved | the stored body is restored and the caller gets a 409 saying the previous version is still in place | `test_config_rollback.py` |
| 8 | A non-object JSON body became a 500 | one guard in `App.handle`; sign-in and reset get the same treatment | `test_envelope.py` |
| 9 | A corrupt schema version replayed every migration | "no table" and "unreadable table" are different answers, and the second stops the server | `test_envelope.py` |
| 10 | NaN or infinity could enter history from Modbus | a non-finite reading, including one produced by scaling, is a rejected point | `test_modbus_finite.py` |
| 11 | A weak first-run password crashed with a traceback | `main()` exits with a sentence | `test_tls.py` |

One architecture gap behind the same principle was closed too: the engine and the database no longer share a
lock, so a slow tick cannot stall every request (`test_engine_lock.py`).

What is still open is tracked in `TECHNICAL_DEBT.md`, not here.

## 3. The rule for a new failure

Ask, in order:

1. **Is it the caller's mistake?** → 4xx with `{property, message}` so the console can put it under the field.
   Never a 500.
2. **Is it a device or network fault?** → the point goes BAD, the source carries the reason, and the
   source-down machinery decides whether that is worth an alarm.
3. **Does it mean data is being lost or not collected?** → a `(system)` or `(source)` event, at URGENT, because
   this is the state an operator must not miss.
4. **Is it a programming error?** → 500, the traceback in the log, and an unhandled-exception counter the
   health endpoint reports. Repeated 500s from one route are a `(system)` alarm, not a silent log line.
5. **Never:** `except Exception: pass`, an unbounded retry list, or a failure whose only trace is a log line.

## 4. Wording

Errors are sentences that say what failed and what to do, in the console's voice:

- `the device did not answer within 0.5s` — not `TimeoutError`
- `point temp is not in the reply` — not `KeyError`
- `history is not being saved; 1204 samples are buffered, 12 lost` — not `sqlite3.OperationalError`
- `give a reason of 3 to 200 characters` — not `validation failed`

A message that contains an exception class name is a bug in the message, not in the exception.
