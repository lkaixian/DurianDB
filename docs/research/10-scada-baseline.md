# 10 The SCADA baseline, and where DurianDB stands

Written 2026-10-20 after a course correction. The project had drifted toward generic web-application polish (migrations, notifications, session screens). The first goal was a reference for how a **SCADA** system works, so the roadmap should follow the functions a SCADA system is expected to have, not the order in which ideas came up.

Sources and honesty: the function list below is the commonly accepted breakdown of a SCADA system (field connectivity, tag database, historian, alarm management, HMI, control, security and audit, reporting, calculation, resilience, integration). The alarm material follows the ISA-18.2 alarm state model and the EEMUA 191 alarm-rate guidance as described by secondary sources and vendor documentation (the standards themselves are paywalled and were not read): [ICONICS alarm state diagram](https://documentation.iconics.com/v10.97.3/Content/Alarming/Alarm%20Server/Alarm%20References/alarm-state-transition-diagram.htm), [WinCC OA alarm shelving](https://www.winccoa.com/documentation/WinCCOA/latest/en_US/alarmshelving/topics/alarm_shelving_overview.html), [Emerson alarm management paper](https://d1-live.emerson.com/documents/automation/article-alarm-management-by-numbers-deltav-en-38292.pdf). Treat figures as guidance. ISA-101 (HMI) and IEC 62443 (security) are named as the relevant families but were not researched in detail.

## Baseline against DurianDB

| # | Function | Expected of a SCADA | DurianDB today | Gap |
|---|---|---|---|---|
| 1 | Field connectivity | Drivers for common protocols, comms health, reconnect | Modbus TCP/RTU/ASCII, MQTT over TLS, HTTP JSON poll, SQL, virtual. Health per source, back-off. | **Inbound HTTP push (Node-RED)**, OPC UA, SNMP, BACnet, DNP3 |
| 2 | Tag database | Name, description, engineering units, range, type, scaling, source and address, scan class | Name, unit, tags, scaling in drivers, logging mode and tolerance | Description, engineering range, discrete state texts, scan classes, calculated tags |
| 3 | **Data quality and time** | Every value carries a quality (good, uncertain, bad, stale) and a source timestamp apart from the server's | Value and the engine's tick time only | **Missing, and the rest depends on it** |
| 4 | **Alarm management** | ISA-18.2 states: normal, unacknowledged, acknowledged, return-to-normal unacknowledged, plus shelved, suppressed by design, out of service. Priorities, deadband or hysteresis, delays, chatter protection, rate KPIs | Raise, clear, acknowledge, 7 levels, on-delay, state shown in text | State model made explicit, hysteresis, shelving with reason and expiry, suppression, out of service, KPIs (rate per 10 minutes, standing alarms, top offenders), change history |
| 5 | Historian | Long-term store, retention, rollups, export, no silent loss | SQLite or MariaDB, rollups, retention, backups, CSV, buffered writes with an alarm on failure | Compression beyond tolerance, backfill, store and forward at the edge |
| 6 | HMI | Process graphics bound to live tags, navigation, alarm indication, trends, faceplates | Dashboards with value, gauge, chart, alarms, table, setpoint | Mimics (SVG bound to points), point detail faceplate, operator notes |
| 7 | Control actions | Permissioned writes, audit, limits on what can be sent, confirmation | Set with roles and audit | Setpoint limits, confirm step, rate limits |
| 8 | Security and audit | Least privilege, authentication, session control, full change audit with before and after, secure transport, account lifecycle | Roles, lockout, CSRF, strict content policy, action audit | Session idle timeout, change audit with before and after, account disable, password lifecycle, API tokens for machines, HTTPS on the server |
| 9 | Reports | On-demand and scheduled: alarm summary, shift report, trend tables | CSV export | Reports |
| 10 | Calculation | Derived tags from expressions, simple sequences | None | Calculated points with a safe expression evaluator |
| 11 | Resilience | Buffering, heartbeat or watchdog, comms-failure alarms | Write buffer, no-update detector, source health | Automatic alarm when a source goes unhealthy, heartbeat |
| 12 | Integration | REST, MQTT, webhooks, gateways | REST, MQTT TLS publisher and source, webhooks, HTTP poll | Inbound HTTP and token access (**asked for**), Sparkplug B, OPC UA server |
| 13 | Diagnostics | Source and database health, system alarms | Source health, database page data, system alarm | Page in the console |

## Order of work from here

1. **Integration first, because it was asked for**: API tokens and an inbound HTTP source so Node-RED can POST and GET, with MQTT as before.
2. **Data quality and source timestamps** (row 3). Alarm logic, history and dashboards all need to know whether a value can be trusted.
3. **Alarm management** (row 4): the explicit state model, hysteresis, shelving, suppression, KPIs.
4. **Tag database fields and safe control** (rows 2 and 7).
5. **Comms-failure alarms and heartbeat** (row 11).
6. **Calculated points** (row 10), then **reports** (row 9) and **mimics** (row 6).
7. Generic account and interface polish continues only where it serves rows 8 and 13.

Already done that belongs to the baseline: the historian work (row 5) and the migration layer it needed.
