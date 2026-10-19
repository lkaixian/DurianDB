"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = t => new Date(t * 1000).toLocaleTimeString();
const main = $("#main");
let me = {name: null, roles: ["anonymous"]}, timers = [];
const isUser = () => me.roles.includes("user");
const isAdmin = () => me.roles.includes("superadmin");

async function api(method, path, body) {
  let r;
  try {
    r = await fetch(path, {method, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body),
      headers: method === "GET" ? {} : {"X-Requested-With": "durian", "Content-Type": "application/json"}});
  } catch {
    throw Object.assign(new Error("Cannot reach the DurianDB server. Is `python -m durian.server` running?"), {status: 0});
  }
  let data = null;
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) throw Object.assign(new Error((data && data.error) || r.statusText), {status: r.status, errors: data && data.errors});
  return data;
}

function stopTimers() { timers.forEach(clearInterval); timers = []; }
function every(fn, ms) {
  const run = async () => { try { await fn(); } catch (e) { if (e.status === 401 && me.name) { me = {name: null, roles: ["anonymous"]}; route(); } else showErr(e); } };
  run();
  timers.push(setInterval(run, ms));
}
function showErr(e, where) {
  for (const el of where ? [where] : main.querySelectorAll("[data-live]")) { el.className = "status err"; el.textContent = e.message + " Retrying every few seconds."; }
}

// ---- navigation
const VIEWS = {overview: viewOverview, dashboards: viewDashList, d: viewDashboard, design: viewDesigner, history: viewHistory, config: viewConfig, users: viewUsers, tokens: viewTokens, database: viewDatabase, audit: viewAudit, account: viewAccount};
function nav(view) {
  const items = me.name ? [["overview", "Overview"], ["dashboards", "Dashboards"], ["history", "History"], ...(isAdmin() ? [["config", "Config"], ["users", "Users"], ["tokens", "Tokens"], ["database", "Database"], ["audit", "Audit"]] : []), ["account", me.name]] : [["", "Log in"]];
  $("#nav").innerHTML = items.map(([v, l]) => `<a href="#${v}" ${v === view || (view === "d" && v === "dashboards") || (view === "design" && v === "dashboards") ? 'aria-current="page"' : ""}>${esc(l)}</a>`).join("");
}
function route() {
  stopTimers();
  document.body.classList.remove("kiosk");
  const [view, ...rest] = (location.hash.slice(1) || "overview").split("/");
  if (!me.name && view === "d" && rest[0]) { nav("d"); return viewDashboard(decodeURIComponent(rest[0]), rest[1]); }  // public dashboards need no login
  if (!me.name) { nav(""); return viewLogin(); }
  const v = VIEWS[view] ? view : "overview";
  if (["config", "users", "tokens", "database", "audit"].includes(v) && !isAdmin()) { location.hash = "#overview"; return; }
  nav(v);
  VIEWS[v](...rest.map(decodeURIComponent));
  main.focus({preventScroll: true});
}
window.addEventListener("hashchange", route);

// ---- login and account
function viewLogin() {
  main.innerHTML = `<section class="login" aria-labelledby="li-h"><h2 id="li-h">Log in</h2>
    <form id="login"><label>Username <input name="username" autocomplete="username" required></label>
    <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
    <button type="submit">Log in</button><p id="li-msg" role="alert" class="err"></p></form></section>`;
  $("#login").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try { me = await api("POST", "/api/login", {username: f.get("username"), password: f.get("password")}); location.hash = "#overview"; route(); }
    catch (e) { $("#li-msg").textContent = e.status === 429 ? "Too many failed attempts. Wait a few minutes." : e.message; }
  });
  $("#login input").focus();
}

