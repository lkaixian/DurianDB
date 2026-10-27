"use strict";
// Config: the editor for sources, points, handlers, publishers, watch lists and maintenance, their list views, and alarm service. Uses helpers from app.js and forms.js.

// ---- out of service: an administrator stops a point's alarms (for example while a sensor is replaced), with a reason that is audited
async function drawService(name) {
  const holds = await api("GET", "/api/alarms").catch(() => []), h = holds.find(x => x.name === name && x.kind === "oos"), f = $("#svc");
  f.innerHTML = h
    ? `<fieldset><legend>Alarm service</legend><p>Out of service since ${fmt(h.created)} by ${esc(h.user)}: ${esc(h.reason)}. Its alarms are not evaluated.</p><button type="submit">Return to service</button><small class="fe" id="svc-e" role="alert"></small></fieldset>`
    : `<fieldset><legend>Alarm service</legend><div class="fld"><label for="svc-r">Reason for taking it out of service</label><input id="svc-r" required minlength="3" maxlength="200" autocomplete="off"><small class="hint">Its alarms stop and open ones return to normal. Recorded in the audit trail.</small></div><button type="submit">Take out of service</button><small class="fe" id="svc-e" role="alert"></small></fieldset>`;
  f.onsubmit = async ev => {
    ev.preventDefault();
    try { await api("POST", `/api/alarms/${encodeURIComponent(name)}/${h ? "in-service" : "out-of-service"}`, h ? {} : {reason: $("#svc-r").value}); await drawService(name); }
    catch (e) { $("#svc-e").textContent = e.message; }
  };
}

