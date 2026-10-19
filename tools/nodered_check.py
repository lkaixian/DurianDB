"""End-to-end check with a real Node-RED: deploys integrations/node-red/durian-flows.json, presses every inject node, and
verifies what arrived in DurianDB and what came back to Node-RED.

Needs: node-red on PATH, the lab MQTT broker from tools/mqtt_lab.py running (lab/mqtt/creds.json), free ports 8769 and 18880.
Run:   python tools/nodered_check.py
"""
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / "lab"
DURIAN, NR = "http://127.0.0.1:8769", "http://127.0.0.1:18880"
PW = "nodered-check-password-1"
creds = json.loads((LAB / "mqtt" / "creds.json").read_text())
procs, tmp = [], Path(tempfile.mkdtemp())


def fail(msg):
    print("FAIL:", msg)
    cleanup()
    sys.exit(1)


def cleanup():
    for p in procs:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)


def wait(url, what, seconds=60):
    for _ in range(seconds * 2):
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return
        except Exception:
            time.sleep(0.5)
    fail(f"{what} did not start")


# ---- DurianDB
env = {**os.environ, "DURIAN_DB": str(tmp / "durian.db"), "DURIAN_ADMIN_PASSWORD": PW, "DURIAN_PORT": "8769", "DURIAN_TICK": "1",
       "DURIAN_MQTT_CREDS": str(LAB / "mqtt" / "creds.json")}
