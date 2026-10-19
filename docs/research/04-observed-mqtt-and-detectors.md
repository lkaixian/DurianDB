# 04 Observed: MQTT client source, virtual points, event detectors

Observed 2026-10-19 on the clean lab instance (core 5.7.8) by making authorized REST calls and reading the instance's own validation errors. Public spec used for paths: https://radixiot.github.io/mango-code-site/5.7.8/restapi/openapi.yaml (it describes endpoints, not the module-specific shapes).

## Method worth copying

Validation is a teaching tool. A create call with a bad type id returns 400, and the runtime log lists the accepted ids. A well-formed but incomplete body returns 422 with one message per property (`property`, `message`, `level`). DurianDB should return the same: all problems at once, keyed by property.

## Type ids

- Data sources (29): virtual, internal, meta, scripting, Modbus IP/serial, BACnet IP/MS-TP, DNP3 IP/serial, OPC DA, SNMP, SQL, SSH, serial, TCP/IP, 1-Wire, ASCII file, data file, eGauge, Haystack, HTTP receiver/retriever (plain and JSON), Influx line protocol, POP3, vmstat, MQTT client.
- Event detectors (18): high limit, low limit, range, state change count, no change, no update, update, analog change, point change, rate of change, smoothness, positive/negative CUSUM, binary state, multistate state, multistate bit, alphanumeric state, alphanumeric regex state.
- Point locators are per source type and carry their own fields. The locator type id must match the source.

## Virtual source and points

- Source: poll period as `{periods, type}`. Created disabled.
- Point locator is flat: `changeType` (string, for example random analog), `min`, `max`, `startValue` (validated as numeric), plus unused knobs for other change types (`amplitude`, `period`, `phaseShift`, `offset`, `volatility`, `maxChange`).
- Logging: `ON_CHANGE` with a `tolerance`, or all values, with a `cacheSize`.

## Event detector

- Create call needs: `detectorType`, `sourceType` (data point), `sourceId` (numeric id, not xid), `alarmLevel`, and for limit detectors a `limit` and a `duration` given as `{periods, type}` (how long the condition must hold before it fires). Created with no handlers (`handlerXids: []`).
- `rtnApplicable: true`: an event can return to normal, so alarms have a start and an end.

## MQTT client source

- Fields: broker URI, topic filters (one string), QoS (three named levels, at least once, at most once, exactly once), client id, user name and password, clean session, keep-alive, connect timeout, reconnect delays (initial, maximum), TLS options (CA certificate, client certificate and private key as PEM text, flags to use them, verify hostname), and ignore parsing errors.
- With an `ssl://` broker URI the source requires all three PEM values, even to only verify the server. So it expects mutual TLS.
- Own event types: connection failure (urgent), publish failure, unknown topic (ignored by default), plus generic read, write and source exceptions.
- "Unknown topic" exists because every topic maps to a point defined in advance. Data for a topic nobody defined is an event, not a new point.
- Not resolved: the exact point-locator fields for a topic (a create attempt returned a server error about a missing name).

## For DurianDB

1. Detector duration (a condition must hold for N before an event fires) and return-to-normal events.
2. Report all validation errors at once, keyed by property.
3. Provide detector types as a registry: high limit, low limit, no update, rate of change first.
4. MQTT: keep optional auto-creation of points (our choice) but also support a mode where unknown topics raise an event.
5. MQTT TLS: always verify the server, and support a client certificate.
