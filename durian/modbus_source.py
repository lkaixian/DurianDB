"""Modbus data source: polls registers in a background thread, decodes them, and supports writes.

Failure handling follows the studied behavior: per-request timeout and retries, a device that rejects a read
only loses those points, and a dead link backs off (period x scale^failures, capped) instead of hammering.
"""
import logging
import math
import threading
import time

from .core import Logging, Point, Quality, Source, sentence
from .modbus import (LIMITS, RANGES, TYPES, ModbusClient, ModbusError, ModbusException, SerialLink, TcpLink,
                     decode, encode, plan_batches)

log = logging.getLogger("durian.modbus_source")

DEFAULTS = {"framing": None, "timeout": 0.5, "retries": 2, "period": 5.0, "persistent": True, "contiguous_only": True,
            "multiple_writes_only": False, "max_read_bits": 2000, "max_read_regs": 125, "max_write_regs": 120,
            "backoff_scale": 1.5, "backoff_max": 300.0, "monitor": False}


def make_client(cfg):
    link_cfg = cfg["link"]
    t = cfg.get("timeout", DEFAULTS["timeout"])
    if link_cfg["kind"] == "tcp":
        link, framing = TcpLink(link_cfg["host"], link_cfg["port"], t), cfg.get("framing") or "tcp"
    else:
        link = SerialLink(link_cfg["port"], link_cfg.get("baudrate", 9600), link_cfg.get("bytesize", 8),
                          link_cfg.get("parity", "N"), link_cfg.get("stopbits", 1), t)
        framing = cfg.get("framing") or "rtu"
    return ModbusClient(link, framing, cfg.get("retries", DEFAULTS["retries"]), cfg.get("persistent", True))


