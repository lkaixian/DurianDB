# 07 Modbus: studied behavior and the DurianDB driver

Written 2026-10-20. Sources: the public Modbus Application Protocol Specification V1.1b3 and Modbus over Serial Line V1.02 (the protocol), the vendor's public documentation page on its Modbus data source (the behavior), and defaults reported by the lab instance's own API when a disabled Modbus source and point were created (black-box). No vendor code was read. DurianDB's driver is written from the standards.

## What the studied product does

| Area | Observed or documented |
|---|---|
| Transports | TCP (new connection each poll), TCP with keep-alive (recommended), UDP, and "encapsulated" (RTU frames over TCP for Ethernet-to-serial converters). Serial: baud, data bits, stop bits, parity, flow control, echo, and RTU or ASCII encoding. |
| Poll defaults | Timeout 500 ms, 2 retries. Poll period default is 5 minutes (a real device needs a shorter one). |
| Batching | Points on the same device and range are merged into fewer reads. An option limits merging to contiguous addresses. Caps: 2000 bits, 125 registers per read, 120 per write. |
| Writes | A "multiple writes only" switch for devices that lack the single-write functions. Only coils and holding registers are writable. |
| Failure | A poll that overruns its period is aborted (an alarm, default urgent), the next poll keeps its original schedule. A dead link backs off: wait grows by a scale factor (default 1.5) up to a maximum (default 5 minutes). |
| Device health | Optional "device monitor" points show whether each device answered. |
| Stale data | A "discard data delay" removes buffered bytes older than a limit. |
| Point address | Device id (slave id), range (coil status, input status, input register, holding register), zero-based offset, data type, bit for packed flags, scaling as `raw * multiplier + additive` (writes reverse it). |
| Data types | 16, 32 and 64-bit integers (signed and unsigned) and 32 and 64-bit floats, each with byte or word swapped variants, plus BCD and "mod 10000" variants (names not documented). |
| Tools | A node scan (function 7 to ids 1 to 240), a raw read and write tool, and I/O logging of every message. |

## What DurianDB implements

`durian/modbus.py` (protocol), `durian/modbus_source.py` (the source), `durian/modbus_sim.py` (a slave for tests).

- Function codes 1, 2, 3, 4 (reads) and 5, 6, 15, 16 (writes). Exception responses are raised as errors and not retried, because retrying cannot change the device's answer.
- Framing: TCP (MBAP header with transaction id check), RTU (CRC-16, response length derived from the function code), ASCII (LRC). RTU and ASCII run over a real serial port or over a TCP socket, so converters work too.
- Serial through pyserial, which also accepts `socket://host:port`; that is how RTU framing is tested without hardware.
- Value types `uint16 int16 uint32 int32 uint64 int64 float32 float64 bool`. Two independent switches replace the vendor's swapped-variant names: `byte_order` (bytes inside each register) and `word_order` (registers within a value). Four combinations cover every variant.
- Batching merges nearby registers; contiguous-only is the default (the safe choice, since reading an unmapped register can make some devices refuse the whole read). With it off, gaps up to 16 registers are bridged.
- Polling runs in a background thread so a slow device never blocks the API. Back-off grows as period x scale^failures, capped. A device that rejects one read only loses those points.
- Writes: scaling reversed, rounding for integer types, range checked against the type, coil and holding register only, single or multiple function by option.
- Online monitor points per device.

## Differences from the studied product, on purpose

- Contiguous-only reads by default.
- `byte_order` and `word_order` instead of named swapped types.
- Unit ids 1 to 247 (the specification's range), the product documents 1 to 240.
- No UDP, no echo flag, no I/O log file, no node scan, no BCD or mod-10000 types, no per-write function type, no abort alarm for an overrunning poll.

## Checked

`tests/test_modbus.py`: published CRC and LRC vectors, every type in every order, batching rules, all three framings against the simulator, RTU through pyserial, exceptions, timeouts with retry, and reconnect after the server hangs up. `tests/test_modbus_source.py`: config validation, decoding of every point kind, merging without changing values, scaled and coil writes, a refused read, back-off growth and cap, recovery, and RTU and ASCII through a serial URL. `tools/browser_modbus.mjs`: the workflow in a real browser.

Not checked: a real serial port, a real device, UDP (not implemented), and timing at scale.
