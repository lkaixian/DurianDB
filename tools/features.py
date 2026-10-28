"""Feature inventory: what DurianDB does, by category, and the evidence for each claim.

    python tools/features.py check        # every path exists, ids unique, names known, implemented entries tested
    python tools/features.py report       # text table by category
    python tools/features.py html         # rewrite the block in docs/progress.html (html --check: exit 1 if stale)
    python tools/features.py svg          # rewrite docs/feature-stack.svg (svg --check: exit 1 if stale)
    python tools/features.py json        # dump the registry

Mango module names (names only, per docs/00-clean-room-policy.md) and the functions named in
docs/research/10 and 11 are the checklist; the evidence is what the repository itself holds.
status: implemented | partial | declined | planned. A declined or planned entry carries the reason in `note`.
"""
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUSES = ("implemented", "partial", "declined", "planned")
CATEGORIES = ("Data sources", "Publishers & outputs", "Alarms & detectors", "Events & handlers", "Points & data model",
              "Dashboards & UI", "Reports & export", "Security & auth", "Analytics & ML", "Operations & resilience",
              "Storage", "Scripting & meta", "Hardware-only")
START, END = "<!-- features:start -->", "<!-- features:end -->"
ANCHOR = "  <section>\n    <h2>Security, in the order it was built</h2>"
PAGE = os.path.join("docs", "progress.html")

DS, PUB, ALM, EVT, PT, UI, REP, SEC, ML, OPS, STO, SCR, HW = CATEGORIES


def f(id, category, name, status, evidence="", note=""):
    return {"id": id, "category": category, "name": name, "status": status, "evidence": evidence.split(), "note": note}


