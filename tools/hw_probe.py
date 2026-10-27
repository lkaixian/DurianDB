"""Probe each field protocol against the real device named in lab/hw.json (or DURIAN_HW_CONFIG).

Copy tools/hw.example.json to lab/hw.json and edit it; absence is the honest default: a protocol with no
config section prints SKIP and exits 0. A configured but unreachable device prints one sentence and counts
as FAILED. Each probe calls the driver in durian/ so the same wire code that polls a plant runs here
against actual hardware.

Run: python tools/hw_probe.py [com smtp snmp sparkplug dnp3 bacnet opcua]
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from durian.core import Point, sentence  # noqa: E402

PROTOCOLS = ("com", "smtp", "snmp", "sparkplug", "dnp3", "bacnet", "opcua")
DEFAULT_CONFIG = ROOT / "lab" / "hw.json"


class ProbeError(Exception):
    """A config or device problem that already reads as one sentence for the operator."""


def config_path():
    return Path(os.environ.get("DURIAN_HW_CONFIG") or DEFAULT_CONFIG)


def shown(path):
    """The repo-relative path in a SKIP line, so the default reads 'lab/hw.json'."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def need(protocol, cfg, key):
    if key not in cfg:
        raise ProbeError(f"the {protocol} config is missing {key}")
    return cfg[key]


def load(path):
    """The config object, or {} when the file is absent. A bad file is an error, never a traceback."""
    if not path.exists():
        return {}
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))  # a Windows editor may add a BOM
    except (OSError, ValueError) as e:
        raise ProbeError(f"{path} is not readable JSON: {sentence(e) or 'the file could not be read'}") from None
    if not isinstance(cfg, dict):
        raise ProbeError(f"{path} must hold one object with a section per protocol")
    for name in PROTOCOLS:
        if name in cfg and not isinstance(cfg[name], dict):
            raise ProbeError(f"{path}: the {name} section must be an object")
    return cfg


def _deadline(seconds, fn):
    """Run fn in a daemon thread: pyserial's Windows open() has no timeout and a dead Bluetooth port can hang."""
    import threading
    box = {}

    def run():
        try:
            box["value"] = fn()
        except Exception as e:
            box["error"] = e

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        # ponytail: a truly stuck driver keeps its handle until this process exits; a subprocess watchdog if that matters
        raise ProbeError(f"the probe did not finish within {seconds:g}s; the serial driver may be stuck")
    if "error" in box:
        raise box["error"]
    return box["value"]


def probe_com(cfg):
    """Open the real COM port and run one Modbus RTU holding-register read through durian.modbus."""
    from durian.modbus import READ_HOLDING, ModbusClient, ModbusTimeout, SerialLink
    port = need("com", cfg, "port")
    timeout = cfg.get("timeout", 1.0)

    def run():
        link = SerialLink(port, baudrate=cfg.get("baudrate", 9600), bytesize=cfg.get("bytesize", 8),
                          parity=cfg.get("parity", "N"), stopbits=cfg.get("stopbits", 1), timeout=timeout)
        client = ModbusClient(link, framing="rtu", retries=cfg.get("retries", 1))
        try:
            return client.read_regs(cfg.get("unit", 1), READ_HOLDING, cfg.get("address", 0), cfg.get("count", 1))
        except ModbusTimeout as e:  # the port opened and nothing answered, so say exactly that
            raise ProbeError(f"the port {port} opened but no Modbus device answered ({sentence(e)})") from None
        finally:
            client.close()

    regs = _deadline(float(cfg.get("deadline", (cfg.get("retries", 1) + 1) * timeout + 5)), run)
    return f"{port} answered a Modbus RTU read: {regs}"