// ---- config editor (administrators)
const KINDS = {source: "Sources", point: "Points", handler: "Handlers", publisher: "Publishers", watchlist: "Watch lists", maintenance: "Maintenance"};
const POINT_BASE = {unit: "", tags: {}, logging: "ON_CHANGE", tolerance: 0, settable: false, read_roles: ["user"], set_roles: []};
const MODBUS_POINT = (m, extra = {}) => ({source: "plc", ...POINT_BASE, modbus: {unit: 1, byte_order: "big", word_order: "big", multiplier: 1, additive: 0, ...m}, ...extra});
const TEMPLATES = {
  source: {
    "Virtual generator": {type: "virtual", enabled: true, read_roles: ["user"]},
    "MQTT over TLS": {type: "mqtt", enabled: true, read_roles: ["user"], creds: "lab/mqtt/creds.json", prefix: "durian/", cmd_prefix: "durian-cmd/", auto_create: true},
    "Modbus TCP": {type: "modbus", enabled: true, read_roles: ["user"], link: {kind: "tcp", host: "127.0.0.1", port: 502}, period: 5, timeout: 0.5, retries: 2, monitor: true},
    "Modbus RTU on a serial port": {type: "modbus", enabled: true, read_roles: ["user"], link: {kind: "serial", port: "COM3", baudrate: 9600, parity: "N", stopbits: 1, bytesize: 8}, framing: "rtu", period: 5, timeout: 0.5, retries: 2},
    "Modbus RTU over TCP (serial converter)": {type: "modbus", enabled: true, read_roles: ["user"], link: {kind: "tcp", host: "192.168.0.50", port: 4001}, framing: "rtu", period: 5},
    "HTTP JSON document": {type: "http_json", enabled: true, read_roles: ["user"], url: "http://127.0.0.1:8000/data", period: 10, timeout: 5},
    "Calculated points": {type: "calc", enabled: true, read_roles: ["user"]},
    "SQL query (SQLite file, read-only)": {type: "sql", enabled: true, read_roles: ["user"], path: "plant.db", query: "select name, value from readings", period: 10},
    "SNMP agent": {type: "snmp", enabled: true, read_roles: ["user"], snmp: {address: "1.3.6.1.2.1.1.5.0"}, period: 15, timeout: 2},
    "Sparkplug B edge node": {type: "sparkplug", enabled: true, read_roles: ["user"], creds: "lab/mqtt/creds.json", group: "Plant"},
    "DNP3 outstation": {type: "dnp3", enabled: true, read_roles: ["user"], dnp3: {address: "127.0.0.1:20000", outstation: 1}, period: 5, timeout: 2},
    "BACnet/IP device": {type: "bacnet", enabled: true, read_roles: ["user"], bacnet: {address: "127.0.0.1:47808", device: 1001}, period: 10, timeout: 2},
    "OPC UA server": {type: "opcua", enabled: true, read_roles: ["user"], opcua: {address: "127.0.0.1:14360", path: ""}, period: 5, timeout: 2},
  },
  point: {
    "Virtual walk": {source: "demo", ...POINT_BASE, generator: {kind: "walk", start: 50, min: 0, max: 100, step: 1}, detectors: [{kind: "HIGH_LIMIT", limit: 80, level: "WARNING", duration: 0}]},
    "Modbus holding register (uint16)": MODBUS_POINT({range: "holding", address: 0, type: "uint16"}, {settable: true, set_roles: ["user"]}),
    "Modbus holding register (float32, word-swapped)": MODBUS_POINT({range: "holding", address: 10, type: "float32", word_order: "little"}),
    "Modbus input register with scaling": MODBUS_POINT({range: "input", address: 0, type: "int16", multiplier: 0.1, additive: -40}, {unit: "C"}),
    "Modbus coil": MODBUS_POINT({range: "coil", address: 0}, {settable: true, set_roles: ["user"]}),
    "Modbus bit of a register": MODBUS_POINT({range: "holding", address: 40, type: "bool", bit: 3}),
    "HTTP JSON value": {source: "web", ...POINT_BASE, http: {path: "sensors.temp"}, scale: {multiplier: 1, additive: 0}},
    "Calculation from other points": {source: "calcs", ...POINT_BASE, calc: {expression: 'p("boiler-temp") * 1.8 + 32'}},
    "SQL row by key": {source: "sql", ...POINT_BASE, sql: {key: "tank1"}, scale: {multiplier: 1, additive: 0}},
  },
  handler: {"Webhook": {type: "webhook", url: "http://127.0.0.1:9000/hook", min_level: "WARNING", enabled: true},
    "Email": {type: "email", to: ["operator@example.com"], from: "durian@example.com", smtp: {host: "smtp.example.com", port: 587, security: "starttls"}, min_level: "URGENT", enabled: true},
    "Set a point": {type: "setpoint", point: "", value: 0, release_on_clear: true, min_level: "URGENT", enabled: true},
    "Slack": {type: "slack", url: "https://hooks.slack.com/services/...", min_level: "URGENT", enabled: true},
    "Scheduled report": {type: "report", to: ["operator@example.com"], from: "durian@example.com", smtp: {host: "smtp.example.com", port: 587, security: "starttls"}, hour: 7, hours: 24, enabled: true}},
  publisher: {"MQTT over TLS": {type: "mqtt", creds: "lab/mqtt/creds.json", prefix: "durian-out/", points: ["*"], enabled: true},
    "HTTP sender": {type: "http", url: "https://other-host:8765/api/ingest/source-name", points: ["*"], enabled: true}},
  watchlist: {"By tag": {tags: {area: "boiler"}, points: [], read_roles: ["user"]}},
  maintenance: {"Weekly window": {enabled: true, mode: "suppress", reason: "", scope: {points: []}, schedule: {type: "weekly", days: [0, 1, 2, 3, 4], start: "22:00", end: "06:00"}}},
};
const firstTemplate = kind => Object.values(TEMPLATES[kind])[0];

