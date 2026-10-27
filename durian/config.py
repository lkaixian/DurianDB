"""Source types, config validation, and building a running Engine from stored config.

Validation returns every problem at once as [{property, message}], the way the studied system does.
"""
import json
import math
import random
import re
from pathlib import Path

from .core import Engine, Logging, Point, Source, make_detector, sentence
from . import calc, http_source, sql_source, units, windows
from .dnp3_source import POINT_GROUPS, Dnp3Source, parse_address
from .bacnet_source import (MAX_INSTANCE, OBJECT_TYPES, PROPERTIES, BacnetError, BacnetSource,
                            parse_address)
from .http_source import HttpJsonSource
from .ingest import HttpInSource
from .modbus import RANGES, TYPES
from .modbus_source import DEFAULTS as MODBUS_KEYS, ModbusSource
from .mqtt_source import MqttSource
from .opcua_source import OpcuaError, OpcuaSource
from .opcua_source import parse_address as parse_opcua_address
from .opcua_source import parse_node_id as parse_opcua_node
from .snmp_source import SnmpSource, encode_oid
from .sparkplug_source import SparkplugSource
from .sql_source import SqlSource

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_TEXT = 255
SOURCE_TYPES = ("virtual", "mqtt", "sparkplug", "modbus", "snmp", "dnp3", "bacnet", "opcua", "http_json",
                "sql", "http_in", "calc")
PARITY, BAUDS = ("N", "E", "O"), (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)


def _int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _modbus_source_errors(b, add):
    link = b.get("link")
    if not isinstance(link, dict) or link.get("kind") not in ("tcp", "serial"):
        add("link.kind", "must be tcp or serial")
        return
    if link["kind"] == "tcp":
        if not (isinstance(link.get("host"), str) and 0 < len(link["host"]) <= 253):
            add("link.host", "must be a host name or address")
        if not _int(link.get("port"), 1, 65535):
            add("link.port", "must be 1 to 65535")
    else:
        if not (isinstance(link.get("port"), str) and 0 < len(link["port"]) <= 200):
            add("link.port", "must be a serial port name such as COM3 or /dev/ttyUSB0")
        if link.get("baudrate", 9600) not in BAUDS:
            add("link.baudrate", f"must be one of {list(BAUDS)}")
        if link.get("parity", "N") not in PARITY:
            add("link.parity", "must be N, E or O")
        if link.get("stopbits", 1) not in (1, 2):
            add("link.stopbits", "must be 1 or 2")
        if link.get("bytesize", 8) not in (7, 8):
            add("link.bytesize", "must be 7 or 8")
    framing = b.get("framing")
    if framing not in (None, "tcp", "rtu", "ascii") or (framing == "tcp" and link["kind"] == "serial"):
        add("framing", "must be tcp (TCP links only), rtu or ascii")
    for k, lo, hi in (("retries", 0, 10), ("max_read_bits", 1, 2000), ("max_read_regs", 1, 125), ("max_write_regs", 1, 123)):
        if k in b and not _int(b[k], lo, hi):
            add(k, f"must be a whole number from {lo} to {hi}")
    for k, lo, hi in (("timeout", 0.05, 30), ("period", 0.2, 86400), ("backoff_max", 1, 86400)):
        if k in b and not (_num(b[k]) and lo <= b[k] <= hi):
            add(k, f"must be a number from {lo} to {hi}")
    if "backoff_scale" in b and not (_num(b["backoff_scale"]) and 1 < b["backoff_scale"] <= 10):
        add("backoff_scale", "must be greater than 1 and at most 10")
    for k in ("persistent", "contiguous_only", "multiple_writes_only", "monitor"):
        if not isinstance(b.get(k, True), bool):
            add(k, "must be true or false")


def _modbus_point_errors(b, add):
    m = b.get("modbus")
    if not isinstance(m, dict):
        add("modbus", "a point on a Modbus source needs a modbus section")
        return
    rng, typ = m.get("range"), m.get("type")
    if not _int(m.get("unit"), 1, 247):
        add("modbus.unit", "must be a device address from 1 to 247")
    if rng not in RANGES:
        add("modbus.range", f"must be one of {sorted(RANGES)}")
        return
    if not _int(m.get("address"), 0, 65535):
        add("modbus.address", "must be 0 to 65535 (zero-based, so register 40001 is holding address 0)")
        return
    if rng in ("coil", "discrete"):
        if typ not in (None, "bool"):
            add("modbus.type", "coils and discrete inputs are single bits, leave type out")
    elif typ != "bool" and typ not in TYPES:
        add("modbus.type", f"must be bool or one of {sorted(TYPES)}")
    elif typ == "bool" and not _int(m.get("bit", 0), 0, 15):
        add("modbus.bit", "must be 0 to 15")
    elif typ != "bool" and m["address"] + TYPES[typ][1] > 65536:
        add("modbus.address", "the value would run past the last register")
    for k in ("byte_order", "word_order"):
        if m.get(k, "big") not in ("big", "little"):
            add(f"modbus.{k}", "must be big or little")
    if not (_num(m.get("multiplier", 1)) and m.get("multiplier", 1) != 0):
        add("modbus.multiplier", "must be a non-zero number")
    if not _num(m.get("additive", 0)):
        add("modbus.additive", "must be a number")
    if b.get("settable") and (rng not in ("coil", "holding") or (rng == "holding" and typ == "bool")):
        add("settable", "only coils and whole holding-register values can be written")


