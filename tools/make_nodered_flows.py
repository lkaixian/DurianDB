"""Generate integrations/node-red/durian-flows.json: example flows for talking to DurianDB from Node-RED.

Run: python tools/make_nodered_flows.py
Secrets are never in the file. The flows read them from environment variables:
  DURIAN_URL (http://127.0.0.1:8765)  DURIAN_SOURCE (the http_in source name)
  DURIAN_INGEST_TOKEN  DURIAN_READ_TOKEN  DURIAN_WRITE_TOKEN
Each group ends by remembering its last result in global context (last_post, last_get, last_read, last_set,
last_mqtt, last_event) so you can see it in the sidebar's context tab, and by a debug node.
"""
import json
from pathlib import Path

TAB = "durian_tab"
nodes = [{"id": TAB, "type": "tab", "label": "DurianDB examples", "disabled": False,
          "info": "Examples for sending data to DurianDB and reading it back. See integrations/node-red/README.md."}]
y = 40


def add(**n):
    n.setdefault("z", TAB)
    nodes.append(n)
    return n["id"]


def comment(title, info):
    global y
    y += 30
    add(id=f"c_{title[:2]}{y}", type="comment", name=title, info=info, x=170, y=y, wires=[])
    y += 40


def func(id_, name, code, x, wires):
    return add(id=id_, type="function", name=name, func=code, outputs=1, timeout=0, noerr=0, initialize="", finalize="", libs=[], x=x, y=y, wires=[wires])


def debug(id_, x):
    return add(id=id_, type="debug", name="result", active=True, tosidebar=True, console=False, tostatus=False, complete="payload",
               targetType="msg", statusVal="", statusType="auto", x=x, y=y, wires=[])


def inject(id_, name, wires):
    return add(id=id_, type="inject", name=name, props=[{"p": "payload"}], repeat="", crontab="", once=False, onceDelay=0.1, topic="",
               payload="", payloadType="date", x=150, y=y, wires=[wires])


def http_request(id_, wires, name="DurianDB"):
    return add(id=id_, type="http request", name=name, method="use", ret="obj", paytoqs="ignore", url="", tls="", persist=False, proxy="",
               insecureHTTPParser=False, authType="", senderr=False, headers=[], x=620, y=y, wires=[wires])


def remember(id_, key, wires, x=800):
    return func(id_, "Remember result", f'global.set("{key}", {{status: msg.statusCode, body: msg.payload}});\nreturn msg;', x, wires)


HEAD = 'const base = env.get("DURIAN_URL") || "http://127.0.0.1:8765";\n'

comment("1  Push values with HTTP POST",
        "Needs an ingest token for an http_in source. One value, a batch, or a flat {point: value} map are all accepted. "
        "ts is optional (epoch seconds or milliseconds, or ISO-8601). The reply lists accepted and rejected values.")
inject("in_post", "Push now", ["fn_post"])
func("fn_post", "Build the request", HEAD + '''msg.method = "POST";
msg.url = base + "/api/ingest/" + env.get("DURIAN_SOURCE");
msg.headers = {"Authorization": "Bearer " + env.get("DURIAN_INGEST_TOKEN"), "Content-Type": "application/json"};
// a batch: several points, each with its own time
msg.payload = {values: [
  {point: "temp",  value: Math.round((20 + Math.random() * 5) * 10) / 10, ts: Date.now()},
  {point: "level", value: Math.round(Math.random() * 100) / 10, ts: Date.now()}
]};
return msg;''', 400, ["hr_post"])
http_request("hr_post", ["keep_post"])
remember("keep_post", "last_post", ["dbg_post"])
debug("dbg_post", 980)

comment("2  Push a value with HTTP GET",
        "For tools that can only issue GET. The same ingest token. Values travel in the query string.")
inject("in_get", "Push now", ["fn_get"])
func("fn_get", "Build the request", HEAD + '''msg.method = "GET";
const v = Math.round(Math.random() * 100) / 10;
msg.url = base + "/api/ingest/" + env.get("DURIAN_SOURCE") + "?point=level&value=" + v + "&ts=" + Date.now();
msg.headers = {"Authorization": "Bearer " + env.get("DURIAN_INGEST_TOKEN")};
delete msg.payload;
return msg;''', 400, ["hr_get"])
http_request("hr_get", ["keep_get"])
remember("keep_get", "last_get", ["dbg_get"])
debug("dbg_get", 980)

comment("3  Read the latest value",
        "Needs a read token. The point must be readable by the token's owner.")