procs.append(subprocess.Popen([sys.executable, "-m", "durian.server"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
wait(DURIAN + "/api/health", "DurianDB")
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def api(method, path, body=None, base=DURIAN):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Requested-With": "durian", "Content-Type": "application/json"})
    try:
        with op.open(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


assert api("POST", "/api/login", {"username": "admin", "password": PW})[0] == 200
for kind, name, body in (
        ("source", "nodered", {"type": "http_in", "read_roles": ["user"]}),
        ("point", "temp", {"source": "nodered", "unit": "C"}), ("point", "level", {"source": "nodered"}),
        ("publisher", "to-nodered", {"type": "mqtt", "creds": str(LAB / "mqtt" / "creds.json"), "prefix": "durian-out/", "points": ["temp"]}),
        ("handler", "to-nodered", {"type": "webhook", "url": NR + "/durian/events", "min_level": "WARNING"})):
    s, r = api("PUT", f"/api/config/{kind}/{name}", body)
    if s != 200:
        fail(f"setup {kind}/{name}: {s} {r}")
tokens = {}
for name, scope, extra in (("ingest", "ingest", {"source": "nodered"}), ("read", "read", {}), ("write", "write", {})):
    s, r = api("POST", "/api/tokens", {"name": "nr-" + name, "scope": scope, **extra})
    tokens[name] = r["token"]

# ---- Node-RED
ud = tmp / "nr"
ud.mkdir()
nr_env = {**os.environ, "DURIAN_URL": DURIAN, "DURIAN_SOURCE": "nodered", "DURIAN_INGEST_TOKEN": tokens["ingest"],
          "DURIAN_READ_TOKEN": tokens["read"], "DURIAN_WRITE_TOKEN": tokens["write"]}
exe = shutil.which("node-red") or fail("node-red is not on PATH")
procs.append(subprocess.Popen([exe, "--userDir", str(ud), "--port", "18880"], env=nr_env, stdout=open(tmp / "nr.log", "w"), stderr=subprocess.STDOUT))
wait(NR + "/flows", "Node-RED", 90)
flows = json.loads((ROOT / "integrations" / "node-red" / "durian-flows.json").read_text())
for n in flows:
    if n["type"] == "tls-config":
        n["ca"] = creds["ca"]
    if n["type"] == "mqtt-broker":
        n.update(port=str(creds["port"]), credentials={"user": creds["user"], "password": creds["password"]})
req = urllib.request.Request(NR + "/flows", method="POST", data=json.dumps(flows).encode(),
                             headers={"Content-Type": "application/json", "Node-RED-Deployment-Type": "full"})
try:
    urllib.request.urlopen(req, timeout=30).read()
except urllib.error.HTTPError as e:
    fail(f"deploy: {e.code} {e.read()[:300]}")
time.sleep(4)  # let the MQTT nodes connect


def inject(node_id):
    urllib.request.urlopen(urllib.request.Request(NR + f"/inject/{node_id}", method="POST", data=b""), timeout=10).read()


def context(key, wait_s=15):
    for _ in range(wait_s * 2):
        try:
            r = json.loads(urllib.request.urlopen(NR + f"/context/global/{key}", timeout=5).read())
            if os.environ.get("NR_DEBUG"):
                print("   context", key, "->", repr(r)[:300])
            if r.get("format") != "undefined" and r.get("msg") not in (None, ""):  # "undefined" means the flow has not run yet
                v = r["msg"]
                return json.loads(v) if isinstance(v, str) and v[:1] in "{[" else v
        except Exception:
            pass
        time.sleep(0.5)
    return None


results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"   {detail}" if detail and not ok else ""))


# 1 POST push, 2 GET push
inject("in_post")
r = context("last_post")
check("HTTP POST push accepted by DurianDB", bool(r) and r["status"] == 200 and r["body"]["accepted"] == 2 and r["body"]["rejected"] == [], r)
inject("in_get")
r = context("last_get")
check("HTTP GET push accepted by DurianDB", bool(r) and r["status"] == 200 and r["body"]["accepted"] == 1, r)
time.sleep(3)
s, pts = api("GET", "/api/points")
vals = {p["name"]: p["last"] for p in pts}
check("pushed values are live points in DurianDB", vals.get("temp") is not None and vals.get("level") is not None, vals)
s, hist = api("GET", "/api/series?point=temp&window=300&rollup=last&bucket=1")
check("source timestamps (milliseconds from Node-RED) were kept", s == 200 and len(hist["items"]) >= 1 and abs(hist["items"][-1]["ts"] - time.time()) < 60, hist)

# 3 read, 4 set
inject("in_read")
r = context("last_read")
check("HTTP GET read returns the latest value", bool(r) and r["status"] == 200 and r["body"]["name"] == "boiler-temp" and r["body"]["value"] is not None, r)
inject("in_set")
r = context("last_set")
check("HTTP POST set accepted with a write token", bool(r) and r["status"] == 200 and r["body"] == {"ok": True}, r)
time.sleep(3)
s, pts = api("GET", "/api/points")
check("the set value reached the point", {p["name"]: p["last"] for p in pts}.get("pump-setpoint") == 55, {p["name"]: p["last"] for p in pts}.get("pump-setpoint"))
s, audit = api("GET", "/api/audit")
check("the set was audited with the token name", any("via token nr-write" in a["detail"] for a in audit), audit[:3])

# 5 MQTT publish over TLS -> DurianDB point and alarm -> 7 webhook back to Node-RED
for _ in range(1):
    inject("in_mqtt")
time.sleep(4)
s, pts = api("GET", "/api/points")
mq = {p["name"]: p["last"] for p in pts}.get("mqtt-temp")
check("MQTT over TLS from Node-RED reached DurianDB", mq is not None and 70 <= mq <= 95, mq)
for _ in range(12):  # random 70 to 95: keep publishing until one crosses the 90 limit
    s, evs = api("GET", "/api/events")
    if any(e["point"] == "mqtt-temp" for e in evs):
        break
    inject("in_mqtt")
    time.sleep(2.5)
ev = context("last_event", 20)
check("DurianDB's webhook reached Node-RED when the alarm was raised", bool(ev) and ev.get("point") == "mqtt-temp" and ev.get("event") in ("raised", "cleared"), ev)

# 6 DurianDB publisher -> Node-RED MQTT in
inject("in_post")  # new value for temp, which the publisher forwards
m = context("last_mqtt", 20)
check("DurianDB's MQTT publisher reached Node-RED", bool(m) and m["topic"] == "durian-out/temp" and "value" in json.dumps(m["body"]), m)

# the token boundaries hold when the caller is Node-RED too
r = urllib.request.Request(DURIAN + "/api/points", headers={"Authorization": "Bearer " + tokens["ingest"]})
try:
    urllib.request.urlopen(r, timeout=5)
    check("an ingest token cannot read other data", False)
except urllib.error.HTTPError as e:
    check("an ingest token cannot read other data", e.code == 403)

cleanup()
print(f"\n{sum(results)} of {len(results)} checks passed")
sys.exit(0 if all(results) else 1)