REGISTRY = [
    # ---- Data sources
    f("ds-virtual", DS, "Virtual generator (Mango virtualDS)", "implemented", "durian/config.py tests/test_core.py", "Sine, ramp, random and similar generators."),
    f("ds-system", DS, "Host vitals source (Mango vmstat, internal)", "partial", "durian/config.py tests/test_system_source.py", "Free disk bytes, process CPU seconds, CPU count; stdlib only, so no memory or load-average series."),
    f("ds-modbus", DS, "Modbus TCP / RTU / ASCII (Mango modbus)", "implemented", "durian/modbus.py durian/modbus_source.py durian/modbus_sim.py tests/test_modbus.py tests/test_modbus_source.py tests/test_modbus_finite.py", "Reads and writes; RTU only proven over a socket URL, never a real COM port (debt U1)."),
    f("ds-mqtt", DS, "MQTT subscriber over TLS (Mango mqttClientDataSource)", "implemented", "durian/mqtt_source.py tests/test_mqtt_source.py tests/test_mqtt_health.py tests/test_tls.py", "One value per point per poll, the latest message (deliberate, debt section 6)."),
    f("ds-sparkplug", DS, "Sparkplug B source", "partial", "durian/sparkplug_source.py durian/sparkplug_codec.py tests/test_sparkplug_source.py tests/test_sparkplug_codec.py", "Read-only: no command topics, STATE or rebirth request; never met a real broker (debt U9)."),
    f("ds-snmp", DS, "SNMP source (Mango snmp)", "partial", "durian/snmp_source.py tests/test_snmp_source.py", "v2c GET and a bounded GETNEXT walk only: no v3, traps or table model; fake agent only (debt U8)."),
    f("ds-dnp3", DS, "DNP3 over TCP (Mango dnp3)", "implemented", "durian/dnp3_source.py tests/test_dnp3_source.py", "Read-only master; fake outstation only (debt U10)."),
    f("ds-bacnet", DS, "BACnet/IP (Mango BACnet)", "implemented", "durian/bacnet_source.py tests/test_bacnet_source.py", "Read-only; fake device only (debt U11)."),
    f("ds-opcua", DS, "OPC UA client", "partial", "durian/opcua_source.py tests/test_opcua_source.py", "SecurityPolicy None only; no Browse or subscriptions; fake server only (debt U12)."),
    f("ds-opcda", DS, "OPC DA (Mango opcda)", "declined", "TECHNICAL_DEBT.md", "Needs Windows COM/DCOM, which is not stdlib."),
    f("ds-http-poll", DS, "HTTP JSON poll (Mango http)", "implemented", "durian/http_source.py durian/polled.py tests/test_pull_sources.py", "No redirects followed; scaling per point."),
    f("ds-http-push", DS, "HTTP ingest push with scoped tokens", "implemented", "durian/ingest.py tests/test_ingest.py tests/test_backfill.py", "Explicit old timestamps backfill history without moving the point's last value."),
    f("ds-line-protocol", DS, "Line-protocol ingest (Mango lineProtocolDataSource)", "partial", "durian/ingest.py tests/test_ingest.py tests/test_ingest_line.py", "Tags are ignored and timestamps are always nanoseconds (debt section 6)."),
    f("ds-sql", DS, "Read-only SQL source (Mango sqlds)", "implemented", "durian/sql_source.py tests/test_pull_sources.py", "SELECT only."),
    f("ds-file", DS, "Text file source (Mango dataFile / asciiFile)", "implemented", "durian/file_source.py tests/test_file_source.py", "name=value or name,value lines; bounded size and lines; device names refused on Windows."),
    f("ds-haystack", DS, "Project Haystack (Mango Haystack)", "implemented", "durian/haystack_source.py tests/test_haystack_source.py", "The read op; authentication beyond a Bearer token never ran (debt U13)."),
    f("ds-egauge", DS, "eGauge meters (Mango egauge)", "implemented", "durian/egauge_source.py tests/test_egauge_source.py", "Parses the documented inst XML; fake server only (debt U13)."),
    f("ds-pop3", DS, "POP3 mailbox values (Mango pop3)", "implemented", "durian/pop3_source.py tests/test_pop3_source.py", "ssl by default, ReDoS-guarded patterns, 2000-character match cap (debt W1)."),
    f("ds-calc", DS, "Calculated points from a checked expression language", "implemented", "durian/calc.py tests/test_calc.py tests/test_calc_health.py", "No eval; see Scripting & meta for the declined scripted variant."),
    f("ds-scan", DS, "Scan classes and source-down alarms", "implemented", "durian/core.py tests/test_scan_classes.py tests/test_source_down.py tests/test_core.py", "Per-point scan multiplier, comms health per source, back-off."),
    f("ds-test-source", DS, "Test-this-source button", "implemented", "durian/testsource.py tests/test_testsource.py", "Runs on a background worker."),
    f("ds-tcpip", DS, "Generic TCP/IP data source (Mango TCPIP)", "planned", "", "Only a TCP publisher exists; no inbound raw-socket source. Not yet designed; no public documentation read for it."),
    f("ds-persistent-src", DS, "Persistent TCP receiver (Mango persistent, source side)", "planned", "", "The publisher half exists (Publishers & outputs); the receiving data source does not, and how the pair negotiates was never examined (research 99)."),
    f("ds-ssh", DS, "SSH command data source (Mango ssh)", "declined", "docs/research/99-open-questions.md", "Needs an SSH library (not stdlib) and runs commands on a remote host; same reasoning as the declined process handler."),
    f("ds-cloudconnect", DS, "Vendor cloud connector (Mango cloudConnect)", "declined", "", "Ties the product to a vendor cloud service; out of scope for a clean-room reference."),
    f("ds-ems", DS, "Energy-management module (Mango ems)", "declined", "", "A vendor add-on never described in the public pages read; eGauge and calculated points cover the metering basics."),

    # ---- Publishers & outputs
    f("pub-mqtt", PUB, "MQTT publisher", "implemented", "durian/outputs.py tests/test_output_health.py tests/test_output_resilience.py", "Bounded, health-reporting; stop() never blocks."),
    f("pub-http", PUB, "HTTP sender publisher", "implemented", "durian/outputs.py tests/test_http_sender.py", "Posts {point, value, ts, quality} JSON; optional Bearer token."),
    f("pub-sparkplug", PUB, "Sparkplug B publisher", "implemented", "durian/sparkplug_publisher.py durian/sparkplug_codec.py tests/test_sparkplug_pub.py tests/test_sparkplug_codec.py", "Never met a real broker (debt U9)."),
    f("pub-tcp", PUB, "Persistent TCP publisher (Mango persistent, publisher side)", "implemented", "durian/outputs.py tests/test_tcp_publisher.py", "Reconnect with back-off."),
    f("pub-grpc", PUB, "gRPC publisher", "declined", "docs/research/11-mango-feature-analysis.md", "Not built: needs HTTP/2 and protobuf, not stdlib; MQTT and the webhook handler cover the integration."),
    f("pub-opcua-server", PUB, "OPC UA server", "planned", "docs/research/10-scada-baseline.md", "Listed as a gap in the SCADA baseline; the client exists, the server does not."),
    f("pub-nodered", PUB, "Node-RED integration flows and end-to-end check", "implemented", "integrations/node-red/README.md integrations/node-red/durian-flows.json tools/nodered_check.py tools/make_nodered_flows.py", "Examples, not a node package."),
    f("pub-health", PUB, "Delivery health and operator-readable failures for outputs", "implemented", "durian/outputs.py durian/app.py tests/test_output_health.py tests/test_app.py", "Failures reach the operator as a sentence."),

    # ---- Alarms & detectors
    f("alm-high-low", ALM, "High and low limit detectors", "implemented", "durian/core.py tests/test_core.py", "On-delay, off-delay, deadband, seven levels."),
    f("alm-rate", ALM, "Rate-of-change detector", "implemented", "durian/core.py tests/test_core.py tests/test_rate_same_timestamp.py", ""),
    f("alm-noupdate", ALM, "No-update detector", "implemented", "durian/core.py tests/test_core.py", ""),
    f("alm-state", ALM, "State, range, change and no-change detectors", "implemented", "durian/core.py tests/test_core.py", ""),
    f("alm-smooth-cusum", ALM, "Smoothness and CUSUM detectors", "implemented", "durian/core.py tests/test_core.py", ""),
    f("alm-text", ALM, "Text detector for text points", "implemented", "durian/core.py tests/test_core.py tests/test_text_points.py", ""),
    f("alm-anomaly", ALM, "ANOMALY detector (EWMA robust-z)", "implemented", "durian/core.py durian/forecast.py tests/test_core.py tests/test_anomaly.py tests/test_forecast.py", "Judges a sample against the window before it."),
    f("alm-trend-limit", ALM, "TREND_LIMIT detector (raises before a limit is crossed)", "partial", "durian/core.py tests/test_trend_limit.py", "Window is memory-only and relearned after each reload; shallow trends alarm late (debt W2)."),
    f("alm-nonfinite", ALM, "Non-finite and clock-step safety in detectors", "implemented", "durian/core.py tests/test_core.py tests/test_detector_nonfinite.py tests/test_clock_step.py", ""),
    f("alm-isa182", ALM, "ISA-18.2 alarm states, acknowledge and acknowledge-all", "implemented", "durian/core.py durian/store.py tests/test_core.py tests/test_store.py", ""),
    f("alm-holds", ALM, "Shelving, suppression by design, out of service", "implemented", "durian/app.py tests/test_app.py tests/test_holds.py", "Reason and expiry on shelving."),
    f("alm-maintenance", ALM, "Maintenance windows: drop, acknowledge, suppress (Mango maintenanceEvents)", "implemented", "durian/windows.py durian/maintenance.py tests/test_windows.py tests/test_maintenance.py", "Once, weekly or manual; scope by points, tags or sources."),
    f("alm-journal-kpi", ALM, "Alarm journal and rate KPIs", "implemented", "durian/app.py tests/test_app.py tests/test_journal.py", ""),
    f("alm-levels", ALM, "Seven alarm levels (none to life safety)", "implemented", "durian/core.py tests/test_core.py", ""),
    f("alm-scheduler", ALM, "Advanced scheduler (Mango advancedScheduler)", "partial", "durian/windows.py tests/test_windows.py", "Weekly and once-off maintenance schedules exist; no general cron-style scheduler for other objects."),
    f("alm-tick-health", ALM, "System alarms for a failing tick and internal errors", "implemented", "durian/app.py tests/test_app.py tests/test_tick_health.py tests/test_internal_errors.py tests/test_error_sentences.py", "docs/12-error-handling.md is the failure model."),

    # ---- Events & handlers
    f("evt-webhook", EVT, "Webhook handler", "implemented", "durian/outputs.py tests/test_handlers.py", "No redirects followed."),
    f("evt-email", EVT, "Email handler with SMTP, recipient groups and templates", "implemented", "durian/outputs.py tests/test_handlers.py tests/test_email_groups.py", "Fake SMTP only (debt U3)."),
    f("evt-slack", EVT, "Slack handler (Mango slackPublisher)", "implemented", "durian/outputs.py tests/test_handlers.py", "Incoming-webhook URL; local receiver only (debt U3)."),
    f("evt-setpoint", EVT, "Set-point handler", "implemented", "durian/outputs.py durian/app.py tests/test_app.py tests/test_handlers.py tests/test_safe_control.py", "Same limits and step size as an operator write; confirm step skipped."),
    f("evt-report", EVT, "Scheduled report handler", "implemented", "durian/outputs.py tests/test_reports.py tests/test_report_formats.py", ""),
    f("evt-escalation", EVT, "Escalation of an unacknowledged alarm", "implemented", "durian/outputs.py durian/app.py tests/test_app.py tests/test_handlers.py tests/test_shared_state.py", "Guarded so two processes do not double-escalate."),
    f("evt-process", EVT, "Process event handler", "declined", "TECHNICAL_DEBT.md", "Runs arbitrary commands on the server; the webhook handler reaches anything external."),
    f("evt-script", EVT, "Script event handler", "declined", "docs/research/11-mango-feature-analysis.md", "Runs arbitrary code on the server."),

    # ---- Points & data model
    f("pt-numeric", PT, "Numeric points with unit, range, decimals, description", "implemented", "durian/config.py durian/core.py tests/test_app.py tests/test_core.py", ""),
    f("pt-states", PT, "Binary and multistate labels", "implemented", "durian/config.py durian/core.py tests/test_app.py tests/test_core.py", "Values stay numbers underneath."),
    f("pt-text", PT, "Alphanumeric (text) points", "implemented", "durian/core.py tests/test_core.py tests/test_text_points.py", ""),
    f("pt-image", PT, "Image points", "declined", "docs/research/11-mango-feature-analysis.md", "History, detectors and every chart assume a number or short text; images would touch all of them."),
    f("pt-logging", PT, "Logging modes: on change, interval, tolerance", "implemented", "durian/core.py tests/test_core.py tests/test_series.py", "Interval keeps last, average, minimum or maximum."),
    f("pt-quality", PT, "Quality and source timestamps", "implemented", "durian/core.py durian/ingest.py tests/test_core.py tests/test_quality.py tests/test_ingest.py", "GOOD / UNCERTAIN / BAD; future-clock, stale and out-of-order rules."),
    f("pt-tags-folders", PT, "Tags, folders and inheritance", "implemented", "durian/config.py durian/units.py tests/test_app.py tests/test_units.py", "Tags inherited from folder and source."),
    f("pt-units", PT, "Engineering units and display-unit conversion", "implemented", "durian/units.py tests/test_units.py", ""),
    f("pt-watchlists", PT, "Watch lists: static, query-based, personal (Mango watchlists)", "implemented", "durian/app.py tests/test_app.py tests/test_watchlists_query.py tests/test_my_lists.py", "Hierarchy is covered by folders."),
    f("pt-csv", PT, "Bulk CSV import and export of points", "implemented", "durian/pointcsv.py tests/test_pointcsv.py", ""),
    f("pt-safe-control", PT, "Safe control: limits, max step, rate limit, confirm, reason", "implemented", "durian/app.py tests/test_app.py tests/test_safe_control.py tests/test_set_value_required.py", "Only Modbus writes to a device; the other protocols are read-only."),
    f("pt-notes", PT, "Operator notes (logbook)", "implemented", "durian/notes.py tests/test_notes.py", ""),
    f("pt-config-keys", PT, "Unknown-setting guard and import validation", "partial", "durian/config.py tests/test_config_keys.py tests/test_import_keys.py", "Dashboards accept unknown keys; the guard is per kind, not per source type (debt W7)."),

    # ---- Dashboards & UI
    f("ui-console", UI, "Browser console (Mango mangoUI)", "implemented", "web/index.html web/app.js web/app.css web/overview.js web/point.js web/alarms.js web/history.js", "One script per page; strict CSP, no inline script."),
    f("ui-config", UI, "Labelled config forms with a JSON view", "implemented", "web/config.js web/forms.js", ""),
    f("ui-admin", UI, "Users, audit, database and account pages", "implemented", "web/users.js web/admin.js web/account.js tests/test_about.py", ""),
    f("ui-dashboards", UI, "Dashboards with 10 widget types, kiosk and public views", "implemented", "durian/dashboards.py web/dashboards.js tests/test_dashboards.py", "No author-written HTML or script."),
    f("ui-designer", UI, "Drag-and-drop dashboard designer (Mango dashboardDesigner)", "implemented", "web/dashboards.js tools/browser_designer.mjs tools/browser_dashboard.mjs", "Checked in headless Chromium, no Python test."),
    f("ui-mimic", UI, "Mimic (process graphic) editor", "implemented", "durian/dashboards.py web/dashboards.js tools/browser_mimic.mjs tests/test_dashboards.py", ""),
    f("ui-prediction", UI, "Prediction widget", "implemented", "durian/dashboards.py tests/test_dashboards.py tests/test_prediction_widget.py", ""),
    f("ui-browser-ci", UI, "Twelve headless browser checks in CI", "implemented", "tools/browser_ci.mjs .github/workflows/ci-browser.yml", ""),
    f("ui-api", UI, "REST API with per-property validation errors (Mango mangoApi)", "implemented", "durian/app.py tests/test_app.py tests/test_envelope.py tests/test_route_map.py tests/test_route_names.py tests/test_permissions.py", "Machine tokens get the data plane only."),
    f("ui-hierarchy", UI, "Point hierarchy as a tree", "partial", "durian/config.py tests/test_app.py", "A folder path with grouping and a filter, not a separate hierarchy object."),

    # ---- Reports & export
    f("rep-csv", REP, "History CSV export", "implemented", "durian/csvfmt.py durian/app.py tests/test_app.py tests/test_iso_csv.py", ""),
    f("rep-summary", REP, "Per-point summary reports with watch-list and tag narrowing", "implemented", "durian/app.py tests/test_app.py tests/test_reports.py", ""),
    f("rep-xlsx", REP, "Excel (.xlsx) report export (Mango excelReports)", "partial", "durian/docfmt.py tests/test_report_formats.py", "Plain tables only; no template-driven workbooks."),
    f("rep-pdf", REP, "PDF report export", "implemented", "durian/docfmt.py tests/test_report_formats.py", ""),
    f("rep-bundle", REP, "Export bundles (history, events, config) and scheduled retrieval", "implemented", "durian/export.py tests/test_export.py tests/test_export_retrieve.py", "One ZIP with a manifest."),
    f("rep-email-daily", REP, "Daily scheduled email report", "implemented", "durian/outputs.py tests/test_reports.py", ""),

    # ---- Security & auth
    f("sec-passwords", SEC, "scrypt passwords, sessions, role inheritance, lockout", "implemented", "durian/auth.py tests/test_auth.py tests/test_auth_state.py tests/test_permissions.py", "Idle timeout 30 minutes, 8 hours absolute."),
    f("sec-csrf-csp", SEC, "CSRF header, SameSite cookies, strict Content-Security-Policy", "implemented", "durian/server.py durian/app.py tests/test_app.py tests/test_envelope.py", ""),
    f("sec-totp", SEC, "TOTP second factor", "implemented", "durian/auth.py tests/test_auth.py tests/test_mfa.py", "Pending secrets live in memory by design."),
    f("sec-oidc", SEC, "SSO through OpenID Connect (code flow with PKCE)", "implemented", "durian/oidc.py tests/test_oidc.py", ""),
    f("sec-ldap", SEC, "LDAP simple-bind sign-in (Mango LDAP)", "partial", "durian/ldap.py durian/auth.py tests/test_auth.py tests/test_ldap_auth.py", "ldaps:// never ran and timing equalisation is untested (debt U14, W5)."),
    f("sec-tokens", SEC, "Scoped API tokens", "implemented", "durian/auth.py tests/test_auth.py tests/test_ingest.py", ""),
    f("sec-reset", SEC, "Password reset by one-time code and offline admin reset", "implemented", "durian/reset.py durian/auth.py tests/test_auth.py tests/test_reset.py", ""),
    f("sec-tls", SEC, "TLS 1.2+, HSTS, Secure cookies, refusal to listen in clear", "implemented", "durian/server.py tools/make_cert.py tests/test_tls.py", ""),
    f("sec-audit", SEC, "Audit trail with before and after values", "implemented", "durian/app.py durian/store.py tests/test_app.py tests/test_store.py", ""),
    f("sec-sql-console", SEC, "Read-only admin SQL console (Mango sqlConsole)", "partial", "durian/store.py tests/test_store.py tests/test_sql_console.py", "SELECT only. The MySQL/MariaDB branch never ran (debt W6)."),

    # ---- Analytics & ML
    f("ml-ewma", ML, "EWMA baseline, robust-z score and forecast with a 95% band", "implemented", "durian/forecast.py tests/test_forecast.py", "Linear or Theil-Sen. Verified on synthetic series only."),
    f("ml-seasonal", ML, "Seasonal hour-of-week baseline and per-slot score", "implemented", "durian/seasonal.py tests/test_seasonal.py", ""),
    f("ml-changepoint", ML, "Change-point detection", "implemented", "durian/changepoint.py tests/test_changepoint.py", ""),
    f("ml-correlate", ML, "Pearson, Spearman and best-lag correlation", "implemented", "durian/correlate.py tests/test_correlate.py", ""),
    f("ml-backtest", ML, "Forecast backtest across models", "partial", "durian/backtest.py tests/test_backtest.py", "Interval is constant width at every horizon (debt W3)."),
    f("ml-models", ML, "Stored seasonal profiles with daily retraining", "partial", "durian/models.py durian/maintenance.py tests/test_models.py tests/test_maintenance.py", "Hour-granular; the profile serves only the 14-day window (debt W4)."),
    f("ml-drift", ML, "Baseline drift between two windows", "implemented", "durian/forecast.py tests/test_forecast.py tests/test_drift.py", ""),
    f("ml-real-data", ML, "Analytics validated on real plant data", "planned", "docs/progress.html", "Every figure comes from generated series."),

    # ---- Operations & resilience
    f("ops-safe-mode", OPS, "Safe mode (login, config and backup only)", "implemented", "durian/app.py tests/test_app.py tests/test_safe_mode.py", ""),
    f("ops-spool", OPS, "Failsafe spool for history the database refuses", "partial", "durian/spool.py tests/test_spool.py", "History only; events and alarm state are not spooled; the health ok flag ignores the spool (debt V5, V6)."),
    f("ops-supervisor", OPS, "Supervisor: restart on hang or crash, safe-mode fallback", "partial", "durian/supervisor.py tests/test_supervisor.py", "SIGTERM path untested end to end (debt V4)."),
    f("ops-cli", OPS, "durian command line: run, supervise, check, backups, restore, passwd, version", "implemented", "durian/cli.py durian/__main__.py tests/test_cli.py", ""),
    f("ops-exe", OPS, "Single-file executable and tag-triggered release", "partial", "tools/build_exe.py tools/smoke_exe.py .github/workflows/release.yml", "Windows build smoke-tested; unsigned, Linux build and release workflow unverified (debt V1, V7)."),
    f("ops-service", OPS, "systemd unit and Windows startup task", "partial", "tools/service/durian.service tools/service/install-windows-service.ps1", "Never run on a machine (debt V3)."),
    f("ops-jobs", OPS, "Background jobs for backups and source tests", "implemented", "durian/jobs.py tests/test_jobs.py", ""),
    f("ops-health", OPS, "Health endpoint, heartbeat, poll-overrun alarm, engine lock", "implemented", "durian/app.py tests/test_app.py tests/test_engine_lock.py tests/test_source_down.py tests/test_tick_health.py", ""),
    f("ops-reload", OPS, "Atomic config reload with rollback", "implemented", "durian/app.py tests/test_app.py tests/test_config_rollback.py", "A reload closes open alarms (deliberate)."),
    f("ops-bounded", OPS, "Bounded in-memory state and event buffer", "implemented", "durian/app.py tests/test_app.py tests/test_bounded_state.py tests/test_event_buffer.py", ""),
    f("ops-ci", OPS, "CI: Python 3.12 to 3.14 on Linux and Windows, MariaDB", "implemented", ".github/workflows/ci-tests.yml .github/workflows/ci-mysql.yml tests/run_all.py", ""),
    f("ops-log4j", OPS, "Runtime log-level reset (Mango log4JReset)", "declined", "", "A JVM logging control; the server logs through the standard library and has no equivalent."),
    f("ops-multi-process", OPS, "Shared auth and engine state between processes", "implemented", "durian/store.py durian/app.py tests/test_app.py tests/test_shared_state.py tests/test_store.py", "Tick lease and escalation guard in the store."),
    f("ops-hw-probe", OPS, "Hardware probe against a lab device", "implemented", "tools/hw_probe.py tools/hw.example.json tests/test_hw_probe.py", "Absence of a device is a clear SKIP."),

    # ---- Storage
    f("sto-sqlite", STO, "SQLite history store", "implemented", "durian/store.py tests/test_store.py tests/test_series.py", ""),
    f("sto-mariadb", STO, "MySQL / MariaDB store", "implemented", "durian/store.py .github/workflows/ci-mysql.yml tests/test_store.py", "Full suite ran on MariaDB 11.4; some admin paths never did (debt W6)."),
    f("sto-rollups", STO, "Eight rollups and bucketed series", "implemented", "durian/store.py tests/test_store.py tests/test_series.py", "Counter resets inside a bucket are not corrected."),
    f("sto-retention", STO, "Retention with scheduled purge", "implemented", "durian/store.py durian/maintenance.py tests/test_store.py tests/test_maintenance.py", ""),
    f("sto-backups", STO, "Backups and restore", "implemented", "durian/store.py durian/cli.py tests/test_store.py tests/test_cli.py", ""),
    f("sto-migrations", STO, "Versioned migrations", "implemented", "durian/migrations.py durian/store.py tests/test_store.py", "Migration v11 never ran on MySQL/MariaDB (debt W6)."),
    f("sto-unique", STO, "Unique history rows per (point, time)", "implemented", "durian/store.py tests/test_store.py tests/test_history_unique.py", ""),
    f("sto-nosql", STO, "NoSQL time-series store (Mango mangoNoSqlDatabase)", "declined", "", "SQLite and MariaDB cover the need with no extra dependency; a custom store would add a format to maintain."),
    f("sto-compression", STO, "Historian compression beyond tolerance logging", "planned", "docs/research/10-scada-baseline.md", "Listed as a gap in the SCADA baseline."),
    f("sto-edge", STO, "Edge store-and-forward", "partial", "durian/spool.py tests/test_spool.py", "Local spool and replay exist; no edge-to-central forwarding protocol (baseline gap)."),

    # ---- Scripting & meta
    f("scr-meta-calc", SCR, "Meta data points via a safe expression language (Mango meta)", "partial", "durian/calc.py tests/test_calc.py", "Calculations over other points only; no scripts."),
    f("scr-meta-script", SCR, "Scripted meta data points", "declined", "docs/research/11-mango-feature-analysis.md", "Would run arbitrary code on the server."),
    f("scr-groovy", SCR, "Groovy scripting (Mango groovy)", "declined", "", "Runs arbitrary code; JVM-only."),
    f("scr-graaljs", SCR, "GraalJS scripting (Mango graaljs)", "declined", "", "Runs arbitrary code; JVM-only."),
    f("scr-nashorn", SCR, "Nashorn scripting (Mango nashorn, scripting)", "declined", "", "Runs arbitrary code; JVM-only."),
    f("scr-global", SCR, "Global scripts (Mango sstGlobalScripts)", "declined", "", "Shared script library for the scripting engines, which are declined."),

    # ---- Hardware-only
    f("hw-onewire", HW, "1-Wire bus (Mango onewire)", "declined", "docs/research/99-open-questions.md", "Needs a 1-Wire adapter and a native driver, not stdlib, and nothing here could verify it."),
    f("hw-serial-generic", HW, "Generic serial ASCII data source (Mango serial)", "partial", "durian/modbus.py tests/test_modbus.py", "Serial exists only as Modbus RTU/ASCII through pyserial; never run on a real COM port (debt U1)."),
    f("hw-real-devices", HW, "Verification of every driver against real hardware", "planned", "TECHNICAL_DEBT.md tools/hw_probe.py", "Debt U1, U3, U8 to U13: all drivers have met simulators and fake servers only."),
    f("hw-rust-core", HW, "Rust real-time detector core", "partial", "rust/README.md rust/src/lib.rs rust/tests/parity.rs .github/workflows/ci-rust.yml tests/test_rust_parity_vectors.py", "Experiment with parity vectors against Python; not wired into the server."),
]