def _snmp_source_errors(b, add):
    if not (isinstance(b.get("host"), str) and 0 < len(b["host"]) <= 253):
        add("host", "must be a host name or address")
    if "port" in b and not _int(b["port"], 1, 65535):
        add("port", "must be 1 to 65535")
    if not (isinstance(b.get("community"), str) and 0 < len(b["community"]) <= 255):
        add("community", "must be the v2c community text the device expects")
    if "timeout" in b and not (_num(b["timeout"]) and 1 <= b["timeout"] <= 30):
        add("timeout", "must be a number of seconds from 1 to 30")
    if "period" in b and not (_num(b["period"]) and 0.2 <= b["period"] <= 86400):
        add("period", "must be a number of seconds from 0.2 to 86400")
    if "backoff_max" in b and not (_num(b["backoff_max"]) and 1 <= b["backoff_max"] <= 86400):
        add("backoff_max", "must be a number of seconds from 1 to 86400")
    if "backoff_scale" in b and not (_num(b["backoff_scale"]) and 1 < b["backoff_scale"] <= 10):
        add("backoff_scale", "must be greater than 1 and at most 10")


def _snmp_point_errors(b, add):
    m = b.get("snmp")
    if not isinstance(m, dict):
        add("snmp", "a point on an SNMP source needs an snmp section naming the OID or subtree to read")
        return
    keys = [k for k in ("address", "walk") if k in m]
    if len(keys) != 1:
        add("snmp", "give either snmp.address for one OID or snmp.walk for a subtree, not both")
        return
    key = keys[0]
    oid = m[key]
    if not isinstance(oid, str):
        add(f"snmp.{key}", "must be the numeric OID to read, such as 1.3.6.1.2.1.1.5.0")
        return
    try:
        encode_oid(oid)
    except ValueError:
        add(f"snmp.{key}", "must be a numeric OID such as 1.3.6.1.2.1.1.5.0")


def _dnp3_source_errors(b, add):
    d = b.get("dnp3")
    if not isinstance(d, dict):
        add("dnp3", "a DNP3 source needs a dnp3 section with address and outstation")
    else:
        try:
            parse_address(d.get("address"))
        except ValueError:
            add("dnp3.address", "must be host:port, such as 10.0.0.5:20000")
        if not _int(d.get("outstation"), 0, 65535):
            add("dnp3.outstation", "must be the outstation's link address, 0 to 65535")
    for k, lo, hi in (("timeout", 1, 30), ("period", 0.2, 86400), ("backoff_max", 1, 86400)):
        if k in b and not (_num(b[k]) and lo <= b[k] <= hi):
            add(k, f"must be a number from {lo} to {hi}")
    if "backoff_scale" in b and not (_num(b["backoff_scale"]) and 1 < b["backoff_scale"] <= 10):
        add("backoff_scale", "must be greater than 1 and at most 10")


def _bacnet_source_errors(b, add):
    m = b.get("bacnet")
    if not isinstance(m, dict):
        add("bacnet", "a BACnet source needs a bacnet section with address and device")
        return
    try:
        parse_address(m.get("address"))
    except BacnetError as e:
        add("bacnet.address", sentence(e))
    if not _int(m.get("device"), 0, MAX_INSTANCE):
        add("bacnet.device", f"must be the device instance, a whole number from 0 to {MAX_INSTANCE}")
    if "timeout" in b and not (_num(b["timeout"]) and 1 <= b["timeout"] <= 30):
        add("timeout", "must be a number of seconds from 1 to 30")
    if "period" in b and not (_num(b["period"]) and 0.2 <= b["period"] <= 86400):
        add("period", "must be a number of seconds from 0.2 to 86400")
    if "backoff_max" in b and not (_num(b["backoff_max"]) and 1 <= b["backoff_max"] <= 86400):
        add("backoff_max", "must be a number of seconds from 1 to 86400")
    if "backoff_scale" in b and not (_num(b["backoff_scale"]) and 1 < b["backoff_scale"] <= 10):
        add("backoff_scale", "must be greater than 1 and at most 10")


