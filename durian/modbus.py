"""Modbus client: framing (TCP, RTU, ASCII), function codes, value codec, and read batching.

Written from the public "Modbus Application Protocol Specification V1.1b3" and "Modbus over Serial Line V1.02",
and from observed behavior of the studied product (docs/research/07). Not derived from its code.
"""
import socket
import struct
import threading

READ_COILS, READ_DISCRETE, READ_HOLDING, READ_INPUT = 1, 2, 3, 4
WRITE_COIL, WRITE_REG, WRITE_COILS, WRITE_REGS = 5, 6, 15, 16
EXCEPTIONS = {1: "illegal function", 2: "illegal data address", 3: "illegal data value", 4: "server device failure",
              5: "acknowledge", 6: "server device busy", 8: "memory parity error", 10: "gateway path unavailable",
              11: "gateway target device failed to respond"}
LIMITS = {"read_bits": 2000, "read_regs": 125, "write_coils": 1968, "write_regs": 123}  # from the specification
RANGES = {"coil": READ_COILS, "discrete": READ_DISCRETE, "holding": READ_HOLDING, "input": READ_INPUT}


class ModbusError(Exception):
    """Base class. Transport problems are retried; ModbusException (device said no) is not."""


class ModbusTimeout(ModbusError):
    pass


class ModbusIOError(ModbusError):
    pass


class ModbusException(ModbusError):
    def __init__(self, code):
        super().__init__(EXCEPTIONS.get(code, f"exception {code}"))
        self.code = code


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def lrc(data: bytes) -> int:
    return (-sum(data)) & 0xFF


# ---- links: move bytes, nothing else
class TcpLink:
    def __init__(self, host, port, timeout=0.5):
        self.host, self.port, self.timeout, self.sock = host, port, timeout, None

    is_open = property(lambda self: self.sock is not None)

    def open(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), self.timeout)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except socket.timeout as e:
            raise ModbusTimeout(f"connect to {self.host}:{self.port} timed out") from e
        except OSError as e:
            raise ModbusIOError(f"connect to {self.host}:{self.port} failed: {e}") from e

    def send(self, data):
        if self.sock is None:
            raise ModbusIOError("link is closed")
        try:
            self.sock.sendall(data)
        except OSError as e:
            raise ModbusIOError(f"send failed: {e}") from e

    def recv_exact(self, n):
        buf = b""
        if self.sock is None:
            raise ModbusIOError("link is closed")
        try:
            while len(buf) < n:
                chunk = self.sock.recv(n - len(buf))
                if not chunk:
                    raise ModbusIOError("connection closed by device")
                buf += chunk
        except socket.timeout as e:
            raise ModbusTimeout("no response within the timeout") from e
        except OSError as e:
            raise ModbusIOError(f"receive failed: {e}") from e
        return buf

    def flush(self):  # discard bytes left over from an earlier, abandoned request
        self.sock.setblocking(False)
        try:
            while self.sock.recv(4096):
                pass
        except (BlockingIOError, OSError):
            pass
        finally:
            self.sock.settimeout(self.timeout)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None


class SerialLink:
    """pyserial link. `port` may be a device name (COM3, /dev/ttyUSB0) or a URL such as socket://host:port."""

    def __init__(self, port, baudrate=9600, bytesize=8, parity="N", stopbits=1, timeout=0.5):
        self.args = dict(baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits, timeout=timeout)
        self.port, self.ser = port, None

    is_open = property(lambda self: self.ser is not None)

    def open(self):
        import serial  # imported late so TCP-only installs do not need pyserial
        try:
            self.ser = serial.serial_for_url(self.port, **self.args)
        except (OSError, ValueError, serial.SerialException) as e:
            raise ModbusIOError(f"cannot open {self.port}: {e}") from e

    def send(self, data):
        try:
            self.ser.write(data)
            self.ser.flush()
        except Exception as e:
            raise ModbusIOError(f"write failed: {e}") from e

    def recv_exact(self, n):
        try:
            buf = self.ser.read(n)
        except Exception as e:
            raise ModbusIOError(f"read failed: {e}") from e
        if len(buf) < n:
            raise ModbusTimeout("no response within the timeout")
        return buf

    def flush(self):
        self.ser.reset_input_buffer()

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            finally:
                self.ser = None


