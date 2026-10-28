"""DNP3 over TCP: a read-only master that polls an outstation's static values and event buffers.

DNP3 is the protocol most substations speak, and its link layer is small enough to build with the
standard library: a frame is a header with a CRC-16/DNP, then up to 16 bytes of user data each with
its own CRC. This module is the master side only. The first successful poll reads class 0 (the
outstation's static values); later polls read the class 1, 2 and 3 event buffers. Nothing is written,
selected or operated, so a value can be trusted to reflect the device and not to change it.

A point names a group and an object index: `dnp3.group` is 1 (binary input), 10 (binary output
status), 20 (counter), 30 (analog input) or 40 (analog output status), and `dnp3.index` is the
object's index in that group. Event objects (groups 2, 11, 22, 32 and 42) update the point whose
static group they belong to, so an analog event at group 32 index 5 updates the point configured as
group 30 index 5.

The DNP3 flags byte decides quality: an offline or communication-lost point is BAD, a restart or a
forced value is UNCERTAIN, and a device timestamp, when the variation carries one, becomes the
sample's time. Every failure is a sentence for the operator, and a failed poll backs off and marks
the source down the way the other polled sources do. Work is bounded: response bytes, transport
segments, objects and events per tick all have caps. An object group this driver does not read
cannot be skipped by a byte count, because DNP3 carries no per-object length, so the rest of that
reply is skipped and named in the source's error rather than guessed at.
"""
import math
import socket
import struct
import time

from .core import Quality, sentence
from .polled import PolledSource

START = b"\x05\x64"
MASTER_ADDRESS = 1  # this master's link address; an outstation usually expects a fixed one
MAX_APP = 65_535  # bytes in one reassembled answer
MAX_FRAGMENTS = 64  # application fragments one answer may use
MAX_SEGMENTS = 256  # transport segments one fragment may use
MAX_FRAMES = 512  # link frames read while waiting for a reply
MAX_OBJECTS = 10_000  # objects decoded from one reply
EVENT_LIMIT = 1000  # events applied from one tick, so a noisy outstation cannot hold the tick open

RESPONSE = 0x81
UNSOLICITED = 0x82
READ = 0x01

ONLINE = 0x01
RESTART = 0x02
COMM_LOST = 0x04
REMOTE_FORCED = 0x08
LOCAL_FORCED = 0x10
STATE = 0x80

# (kind, object bytes, carries a 6-byte device time)
_BINARY = {1: ("bit", 1, False), 2: ("bit", 7, True)}
_COUNTER = {1: ("uint32", 5, False), 2: ("uint16", 3, False),
            5: ("uint32", 11, True), 6: ("uint16", 9, True)}
_COUNTER_EVENT = {**_COUNTER, 3: ("uint32", 5, False), 4: ("uint16", 3, False),
                  7: ("uint32", 11, True), 8: ("uint16", 9, True)}
_ANALOG = {1: ("int32", 5, False), 2: ("int16", 3, False), 3: ("int32", 11, True), 4: ("int16", 9, True),
           5: ("float", 5, False), 6: ("double", 9, False)}
_ANALOG_EVENT = {**_ANALOG, 7: ("float", 11, True), 8: ("double", 15, True)}

LAYOUTS = {1: _BINARY, 2: _BINARY, 10: _BINARY, 11: _BINARY,
           20: _COUNTER, 22: _COUNTER_EVENT, 30: _ANALOG, 32: _ANALOG_EVENT,
           40: _ANALOG, 42: _ANALOG_EVENT}
POINT_GROUPS = (1, 10, 20, 30, 40)
EVENT_GROUPS = {2: 1, 11: 10, 22: 20, 32: 30, 42: 40}


class Dnp3Error(ValueError):
    """A request or reply that cannot be built or understood. The message is a sentence for the operator."""


