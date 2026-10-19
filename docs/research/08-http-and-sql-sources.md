# 08 HTTP JSON and SQL sources

Written 2026-10-20. Sources: the vendor's public data source list (which names both types), and defaults and validation reported by the lab instance's API when disabled sources were created (black-box).

## Observed in the studied product

HTTP JSON retriever:
- Required: URL. Defaults: poll period 5 minutes, timeout 30 seconds, 2 retries.
- Has an optional second URL for writing a value back ("set point URL").
- TLS: `verifyServerCertificate` and `verifyServerHostname` default to **false**, with optional trust of the system PKI CA or a pasted certificate.
- Points read values out of the returned JSON (this retriever type has its own point locator type, `PL.HTTP_JSON_RETRIEVER`).

SQL source:
- Required: a JDBC driver class and a connection URL, plus a select statement. The driver class is checked by loading it at validation time ("Driver class loading problem" otherwise).
- So it can reach any database a driver exists for, and runs whatever statement the administrator wrote.

## DurianDB's versions (`durian/http_source.py`, `durian/sql_source.py`, base in `durian/polled.py`)

| | Studied product | DurianDB |
|---|---|---|
| HTTP TLS | Verification off by default | Always on. Optional private CA file. |
| HTTP redirects | not stated | Never followed (a poll cannot be bounced to another host). |
| HTTP size | not stated | Capped (`max_bytes`, default 1 MB). |
| HTTP auth | headers | Headers, or a token file read each request so a rotated token is picked up, instead of a secret in the stored config. |
| HTTP write | separate URL | Not supported. The source is read-only. |
| JSON lookup | point locator | Path such as `sensors.temp` or `items[0].value`, numbers and booleans only. |
| SQL targets | any JDBC database | One SQLite file, opened read-only. |
| SQL statement | any | One `SELECT`, first column a key, second a number. Validation rejects anything else; the connection is `query_only` and has a runtime limit, so a validation bug still cannot write or hang. |
| Failure | retries, then alarm | Whole-fetch failure backs off (period x scale^failures, capped); a point whose value is missing or not numeric is reported as rejected and the rest still update. |

Both run in a background thread, so a slow server never stalls the API or the engine.

## Not built, and why

BACnet, DNP3, OPC DA and UA, SNMP, serial ASCII and the other protocols in the studied product's list each need either a large protocol implementation or a third-party library that is not installed here. They are the natural next exercises, in this order of effort: SNMP v2c (a small UDP protocol with BER encoding), OPC UA (use a library), BACnet/IP (a large stack), DNP3 (a large stack).

## Checked

`tests/test_pull_sources.py`: path lookup including failures, config validation, scaling, partial rejection, redirect refusal, oversized and non-JSON replies, token file header, back-off growth and recovery, SQL key lookup and updates, and that the SQL connection refuses to write even when validation is bypassed.
