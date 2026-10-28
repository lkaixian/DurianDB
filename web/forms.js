"use strict";
// Form editors for sources, points, handlers, publishers and watch lists. One state object (`body`) is the truth: the labelled
// fields and the Advanced JSON box are two views of it, so anything the form does not cover can still be edited as JSON.
// Uses helpers from app.js ($, esc). Nothing here runs author text as code or markup.

const LEVELS = ["INFORMATION", "IMPORTANT", "WARNING", "URGENT", "CRITICAL", "LIFE_SAFETY"];
const ROLE_HINT = "Roles that may read it. user = any signed-in user, anonymous = everyone. Leave empty for administrators only.";

const pathParts = p => p.split(".");
function getPath(o, p) { return pathParts(p).reduce((a, k) => (a == null ? undefined : a[k]), o); }
function setPath(o, p, v) {
  const ks = pathParts(p); let a = o;
  for (const k of ks.slice(0, -1)) a = a[k] = (a[k] && typeof a[k] === "object") ? a[k] : {};
  a[ks[ks.length - 1]] = v;
}
function delPath(o, p) {
  const ks = pathParts(p); let a = o;
  for (const k of ks.slice(0, -1)) { a = a?.[k]; if (a == null) return; }
  delete a[ks[ks.length - 1]];
}

// A field: {path, label, type: text|number|int|check|select|textarea|roles|tags, hint, options: [[value, label]], optional, numeric, show(body)}
const F = (path, label, type, extra = {}) => ({path, label, type, ...extra});
const opts = (...a) => a.map(x => Array.isArray(x) ? x : [x, x]);

const SOURCE_GROUPS = {
  _common: body => [{title: "Access and health", fields: [
    F("enabled", "Enabled", "check", {hint: "A disabled source is kept but not started."}),
    F("read_roles", "Who can read it", "roles", {hint: ROLE_HINT}),
    F("heartbeat_seconds", "Heartbeat (seconds)", "number", {optional: true, hint: "Raise a source-down alarm if no good value arrives for this long. Empty = off."}),
    F("down_after", "Down after (seconds)", "number", {optional: true, hint: "How long a failure must last before the alarm. Empty = 10."})]}],
  virtual: () => [],
  calc: () => [{title: "Calculations", fields: [], note: "Each calculated point holds its own expression. Add them under Points."}],
  mqtt: () => [{title: "MQTT broker", fields: [
    F("creds", "Credentials file", "text", {hint: "Path to the JSON file with host, port, user, password and CA."}),
    F("prefix", "Topic prefix", "text", {hint: "Must end with /. Values arrive on prefix + point name."}),
    F("cmd_prefix", "Command prefix", "text", {hint: "Must end with / and not overlap the topic prefix."}),
    F("auto_create", "Create points for new topics", "check")]}],
  modbus: body => [{title: "Link", fields: [
    F("link.kind", "Link type", "select", {options: opts(["tcp", "TCP"], ["serial", "Serial port"])}),
    ...(getPath(body, "link.kind") === "serial" ? [
      F("link.port", "Serial port", "text", {hint: "For example COM3 or /dev/ttyUSB0."}),
      F("link.baudrate", "Baud rate", "select", {numeric: true, options: opts(1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)}),
      F("link.parity", "Parity", "select", {options: opts(["N", "None"], ["E", "Even"], ["O", "Odd"])}),
      F("link.stopbits", "Stop bits", "select", {numeric: true, options: opts(1, 2)}),
      F("link.bytesize", "Data bits", "select", {numeric: true, options: opts(7, 8)})]
      : [F("link.host", "Host", "text", {hint: "IP address or name of the device or serial converter."}), F("link.port", "Port", "int", {hint: "Usually 502."})]),
    F("framing", "Framing", "select", {optional: true, options: opts(["", "Default for the link"], ["tcp", "Modbus TCP"], ["rtu", "RTU"], ["ascii", "ASCII"]), hint: "RTU over a TCP serial converter needs RTU here."})]},
    {title: "Polling", fields: [
      F("period", "Poll every (seconds)", "number"), F("timeout", "Timeout (seconds)", "number"), F("retries", "Retries", "int"),
      F("monitor", "Add an online/offline point per device", "check")]}],
  snmp: () => [{title: "SNMP agent", fields: [
    F("host", "Host", "text", {hint: "IP address or name of the device."}),
    F("port", "Port", "int", {optional: true, hint: "Usually 161."}),
    F("community", "Community", "text", {hint: "The v2c community text the device expects, for example public."}),
    F("period", "Poll every (seconds)", "number"), F("timeout", "Timeout (seconds)", "number", {hint: "1 to 30."})]}],
  bacnet: () => [{title: "BACnet/IP device", fields: [
    F("bacnet.address", "Address", "text", {hint: "host:port of the device, for example 10.0.0.5:47808. The port defaults to 47808."}),
    F("bacnet.device", "Device instance", "int", {hint: "The instance number the device answers with, such as 1001."}),
    F("period", "Poll every (seconds)", "number"), F("timeout", "Timeout (seconds)", "number", {hint: "1 to 30."})]}],
  dnp3: () => [{title: "DNP3 outstation", fields: [
    F("dnp3.address", "Address", "text", {hint: "host:port of the outstation, for example 10.0.0.5:20000. The port is the outstation's TCP port, commonly 20000."}),
    F("dnp3.outstation", "Outstation link address", "int", {hint: "The link address the outstation answers on, 0 to 65535."}),
    F("period", "Poll every (seconds)", "number", {hint: "0.2 to 86400."}), F("timeout", "Timeout (seconds)", "number", {hint: "1 to 30."})]}],
  opcua: () => [{title: "OPC UA server", fields: [
    F("opcua.address", "Address", "text", {hint: "host:port of the server, for example 10.0.0.5:14360."}),
    F("opcua.path", "Endpoint path", "text", {optional: true, hint: "Optional path of the endpoint URL, for example plant."}),
    F("opcua.security_policy", "Security policy", "select", {optional: true, options: opts("None"), hint: "Only SecurityPolicy None is implemented; the connection is not encrypted."}),
    F("period", "Poll every (seconds)", "number"), F("timeout", "Timeout (seconds)", "number", {hint: "1 to 30."})]}],
  http_json: () => [{title: "HTTP JSON document", fields: [
    F("url", "URL", "text", {hint: "http or https. Redirects are never followed."}),
    F("period", "Fetch every (seconds)", "number"), F("timeout", "Timeout (seconds)", "number"),
    F("token_file", "Bearer token file", "text", {optional: true, hint: "Optional. Read on every fetch, so a rotated token is picked up."}),
    F("ca", "CA certificate file", "text", {optional: true, hint: "Optional. For a private certificate authority."})]}],
  sql: () => [{title: "SQLite query", fields: [
    F("path", "Database file", "text", {hint: "Opened read-only."}),
    F("query", "Query", "textarea", {hint: "One SELECT returning a key column and a number column."}),
    F("period", "Run every (seconds)", "number"), F("timeout", "Timeout (seconds)", "number")]}],
  file: () => [{title: "Text file", fields: [
    F("path", "File", "text", {hint: "A local file of name=value lines, read every period."}),
    F("format", "Format", "select", {optional: true, options: opts("lines")}),
    F("period", "Read every (seconds)", "number", {optional: true, hint: "1 to 86400."})]}],
  http_in: () => [{title: "Pushed values", fields: [
    F("auto_create", "Create points for new names", "check", {hint: "Off = only points you added accept values."}),
    F("max_age_days", "Oldest accepted timestamp (days)", "int")]}],
};

