"""Test button for the source editor: try a source's settings once and say what happened, without saving anything.

HTTP JSON lists the numeric values it found (so the point's path can be picked), SQL shows the first rows, Modbus TCP and serial
check that a device answers. Administrators only: it makes the server open the connection the settings describe, exactly as
saving the source would.
"""
from . import http_source, sql_source
from .config import validate_source
from .core import sentence
from .http_source import HttpJsonSource
from .modbus import RANGES, ModbusError, ModbusException
from .modbus_source import make_client
from .sql_source import SqlSource

MAX_FOUND = 40


def _leaves(node, path=""):
    """(path, number) for every numeric value in a JSON document, in the path syntax a point uses."""
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node[:50]):
            yield from _leaves(v, f"{path}[{i}]")


def test_source(body):
    """{ok, message, found: [{name, value}]}. Never raises for a bad connection; that is the answer."""
    body = body if isinstance(body, dict) else {}
    validate = validate_source("test", body)
    if validate:
        return {"ok": False, "message": "The settings are not valid yet: " + "; ".join(f"{e['property']}: {e['message']}" for e in validate[:3]), "found": []}
    kind = body.get("type")
    try:
        if kind == "http_json":
            leaves = list(_leaves(HttpJsonSource("test", body).document()))
            return {"ok": True, "message": f"Fetched the document: {len(leaves)} numeric value(s) found. Use a path below on a point.",
                    "found": [{"name": p, "value": v} for p, v in leaves[:MAX_FOUND]]}
        if kind == "sql":
            rows = SqlSource("test", body).rows()
            return {"ok": True, "message": f"The query returned {len(rows)} row(s). The first column is the key a point uses.",
                    "found": [{"name": str(r[0]), "value": r[1] if len(r) > 1 else None} for r in rows[:MAX_FOUND]]}
        if kind == "modbus":
            client = make_client(body)
            try:
                with client.lock:
                    try:
                        client.read_regs(1, RANGES["holding"], 0, 1)
                        said = "The device answered a read of holding register 0 at address 1."
                    except ModbusException as e:  # it answered, just not with data: the link works
                        said = f"The device answered (it refused holding register 0 at address 1: {sentence(e)}). The link works."
                    finally:
                        client.end_cycle()
            finally:
                client.close()
            return {"ok": True, "message": said, "found": []}
    except ModbusError as e:
        return {"ok": False, "message": f"No answer from the device: {sentence(e)}", "found": []}
    except Exception as e:  # any transport or parsing failure is the test result
        return {"ok": False, "message": f"Could not test this source: {sentence(e) or 'it did not answer'}", "found": []}
    return {"ok": False, "message": "This source type cannot be tested from here.", "found": []}