async function viewConfig(kind = "source", name) {
  if (!KINDS[kind]) kind = "source";
  main.innerHTML = `<section aria-labelledby="c-h"><h2 id="c-h">Config</h2>
    <ul class="list">${Object.entries(KINDS).map(([k, l]) => `<li><a class="btn" href="#config/${k}" ${k === kind ? 'aria-current="page"' : ""}>${l}</a></li>`).join("")}</ul>
    <div id="cfg" class="status" data-live>Loading...</div></section>`;
  const items = await api("GET", `/api/config/${kind}`).catch(e => { showErr(e); return null; });
  if (!items) return;
  if (!name) return renderConfigList(kind, items);
  const sources = kind === "point" ? await api("GET", "/api/config/source").catch(() => []) : [];
  const newPoint = () => { const s = sources[0], start = s ? POINT_PARTS[s.type].start() : {}; return {source: s ? s.name : "", ...POINT_BASE, ...start}; };
  const cur = name === "_new" ? {name: "", ...(kind === "point" ? newPoint() : firstTemplate(kind))} : items.find(i => i.name === name);
  const {name: _n, ...body} = cur || {};
  $("#cfg").className = "";
  $("#cfg").innerHTML = `<p class="crumb"><a href="#config/${kind}">&lsaquo; All ${KINDS[kind].toLowerCase()}</a></p><h3>${name === "_new" ? "New " + KINDS[kind].toLowerCase().replace(/s$/, "") : esc(name)}</h3>
    ${cur ? `<form id="ed"><label>Name <input name="name" value="${esc(cur.name)}" ${name === "_new" ? "" : "readonly"} required pattern="[A-Za-z0-9_.\\-]{1,64}"></label>
      ${name === "_new" && kind !== "point" && Object.keys(TEMPLATES[kind]).length > 1 ? `<label>${kind === "source" ? "Source type" : "Start from"} <select id="tpl">${Object.keys(TEMPLATES[kind]).map(t => `<option>${esc(t)}</option>`).join("")}</select></label>` : ""}
      <div id="editor"></div>
      <div class="row"><button type="submit">Save</button>${kind === "source" ? '<button type="button" id="test">Test settings</button>' : ""}${name !== "_new" ? '<button type="button" id="del">Delete</button>' : ""}</div></form><div id="ed-msg" role="status"></div><div id="test-out" role="status"></div>
      ${kind === "point" && name !== "_new" ? '<form id="svc" class="svc"></form>' : ""}`
      : '<p class="note">Choose an item, or create a new one. Saving validates every field and reloads the engine.</p>'}`;
  const msg = $("#ed-msg");
  if (!cur) return;
  if (kind === "point" && name !== "_new") await drawService(name);
  const mount = b => mountEditor($("#editor"), {kind, body: structuredClone(b), sources});
  let editor = mount(body);
  $("#tpl")?.addEventListener("change", e => { editor = mount(TEMPLATES[kind][e.target.value]); });
  const report = (ok, text, errs) => { msg.className = ok ? "" : "err"; msg.innerHTML = esc(text) + (errs ? `<ul class="errors">${errs.map(e => `<li><code>${esc(e.property)}</code>: ${esc(e.message)}</li>`).join("")}</ul>` : ""); };
  $("#test")?.addEventListener("click", async ev => {
    const out = $("#test-out"), b = ev.target;
    b.disabled = true; out.className = "status"; out.textContent = "Testing...";
    try {
      const r0 = await api("POST", "/api/test/source", editor.body());
      const j = r0.job ? await waitJob(r0.job) : r0;
      if (j.state === "error") throw new Error(j.error);
      const r = j.state === "done" ? j.result : j;
      out.className = r.ok ? "" : "err";
      out.innerHTML = `<p>${esc(r.ok ? "Passed. " + r.message : "Failed. " + r.message)}</p>` + (r.found.length ? `<table class="stack"><thead><tr><th>Path or key</th><th>Value</th></tr></thead><tbody>${r.found.map(f => `<tr><td data-label="Path or key"><code>${esc(f.name)}</code></td><td class="num" data-label="Value">${esc(String(f.value))}</td></tr>`).join("")}</tbody></table>` : "");
    } catch (e) { out.className = "err"; out.textContent = e.message; }
    b.disabled = false;
  });
  $("#ed").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const parsed = editor.body();
    try { await api("PUT", `/api/config/${kind}/${encodeURIComponent(f.get("name"))}`, parsed); location.hash = `#config/${kind}/${encodeURIComponent(f.get("name"))}`; if (name !== "_new") route(); }
    catch (e) {
      const loose = e.status === 422 ? editor.showErrors(e.errors) : [];
      report(false, e.status === 422 ? (loose.length ? "Not saved. Fix these:" : "Not saved. The problems are marked on the fields above.") : e.message, loose.length ? loose : null);
    }
  });
  $("#del")?.addEventListener("click", async () => {
    if (!confirm(`Delete ${kind} "${name}"?`)) return;
    try { await api("DELETE", `/api/config/${kind}/${encodeURIComponent(name)}`); location.hash = `#config/${kind}`; }
    catch (e) { report(false, e.message); }
  });
}