const GEN = {
  walk: [["start", "Start"], ["min", "Minimum"], ["max", "Maximum"], ["step", "Step"], ["drift", "Drift"]],
  sine: [["offset", "Offset"], ["amplitude", "Amplitude"], ["period", "Period (seconds)"]],
  constant: [["value", "Value"]],
};
const MODBUS_TYPES = ["uint16", "int16", "uint32", "int32", "uint64", "int64", "float32", "float64", "bool"];

// What a point needs from its source type, and what to start with when the source changes.
const POINT_PARTS = {
  virtual: {start: () => ({generator: {kind: "walk", start: 50, min: 0, max: 100, step: 1}}), keys: ["generator"],
    fields: b => [F("generator.kind", "Generator", "select", {options: opts(["walk", "Random walk"], ["sine", "Sine wave"], ["constant", "Constant"])}),
      ...(GEN[getPath(b, "generator.kind")] || []).map(([k, l]) => F("generator." + k, l, "number", {optional: k === "drift"}))]},
  modbus: {start: () => ({modbus: {unit: 1, range: "holding", address: 0, type: "uint16"}}), keys: ["modbus"],
    fields: b => {
      const bit = ["coil", "discrete"].includes(getPath(b, "modbus.range"));
      return [F("modbus.unit", "Device address", "int", {hint: "1 to 247."}),
        F("modbus.range", "Register area", "select", {options: opts(["coil", "Coil"], ["discrete", "Discrete input"], ["holding", "Holding register"], ["input", "Input register"])}),
        F("modbus.address", "Address", "int", {hint: "Zero-based: register 40001 is holding address 0."}),
        ...(bit ? [] : [F("modbus.type", "Data type", "select", {options: opts(...MODBUS_TYPES)})]),
        ...(!bit && getPath(b, "modbus.type") === "bool" ? [F("modbus.bit", "Bit (0 to 15)", "int")] : []),
        ...(!bit && getPath(b, "modbus.type") !== "bool" ? [
          F("modbus.byte_order", "Byte order", "select", {optional: true, options: opts(["", "big"], "big", "little")}),
          F("modbus.word_order", "Word order", "select", {optional: true, options: opts(["", "big"], "big", "little"), hint: "little = word-swapped, common for float32 on some devices."}),
          F("modbus.multiplier", "Multiplier", "number", {optional: true}), F("modbus.additive", "Add", "number", {optional: true, hint: "value = raw x multiplier + add"})] : [])];
    }},
  snmp: {start: () => ({snmp: {address: "1.3.6.1.2.1.1.5.0"}}), keys: ["snmp"],
    fields: () => [F("snmp.address", "OID", "text", {hint: "The numeric OID to read, such as 1.3.6.1.2.1.1.5.0."})]},
  bacnet: {start: () => ({bacnet: {object_type: "analogInput", instance: 1, property: "presentValue"}}), keys: ["bacnet"],
    fields: () => [F("bacnet.object_type", "Object type", "select", {options: opts(["analogInput", "analogInput"], ["analogOutput", "analogOutput"], ["analogValue", "analogValue"], ["binaryInput", "binaryInput"], ["binaryOutput", "binaryOutput"], ["binaryValue", "binaryValue"], ["multiStateValue", "multiStateValue"])}),
      F("bacnet.instance", "Object instance", "int", {hint: "The object's instance number, such as 1."}),
      F("bacnet.property", "Property", "select", {options: opts(["presentValue", "presentValue"], ["statusFlags", "statusFlags"], ["units", "units"], ["objectName", "objectName"])})]},
  dnp3: {start: () => ({dnp3: {group: 30, index: 0}}), keys: ["dnp3"],
    fields: () => [F("dnp3.group", "Group", "select", {numeric: true, options: opts(1, 10, 20, 30, 40)}),
      F("dnp3.index", "Object index", "int", {hint: "The object's index in that group, 0 to 65535."})]},
  opcua: {start: () => ({opcua: {node: "ns=2;i=85"}}), keys: ["opcua"],
    fields: () => [F("opcua.node", "Node id", "text", {hint: "The node's id in string form, such as ns=2;i=85 or ns=2;s=Temperature."})]},
  http_json: {start: () => ({http: {path: ""}}), keys: ["http", "scale"],
    fields: () => [F("http.path", "Path in the JSON", "text", {hint: "For example sensors.temp or items[0].value."}),
      F("scale.multiplier", "Multiplier", "number", {optional: true}), F("scale.additive", "Add", "number", {optional: true})]},
  sql: {start: () => ({sql: {key: ""}}), keys: ["sql", "scale"],
    fields: () => [F("sql.key", "Row key", "text", {hint: "The value in the first column that identifies this point's row."}),
      F("scale.multiplier", "Multiplier", "number", {optional: true}), F("scale.additive", "Add", "number", {optional: true})]},
  calc: {start: () => ({calc: {expression: 'p("boiler-temp") * 1.8 + 32'}}), keys: ["calc"],
    fields: () => [F("calc.expression", "Expression", "textarea", {hint: 'Read a point with p("name"). Operators + - * / %, comparisons, and, or, not, a if cond else b, abs min max round sqrt.'})]},
  mqtt: {start: () => ({}), keys: [], fields: () => []},
  http_in: {start: () => ({}), keys: [], fields: () => []},
};

