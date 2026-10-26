# 12 Graceful failure: every way DurianDB can fail, and what the operator sees

Written 2026-10-27, after an audit of the current code (`durian/`, `web/`). It is a plan as much as a record:
section 1 is what exists, section 2 is what is missing and in what order to close it, section 3 is the rule
that decides where a new failure belongs.

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

## 2. Gaps, worst first

Ordered by what would actually hurt an operator. Each is a real defect found in the audit, not a wish.

1. **A failing tick loop is invisible.** `server.py:136` logs and continues. If `app.tick()` keeps raising,
   history stops, alarms stop, and source-down detection stops, while the console still renders the last
   values. *Fix:* count consecutive failures in the engine and raise a `(system)` URGENT alarm at the third,
   naming the exception; clear it on a good tick. The console shows it in the header count like any other.
2. **Delivery failures are invisible.** Webhook, email, Slack and set-point handlers can fail forever and no
   page says so. *Fix:* `Outputs.failed` and a per-handler last-error/last-success surface on the Handlers
   page, plus a `(system)` alarm when a handler has failed for five minutes.
3. **Publishers that never start are invisible.** A bad credentials file means values are silently not
   published. *Fix:* hold the publisher as a faulted stub with its reason, show it in the Publishers list, and
   alarm like (2).
4. **The event write path can grow without bound.** Failed alarm inserts are retried with no cap (unlike
   history's `PENDING_MAX`), so a database outage during an alarm storm grows memory until the process dies.
   *Fix:* the same cap and the same `(system)` alarm as history.
5. **The source-down alarm is less informative than the source table.** For sources that manage their own
   errors it says "not connected" and drops the real reason. *Fix:* put `s.error` in the alarm detail.
6. **Config import has no rollback.** Replace mode writes and deletes as it goes; a failure halfway leaves half
   a configuration. *Fix:* validate everything first (already done), then apply inside one transaction.
7. **A saved config whose reload fails returns 500 but stays saved.** The operator cannot tell whether it took.
   *Fix:* reload first, persist second, and say which of the two failed.
8. **A non-object JSON body becomes 500.** `[]` reaches `.get` and raises `AttributeError`. *Fix:* one guard in
   `App.handle` turning a non-dict, non-str body into 400 `body must be a JSON object`.
9. **A corrupt schema version replays every migration.** `Store.version()` returns 0 on any error. *Fix:*
   distinguish "no table" from "unreadable" and refuse to start on the second.
10. **NaN can enter from Modbus.** `decode` output is never checked for finiteness, unlike the HTTP and SQL
    paths. *Fix:* the same check, and a rejection message naming the register.
11. **A slow starter-up crashes with a traceback.** `bootstrap` can raise `AuthError` before the server is
    listening. *Fix:* catch it in `main()` and exit with the reason, as the TLS settings already do.

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