# ---- checks
def problems(reg=None, root=ROOT):
    """Plain sentences, one per fault; an empty list means the registry is sound."""
    reg = REGISTRY if reg is None else reg
    out, seen = [], set()
    for e in reg:
        i = e.get("id", "?")
        if i in seen:
            out.append(f"The id {i} is used twice; every feature needs its own id.")
        seen.add(i)
        if e.get("status") not in STATUSES:
            out.append(f"{i}: the status {e.get('status')!r} is unknown; use one of {', '.join(STATUSES)}.")
        if e.get("category") not in CATEGORIES:
            out.append(f"{i}: the category {e.get('category')!r} is unknown.")
        ev = e.get("evidence", [])
        for p in ev:
            if not os.path.exists(os.path.join(root, p)):
                out.append(f"{i}: the evidence path {p} does not exist.")
        if e.get("status") in ("implemented", "partial") and not ev:
            out.append(f"{i}: it is marked {e['status']} but lists no evidence.")
        if e.get("status") in ("declined", "planned") and not e.get("note"):
            out.append(f"{i}: it is marked {e['status']} but gives no reason in its note.")
        if e.get("status") == "implemented":
            for p in ev:
                m = re.fullmatch(r"(?:durian|tools)/(\w+)\.py", p)
                if not m:
                    continue
                for stem in (m[1], m[1].removesuffix("_source")):
                    t = f"tests/test_{stem}.py"
                    if os.path.exists(os.path.join(root, t)) and t not in ev:
                        out.append(f"{i}: it is marked implemented with {p}, but {t} exists and is not listed as evidence.")
                        break
    return out