inject("in_read", "Read now", ["fn_read"])
func("fn_read", "Build the request", HEAD + '''msg.method = "GET";
msg.url = base + "/api/value/" + (msg.point || "boiler-temp");
msg.headers = {"Authorization": "Bearer " + env.get("DURIAN_READ_TOKEN")};
delete msg.payload;
return msg;''', 400, ["hr_read"])
http_request("hr_read", ["keep_read"])
remember("keep_read", "last_read", ["dbg_read"])
debug("dbg_read", 980)

comment("4  Set a point (control)",
        "Needs a write token. The point must be settable and the token's owner must hold one of its set roles. Every set is audited with the token name.")
inject("in_set", "Set now", ["fn_set"])
func("fn_set", "Build the request", HEAD + '''msg.method = "POST";
msg.url = base + "/api/points/pump-setpoint/set";
msg.headers = {"Authorization": "Bearer " + env.get("DURIAN_WRITE_TOKEN"), "Content-Type": "application/json"};
msg.payload = {value: 55};
return msg;''', 400, ["hr_set"])
http_request("hr_set", ["keep_set"])
remember("keep_set", "last_set", ["dbg_set"])
debug("dbg_set", 980)

comment("5  Publish over MQTT with TLS",
        "Topic durian/<point>, payload a number or {\"value\": n}. Set the broker's TLS config to the CA file and fill in the user and password.")
inject("in_mqtt", "Publish now", ["fn_mqtt"])
func("fn_mqtt", "Build the message", '''msg.topic = "durian/mqtt-temp";
msg.payload = {value: Math.round((70 + Math.random() * 25) * 10) / 10};
return msg;''', 400, ["mq_out"])
add(id="mq_out", type="mqtt out", name="DurianDB mqtt-temp", topic="", qos="1", retain="false", respTopic="", contentType="", userProps="",
    correl="", expiry="", broker="durian_broker", x=720, y=y, wires=[])

comment("6  Receive what DurianDB publishes over MQTT",
        "Configure a publisher in DurianDB with prefix durian-out/ and the points to share. Node-RED receives them here.")
add(id="mq_in", type="mqtt in", name="durian-out/#", topic="durian-out/#", qos="1", datatype="auto-detect", broker="durian_broker", nl=False,
    rap=True, rh=0, inputs=0, x=150, y=y, wires=[["keep_mqtt"]])
func("keep_mqtt", "Remember", 'global.set("last_mqtt", {topic: msg.topic, body: msg.payload});\nreturn msg;', 400, ["dbg_mqtt"])
debug("dbg_mqtt", 700)

comment("7  Receive DurianDB event webhooks",
        "Add a handler in DurianDB: type webhook, url http://<node-red>:1880/durian/events, minimum level you care about. "
        "Each alarm raised or cleared arrives here as JSON {event, point, level, message, ts, cleared_ts}.")
add(id="wh_in", type="http in", name="POST /durian/events", url="/durian/events", method="post", upload=False, swaggerDoc="", x=170, y=y, wires=[["keep_event"]])
func("keep_event", "Remember and answer", 'global.set("last_event", msg.payload);\nmsg.statusCode = 204;\nreturn msg;', 430, ["wh_out", "dbg_event"])
add(id="wh_out", type="http response", name="204", statusCode="", headers={}, x=700, y=y, wires=[])
y += 50
debug("dbg_event", 700)

# configuration nodes, shared by the MQTT nodes
nodes.append({"id": "durian_tls", "type": "tls-config", "name": "DurianDB CA", "cert": "", "key": "", "ca": "", "certname": "", "keyname": "",
              "caname": "", "servername": "", "verifyservercert": True, "alpnprotocol": ""})
nodes.append({"id": "durian_broker", "type": "mqtt-broker", "name": "DurianDB MQTT (TLS)", "broker": "localhost", "port": "18883",
              "tls": "durian_tls", "clientid": "", "autoConnect": True, "usetls": True, "protocolVersion": "4", "keepalive": "60",
              "cleansession": True, "autoUnsubscribe": True, "birthTopic": "", "birthQos": "0", "birthRetain": "false", "birthPayload": "",
              "birthMsg": {}, "closeTopic": "", "closeQos": "0", "closeRetain": "false", "closePayload": "", "closeMsg": {}, "willTopic": "",
              "willQos": "0", "willRetain": "false", "willPayload": "", "willMsg": {}, "userProps": "", "sessionExpiry": ""})

out = Path(__file__).resolve().parent.parent / "integrations" / "node-red" / "durian-flows.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(nodes, indent=2))
print(f"wrote {out} with {len(nodes)} nodes")
