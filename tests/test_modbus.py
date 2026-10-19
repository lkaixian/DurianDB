"""Modbus protocol, codec, batching and client checks against the simulator. Run: python tests/test_modbus.py"""
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from durian.modbus import (ModbusClient, ModbusException, ModbusIOError, ModbusTimeout, SerialLink, TcpLink, TYPES,  # noqa: E402
                           crc16, decode, encode, lrc, plan_batches, READ_COILS, READ_DISCRETE, READ_HOLDING, READ_INPUT)
from durian.modbus_sim import Slave  # noqa: E402

# ---- checksums against published examples
assert crc16(bytes.fromhex("010300000 00A".replace(" ", ""))) == 0xCDC5          # sent as C5 CD
assert struct.pack("<H", crc16(bytes.fromhex("0104 02FFFF".replace(" ", "")))) == bytes.fromhex("B880")
assert lrc(bytes.fromhex("010300000001")) == 0xFB

# ---- codec: every type, every order, round trip; and known byte layouts
samples = {"uint16": 54321, "int16": -1234, "uint32": 3_000_000_000, "int32": -123456789, "uint64": 2 ** 63 + 5,
           "int64": -2 ** 40, "float32": 1.5, "float64": -2.25}
for t, v in samples.items():
    for bo in ("big", "little"):
        for wo in ("big", "little"):
            assert decode(encode(v, t, bo, wo), t, bo, wo) == v, (t, bo, wo)
assert encode(0x12345678, "uint32") == [0x1234, 0x5678]
assert encode(0x12345678, "uint32", word_order="little") == [0x5678, 0x1234]
assert encode(0x12345678, "uint32", byte_order="little") == [0x3412, 0x7856]
assert encode(1.0, "float32") == [0x3F80, 0x0000]
assert decode([0x3F80, 0x0000], "float32") == 1.0
for bad in (("uint16", 70000), ("uint16", -1), ("int16", 40000), ("uint16", 1.5)):
    try:
        encode(bad[1], bad[0])
        raise AssertionError(bad)
    except ValueError:
        pass

# ---- batching
items = [("a", 0, 1), ("b", 1, 2), ("c", 3, 1), ("d", 10, 1), ("e", 200, 1)]
assert [(s, n, [k for k, *_ in m]) for s, n, m in plan_batches(items, 125, True)] == [(0, 4, ["a", "b", "c"]), (10, 1, ["d"]), (200, 1, ["e"])]
assert [(s, n) for s, n, _ in plan_batches(items, 125, False)] == [(0, 11), (200, 1)]          # gap of 6 is bridged, gap of 189 is not
assert [(s, n) for s, n, _ in plan_batches([("a", 0, 1), ("b", 100, 1)], 60, False)] == [(0, 1), (100, 1)]
assert [(s, n) for s, n, _ in plan_batches([(i, i, 1) for i in range(10)], 4, True)] == [(0, 4), (4, 4), (8, 2)]   # count cap splits
assert [(s, n) for s, n, _ in plan_batches([("a", 0, 4), ("b", 2, 4)], 125, True)] == [(0, 6)]                    # overlap merges


# ---- client against the simulator, in every framing
def check_client(client, srv, unit=1):
    dev = srv.devices[unit]
    dev.holding[10:13] = [111, 222, 333]
    dev.input[5] = 4242
    dev.coils[3], dev.discrete[7] = True, True
    assert client.read_regs(unit, READ_HOLDING, 10, 3) == [111, 222, 333]
    assert client.read_regs(unit, READ_INPUT, 5, 1) == [4242]
    assert client.read_bits(unit, READ_COILS, 0, 8) == [False, False, False, True, False, False, False, False]
    assert client.read_bits(unit, READ_DISCRETE, 5, 4) == [False, False, True, False]
    client.write_reg(unit, 20, 0xBEEF)
    client.write_regs(unit, 30, [1, 2, 3])
    client.write_coil(unit, 40, True)
    client.write_coils(unit, 50, [True, False, True, True, False, False, False, False, True])
    assert dev.holding[20] == 0xBEEF and dev.holding[30:33] == [1, 2, 3] and dev.coils[40]
    assert dev.coils[50:59] == [True, False, True, True, False, False, False, False, True]
    assert client.read_regs(unit, READ_HOLDING, 30, 3) == [1, 2, 3]
    try:
        client.read_regs(unit, READ_HOLDING, 990, 20)  # past the end of the map
        raise AssertionError("expected an exception response")
    except ModbusException as e:
        assert e.code == 2 and "illegal data address" in str(e)
    for bad in (lambda: client.read_regs(unit, READ_HOLDING, 0, 126), lambda: client.read_bits(unit, READ_COILS, 0, 2001),
                lambda: client.write_regs(unit, 0, [0] * 124)):
        try:
            bad()
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


for framing in ("tcp", "rtu", "ascii"):
    srv = Slave(framing).start()
    client = ModbusClient(TcpLink("127.0.0.1", srv.port, 0.5), framing)
    check_client(client, srv)
    check_client(client, srv, unit=2)  # a second device behind the same connection
    client.close()
    srv.stop()

# ---- RTU through pyserial, using its socket:// URL in place of a COM port
srv = Slave("rtu").start()
client = ModbusClient(SerialLink(f"socket://127.0.0.1:{srv.port}", 9600, 8, "N", 1, 0.5), "rtu")
check_client(client, srv)
client.close()
srv.stop()

# ---- failures: silent unit times out after the retries, dropped connection recovers, dead host raises
srv = Slave("tcp").start()
client = ModbusClient(TcpLink("127.0.0.1", srv.port, 0.2), "tcp", retries=1)
t0 = time.time()
try:
    client.read_regs(99, READ_HOLDING, 0, 1)
    raise AssertionError("expected timeout")
except ModbusTimeout:
    pass
assert 0.35 < time.time() - t0 < 1.5 and len([r for r in srv.requests if r[0] == 99]) == 2  # first try plus one retry
srv.devices[1].holding[0] = 7
srv.drop_next = 1  # the server hangs up once; the client reconnects and retries
assert client.read_regs(1, READ_HOLDING, 0, 1) == [7]
assert client.read_regs(1, READ_HOLDING, 0, 1) == [7]
client.close()
srv.stop()
dead = ModbusClient(TcpLink("127.0.0.1", srv.port, 0.2), "tcp", retries=1)
try:
    dead.read_regs(1, READ_HOLDING, 0, 1)
    raise AssertionError("expected a connection failure")
except (ModbusIOError, ModbusTimeout):
    pass
print("ok")
