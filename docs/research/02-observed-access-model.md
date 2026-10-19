# 02 Observed: access model of the REST API

Observed 2026-10-19 on a clean local Mango v5 (regular edition, fresh install, read-only account, GET requests only). Black-box: request and response only, nothing inside the install was read. Raw responses are in `observed/raw/`.

## Observations

| Request (GET `/rest/latest/...`) | Result | Reading |
|---|---|---|
| `data-sources`, `data-points`, `event-detectors`, `event-handlers`, `publishers`, `watch-lists`, `events` | 200, `{"items":[],"total":0}` | A list endpoint filters by permission and returns an empty page, not an error. The account cannot read the built-in internal data source. |
| `roles` | 200, two roles: `anonymous` (id 3), `user` (id 2, inherits `anonymous`) | Roles form an inheritance chain. A role carries `xid`, `name`, `revision`, `inherited`. |
| `system-settings` | 403, body names the missing role | A single resource is refused outright, and the error message says a role is missing. |
| `modules` | 404 "No endpoint" | Not at that path in this version. Path to be found from the API reference. |

Auth: HTTP Basic worked for read requests. Error bodies carry `mangoStatusCode`, `mangoStatusName`, `localizedMessage`.

## Design lessons for DurianDB

1. Permission is a set of roles on each object (read roles, edit roles, set roles). Authorization is "does the caller hold a role in the object's set", with roles able to inherit.
2. List endpoints hide what the caller may not see. Single-object endpoints refuse. DurianDB should do the same, so an empty list does not leak that anything exists.
3. Every item has a stable `xid` (external id) next to its numeric id, which is what import/export and scripts use.

## Not yet observed (needs read access granted)

Data source and point shape, detector and handler shape, event lifecycle, rollups. See `99-open-questions.md`.