def _dnp3_point_errors(b, add):
    m = b.get("dnp3")
    if not isinstance(m, dict):
        add("dnp3", "a point on a DNP3 source needs a dnp3 section naming its group and index")
        return
    g = m.get("group")
    if not (isinstance(g, int) and not isinstance(g, bool) and g in POINT_GROUPS):
        add("dnp3.group", "must be one of 1, 10, 20, 30 or 40")
    if not _int(m.get("index"), 0, 65535):
        add("dnp3.index", "must be an object index from 0 to 65535")


def _bacnet_point_errors(b, add):
    m = b.get("bacnet")
    if not isinstance(m, dict):
        add("bacnet", "a point on a BACnet source needs a bacnet section naming its object_type, instance and property")
        return
    if m.get("object_type") not in OBJECT_TYPES:
        add("bacnet.object_type", f"must be one of {sorted(OBJECT_TYPES)}")
    if not _int(m.get("instance"), 0, MAX_INSTANCE):
        add("bacnet.instance", f"must be the object instance, a whole number from 0 to {MAX_INSTANCE}")
    if m.get("property") not in PROPERTIES:
        add("bacnet.property", f"must be one of {list(PROPERTIES)}")


def _opcua_source_errors(b, add):
    m = b.get("opcua")
    if not isinstance(m, dict):
        add("opcua", "an OPC UA source needs an opcua section with address")
        return
    try:
        parse_opcua_address(m.get("address"))
    except OpcuaError as e:
        add("opcua.address", sentence(e))
    if "path" in m and not (isinstance(m["path"], str) and len(m["path"]) <= 200
                            and not any(c.isspace() for c in m["path"])):
        add("opcua.path", "must be the endpoint URL path, up to 200 characters, such as plant")
    policy = m.get("security_policy", "None")
    if not (isinstance(policy, str) and policy.lower() == "none"):
        add("opcua.security_policy", "only SecurityPolicy None is implemented; leave it None")
    mode = m.get("security_mode", "None")
    if not (isinstance(mode, str) and mode.lower() == "none"):
        add("opcua.security_mode", "only SecurityMode None is implemented; leave it None")
    if "timeout" in b and not (_num(b["timeout"]) and 1 <= b["timeout"] <= 30):
        add("timeout", "must be a number of seconds from 1 to 30")
    if "period" in b and not (_num(b["period"]) and 0.2 <= b["period"] <= 86400):
        add("period", "must be a number of seconds from 0.2 to 86400")
    if "backoff_max" in b and not (_num(b["backoff_max"]) and 1 <= b["backoff_max"] <= 86400):
        add("backoff_max", "must be a number of seconds from 1 to 86400")
    if "backoff_scale" in b and not (_num(b["backoff_scale"]) and 1 < b["backoff_scale"] <= 10):
        add("backoff_scale", "must be greater than 1 and at most 10")


def _opcua_point_errors(b, add):
    m = b.get("opcua")
    if not isinstance(m, dict):
        add("opcua", "a point on an OPC UA source needs an opcua section naming its node")
        return
    try:
        parse_opcua_node(m.get("node"))
    except OpcuaError as e:
        add("opcua.node", sentence(e))


class Virtual(Source):
    """Generates values: random walk, sine wave, or constant. Settable points hold the value written."""

    def __init__(self, name):
        super().__init__(name)
        self.gens, self.state, self.override = {}, {}, {}

    def poll(self, ts):
        out = {}
        for n, g in self.gens.items():
            if n in self.override:
                out[n] = self.override[n]
                continue
            kind = g.get("kind", "walk")
            if kind == "constant":
                v = g["value"]
            elif kind == "sine":
                v = g.get("offset", 0) + g["amplitude"] * math.sin(2 * math.pi * ts / g["period"])
            else:
                step = g.get("step", 1.0)
                v = self.state.get(n, g.get("start", 0)) + random.uniform(-step, step) + g.get("drift", 0)
                v = min(g.get("max", math.inf), max(g.get("min", -math.inf), v))
                self.state[n] = v
            out[n] = v if isinstance(v, str) else round(v, 3)
        return out

    def write(self, name, value):
        if value is None:
            self.override.pop(name, None)
        else:
            self.override[name] = value


def _roles_ok(v):
    return isinstance(v, list) and all(isinstance(r, str) and 0 < len(r) <= 32 for r in v) and len(v) <= 20


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _tags_ok(tags):
    return isinstance(tags, dict) and len(tags) <= 20 and all(
        isinstance(k, str) and isinstance(v, str) and 0 < len(k) <= 64 and len(v) <= 64 for k, v in tags.items())