function pointGroups(body, sources) {
  const type = (sources.find(s => s.name === body.source) || {}).type, part = POINT_PARTS[type];
  const settable = !!body.settable;
  // The server rejects settable on these source types, so do not offer it (durian/config.py).
  const readOnly = ["http_json", "sql", "http_in", "calc", "snmp", "dnp3", "bacnet"].includes(type);
  return [
    {title: "Identity", fields: [
      F("source", "Data source", "select", {options: sources.length ? opts(...sources.map(s => [s.name, `${s.name} (${s.type})`])) : [["", "Create a source first"]]}),
      F("datatype", "Value is", "select", {optional: true, options: opts(["number", "A number"], ["text", "Text (on virtual, HTTP in or MQTT sources)"])}),
      F("folder", "Folder", "text", {optional: true, hint: "Groups points on the overview, for example Plant/Boiler."}),
      F("description", "Description", "text", {optional: true}), F("unit", "Unit", "text", {optional: true, hint: "For example C, bar or %."}),
      F("tags", "Tags", "tags", {hint: "key:value pairs, comma separated, for example area:boiler, line:2."})]},
    ...(part && part.fields(body).length ? [{title: `Value from the ${type} source`, fields: part.fields(body)}] : []),
    {title: "Range and display", fields: [
      F("states", "State labels", "states", {optional: true, hint: "For on/off and multi-state points: 0=Stopped, 1=Running, 2=Fault. The label is shown instead of the number."}),
      F("eu_low", "Engineering range, low", "number", {optional: true}), F("eu_high", "Engineering range, high", "number", {optional: true}),
      F("decimals", "Decimals shown", "int", {optional: true, hint: "0 to 6."})]},
    {title: "History", fields: [
      F("logging", "Log", "select", {options: opts(["ON_CHANGE", "On change"], ["ALL", "Every value"], ["INTERVAL", "At fixed intervals"], ["NEVER", "Never"])}),
      ...(body.logging === "INTERVAL" ? [F("log_interval", "Sample every (seconds)", "number"),
        F("log_mode", "Sample is", "select", {optional: true, options: opts(["instant", "The last value"], ["avg", "The average of the period"], ["min", "The minimum of the period"], ["max", "The maximum of the period"])})] : []),
      F("tolerance", "Ignore changes of", "number", {optional: true, hint: "With 'on change': moves this small are not logged."}),
      F("retention_days", "Keep history for (days)", "int", {optional: true, hint: "Empty = the database default. 0 = forever."})]},
    {title: "Access and control", fields: [
      F("read_roles", "Who can read it", "roles", {hint: ROLE_HINT}),
      ...(readOnly ? [] : [F("settable", "Operators can set this point", "check")]),
      ...(!readOnly && settable ? [F("set_roles", "Who can set it", "roles", {hint: "Empty = administrators only."}),
        F("set_min", "Lowest value accepted", "number", {optional: true, hint: "Empty = the engineering range."}), F("set_max", "Highest value accepted", "number", {optional: true}),
        F("max_step", "Largest change per write", "number", {optional: true}), F("confirm", "Ask for confirmation before each write", "check"),
        F("require_reason", "Ask for a reason with each write", "check", {hint: "The reason is stored in the audit log with the old and new value."})] : [])]},
    {title: "Suppression", fields: [
      F("suppress_when.point", "Hold this point's alarms while point", "text", {optional: true, hint: "Empty = never suppressed."}),
      F("suppress_when.op", "is", "select", {optional: true, options: opts(["", "choose"], ">", "<", "==")}), F("suppress_when.value", "than", "number", {optional: true})]},
    {title: "Alarms", detectors: true},
  ];
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MAINT_GROUPS = body => {
  const kind = getPath(body, "schedule.type");
  return [
    {title: "What happens", fields: [
      F("enabled", "Enabled", "check", {hint: "A disabled window never applies."}),
      F("mode", "During the window", "select", {options: opts(["suppress", "Hold alarms: record them, announce nothing"], ["acknowledge", "Acknowledge alarms at once and announce nothing"], ["drop", "Drop alarms: do not record them"])}),
      F("reason", "Reason", "text", {optional: true, hint: "Shown to operators, for example Pump 3 replacement."})]},
    {title: "Which alarms", fields: [
      F("scope.points", "Points", "list", {optional: true, hint: "Point names, comma separated."}),
      F("scope.tags", "Tags", "tags", {optional: true, hint: "Every point with all of these tags, for example area:boiler."}),
      F("scope.sources", "Sources", "list", {optional: true, hint: "Source names. Also covers the source-down alarm."})]},
    {title: "When", fields: [
      F("schedule.type", "Schedule", "select", {options: opts(["weekly", "Every week"], ["once", "Once"], ["manual", "Only when switched on"])}),
      ...(kind === "weekly" ? [F("schedule.days", "Days", "days", {hint: "For example Mon, Tue, Fri."}), F("schedule.start", "From (HH:MM)", "text", {hint: "Server local time."}),
        F("schedule.end", "Until (HH:MM)", "text", {hint: "An end before the start crosses midnight."})] : []),
      ...(kind === "once" ? [F("schedule.from", "From", "datetime"), F("schedule.to", "Until", "datetime")] : []),
      ...(kind === "manual" ? [F("active", "In effect now", "check", {hint: "Tick and save to start the window, untick and save to end it."})] : [])]},
  ];
};

const GROUPS = {
  maintenance: MAINT_GROUPS,
  source: (body) => [...SOURCE_GROUPS._common(body), ...(SOURCE_GROUPS[body.type] || (() => []))(body)],
  handler: body => [
    {title: {webhook: "Webhook", email: "Email", setpoint: "Set a point", report: "Scheduled report", slack: "Slack"}[body.type] || "Handler", fields: [
      F("enabled", "Enabled", "check"),
      F("min_level", "Act from level", "select", {options: opts(...LEVELS)}),
      ...(body.type === "email" || body.type === "report" ? [
        F("to", "To", "list", {hint: "Email addresses, comma separated."}), F("from", "From", "text"),
        F("smtp.host", "SMTP server", "text"), F("smtp.port", "SMTP port", "int", {hint: "25, 587 or 465."}),
        F("smtp.security", "Security", "select", {options: opts(["none", "None"], ["starttls", "STARTTLS"], ["ssl", "SSL/TLS"])}),
        F("creds", "Credentials file", "text", {optional: true, hint: "Optional JSON file with user and password, so the password is not stored in the settings."}),
        ...(body.type === "email" ? [F("groups", "Also send to groups", "groups", {optional: true, hint: "Email group names, comma separated. Their addresses are added to To, once each."}),
          F("subject", "Subject template", "text", {optional: true, hint: "Placeholders: {level} {point} {value} {source} {time} {message}. Empty = the standard subject."}),
          F("body", "Body template", "textarea", {optional: true, hint: "Placeholders: {level} {point} {value} {source} {time} {message}. An unknown {name} stays as typed. Empty = the standard body."}),
          F("escalate_after", "Escalate if unacknowledged for (minutes)", "int", {optional: true, hint: "Leave empty for no escalation."}),
          F("escalate_to", "Escalate to", "list", {optional: true, hint: "Email addresses to tell when nobody has acknowledged the alarm in time."})] : []),
        ...(body.type === "report" ? [F("hour", "Send daily from hour", "int", {hint: "0 to 23, server local time."}),
          F("hours", "Window (hours)", "int", {optional: true, hint: "Empty = 24. The summary covers this many hours before sending."}),
          F("watchlist", "Watch list", "text", {optional: true, hint: "A shared watch list to limit the points. Empty = every point."})]
          : [F("on_clear", "Also send when the alarm returns to normal", "check")])]
        : body.type === "setpoint" ? [
        F("point", "Point to set", "text", {hint: "It must be settable. The same limits and step size as an operator apply; the confirm step does not."}),
        F("value", "Value when the alarm is raised", "number"),
        F("clear_value", "Value when it returns to normal", "number", {optional: true}),
        F("release_on_clear", "Release the point when it returns to normal", "check", {hint: "Used when there is no value above."})]
        : body.type === "slack" ? [F("url", "Incoming webhook URL", "text", {hint: "From Slack's Incoming Webhooks app. Each alarm is posted as a message."}), F("on_clear", "Also post when the alarm returns to normal", "check")]
        : [F("url", "URL", "text", {hint: "Alarms are posted here as JSON."})])]},
    {title: "Which alarms", fields: [
      F("scope.points", "Points", "list", {optional: true, hint: "Leave all three empty for every alarm."}),
      F("scope.tags", "Tags", "tags", {optional: true, hint: "Points with all of these tags, for example area:boiler."}),
      F("scope.sources", "Sources", "list", {optional: true})]}],
  publisher: body => [{title: {http: "HTTP sender", sparkplug: "Sparkplug B publisher"}[body.type] || "MQTT publisher", fields: [
    F("enabled", "Enabled", "check"),
    ...(body.type === "http" ? [F("url", "URL", "text", {hint: "Each value is posted here as {point, value, ts, quality}, the body another DurianDB's /api/ingest takes."}),
      F("creds", "Token file", "text", {optional: true, hint: "A JSON file with a token, sent as a Bearer token."}),
      F("resend", "Resend an unchanged value every (seconds)", "number", {optional: true, hint: "Empty = 60. A changed value is always sent at once."})]
      : body.type === "sparkplug" ? [F("creds", "Credentials file", "text", {hint: "Path to the JSON file with host, port, user, password and CA."}),
        F("group", "Group ID", "text", {hint: "One topic level: 1 to 64 letters, digits or underscores, without '/', '+' or '#'."}),
        F("node", "Node ID", "text", {hint: "One topic level: 1 to 64 letters, digits or underscores, without '/', '+' or '#'."}),
        F("device", "Device ID", "text", {optional: true, hint: "Empty publishes node messages only. One topic level, 1 to 64 letters, digits or underscores, without '/', '+' or '#'."})]
      : [F("creds", "Credentials file", "text"), F("prefix", "Topic prefix", "text", {hint: "Must end with / and not overlap a source's prefix."})]),
    F("points", "Points to publish", "list", {hint: "Point names, comma separated, or * for all."})]}],
  watchlist: () => [{title: "Watch list", fields: [
    F("tags", "Tags to match", "tags", {hint: "key:value pairs. A point matching all of them is included."}),
    F("name_like", "Name pattern", "text", {optional: true, hint: "For example boiler-*. A query list: points matching every filled-in part are included."}),
    F("text_like", "Description contains", "text", {optional: true}),
    F("points", "Points to include", "list", {hint: "Point names, comma separated."}),
    F("read_roles", "Who can read it", "roles", {hint: ROLE_HINT})]},
    {title: "Query, resolved each time the list is read", fields: [
    F("query.folder", "Folder and its subfolders", "text", {optional: true, hint: "For example plant/boiler. A point created there later is included; a point moved out drops."}),
    F("query.tags", "Query tags", "tags", {optional: true, hint: "key:value pairs. A point must have every one of them."}),
    F("query.source", "From source", "text", {optional: true, hint: "Source name. Empty = any source."}),
    F("query.name_like", "Name contains", "text", {optional: true, hint: "Part of the point name. Empty = any name."})]}],
  emailgroup: () => [{title: "Email group", fields: [
    F("addresses", "Addresses", "list", {hint: "Email addresses, comma separated. Email handlers that name this group send to all of them, once each."})]}],
};

// ---- rendering
const idOf = p => "f-" + p.replace(/[^A-Za-z0-9]+/g, "-");
function control(f, body) {
  const v = getPath(body, f.path), id = idOf(f.path), a = `id="${id}" data-path="${esc(f.path)}" data-type="${f.type}" ${f.optional ? 'data-optional="1"' : ""} ${f.numeric ? 'data-numeric="1"' : ""} aria-describedby="${id}-h ${id}-e"`;
  switch (f.type) {
    case "check": return `<input type="checkbox" ${a} ${v ? "checked" : ""}>`;
    case "select": return `<select ${a}>${f.options.map(([val, l]) => `<option value="${esc(String(val))}" ${String(v ?? "") === String(val) ? "selected" : ""}>${esc(String(l))}</option>`).join("")}</select>`;
    case "textarea": return `<textarea ${a} spellcheck="false" rows="3">${esc(v ?? "")}</textarea>`;
    case "number": case "int": return `<input type="number" step="${f.type === "int" ? 1 : "any"}" ${a} value="${esc(v ?? "")}">`;
    case "roles": case "list": return `<input type="text" ${a} value="${esc((v || []).join(", "))}" autocomplete="off">`;
    case "groups": return `<input type="text" ${a} list="${id}-dl" value="${esc((v || []).join(", "))}" autocomplete="off"><datalist id="${id}-dl" data-groups="1"></datalist>`;
    case "tags": return `<input type="text" ${a} value="${esc(Object.entries(v || {}).map(([k, x]) => k + ":" + x).join(", "))}" autocomplete="off">`;
    case "days": return `<input type="text" ${a} value="${esc((v || []).map(d => DAYS[d]).join(", "))}" autocomplete="off">`;
    case "datetime": return `<input type="datetime-local" ${a} value="${v ? new Date(v * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ""}">`;
    case "states": return `<input type="text" ${a} value="${esc(Object.entries(v || {}).map(([k, x]) => k + "=" + x).join(", "))}" autocomplete="off">`;
    default: return `<input type="text" ${a} value="${esc(v ?? "")}" autocomplete="off">`;
  }
}
function fieldHtml(f, body) {
  const id = idOf(f.path), tail = `<small class="hint" id="${id}-h">${esc(f.hint || "")}</small><small class="fe" id="${id}-e" data-err="${esc(f.path)}"></small>`;
  return f.type === "check" ? `<div class="fld chk"><label class="inl">${control(f, body)} ${esc(f.label)}</label>${tail}</div>`
    : `<div class="fld"><label for="${id}">${esc(f.label)}</label>${control(f, body)}${tail}</div>`;
}
// What each alarm condition needs. "list" fields are comma separated numbers.
const DET = {
  HIGH_LIMIT: {label: "Above a limit", args: [["limit", "Limit"]], late: true, band: true, start: {limit: 0}},
  LOW_LIMIT: {label: "Below a limit", args: [["limit", "Limit"]], late: true, band: true, start: {limit: 0}},
  RATE_OF_CHANGE: {label: "Changing too fast", args: [["per_second", "Units per second"]], late: true, start: {per_second: 1}},
  STATE: {label: "In a state", args: [["states", "States (numbers)", "list"]], late: true, start: {states: [1]}},
  RANGE: {label: "Outside or inside a range", args: [["low", "Low"], ["high", "High"], ["mode", "Alarm when", "select", opts(["outside", "outside the range"], ["inside", "inside the range"])]], late: true, start: {low: 0, high: 100, mode: "outside"}},
  CHANGE: {label: "Every change", args: [["tolerance", "Ignore changes of"]], start: {tolerance: 0}},
  NO_CHANGE: {label: "Stuck value", args: [["seconds", "Seconds unchanged"], ["tolerance", "Ignore changes of"]], start: {seconds: 600, tolerance: 0}},
  SMOOTHNESS: {label: "Noisy signal", args: [["limit", "Scatter limit (std. deviation)"], ["samples", "Over this many samples"]], late: true, start: {limit: 1, samples: 10}},
  CUSUM: {label: "Slow drift (CUSUM)", args: [["target", "Target value"], ["slack", "Allowance per sample"], ["threshold", "Alarm when the sum passes"]], late: true, start: {target: 0, slack: 0, threshold: 10}},
  ANOMALY: {label: "Outlier (statistical)", args: [["z_limit", "Standard deviations from normal"], ["window", "Compared with this many samples"]], late: true, start: {z_limit: 3, window: 20}},
  TEXT: {label: "Text matches", args: [["values", "Texts", "tlist"], ["mode", "Alarm when the value", "select", opts(["equals", "equals one of them"], ["contains", "contains one of them"])]], late: true, start: {values: ["FAULT"], mode: "equals"}},
  NO_UPDATE: {label: "No update", args: [["seconds", "Seconds without a value"]], start: {seconds: 60}},
};
function detectorHtml(d, i) {
  const kind = d.kind || "HIGH_LIMIT", spec = DET[kind];
  const n = (k, label, hint) => `<div class="fld"><label for="d${i}-${k}">${label}</label><input type="number" step="any" id="d${i}-${k}" data-det="${i}" data-dpath="${k}" value="${esc(d[k] ?? "")}"><small class="hint">${hint || ""}</small></div>`;
  const arg = ([k, label, type, options]) => type === "list" || type === "tlist"
    ? `<div class="fld"><label for="d${i}-${k}">${label}</label><input type="text" id="d${i}-${k}" data-det="${i}" data-dpath="${k}" data-list="${type === "tlist" ? "text" : "1"}" value="${esc((d[k] || []).join(", "))}" autocomplete="off"><small class="hint">Comma separated, for example 2, 3.</small></div>`
    : type === "select" ? `<div class="fld"><label for="d${i}-${k}">${label}</label><select id="d${i}-${k}" data-det="${i}" data-dpath="${k}">${options.map(([v, l]) => `<option value="${v}" ${v === (d[k] || options[0][0]) ? "selected" : ""}>${l}</option>`).join("")}</select></div>`
    : n(k, label);
  return `<fieldset class="det"><legend>Alarm ${i + 1}</legend><div class="fgrid">
    <div class="fld"><label for="d${i}-kind">Condition</label><select id="d${i}-kind" data-det="${i}" data-dpath="kind" data-rerender="1">${Object.entries(DET).map(([v, s]) => `<option value="${v}" ${v === kind ? "selected" : ""}>${s.label}</option>`).join("")}</select></div>
    ${spec.args.map(arg).join("")}
    <div class="fld"><label for="d${i}-level">Level</label><select id="d${i}-level" data-det="${i}" data-dpath="level">${LEVELS.map(l => `<option ${l === (d.level || (kind === "CHANGE" ? "INFORMATION" : "WARNING")) ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    ${spec.late ? n("duration", "Must hold for (seconds)", "On-delay: the condition must last this long.") : ""}
    ${spec.band ? n("deadband", "Dead band", "Once raised, the value must pass the limit by this much to clear.") : ""}
    ${spec.late ? n("off_delay", "Stay raised for (seconds)", "Off-delay: normal this long before it clears.") : ""}
    </div><small class="fe" data-err="detectors[${i}]"></small><button type="button" data-del-det="${i}">Remove alarm ${i + 1}</button></fieldset>`;
}
function groupsHtml(groups, body) {
  return groups.map(g => g.detectors
    ? `<fieldset><legend>${g.title}</legend>${(body.detectors || []).map(detectorHtml).join("") || '<p class="note">No alarms on this point.</p>'}<button type="button" data-add-det="1">Add an alarm</button><small class="fe" data-err="detectors"></small></fieldset>`
    : `<fieldset><legend>${g.title}</legend>${g.note ? `<p class="note">${g.note}</p>` : ""}<div class="fgrid">${g.fields.map(f => fieldHtml(f, body)).join("")}</div></fieldset>`).join("");
}

// ---- value in/out of a control
function readControl(el) {
  const t = el.dataset.type, raw = el.type === "checkbox" ? el.checked : el.value.trim();
  if (t === "check") return raw;
  if (t === "number" || t === "int") return raw === "" ? undefined : Number(raw);
  if (t === "select") return raw === "" ? undefined : el.dataset.numeric ? Number(raw) : raw;
  if (t === "roles" || t === "list" || t === "groups") return raw.split(",").map(s => s.trim()).filter(Boolean);
  if (t === "days") { const d = raw.split(/[ ,]+/).map(x => DAYS.findIndex(n => n.toLowerCase() === x.slice(0, 3).toLowerCase())).filter(i => i >= 0); return [...new Set(d)].sort(); }
  if (t === "datetime") return raw === "" ? undefined : new Date(raw).getTime() / 1000;
  if (t === "states") { const o = Object.fromEntries(raw.split(",").map(s => s.trim()).filter(s => s.includes("=")).map(s => { const [k, ...v] = s.split("="); return [k.trim(), v.join("=").trim()]; })); return Object.keys(o).length ? o : undefined; }
  if (t === "tags") return Object.fromEntries(raw.split(",").map(s => s.trim()).filter(s => s.includes(":")).map(s => { const [k, ...v] = s.split(":"); return [k.trim(), v.join(":").trim()]; }));
  return raw === "" ? undefined : raw;
}

// What must follow when one field changes the shape of the rest.
function afterChange(kind, path, body, sources) {
  if (kind === "source" && path === "link.kind") body.link = body.link.kind === "serial" ? {kind: "serial", port: "COM3", baudrate: 9600, parity: "N", stopbits: 1, bytesize: 8} : {kind: "tcp", host: "127.0.0.1", port: 502};
  if (kind === "point" && path === "source") {
    const type = (sources.find(s => s.name === body.source) || {}).type;
    for (const p of Object.values(POINT_PARTS)) for (const k of p.keys) delete body[k];
    Object.assign(body, (POINT_PARTS[type] || {start: () => ({})}).start());
  }
  if (kind === "maintenance" && path === "schedule.type") {
    const t = body.schedule.type, day = new Date(), p = n => String(n).padStart(2, "0");
    body.schedule = t === "weekly" ? {type: t, days: [0, 1, 2, 3, 4], start: "22:00", end: "06:00"} : t === "once" ? {type: t, from: Math.floor(Date.now() / 1000), to: Math.floor(Date.now() / 1000) + 3600} : {type: t};
    if (t !== "manual") delete body.active;
  }
  if (kind === "point" && path === "generator.kind") {
    const keep = body.generator.kind, dflt = {walk: {start: 50, min: 0, max: 100, step: 1}, sine: {offset: 0, amplitude: 1, period: 60}, constant: {value: 0}}[keep];
    body.generator = {kind: keep, ...dflt};
  }
}
function tidy(kind, body) {
  if (kind === "handler" && body.scope) {
    for (const k of ["points", "tags", "sources"]) if (body.scope[k] === undefined || (Array.isArray(body.scope[k]) ? !body.scope[k].length : !Object.keys(body.scope[k]).length)) delete body.scope[k];
    if (!Object.keys(body.scope).length) delete body.scope;
  }
  if (kind === "handler" && !body.groups?.length) delete body.groups;
  if (kind === "maintenance") {
    for (const k of ["points", "tags", "sources"]) if (body.scope && (body.scope[k] === undefined || (Array.isArray(body.scope[k]) ? !body.scope[k].length : !Object.keys(body.scope[k]).length))) delete body.scope[k];
  }  // drop what an empty optional control leaves behind
  if (kind === "watchlist" && body.query) {  // a blank query field is not part of the rule, like an empty optional field
    for (const k of Object.keys(body.query)) if (body.query[k] == null || (typeof body.query[k] === "object" && !Object.keys(body.query[k]).length)) delete body.query[k];
    if (!Object.keys(body.query).length) delete body.query;
  }
  if (kind === "point") {
    if (!body.suppress_when?.point) delete body.suppress_when;
    else if (!body.suppress_when.op || body.suppress_when.value === undefined) { body.suppress_when.op ||= ">"; body.suppress_when.value ??= 0; }
    if (!body.settable) for (const k of ["set_roles", "set_min", "set_max", "max_step", "confirm", "require_reason"]) delete body[k];
    if (body.tags && !Object.keys(body.tags).length) body.tags = {};
  }
  return body;
}

// ---- the editor
function mountEditor(host, {kind, body, sources = []}) {
  host.innerHTML = `<div id="fe-fields"></div>
    <details id="adv-box"><summary>Advanced: all settings as JSON</summary><p class="note">Anything the form does not show can be edited here. The form and this box stay in step.</p>
    <label for="adv">Settings (JSON)</label><textarea id="adv" spellcheck="false"></textarea><small class="fe" id="adv-e" role="alert"></small></details>`;
  const fields = host.querySelector("#fe-fields"), adv = host.querySelector("#adv");
  const groups = () => (kind === "point" ? pointGroups(body, sources) : GROUPS[kind](body));
  let groupNames = [];  // suggestions only: an existing group can still be typed before the list arrives
  const fillGroups = () => { const dl = fields.querySelector("datalist[data-groups]"); if (dl) dl.innerHTML = groupNames.map(n => `<option value="${esc(n)}">`).join(""); };
  if (kind === "handler") api("GET", "/api/config/emailgroup").then(gs => { groupNames = gs.map(g => g.name); fillGroups(); }).catch(() => {});
  const sync = () => { if (document.activeElement !== adv) adv.value = JSON.stringify(tidy(kind, structuredClone(body)), null, 2); };
  const draw = focusId => {
    fields.innerHTML = groupsHtml(groups(), body);
    fillGroups();
    sync();
    if (focusId) document.getElementById(focusId)?.focus();
  };
  draw();
  fields.addEventListener("input", ev => {
    const el = ev.target;
    if (el.dataset.det != null) return;   // detector rows are handled on change, below
    if (!el.dataset.path || el.tagName === "SELECT" || el.type === "checkbox") return;
    store(el);
  });
  function store(el) {
    const v = readControl(el);
    if (v === undefined) delPath(body, el.dataset.path); else setPath(body, el.dataset.path, v);
    sync();
  }
  fields.addEventListener("change", ev => {
    const el = ev.target;
    if (el.dataset.det != null) {
      const d = body.detectors[Number(el.dataset.det)], k = el.dataset.dpath;
      if (el.dataset.list) { const l = el.value.split(",").map(x => x.trim()).filter(Boolean); if (el.dataset.list !== "text") for (let j = l.length; j--;) { l[j] = Number(l[j]); if (Number.isNaN(l[j])) l.splice(j, 1); } if (l.length) d[k] = l; else delete d[k]; }
      else if (el.tagName === "SELECT") d[k] = el.value; else if (el.value === "") delete d[k]; else d[k] = Number(el.value);
      if (k === "kind") { for (const x of Object.values(DET).flatMap(sp => sp.args.map(a => a[0])).concat(["deadband"])) delete d[x]; Object.assign(d, DET[d.kind].start); }
      if (el.dataset.rerender) draw(el.id); else sync();
      return;
    }
    if (!el.dataset.path) return;
    store(el);
    if (el.tagName === "SELECT" || el.type === "checkbox") { afterChange(kind, el.dataset.path, body, sources); draw(el.id); }
  });
  fields.addEventListener("click", ev => {
    const t = ev.target;
    if (t.dataset.addDet) { (body.detectors ||= []).push({kind: "HIGH_LIMIT", limit: 0, level: "WARNING", duration: 0}); draw(); fields.querySelector(`#d${body.detectors.length - 1}-kind`)?.focus(); }
    if (t.dataset.delDet != null) { body.detectors.splice(Number(t.dataset.delDet), 1); if (!body.detectors.length) delete body.detectors; draw(); fields.querySelector("[data-add-det]")?.focus(); }
  });
  adv.addEventListener("input", () => {
    const err = host.querySelector("#adv-e");
    try {
      const parsed = JSON.parse(adv.value);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("must be a JSON object");
      for (const k of Object.keys(body)) delete body[k];
      Object.assign(body, parsed);
      err.textContent = "";
      fields.innerHTML = groupsHtml(groups(), body);
    } catch (e) { err.textContent = "Not valid JSON yet: " + e.message; }
  });
  return {
    body: () => tidy(kind, structuredClone(body)),
    // Put each server message under the field it names; return the ones that name something else.
    showErrors(errs) {
      host.querySelectorAll("[data-err]").forEach(e => { e.textContent = ""; });
      host.querySelectorAll("[aria-invalid]").forEach(e => e.removeAttribute("aria-invalid"));
      const loose = [];
      for (const e of errs || []) {
        const spot = [...host.querySelectorAll("[data-err]")].find(s => s.dataset.err === e.property)
          || [...host.querySelectorAll("[data-err]")].find(s => e.property.startsWith(s.dataset.err + ".") || e.property.startsWith(s.dataset.err + "["));
        if (!spot) { loose.push(e); continue; }
        spot.textContent = e.message;
        document.getElementById(idOf(e.property))?.setAttribute("aria-invalid", "true");
      }
      return loose;
    },
  };
}