function viewAccount() {
  main.innerHTML = `<section aria-labelledby="ac-h"><h2 id="ac-h">Account: ${esc(me.name)}</h2>
    <p class="note">Roles: ${esc(me.roles.join(", "))}</p>
    <form id="pw" class="row"><input class="sr" name="username" value="${esc(me.name)}" autocomplete="username" readonly tabindex="-1" aria-hidden="true"><label>Current password <input name="old" type="password" autocomplete="current-password" required></label>
    <label>New password (10+ characters) <input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
    <button type="submit">Change password</button></form><p id="pw-msg" role="status"></p>
    <button id="logout" type="button">Log out</button></section>`;
  $("#pw").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target), out = $("#pw-msg");
    try { await api("POST", "/api/password", {old: f.get("old"), new: f.get("new")}); out.className = ""; out.textContent = "Password changed."; ev.target.reset(); }
    catch (e) { out.className = "err"; out.textContent = (e.errors || []).map(x => x.message).join(" ") || e.message; }
  });
  $("#logout").addEventListener("click", async () => { await api("POST", "/api/logout", {}).catch(() => {}); me = {name: null, roles: ["anonymous"]}; location.hash = ""; route(); });
}

// ---- overview: alarms and live points
function viewOverview() {
  main.innerHTML = `<div class="grid">
    <section aria-labelledby="al-h"><h2 id="al-h">Alarms <small id="al-count"></small></h2><div id="alarms" class="status" data-live>Loading alarms...</div></section>
    <section aria-labelledby="pt-h"><h2 id="pt-h">Live points</h2>
      <form id="filter" class="row"><label>Watch list <select id="f-wl"><option value="">All points</option></select></label>
      <label>Tag <input id="f-tag" placeholder="area:boiler" autocomplete="off"></label></form>
      <div id="points" class="status" data-live>Loading points...</div></section>
    <section class="wide" aria-labelledby="kpi-h"><h2 id="kpi-h">Alarm performance, last 24 hours</h2><div id="kpis" class="status" data-live>Loading...</div></section></div>`;
  api("GET", "/api/watchlists").then(w => { $("#f-wl").insertAdjacentHTML("beforeend", w.map(x => `<option>${esc(x.name)}</option>`).join("")); }).catch(() => {});
  ptSig = "";
  $("#filter").addEventListener("input", () => { ptSig = ""; refreshOverview().catch(showErr); });
  $("#alarms").addEventListener("click", async ev => {
    const d = ev.target.dataset, id = d.ack;
    if (!id && !d.shelve && !d.unshelve) return;
    let call = ["POST", `/api/events/${id}/ack`, undefined];
    if (d.shelve) {  // a native prompt keeps this keyboard and screen reader friendly with no extra markup
      const reason = prompt(`Why shelve the alarm on ${d.shelve}? (3 to 200 characters)`);
      if (reason === null) return;
      const minutes = parseInt(prompt("Shelve for how many minutes? (1 to 1440)", "60"), 10);
      if (!minutes) return;
      call = ["POST", `/api/alarms/${encodeURIComponent(d.shelve)}/shelve`, {reason, minutes}];
    } else if (d.unshelve) call = ["POST", `/api/alarms/${encodeURIComponent(d.unshelve)}/unshelve`, undefined];
    ev.target.disabled = true;
    try { await api(...call); await refreshOverview(); } catch (e) { ev.target.disabled = false; showErr(e, $("#alarms")); }
  });
  $("#points").addEventListener("submit", async ev => {
    ev.preventDefault();
    const name = ev.target.dataset.set, v = ev.target.value.value, out = ev.target.querySelector("output");
    try {
      const url = `/api/points/${encodeURIComponent(name)}/set`, value = ev.submitter?.dataset.release ? null : Number(v);
      let res = await api("POST", url, {value});
      if (res && res.confirm) {  // the point asks for a second step: a native dialog, then the same value with the token
        if (!confirm(`Set ${name} to ${value}?`)) { out.textContent = "cancelled"; return; }
        res = await api("POST", url, {value, confirm: res.confirm});
      }
      out.textContent = "sent";
    }
    catch (e) { out.textContent = e.message; }
  });
  every(refreshOverview, 2000);
  every(refreshKpis, 30000);
}

