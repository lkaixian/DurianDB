"""Publish simulated sensor values to the lab broker over TLS. Run: python tools/sim_publisher.py [seconds]"""
import json
import math
import ssl
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

c = json.loads((Path(__file__).resolve().parent.parent / "lab" / "mqtt" / "creds.json").read_text())
cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
cl.tls_set(ca_certs=c["ca"], tls_version=ssl.PROTOCOL_TLS_CLIENT)
cl.username_pw_set(c["user"], c["password"])
cl.connect(c["host"], c["port"])
cl.loop_start()
t0, dur = time.time(), float(sys.argv[1]) if len(sys.argv) > 1 else 1e9
while time.time() - t0 < dur:
    t = time.time() - t0
    cl.publish("durian/mqtt-temp", json.dumps({"value": round(70 + 25 * math.sin(t / 20), 2)}), qos=1)
    cl.publish("durian/mqtt-flow", round(12 + 3 * math.sin(t / 7), 2), qos=1)
    time.sleep(1)
cl.loop_stop()