def crc16(data):
    """CRC-16/DNP: reflected, polynomial 0x3D65, initial zero, final complement. Check value 0xEA82."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA6BC if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def parse_address(text):
    """(host, port) from 'host:port'. Raises Dnp3Error with a sentence when it is not one."""
    if not isinstance(text, str):
        raise Dnp3Error("give dnp3.address as host:port, such as 10.0.0.5:20000")
    host, _, port = text.strip().rpartition(":")
    if not host or len(host) > 253 or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise Dnp3Error("give dnp3.address as host:port, such as 10.0.0.5:20000")
    return host, int(port)


def frame(control, dest, src, user_data):
    """One complete link frame: header with its CRC, then 16-byte user blocks each with their CRC.

    The length byte counts the control, the two addresses and the user data, but not any CRC, so a
    frame carries at most 250 user bytes.
    """
    if len(user_data) > 250:
        raise Dnp3Error("a DNP3 link frame cannot carry more than 250 bytes of user data")
    header = START + bytes([len(user_data) + 5, control]) + struct.pack("<HH", dest, src)
    out = header + struct.pack("<H", crc16(header))
    for i in range(0, len(user_data), 16):
        block = user_data[i:i + 16]
        out += block + struct.pack("<H", crc16(block))
    return out


def _recv_exact(sock, n, what):
    """Exactly n bytes, or a sentence naming what was still arriving when the peer closed."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise Dnp3Error(f"the device closed the connection while {what} was still arriving; check the outstation")
        buf += chunk
    return bytes(buf)


def read_frame(sock):
    """(control, dest, src, user data) from one link frame, checking both the header and each block CRC."""
    header = _recv_exact(sock, 10, "the link header")
    if header[:2] != START:
        raise Dnp3Error("the reply does not start with a DNP3 link header; check that the port is DNP3 TCP "
                        "and that no other program is answering on it")
    length = header[2]
    if length < 5:
        raise Dnp3Error(f"the reply's link header claims {length} bytes, too short for its addresses")
    if crc16(header[:8]) != int.from_bytes(header[8:10], "little"):
        raise Dnp3Error("the reply's link header failed its CRC check; check the cable and any gateway "
                        "between DurianDB and the outstation")
    remaining = length - 5
    user, block = bytearray(), 0
    while remaining:
        block += 1
        n = min(16, remaining)
        raw = _recv_exact(sock, n + 2, "a data block")
        if crc16(raw[:n]) != int.from_bytes(raw[n:], "little"):
            raise Dnp3Error(f"the reply failed its CRC check in data block {block}; check the cable and any "
                            "gateway between DurianDB and the outstation")
        user += raw[:n]
        remaining -= n
    return header[3], int.from_bytes(header[4:6], "little"), int.from_bytes(header[6:8], "little"), bytes(user)


def _quality(flags):
    """The project's quality for one DNP3 flags byte."""
    if not flags & ONLINE or flags & COMM_LOST:
        return Quality.BAD
    if flags & (RESTART | REMOTE_FORCED | LOCAL_FORCED):
        return Quality.UNCERTAIN
    return Quality.GOOD


def _decode(kind, raw, has_time, group, index):
    """(value, flags, timestamp or None) from one object's bytes."""
    if kind == "bit":
        flags = raw[0]
        value = 1.0 if flags & STATE else 0.0
    else:
        flags = raw[-7] if has_time else raw[-1]
        if kind == "uint32":
            value = int.from_bytes(raw[:4], "little")
        elif kind == "uint16":
            value = int.from_bytes(raw[:2], "little")
        elif kind == "int32":
            value = int.from_bytes(raw[:4], "little", signed=True)
        elif kind == "int16":
            value = int.from_bytes(raw[:2], "little", signed=True)
        elif kind == "float":
            value = struct.unpack("<f", raw[:4])[0]
        else:
            value = struct.unpack("<d", raw[:8])[0]
        if not math.isfinite(value):
            raise Dnp3Error(f"the device sent a group {group} index {index} value that is not a finite number; "
                            "check the outstation's scaling")
    ts = int.from_bytes(raw[-6:], "little") / 1000.0 if has_time else 0.0
    return float(value), flags, (ts or None)