def _folder_ok(v):
    return isinstance(v, str) and len(v) <= 200 and all(0 < len(x.strip()) <= 64 for x in v.split("/"))


def resolve(tags, unit, folder, source, folder_defaults=None):
    """The tags and unit a point is shown with, in one place so no two views can disagree.

    A point's own value wins; a missing tag or an empty unit is inherited from the folder first, then the source.
    `source` is the stored source config; `folder_defaults` maps a folder path to its own default object.
    """
    source = source if isinstance(source, dict) else {}
    folders = source.get("folders") if isinstance(source.get("folders"), dict) else {}
    try:
        default = (folder_defaults or {}).get(folder or "") or folders.get(folder or "") or {}
    except TypeError:  # an unhashable folder path cannot match anything
        default = {}
    default = default if isinstance(default, dict) else {}
    raw = source.get("default_tags") if isinstance(source.get("default_tags"), dict) else source.get("tags")
    out = dict(raw) if isinstance(raw, dict) else {}
    ftags = default.get("tags") if isinstance(default.get("tags"), dict) else default.get("default_tags")
    out.update(ftags if isinstance(ftags, dict) else {})
    out.update(tags if isinstance(tags, dict) else {})
    eff = unit or default.get("unit") or default.get("default_unit") or source.get("default_unit") or source.get("unit") or ""
    return out, eff if isinstance(eff, str) else ""


roles_ok = _roles_ok


def load_creds(path):
    c = json.loads(Path(path).read_text())
    for k in ("host", "port", "ca", "user", "password"):
        if k not in c:
            raise ValueError(f"credentials file lacks '{k}'")
    return c


def validate_source(name, b):
    e = []
    add = lambda p, m: e.append({"property": p, "message": m})
    if not NAME.match(str(name)):
        add("name", "use 1 to 64 letters, digits, dot, dash or underscore")
    if b.get("type") not in SOURCE_TYPES:
        add("type", f"must be one of {list(SOURCE_TYPES)}")
    if not isinstance(b.get("enabled", True), bool):
        add("enabled", "must be true or false")
    if not _roles_ok(b.get("read_roles", ["user"])):
        add("read_roles", "must be a list of role names")
    hb = b.get("heartbeat_seconds")
    if "heartbeat_seconds" in b and not (_num(hb) and 5 <= hb <= 86400):
        add("heartbeat_seconds", "must be a number of seconds from 5 to 86400")
    if "down_after" in b and not (_num(b["down_after"]) and 0 <= b["down_after"] <= 3600):
        add("down_after", "must be a number of seconds from 0 to 3600")
    if not _tags_ok(b.get("default_tags", {})):
        add("default_tags", "must be up to 20 text key/value pairs")
    if not (isinstance(b.get("default_unit", ""), str) and len(b.get("default_unit", "")) <= 16):
        add("default_unit", "must be text, up to 16 characters")
    folders = b.get("folders", {})
    if not isinstance(folders, dict) or len(folders) > 200:
        add("folders", "must be a map of folder paths to their defaults, at most 200")
    else:
        for path, fd in folders.items():
            if not _folder_ok(path):
                add(f"folders[{path}]", "must be a folder path such as Plant/Boiler")
                continue
            if not isinstance(fd, dict):
                add(f"folders[{path}]", "must be {tags: {...}, unit: '...'}")
                continue
            if not _tags_ok(fd.get("tags", {})):
                add(f"folders[{path}].tags", "must be up to 20 text key/value pairs")
            if not (isinstance(fd.get("unit", ""), str) and len(fd.get("unit", "")) <= 16):
                add(f"folders[{path}].unit", "must be text, up to 16 characters")
    if b.get("type") == "mqtt":
        try:
            load_creds(b.get("creds", ""))
        except (OSError, ValueError, TypeError) as ex:
            add("creds", f"cannot use credentials file: {sentence(ex) or 'it could not be read'}")
        pre, cmd = b.get("prefix", "durian/"), b.get("cmd_prefix", "durian-cmd/")
        for prop, v in (("prefix", pre), ("cmd_prefix", cmd)):
            if not isinstance(v, str) or not v.endswith("/") or "#" in v or "+" in v:
                add(prop, "must be a topic prefix ending in '/', without wildcards")
        if isinstance(pre, str) and isinstance(cmd, str) and (cmd.startswith(pre) or pre.startswith(cmd)):
            add("cmd_prefix", "must not overlap prefix, or commands would come back in as data")
        if not isinstance(b.get("auto_create", True), bool):
            add("auto_create", "must be true or false")
    if b.get("type") == "sparkplug":
        try:
            load_creds(b.get("creds", ""))
        except (OSError, ValueError, TypeError):
            add("creds", "cannot read the credentials file; give the path to a JSON file with host, port, ca, user and password")
        if not (isinstance(b.get("group"), str) and NAME.match(b["group"])):
            add("group", "give the Sparkplug group id: 1 to 64 letters, digits, dot, dash or underscore")
        if not isinstance(b.get("auto_create", True), bool):
            add("auto_create", "must be true or false")
    if b.get("type") == "modbus":
        _modbus_source_errors(b, add)
    elif b.get("type") == "snmp":
        _snmp_source_errors(b, add)
    elif b.get("type") == "dnp3":
        _dnp3_source_errors(b, add)
    elif b.get("type") == "bacnet":
        _bacnet_source_errors(b, add)
    elif b.get("type") == "opcua":
        _opcua_source_errors(b, add)
    elif b.get("type") == "http_json":
        http_source.validate_source(b, add)
    elif b.get("type") == "sql":
        sql_source.validate_source(b, add)
    elif b.get("type") == "http_in":
        if not isinstance(b.get("auto_create", False), bool):
            add("auto_create", "must be true or false")
        if "max_age_days" in b and not _int(b["max_age_days"], 1, 365):
            add("max_age_days", "must be a whole number from 1 to 365")
    return e


