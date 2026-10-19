# Open questions

Answered so far: roles and permissions (02), resource shapes and defaults (03), MQTT client source and detector creation (04). See 05 for the mapping.

Still open, to study next through public docs and black-box observation:

- MQTT point locator: which fields map a topic and payload to a point (a create call returned a server error about a missing name).
- Polling model: update period, quantization, what happens when a poll overruns (default alarm level is known: urgent).
- Event lifecycle detail: duplicate handling modes (IGNORE, IGNORE_SAME_MESSAGE), event handler types and their settings.
- Rollups: delta and accumulator semantics, and how they behave with sparse data.
- Time-series store: batching, purge and shard behavior as documented.
- Point hierarchy and user-owned watch lists.
- Scripting: meta points and the scripting data source.
- Enterprise edition: which features differ (`app-en` has not been run).