def _parse_objects(data, pos, left):
    """([(group, index, value, flags, time)], notes, objects used) from one application fragment."""
    readings, notes, used = [], [], 0
    while pos < len(data):
        if len(data) - pos < 3:
            raise Dnp3Error("the reply ends in the middle of an object header")
        group, var, qual = data[pos], data[pos + 1], data[pos + 2]
        pos += 3
        layout = LAYOUTS.get(group, {}).get(var)
        if layout is None:
            notes.append(f"the device sent group {group} variation {var}, which this driver does not read, so the "
                         "rest of that reply was skipped; use groups 1, 10, 20, 30 or 40")
            return readings, notes, used
        prefix, rng = qual >> 4, qual & 0x0F
        if prefix == 0:
            width = 0
        elif prefix in (1, 2, 3):
            width = 1 << (prefix - 1)
        else:
            raise Dnp3Error(f"the reply uses qualifier 0x{qual:02x}, which this driver does not read")
        if rng == 0x06:  # all objects: the reply's end is the only boundary
            count, start = None, 0
        elif rng in (7, 8):
            n = 1 if rng == 7 else 2
            if pos + n > len(data):
                raise Dnp3Error("the reply ends in the middle of an object count")
            count = int.from_bytes(data[pos:pos + n], "little")
            pos, start = pos + n, 0
        elif rng in (0, 1, 2):
            n = 1 << rng
            if pos + 2 * n > len(data):
                raise Dnp3Error("the reply ends in the middle of an object range")
            start = int.from_bytes(data[pos:pos + n], "little")
            stop = int.from_bytes(data[pos + n:pos + 2 * n], "little")
            pos += 2 * n
            if stop < start:
                raise Dnp3Error("the reply's object range ends before it starts")
            count = stop - start + 1
        else:
            raise Dnp3Error(f"the reply uses qualifier 0x{qual:02x}, which this driver does not read")
        if count is not None and count > MAX_OBJECTS:
            raise Dnp3Error(f"the reply claims {count} objects, more than this driver reads in one answer")
        kind, size, has_time = layout
        i = 0
        while count is None or i < count:
            if count is None and pos >= len(data):
                break
            if used >= left:
                notes.append(f"the device sent more than {left} objects in one poll, so the rest were skipped; "
                             "shorten the period if this repeats")
                return readings, notes, used
            if width:
                if pos + width > len(data):
                    raise Dnp3Error("the reply ends in the middle of an object index")
                index = int.from_bytes(data[pos:pos + width], "little")
                pos += width
            else:
                index = start + i
            if pos + size > len(data):
                raise Dnp3Error(f"the reply ends in the middle of a group {group} variation {var} object")
            value, flags, ts = _decode(kind, data[pos:pos + size], has_time, group, index)
            pos += size
            readings.append((group, index, value, flags, ts))
            used += 1
            i += 1
    return readings, notes, used


def _parse_app(fragments, left):
    """([(group, index, value, flags, time)], notes) from one answer's application fragments."""
    objects = []
    for i, data in enumerate(fragments):
        if len(data) < 4:
            raise Dnp3Error("the reply is too short to hold a DNP3 application header")
        control, function, iin = data[0], data[1], data[2:4]
        if function not in (RESPONSE, UNSOLICITED):
            raise Dnp3Error(f"the device answered with application function 0x{function:02x}, not a response")
        refusal = []
        if iin[1] & 0x01:
            refusal.append("it does not support this function")
        if iin[1] & 0x02:
            refusal.append("it does not know this object")
        if iin[1] & 0x04:
            refusal.append("it rejected a parameter")
        if refusal:
            raise Dnp3Error("the device refused the read: " + "; ".join(refusal) +
                            "; check the point groups and the outstation's configuration")
        if i == 0 and not control & 0x80:
            raise Dnp3Error("the reply's first application fragment is not marked first; check the outstation")
        if i and control & 0x80:
            raise Dnp3Error("the device started an application message in the middle of its answer; "
                            "another master may be sharing this outstation")
        if i == len(fragments) - 1 and not control & 0x40:
            raise Dnp3Error("the reply's last application fragment is not marked last; check the outstation")
        objects.append(data[4:])
    readings, notes, _ = _parse_objects(b"".join(objects), 0, left)
    return readings, notes


