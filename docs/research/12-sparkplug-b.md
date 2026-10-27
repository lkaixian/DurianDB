# 12 Sparkplug B: what the specification is, and what a dependency-free driver would look like

Written 2026-10-27 from the public Eclipse Sparkplug specification (sparkplug.eclipse.org) and public MQTT
documentation. No vendor source or jar was opened; Mango's own Sparkplug support was not studied. Where the
specification leaves a choice, this note says what DurianDB would choose and why.

This is the design note for the next input driver. Nothing here is implemented yet.

## What the specification is

Sparkplug B is a payload and topic convention for SCADA over MQTT, standardised by the Eclipse Foundation.
It exists to solve three problems a plain MQTT topic has: what a value *is*, when the values are current,
and whether a replayed message is stale.

- **Topic namespace**: `spBv1.0/<group>/<message type>/<edge node>[/<device>]`. Message types are `NBIRTH`,
  `NDEATH`, `NDATA`, `NCmd`, `STATE` for a node, and `DBIRTH`, `DDEATH`, `DDATA`, `DCmd` for a device.
- **Birth and death**: a node publishes `NBIRTH` with a sequence number and its full metric list; every
  `NDATA` after it carries only what changed. `NDEATH` is published through the MQTT Last Will, so the
  broker sends it when the session dies — that is the whole liveness mechanism, and it is why a Sparkplug
  source can report "down" faster and more honestly than a heartbeat timeout.
- **Sequence numbers**: a byte that wraps 0-255. A gap means messages were lost, and a receiver must not
  pretend otherwise.
- **State payload**: `STATE/<scada host>` carries a JSON payload (`online`, `offline`, `timestamp`) that a
  host publishes as its own Last Will, so edge nodes know whether the host is alive. Not every device reads
  it; DurianDB would publish it and not depend on it.
- **Payload encoding**: protobuf, message `Payload` with `timestamp`, `metrics[]`, `seq`, `uuid`, `body`.
  A metric carries `name`, `alias`, `timestamp`, `datatype`, `is_null`, and exactly one value field.

## The dependency problem, and the answer

The honest options for protobuf in a zero-dependency project:

1. **Add `protobuf` or `betterproto`.** A real dependency, a generated module, and a build step for a
   runtime that currently installs with `pip install -r requirements.txt` and nothing else.
2. **Hand-parse the wire format.** Protobuf's encoding is small: varints, length-delimited fields, fixed
   32/64-bit fields, and field numbers. For `Payload` and `Metric` it is perhaps 120 lines of reader and
    writer, with the field numbers written down once from the public specification.
3. **Speak only the subset that matters and refuse the rest by name.** Decode the fields listed above;
   any other field number is skipped by its wire type, which the format makes safe by construction.

DurianDB would take (2) and (3) together. The parser is the driver's own test surface: a round-trip test of
encode/decode covers it without a broker, and a skipped-unknown-field test proves a newer edge node does not
break the driver. If the specification changes shape, that is a version to support deliberately, not a
silent upgrade through a package.

## What the driver would be

`durian/sparkplug.py`, a `Source` subclass like the MQTT one (`durian/mqtt_source.py`) — the same TLS client,
the same certificate verification, the same `manages_error` and `connected` contract, so the source-down
machine and the failure model apply unchanged.

- `poll()` returns nothing; values arrive on the broker thread and are handed to the engine like the MQTT
  source's push path.
- One point per metric name, qualified by `<node>/<device>/<metric>` so two devices may use the same metric
  name without colliding. A point that disappears from a `DBIRTH` becomes BAD with the reason, not silently
  frozen — the same rule as a rejected Modbus register.
- `NBIRTH` and `DBIRTH` replace the metric set. A metric that is in the birth but not the data is unchanged,
  not stale — that is what sequence-numbered change sets mean, and treating it as stale would mark every
  quiet point BAD.
- Alarm states, quality, history and everything downstream are unchanged: Sparkplug is a way in, not a
  second data model.

### The failure cases that must be sentences

- A sequence gap: `sparkplug messages 12 and 15 are missing from plant/line1` — a lost message is data loss
  and belongs in the source's error, not a counter.
- A malformed payload: the metric is rejected by name; the source stays up.
- `NDEATH` or a will-triggered disconnect: the source-down alarm fires immediately, without waiting for
  `down_after`, because a Last Will is already the device's own statement that it is gone.
- A birth with a timestamp older than the values already held: the older birth does not overwrite newer
  values, and the source says it ignored a stale birth.

## What to build first

The parser and its round-trip test, with no broker. Then the birth/data/sequence handling against a
simulated publisher (a second client on the same codebase, as `modbus_sim.py` does for Modbus). Only then a
`STATE` publisher, because it is the part that a lab device would need to see before it sends data at all.

## Open questions

- Does the lab MQTT broker in `docs/research/03-observed-lab-instance.md` allow a Last Will with the client
  the driver uses? It must, for `NDEATH` to mean anything; worth confirming on the lab instance.
- Whether to support `alias` metrics (numeric aliases replacing names in data messages). It is a bandwidth
  feature; a driver that ignores aliases is correct but noisier on the wire. Decide when a real device
  offers them.