# ---- framers: wrap a PDU for the wire and unwrap the answer
class TcpFramer:
    """MBAP header: transaction id, protocol id 0, length, then unit id and PDU."""

    def __init__(self):
        self.tid = 0

    def request(self, link, unit, pdu):
        self.tid = (self.tid + 1) & 0xFFFF
        link.send(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, unit) + pdu)
        tid, proto, length, u = struct.unpack(">HHHB", link.recv_exact(7))
        if proto != 0 or not 2 <= length <= 254:
            raise ModbusIOError("bad MBAP header")
        body = link.recv_exact(length - 1)
        if tid != self.tid or u != unit:
            raise ModbusIOError("response does not match the request")
        return body


def _rtu_rest(head: bytes) -> int:
    """Bytes still to read after unit and function, including the 2 CRC bytes (the data length is implied by the function)."""
    fc = head[1]
    if fc & 0x80:
        return 1 + 2
    if fc in (READ_COILS, READ_DISCRETE, READ_HOLDING, READ_INPUT):
        return -1  # byte count comes next
    return 4 + 2


class RtuFramer:
    """Unit, PDU, CRC-16 (low byte first). Used on serial lines, and over TCP for Ethernet-to-serial converters."""

    def request(self, link, unit, pdu):
        body = bytes([unit]) + pdu
        link.send(body + struct.pack("<H", crc16(body)))
        head = link.recv_exact(2)
        rest = _rtu_rest(head)
        if rest < 0:
            count = link.recv_exact(1)
            head, rest = head + count, count[0] + 2
        frame = head + link.recv_exact(rest)
        if crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
            raise ModbusIOError("CRC error in response")
        if frame[0] != unit:
            raise ModbusIOError("response from another unit")
        return frame[1:-2]


class AsciiFramer:
    """':' + hex(unit, PDU, LRC) + CR LF."""

    def request(self, link, unit, pdu):
        body = bytes([unit]) + pdu
        link.send(b":" + (body + bytes([lrc(body)])).hex().upper().encode() + b"\r\n")
        buf = b""
        while not buf.endswith(b"\n"):
            buf += link.recv_exact(1)
            if len(buf) > 513:
                raise ModbusIOError("ASCII frame too long")
        try:
            raw = bytes.fromhex(buf.strip().lstrip(b":").decode())
        except ValueError as e:
            raise ModbusIOError("bad ASCII frame") from e
        if len(raw) < 4 or lrc(raw[:-1]) != raw[-1]:
            raise ModbusIOError("LRC error in response")
        if raw[0] != unit:
            raise ModbusIOError("response from another unit")
        return raw[1:-1]


FRAMERS = {"tcp": TcpFramer, "rtu": RtuFramer, "ascii": AsciiFramer}


