# 05 Design mapping: studied behavior to DurianDB

Written 2026-10-19. Left column is what the documentation and black-box observation showed (notes 01 to 04). Right column is what DurianDB does and where it differs on purpose.

| Studied behavior | DurianDB |
|---|---|
| Data source owns its points, created disabled first | `source` and `point` objects; a source can be `enabled: false`. Points name their source. |
| Source types come from modules with their own point locators | Registry of two types, `virtual` and `mqtt` (`config.py`). Adding one means a `Source` subclass plus a validator. |
| Create returns defaults; validation errors listed per property | Same shape: `422` with `errors: [{property, message}]`, all problems at once. |
| Permissions: read, edit, set role lists; roles inherit; empty list means superadmin only | Read and set role lists on points, read on sources and watch lists. Inheritance `superadmin > user > anonymous`. Difference: default read role is `user`, because an empty default hid everything from a normal user during research. |
| Lists hide what you cannot see, single objects refuse | Same. Unknown and hidden both answer 404 for points. |
| No default credentials worth keeping | First run generates an admin password and prints it once (the studied default login is public knowledge). |
| Event detectors: many types, duration, return to normal | High, low, rate of change, no update. Duration and return-to-normal on all latching ones. Events store `cleared_ts` and `acked_by`. |
| Event handlers and publishers are separate concepts | `handler` (webhook) and `publisher` (MQTT TLS) objects. A publisher cannot use a prefix a source subscribes to. |
| MQTT client needs CA, client cert and key for `ssl://`; unknown topic is an event | Verifies server cert and hostname always; optional client cert. Unknown topics are auto-created by default (`auto_create`), or dropped and counted when off. |
| Logging mode with tolerance and a recent-value cache | `logging` (NEVER, ON_CHANGE, ALL), `tolerance`, bounded cache of 100 samples. |
| Rollups on history | `avg, min, max, last, count` per time bucket via one SQL query. |
| Tags and watch lists (static, query, hierarchy) | Tags on points; a watch list is a tag match, a list of names, or both. No hierarchy. |
| Audit trail and set-point permission | `audit` table for set, ack, config and user changes. Set needs `settable` and a set role. |
| Config as importable JSON | All config is JSON in one table and edited as JSON in the console. |

## Not implemented (and why)

Industrial protocol drivers, scripting engines and meta points, point hierarchy, reports, dashboards beyond the console, user-owned watch lists, purge jobs, clustering. Each is a real feature of the studied product and a good next exercise.
