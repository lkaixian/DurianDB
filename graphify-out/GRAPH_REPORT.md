# Graph Report - DurianDB  (2026-10-20)

## Corpus Check
- 95 files · ~78,527 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 4 file(s) not represented in the graph (top: .pptx 2, (none) 1, .css 1)

## Summary
- 1133 nodes · 2615 edges · 61 communities (49 shown, 12 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 232 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `51cc378b`
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
- server.py
- Maintenance
- modbus_source.py
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
- outputs.py
- ModbusClient
- Observed: MQTT source, virtual points, detectors
- Design mapping: studied behavior to DurianDB
- SerialLink
- Clean-room policy
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
- windows.py
- browser_admin.mjs
- browser_forms.mjs
- browser_point.mjs
- Quality
- Outputs
- Cusum
- Event
- browser_check.mjs
- browser_dashboard.mjs
- browser_mylists.mjs
- migrations.py
- make_nodered_flows.py
- NoUpdate
- Connecting Node-RED to DurianDB
- NoChange
- test_modbus_source.py
- 10 The SCADA baseline, and where DurianDB stands
- Sink
- H
- DESIGN.md

## God Nodes (most connected - your core abstractions)
1. `App` - 81 edges
2. `Store` - 71 edges
3. `ApiError` - 65 edges
4. `Source` - 27 edges
5. `new_store()` - 25 edges
6. `seed()` - 23 edges
7. `Quality` - 23 edges
8. `ModbusSource` - 23 edges
9. `api()` - 20 edges
10. `09 Dashboards and the extension question` - 20 edges

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

## Communities (61 total, 12 thin omitted)

### Community 0 - "09 Dashboards and the extension question"
Cohesion: 0.05
Nodes (56): Anonymous role held by everyone, Checks: tests/test_dashboards.py and browser_dashboard.mjs, Dashboards as data, not code, Designer as form with move/size buttons and live preview, 09 Dashboards and the extension question, Dashboards list plus #d/name links instead of menu items, No UI upload of widget code, Not built (drag and drop, templates, URL params, per-user, themes, menu editor) (+48 more)

### Community 1 - "http_source.py"
Cohesion: 0.06
Nodes (25): Polling in background thread, Note 08: HTTP JSON and SQL sources, JSON path lookup (sensors.temp, items[0].value), numbers and booleans only, HTTP response size cap (max_bytes, 1 MB), HTTP redirects never followed, Pull-source failure back-off and partial rejection, HTTP source read-only (no write URL), Single SELECT, key column then numeric column (+17 more)

### Community 3 - "browser_modbus.mjs"
Cohesion: 0.25
Nodes (9): ev(), go(), pending, problems, send(), shot(), sleep(), targets (+1 more)

### Community 4 - "app.js"
Cohesion: 0.09
Nodes (57): errList(), SCOPE_HELP, viewDatabase(), viewTokens(), when(), ackAllHtml(), alarmTableHtml(), api() (+49 more)

### Community 5 - "core.py"
Cohesion: 0.18
Nodes (4): High limit detector, Event detectors: high, low, rate of change, no update with duration and return to normal, HighLimit, LowLimit

### Community 6 - "app.py"
Cohesion: 0.09
Nodes (13): ApiError, new_store(), call(), call(), call(), get(), call(), call() (+5 more)

### Community 7 - "server.py"
Cohesion: 0.11
Nodes (8): seed(), main(), run_maintenance(), run_ticks(), call(), Smtp, put(), call()

### Community 8 - "Maintenance"
Cohesion: 0.09
Nodes (6): _is_time(), Maintenance, Overlay, _parse_time(), export(), parse()

### Community 9 - "modbus_source.py"
Cohesion: 0.22
Nodes (6): ModbusError, ModbusException, make_client(), SqlSource, _leaves(), test_source()

### Community 10 - "auth.py"
Cohesion: 0.17
Nodes (6): AuthError, hash_password(), verify_password(), fails(), call(), make()

### Community 11 - "modbus.py"
Cohesion: 0.11
Nodes (14): byte_order and word_order switches, Studied product Modbus behavior (transports, defaults, batching, back-off), crc16(), decode(), encode(), lrc(), Device, frame_response() (+6 more)

### Community 12 - "Source"
Cohesion: 0.07
Nodes (11): Source types registry (virtual, mqtt), build_engine(), Virtual, Source, HttpInSource, MqttSource, parse(), Feed (+3 more)

### Community 13 - "ModbusSource"
Cohesion: 0.11
Nodes (12): Failure back-off period x scale^failures, capped, Register read batching/merging, Contiguous-only batching by default, Note 07: Modbus studied behavior and driver, Modbus exception responses raised, not retried, Online monitor points per device, Scaling raw*multiplier+additive, reversed on write, Unit ids 1 to 247 (+4 more)

### Community 14 - ".config"
Cohesion: 0.16
Nodes (7): validate_maintenance(), validate_watchlist(), _http_url(), _num(), validate_handler(), validate_publisher(), validate()

### Community 15 - "App"
Cohesion: 0.17
Nodes (5): Designer role required to create dashboards, Owner and edit_roles edit permission, App, can(), check_password_strength()

### Community 16 - "config.py"
Cohesion: 0.20
Nodes (12): _int(), load_creds(), make_point(), _modbus_point_errors(), _modbus_source_errors(), _num(), _roles_ok(), validate_point() (+4 more)

### Community 17 - "mqtt_lab.py"
Cohesion: 0.25
Nodes (3): main(), name(), pem_key()

### Community 18 - "Observed: clean lab instance (core 5.7.8)"
Cohesion: 0.22
Nodes (10): Data source, Observed: clean lab instance (core 5.7.8), Data source resource shape and defaults, Event types with duplicate-handling mode, Internal data source: system observes itself with same machinery, Small core with capabilities from modules, POLL_ABORTED default alarm level URGENT, Tags and rollup defaults on point (+2 more)

### Community 19 - "ChangeDetector"
Cohesion: 0.18
Nodes (3): ChangeDetector, state_label(), StateDetector

### Community 20 - "Data point"
Cohesion: 0.20
Nodes (12): Alarm levels (none to life safety), Data flow, Data point, Event / alarm, Event handler, Point locator, Maintenance events, Rollups (+4 more)

### Community 21 - "outputs.py"
Cohesion: 0.10
Nodes (9): tls_client(), call(), http(), call(), api(), cleanup(), context(), fail() (+1 more)

### Community 22 - "ModbusClient"
Cohesion: 0.16
Nodes (4): ModbusClient, ModbusIOError, ModbusTimeout, TcpLink

### Community 23 - "Observed: MQTT source, virtual points, detectors"
Cohesion: 0.22
Nodes (11): Event detector, Observed: MQTT source, virtual points, detectors, Detector creation needs type, source id, alarm level, limit, duration, empty handlers, Detector duration: condition must hold N before firing, MQTT TLS requires CA, client cert and key for ssl:// (mutual TLS), Return to normal (rtnApplicable): alarms have start and end, Data source and detector type id registries, Unknown topic is an event, not a new point (+3 more)

### Community 24 - "Design mapping: studied behavior to DurianDB"
Cohesion: 0.18
Nodes (12): Create sources disabled first, then enable, Logging tolerance and recent-value cache, Design mapping: studied behavior to DurianDB, Audit trail and set-point permission, Config as JSON in one table, edited in console, Lists hide, single objects refuse (404), Logging mode, tolerance, bounded cache of 100 samples, Not implemented: protocol drivers, scripting, hierarchy, reports, purge, clustering (+4 more)

### Community 25 - "SerialLink"
Cohesion: 0.10
Nodes (7): Modbus framing: TCP MBAP, RTU CRC-16, ASCII LRC, Serial through pyserial including socket:// URL, AsciiFramer, _rtu_rest(), RtuFramer, SerialLink, TcpFramer

### Community 26 - "Clean-room policy"
Cohesion: 0.07
Nodes (29): Allowed sources (public docs, Swagger, release notes), Clean-room policy, Forbidden sources (Mango source, decompiled binaries, module jars), Own decisions where docs are silent, Radix IoT EULA (ma-core-public LICENSE), Extension model (Java modules, REST, RQL), Operations (safe mode, metrics, backups), Lesson: backup and purge schedules are part of time-series feature (+21 more)

### Community 27 - "test_core.py"
Cohesion: 0.29
Nodes (3): Broken, run(), Scripted

### Community 28 - "forms.js"
Cohesion: 0.13
Nodes (26): afterChange(), control(), DAYS, delPath(), DET, detectorHtml(), F(), fieldHtml() (+18 more)

### Community 29 - "Observed: access model of the REST API"
Cohesion: 0.29
Nodes (7): Observed: access model of the REST API, Lists hide unseen items, single objects refuse, Per-object read/edit/set role lists, Role inheritance chain (user inherits anonymous), Error bodies with mangoStatusCode and localizedMessage, Stable xid external id next to numeric id, Empty permission lists mean superadmin only

### Community 30 - "Storage"
Cohesion: 0.33
Nodes (5): Logging modes, Relational config DB (H2/MySQL/PostgreSQL/MSSQL), Storage, Time-series history store (IasTSDB), Rollups avg/min/max/last/count via one SQL query

### Community 31 - ".handle"
Cohesion: 0.11
Nodes (4): need(), alarm_state(), Level, Raw

### Community 34 - "Auth"
Cohesion: 0.12
Nodes (5): Generated admin password printed once, Auth, held(), Handler, Security notes (scrypt, CSRF header, CSP, lockout)

### Community 35 - "test_calc.py"
Cohesion: 0.17
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

### Community 39 - "windows.py"
Cohesion: 0.16
Nodes (3): active(), covers(), _minutes()

### Community 40 - "browser_admin.mjs"
Cohesion: 0.17
Nodes (13): call(), ev(), file, file2, go(), pending, problems, send() (+5 more)

### Community 41 - "browser_forms.mjs"
Cohesion: 0.21
Nodes (15): adv(), ev(), go(), j, msg(), pend, problems, putPoint() (+7 more)

### Community 42 - "browser_point.mjs"
Cohesion: 0.21
Nodes (11): call(), dialogs, ev(), H, pend, problems, send(), shot() (+3 more)

### Community 43 - "Quality"
Cohesion: 0.21
Nodes (5): Quality, parse_payload(), parse_quality(), parse_time(), stamp()

### Community 44 - "Outputs"
Cohesion: 0.24
Nodes (3): Publisher, Event handlers and publishers are separate concepts, Outputs

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

### Community 54 - "Connecting Node-RED to DurianDB"
Cohesion: 0.29
Nodes (6): 1. Set up DurianDB, 2. Set up Node-RED, 3. What to send, 4. When it does not work, 5. Security notes, Connecting Node-RED to DurianDB

### Community 57 - "10 The SCADA baseline, and where DurianDB stands"
Cohesion: 0.50
Nodes (3): 10 The SCADA baseline, and where DurianDB stands, Baseline against DurianDB, Order of work from here

## Knowledge Gaps
- **29 isolated node(s):** `Checks: tests/test_dashboards.py and browser_dashboard.mjs`, `Not built (drag and drop, templates, URL params, per-user, themes, menu editor)`, `Known limits (deliberate)`, `Run and configuration (env vars, MQTT lab)`, `Console shell (nav, main, skip link)` (+24 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 377 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `App` connect `App` to `09 Dashboards and the extension question`, `Auth`, `test_calc.py`, `app.py`, `windows.py`, `Maintenance`, `server.py`, `auth.py`, `Quality`, `Source`, `.points`, `.config`, `Event`, `Outputs`, `outputs.py`, `Design mapping: studied behavior to DurianDB`, `test_modbus_source.py`, `.handle`?**
  _High betweenness centrality (0.116) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `App` (e.g. with `Audit trail and set-point permission` and `Lists hide, single objects refuse (404)`) actually correct?**
  _`App` has 13 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Checks: tests/test_dashboards.py and browser_dashboard.mjs`, `Not built (drag and drop, templates, URL params, per-user, themes, menu editor)`, `Known limits (deliberate)` to the rest of the system?**
  _29 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `09 Dashboards and the extension question` be split into smaller, more focused modules?**
  _Cohesion score 0.05150905432595573 - nodes in this community are weakly interconnected._
- **Why does `Store` connect `Store` to `09 Dashboards and the extension question`, `test_modbus_source.py`, `app.py`, `server.py`, `auth.py`, `Design mapping: studied behavior to DurianDB`, `Clean-room policy`, `Storage`?**
  _High betweenness centrality (0.088) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `Store` (e.g. with `Rollups avg/min/max/last/count via one SQL query` and `Tags and watch lists (tag match, names, or both; no hierarchy)`) actually correct?**
  _`Store` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Should `http_source.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06313497822931785 - nodes in this community are weakly interconnected._