async function refreshKpis() {
  const k = await api("GET", "/api/alarms/kpis?hours=24");
  const mins = k.median_seconds_to_acknowledge == null ? "none acknowledged" : k.median_seconds_to_acknowledge < 90 ? `${Math.round(k.median_seconds_to_acknowledge)} s` : `${Math.round(k.median_seconds_to_acknowledge / 60)} min`;
  const list = (a, key, unit) => a.length ? a.map(x => `${esc(x.point)} (${x[key]} ${unit})`).join(", ") : "none";
  const rows = [["Alarms raised", k.alarms], ["Average per hour", k.per_hour], ["Peak in 10 minutes", k.peak_per_10min], ["10-minute periods over 10 alarms", k.flood_10min_periods],
    ["Median time to acknowledge", mins], ["Active now / not acknowledged", `${k.active_now} / ${k.unacknowledged_now}`], ["Active over 24 hours", k.stale_now],
    ["Held now (shelved or suppressed)", k.held_now], ["Shelved points / out of service", `${k.shelved_points} / ${k.out_of_service_points}`],
    ["Most frequent", list(k.top_points, "alarms", "alarms")], ["Chattering (3+ in 10 minutes)", list(k.chattering_points, "periods", "periods")]];
  $("#kpis").className = "";
  $("#kpis").innerHTML = `<table class="stack"><tbody>${rows.map(([n, v]) => `<tr><th scope="row">${n}</th><td class="num">${typeof v === "string" ? v : esc(String(v))}</td></tr>`).join("")}</tbody></table>`;
}

async function refreshOverview() {
  const q = new URLSearchParams();
  if ($("#f-wl").value) q.set("watchlist", $("#f-wl").value);
  if ($("#f-tag").value.includes(":")) q.set("tag", $("#f-tag").value.trim());
  const [pts, evs] = await Promise.all([api("GET", "/api/points?" + q), api("GET", "/api/events")]);
  renderPoints(pts, q.size > 0);
  renderAlarms(evs);
}

function renderAlarms(evs) {
  const box = $("#alarms"), open = evs.filter(e => e.acked_ts == null).length;
  $("#al-count").textContent = evs.length ? `${open} unacknowledged of ${evs.length}` : "";
  if (!evs.length) { box.className = "status"; box.textContent = "No alarms. One is raised when a point breaks a limit or stops reporting, and it stays here until someone acknowledges it."; return; }
  box.className = "";
  const STATE = {active_unacked: e => "Active, not acknowledged", active_acked: e => `Active, acknowledged by ${esc(e.acked_by)} at ${fmt(e.acked_ts)}`,
    rtn_unacked: e => `Returned to normal at ${fmt(e.cleared_ts)}, not acknowledged`, closed: e => `Closed: returned to normal at ${fmt(e.cleared_ts)}, acknowledged by ${esc(e.acked_by)}`};
  const HELD = {shelved: "Shelved. ", suppressed: "Suppressed by design. "};
  const state = e => (e.held && !e.cleared_ts ? HELD[e.held] : "") + STATE[e.state](e);
  const shelving = e => !isUser() || e.cleared_ts != null ? "" : e.held === "shelved"
    ? `<button data-unshelve="${esc(e.point)}" aria-label="Unshelve alarm on ${esc(e.point)}">Unshelve</button>`
    : e.held ? "" : `<button data-shelve="${esc(e.point)}" aria-label="Shelve alarm on ${esc(e.point)}">Shelve</button>`;
  box.innerHTML = `<table class="stack"><thead><tr><th>Level</th><th>Point</th><th>Detail</th><th>Raised</th><th>State</th><th><span class="sr">Action</span></th></tr></thead><tbody>` +
    evs.map(e => `<tr class="${e.acked_ts == null ? "open" : "done"}"><td class="lvl" data-label="Level">${esc(e.level_name)}</td><td data-label="Point">${esc(e.point)}</td>
      <td class="num" data-label="Detail">${esc(e.message)}</td><td class="num" data-label="Raised">${fmt(e.ts)}</td><td data-label="State">${state(e)}</td>
      <td>${e.acked_ts == null && isUser() ? `<button data-ack="${e.id}" aria-label="Acknowledge alarm on ${esc(e.point)} raised at ${fmt(e.ts)}">Acknowledge</button>` : ""} ${shelving(e)}</td></tr>`).join("") + "</tbody></table>";
}