def validate_point(name, b, store):
    e = []
    add = lambda p, m: e.append({"property": p, "message": m})
    if not NAME.match(str(name)):
        add("name", "use 1 to 64 letters, digits, dot, dash or underscore")
    src = store.get("source", b.get("source")) if isinstance(b.get("source"), str) else None
    if src is None:
        add("source", "unknown source")
    if b.get("logging", "ON_CHANGE") not in Logging.__members__:
        add("logging", f"must be one of {list(Logging.__members__)}")
    if b.get("logging") == "INTERVAL" and not (_num(b.get("log_interval")) and 1 <= b["log_interval"] <= 86400):
        add("log_interval", "give the seconds between samples, 1 to 86400")
    if b.get("log_mode", "instant") not in ("instant", "avg", "min", "max"):
        add("log_mode", "must be instant, avg, min or max")
    if not (_num(b.get("tolerance", 0)) and b.get("tolerance", 0) >= 0):
        add("tolerance", "must be a number, zero or more")
    tags = b.get("tags", {})
    if not _tags_ok(tags):
        add("tags", "must be up to 20 text key/value pairs")
    if not (isinstance(b.get("unit", ""), str) and len(b.get("unit", "")) <= 16):
        add("unit", "must be text, up to 16 characters")
    du, folder = b.get("display_unit", ""), b.get("folder") if isinstance(b.get("folder"), str) else ""
    if not (isinstance(du, str) and len(du) <= 16):
        add("display_unit", "must be text, up to 16 characters")
    elif du:  # a display unit is checked against the unit the point will actually have, inherited or its own
        got = store.get("folder", folder) if store is not None and folder else None
        _, eff_unit = resolve({}, b.get("unit", ""), folder, src or {}, {folder: got} if got else None)
        if not eff_unit:
            add("display_unit", "a point with no unit cannot be shown in one; set unit first")
        elif not units.compatible(eff_unit, du):
            add("display_unit", f"cannot show {eff_unit} as {du}: different kinds of quantity")
    if not isinstance(b.get("settable", False), bool):
        add("settable", "must be true or false")
    r = b.get("retention_days")
    if "retention_days" in b and not (isinstance(r, int) and not isinstance(r, bool) and 0 <= r <= 36500):
        add("retention_days", "must be a whole number of days, 0 to keep forever (leave out to use the database default)")
    for p in ("read_roles", "set_roles"):
        if not _roles_ok(b.get(p, [])):
            add(p, "must be a list of role names")
    text = b.get("datatype", "number")
    if text not in ("number", "text"):
        add("datatype", "must be number or text")
    text = text == "text"
    if text:
        if src and src.get("type") not in ("virtual", "http_in", "mqtt", "sparkplug", "snmp", "bacnet", "opcua"):
            add("datatype", "text points are supported on virtual, http_in, mqtt, sparkplug, snmp, bacnet and opcua sources")
        if b.get("logging") == "INTERVAL":
            add("logging", "a text point is logged on change or always, not at an interval")
    dets = b.get("detectors", [])
    if not isinstance(dets, list) or len(dets) > 10:
        add("detectors", "must be a list of at most 10")
    else:
        for i, d in enumerate(dets):
            try:
                kind = d.get("kind") if isinstance(d, dict) else None
                if text != (kind == "TEXT") and not (text and kind in ("NO_UPDATE", "CHANGE")):
                    raise ValueError("a text point takes TEXT, CHANGE or NO_UPDATE alarms" if text else "the TEXT alarm needs datatype text")
                make_detector(d)
            except (ValueError, TypeError, AttributeError) as ex:
                add(f"detectors[{i}]", sentence(ex) or "the detector settings do not fit together")
    if not (isinstance(b.get("description", ""), str) and len(b.get("description", "")) <= 200):
        add("description", "must be text, up to 200 characters")
    for k in ("eu_low", "eu_high", "set_min", "set_max"):
        if k in b and not _num(b[k]):
            add(k, "must be a number")
    for lo, hi in (("eu_low", "eu_high"), ("set_min", "set_max")):
        if _num(b.get(lo)) and _num(b.get(hi)) and b[lo] >= b[hi]:
            add(hi, f"must be greater than {lo}")
    d = b.get("decimals")
    if "decimals" in b and not (isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6):
        add("decimals", "must be a whole number from 0 to 6")
    if "max_step" in b and not (_num(b["max_step"]) and b["max_step"] > 0):
        add("max_step", "must be a number greater than zero")
    if not isinstance(b.get("confirm", False), bool):
        add("confirm", "must be true or false")
    if not isinstance(b.get("require_reason", False), bool):
        add("require_reason", "must be true or false")
    if not b.get("settable") and any(k in b for k in ("set_min", "set_max", "max_step", "confirm", "require_reason")):
        add("settable", "set limits and confirmation only apply to a settable point")
    fo = b.get("folder")
    if "folder" in b and not _folder_ok(fo):
        add("folder", "must be a path such as Plant/Boiler: names separated by /, none empty, each up to 64 characters")
    st = b.get("states")
    if "states" in b and not (isinstance(st, dict) and 1 <= len(st) <= 50 and all(isinstance(k, str) and re.fullmatch(r"-?\d+", k) and isinstance(v, str) and 1 <= len(v) <= 40 for k, v in st.items())):
        add("states", 'must be {"0": "Stopped", "1": "Running"}: whole-number keys, text labels of 1 to 40 characters, at most 50')
    sw = b.get("suppress_when")
    if sw is not None and not (isinstance(sw, dict) and isinstance(sw.get("point"), str) and sw["point"] != name
                               and sw.get("op") in (">", "<", "==") and _num(sw.get("value"))):
        add("suppress_when", "must be {point: another point's name, op: >, < or ==, value: number}")
    if src and src.get("type") == "virtual":
        g = b.get("generator", {"kind": "walk"})
        kind = g.get("kind") if isinstance(g, dict) else None
        need = {"walk": [], "sine": ["amplitude", "period"], "constant": ["value"]}
        if text:
            if not (kind == "constant" and isinstance(g.get("value"), str) and len(g["value"]) <= MAX_TEXT):
                add("generator", f"a text point on a virtual source needs generator {{kind: constant, value: text of up to {MAX_TEXT} characters}}")
        elif kind not in need:
            add("generator.kind", "must be walk, sine or constant")
        else:
            for k in need[kind] + [k for k in ("start", "min", "max", "step", "drift", "offset") if k in g]:
                if not _num(g.get(k)):
                    add(f"generator.{k}", "must be a number")
            if kind == "sine" and _num(g.get("period")) and g["period"] <= 0:
                add("generator.period", "must be greater than zero")
    if src and src.get("type") == "modbus":
        _modbus_point_errors(b, add)
    elif src and src.get("type") == "snmp":
        _snmp_point_errors(b, add)
    elif src and src.get("type") == "dnp3":
        _dnp3_point_errors(b, add)
    elif src and src.get("type") == "bacnet":
        _bacnet_point_errors(b, add)
    elif src and src.get("type") == "opcua":
        _opcua_point_errors(b, add)
    elif src and src.get("type") == "http_json":
        http_source.validate_point(b, add)
    elif src and src.get("type") == "sql":
        sql_source.validate_point(b, add)
    sc = b.get("scale", {})
    if src and src.get("type") in ("http_json", "sql") and not (
            isinstance(sc, dict) and _num(sc.get("multiplier", 1)) and sc.get("multiplier", 1) != 0 and _num(sc.get("additive", 0))):
        add("scale", "must be {multiplier: non-zero number, additive: number}")
    if b.get("settable") and src and src.get("type") in ("http_json", "sql", "http_in", "calc", "snmp", "dnp3",
                                                         "bacnet", "opcua"):
        add("settable", "this source type is read-only")
    if src and src.get("type") == "calc":
        try:
            tree = calc.parse(b["calc"].get("expression") if isinstance(b.get("calc"), dict) else None)
            if name in calc.refs(tree):
                add("calc.expression", "a point cannot read itself")
        except ValueError as ex:
            add("calc.expression", sentence(ex) or "the expression could not be read")
    return e