def counts(reg=None):
    reg = REGISTRY if reg is None else reg
    return {c: {s: sum(1 for e in reg if e["category"] == c and e["status"] == s) for s in STATUSES} for c in CATEGORIES}


def pct(c):
    live = c["implemented"] + c["partial"] + c["planned"]
    return f"{100 * c['implemented'] / live:.0f}%" if live else "n/a"


def total(cs):
    return {s: sum(c[s] for c in cs.values()) for s in STATUSES}


def report(reg=None):
    cs = counts(reg)
    lines = [f"{'Category':<26}{'impl':>6}{'part':>6}{'decl':>6}{'plan':>6}{'%impl':>8}"]
    for c, n in cs.items():
        lines.append(f"{c:<26}{n['implemented']:>6}{n['partial']:>6}{n['declined']:>6}{n['planned']:>6}{pct(n):>8}")
    t = total(cs)
    lines.append(f"{'Total':<26}{t['implemented']:>6}{t['partial']:>6}{t['declined']:>6}{t['planned']:>6}{pct(t):>8}")
    lines.append("%impl = implemented / (implemented + partial + planned); declined entries are left out.")
    return "\n".join(lines)


# ---- html
CSS = """<style>
  #features details { background: var(--surface); border: 1px solid var(--line); border-radius: 4px; margin-top: 8px; }
  #features summary { cursor: pointer; padding: 10px 14px; font-weight: 620; display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; }
  #features summary .meta { font-weight: 400; font-size: 12px; font-variant-numeric: tabular-nums; }
  #features .tablewrap { border: 0; border-top: 1px solid var(--line); border-radius: 0; }
  #features td.ev, #features td.why { white-space: normal; min-width: 200px; }
  #features td.ev a { display: block; word-break: break-all; font-size: 12px; }
  .pill.plan { background: var(--accent-wash); color: var(--accent); border-color: var(--accent); }
  @media (max-width: 720px) { #features th, #features td { padding: 7px 8px; } }
</style>"""
PILL = {"implemented": "done", "partial": "part", "declined": "none", "planned": "plan"}