function spark(h, lim) {
  if (h.length < 2) return "";
  const vs = h.map(p => p.value).concat([lim.high, lim.low].filter(x => x != null)), lo = Math.min(...vs), hi = Math.max(...vs), span = (hi - lo) || 1;
  const y = v => 52 - ((v - lo) / span) * 48, x = i => (i / (h.length - 1)) * 300;
  const line = v => v == null ? "" : `<line class="lim" x1="0" x2="300" y1="${y(v)}" y2="${y(v)}"/>`;
  return `<svg viewBox="0 0 300 56" preserveAspectRatio="none" role="img" aria-label="Recent values">${line(lim.high)}${line(lim.low)}<path class="line" d="${h.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.value).toFixed(1)).join("")}"/></svg>`;
}

let ptSig = "";
function renderPoints(pts, filtered) {
  const box = $("#points");
  if (!pts.length) { ptSig = ""; box.className = "status"; box.textContent = filtered ? "No points match this filter." : "No points you can see yet. An administrator adds them under Config."; return; }
  box.className = "";
  const sig = pts.map(p => p.name + (p.settable ? "*" : "")).join();
  if (sig !== ptSig) {  // rebuild only when the list changes, so typing in a set field is never interrupted
    ptSig = sig;
    box.innerHTML = pts.map(p => `<div class="pt" data-pt="${esc(p.name)}"><div class="head"><span>${esc(p.name)} <span class="tags">${esc(Object.entries(p.tags).map(([k, v]) => k + ":" + v).join(" "))}</span>${p.description ? `<small class="note"> ${esc(p.description)}</small>` : ""}</span><span class="val num"></span></div><div class="spark"></div>
      ${p.settable && isUser() ? `<form data-set="${esc(p.name)}"><input name="value" type="number" step="any" required placeholder="${p.set_limits.min ?? ""}${p.set_limits.min != null || p.set_limits.max != null ? " to " : "value"}${p.set_limits.max ?? ""}" ${p.set_limits.min != null ? `min="${p.set_limits.min}"` : ""} ${p.set_limits.max != null ? `max="${p.set_limits.max}"` : ""} aria-label="New value for ${esc(p.name)}${p.set_limits.max_step != null ? `, at most ${p.set_limits.max_step} away from the present value` : ""}"><button type="submit">Set</button><button type="submit" data-release="1" formnovalidate>Release</button><output role="status"></output></form>` : ""}</div>`).join("");
  }
  for (const p of pts) {
    const row = [...box.children].find(r => r.dataset.pt === p.name), v = row.querySelector(".val");
    v.textContent = p.last == null ? "no value yet" : `${p.decimals == null ? p.last : Number(p.last).toFixed(p.decimals)}${p.unit ? " " + p.unit : ""}${p.quality && p.quality !== "good" ? " (" + p.quality + ")" : ""}`;
    v.classList.toggle("over", p.last != null && ((p.limits.high != null && p.last > p.limits.high) || (p.limits.low != null && p.last < p.limits.low)));
    row.querySelector(".spark").innerHTML = spark(p.history, p.limits);
  }
}

// ---- history chart
const ROLL = {avg: "Average", min: "Minimum", max: "Maximum", last: "Last value", count: "Sample count"};
function viewHistory() {
  main.innerHTML = `<section aria-labelledby="h-h"><h2 id="h-h">History</h2><form id="hf" class="row"><label>Point <select id="h-point"></select></label>
    <label>Window <select id="h-win"><option value="300">5 minutes</option><option value="3600" selected>1 hour</option><option value="86400">24 hours</option></select></label>
    <label>Rollup <select id="h-roll">${Object.entries(ROLL).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label></form>
    <div id="hist" class="status" data-live>Loading history...</div></section>`;
  let known = [];
  $("#hf").addEventListener("change", () => draw(known).catch(e => showErr(e)));
  every(async () => {
    const pts = await api("GET", "/api/points");
    if (pts.map(p => p.name).join() !== known.map(p => p.name).join()) {
      const cur = $("#h-point").value;
      $("#h-point").innerHTML = pts.map(p => `<option>${esc(p.name)}</option>`).join("");
      if (pts.some(p => p.name === cur)) $("#h-point").value = cur;
    }
    known = pts;
    await draw(pts);
  }, 5000);
}