def probe_smtp(cfg):
    """Connect to the mail server, read its EHLO banner and NOOP; no message is sent."""
    import smtplib
    import ssl
    host, port = need("smtp", cfg, "host"), int(cfg.get("port", 25))
    security = cfg.get("security", "none")
    creds = cfg
    if cfg.get("creds"):  # the same user/password file shape durian.outputs accepts
        path = Path(cfg["creds"])
        path = path if path.is_absolute() else ROOT / path
        try:
            creds = {**cfg, **json.loads(path.read_text(encoding="utf-8-sig"))}
        except (OSError, ValueError) as e:
            raise ProbeError(f"cannot read the SMTP credentials in {path}: {sentence(e)}") from None
    cls = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
    kw = {"context": ssl.create_default_context()} if security == "ssl" else {}
    with cls(host, port, timeout=cfg.get("timeout", 5), **kw) as c:
        code, msg = c.ehlo()
        if security == "starttls":
            c.starttls(context=ssl.create_default_context())
            code, msg = c.ehlo()
        if creds.get("user"):
            c.login(creds["user"], creds.get("password", ""))
        code, msg = c.noop()
    text = (msg.decode("utf-8", "replace") if isinstance(msg, bytes) else str(msg)).splitlines()[0][:80]
    return f"the SMTP server at {host}:{port} answered {code} {text!r}"


def probe_snmp(cfg):
    """One SNMP v2c GET through durian.snmp_source, bounded to the configured OIDs."""
    from durian.snmp_source import ERRORS, SnmpError, SnmpSource, decode_response, encode_get
    host = need("snmp", cfg, "host")
    oids = cfg.get("oids") or ["1.3.6.1.2.1.1.1.0"]
    if not isinstance(oids, list) or not all(isinstance(o, str) for o in oids):
        raise ProbeError("the snmp config's oids must be a list of dotted OIDs")
    src = SnmpSource("hw-probe", {"host": host, "port": cfg.get("port", 161),
                                  "community": cfg.get("community", "public"),
                                  "timeout": cfg.get("timeout", 2.0)})
    request_id = src._next_id()
    got_id, status, _, binds = decode_response(src._exchange(encode_get(request_id, src.community, oids)))
    if got_id != request_id:
        raise SnmpError(f"the agent answered request {got_id} while {request_id} was sent")
    if status:
        raise SnmpError(ERRORS.get(status, f"the agent reported error {status}"))
    return f"{host} answered GET for {len(oids)} OID(s) with {len(binds)} value(s): " \
           + ", ".join(repr(v) for _, _, v in binds[:3])


def probe_sparkplug(cfg):
    """Subscribe to the broker with durian.sparkplug_source and say whether any node state arrived."""
    from durian.sparkplug_source import SparkplugSource
    seconds = float(cfg.get("seconds", 5))
    creds = cfg
    if cfg.get("creds"):
        path = Path(cfg["creds"])
        path = path if path.is_absolute() else ROOT / path
        try:
            creds = {**cfg, **json.loads(path.read_text(encoding="utf-8-sig"))}
        except (OSError, ValueError) as e:
            raise ProbeError(f"cannot read the MQTT credentials in {path}: {sentence(e)}") from None
    for key in ("host", "port", "ca"):  # user/password are optional: an anonymous broker is valid
        need("sparkplug", creds, key)
    creds = {**{"user": None, "password": None}, **creds}  # paho treats None as no credentials
    src = SparkplugSource("hw-probe", creds=creds, group=cfg.get("group", "Plant"),
                          factory=lambda name: Point(name))
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not src.connected:
            if src.error and not src.error.endswith("yet"):  # the constructor's "not connected ... yet"
                raise ProbeError(src.error)
            time.sleep(0.1)
        if not src.connected:
            raise ProbeError(f"did not connect to {creds['host']}:{creds['port']} within {seconds:g}s")
        seen = {}
        while time.monotonic() < deadline and src.connected:
            seen.update(src.poll(time.time()))
            if seen:
                break
            time.sleep(0.2)
        if not src.connected:
            raise ProbeError(src.error or "the broker closed the connection")
    finally:
        try:
            src.stop()
        except Exception:  # a failed courtesy disconnect must not hide the result
            pass
    detail = f"connected to {creds['host']}:{creds['port']} and saw {len(seen)} node metric(s)"
    return detail if seen else detail + f"; no Sparkplug node state arrived within {seconds:g}s"