class ModbusSource(Source):
    def __init__(self, name, cfg, client=None):
        super().__init__(name)
        self.manages_error = True
        self.cfg = {**DEFAULTS, **cfg}
        self.client = client or make_client(cfg)
        self.fails, self.next_try, self.connected = 0, 0.0, None
        self.period = self.cfg["period"]
        self._latest, self._lock, self._stop = {}, threading.Lock(), threading.Event()
        self._thread = None

    # ---- lifecycle
    def start(self):
        """Call after the points are added: the read plan is built once from them."""
        self._plan = self._build_plan()
        if self.cfg["monitor"]:
            for unit in sorted({u for u, *_ in self._plan}):
                self.add(Point(self._monitor_name(unit), logging=Logging.ON_CHANGE, meta={"source": self.name, "read_roles": ["user"]}))
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self.client.lock:  # wait for a request in flight instead of closing the socket under it
            self.client.close()

    def _monitor_name(self, unit):
        return f"{self.name}-unit{unit}-online"

    def _build_plan(self):
        """[(unit, range, [(start, count, [(point name, address, count)])])]"""
        groups = {}
        for p in self.points.values():
            m = p.meta.get("modbus")
            if m:
                n = 1 if m["range"] in ("coil", "discrete") or m.get("type") == "bool" else TYPES[m["type"]][1]
                groups.setdefault((m["unit"], m["range"]), []).append((p.name, m["address"], n))
        limit = lambda rng: self.cfg["max_read_bits"] if rng in ("coil", "discrete") else self.cfg["max_read_regs"]
        return [(u, r, plan_batches(items, min(limit(r), LIMITS["read_bits" if r in ("coil", "discrete") else "read_regs"]),
                                    self.cfg["contiguous_only"])) for (u, r), items in sorted(groups.items())]

    # ---- polling
    def _loop(self):
        # ponytail: one GIL yield keeps the first poll after the build that created this source; a build slow
        # enough to be preempted past it can still race, so start sources from a post-reload hook if that shows.
        time.sleep(0)
        while not self._stop.is_set():
            started = time.monotonic()
            if time.time() >= self.next_try:
                try:
                    self.cycle()
                except Exception as e:  # a bug here must not silently kill polling
                    log.exception("modbus source %s cycle bug", self.name)
                    self.error = f"internal error: {sentence(e) or 'the source loop failed'}"
                self.poll_ms = (time.monotonic() - started) * 1000
            wait = max(0.05, self.cfg["period"] - (time.monotonic() - started))  # a slow cycle shortens the wait, not the schedule
            self._stop.wait(max(wait, self.next_try - time.time()))

    def cycle(self):
        """One full read of every point. Public so tests can drive it without the thread."""
        values, rejected, online = {}, [], {}
        self.poll_no += 1
        try:
            with self.client.lock:
                if self._stop.is_set():  # stop() won the race for the lock; do not reopen the link
                    return
                for unit, rng, batches in self._plan:
                    for start, count, members in batches:
                        members = [m for m in members if self.due(self.points[m[0]])]
                        if not members:  # every point in this batch is between scan slots: no request at all
                            continue
                        online.setdefault(unit, False)
                        try:
                            raw = self._read(unit, rng, start, count)
                        except ModbusException as e:  # the device said no: only these points are affected
                            rejected += [f"{n}: {sentence(e)}" for n, _, _ in members]
                            online[unit] = True
                            continue
                        online[unit] = True
                        for name, addr, n in members:
                            try:
                                values[name] = self._convert(self.points[name], raw[addr - start:addr - start + n])
                            except (ValueError, ModbusError) as e:
                                rejected.append(f"{name}: {sentence(e)}")
                self.client.end_cycle()
        except ModbusError as e:  # transport failure: nothing more can be read this cycle
            self.fails += 1
            wait = min(self.cfg["period"] * self.cfg["backoff_scale"] ** self.fails, self.cfg["backoff_max"])
            self.next_try = time.time() + wait
            self.error, self.connected = f"{sentence(e) or 'the device did not answer'} (retry in {wait:.0f}s)", False
            values = {}
            online = {u: False for u in online or {u for u, *_ in self._plan}}
        else:
            self.fails, self.next_try, self.connected = 0, 0.0, True
            self.error = f"{len(rejected)} point(s) rejected: " + "; ".join(rejected[:3]) if rejected else None
        now = time.time()
        out = {n: (v, now, Quality.GOOD) for n, v in values.items()}
        # A skipped poll is not a failed read: leave the point out entirely, so it keeps its value, quality
        # and received time instead of being aged towards BAD.
        out.update({n: (None, now, Quality.BAD) for n, p in self.points.items()
                    if "modbus" in p.meta and n not in values and self.due(p)})
        if self.cfg["monitor"]:
            out.update({self._monitor_name(u): (1.0 if ok else 0.0, now, Quality.GOOD) for u, ok in online.items()})
        with self._lock:
            self._latest.update(out)

    def _read(self, unit, rng, start, count):
        if rng in ("coil", "discrete"):
            return self.client.read_bits(unit, RANGES[rng], start, count)
        return self.client.read_regs(unit, RANGES[rng], start, count)

    def _convert(self, point, raw):
        m = point.meta["modbus"]
        if m["range"] in ("coil", "discrete"):
            return 1.0 if raw[0] else 0.0
        if m["type"] == "bool":
            return 1.0 if raw[0] >> m.get("bit", 0) & 1 else 0.0
        v = decode(raw, m["type"], m.get("byte_order", "big"), m.get("word_order", "big"))
        v = v * m.get("multiplier", 1) + m.get("additive", 0)
        if not math.isfinite(v):  # a float register can hold NaN or infinity, or scaling can overflow: no comparison against it is ever true
            raise ValueError(f"{point.name} read {v} from the device")
        return v

    def poll(self, ts):
        with self._lock:
            out, self._latest = self._latest, {}
        return out

    # ---- writes
    def write(self, name, value):
        p = self.points.get(name)
        m = p.meta.get("modbus") if p else None
        if not m or m["range"] not in ("coil", "holding"):
            raise RuntimeError("only coil and holding-register points can be written")
        if value is None:
            raise RuntimeError("Modbus points cannot be released, they hold what was last written")
        if m["range"] == "holding" and m.get("type") == "bool":
            raise RuntimeError("writing a single bit of a register is not supported")
        try:
            with self.client.lock:
                if m["range"] == "coil":
                    (self.client.write_coils(m["unit"], m["address"], [bool(value)]) if self.cfg["multiple_writes_only"]
                     else self.client.write_coil(m["unit"], m["address"], bool(value)))
                else:
                    raw = (value - m.get("additive", 0)) / (m.get("multiplier", 1) or 1)
                    if m["type"] != "float32" and m["type"] != "float64":
                        raw = round(raw)
                    regs = encode(raw, m["type"], m.get("byte_order", "big"), m.get("word_order", "big"))
                    if len(regs) > self.cfg["max_write_regs"]:
                        raise RuntimeError("value needs more registers than max_write_regs")
                    (self.client.write_reg(m["unit"], m["address"], regs[0]) if len(regs) == 1 and not self.cfg["multiple_writes_only"]
                     else self.client.write_regs(m["unit"], m["address"], regs))
                self.client.end_cycle()
        except ModbusException as e:
            raise RuntimeError(f"device refused the write: {sentence(e)}")
        except (ModbusError, ValueError) as e:
            raise RuntimeError(f"write failed: {sentence(e)}")
        with self._lock:
            self._latest[name] = float(value)