// ---- config lists: one dense, filterable table per kind (rows open the editor)
const yes = v => v ? "Yes" : "No";
const tagText = t => Object.entries(t || {}).map(([k, v]) => k + ":" + v).join(", ");
// what a handler or publisher's deliveries are doing: a route that has been failing for a week must be visible
const delivery = o => !o ? "" : o.failed ? `${o.failed} failed, last ${o.last_error || "error"}` : o.last_ok ? "Working" : "Nothing sent yet";
const LISTS = {
  source: {cols: ["Name", "Type", "Enabled", "Points", "State", "Last poll", "Dropped", "Error"], cell: (i, x) => [null, i.type, yes(i.enabled !== false), x.points ?? "", x.down ? "Down: " + x.down : x.name ? "Up" : "Not running", x.poll_ms == null ? "" : `${(x.poll_ms / 1000).toFixed(2)} s${x.overruns ? `, ${x.overruns} too slow` : ""}`, x.dropped ?? "", x.error || ""]},
  point: {cols: ["Name", "Folder", "Source", "Value", "Quality", "Alarms", "Settable", "Tags"], cell: (i, x) => [null, i.folder || "", i.source, x.last == null ? "no value yet" : `${x.decimals == null || typeof x.last === "string" ? x.last : Number(x.last).toFixed(x.decimals)}${x.unit ? " " + x.unit : ""}`, x.quality || "", (i.detectors || []).length || "none", yes(i.settable), tagText(i.tags)]},
  handler: {cols: ["Name", "Type", "Target", "From level", "Enabled", "Delivery"], cell: (i, x, o) => [null, i.type, i.type === "email" || i.type === "report" ? (i.to || []).join(", ") : i.type === "setpoint" ? `${i.point} = ${i.value}` : i.url || "", i.min_level || "", yes(i.enabled !== false), delivery(o)]},
  publisher: {cols: ["Name", "Type", "Topic prefix or URL", "Points", "Enabled", "Delivery"], cell: (i, x, o) => [null, i.type, i.prefix || i.url || "", (i.points || []).join(", "), yes(i.enabled !== false), delivery(o)]},
  maintenance: {cols: ["Name", "Applies to", "During", "When", "Enabled"], cell: i => [null, [...(i.scope?.points || []), ...(i.scope?.sources || []).map(s => "source " + s), tagText(i.scope?.tags)].filter(Boolean).join(", "), {suppress: "Hold alarms", acknowledge: "Acknowledge", drop: "Drop alarms"}[i.mode] || i.mode,
    i.schedule?.type === "weekly" ? `${(i.schedule.days || []).map(d => ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d]).join(" ")} ${i.schedule.start}-${i.schedule.end}` : i.schedule?.type === "once" ? `${fmt(i.schedule.from)} to ${fmt(i.schedule.to)}` : i.active ? "Switched on" : "Switched off", yes(i.enabled !== false)]},
  watchlist: {cols: ["Name", "Tags", "Points", "Who can read"], cell: i => [null, tagText(i.tags), (i.points || []).join(", "), (i.read_roles || []).join(", ")]},
};
async function renderConfigList(kind, items) {
  const live = kind === "source" ? await api("GET", "/api/sources").catch(() => []) : kind === "point" ? await api("GET", "/api/points").catch(() => []) : [];
  const outs = ["handler", "publisher"].includes(kind) ? await api("GET", "/api/outputs").catch(() => ({targets: []})) : {targets: []};
  const by = Object.fromEntries(live.map(x => [x.name, x])), L = LISTS[kind], one = KINDS[kind].toLowerCase().replace(/s$/, ""), out = Object.fromEntries((outs.targets || []).map(t => [t.name, t]));
  const box = $("#cfg");
  box.className = "";
  box.innerHTML = `<div class="row"><a class="btn" href="#config/${kind}/_new">New ${one}</a>
    ${kind === "point" ? `<a class="btn" href="/api/export/points" download>Export CSV</a><label>Import CSV <input id="csv" type="file" accept=".csv,text/csv"></label>` : ""}
    ${items.length > 5 ? `<label>Filter <input id="lf" type="search" placeholder="name, source, tag" autocomplete="off"></label>` : ""}<span id="lcount" class="note" role="status"></span></div><div id="csv-out" role="status"></div>
    ${items.length ? `<table class="stack dense"><thead><tr>${L.cols.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${items.map(i => {
      const cells = L.cell(i, by[i.name] || {}, out[i.name]);
      return `<tr data-q="${esc((i.name + " " + cells.join(" ")).toLowerCase())}">${cells.map((c, n) => n === 0
        ? `<td data-label="${L.cols[0]}"><a href="#config/${kind}/${encodeURIComponent(i.name)}">${esc(i.name)}</a></td>`
        : `<td data-label="${L.cols[n]}" class="${String(c).startsWith("Down") || c === "bad" || ((L.cols[n] === "Error" || L.cols[n] === "Delivery") && c && !String(c).startsWith("Working")) ? "err" : ""}">${esc(String(c))}</td>`).join("")}</tr>`;
    }).join("")}</tbody></table>` : `<p class="status">No ${KINDS[kind].toLowerCase()} yet. ${kind === "source" ? "Add a source first, then points that read from it." : kind === "point" ? "Create a source first, then add points that read from it." : `Use New ${one} to add one.`}</p>`}`;
  box.querySelector("table.dense") && makeSortable(box.querySelector("table.dense"));
  $("#csv")?.addEventListener("change", async ev => {  // check the whole file first; nothing is written until the person applies it
    const out = $("#csv-out"), file = ev.target.files[0];
    if (!file) return;
    const text = await file.text();
    const show = (ok, html) => { out.className = ok ? "" : "err"; out.innerHTML = html; };
    try {
      const r = await api("POST", "/api/import/points", text, "text/csv");
      show(true, `<p>${esc(file.name)} is valid: ${r.created} point(s) would be created and ${r.updated} updated.</p><button type="button" id="csv-go">Apply to ${r.created + r.updated} point(s)</button>`);
      $("#csv-go").addEventListener("click", async () => {
        try { const d = await api("POST", "/api/import/points?apply=1", text, "text/csv"); show(true, `<p>Imported: ${d.created} created, ${d.updated} updated.</p>`); setTimeout(route, 800); }
        catch (e) { show(false, esc(e.message)); }
      });
    } catch (e) {
      show(false, `<p>${esc(e.status === 422 ? "Nothing was imported. Fix these rows:" : e.message)}</p>` + (e.errors ? `<table class="stack dense"><thead><tr><th>Line</th><th>Point</th><th>Column</th><th>Problem</th></tr></thead><tbody>${e.errors.map(x => `<tr><td data-label="Line" class="num">${x.line}</td><td data-label="Point">${esc(x.point ?? "")}</td><td data-label="Column"><code>${esc(x.property)}</code></td><td data-label="Problem">${esc(x.message)}</td></tr>`).join("")}</tbody></table>` : ""));
    }
  });
  const count = () => { const rows = [...box.querySelectorAll("tbody tr")]; $("#lcount").textContent = rows.length ? `${rows.filter(r => !r.hidden).length} of ${rows.length}` : ""; };
  count();
  $("#lf")?.addEventListener("input", e => {
    const q = e.target.value.trim().toLowerCase();
    box.querySelectorAll("tbody tr").forEach(r => { r.hidden = q !== "" && !q.split(/\s+/).every(w => r.dataset.q.includes(w)); });
    count();
  });
}