async function draw(pts) {
  const box = $("#hist"), point = $("#h-point").value;
  if (!point) { box.className = "status"; box.textContent = "No points yet. They appear here as soon as a data source reports a value."; return; }
  const win = $("#h-win").value, roll = $("#h-roll").value, bucket = Math.max(1, Math.floor(win / 120));
  const r = await api("GET", `/api/series?point=${encodeURIComponent(point)}&window=${win}&rollup=${roll}&bucket=${bucket}`);
  const title = `${ROLL[roll]} of ${point}, per ${bucket} s, last ${$("#h-win").selectedOptions[0].text}`;
  if (r.items.length < 2) { box.className = "status"; box.textContent = `${title}: not enough history yet (${r.items.length} bucket${r.items.length === 1 ? "" : "s"}).`; return; }
  const lim = (pts.find(p => p.name === point) || {limits: {}}).limits, lims = roll === "count" ? [] : [lim.high, lim.low].filter(x => x != null);
  const vals = r.items.map(i => i.value), all = vals.concat(lims);
  let lo = Math.min(...all), hi = Math.max(...all); if (hi === lo) { hi += 1; lo -= 1; }
  const L = 48, R = 8, T = 8, B = 22, W = 720, H = 260, t0 = r.items[0].ts, t1 = r.items[r.items.length - 1].ts;
  const x = t => L + ((t - t0) / ((t1 - t0) || 1)) * (W - L - R), y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const f = v => Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);
  box.className = "";
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(title)}">
    <line class="axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/><line class="axis" x1="${L}" x2="${L}" y1="${T}" y2="${H - B}"/>
    <text x="${L - 6}" y="${T + 9}" text-anchor="end">${f(hi)}</text><text x="${L - 6}" y="${H - B}" text-anchor="end">${f(lo)}</text>
    <text x="${L}" y="${H - 6}">${fmt(t0)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${fmt(t1)}</text>
    ${lims.map(v => `<line class="lim" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`).join("")}
    <path class="line" d="${r.items.map((i, k) => (k ? "L" : "M") + x(i.ts).toFixed(1) + " " + y(i.value).toFixed(1)).join("")}"/></svg>
    <p class="note">${esc(title)}. Latest ${f(vals[vals.length - 1])}, range ${f(Math.min(...vals))} to ${f(Math.max(...vals))}${lims.length ? ", dashed lines are alarm limits" : ""}.</p>`;
}

// ---- config editor (administrators)
const KINDS = {source: "Sources", point: "Points", handler: "Handlers", publisher: "Publishers", watchlist: "Watch lists"};
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
    "SQL query (SQLite file, read-only)": {type: "sql", enabled: true, read_roles: ["user"], path: "plant.db", query: "select name, value from readings", period: 10},
  },
  point: {
    "Virtual walk": {source: "demo", ...POINT_BASE, generator: {kind: "walk", start: 50, min: 0, max: 100, step: 1}, detectors: [{kind: "HIGH_LIMIT", limit: 80, level: "WARNING", duration: 0}]},
    "Modbus holding register (uint16)": MODBUS_POINT({range: "holding", address: 0, type: "uint16"}, {settable: true, set_roles: ["user"]}),
    "Modbus holding register (float32, word-swapped)": MODBUS_POINT({range: "holding", address: 10, type: "float32", word_order: "little"}),
    "Modbus input register with scaling": MODBUS_POINT({range: "input", address: 0, type: "int16", multiplier: 0.1, additive: -40}, {unit: "C"}),
    "Modbus coil": MODBUS_POINT({range: "coil", address: 0}, {settable: true, set_roles: ["user"]}),
    "Modbus bit of a register": MODBUS_POINT({range: "holding", address: 40, type: "bool", bit: 3}),
    "HTTP JSON value": {source: "web", ...POINT_BASE, http: {path: "sensors.temp"}, scale: {multiplier: 1, additive: 0}},
    "SQL row by key": {source: "sql", ...POINT_BASE, sql: {key: "tank1"}, scale: {multiplier: 1, additive: 0}},
  },
  handler: {"Webhook": {type: "webhook", url: "http://127.0.0.1:9000/hook", min_level: "WARNING", enabled: true}},
  publisher: {"MQTT over TLS": {type: "mqtt", creds: "lab/mqtt/creds.json", prefix: "durian-out/", points: ["*"], enabled: true}},
  watchlist: {"By tag": {tags: {area: "boiler"}, points: [], read_roles: ["user"]}},
};
const firstTemplate = kind => Object.values(TEMPLATES[kind])[0];

async function viewConfig(kind = "source", name) {
  if (!KINDS[kind]) kind = "source";
  main.innerHTML = `<section aria-labelledby="c-h"><h2 id="c-h">Config</h2>
    <ul class="list">${Object.entries(KINDS).map(([k, l]) => `<li><a class="btn" href="#config/${k}" ${k === kind ? 'aria-current="page"' : ""}>${l}</a></li>`).join("")}</ul>
    <div id="health"></div><div id="cfg" class="status" data-live>Loading...</div></section>`;
  const items = await api("GET", `/api/config/${kind}`).catch(e => { showErr(e); return null; });
  if (!items) return;
  if (kind === "source") {
    const h = await api("GET", "/api/sources").catch(() => []);
    $("#health").innerHTML = h.length ? `<h3>Source health</h3><table class="stack"><thead><tr><th>Name</th><th>Type</th><th>Points</th><th>State</th><th>Connected</th><th>Dropped</th><th>Error</th></tr></thead><tbody>${h.map(s => `<tr><td data-label="Name">${esc(s.name)}</td><td data-label="Type">${esc(s.type)}</td><td class="num" data-label="Points">${s.points}</td><td class="${s.down ? "err" : ""}" data-label="State">${s.down ? "Down: " + esc(s.down) : "Up"}</td><td data-label="Connected">${s.connected == null ? "n/a" : s.connected ? "yes" : "no"}</td><td class="num" data-label="Dropped">${s.dropped ?? "n/a"}</td><td class="${s.error ? "err" : ""}" data-label="Error">${esc(s.error || "none")}</td></tr>`).join("")}</tbody></table>` : "";
  }
  const cur = name === "_new" ? {name: "", ...firstTemplate(kind)} : items.find(i => i.name === name);
  const {name: _n, ...body} = cur || {};
  $("#cfg").className = "";
  $("#cfg").innerHTML = `<ul class="list">${items.map(i => `<li><a class="btn" href="#config/${kind}/${encodeURIComponent(i.name)}" ${i.name === name ? 'aria-current="page"' : ""}>${esc(i.name)}</a></li>`).join("")}<li><a class="btn" href="#config/${kind}/_new">New ${KINDS[kind].toLowerCase().replace(/s$/, "")}</a></li></ul>
    ${cur ? `<form id="ed"><label>Name <input name="name" value="${esc(cur.name)}" ${name === "_new" ? "" : "readonly"} required pattern="[A-Za-z0-9_.\\-]{1,64}"></label>
      ${name === "_new" && Object.keys(TEMPLATES[kind]).length > 1 ? `<label>Start from <select id="tpl">${Object.keys(TEMPLATES[kind]).map(t => `<option>${esc(t)}</option>`).join("")}</select></label>` : ""}
      <label>Settings (JSON) <textarea name="body" spellcheck="false">${esc(JSON.stringify(body, null, 2))}</textarea></label>
      <div class="row"><button type="submit">Save</button>${name !== "_new" ? '<button type="button" id="del">Delete</button>' : ""}</div></form><div id="ed-msg" role="status"></div>`
      : '<p class="note">Choose an item, or create a new one. Saving validates every field and reloads the engine.</p>'}`;
  const msg = $("#ed-msg");
  if (!cur) return;
  $("#tpl")?.addEventListener("change", e => { $("#ed").body.value = JSON.stringify(TEMPLATES[kind][e.target.value], null, 2); });
  const report = (ok, text, errs) => { msg.className = ok ? "" : "err"; msg.innerHTML = esc(text) + (errs ? `<ul class="errors">${errs.map(e => `<li><code>${esc(e.property)}</code>: ${esc(e.message)}</li>`).join("")}</ul>` : ""); };
  $("#ed").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    let parsed;
    try { parsed = JSON.parse(f.get("body")); } catch (e) { return report(false, "Settings are not valid JSON: " + e.message); }
    try { await api("PUT", `/api/config/${kind}/${encodeURIComponent(f.get("name"))}`, parsed); location.hash = `#config/${kind}/${encodeURIComponent(f.get("name"))}`; if (name !== "_new") route(); }
    catch (e) { report(false, e.status === 422 ? "Not saved. Fix these:" : e.message, e.errors); }
  });
  $("#del")?.addEventListener("click", async () => {
    if (!confirm(`Delete ${kind} "${name}"?`)) return;
    try { await api("DELETE", `/api/config/${kind}/${encodeURIComponent(name)}`); location.hash = `#config/${kind}`; }
    catch (e) { report(false, e.message); }
  });
}