def validate_maintenance(name, b):
    return windows.validate(name, b, NAME)


def validate_watchlist(name, b):
    e, add = [], lambda p, m: e.append({"property": p, "message": m})
    if not NAME.match(str(name)):
        add("name", "use 1 to 64 letters, digits, dot, dash or underscore")
    tags, pts = b.get("tags", {}), b.get("points", [])
    if not (isinstance(tags, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in tags.items())):
        add("tags", "must be text key/value pairs")
    if not (isinstance(pts, list) and all(isinstance(p, str) for p in pts)):
        add("points", "must be a list of point names")
    for k in ("name_like", "text_like"):  # a query list: a point name pattern (* and ?) and/or text in the description
        if k in b and not (isinstance(b[k], str) and 0 < len(b[k]) <= 64):
            add(k, "must be 1 to 64 characters")
    if not tags and not pts and "name_like" not in b and "text_like" not in b:
        add("tags", "give tags to match, a list of points, or a name or description query")
    if not _roles_ok(b.get("read_roles", ["user"])):
        add("read_roles", "must be a list of role names")
    return e


def make_point(name, b, source_name):
    p = Point(name, logging=Logging[b.get("logging", "ON_CHANGE")], tolerance=float(b.get("tolerance", 0)),
              interval=float(b.get("log_interval", 0)), interval_mode=b.get("log_mode", "instant"),
              tags=dict(b.get("tags", {})), detectors=[make_detector(d) for d in b.get("detectors", [])])
    p.text = b.get("datatype") == "text"
    p.meta = {"source": source_name, "unit": b.get("unit", ""), "display_unit": b.get("display_unit", ""),
              "settable": bool(b.get("settable", False)),
              "read_roles": b.get("read_roles", ["user"]), "set_roles": b.get("set_roles", []),
              "detector_cfg": b.get("detectors", [])}
    for k in ("modbus", "snmp", "dnp3", "bacnet", "opcua", "http", "sql", "scale", "suppress_when", "calc",
              "states", "folder"):
        if b.get(k):
            p.meta[k] = b[k]
    for k in ("description", "eu_low", "eu_high", "decimals", "set_min", "set_max", "max_step", "confirm", "require_reason"):  # zero is a real value here
        if k in b:
            p.meta[k] = b[k]
    return p


