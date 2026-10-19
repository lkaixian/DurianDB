# 01 Core concepts (from public docs)

Researched 2026-10-19. Paraphrased. Confidence is high where the docs state it, low where marked inferred.

Sources: [docs home](https://docs.radixiot.com/), [data sources](https://docs.radixiot.com/docs/data-sources/), [data points](https://docs.radixiot.com/docs/user-guide/data-points/), [events](https://docs.radixiot.com/docs/user-guide/events/), [configuration](https://docs.radixiot.com/docs/configuration/), [database](https://docs.radixiot.com/docs/administration/database/), [developers](https://docs.radixiot.com/docs/developers/).

## Data flow

device -> data source -> data point -> (history store, event detectors, publishers, scripts) -> UI / REST.

## Data source

- A connector to a protocol or system. Families: protocols (Modbus, BACnet, OPC UA/DA, SNMP, MQTT), data (SQL, HTTP, file, POP3 email), network (TCP, SSH), system (virtual, internal, meta, scripting), specialized (eGauge, Haystack, Z-Wave, ...).
- Owns its points. Each point belongs to exactly one data source, which decides how and when values are collected.
- A point carries a "locator", the protocol-specific address of the value.
- Not documented on the pages read: polling vs event-driven split, update period, quantization. (open question)

## Data point

- Types: numeric, binary, multistate, alphanumeric, image. Type limits which detectors, renderers and rollups apply.
- Logging modes: all updates, on change, at intervals, never. Plus purge and cache-size settings.
- Tags: key/value metadata for grouping, queries and watch lists.
- Permissions: separate read and set (write) access.
- Bulk import/export via CSV.
- Rollups depend on type, with per-point defaults. Methods not described in the docs read.

## Events and alarms

- Event detector: a rule on a point (or other source) that raises an event. Listed: high limit, low limit, state, change, no change, no update, range, rate of change, smoothness, CUSUM, and others.
- Alarm levels, low to high: none, information, important, warning, urgent, critical, life safety.
- Event handler: reacts to an event. Listed: email, set point, process, script.
- Maintenance events can suppress or auto-acknowledge events for chosen sources, points or watch lists.
- Not documented on the page read: how an event clears, and exact acknowledgement semantics. (open question)

## Publishers

Push point values outward: HTTP sender, MQTT Sparkplug, gRPC, Slack. Share queue management and snapshot scheduling. Community forum posts say a publisher has a connection-lost event that data sources lack (low confidence, forum source).

## Watch lists

Three kinds: static, query-based, hierarchy-based. Used for live monitoring, charts and comparison.

## Scripting

JavaScript for meta data points (computed points), scripting data sources and global helpers. Also a process event handler for external commands.

## Storage

- Relational DB holds configuration, users, point definitions, events. H2 default; MySQL, PostgreSQL, MSSQL as alternatives.
- Point history sits in a separate time-series store (an embedded NoSQL module, "IasTSDB") with write-behind batching, purge and shards.
- Optional persistent point-value cache for faster data-source start-up.
- Inferred design: split config store from high-volume history store.

## Extension model

Java modules can add data sources, REST endpoints, DB tables and UI pages. REST API is Swagger-described, session auth with CSRF, RQL query filtering.

## Operations

Safe mode, internal metrics, thread management, backups, SQL console, properties resolved from file, environment and JVM properties.
