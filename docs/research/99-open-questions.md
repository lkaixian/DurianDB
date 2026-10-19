# Open questions

Answered so far: roles and permissions (02), resource shapes and defaults (03), MQTT client source and detector creation (04). See 05 for the mapping.

Still open, to study next through public docs and black-box observation:

- MQTT point locator: which fields map a topic and payload to a point (a create call returned a server error about a missing name).
- Polling model: update period, quantization, what happens when a poll overruns (default alarm level is known: urgent).
- Event lifecycle detail: duplicate handling modes (IGNORE, IGNORE_SAME_MESSAGE), event handler types and their settings.
- Rollups: delta and accumulator semantics, and how they behave with sparse data.
- Time-series store: batching, purge and shard behavior as documented.
- Modbus: the node scan, UDP transport, BCD and mod-10000 types, and how an overrunning poll is aborted and alarmed. A real serial port and a real device have not been tried.
- Other protocols in the studied product's list (BACnet, DNP3, OPC, SNMP, serial ASCII, 1-Wire) are unexamined beyond their type ids.
- Point hierarchy and user-owned watch lists.
- Scripting: meta points and the scripting data source.
- Enterprise edition: answered at the module and settings level in note 06. Still open: how the NoSQL store behaves under load, and how the persistent source and publisher pair negotiate.
