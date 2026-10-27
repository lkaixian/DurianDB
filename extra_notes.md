# Extra notes

Written 2026-10-27. These are limits and operational notes that are not open defects, so they are not rows in
`TECHNICAL_DEBT.md`. Deliberate design choices that already have an entry there stay there; this file holds the
rest.

## Reaching the console over the network

The server listens on `127.0.0.1` and speaks plain HTTP there only. On any other address it needs a certificate
and serves HTTPS. `python tools/make_cert.py plant-pc.local` writes a self-signed certificate and key. Each
browser must trust that certificate, or it must be replaced with one signed by a certificate authority; until
then the browser warns, and because the session cookie is marked `Secure` the console may not keep a session.

## MQTT lab

The commands below assume Mosquitto is installed at `C:\Program Files\mosquitto\mosquitto.exe`. The lab broker
listens on port 18883 and does not touch a broker that is already running.

```sh
python tools/mqtt_lab.py
"C:\Program Files\mosquitto\mosquitto.exe" -c lab/mqtt/mosquitto.conf
python tools/sim_publisher.py
```

## Fixed limits

- A mimic widget draws on a canvas of at most 2000 by 2000 and holds at most 80 shapes.
- A report covers at most 31 days and at most 200 points.
- A scheduled report that fails to send is not retried until the next day.
- A point's logbook keeps the newest 200 notes and drops the oldest.
- The alarm journal returns at most 200 rows per page, or 5000 for a CSV download.
- History retention defaults to 365 days, purged daily at 03:00 server local time. Set `retention_days` to 0 to
  keep everything.