// ---- users and audit
async function viewUsers() {
  main.innerHTML = `<section aria-labelledby="u-h"><h2 id="u-h">Users</h2><div id="ul" class="status" data-live>Loading users...</div>
    <h2>Add or update a user</h2><p class="note">Leave the password empty to keep an existing user's password. Roles: superadmin, user, or your own names used in point read and set roles.</p>
    <form id="uf" class="row"><label>Name <input name="name" autocomplete="off" required pattern="[A-Za-z0-9_.\\-]{1,32}"></label><label>Roles (comma separated) <input name="roles" value="user" autocomplete="off" required></label>
    <label>Password (10+ characters) <input name="password" type="password" autocomplete="new-password"></label><button type="submit">Save user</button></form><p id="um" role="status"></p></section>`;
  const load = async () => {
    const us = await api("GET", "/api/users");
    $("#ul").className = "";
    $("#ul").innerHTML = `<table class="stack"><thead><tr><th>Name</th><th>Roles</th><th><span class="sr">Action</span></th></tr></thead><tbody>${us.map(u => `<tr><td data-label="Name">${esc(u.name)}</td><td data-label="Roles">${esc(u.roles.join(", "))}</td><td>${u.name === me.name ? "" : `<button data-del="${esc(u.name)}" aria-label="Delete user ${esc(u.name)}">Delete</button>`}</td></tr>`).join("")}</tbody></table>`;
  };
  $("#ul").addEventListener("click", async ev => {
    const n = ev.target.dataset.del;
    if (n && confirm(`Delete user "${n}"?`)) { try { await api("DELETE", `/api/users/${encodeURIComponent(n)}`); await load(); } catch (e) { $("#um").className = "err"; $("#um").textContent = e.message; } }
  });
  $("#uf").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target), out = $("#um"), body = {roles: f.get("roles").split(",").map(s => s.trim()).filter(Boolean)};
    if (f.get("password")) body.password = f.get("password");
    try { await api("PUT", `/api/users/${encodeURIComponent(f.get("name"))}`, body); out.className = ""; out.textContent = "Saved."; ev.target.reset(); await load(); }
    catch (e) { out.className = "err"; out.textContent = (e.errors || []).map(x => `${x.property}: ${x.message}`).join(". ") || e.message; }
  });
  await load().catch(e => showErr(e));
}

async function viewAudit() {
  main.innerHTML = `<section aria-labelledby="a-h"><h2 id="a-h">Audit trail <small>newest first, last 200</small></h2><div id="au" class="status" data-live>Loading...</div></section>`;
  const rows = await api("GET", "/api/audit").catch(e => { showErr(e); return null; });
  if (!rows) return;
  $("#au").className = rows.length ? "" : "status";
  $("#au").innerHTML = rows.length ? `<table class="stack"><thead><tr><th>Time</th><th>User</th><th>Action</th><th>Detail</th></tr></thead><tbody>${rows.map(r => `<tr><td class="num" data-label="Time">${new Date(r.ts * 1000).toLocaleString()}</td><td data-label="User">${esc(r.user)}</td><td data-label="Action">${esc(r.action)}</td><td data-label="Detail">${esc(r.detail)}</td></tr>`).join("")}</tbody></table>` : "Nothing recorded yet. Config changes, set-value commands, acknowledgements and user changes appear here.";
}

// ---- start
(async () => {
  try { me = await api("GET", "/api/me"); } catch (e) { main.innerHTML = ""; const p = document.createElement("p"); p.className = "status err"; p.textContent = e.message; main.append(p); return; }
  route();
})();
