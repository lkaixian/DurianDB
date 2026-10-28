"""A config body with a key the server does not read is refused by name, and every key the console really posts still saves.

Run: python tests/test_config_keys.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _support import new_store  # noqa: E402
from durian.app import ApiError, App  # noqa: E402
from durian.config import seed  # noqa: E402

store = new_store()
seed(store)
app = App(store)
admin = {"name": "admin", "roles": {"superadmin"}}
tmp = Path(tempfile.mkdtemp())


def call(method, path, body=None):
    try:
        return app.handle(method, path, {}, body, admin)
    except ApiError as e:
        return e.status, {"error": str(e), "errors": e.errors}


def props(r):
    return [e["property"] for e in r[1].get("errors", [])]


# ---- a typo'd safety setting on a point is refused by name and never stored (the C15 defect)
status, payload = call("PUT", "/api/config/point/typo", {"source": "demo", "max_stepp": 5})
assert status == 422, (status, payload)
assert "max_stepp" in props((status, payload)), payload
assert store.get("point", "typo") is None, "a refused body must not be stored"

# two wrong keys are both named, not one blob complaint
status, payload = call("PUT", "/api/config/point/typo", {"source": "demo", "max_stepp": 5, "set_minn": 1})
assert status == 422 and {"max_stepp", "set_minn"} <= set(props((status, payload))), payload

# an edit carrying a typo leaves the stored good version exactly as it was
assert call("PUT", "/api/config/point/typo", {"source": "demo", "max_step": 5, "settable": True})[0] == 200
before = store.get("point", "typo")
status, payload = call("PUT", "/api/config/point/typo", {"source": "demo", "max_stepp": 9, "settable": True})
assert status == 422 and "max_stepp" in props((status, payload)), payload
assert store.get("point", "typo") == before, "the previous version is still in place"

# ---- unknown keys are refused on every config kind, by their own name
JUNK = {"source": ("k-junk-src", "enabledd"), "point": ("k-junk-point", "max_stepp"), "handler": ("k-junk-h", "urll"),
        "publisher": ("k-junk-p", "resendd"), "watchlist": ("k-junk-w", "tagz"),
        "maintenance": ("k-junk-m", "modes"), "emailgroup": ("k-junk-e", "addressz")}
VALID = {"source": {"type": "virtual"}, "point": {"source": "demo"}, "handler": {"type": "webhook", "url": "http://127.0.0.1:9/h"},
         "publisher": {"type": "http", "url": "http://127.0.0.1:9/in", "points": ["*"]}, "watchlist": {"tags": {"area": "boiler"}},
         "maintenance": {"mode": "suppress", "scope": {"points": ["boiler-temp"]}, "schedule": {"type": "manual"}},
         "emailgroup": {"addresses": ["a@example.com"]}}
for kind, (name, junk) in JUNK.items():
    assert call("PUT", f"/api/config/{kind}/{name}", VALID[kind])[0] == 200, (kind, VALID[kind])
    status, payload = call("PUT", f"/api/config/{kind}/{name}", {**VALID[kind], junk: 1})
    assert status == 422 and junk in props((status, payload)), (kind, status, payload)

# ---- the guard does not misfire: a full realistic body for each kind still saves
creds = tmp / "creds.json"  # the fields load_creds names, never a secret in config
creds.write_text(json.dumps({"host": "127.0.0.1", "port": 1883, "ca": "ca.pem", "user": "u", "password": "p"}))
token, ca, db = tmp / "token.json", tmp / "ca.pem", tmp / "plant.db"
token.write_text('{"token": "t"}')
ca.write_text("ca")
db.write_text("")
smtp = tmp / "smtp.json"
smtp.write_text('{"user": "u", "password": "p"}')

COMMON = {"enabled": True, "read_roles": ["user"], "heartbeat_seconds": 10, "down_after": 5,
          "default_tags": {"site": "x"}, "default_unit": "C", "tags": {"legacy": "yes"}, "unit": "C",
          "folders": {"Plant": {"tags": {"area": "plant"}, "unit": "C"}}}
SOURCES = {
    "k-virtual": {"type": "virtual", **COMMON},
    "k-calc": {"type": "calc", **COMMON},
    "k-mqtt": {"type": "mqtt", **COMMON, "enabled": False, "creds": str(creds), "prefix": "durian/",
               "cmd_prefix": "durian-cmd/", "auto_create": True},
    "k-sparkplug": {"type": "sparkplug", **COMMON, "enabled": False, "creds": str(creds), "group": "Plant", "auto_create": True},
    "k-modbus": {"type": "modbus", **COMMON, "enabled": False, "link": {"kind": "tcp", "host": "127.0.0.1", "port": 502},
                 "framing": "tcp", "retries": 2, "max_read_bits": 100, "max_read_regs": 10, "max_write_regs": 10,
                 "timeout": 1, "period": 5, "backoff_max": 60, "backoff_scale": 2, "persistent": True,
                 "contiguous_only": True, "multiple_writes_only": False, "monitor": True},
    "k-snmp": {"type": "snmp", **COMMON, "enabled": False, "host": "127.0.0.1", "port": 161, "community": "public",
               "timeout": 2, "period": 5, "backoff_max": 60, "backoff_scale": 2},
    "k-dnp3": {"type": "dnp3", **COMMON, "enabled": False, "dnp3": {"address": "127.0.0.1:20000", "outstation": 1},
               "timeout": 2, "period": 5, "backoff_max": 60, "backoff_scale": 2},
    "k-bacnet": {"type": "bacnet", **COMMON, "enabled": False, "bacnet": {"address": "127.0.0.1:47808", "device": 1001},
                 "timeout": 2, "period": 5, "backoff_max": 60, "backoff_scale": 2},
    "k-opcua": {"type": "opcua", **COMMON, "enabled": False, "opcua": {"address": "127.0.0.1:14360", "path": "plant",
                 "security_policy": "None", "security_mode": "None"}, "timeout": 2, "period": 5, "backoff_max": 60, "backoff_scale": 2},
    "k-http": {"type": "http_json", **COMMON, "enabled": False, "url": "http://127.0.0.1:9/data",
               "headers": {"Accept": "application/json"}, "token_file": str(token), "ca": str(ca), "max_bytes": 1024,
               "timeout": 5, "period": 10, "backoff_max": 60, "backoff_scale": 2},
    "k-sql": {"type": "sql", **COMMON, "enabled": False, "path": str(db), "query": "select name, value from readings",
              "timeout": 5, "period": 10, "backoff_max": 60, "backoff_scale": 2},
    "k-http-in": {"type": "http_in", **COMMON, "auto_create": True, "max_age_days": 7},
    "k-system": {"type": "system", **COMMON, "path": str(db)},
}
for name, body in SOURCES.items():
    assert call("PUT", f"/api/config/source/{name}", body)[0] == 200, (name, call("PUT", f"/api/config/source/{name}", body))

# every point key the form and validators know, on the source type that takes it
POINTS = {
    "k-full": {"source": "k-virtual", "logging": "INTERVAL", "log_interval": 60, "log_mode": "avg", "tolerance": 0.1,
               "tags": {"area": "boiler"}, "unit": "C", "display_unit": "F", "settable": True, "retention_days": 30,
               "read_roles": ["user"], "set_roles": ["user"], "datatype": "number",
               "detectors": [{"kind": "HIGH_LIMIT", "limit": 90, "level": "URGENT"}], "description": "full body",
               "eu_low": 0, "eu_high": 100, "set_min": 1, "set_max": 99, "decimals": 2, "max_step": 5, "confirm": True,
               "require_reason": True, "min_write_gap": 30, "scan": 2, "folder": "Plant/Boiler",
               "states": {"0": "Stopped", "1": "Running"},
               "suppress_when": {"point": "boiler-temp", "op": ">", "value": 1},
               "generator": {"kind": "walk", "start": 1, "min": 0, "max": 10, "step": 1, "drift": 0.1}},
    "k-modbus-p": {"source": "k-modbus", "modbus": {"unit": 1, "range": "holding", "address": 0, "type": "float32",
                   "bit": 0, "byte_order": "big", "word_order": "little", "multiplier": 1, "additive": 0}},
    "k-snmp-p": {"source": "k-snmp", "snmp": {"address": "1.3.6.1.2.1.1.5.0"}},
    "k-dnp3-p": {"source": "k-dnp3", "dnp3": {"group": 30, "index": 0}},
    "k-bacnet-p": {"source": "k-bacnet", "bacnet": {"object_type": "analogInput", "instance": 1, "property": "presentValue"}},
    "k-opcua-p": {"source": "k-opcua", "opcua": {"node": "ns=2;i=85"}},
    "k-http-p": {"source": "k-http", "http": {"path": "sensors.temp"}, "scale": {"multiplier": 1, "additive": 0}},
    "k-sql-p": {"source": "k-sql", "sql": {"key": "tank1"}, "scale": {"multiplier": 1, "additive": 0}},
    "k-calc-p": {"source": "k-calc", "calc": {"expression": 'p("boiler-temp") * 2'}},
}
for name, body in POINTS.items():
    assert call("PUT", f"/api/config/point/{name}", body)[0] == 200, (name, call("PUT", f"/api/config/point/{name}", body))

assert call("PUT", "/api/config/emailgroup/k-ops", {"addresses": ["a@example.com", "b@example.com"]})[0] == 200
assert call("PUT", "/api/config/watchlist/k-wl", {"tags": {"area": "boiler"}, "points": ["boiler-temp"],
    "name_like": "boiler*", "text_like": "temperature", "read_roles": ["user"],
    "query": {"folder": "Plant", "tags": ["area:boiler"], "source": "k-virtual", "name_like": "boiler"}})[0] == 200

HANDLERS = {
    "k-hook": {"type": "webhook", "url": "http://127.0.0.1:9/hook", "min_level": "WARNING", "enabled": True,
               "scope": {"points": ["boiler-temp"], "tags": {"area": "boiler"}, "sources": ["k-virtual"]}},
    "k-slack": {"type": "slack", "url": "https://hooks.slack.com/services/x/y/z", "min_level": "URGENT",
                "enabled": True, "on_clear": True},
    "k-mail": {"type": "email", "to": ["a@example.com"], "from": "durian@example.com",
               "smtp": {"host": "127.0.0.1", "port": 25, "security": "none"}, "creds": str(smtp),
               "on_clear": True, "escalate_after": 5, "escalate_to": ["b@example.com"], "groups": ["k-ops"],
               "subject": "[{level}] {point}", "body": "{message}", "min_level": "WARNING", "enabled": True,
               "scope": {"tags": {"area": "boiler"}}},
    "k-report": {"type": "report", "to": ["a@example.com"], "from": "durian@example.com",
                 "smtp": {"host": "127.0.0.1", "port": 25, "security": "none"}, "hour": 7, "hours": 24,
                 "watchlist": "k-wl", "enabled": True},
    "k-set": {"type": "setpoint", "point": "boiler-temp", "value": 0, "clear_value": 1, "release_on_clear": True,
              "min_level": "URGENT", "enabled": True},
}
for name, body in HANDLERS.items():
    assert call("PUT", f"/api/config/handler/{name}", body)[0] == 200, (name, call("PUT", f"/api/config/handler/{name}", body))

PUBLISHERS = {
    "k-pub-mqtt": {"type": "mqtt", "creds": str(creds), "prefix": "durian-out/", "points": ["*"], "enabled": False},
    "k-pub-http": {"type": "http", "url": "http://127.0.0.1:9/in", "creds": str(token), "resend": 60,
                   "points": ["boiler-temp"], "enabled": False},
    "k-pub-sp": {"type": "sparkplug", "creds": str(creds), "group": "Plant", "node": "edge-1", "device": "dev1",
                 "points": ["*"], "enabled": False},
}
for name, body in PUBLISHERS.items():
    assert call("PUT", f"/api/config/publisher/{name}", body)[0] == 200, (name, call("PUT", f"/api/config/publisher/{name}", body))

MAINT = {
    "k-maint-weekly": {"enabled": True, "mode": "suppress", "reason": "pump swap",
                       "scope": {"points": ["boiler-temp"], "tags": {"area": "boiler"}, "sources": ["k-virtual"]},
                       "schedule": {"type": "weekly", "days": [0, 1], "start": "22:00", "end": "06:00"}},
    "k-maint-once": {"mode": "drop", "scope": {"points": ["boiler-temp"]},
                     "schedule": {"type": "once", "from": 1000, "to": 2000}, "active": False},
    "k-maint-manual": {"mode": "acknowledge", "scope": {"sources": ["k-virtual"]},
                       "schedule": {"type": "manual"}, "active": True},
}
for name, body in MAINT.items():
    assert call("PUT", f"/api/config/maintenance/{name}", body)[0] == 200, (name, call("PUT", f"/api/config/maintenance/{name}", body))

# the guard reads keys only: a refused save is refused before the rollback path, so nothing was started or stored
assert store.get("point", "typo") == before
print("ok")
