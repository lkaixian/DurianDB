"""A small Modbus slave for tests and teaching. Serves TCP (MBAP), RTU, or ASCII framing over a TCP socket.

Run:  python -m durian.modbus_sim [framing] [port]      (framing: tcp | rtu | ascii, default tcp on 1502)
Units 1 and 2 answer. Unit 99 never answers (to show timeouts). Addresses at or above SIZE are illegal.
"""
import socket
import socketserver
import struct
import sys
import threading

from .modbus import (READ_COILS, READ_DISCRETE, READ_HOLDING, READ_INPUT, WRITE_COIL, WRITE_COILS, WRITE_REG, WRITE_REGS,
                     crc16, lrc)

SIZE = 1000


class Device:
    def __init__(self):
        self.holding, self.input = [0] * SIZE, [0] * SIZE
        self.coils, self.discrete = [False] * SIZE, [False] * SIZE


def handle(dev: Device, pdu: bytes) -> bytes:
    """Return the response PDU for a request PDU."""
    fc = pdu[0]

    def exc(code):
        return bytes([fc | 0x80, code])

    try:
        if fc in (READ_COILS, READ_DISCRETE, READ_HOLDING, READ_INPUT):
            addr, n = struct.unpack(">HH", pdu[1:5])
            bits = fc in (READ_COILS, READ_DISCRETE)
            if not 1 <= n <= (2000 if bits else 125):
                return exc(3)
            if addr + n > SIZE:
                return exc(2)
            if bits:
                src = (dev.coils if fc == READ_COILS else dev.discrete)[addr:addr + n]
                out = bytearray((n + 7) // 8)
                for i, v in enumerate(src):
                    if v:
                        out[i // 8] |= 1 << (i % 8)
                return bytes([fc, len(out)]) + bytes(out)
            regs = (dev.holding if fc == READ_HOLDING else dev.input)[addr:addr + n]
            return bytes([fc, 2 * n]) + struct.pack(f">{n}H", *regs)
        if fc == WRITE_COIL:
            addr, val = struct.unpack(">HH", pdu[1:5])
            if val not in (0, 0xFF00):
                return exc(3)
            if addr >= SIZE:
                return exc(2)
            dev.coils[addr] = val == 0xFF00
            return pdu[:5]
        if fc == WRITE_REG:
            addr, val = struct.unpack(">HH", pdu[1:5])
            if addr >= SIZE:
                return exc(2)
            dev.holding[addr] = val
            return pdu[:5]
        if fc == WRITE_COILS:
            addr, n, count = struct.unpack(">HHB", pdu[1:6])
            if not 1 <= n <= 1968 or count != (n + 7) // 8 or len(pdu) != 6 + count:
                return exc(3)
            if addr + n > SIZE:
                return exc(2)
            for i in range(n):
                dev.coils[addr + i] = bool(pdu[6 + i // 8] >> (i % 8) & 1)
            return pdu[:5]
        if fc == WRITE_REGS:
            addr, n, count = struct.unpack(">HHB", pdu[1:6])
            if not 1 <= n <= 123 or count != 2 * n or len(pdu) != 6 + count:
                return exc(3)
            if addr + n > SIZE:
                return exc(2)
            dev.holding[addr:addr + n] = struct.unpack(f">{n}H", pdu[6:])
            return pdu[:5]
    except struct.error:
        return exc(3)
    return exc(1)


def _recv(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError
        buf += chunk
    return buf


def read_request(sock, framing):
    """Return (unit, pdu) from the socket, or raise ConnectionError."""
    if framing == "tcp":
        tid, _, length, unit = struct.unpack(">HHHB", _recv(sock, 7))
        return (tid, unit), _recv(sock, length - 1)
    if framing == "rtu":
        head = _recv(sock, 2)
        fc = head[1]
        if fc in (WRITE_COILS, WRITE_REGS):
            mid = _recv(sock, 5)
            body = head + mid + _recv(sock, mid[4] + 2)
        else:
            body = head + _recv(sock, 4 + 2)
        if crc16(body[:-2]) != struct.unpack("<H", body[-2:])[0]:
            raise ConnectionError("bad CRC")
        return body[0], body[1:-2]
    buf = b""
    while not buf.endswith(b"\n"):
        buf += _recv(sock, 1)
    raw = bytes.fromhex(buf.strip().lstrip(b":").decode())
    if lrc(raw[:-1]) != raw[-1]:
        raise ConnectionError("bad LRC")
    return raw[0], raw[1:-1]


def frame_response(framing, ident, pdu):
    if framing == "tcp":
        tid, unit = ident
        return struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu
    body = bytes([ident]) + pdu
    if framing == "rtu":
        return body + struct.pack("<H", crc16(body))
    return b":" + (body + bytes([lrc(body)])).hex().upper().encode() + b"\r\n"


class Slave(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, framing="tcp", port=0, units=(1, 2)):
        self.framing, self.devices, self.silent = framing, {u: Device() for u in units}, {99}
        self.drop_next = 0  # test hook: close the connection instead of answering this many times
        self.requests = []  # (unit, pdu) log, for tests
        self.conns = set()
        super().__init__(("127.0.0.1", port), _Handler)

    @property
    def port(self):
        return self.server_address[1]

    def start(self):
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()
        for c in list(self.conns):  # a dead device drops its connections too
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        srv = self.server
        srv.conns.add(self.request)
        try:
            self.serve(srv)
        finally:
            srv.conns.discard(self.request)

    def serve(self, srv):
        while True:
            try:
                ident, pdu = read_request(self.request, srv.framing)
            except (ConnectionError, OSError, ValueError):
                return
            unit = ident[1] if srv.framing == "tcp" else ident
            srv.requests.append((unit, pdu))
            if srv.drop_next:
                srv.drop_next -= 1
                return
            if unit in srv.silent or unit not in srv.devices:
                continue
            try:
                self.request.sendall(frame_response(srv.framing, ident, handle(srv.devices[unit], pdu)))
            except OSError:
                return


if __name__ == "__main__":
    framing = sys.argv[1] if len(sys.argv) > 1 else "tcp"
    s = Slave(framing, int(sys.argv[2]) if len(sys.argv) > 2 else 1502)
    s.devices[1].holding[0:4] = [10, 20, 30, 40]
    print(f"Modbus {framing} slave on 127.0.0.1:{s.port}, units 1 and 2")
    s.serve_forever()