def build_engine(store):
    """Engine from stored config. A source that fails to build is kept as an unhealthy stub, not fatal."""
    eng = Engine()
    points = store.list("point")
    held = {o["name"]: o["value"] for o in store.list("override")}  # values operators set and have not released
    for s in store.list("source"):
        if not s.get("enabled", True):
            continue
        mine = {p["name"]: p for p in points if p.get("source") == s["name"]}
        try:
            if s["type"] == "virtual":
                src = Virtual(s["name"])
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
                    src.gens[n] = p.get("generator", {"kind": "walk"})
                    if n in held:
                        src.override[n] = held[n]
            elif s["type"] in ("http_json", "sql"):
                src = (HttpJsonSource if s["type"] == "http_json" else SqlSource)(s["name"], s)
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
                src.start()
            elif s["type"] == "calc":
                src = calc.CalcSource(s["name"])
                src.engine = eng
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
            elif s["type"] == "http_in":
                auto = (lambda n: make_point(n, {}, s["name"])) if s.get("auto_create") else None
                src = HttpInSource(s["name"], bool(s.get("auto_create")), auto, s.get("max_age_days", 7))
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
            elif s["type"] == "modbus":
                src = ModbusSource(s["name"], {k: v for k, v in s.items() if k in MODBUS_KEYS or k == "link"})
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
                src.start()  # only after every point is added: the read plan is built once from them
            elif s["type"] == "sparkplug":
                auto = (lambda n: make_point(n, {}, s["name"])) if s.get("auto_create", True) else None
                src = SparkplugSource(s["name"], load_creds(s["creds"]), s.get("group", "Plant"), auto)
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
            elif s["type"] == "snmp":
                src = SnmpSource(s["name"], s)
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
                src.start()
            elif s["type"] == "dnp3":
                src = Dnp3Source(s["name"], s)
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
                src.start()
            elif s["type"] == "bacnet":
                src = BacnetSource(s["name"], s)
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
                src.start()
            elif s["type"] == "opcua":
                src = OpcuaSource(s["name"], s)
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
                src.start()
            else:
                auto = (lambda n: make_point(n, {}, s["name"])) if s.get("auto_create", True) else None
                src = MqttSource(s["name"], load_creds(s["creds"]), s.get("prefix", "durian/"),
                                 s.get("cmd_prefix", "durian-cmd/"), auto)
                for n, p in mine.items():
                    src.add(make_point(n, p, s["name"]))
        except Exception as ex:
            src = Source(s["name"])
            src.error = f"cannot start: {sentence(ex) or 'check its settings'}"
        src.meta = {"type": s.get("type"), "read_roles": s.get("read_roles", ["user"]),
                    "default_tags": s.get("default_tags") or s.get("tags") or {},
                    "default_unit": s.get("default_unit") or s.get("unit") or "", "folders": s.get("folders", {})}
        src.heartbeat, src.down_after = s.get("heartbeat_seconds"), s.get("down_after", 10.0)
        eng.sources.append(src)
    eng.sources.sort(key=lambda s: isinstance(s, calc.CalcSource))  # calculations run last, on this tick's fresh inputs
    return eng


