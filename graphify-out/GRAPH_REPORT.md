# Graph Report - DurianDB  (2026-10-20)

## Corpus Check
- Corpus is ~24,355 words - fits in a single context window. You may not need a graph.

## Summary
- 554 nodes · 1172 edges · 25 communities (19 shown, 6 thin omitted)
- Extraction: 89% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 129 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- App API Layer
- Pull Sources and Safety Choices
- Config and Source Registry
- Outputs and MQTT Source
- Console Frontend
- Modbus Polling Behavior
- Clean-Room Policy
- Store and Rollups
- Modbus Simulator
- Browser Check Tools
- Modbus Framing
- Studied Concepts
- MQTT Lab Script
- Modbus Errors
- Design Mapping
- Modbus Codec
- Modbus Client
- Detector Research
- Core Concepts Note
- Serial Link
- Access Model Research
- Storage Design
- HTTP Server Handler
- Test Web Server
- Webhook Test Receiver

## God Nodes (most connected - your core abstractions)
1. `Store` - 34 edges
2. `App` - 27 edges
3. `ModbusSource` - 22 edges
4. `Design mapping: studied behavior to DurianDB` - 20 edges
5. `Source` - 19 edges
6. `ApiError` - 18 edges
7. `ModbusClient` - 18 edges
8. `ModbusIOError` - 16 edges
9. `Note 07: Modbus studied behavior and driver` - 16 edges
10. `build_engine()` - 15 edges

## Surprising Connections (you probably didn't know these)
- `Audit trail and set-point permission` --conceptually_related_to--> `App`  [INFERRED]
  docs/research/05-design-mapping.md → durian/app.py
- `Lists hide, single objects refuse (404)` --conceptually_related_to--> `App`  [INFERRED]
  docs/research/05-design-mapping.md → durian/app.py
- `Generated admin password printed once` --conceptually_related_to--> `Auth`  [INFERRED]
  docs/research/05-design-mapping.md → durian/auth.py
- `Alarm levels (none to life safety)` --conceptually_related_to--> `Level`  [INFERRED]
  docs/research/01-concepts.md → durian/core.py
- `High limit detector` --conceptually_related_to--> `HighLimit`  [INFERRED]
  docs/research/01-concepts.md → durian/core.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Clean-room policy rules** — docs_00_clean_room_policy_cleanroompolicy, docs_00_clean_room_policy_allowedsources, docs_00_clean_room_policy_forbiddensources, docs_00_clean_room_policy_ownedecisions [EXTRACTED 1.00]
- **Data acquisition pipeline** — docs_research_01_concepts_datasource, docs_research_01_concepts_datapoint, docs_research_01_concepts_eventdetector, docs_research_01_concepts_publisher, docs_research_01_concepts_storage [EXTRACTED 1.00]
- **Alarm chain: detector, event, handler** — docs_research_01_concepts_eventdetector, docs_research_01_concepts_event, docs_research_01_concepts_eventhandler, docs_research_01_concepts_alarmlevels [EXTRACTED 1.00]
- **Studied permissions to DurianDB auth** — docs_research_02_observed_access_model_objectpermissions, docs_research_05_design_mapping_permissionsroles, durian_auth_can [INFERRED 0.75]
- **Detector observation to design to core** — docs_research_04_observed_mqtt_and_detectors_detectorduration, docs_research_05_design_mapping_detectors, durian_core_engine [INFERRED 0.75]
- **HTTP and SQL pull-source safety choices** — docs_research_08_http_and_sql_sources_tlsverify, docs_research_08_http_and_sql_sources_noredirects, docs_research_08_http_and_sql_sources_maxbytes, docs_research_08_http_and_sql_sources_sqlitereadonly, docs_research_08_http_and_sql_sources_selectonly [INFERRED 0.75]
- **Modbus read flow: framing, batching, decoding** — docs_research_07_modbus_framing, docs_research_07_modbus_batching, docs_research_07_modbus_byteword, docs_research_07_modbus_scaling [INFERRED 0.75]
- **Failure handling: background poll, back-off, partial rejection** — docs_research_07_modbus_backgroundpoll, docs_research_07_modbus_backoff, docs_research_08_http_and_sql_sources_pullbackoff [INFERRED 0.75]

## Communities (25 total, 6 thin omitted)

### Community 0 - "App API Layer"
Cohesion: 0.05
Nodes (21): ApiError, App, need(), Auth, AuthError, can(), check_password_strength(), hash_password() (+13 more)

### Community 1 - "Pull Sources and Safety Choices"
Cohesion: 0.06
Nodes (38): LDAP authentication module, Unit ids 1 to 247, Note 08: HTTP JSON and SQL sources, JSON path lookup (sensors.temp, items[0].value), numbers and booleans only, HTTP response size cap (max_bytes, 1 MB), HTTP redirects never followed, Pull-source failure back-off and partial rejection, HTTP source read-only (no write URL) (+30 more)

### Community 2 - "Config and Source Registry"
Cohesion: 0.06
Nodes (16): Config as JSON in one table, edited in console, Source types registry (virtual, mqtt), build_engine(), Virtual, Detector, Engine, Event, HighLimit (+8 more)

### Community 3 - "Outputs and MQTT Source"
Cohesion: 0.08
Nodes (12): Publisher, MQTT TLS requires CA, client cert and key for ssl:// (mutual TLS), Event handlers and publishers are separate concepts, MQTT TLS: always verify server, optional client cert, unknown topics auto_create, load_creds(), Level, MqttSource, parse() (+4 more)

### Community 4 - "Console Frontend"
Cohesion: 0.15
Nodes (31): api(), draw(), esc(), every(), firstTemplate(), fmt(), isAdmin(), isUser() (+23 more)