def _e(s):
    return html.escape(s, quote=True)


def render(reg=None):
    reg = REGISTRY if reg is None else reg
    cs = counts(reg)
    t = total(cs)
    tiles = "".join(f'<div class="fig"><div class="k">{s}</div><div class="v">{t[s]}</div><div class="n">features</div></div>' for s in STATUSES)
    tiles += f'<div class="fig"><div class="k">Implemented</div><div class="v">{pct(t)}</div><div class="n">of those not declined</div></div>'
    out = [START, CSS, '  <section id="features">', "    <h2>Features by category</h2>",
           '    <img src="feature-stack.svg" alt="Feature stack: one square per feature, grouped by category, coloured by status" style="max-width:100%;height:auto">',
           f'    <p class="meta">{len(reg)} features, generated by <code>tools/features.py</code> from a checked registry; every path below exists.</p>',
           f'    <div class="figures">{tiles}</div>']
    for c in CATEGORIES:
        rows = [e for e in reg if e["category"] == c]
        n = cs[c]
        sm = f"{n['implemented']} implemented, {n['partial']} partial, {n['declined']} declined, {n['planned']} planned"
        out.append(f'    <details><summary>{_e(c)} <span class="meta">{sm}</span></summary><div class="tablewrap"><table>')
        out.append("      <thead><tr><th>Feature</th><th>Status</th><th>Evidence</th><th>Note</th></tr></thead><tbody>")
        for e in rows:
            links = "".join(f'<a href="../{_e(p)}"><code>{_e(p)}</code></a>' for p in e["evidence"]) or "none"
            out.append(f'      <tr><td class="name">{_e(e["name"])}</td><td><span class="pill {PILL[e["status"]]}">{e["status"]}</span></td>'
                       f'<td class="ev">{links}</td><td class="name">{_e(e["note"])}</td></tr>')
        out.append("      </tbody></table></div></details>")
    out += ["  </section>", END]
    return "\n".join(out)


