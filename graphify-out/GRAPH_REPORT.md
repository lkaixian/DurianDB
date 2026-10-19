# Graph Report - DurianDB  (2026-10-19)

## Corpus Check
- Corpus is ~13,898 words - fits in a single context window. You may not need a graph.

## Summary
- 359 nodes · 712 edges · 18 communities (16 shown, 2 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 82 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Config, Sources and MQTT TLS
- HTTP Server and Seed
- Core Engine and Detectors
- Console Frontend
- App API Layer
- Auth and Sessions
- Store and Rollups
- Clean-Room Policy
- Studied Concepts
- MQTT Lab Script
- Detector Research
- Browser Check Tool
- Lab Instance Observations
- Design Mapping
- Open Questions
- Access Model Research
- Storage Design

## God Nodes (most connected - your core abstractions)
1. `Store` - 29 edges
2. `App` - 25 edges
3. `Design mapping: studied behavior to DurianDB` - 22 edges
4. `ApiError` - 14 edges
5. `build_engine()` - 13 edges
6. `Source` - 13 edges
7. `Observed: clean lab instance (core 5.7.8)` - 13 edges
8. `Auth` - 12 edges
9. `AuthError` - 11 edges
10. `Point` - 11 edges

## Surprising Connections (you probably didn't know these)
- `Audit trail and set-point permission` --conceptually_related_to--> `App`  [INFERRED]
  docs/research/05-design-mapping.md → durian/app.py
- `Lists hide, single objects refuse (404)` --conceptually_related_to--> `App`  [INFERRED]
  docs/research/05-design-mapping.md → durian/app.py
- `Console shell: nav, main, loads app.js and app.css` --conceptually_related_to--> `App`  [INFERRED]
  web/index.html → durian/app.py
- `Config as JSON in one table, edited in console` --conceptually_related_to--> `build_engine()`  [INFERRED]
  docs/research/05-design-mapping.md → durian/config.py
- `Known limits: config change rebuilds engine, latest MQTT value per poll, no purge` --conceptually_related_to--> `build_engine()`  [INFERRED]
  README.md → durian/config.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Clean-room policy rules** — docs_00_clean_room_policy_cleanroompolicy, docs_00_clean_room_policy_allowedsources, docs_00_clean_room_policy_forbiddensources, docs_00_clean_room_policy_ownedecisions [EXTRACTED 1.00]
- **Data acquisition pipeline** — docs_research_01_concepts_datasource, docs_research_01_concepts_datapoint, docs_research_01_concepts_eventdetector, docs_research_01_concepts_publisher, docs_research_01_concepts_storage [EXTRACTED 1.00]
- **Alarm chain: detector, event, handler** — docs_research_01_concepts_eventdetector, docs_research_01_concepts_event, docs_research_01_concepts_eventhandler, docs_research_01_concepts_alarmlevels [EXTRACTED 1.00]
- **Studied permissions to DurianDB auth** — docs_research_02_observed_access_model_objectpermissions, docs_research_05_design_mapping_permissionsroles, durian_auth_can [INFERRED 0.75]
- **Detector observation to design to core** — docs_research_04_observed_mqtt_and_detectors_detectorduration, docs_research_05_design_mapping_detectors, durian_core_engine [INFERRED 0.75]

## Communities (18 total, 2 thin omitted)

### Community 0 - "Config, Sources and MQTT TLS"
Cohesion: 0.07
Nodes (22): MQTT TLS requires CA, client cert and key for ssl:// (mutual TLS), MQTT TLS: always verify server, optional client cert, unknown topics auto_create, Source types registry (virtual, mqtt), build_engine(), load_creds(), make_point(), _num(), _roles_ok() (+14 more)

### Community 1 - "HTTP Server and Seed"
Cohesion: 0.06
Nodes (7): seed(), Handler, main(), run_ticks(), call(), call(), Hook

### Community 2 - "Core Engine and Detectors"
Cohesion: 0.07
Nodes (11): Detector, Engine, Event, HighLimit, _Latch, LowLimit, NoUpdate, RateOfChange (+3 more)

### Community 3 - "Console Frontend"
Cohesion: 0.17
Nodes (29): api(), draw(), esc(), every(), fmt(), isAdmin(), isUser(), KINDS (+21 more)

### Community 4 - "App API Layer"
Cohesion: 0.14
Nodes (7): ApiError, App, need(), check_password_strength(), Level, Outputs, validate_handler()

### Community 5 - "Auth and Sessions"
Cohesion: 0.13
Nodes (8): Generated admin password printed once, Auth, AuthError, can(), hash_password(), held(), verify_password(), fails()

### Community 7 - "Clean-Room Policy"
Cohesion: 0.13
Nodes (13): Allowed sources (public docs, Swagger, release notes), Clean-room policy, Forbidden sources (Mango source, decompiled binaries, module jars), Own decisions where docs are silent, Radix IoT EULA (ma-core-public LICENSE), Extension model (Java modules, REST, RQL), Operations (safe mode, metrics, backups), Educational independent reference, no Mango code or branding (+5 more)

### Community 8 - "Studied Concepts"
Cohesion: 0.18
Nodes (14): Alarm levels (none to life safety), Data flow, Data point, Event / alarm, Event handler, Point locator, Maintenance events, Publisher (+6 more)

### Community 9 - "MQTT Lab Script"
Cohesion: 0.23
Nodes (3): main(), name(), pem_key()

### Community 10 - "Detector Research"
Cohesion: 0.20
Nodes (12): Event detector, High limit detector, Observed: MQTT source, virtual points, detectors, Detector creation needs type, source id, alarm level, limit, duration, empty handlers, Detector duration: condition must hold N before firing, Return to normal (rtnApplicable): alarms have start and end, Data source and detector type id registries, Unknown topic is an event, not a new point (+4 more)

### Community 11 - "Browser Check Tool"
Cohesion: 0.24
Nodes (10): ev(), go(), pending, problems, send(), shot(), sleep(), summary() (+2 more)

### Community 12 - "Lab Instance Observations"
Cohesion: 0.22
Nodes (10): Data source, Observed: clean lab instance (core 5.7.8), Create sources disabled first, then enable, Data source resource shape and defaults, Event types with duplicate-handling mode, Internal data source: system observes itself with same machinery, Small core with capabilities from modules, POLL_ABORTED default alarm level URGENT (+2 more)

### Community 13 - "Design Mapping"
Cohesion: 0.22
Nodes (10): Logging tolerance and recent-value cache, Design mapping: studied behavior to DurianDB, Audit trail and set-point permission, Config as JSON in one table, edited in console, Lists hide, single objects refuse (404), Logging mode, tolerance, bounded cache of 100 samples, Not implemented: protocol drivers, scripting, hierarchy, reports, purge, clustering, Permissions: read/edit/set role lists with inheritance, default read role user (+2 more)

### Community 14 - "Open Questions"
Cohesion: 0.20
Nodes (10): MQTT client source fields and events, Open questions, Open: enterprise edition differences, Open: duplicate handling and handler types, Open: point hierarchy and user-owned watch lists, Open: MQTT point locator fields, Open: polling model and overrun, Open: delta and accumulator rollups on sparse data (+2 more)

### Community 15 - "Access Model Research"
Cohesion: 0.29
Nodes (7): Observed: access model of the REST API, Lists hide unseen items, single objects refuse, Per-object read/edit/set role lists, Role inheritance chain (user inherits anonymous), Error bodies with mangoStatusCode and localizedMessage, Stable xid external id next to numeric id, Empty permission lists mean superadmin only

### Community 16 - "Storage Design"
Cohesion: 0.33
Nodes (5): Logging modes, Relational config DB (H2/MySQL/PostgreSQL/MSSQL), Storage, Time-series history store (IasTSDB), Rollups avg/min/max/last/count via one SQL query

## Knowledge Gaps
- **28 isolated node(s):** `Radix IoT EULA (ma-core-public LICENSE)`, `Allowed sources (public docs, Swagger, release notes)`, `Forbidden sources (Mango source, decompiled binaries, module jars)`, `Own decisions where docs are silent`, `Point locator` (+23 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 123 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Design mapping: studied behavior to DurianDB` connect `Design Mapping` to `Config, Sources and MQTT TLS`, `Auth and Sessions`, `Clean-Room Policy`, `Studied Concepts`, `Detector Research`, `Lab Instance Observations`, `Open Questions`, `Access Model Research`, `Storage Design`?**
  _High betweenness centrality (0.163) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `Store` (e.g. with `Rollups avg/min/max/last/count via one SQL query` and `Tags and watch lists (tag match, names, or both; no hierarchy)`) actually correct?**
  _`Store` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Radix IoT EULA (ma-core-public LICENSE)`, `Allowed sources (public docs, Swagger, release notes)`, `Forbidden sources (Mango source, decompiled binaries, module jars)` to the rest of the system?**
  _28 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Config, Sources and MQTT TLS` be split into smaller, more focused modules?**
  _Cohesion score 0.06734006734006734 - nodes in this community are weakly interconnected._
- **Why does `Store` connect `Store and Rollups` to `Storage Design`, `HTTP Server and Seed`, `Auth and Sessions`, `Design Mapping`?**
  _High betweenness centrality (0.125) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `App` (e.g. with `Audit trail and set-point permission` and `Lists hide, single objects refuse (404)`) actually correct?**
  _`App` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Should `HTTP Server and Seed` be split into smaller, more focused modules?**
  _Cohesion score 0.06236786469344609 - nodes in this community are weakly interconnected._