class ModbusClient:
    def __init__(self, link, framing="tcp", retries=2, persistent=True):
        self.link, self.framer, self.retries, self.persistent = link, FRAMERS[framing](), retries, persistent
        self.lock = threading.RLock()  # one request on the wire at a time; callers may hold it across a poll

    def call(self, unit, pdu):
        with self.lock:
            last = None
            for _ in range(self.retries + 1):
                try:
                    if not self.link.is_open:
                        self.link.open()
                    self.link.flush()
                    resp = self.framer.request(self.link, unit, pdu)
                except ModbusError as e:
                    last = e
                    self.link.close()  # reconnect on the next attempt
                    continue
                if resp[0] == pdu[0] | 0x80:
                    raise ModbusException(resp[1])  # the device answered; retrying will not change that
                if resp[0] != pdu[0]:
                    last = ModbusIOError("unexpected function in response")
                    self.link.close()
                    continue
                return resp
            raise last

    def end_cycle(self):
        if not self.persistent:
            self.link.close()

    def close(self):
        self.link.close()

    def read_bits(self, unit, fc, address, count):
        if not 1 <= count <= LIMITS["read_bits"]:
            raise ValueError("bit count out of range")
        r = self.call(unit, struct.pack(">BHH", fc, address, count))
        if len(r) != 2 + (count + 7) // 8 or r[1] != len(r) - 2:
            raise ModbusIOError("short or malformed read response")
        return [bool(r[2 + i // 8] >> (i % 8) & 1) for i in range(count)]

    def read_regs(self, unit, fc, address, count):
        if not 1 <= count <= LIMITS["read_regs"]:
            raise ValueError("register count out of range")
        r = self.call(unit, struct.pack(">BHH", fc, address, count))
        if len(r) != 2 + 2 * count or r[1] != 2 * count:
            raise ModbusIOError("short or malformed read response")
        return list(struct.unpack(f">{count}H", r[2:]))

    def _echo(self, r, expect):
        if r[1:5] != expect:
            raise ModbusIOError("write was not acknowledged correctly")

    def write_coil(self, unit, address, on):
        r = self.call(unit, struct.pack(">BHH", WRITE_COIL, address, 0xFF00 if on else 0))
        self._echo(r, struct.pack(">HH", address, 0xFF00 if on else 0))

    def write_reg(self, unit, address, value):
        r = self.call(unit, struct.pack(">BHH", WRITE_REG, address, value))
        self._echo(r, struct.pack(">HH", address, value))

    def write_coils(self, unit, address, values):
        if not 1 <= len(values) <= LIMITS["write_coils"]:
            raise ValueError("coil count out of range")
        packed = bytearray((len(values) + 7) // 8)
        for i, v in enumerate(values):
            if v:
                packed[i // 8] |= 1 << (i % 8)
        r = self.call(unit, struct.pack(">BHHB", WRITE_COILS, address, len(values), len(packed)) + bytes(packed))
        self._echo(r, struct.pack(">HH", address, len(values)))

    def write_regs(self, unit, address, values):
        if not 1 <= len(values) <= LIMITS["write_regs"]:
            raise ValueError("register count out of range")
        r = self.call(unit, struct.pack(">BHHB", WRITE_REGS, address, len(values), 2 * len(values)) + struct.pack(f">{len(values)}H", *values))
        self._echo(r, struct.pack(">HH", address, len(values)))


# ---- value codec: registers <-> numbers. Wire order of each register is always big-endian (the standard).
TYPES = {"uint16": ("H", 1), "int16": ("h", 1), "uint32": ("I", 2), "int32": ("i", 2),
         "uint64": ("Q", 4), "int64": ("q", 4), "float32": ("f", 2), "float64": ("d", 4)}


def decode(regs, type_, byte_order="big", word_order="big"):
    """byte_order swaps the two bytes inside each register, word_order reverses the register sequence."""
    code, n = TYPES[type_]
    if len(regs) != n:
        raise ValueError(f"{type_} needs {n} register(s)")
    words = [((w & 0xFF) << 8) | (w >> 8) for w in regs] if byte_order == "little" else list(regs)
    if word_order == "little":
        words.reverse()
    return struct.unpack(">" + code, struct.pack(f">{n}H", *words))[0]


def encode(value, type_, byte_order="big", word_order="big"):
    code, n = TYPES[type_]
    if code in "fd":
        raw = struct.pack(">" + code, float(value))
    else:
        if not isinstance(value, int) and value != int(value):  # compare exactly: float() would blur 64-bit integers
            raise ValueError(f"{type_} needs a whole number")
        try:
            raw = struct.pack(">" + code, int(value))
        except struct.error as e:
            raise ValueError(f"{value} does not fit in {type_}") from e
    words = list(struct.unpack(f">{n}H", raw))
    if word_order == "little":
        words.reverse()
    return [((w & 0xFF) << 8) | (w >> 8) for w in words] if byte_order == "little" else words


# ---- batching: fewer, larger reads
MAX_GAP = 16  # registers we are willing to read and throw away to join two nearby points, when not contiguous-only


def plan_batches(items, max_count, contiguous_only=True):
    """items: [(key, address, count)] -> [(start, count, [(key, address, count), ...])]."""
    batches = []
    for item in sorted(items, key=lambda i: (i[1], i[2])):
        key, addr, cnt = item
        if batches:
            start, size, members = batches[-1]
            end = start + size
            gap = addr - end
            new_end = max(end, addr + cnt)
            if (gap <= 0 if contiguous_only else gap <= MAX_GAP) and new_end - start <= max_count:
                batches[-1] = (start, new_end - start, members + [item])
                continue
        batches.append((addr, cnt, [item]))
    return batches