### Community 5 - "Modbus Polling Behavior"
Cohesion: 0.10
Nodes (13): Polling in background thread, Failure back-off period x scale^failures, capped, Register read batching/merging, Contiguous-only batching by default, Note 07: Modbus studied behavior and driver, Modbus exception responses raised, not retried, Online monitor points per device, Scaling raw*multiplier+additive, reversed on write (+5 more)

### Community 6 - "Clean-Room Policy"
Cohesion: 0.10
Nodes (24): Allowed sources (public docs, Swagger, release notes), Clean-room policy, Forbidden sources (Mango source, decompiled binaries, module jars), Own decisions where docs are silent, Radix IoT EULA (ma-core-public LICENSE), Lesson: backup and purge schedules are part of time-series feature, Lesson: data loss deserves its own system alarm, Note 06: enterprise vs regular edition (+16 more)

### Community 8 - "Modbus Simulator"
Cohesion: 0.13
Nodes (7): Device, frame_response(), handle(), _Handler, read_request(), _recv(), Slave

### Community 9 - "Browser Check Tools"
Cohesion: 0.13
Nodes (19): ev(), go(), pending, problems, send(), shot(), sleep(), summary() (+11 more)

### Community 10 - "Modbus Framing"
Cohesion: 0.15
Nodes (8): Modbus framing: TCP MBAP, RTU CRC-16, ASCII LRC, Serial through pyserial including socket:// URL, AsciiFramer, crc16(), lrc(), _rtu_rest(), RtuFramer, TcpFramer

### Community 11 - "Studied Concepts"
Cohesion: 0.16
Nodes (16): Data point, Data source, Point locator, Rollups, Tags, Watch list, Observed: clean lab instance (core 5.7.8), Data point resource shape (+8 more)

### Community 12 - "MQTT Lab Script"
Cohesion: 0.23
Nodes (3): main(), name(), pem_key()

### Community 13 - "Modbus Errors"
Cohesion: 0.22
Nodes (4): ModbusError, ModbusIOError, ModbusTimeout, TcpLink

### Community 14 - "Design Mapping"
Cohesion: 0.18
Nodes (12): Create sources disabled first, then enable, Logging tolerance and recent-value cache, Design mapping: studied behavior to DurianDB, Audit trail and set-point permission, Lists hide, single objects refuse (404), Logging mode, tolerance, bounded cache of 100 samples, Generated admin password printed once, Not implemented: protocol drivers, scripting, hierarchy, reports, purge, clustering (+4 more)

### Community 15 - "Modbus Codec"
Cohesion: 0.23
Nodes (6): byte_order and word_order switches, Studied product Modbus behavior (transports, defaults, batching, back-off), decode(), encode(), ModbusException, check_client()

### Community 17 - "Detector Research"
Cohesion: 0.22
Nodes (11): Event detector, High limit detector, Observed: MQTT source, virtual points, detectors, Detector creation needs type, source id, alarm level, limit, duration, empty handlers, Detector duration: condition must hold N before firing, Return to normal (rtnApplicable): alarms have start and end, Data source and detector type id registries, Unknown topic is an event, not a new point (+3 more)

### Community 18 - "Core Concepts Note"
Cohesion: 0.22
Nodes (8): Alarm levels (none to life safety), Data flow, Event / alarm, Event handler, Extension model (Java modules, REST, RQL), Maintenance events, Operations (safe mode, metrics, backups), Scripting (JavaScript, meta points)

### Community 20 - "Access Model Research"
Cohesion: 0.29
Nodes (7): Observed: access model of the REST API, Lists hide unseen items, single objects refuse, Per-object read/edit/set role lists, Role inheritance chain (user inherits anonymous), Error bodies with mangoStatusCode and localizedMessage, Stable xid external id next to numeric id, Empty permission lists mean superadmin only

### Community 21 - "Storage Design"
Cohesion: 0.33
Nodes (5): Logging modes, Relational config DB (H2/MySQL/PostgreSQL/MSSQL), Storage, Time-series history store (IasTSDB), Rollups avg/min/max/last/count via one SQL query

## Knowledge Gaps
- **25 isolated node(s):** `Radix IoT EULA (ma-core-public LICENSE)`, `Allowed sources (public docs, Swagger, release notes)`, `Forbidden sources (Mango source, decompiled binaries, module jars)`, `Own decisions where docs are silent`, `Point locator` (+20 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 179 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Store` connect `Store and Rollups` to `App API Layer`, `Storage Design`, `Design Mapping`, `Clean-Room Policy`?**
  _High betweenness centrality (0.099) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `Store` (e.g. with `Rollups avg/min/max/last/count via one SQL query` and `Tags and watch lists (tag match, names, or both; no hierarchy)`) actually correct?**
  _`Store` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Radix IoT EULA (ma-core-public LICENSE)`, `Allowed sources (public docs, Swagger, release notes)`, `Forbidden sources (Mango source, decompiled binaries, module jars)` to the rest of the system?**
  _25 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `App API Layer` be split into smaller, more focused modules?**
  _Cohesion score 0.05030525030525031 - nodes in this community are weakly interconnected._
- **Why does `Design mapping: studied behavior to DurianDB` connect `Design Mapping` to `Config and Source Registry`, `Outputs and MQTT Source`, `Clean-Room Policy`, `Studied Concepts`, `Detector Research`, `Access Model Research`, `Storage Design`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `App` (e.g. with `Audit trail and set-point permission` and `Lists hide, single objects refuse (404)`) actually correct?**
  _`App` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Should `Pull Sources and Safety Choices` be split into smaller, more focused modules?**
  _Cohesion score 0.05711849957374254 - nodes in this community are weakly interconnected._