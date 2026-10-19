# Connecting Node-RED to DurianDB

Two ways in, one way back, and everything is verified against a real Node-RED 5 by `python tools/nodered_check.py`.

| Direction | How | Used for |
|---|---|---|
| Node-RED to DurianDB | **HTTP POST or GET** to `/api/ingest/<source>` with an ingest token | Pushing readings, with or without the device's own timestamp |
| Node-RED to DurianDB | **MQTT over TLS** to the topics `durian/<point>` | Same, through your broker |
| DurianDB to Node-RED | **MQTT** (a publisher) or an **HTTP webhook** to a Node-RED `http in` node | Sharing values, receiving alarms |
| Node-RED reading DurianDB | **HTTP GET** `/api/value/<point>` with a read token | Using DurianDB values in flows |
| Node-RED controlling DurianDB | **HTTP POST** `/api/points/<point>/set` with a write token | Setting points, audited under the token's name |

## 1. Set up DurianDB

1. **Config, Sources, New source**, start from nothing and use `{"type": "http_in", "read_roles": ["user"]}`. Name it for example `nodered`. Add `"auto_create": true` if points should appear the first time a value arrives, otherwise add each point under **Config, Points** (`{"source": "nodered", "unit": "C"}`).
2. **Tokens, Create a token**:
   - **ingest** on that source, for pushing values. It can reach nothing else.
   - **read**, to read values back. It acts as a user and sees only what that user may see.
   - **write**, if flows should set points. The point must be settable and the user must hold one of its set roles.
   Copy each token when it is shown. It is shown once and only a hash is kept.
3. Optional, for events and sharing: **Config, Handlers** (`{"type": "webhook", "url": "http://127.0.0.1:1880/durian/events", "min_level": "WARNING"}`) and **Config, Publishers** (`{"type": "mqtt", "creds": "<path to creds.json>", "prefix": "durian-out/", "points": ["temp"]}`).

## 2. Set up Node-RED

Start Node-RED with these environment variables (the flows never contain secrets):

```
DURIAN_URL=http://127.0.0.1:8765
DURIAN_SOURCE=nodered
DURIAN_INGEST_TOKEN=dtk_...
DURIAN_READ_TOKEN=dtk_...
DURIAN_WRITE_TOKEN=dtk_...
```

Import `durian-flows.json` (menu, Import). It has seven groups: POST push, GET push, read a value, set a point, MQTT publish, MQTT subscribe, webhook receiver. Each group ends in a debug node and remembers its last result in global context (`last_post`, `last_get`, `last_read`, `last_set`, `last_mqtt`, `last_event`).

For the MQTT groups open **DurianDB MQTT (TLS)**: set the host and port of your broker, the user and password, and in its TLS config the CA file that signed the broker's certificate. Leave **Verify server certificate** on. With the lab broker from `tools/mqtt_lab.py` the port is 18883 and the CA is `lab/mqtt/ca.pem`.

## 3. What to send

`POST /api/ingest/<source>` with `Authorization: Bearer <ingest token>` and a JSON body in any of these shapes:

```json
{"point": "temp", "value": 21.5}
{"point": "temp", "value": 21.5, "ts": 1700000000123}
{"values": [{"point": "temp", "value": 21.5, "ts": "2023-11-14T22:13:20Z"}, {"point": "level", "value": 7.5}]}
{"temp": 21.5, "level": 7.5}
```

`ts` is optional: epoch seconds or milliseconds, or ISO-8601. Without it the arrival time is used. A value is refused when its point is unknown (unless `auto_create`), the value is not a finite number, the timestamp is more than a minute in the future, older than the source accepts (7 days by default) or older than the newest value already received for that point, which keeps history in order.

For tools that can only GET: `GET /api/ingest/<source>?point=temp&value=21.5&ts=1700000000` with the same header.

The reply tells you exactly what happened:

```json
{"accepted": 1, "rejected": [{"index": 1, "point": "ghost", "reason": "unknown point (add it under Config, or turn on auto_create for this source)"}]}
```

Status 200 when something was accepted, 422 when nothing was. Use a switch node on `msg.statusCode` to handle failures.

MQTT: publish to `durian/<point>` with a bare number or `{"value": 21.5}`. Values that are not finite numbers are dropped and counted on the source's health row.

## 4. When it does not work

| You see | Meaning |
|---|---|
| 401, `invalid, expired or disabled token` | The token is wrong, was deleted or disabled, expired, or its owner was deleted. |
| 403, `this ingest token does not allow that` | An ingest token only reaches its own source. Use a read or write token for everything else. |
| 403, `pushing values needs an ingest token` | You are using a browser session or another token type on the ingest URL. |
| 404, `unknown ingest source` | The source name is wrong or it is not of type `http_in`. |
| 422 with reasons | Nothing was accepted. Read `rejected`. |
| Nothing at all | Check `DURIAN_URL` is reachable from where Node-RED runs, and that the `Authorization` header is set (function node, not the http request node's own auth). |

## 5. Security notes

- A token never grants administration, even when its owner is an administrator. Tokens can be disabled or deleted at any time under **Tokens**, and are removed with their owner.
- Prefer one ingest token per sender, so one can be revoked alone.
- DurianDB listens on plain HTTP on 127.0.0.1. If Node-RED is on another machine, put a reverse proxy with HTTPS in front and use an `https://` URL (Node-RED's http request node verifies certificates unless told otherwise).
- Webhooks are sent by DurianDB without a secret. Treat the receiving URL as internal.