# ---- svg
SVG = os.path.join("docs", "feature-stack.svg")
FILL = {"implemented": "#2ea043", "partial": "#d29922", "planned": "#388bfd", "declined": "#6e7681"}
ORDER = ("implemented", "partial", "planned", "declined")
CELL, GAP, LABEL, WIDTH = 22, 6, 16, 1000
TEXT = 'fill="#fff" stroke="#0d1117" stroke-width="3" paint-order="stroke" font-family="sans-serif"'


def svg(reg=None):
    """A treemap-like picture of the registry: one cell per feature, one block per category."""
    reg = REGISTRY if reg is None else reg
    cs = counts(reg)
    t = total(cs)
    live = t["implemented"] + t["partial"] + t["planned"]
    head = f"Mango feature stack: {pct(t)} ({t['implemented']}/{live})"
    blocks, x, y, rowh = [], GAP, 38, 0
    for c in CATEGORIES:
        es = sorted((e for e in reg if e["category"] == c), key=lambda e: ORDER.index(e["status"]))
        if not es:
            continue
        cols = max(7, round((len(es) * 1.6) ** 0.5))  # at least wide enough for the label
        rows = -(-len(es) // cols)
        w, h = cols * CELL, rows * CELL + LABEL
        if x + w + GAP > WIDTH and x > GAP:
            x, y, rowh = GAP, y + rowh + GAP, 0
        n = cs[c]
        g = [f'<g><title>{_e(c)}: {n["implemented"]}/{len(es)} ({pct(n)})</title>']
        for i, e in enumerate(es):
            cx, cy = x + i % cols * CELL, y + LABEL + i // cols * CELL
            g.append(f'<rect data-cell="1" x="{cx}" y="{cy}" width="{CELL}" height="{CELL}" fill="{FILL[e["status"]]}" '
                     f'stroke="#0d1117" stroke-width="0.5"><title>{_e(e["name"])} — {e["status"]}</title></rect>')
        g.append(f'<rect x="{x}" y="{y + LABEL}" width="{w}" height="{rows * CELL}" fill="none" stroke="#0d1117" stroke-width="2"/>')
        g.append(f'<text x="{x + 2}" y="{y + 12}" font-size="12" {TEXT}>{_e(c)}</text></g>')
        blocks.append("".join(g))
        x, rowh = x + w + GAP, max(rowh, h)
    y += rowh + 14
    legend = "".join(f'<rect x="{GAP + i * 170}" y="{y}" width="14" height="14" fill="{FILL[s]}" stroke="#0d1117" stroke-width="0.5"/>'
                     f'<text x="{GAP + i * 170 + 20}" y="{y + 12}" font-size="12" {TEXT}>{s} {t[s]}</text>' for i, s in enumerate(ORDER))
    foot = f'<text x="{GAP}" y="{y + 34}" font-size="11" {TEXT}>generated by tools/features.py svg from {len(reg)} features</text>'
    desc = "One square per feature, grouped by category: " + ", ".join(f"{t[s]} {s}" for s in ORDER) + "."
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" width="{WIDTH}" height="{y + 42}" viewBox="0 0 {WIDTH} {y + 42}">',
        f'<title>{_e(head)}</title><desc>{_e(desc)}</desc>',
        f'<rect width="{WIDTH}" height="{y + 42}" fill="#0d1117"/>',
        f'<text x="{GAP}" y="26" font-size="20" font-weight="bold" {TEXT}>{_e(head)}</text>',
        *blocks, legend, foot, "</svg>", ""])


