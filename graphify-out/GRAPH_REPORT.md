# Graph Report - DurianDB  (2026-10-19)

## Corpus Check
- Corpus is ~1,219 words - fits in a single context window. You may not need a graph.

## Summary
- 65 nodes · 98 edges · 7 communities
- Extraction: 83% EXTRACTED · 17% INFERRED · 0% AMBIGUOUS · INFERRED: 17 edges (avg confidence: 0.79)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Core Model Types
- Events and Detectors
- Clean-Room Policy
- Research Gaps and Extension
- Data Acquisition Concepts
- Storage Design
- Engine and Polling

## God Nodes (most connected - your core abstractions)
1. `Data point` - 9 edges
2. `Clean-room policy` - 8 edges
3. `Data flow` - 8 edges
4. `Engine` - 7 edges
5. `demo()` - 6 edges
6. `Event / alarm` - 6 edges
7. `Event` - 5 edges
8. `HighLimit` - 5 edges
9. `Point` - 5 edges
10. `Source` - 5 edges

## Surprising Connections (you probably didn't know these)
- `High limit detector` --conceptually_related_to--> `HighLimit`  [INFERRED]
  docs/research/01-concepts.md → durian/core.py
- `Data point` --conceptually_related_to--> `Point`  [INFERRED]
  docs/research/01-concepts.md → durian/core.py
- `Data source` --conceptually_related_to--> `Source`  [INFERRED]
  docs/research/01-concepts.md → durian/core.py
- `Data flow` --conceptually_related_to--> `Engine`  [INFERRED]
  docs/research/01-concepts.md → durian/core.py
- `durian/ original implementation` --conceptually_related_to--> `Engine`  [INFERRED]
  README.md → durian/core.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Data acquisition pipeline** — docs_research_01_concepts_datasource, docs_research_01_concepts_datapoint, docs_research_01_concepts_eventdetector, docs_research_01_concepts_publisher, docs_research_01_concepts_storage [EXTRACTED 1.00]
- **Alarm chain: detector, event, handler** — docs_research_01_concepts_eventdetector, docs_research_01_concepts_event, docs_research_01_concepts_eventhandler, docs_research_01_concepts_alarmlevels [EXTRACTED 1.00]
- **Clean-room policy rules** — docs_00_clean_room_policy_cleanroompolicy, docs_00_clean_room_policy_allowedsources, docs_00_clean_room_policy_forbiddensources, docs_00_clean_room_policy_ownedecisions [EXTRACTED 1.00]

## Communities (7 total, 0 thin omitted)

### Community 0 - "Core Model Types"
Cohesion: 0.16
Nodes (5): Alarm levels (none to life safety), Level, Logging, Point, Source

### Community 1 - "Events and Detectors"
Cohesion: 0.20
Nodes (7): Event / alarm, Event detector, Event handler, High limit detector, Scripting (JavaScript, meta points), Event, HighLimit

### Community 2 - "Clean-Room Policy"
Cohesion: 0.29
Nodes (6): Clean-room policy, Forbidden sources (Mango source, decompiled binaries, module jars), Radix IoT EULA (ma-core-public LICENSE), Operations (safe mode, metrics, backups), DurianDB, Mango (Radix IoT)

### Community 3 - "Research Gaps and Extension"
Cohesion: 0.24
Nodes (9): Allowed sources (public docs, Swagger, release notes), Own decisions where docs are silent, Extension model (Java modules, REST, RQL), Rollups, Open question: event clearing, acknowledgement, duplicate suppression, Open question: module development extension points, Open question: polling model, update period, quantization, failure handling, Open question: REST API resource model, RQL, websockets (+1 more)

### Community 4 - "Data Acquisition Concepts"
Cohesion: 0.36
Nodes (8): Data flow, Data point, Data source, Point locator, Maintenance events, Publisher, Tags, Watch list

### Community 5 - "Storage Design"
Cohesion: 0.33
Nodes (5): Logging modes, Relational config DB (H2/MySQL/PostgreSQL/MSSQL), Storage, Time-series history store (IasTSDB), Open question: NoSQL batching, purge, shard behavior

### Community 6 - "Engine and Polling"
Cohesion: 0.40
Nodes (3): demo(), Engine, durian/ original implementation

## Knowledge Gaps
- **5 isolated node(s):** `Radix IoT EULA (ma-core-public LICENSE)`, `Forbidden sources (Mango source, decompiled binaries, module jars)`, `Point locator`, `Relational config DB (H2/MySQL/PostgreSQL/MSSQL)`, `Operations (safe mode, metrics, backups)`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 15 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Data flow` connect `Data Acquisition Concepts` to `Events and Detectors`, `Clean-Room Policy`, `Storage Design`, `Engine and Polling`?**
  _High betweenness centrality (0.333) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `Data point` (e.g. with `Point` and `Watch list`) actually correct?**
  _`Data point` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Radix IoT EULA (ma-core-public LICENSE)`, `Forbidden sources (Mango source, decompiled binaries, module jars)`, `Point locator` to the rest of the system?**
  _5 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Why does `Engine` connect `Engine and Polling` to `Core Model Types`, `Events and Detectors`, `Data Acquisition Concepts`?**
  _High betweenness centrality (0.212) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `Engine` (e.g. with `Data flow` and `durian/ original implementation`) actually correct?**
  _`Engine` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Why does `Data point` connect `Data Acquisition Concepts` to `Core Model Types`, `Research Gaps and Extension`, `Storage Design`?**
  _High betweenness centrality (0.187) - this node is a cross-community bridge._