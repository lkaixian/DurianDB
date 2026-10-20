# Graph Report - DurianDB  (2026-10-20)

## Corpus Check
- 100 files · ~85,054 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 6 file(s) not represented in the graph (top: .pptx 2, (none) 1, .db-shm 1)

## Summary
- 1206 nodes · 2776 edges · 84 communities (57 shown, 27 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 240 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `ab41f266`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- 09 Dashboards and the extension question
- http_source.py
- Store
- browser_modbus.mjs
- app.js
- core.py
- app.py
- json
- Maintenance
- http_source.py
- auth.py
- modbus.py
- Source
- ModbusSource
- .config
- App
- config.py
- mqtt_lab.py
- Observed: clean lab instance (core 5.7.8)
- ChangeDetector
- Data point
- sys
- ModbusClient
- Observed: MQTT source, virtual points, detectors
- Design mapping: studied behavior to DurianDB
- ModbusIOError
- Note 06: enterprise vs regular edition
- test_core.py
- forms.js
- Observed: access model of the REST API
- Storage
- .handle
- H
- Hook
- Auth
- test_calc.py
- browser_mango.mjs
- _Latch
- browser_mimic.mjs
- test_windows.py
- browser_admin.mjs
- browser_forms.mjs
- browser_point.mjs
- outputs.py
- Outputs
- Level
- Cusum
- Event
- browser_check.mjs
- browser_dashboard.mjs
- browser_mylists.mjs
- migrations.py
- make_nodered_flows.py
- dashboards.js
- Connecting Node-RED to DurianDB
- make_detector
- test_modbus_source.py
- 10 The SCADA baseline, and where DurianDB stands
- Sink
- H
- DESIGN.md
- .run
- dashboards.py
- viewDesigner
- Note 07: Modbus studied behavior and driver
- server.py
- browser_designer.mjs
- MqttSource
- Slave
- validate_dashboard
- Clean-room policy
- browser_login.mjs
- app.py
- .set_password
- test_handlers.py
- can
- RtuFramer
- TcpFramer
- AsciiFramer

## God Nodes (most connected - your core abstractions)
1. `App` - 86 edges
2. `Store` - 73 edges
3. `ApiError` - 71 edges
4. `Source` - 27 edges
5. `new_store()` - 27 edges
6. `seed()` - 23 edges
7. `Quality` - 23 edges
8. `ModbusSource` - 23 edges
9. `hash_password()` - 20 edges
10. `api()` - 20 edges

## Surprising Connections (you probably didn't know these)
- `Audit trail and set-point permission` --conceptually_related_to--> `App`  [INFERRED]
  docs/research/05-design-mapping.md → durian/app.py
- `Lists hide, single objects refuse (404)` --conceptually_related_to--> `App`  [INFERRED]
  docs/research/05-design-mapping.md → durian/app.py
- `LDAP authentication module` --conceptually_related_to--> `validate_source()`  [INFERRED]
  docs/research/06-observed-enterprise-vs-regular.md → durian/config.py
- `Unit ids 1 to 247` --conceptually_related_to--> `validate_point()`  [INFERRED]
  docs/research/07-modbus.md → durian/config.py
- `Config as JSON in one table, edited in console` --conceptually_related_to--> `build_engine()`  [INFERRED]
  docs/research/05-design-mapping.md → durian/config.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Alarm chain: detector, event, handler** — docs_research_01_concepts_eventdetector, docs_research_01_concepts_event, docs_research_01_concepts_eventhandler, docs_research_01_concepts_alarmlevels [EXTRACTED 1.00]
- **Clean-room policy rules** — docs_00_clean_room_policy_cleanroompolicy, docs_00_clean_room_policy_allowedsources, docs_00_clean_room_policy_forbiddensources, docs_00_clean_room_policy_ownedecisions [EXTRACTED 1.00]
- **Data acquisition pipeline** — docs_research_01_concepts_datasource, docs_research_01_concepts_datapoint, docs_research_01_concepts_eventdetector, docs_research_01_concepts_publisher, docs_research_01_concepts_storage [EXTRACTED 1.00]
- **Detector observation to design to core** — docs_research_04_observed_mqtt_and_detectors_detectorduration, docs_research_05_design_mapping_detectors, durian_core_engine [INFERRED 0.75]
- **Studied permissions to DurianDB auth** — docs_research_02_observed_access_model_objectpermissions, docs_research_05_design_mapping_permissionsroles, durian_auth_can [INFERRED 0.75]
- **Failure handling: background poll, back-off, partial rejection** — docs_research_07_modbus_backgroundpoll, docs_research_07_modbus_backoff, docs_research_08_http_and_sql_sources_pullbackoff [INFERRED 0.75]
- **Modbus read flow: framing, batching, decoding** — docs_research_07_modbus_framing, docs_research_07_modbus_batching, docs_research_07_modbus_byteword, docs_research_07_modbus_scaling [INFERRED 0.75]
- **HTTP and SQL pull-source safety choices** — docs_research_08_http_and_sql_sources_tlsverify, docs_research_08_http_and_sql_sources_noredirects, docs_research_08_http_and_sql_sources_maxbytes, docs_research_08_http_and_sql_sources_sqlitereadonly, docs_research_08_http_and_sql_sources_selectonly [INFERRED 0.75]
- **Widget extension: validator, renderer, server-operator install** — docs_research_09_dashboards_widget_extension_pair, durian_dashboards_widget, web_dashboards_registerwidget [INFERRED 0.85]
- **Data not code: content policy, no upload, XSS avoidance** — docs_research_09_dashboards_stored_xss_risk, docs_research_09_dashboards_dashboards_as_data, docs_research_09_dashboards_strict_content_policy, docs_research_09_dashboards_no_ui_widget_upload [INFERRED 0.85]

## Communities (84 total, 27 thin omitted)

### Community 0 - "09 Dashboards and the extension question"
Cohesion: 0.19
Nodes (18): Anonymous role held by everyone, Checks: tests/test_dashboards.py and browser_dashboard.mjs, Dashboards as data, not code, 09 Dashboards and the extension question, No UI upload of widget code, Not built (drag and drop, templates, URL params, per-user, themes, menu editor), Sandboxed frame with own origin (not built), Stored-XSS risk of pages that are code (+10 more)

### Community 1 - "http_source.py"
Cohesion: 0.09
Nodes (16): Polling in background thread, Note 08: HTTP JSON and SQL sources, JSON path lookup (sensors.temp, items[0].value), numbers and booleans only, HTTP response size cap (max_bytes, 1 MB), HTTP redirects never followed, Pull-source failure back-off and partial rejection, HTTP source read-only (no write URL), Single SELECT, key column then numeric column (+8 more)

### Community 3 - "browser_modbus.mjs"
Cohesion: 0.25
Nodes (9): ev(), go(), pending, problems, send(), shot(), sleep(), targets (+1 more)

### Community 4 - "app.js"
Cohesion: 0.09
Nodes (58): errList(), SCOPE_HELP, viewDatabase(), viewTokens(), when(), ackAllHtml(), alarmTableHtml(), api() (+50 more)

### Community 5 - "core.py"
Cohesion: 0.14
Nodes (4): Event detectors: high, low, rate of change, no update with duration and return to normal, HighLimit, LowLimit, NoUpdate

### Community 6 - "app.py"
Cohesion: 0.10
Nodes (11): ApiError, new_store(), call(), call(), call(), call(), call(), call() (+3 more)

### Community 7 - "json"
Cohesion: 0.16
Nodes (5): put(), call(), http(), call(), call()

### Community 8 - "Maintenance"
Cohesion: 0.09
Nodes (6): _is_time(), Maintenance, Overlay, _parse_time(), export(), parse()

### Community 9 - "http_source.py"
Cohesion: 0.15
Nodes (9): dig(), _num(), validate_source(), parse_payload(), NoRedirect, number(), scale(), _num() (+1 more)

### Community 10 - "auth.py"
Cohesion: 0.12
Nodes (9): AuthError, hash_password(), totp(), verify_password(), fails(), get(), call(), make() (+1 more)

### Community 11 - "modbus.py"
Cohesion: 0.18
Nodes (10): byte_order and word_order switches, crc16(), decode(), encode(), lrc(), frame_response(), _Handler, read_request() (+2 more)

### Community 12 - "Source"
Cohesion: 0.19
Nodes (6): Source, Feed, call(), Link, mk(), Push

### Community 13 - "ModbusSource"
Cohesion: 0.10
Nodes (7): Point, ModbusError, ModbusException, make_client(), ModbusSource, _leaves(), test_source()

### Community 14 - ".config"
Cohesion: 0.17
Nodes (7): validate_maintenance(), validate_watchlist(), _http_url(), _num(), validate_handler(), validate_publisher(), validate()

### Community 16 - "config.py"
Cohesion: 0.16
Nodes (15): Source types registry (virtual, mqtt), build_engine(), _int(), load_creds(), make_point(), _modbus_point_errors(), _modbus_source_errors(), _num() (+7 more)

### Community 17 - "mqtt_lab.py"
Cohesion: 0.21
Nodes (3): main(), name(), pem_key()

### Community 18 - "Observed: clean lab instance (core 5.7.8)"
Cohesion: 0.17
Nodes (13): Data source, Observed: clean lab instance (core 5.7.8), Create sources disabled first, then enable, Data point resource shape, Data source resource shape and defaults, Event types with duplicate-handling mode, Internal data source: system observes itself with same machinery, Small core with capabilities from modules (+5 more)

### Community 20 - "Data point"
Cohesion: 0.21
Nodes (12): Alarm levels (none to life safety), Data flow, Data point, Event / alarm, Event handler, Point locator, Maintenance events, Publisher (+4 more)

### Community 21 - "sys"
Cohesion: 0.15
Nodes (5): api(), cleanup(), context(), fail(), wait()

### Community 23 - "Observed: MQTT source, virtual points, detectors"
Cohesion: 0.24
Nodes (10): Event detector, High limit detector, Observed: MQTT source, virtual points, detectors, Detector creation needs type, source id, alarm level, limit, duration, empty handlers, Detector duration: condition must hold N before firing, Return to normal (rtnApplicable): alarms have start and end, Data source and detector type id registries, Unknown topic is an event, not a new point (+2 more)

### Community 24 - "Design mapping: studied behavior to DurianDB"
Cohesion: 0.20
Nodes (11): Logging tolerance and recent-value cache, Design mapping: studied behavior to DurianDB, Audit trail and set-point permission, Config as JSON in one table, edited in console, Event handlers and publishers are separate concepts, Lists hide, single objects refuse (404), Logging mode, tolerance, bounded cache of 100 samples, Not implemented: protocol drivers, scripting, hierarchy, reports, purge, clustering (+3 more)

### Community 25 - "ModbusIOError"
Cohesion: 0.16
Nodes (4): ModbusIOError, ModbusTimeout, SerialLink, TcpLink

### Community 26 - "Note 06: enterprise vs regular edition"
Cohesion: 0.16
Nodes (16): Lesson: backup and purge schedules are part of time-series feature, Lesson: data loss deserves its own system alarm, Note 06: enterprise vs regular edition, Lesson: keep history store behind small interface (add_history, series), LDAP authentication module, Enterprise embedded time-series store (mangoNoSql), Persistent source/publisher pair with throttle (server replication), Write-behind buffered batch inserts (+8 more)

### Community 27 - "test_core.py"
Cohesion: 0.22
Nodes (4): alarm_state(), Broken, run(), Scripted

### Community 28 - "forms.js"
Cohesion: 0.13
Nodes (26): afterChange(), control(), DAYS, delPath(), DET, detectorHtml(), F(), fieldHtml() (+18 more)

### Community 29 - "Observed: access model of the REST API"
Cohesion: 0.29
Nodes (7): Observed: access model of the REST API, Lists hide unseen items, single objects refuse, Per-object read/edit/set role lists, Role inheritance chain (user inherits anonymous), Error bodies with mangoStatusCode and localizedMessage, Stable xid external id next to numeric id, Empty permission lists mean superadmin only

### Community 30 - "Storage"
Cohesion: 0.33
Nodes (5): Logging modes, Relational config DB (H2/MySQL/PostgreSQL/MSSQL), Storage, Time-series history store (IasTSDB), Rollups avg/min/max/last/count via one SQL query

### Community 34 - "Auth"
Cohesion: 0.07
Nodes (11): Generated admin password printed once, Auth, held(), Handler, Dashboards usage section, DurianDB README, Independent original work, no Mango code, Known limits (deliberate) (+3 more)

### Community 35 - "test_calc.py"
Cohesion: 0.16
Nodes (9): CalcSource, read(), evaluate(), go(), parse(), refs(), call(), ev() (+1 more)

### Community 36 - "browser_mango.mjs"
Cohesion: 0.18
Nodes (17): adv(), call(), ev(), go(), names(), pend, problems, rowsN() (+9 more)

### Community 37 - "_Latch"
Cohesion: 0.14
Nodes (4): _Latch, RangeDetector, RateOfChange, TextDetector

### Community 38 - "browser_mimic.mjs"
Cohesion: 0.16
Nodes (15): 11 Mango feature analysis: what the public documentation names, and what DurianDB took, Decisions, Left open, What the pages say, ev(), first(), go(), info() (+7 more)

### Community 39 - "test_windows.py"
Cohesion: 0.27
Nodes (3): active(), _minutes(), call()

### Community 40 - "browser_admin.mjs"
Cohesion: 0.17
Nodes (13): call(), ev(), file, file2, go(), pending, problems, send() (+5 more)

### Community 41 - "browser_forms.mjs"
Cohesion: 0.21
Nodes (15): adv(), ev(), go(), j, msg(), pend, problems, putPoint() (+7 more)

### Community 42 - "browser_point.mjs"
Cohesion: 0.21
Nodes (11): call(), dialogs, ev(), H, pend, problems, send(), shot() (+3 more)

### Community 43 - "outputs.py"
Cohesion: 0.10
Nodes (7): Quality, HttpInSource, parse_quality(), parse_time(), parse(), parse_text(), stamp()

### Community 47 - "Event"
Cohesion: 0.31
Nodes (3): Engine, Event, check()

### Community 48 - "browser_check.mjs"
Cohesion: 0.27
Nodes (10): ev(), go(), pending, problems, send(), shot(), sleep(), summary() (+2 more)

### Community 49 - "browser_dashboard.mjs"
Cohesion: 0.27
Nodes (10): ev(), go(), overflow(), pending, problems, send(), shot(), sleep() (+2 more)

### Community 50 - "browser_mylists.mjs"
Cohesion: 0.29
Nodes (9): ev(), go(), pend, problems, send(), shot(), sleep(), targets (+1 more)

### Community 52 - "make_nodered_flows.py"
Cohesion: 0.39
Nodes (7): add(), comment(), debug(), func(), http_request(), inject(), remember()

### Community 53 - "dashboards.js"
Cohesion: 0.14
Nodes (16): Three fixed tones (normal, warn, alarm), CORE, DASHES, formatted(), heading(), MM, MM_NEW, noAccess() (+8 more)

### Community 54 - "Connecting Node-RED to DurianDB"
Cohesion: 0.29
Nodes (6): 1. Set up DurianDB, 2. Set up Node-RED, 3. What to send, 4. When it does not work, 5. Security notes, Connecting Node-RED to DurianDB

### Community 55 - "make_detector"
Cohesion: 0.14
Nodes (4): ChangeDetector, Detector, make_detector(), NoChange

### Community 57 - "10 The SCADA baseline, and where DurianDB stands"
Cohesion: 0.50
Nodes (3): 10 The SCADA baseline, and where DurianDB stands, Baseline against DurianDB, Order of work from here

### Community 62 - "dashboards.py"
Cohesion: 0.28
Nodes (12): _alarms(), _chart(), _gauge(), _mimic(), _num(), _setpoint(), _status(), _table() (+4 more)

### Community 63 - "viewDesigner"
Cohesion: 0.19
Nodes (14): Designer as form with move/size buttons and live preview, Dashboards list plus #d/name links instead of menu items, hits(), loadData(), mimicEditor(), paintDashboard(), place(), settle() (+6 more)

### Community 64 - "Note 07: Modbus studied behavior and driver"
Cohesion: 0.18
Nodes (13): Register read batching/merging, Contiguous-only batching by default, Note 07: Modbus studied behavior and driver, Modbus framing: TCP MBAP, RTU CRC-16, ASCII LRC, Modbus exception responses raised, not retried, Deliberate omissions: UDP, echo, I/O log, node scan, BCD, mod-10000, abort alarm, Online monitor points per device, Scaling raw*multiplier+additive, reversed on write (+5 more)

### Community 65 - "server.py"
Cohesion: 0.20
Nodes (5): seed(), main(), run_maintenance(), run_ticks(), call()

### Community 66 - "browser_designer.mjs"
Cohesion: 0.22
Nodes (13): centre(), count(), ev(), mouse(), moved, pend, problems, send() (+5 more)

### Community 67 - "MqttSource"
Cohesion: 0.20
Nodes (4): MQTT TLS requires CA, client cert and key for ssl:// (mutual TLS), MQTT TLS: always verify server, optional client cert, unknown topics auto_create, MqttSource, tls_client()

### Community 68 - "Slave"
Cohesion: 0.18
Nodes (4): Studied product Modbus behavior (transports, defaults, batching, back-off), Device, handle(), Slave

### Community 69 - "validate_dashboard"
Cohesion: 0.27
Nodes (4): Validation with error paths, grid fit/overlap, warnings, Ctx, validate_dashboard(), no()

### Community 70 - "Clean-room policy"
Cohesion: 0.20
Nodes (7): Allowed sources (public docs, Swagger, release notes), Clean-room policy, Forbidden sources (Mango source, decompiled binaries, module jars), Own decisions where docs are silent, Radix IoT EULA (ma-core-public LICENSE), Extension model (Java modules, REST, RQL), Operations (safe mode, metrics, backups)

### Community 71 - "browser_login.mjs"
Cohesion: 0.27
Nodes (8): ev(), fill(), pend, problems, send(), shot(), targets, ws

### Community 77 - "can"
Cohesion: 0.50
Nodes (3): Designer role required to create dashboards, Owner and edit_roles edit permission, can()

## Knowledge Gaps
- **29 isolated node(s):** `Checks: tests/test_dashboards.py and browser_dashboard.mjs`, `Not built (drag and drop, templates, URL params, per-user, themes, menu editor)`, `Console shell (nav, main, skip link)`, `Event types with duplicate-handling mode`, `Internal data source: system observes itself with same machinery` (+24 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 407 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **27 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `App` connect `App` to `09 Dashboards and the extension question`, `app.py`, `json`, `Maintenance`, `auth.py`, `Source`, `.config`, `Design mapping: studied behavior to DurianDB`, `.handle`, `Auth`, `test_calc.py`, `test_windows.py`, `outputs.py`, `Outputs`, `Level`, `Event`, `test_modbus_source.py`, `server.py`, `._apply_holds`, `app.py`, `.set_password`, `test_handlers.py`, `can`?**
  _High betweenness centrality (0.129) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `App` (e.g. with `Audit trail and set-point permission` and `Lists hide, single objects refuse (404)`) actually correct?**
  _`App` has 13 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Checks: tests/test_dashboards.py and browser_dashboard.mjs`, `Not built (drag and drop, templates, URL params, per-user, themes, menu editor)`, `Console shell (nav, main, skip link)` to the rest of the system?**
  _29 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `http_source.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08870967741935484 - nodes in this community are weakly interconnected._
- **Why does `Store` connect `Store` to `09 Dashboards and the extension question`, `server.py`, `test_modbus_source.py`, `app.py`, `json`, `._exec`, `auth.py`, `mqtt_lab.py`, `.describe`, `.backup_to`, `._upsert`, `sys`, `Design mapping: studied behavior to DurianDB`, `Note 06: enterprise vs regular edition`, `.run`, `Storage`?**
  _High betweenness centrality (0.119) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `Store` (e.g. with `Rollups avg/min/max/last/count via one SQL query` and `Tags and watch lists (tag match, names, or both; no hierarchy)`) actually correct?**
  _`Store` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Should `Store` be split into smaller, more focused modules?**
  _Cohesion score 0.14 - nodes in this community are weakly interconnected._