def seed(store, lab_creds=None):
    """First-run demo config. Safe to call every start: it only writes when no source exists."""
    if store.list("source"):
        return False
    store.put("source", "demo", {"type": "virtual", "enabled": True, "read_roles": ["user"]})
    store.put("point", "boiler-temp", {"source": "demo", "unit": "C", "tags": {"area": "boiler"}, "tolerance": 0.2,
              "generator": {"kind": "walk", "start": 82, "min": 60, "max": 110, "step": 1.6, "drift": 0.15},
              "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT", "duration": 4}]})
    store.put("point", "line-pressure", {"source": "demo", "unit": "bar", "tags": {"area": "line"},
              "generator": {"kind": "sine", "offset": 6, "amplitude": 2, "period": 180},
              "detectors": [{"kind": "HIGH_LIMIT", "limit": 7.5, "level": "WARNING"}]})
    store.put("point", "pump-setpoint", {"source": "demo", "unit": "%", "tags": {"area": "line"}, "settable": True,
              "set_roles": ["user"], "generator": {"kind": "constant", "value": 40}, "logging": "ON_CHANGE"})
    store.put("dashboard", "plant-overview", {"title": "Plant overview", "owner": None, "read_roles": ["user"], "edit_roles": [], "columns": 12, "refresh": 2,
              "widgets": [
                  {"id": "temp", "type": "value", "title": "Boiler temperature", "point": "boiler-temp", "x": 0, "y": 0, "w": 3, "h": 2,
                   "decimals": 1, "rules": [{"op": ">", "value": 90, "tone": "alarm"}, {"op": ">", "value": 85, "tone": "warn"}]},
                  {"id": "press", "type": "gauge", "title": "Line pressure", "point": "line-pressure", "min": 0, "max": 10, "x": 3, "y": 0, "w": 3, "h": 2,
                   "zones": [{"from": 7.5, "to": 10, "tone": "alarm"}]},
                  {"id": "pump", "type": "setpoint", "title": "Pump setpoint", "point": "pump-setpoint", "x": 6, "y": 0, "w": 3, "h": 2},
                  {"id": "health", "type": "status", "title": "Sources", "x": 9, "y": 0, "w": 3, "h": 2},
                  {"id": "trend", "type": "chart", "title": "Boiler temperature, last hour", "points": ["boiler-temp"], "window": 3600, "rollup": "avg", "x": 0, "y": 2, "w": 6, "h": 4},
                  {"id": "alarms", "type": "alarms", "title": "Open alarms", "state": "open", "limit": 8, "x": 6, "y": 2, "w": 6, "h": 4},
                  {"id": "line", "type": "table", "title": "Line points", "tag": "area:line", "x": 0, "y": 6, "w": 6, "h": 3},
                  {"id": "note", "type": "text", "title": "About this dashboard", "x": 6, "y": 6, "w": 6, "h": 3,
                   "text": "Built from the dashboard editor. Every widget is plain data: a type, the points it reads and a place on the grid."}]})
    if lab_creds and Path(lab_creds).exists():
        store.put("source", "lab-mqtt", {"type": "mqtt", "enabled": True, "read_roles": ["user"], "creds": str(lab_creds),
                  "prefix": "durian/", "cmd_prefix": "durian-cmd/", "auto_create": True})
        store.put("point", "mqtt-temp", {"source": "lab-mqtt", "unit": "C", "tags": {"area": "boiler", "via": "mqtt"},
                  "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"},
                                {"kind": "NO_UPDATE", "seconds": 30, "level": "WARNING"}]})
    return True