def svg_sync(write, reg=None, root=ROOT):
    path = os.path.join(root, SVG)
    new = svg(reg)
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            if fh.read().replace(chr(13) + chr(10), chr(10)) == new:  # a Windows checkout may have turned LF into CRLF
                return True
    except FileNotFoundError:
        pass
    if write:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
    return False


def sync(write, reg=None, root=ROOT):
    """True when the page already holds the current block; with write, make it so."""
    path = os.path.join(root, PAGE)
    with open(path, encoding="utf-8", newline="") as fh:
        text = fh.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")
    block = render(reg)
    if START in text and END in text:
        a, b = text.index(START), text.index(END) + len(END)
        new = text[:a] + block + text[b:]
    elif ANCHOR in text:
        new = text.replace(ANCHOR, block + "\n\n" + ANCHOR, 1)
    else:
        raise SystemExit("docs/progress.html has neither the features markers nor the Security section to insert before.")
    if new == text:
        return True
    if write:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new.replace("\n", nl))
    return False


def main(argv):
    cmd, flag = (argv + ["", ""])[:2]
    if cmd == "check":
        ps = problems()
        print("\n".join(ps) if ps else f"ok: {len(REGISTRY)} features, every path exists.")
        return 1 if ps else 0
    if cmd == "report":
        print(report())
        return 0
    if cmd == "html":
        fresh = sync(write=flag != "--check")
        if flag == "--check":
            print("docs/progress.html is current." if fresh else "docs/progress.html is out of date; run python tools/features.py html.")
            return 0 if fresh else 1
        print("docs/progress.html already current." if fresh else "docs/progress.html updated.")
        return 0
    if cmd == "svg":
        fresh = svg_sync(write=flag != "--check")
        if flag == "--check":
            print("docs/feature-stack.svg is current." if fresh else "docs/feature-stack.svg is out of date; run python tools/features.py svg.")
            return 0 if fresh else 1
        print("docs/feature-stack.svg already current." if fresh else "docs/feature-stack.svg updated.")
        return 0
    if cmd == "json":
        print(json.dumps(REGISTRY, indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
