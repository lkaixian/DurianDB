# 03 Observed: clean lab instance (Mango core 5.7.8)

Observed 2026-10-19 through the REST API of a clean local install, using an admin account the owner authorized for this purpose. Black-box only: requests and responses. Nothing inside the install files was read. Raw admin dumps are kept local and not committed because they can hold settings and user details.

## Platform shape

- Core 5.7.8 plus about 40 modules, each listed with name, version, vendor, description, dependencies and a signed flag. Capabilities come from modules: data sources (virtual, Modbus, BACnet, DNP3, SNMP, OPC DA, SQL, HTTP, SSH, serial, TCP/IP, POP3, MQTT client, scripting, meta, internal, and more), publishers (Slack, Modbus, BACnet, HTTP, Haystack), scripting engines (GraalJS, Nashorn, Groovy), watch lists, maintenance events, Excel reports, dashboard designer, SQL console.
- Lesson: the core is small and everything else plugs in through modules that declare dependencies. DurianDB models this as a registry of source types.
- Roles: `superadmin` inherits `user`, `user` inherits `anonymous`. Users hold a list of roles.
- A fresh install already runs one data source (internal, 25 points) that reports system metrics such as disk space. A system observes itself with the same machinery as devices.

## Data source resource

Fields: `xid`, `name`, `modelType`, `enabled`, `pollPeriod {periods, type}`, `useCron`, `quantize`, `eventAlarmLevels[]`, `purgeSettings {override, frequency}`, `readPermission[]`, `editPermission[]`, `lifecycleState`, `revision`.

Observed on create (POST `data-sources`, type VIRTUAL, disabled):
- Returns 201 with the full object, server-assigned `id`, and defaults filled in.
- Disabled sources report `lifecycleState: TERMINATED` and do not poll.
- Default alarm level for a poll that overruns its period (POLL_ABORTED) is URGENT.
- Default purge is one year. Permission lists default to empty, meaning only superadmin can see it. That is why a plain user got empty lists earlier.

## Data point resource

Fields: `xid`, `name`, `dataType` (numeric, binary, multistate, alphanumeric, image), `pointLocator` (type-specific address, with `settable`), `loggingProperties {loggingType, tolerance, discardExtremeValues, cacheSize}`, `textRenderer`, `rollup`, `unit`, `tags{}`, `readPermission[]`, `setPermission[]`, `purgeOverride`, `plotType`, `simplifyType`.

Lessons: logging mode has a tolerance (ignore small changes), a cache size keeps recent values in memory, rendering and rollup defaults live on the point.

## Event types

`DATA_SOURCE`, `DATA_POINT`, `PUBLISHER`, `SYSTEM` and others, each with optional subtype and reference ids, and a duplicate-handling mode (for example IGNORE).

## For DurianDB

1. Add a type registry for sources instead of hard-coding them.
2. Give points a logging tolerance, so jitter is not stored.
3. Make permissions per object, with roles that inherit.
4. Give every source default alarm levels for its own failures.
5. Create things disabled first, then enable.
