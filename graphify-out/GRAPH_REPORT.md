# Graph Report - DurianDB  (2026-10-20)

## Corpus Check
- Corpus is ~31,257 words - fits in a single context window. You may not need a graph.

## Summary
- 635 nodes · 1349 edges · 34 communities (30 shown, 4 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 166 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Dashboard Design Concepts
- Pull Sources and Safety
- Enterprise Comparison Lessons
- Browser Check Tools
- Console Frontend
- Core Engine and Detectors
- API Errors and Routing
- App Layer and Seed
- Dashboard Validators
- Modbus Errors
- Auth and Users
- Modbus Protocol Core
- MQTT Source
- Point and Codec
- Outputs and Config API
- Dashboard Permissions
- Config Validation
- MQTT Lab Script
- Lab Instance Observations
- Source Registry and Build
- Studied Concepts
- Modbus Behavior Notes
- Modbus Client
- Detector Research
- Design Mapping
- Modbus Framing
- Clean-Room Policy
- Engine Runtime
- Modbus Simulator
- Access Model Research
- Storage Design
- Modbus Test Helpers
- HTTP Server Handler
- Webhook Test Receiver

## God Nodes (most connected - your core abstractions)
1. `Store` - 37 edges
2. `App` - 32 edges
3. `ModbusSource` - 22 edges
4. `ApiError` - 21 edges
5. `Design mapping: studied behavior to DurianDB` - 20 edges
6. `09 Dashboards and the extension question` - 20 edges
7. `Source` - 18 edges
8. `ModbusClient` - 18 edges
9. `ModbusIOError` - 16 edges
10. `build_engine()` - 15 edges

## Surprising Connections (you probably didn't know these)
- `Audit trail and set-point permission` --conceptually_related_to--> `App`  [INFERRED]
  docs/research/05-design-mapping.md → durian/app.py
- `Lists hide, single objects refuse (404)` --conceptually_related_to--> `App`  [INFERRED]
  docs/research/05-design-mapping.md → durian/app.py
- `Generated admin password printed once` --conceptually_related_to--> `Auth`  [INFERRED]
  docs/research/05-design-mapping.md → durian/auth.py
- `LDAP authentication module` --conceptually_related_to--> `validate_source()`  [INFERRED]
  docs/research/06-observed-enterprise-vs-regular.md → durian/config.py
- `Unit ids 1 to 247` --conceptually_related_to--> `validate_point()`  [INFERRED]
  docs/research/07-modbus.md → durian/config.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Clean-room policy rules** — docs_00_clean_room_policy_cleanroompolicy, docs_00_clean_room_policy_allowedsources, docs_00_clean_room_policy_forbiddensources, docs_00_clean_room_policy_ownedecisions [EXTRACTED 1.00]
- **Data acquisition pipeline** — docs_research_01_concepts_datasource, docs_research_01_concepts_datapoint, docs_research_01_concepts_eventdetector, docs_research_01_concepts_publisher, docs_research_01_concepts_storage [EXTRACTED 1.00]
- **Alarm chain: detector, event, handler** — docs_research_01_concepts_eventdetector, docs_research_01_concepts_event, docs_research_01_concepts_eventhandler, docs_research_01_concepts_alarmlevels [EXTRACTED 1.00]
- **Studied permissions to DurianDB auth** — docs_research_02_observed_access_model_objectpermissions, docs_research_05_design_mapping_permissionsroles, durian_auth_can [INFERRED 0.75]
- **Detector observation to design to core** — docs_research_04_observed_mqtt_and_detectors_detectorduration, docs_research_05_design_mapping_detectors, durian_core_engine [INFERRED 0.75]
- **Modbus read flow: framing, batching, decoding** — docs_research_07_modbus_framing, docs_research_07_modbus_batching, docs_research_07_modbus_byteword, docs_research_07_modbus_scaling [INFERRED 0.75]
- **Failure handling: background poll, back-off, partial rejection** — docs_research_07_modbus_backgroundpoll, docs_research_07_modbus_backoff, docs_research_08_http_and_sql_sources_pullbackoff [INFERRED 0.75]
- **HTTP and SQL pull-source safety choices** — docs_research_08_http_and_sql_sources_tlsverify, docs_research_08_http_and_sql_sources_noredirects, docs_research_08_http_and_sql_sources_maxbytes, docs_research_08_http_and_sql_sources_sqlitereadonly, docs_research_08_http_and_sql_sources_selectonly [INFERRED 0.75]
- **Widget extension: validator, renderer, server-operator install** — docs_research_09_dashboards_widget_extension_pair, durian_dashboards_widget, web_dashboards_registerwidget [INFERRED 0.85]
- **Data not code: content policy, no upload, XSS avoidance** — docs_research_09_dashboards_stored_xss_risk, docs_research_09_dashboards_dashboards_as_data, docs_research_09_dashboards_strict_content_policy, docs_research_09_dashboards_no_ui_widget_upload [INFERRED 0.85]

## Communities (34 total, 4 thin omitted)

### Community 0 - "Dashboard Design Concepts"
Cohesion: 0.06
Nodes (43): Anonymous role held by everyone, Checks: tests/test_dashboards.py and browser_dashboard.mjs, Dashboards as data, not code, Designer as form with move/size buttons and live preview, 09 Dashboards and the extension question, Dashboards list plus #d/name links instead of menu items, No UI upload of widget code, Not built (drag and drop, templates, URL params, per-user, themes, menu editor) (+35 more)

### Community 1 - "Pull Sources and Safety"
Cohesion: 0.07
Nodes (25): Polling in background thread, Note 08: HTTP JSON and SQL sources, JSON path lookup (sensors.temp, items[0].value), numbers and booleans only, HTTP response size cap (max_bytes, 1 MB), HTTP redirects never followed, Pull-source failure back-off and partial rejection, HTTP source read-only (no write URL), Single SELECT, key column then numeric column (+17 more)

### Community 2 - "Enterprise Comparison Lessons"
Cohesion: 0.08
Nodes (19): Lesson: backup and purge schedules are part of time-series feature, Lesson: data loss deserves its own system alarm, Note 06: enterprise vs regular edition, Lesson: keep history store behind small interface (add_history, series), LDAP authentication module, Enterprise embedded time-series store (mangoNoSql), Persistent source/publisher pair with throttle (server replication), Write-behind buffered batch inserts (+11 more)

### Community 3 - "Browser Check Tools"
Cohesion: 0.09
Nodes (29): ev(), go(), pending, problems, send(), shot(), sleep(), summary() (+21 more)

### Community 4 - "Console Frontend"
Cohesion: 0.15
Nodes (31): api(), draw(), esc(), every(), firstTemplate(), fmt(), isAdmin(), isUser() (+23 more)

### Community 5 - "Core Engine and Detectors"
Cohesion: 0.10
Nodes (7): Detector, Event, HighLimit, _Latch, LowLimit, NoUpdate, RateOfChange

### Community 6 - "API Errors and Routing"
Cohesion: 0.11
Nodes (6): ApiError, need(), call(), call(), call(), put()

### Community 7 - "App Layer and Seed"
Cohesion: 0.14
Nodes (5): seed(), main(), run_ticks(), call(), call()

### Community 8 - "Dashboard Validators"
Cohesion: 0.17
Nodes (14): Validation with error paths, grid fit/overlap, warnings, _alarms(), _chart(), Ctx, _gauge(), _num(), _setpoint(), _status() (+6 more)

### Community 9 - "Modbus Errors"
Cohesion: 0.13
Nodes (6): ModbusError, ModbusIOError, ModbusTimeout, SerialLink, make_client(), TcpLink

### Community 10 - "Auth and Users"
Cohesion: 0.18
Nodes (6): Auth, AuthError, check_password_strength(), hash_password(), verify_password(), fails()

### Community 11 - "Modbus Protocol Core"
Cohesion: 0.17
Nodes (8): crc16(), encode(), lrc(), _rtu_rest(), frame_response(), _Handler, read_request(), _recv()

### Community 12 - "MQTT Source"
Cohesion: 0.12
Nodes (5): MQTT TLS requires CA, client cert and key for ssl:// (mutual TLS), MQTT TLS: always verify server, optional client cert, unknown topics auto_create, MqttSource, parse(), tls_client()

### Community 13 - "Point and Codec"
Cohesion: 0.15
Nodes (3): Point, decode(), ModbusSource

### Community 14 - "Outputs and Config API"
Cohesion: 0.14
Nodes (4): Event handlers and publishers are separate concepts, Outputs, validate_handler(), validate_publisher()

### Community 15 - "Dashboard Permissions"
Cohesion: 0.23
Nodes (6): Designer role required to create dashboards, Owner and edit_roles edit permission, Dashboard visibility separate from data access, App, can(), Level

### Community 16 - "Config Validation"
Cohesion: 0.23
Nodes (12): _int(), load_creds(), make_point(), _modbus_point_errors(), _modbus_source_errors(), _num(), _roles_ok(), validate_point() (+4 more)

### Community 17 - "MQTT Lab Script"
Cohesion: 0.23
Nodes (3): main(), name(), pem_key()

### Community 18 - "Lab Instance Observations"
Cohesion: 0.17
Nodes (13): Data source, Observed: clean lab instance (core 5.7.8), Create sources disabled first, then enable, Data point resource shape, Data source resource shape and defaults, Event types with duplicate-handling mode, Internal data source: system observes itself with same machinery, Small core with capabilities from modules (+5 more)

### Community 19 - "Source Registry and Build"
Cohesion: 0.18
Nodes (5): Source types registry (virtual, mqtt), build_engine(), Virtual, Source, Broken

### Community 20 - "Studied Concepts"
Cohesion: 0.21
Nodes (12): Alarm levels (none to life safety), Data flow, Data point, Event / alarm, Event handler, Point locator, Maintenance events, Publisher (+4 more)

### Community 21 - "Modbus Behavior Notes"
Cohesion: 0.21
Nodes (11): Register read batching/merging, byte_order and word_order switches, Contiguous-only batching by default, Note 07: Modbus studied behavior and driver, Modbus exception responses raised, not retried, Online monitor points per device, Scaling raw*multiplier+additive, reversed on write, Studied product Modbus behavior (transports, defaults, batching, back-off) (+3 more)

### Community 23 - "Detector Research"
Cohesion: 0.22
Nodes (11): Event detector, High limit detector, Observed: MQTT source, virtual points, detectors, Detector creation needs type, source id, alarm level, limit, duration, empty handlers, Detector duration: condition must hold N before firing, Return to normal (rtnApplicable): alarms have start and end, Data source and detector type id registries, Unknown topic is an event, not a new point (+3 more)

### Community 24 - "Design Mapping"
Cohesion: 0.20
Nodes (11): Logging tolerance and recent-value cache, Design mapping: studied behavior to DurianDB, Audit trail and set-point permission, Config as JSON in one table, edited in console, Lists hide, single objects refuse (404), Logging mode, tolerance, bounded cache of 100 samples, Generated admin password printed once, Not implemented: protocol drivers, scripting, hierarchy, reports, purge, clustering (+3 more)

### Community 25 - "Modbus Framing"
Cohesion: 0.18
Nodes (5): Modbus framing: TCP MBAP, RTU CRC-16, ASCII LRC, Serial through pyserial including socket:// URL, AsciiFramer, RtuFramer, TcpFramer

### Community 26 - "Clean-Room Policy"
Cohesion: 0.20
Nodes (7): Allowed sources (public docs, Swagger, release notes), Clean-room policy, Forbidden sources (Mango source, decompiled binaries, module jars), Own decisions where docs are silent, Radix IoT EULA (ma-core-public LICENSE), Extension model (Java modules, REST, RQL), Operations (safe mode, metrics, backups)

### Community 27 - "Engine Runtime"
Cohesion: 0.29
Nodes (3): Engine, run(), Scripted

### Community 28 - "Modbus Simulator"
Cohesion: 0.20
Nodes (3): Device, handle(), Slave

### Community 29 - "Access Model Research"
Cohesion: 0.29
Nodes (7): Observed: access model of the REST API, Lists hide unseen items, single objects refuse, Per-object read/edit/set role lists, Role inheritance chain (user inherits anonymous), Error bodies with mangoStatusCode and localizedMessage, Stable xid external id next to numeric id, Empty permission lists mean superadmin only

### Community 30 - "Storage Design"
Cohesion: 0.33
Nodes (5): Logging modes, Relational config DB (H2/MySQL/PostgreSQL/MSSQL), Storage, Time-series history store (IasTSDB), Rollups avg/min/max/last/count via one SQL query

## Knowledge Gaps
- **29 isolated node(s):** `Radix IoT EULA (ma-core-public LICENSE)`, `Allowed sources (public docs, Swagger, release notes)`, `Forbidden sources (Mango source, decompiled binaries, module jars)`, `Own decisions where docs are silent`, `Point locator` (+24 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 199 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **4 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Store` connect `Enterprise Comparison Lessons` to `Dashboard Design Concepts`, `API Errors and Routing`, `App Layer and Seed`, `Auth and Users`, `Design Mapping`, `Storage Design`?**
  _High betweenness centrality (0.114) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `Store` (e.g. with `Rollups avg/min/max/last/count via one SQL query` and `Tags and watch lists (tag match, names, or both; no hierarchy)`) actually correct?**
  _`Store` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Radix IoT EULA (ma-core-public LICENSE)`, `Allowed sources (public docs, Swagger, release notes)`, `Forbidden sources (Mango source, decompiled binaries, module jars)` to the rest of the system?**
  _29 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Dashboard Design Concepts` be split into smaller, more focused modules?**
  _Cohesion score 0.0573025856044724 - nodes in this community are weakly interconnected._
- **Why does `09 Dashboards and the extension question` connect `Dashboard Design Concepts` to `Dashboard Validators`, `Dashboard Permissions`?**
  _High betweenness centrality (0.110) - this node is a cross-community bridge._
- **Are the 8 inferred relationships involving `App` (e.g. with `Audit trail and set-point permission` and `Lists hide, single objects refuse (404)`) actually correct?**
  _`App` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Should `Pull Sources and Safety` be split into smaller, more focused modules?**
  _Cohesion score 0.07482993197278912 - nodes in this community are weakly interconnected._