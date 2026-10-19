# 11 Mango feature analysis: what the public documentation names, and what DurianDB took

Written 2026-10-20. Sources are the public documentation pages at docs.radixiot.com read that day (home, User Guide, Events and Alarms, Data Points, Configuration). No lab instance was logged into, no jar or source was opened. The pages are overviews: where they name a feature without describing it, this note says so and DurianDB's version is its own design.

## What the pages say

- **Data types:** numeric, binary, multistate, alphanumeric and image. The type decides which detectors, renderers and rollups are available.
- **Logging:** on every change, at fixed intervals, on every update, or not at all.
- **Event detectors (names only):** high limit, low limit, state, change, no change, no update, range, rate of change, smoothness, CUSUM.
- **Event handlers (names only):** email, set point, process, script. Email has SMTP settings, recipient lists, templates and escalation.
- **Alarm levels:** none, information, important, warning, urgent, critical, life safety.
- **Maintenance events:** "schedule or manually run maintenance periods that drop, auto-acknowledge, or suppress handlers for events from selected data sources, data points, or a watch list". An Advanced Scheduler offers weekly repeating schedules.
- **Publishers:** HTTP sender, MQTT Sparkplug, gRPC, Slack, with queue management.
- **Watch lists:** static, query-based and hierarchy-based. **Meta data points:** scripted calculations. **Reports:** Excel from templates, rollups and statistics, CSV export. **Tags:** key/value pairs; bulk CSV import and export of points.

## Decisions

| Mango feature | DurianDB | Why |
|---|---|---|
| Binary and multistate types | `states` labels on a point, label shown everywhere, select to set | Operators read "Running", not 1. Values stay numbers underneath, so history, limits and rollups are unchanged. |
| Alphanumeric and image types | Not built | Everything stored is a number. Strings would touch history, detectors and every chart. |
| State, range, change, no-change detectors | `STATE`, `RANGE`, `CHANGE`, `NO_CHANGE` | They answer real plant questions: a pump in Fault, a value out of band, a frozen sensor. Same on-delay, off-delay and level model as the others. |
| Smoothness and CUSUM detectors | Not built | Statistical detectors that need tuning guidance the pages do not give. |
| Maintenance events (drop, acknowledge, suppress) | Config kind `maintenance` with those three modes, scope by points, tags or sources, schedules once, weekly or manual | Planned work should not page anyone. Held alarms reuse the shelving machinery, so an alarm still active when the window ends is announced. |
| Interval logging | `logging: INTERVAL` with the last value or the average, minimum or maximum of each period | Slow, steady signals do not need every update stored. |
| Rollups beyond avg/min/max/last/count | `first`, `sum`, `delta` (last minus first of the bucket) | Counters and totals. Counter resets inside a bucket are not corrected. |
| Set-point event handler | `setpoint` handler: value on raise, value or release on return to normal, through the same limits and step size as an operator write | Closing a loop on an alarm is common, and it must not be a way around safe control. The confirm step is skipped because no person is there to confirm. |
| Email event handler | `email` handler with SMTP (none, STARTTLS, SSL), credentials file, scope | The first thing an operator without Node-RED expects. Recipient lists per group and templates are not built. |
| Process and script handlers, scripting meta points | Declined | They run arbitrary code or commands on the server. The expression language in `calc.py` covers calculations without that risk, and the webhook handler reaches anything external. |
| Hierarchy watch lists, point folders | A `folder` path on each point, grouping on the overview and a `folder` filter that includes subfolders | Operators think in plant areas. Tags and watch lists still group points across folders. |
| HTTP sender, Slack, gRPC publishers | Not built | MQTT and the webhook cover outbound data and alarms. |

## Left open

Delta and accumulator rollup semantics on sparse data, how Mango handles an overrunning poll beyond the documented alarm, and the exact duplicate-event rules were not described on the pages read.