def _read(variation):
    """The application request for one class: group 60 variation 1 (class 0) through 4 (class 3)."""
    return bytes([0xC0, READ, 60, variation, 0x06])


class Dnp3Source(PolledSource):
    """Read class 0 once, then class 1, 2 and 3 events, one outstation at a time."""

    def __init__(self, name, cfg):
        super().__init__(name, cfg.get("period", 5.0), cfg.get("backoff_scale", 1.5), cfg.get("backoff_max", 300.0))
        d = cfg.get("dnp3") if isinstance(cfg.get("dnp3"), dict) else {}
        self.host, self.port = parse_address(d.get("address"))
        self.outstation = int(d.get("outstation", 1))
        self.timeout = float(cfg.get("timeout", 2.0))
        self._seq = 0
        self._integrity_done = False
        self._was_integrity = False
        self._times, self._qualities = {}, {}

    def failure_text(self, e):
        """DNP3 failures are already sentences; sentence() also strips any errno tag a wrapped OS error brings."""
        return sentence(e)

    def _next_seq(self):
        """A fresh transport sequence number; it wraps at 64 by design."""
        self._seq = (self._seq + 1) % 64
        return self._seq

    def _exchange(self, app):
        """One application request and its reassembled response, with at most one retry after a timeout."""
        for _ in range(2):
            try:
                return self._one_exchange(app)
            except socket.timeout:
                continue
            except socket.gaierror:
                raise Dnp3Error(f"cannot resolve the DNP3 host name '{self.host}'") from None
            except OSError as e:
                raise Dnp3Error(f"cannot reach {self.host}:{self.port}: {sentence(e)}") from None
        raise Dnp3Error(f"the device at {self.host}:{self.port} did not answer within {self.timeout:g}s")

    def _one_exchange(self, app):
        user = bytes([0xC0 | self._next_seq()]) + app
        packet = frame(0xC4, self.outstation, MASTER_ADDRESS, user)  # DIR=1, PRM=1, unconfirmed user data
        with socket.create_connection((self.host, self.port), self.timeout) as sock:
            sock.settimeout(self.timeout)
            sock.sendall(packet)
            return self._reply(sock)

    def _reply(self, sock):
        """The application fragments of one answer, each reassembled from its transport segments."""
        fragments, total, expected, read = [], 0, None, 0
        while len(fragments) < MAX_FRAGMENTS:
            payload, expected, read = self._transport(sock, expected, read)
            total += len(payload)
            if total > MAX_APP:
                raise Dnp3Error(f"the reply is larger than {MAX_APP} bytes; check the outstation's event buffer")
            if len(payload) < 2:
                raise Dnp3Error("the reply carries no application data; check the outstation's configuration")
            fragments.append(payload)
            if payload[0] & 0x40:  # FIN in the application control: this was the last fragment
                return fragments
        raise Dnp3Error(f"the reply has more than {MAX_FRAGMENTS} application fragments; check the outstation")

    def _transport(self, sock, expected, read):
        """(payload, next sequence, frames read) for one transport message, stopping at its final segment."""
        chunks, total = [], 0
        while read < MAX_FRAMES:
            read += 1
            _, dest, src, user = read_frame(sock)
            if not user:  # a link-status or acknowledgement frame carries no data
                continue
            if src != self.outstation:
                raise Dnp3Error(f"the reply came from outstation {src} but this source is configured as "
                                f"{self.outstation}; check dnp3.outstation")
            if dest != MASTER_ADDRESS:
                raise Dnp3Error(f"the reply was addressed to link address {dest}, not this master's {MASTER_ADDRESS}")
            fir, fin, seq = user[0] & 0x80, user[0] & 0x40, user[0] & 0x3F
            if not chunks and not fir:
                raise Dnp3Error("the reply starts in the middle of a transport segment; check the outstation")
            if expected is not None and seq != expected:
                raise Dnp3Error(f"transport segment {seq} arrived while segment {expected} was expected; "
                                "another master may be sharing this outstation")
            chunks.append(user[1:])
            total += len(user) - 1
            if total > MAX_SEGMENTS * 250:
                raise Dnp3Error(f"one application fragment is larger than {MAX_SEGMENTS * 250} bytes; "
                                "check the outstation's event buffer")
            expected = (seq + 1) & 0x3F
            if fin:
                return b"".join(chunks), expected, read
        raise Dnp3Error(f"the reply did not finish within {MAX_FRAMES} link frames; check the outstation")

    def _read_class(self, variation, left):
        """([(group, index, value, flags, time)], notes, objects) for one class read."""
        readings, notes = _parse_app(self._exchange(_read(variation)), left)
        return readings, notes, len(readings)

    def fetch(self):
        self._times, self._qualities = {}, {}
        integrity = not self._integrity_done
        self._was_integrity = integrity
        readings, rejected = [], []
        if integrity:
            got, notes, _ = self._read_class(1, MAX_OBJECTS)
            readings += got
            rejected += notes
        else:
            left = EVENT_LIMIT
            for variation in (2, 3, 4):
                if left <= 0:
                    rejected.append(f"the device sent more than {EVENT_LIMIT} events in one poll, so the rest "
                                    "were skipped; shorten the period if this repeats")
                    break
                got, notes, used = self._read_class(variation, left)
                readings += got
                rejected += notes
                left -= used
        by_key = {(EVENT_GROUPS.get(g, g), i): (v, f, t) for g, i, v, f, t in readings}
        values = {}
        for p in self.points.values():
            m = p.meta.get("dnp3") or {}
            key = (m.get("group"), m.get("index"))
            if key in by_key:
                value, flags, ts = by_key[key]
                values[p.name] = value
                if ts is not None:
                    self._times[p.name] = ts
                q = _quality(flags)
                if q != Quality.GOOD:
                    self._qualities[p.name] = q
            elif integrity:
                rejected.append(f"{p.name}: the device did not return group {key[0]} index {key[1]}")
        self._integrity_done = True
        return values, rejected

    def cycle(self):
        """One fetch. Public so tests can drive it without the thread.

        Unlike a read-everything source, an event poll returns only what changed, so points that are
        not in this poll keep their last value instead of being marked bad.
        """
        try:
            values, rejected = self.fetch()
        except Exception as e:  # a failed poll backs off and marks every point bad, as the other sources do
            if self._stop.is_set():
                return
            self.fails += 1
            wait = min(self.period * self.backoff_scale ** self.fails, self.backoff_max)
            self.next_try = time.monotonic() + wait
            self._integrity_done = False  # a recovered link re-reads every static value, so points leave BAD
            self.error, self.connected = f"{self.failure_text(e)} (retry in {wait:.0f}s)", False
            with self._lock:
                self._latest.update({n: (None, time.time(), Quality.BAD) for n in self.points})
            return
        if self._stop.is_set():
            return
        self.fails, self.next_try, self.connected = 0, 0.0, True
        self.error = f"{len(rejected)} point(s) rejected: " + "; ".join(rejected[:3]) if rejected else None
        now = time.time()
        with self._lock:
            for n, v in values.items():
                self._latest[n] = (v, self._times.get(n, now), self._qualities.get(n, Quality.GOOD))
            if self._was_integrity:  # class 0 is the whole static set: a point it omits is a config mismatch
                self._latest.update({n: (None, now, Quality.BAD) for n in self.points if n not in values})