def probe_dnp3(cfg):
    """One class-0 read through durian.dnp3_source, reporting the objects the outstation returned."""
    from durian.dnp3_source import MAX_OBJECTS, Dnp3Source
    address = need("dnp3", cfg, "address")
    src = Dnp3Source("hw-probe", {"timeout": cfg.get("timeout", 2.0),
                                  "dnp3": {"address": address, "outstation": cfg.get("outstation", 1)}})
    readings, notes, _ = src._read_class(1, MAX_OBJECTS)
    return f"{address} answered a class 0 read with {len(readings)} object(s)" \
           + (f"; {'; '.join(notes[:2])}" if notes else "")


def probe_bacnet(cfg):
    """Who-Is/I-Am bind, then one ReadPropertyMultiple of a configured object through durian.bacnet_source."""
    from durian.bacnet_source import BacnetSource
    address = need("bacnet", cfg, "address")
    if "device" not in cfg:
        raise ProbeError("the bacnet config is missing device (the device instance number)")
    src = BacnetSource("hw-probe", {"timeout": cfg.get("timeout", 2.0),
                                    "bacnet": {"address": address, "device": cfg["device"]}})
    point = Point("probe")
    point.meta["bacnet"] = {"object_type": cfg.get("object_type", "analogInput"),
                            "instance": cfg.get("instance", 1), "property": cfg.get("property", "presentValue")}
    src.add(point)
    values, rejected = src.fetch()
    if "probe" not in values:
        raise ProbeError(f"{address} answered the Who-Is but returned no readable value"
                         + (f": {'; '.join(rejected[:2])}" if rejected else ""))
    return f"{address} device {cfg['device']} answered with {values['probe']!r}"


def probe_opcua(cfg):
    """Hello/session handshake and one Read through durian.opcua_source."""
    from durian.opcua_source import OpcuaSource
    address = need("opcua", cfg, "address")
    node = cfg.get("node", "i=2258")
    src = OpcuaSource("hw-probe", {"timeout": cfg.get("timeout", 2.0),
                                   "opcua": {"address": address, "path": cfg.get("path", "")}})
    point = Point("probe")
    point.meta["opcua"] = {"node": node}
    src.add(point)
    values, rejected = src.fetch()
    if "probe" not in values:
        raise ProbeError(f"{address} completed the session but returned no value for {node}"
                         + (f": {'; '.join(rejected[:2])}" if rejected else ""))
    return f"{address} answered a Read for {node}: {values['probe']!r}"


PROBES = {"com": probe_com, "smtp": probe_smtp, "snmp": probe_snmp, "sparkplug": probe_sparkplug,
          "dnp3": probe_dnp3, "bacnet": probe_bacnet, "opcua": probe_opcua}


def main(argv):
    names = argv[1:] or list(PROTOCOLS)
    unknown = [n for n in names if n not in PROBES]
    if unknown:
        print(f"ERROR: unknown protocol(s) {', '.join(unknown)}; choose from {', '.join(PROTOCOLS)}")
        return 2
    path = config_path()
    try:
        cfg = load(path)
    except ProbeError as e:
        print(f"ERROR: {e}")
        return 2

    ran = skipped = failed = 0
    for name in names:
        section = cfg.get(name)
        if section is None:
            print(f"SKIP {name}: no config for it in {shown(path)}")
            skipped += 1
            continue
        try:
            detail = PROBES[name](section)
        except Exception as e:  # one sentence out; a hardware surprise must not become a traceback
            # ponytail: a future driver exception that embeds a credential would echo it here; keep driver messages credential-free
            print(f"FAIL {name}: {sentence(e) or 'the probe failed without a reason'}")
            failed += 1
        else:
            print(f"OK {name}: {detail}")
            ran += 1
    print(f"{ran} ran, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
