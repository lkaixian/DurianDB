# 06 Observed: enterprise edition versus regular edition

Observed 2026-10-19. Two clean local copies of the same release (core 5.7.8) were started side by side, one from each distribution, and queried through the REST API with an admin account on each. Black-box: module lists, accepted type ids (reported by the instance's own validation errors) and system settings. Nothing inside either install was read. The licensing terms for the enterprise copy are the owner's responsibility; they stated they hold permission.

## Result

| | Regular | Enterprise |
|---|---|---|
| Modules | 40 | 43 |
| Data source types | 29 | 30 (adds `PERSISTENT`) |
| Point locator types | 27 | 28 (adds `PL.PERSISTENT`) |
| Publisher types | 4 (BACnet, HTTP sender, Modbus IP, MQTT) | 5 (adds `PERSISTENT`) |
| Event detector types | 18 | 18 (same) |
| Event handler types | 7 | 7 (email, Excel report, process, script, set point, Slack, SNMP) |
| System settings | 201 | 226 (25 more) |

The three extra modules, all at the same version as the core:

1. `mangoNoSqlDatabase`: an embedded time-series store for point history.
2. `persistent`: a data source and publisher pair for moving point values between Mango servers, with a throttle when the receiver falls behind.
3. `ldapAuthentication`: log in through a directory server.

## What the extra settings say about the time-series store

Settings grouped under `mangoNoSql` show the design knobs, without revealing the code:

- Write-behind buffering: values are queued and flushed in batches. Tunables: minimum and maximum inserts per point, batch period, minimum flush interval, how long to wait before backdating, a thread-spawn threshold, how long a point may stay idle before its buffer is cleaned.
- Backup: on or off, schedule (hour, minute, period), incremental or full, number of files kept, location, and the time of the last success.
- Maintenance: a purge that works inside a storage shard, and a corruption scan with its own thread count.
- A dedicated system alarm level for "NoSQL data lost", so loss of buffered values is an event, not a log line.

`persistent` adds two throttle settings: how many values may wait before the sender slows down, and the longest it will wait.

## Lessons for DurianDB

1. Keep the history store behind a small interface (`Store.add_history`, `series`) so a different engine could replace SQLite. The regular edition keeps history in its relational database, the enterprise edition swaps in a purpose-built store. DurianDB already inserts history in one batch per tick, which is the simplest form of write-behind.
2. Data loss deserves its own alarm. If a history insert fails, raise a system event.
3. A backup and a purge schedule are part of a time-series feature, not extras. Both are missing from DurianDB (see the known limits in the README).
4. Replication between servers is a separate source and publisher pair with back-pressure. Not implemented.
5. External authentication is a module, so the core keeps its own users and roles.
