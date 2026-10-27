"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = t => new Date(t * 1000).toLocaleTimeString();
const main = $("#main");
let me = {name: null, roles: ["anonymous"]}, timers = [];
const isUser = () => me.roles.includes("user");
const isAdmin = () => me.roles.includes("superadmin");

async function api(method, path, body, type) {  // `type` sends `body` as it is, with that content type, instead of as JSON
  let r;
  try {
    r = await fetch(path, {method, credentials: "same-origin", body: body === undefined ? undefined : type ? body : JSON.stringify(body),
      headers: method === "GET" ? {} : {"X-Requested-With": "durian", "Content-Type": type || "application/json"}});
  } catch {
    throw Object.assign(new Error("Cannot reach the DurianDB server. Is `python -m durian.server` running?"), {status: 0});
  }
  let data = null;
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) throw Object.assign(new Error((data && data.error) || r.statusText), {status: r.status, errors: data && data.errors});
  return data;
}

async function waitJob(id, ms = 500) {  // poll a background job until it is done or has an error sentence
  for (;;) {
    const j = await api("GET", `/api/jobs/${id}`);
    if (j.state !== "running") return j;
    await new Promise(r => setTimeout(r, ms));
  }
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
const VIEWS = {overview: viewOverview, dashboards: viewDashList, d: viewDashboard, design: viewDesigner, history: viewHistory, reports: viewReports, point: viewPoint, alarms: viewAlarms, config: viewConfig, users: viewUsers, tokens: viewTokens, database: viewDatabase, audit: viewAudit, account: viewAccount};
function nav(view) {
  const groups = me.name ? [["Monitor", [["overview", "Overview"], ["alarms", "Alarms"], ["dashboards", "Dashboards"], ["history", "History"], ["reports", "Reports"]]],
    ...(isAdmin() ? [["Configure", [["config", "Config"], ["users", "Users"], ["tokens", "Tokens"]]], ["System", [["database", "Database"], ["audit", "Audit"]]]] : []),
    ["Signed in", [["account", me.name]]]] : [];
  document.querySelector(".shell").classList.toggle("solo", !me.name);  // before sign-in there is nothing to navigate to
  const cur = v => v === view || (view === "point" && v === "overview") || (view === "d" && v === "dashboards") || (view === "design" && v === "dashboards") ? 'aria-current="page"' : "";
  $("#nav").innerHTML = groups.map(([g, items]) => `<p class="ng">${esc(g)}</p>${items.map(([v, l]) => `<a href="#${v}" ${cur(v)}>${esc(l)}</a>`).join("")}`).join("");
}
// The header shows how many alarms still wait for someone, whatever page is open. Text and colour together.
// Optional audible alert: a short beep when the number of unacknowledged alarms goes up. Off until someone turns it on (browsers need a click first).
let sound = {on: false, ctx: null, last: null};
try { sound.on = localStorage.getItem("durian.sound") === "1"; } catch { /* private window: stays off */ }
function beep() {
  try {
    sound.ctx ??= new AudioContext();
    const o = sound.ctx.createOscillator(), g = sound.ctx.createGain();
    o.frequency.value = 880; g.gain.value = 0.15;
    o.connect(g); g.connect(sound.ctx.destination); o.start(); o.stop(sound.ctx.currentTime + 0.3);
  } catch { /* no audio available */ }
}
function drawSound() { const b = $("#snd"); b.hidden = !me.name; b.setAttribute("aria-pressed", String(sound.on)); b.textContent = "Alarm sound: " + (sound.on ? "on" : "off"); }
$("#snd").addEventListener("click", () => {
  sound.on = !sound.on;
  try { localStorage.setItem("durian.sound", sound.on ? "1" : "0"); } catch { /* not saved */ }
  if (sound.on) beep();  // also proves the speakers work, and satisfies the browser's need for a click
  drawSound();
});

async function refreshAlarmCount() {
  const a = $("#alarm-count");
  drawSound();
  if (!me.name) { a.hidden = true; return; }
  try {
    const n = (await api("GET", "/api/events")).filter(e => e.acked_ts == null && e.state !== "closed").length;
    if (sound.on && sound.last != null && n > sound.last) beep();
    sound.last = n;
    a.hidden = false; a.className = n ? "ac on" : "ac";
    a.textContent = n ? `${n} alarm${n === 1 ? "" : "s"} not acknowledged` : "No unacknowledged alarms";
  } catch { a.hidden = true; }
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
  main.innerHTML = `<section class="login" aria-labelledby="li-h"><div class="card">
    <p class="brand">DurianDB</p><h2 id="li-h">Sign in</h2><p class="note">Plant monitoring and alarm console.</p>
    <form id="login"><label>Username <input name="username" autocomplete="username" required></label>
    <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
    <label id="li-code" hidden>Authentication code <input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}"></label>
    <button type="submit" class="primary">Sign in</button><p id="li-msg" role="alert" class="err"></p></form>
    <div id="li-sso"></div>
    <details class="forgot"><summary>Forgot your password?</summary>
      <p class="note">Ask an administrator for a reset code (Users page, Reset password). The code works once and expires after an hour.</p>
      <form id="reset"><label>Username <input name="username" autocomplete="username" required></label>
      <label>Reset code <input name="code" autocomplete="off" required></label>
      <label>New password (10+ characters) <input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
      <button type="submit">Set new password</button><p id="rs-msg" role="status"></p></form></details></div></section>`;
  $("#login").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try { me = await api("POST", "/api/login", {username: f.get("username"), password: f.get("password"), code: f.get("code") || undefined}); location.hash = "#overview"; route(); }
    catch (e) {
      $("#li-msg").textContent = e.status === 429 ? "Too many failed attempts. Wait a few minutes." : e.message;
      if (/code required/.test(e.message)) { $("#li-code").hidden = false; $("#li-code input").required = true; $("#li-code input").focus(); }
    }
  });
  $("#reset").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target), out = $("#rs-msg");
    try {
      await api("POST", "/api/reset", {username: f.get("username"), code: f.get("code").trim(), new: f.get("new")});
      out.className = ""; out.textContent = "Password changed. Sign in with the new one.";
      ev.target.reset();
    } catch (e) { out.className = "err"; out.textContent = (e.errors || []).map(x => x.message).join(" ") || e.message; }
  });
  api("GET", "/api/auth/sso").then(s => {  // a link only when an administrator configured single sign-on
    if (s.configured) $("#li-sso").innerHTML = `<p class="note">or</p><a class="btn" href="/api/auth/sso/start">Sign in with ${esc(s.issuer)}</a>`;
  }).catch(() => { /* no SSO status: password sign-in still works */ });
  $("#login input").focus();
}

function viewAccount() {
  main.innerHTML = `<section aria-labelledby="ac-h"><h2 id="ac-h">Account: ${esc(me.name)}</h2>
    <p class="note">Roles: ${esc(me.roles.join(", "))}</p>
    <form id="pw" class="row"><input class="sr" name="username" value="${esc(me.name)}" autocomplete="username" readonly tabindex="-1" aria-hidden="true"><label>Current password <input name="old" type="password" autocomplete="current-password" required></label>
    <label>New password (10+ characters) <input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
    <button type="submit">Change password</button></form><p id="pw-msg" role="status"></p>
    <h3>Two-factor sign-in</h3><div id="mfa"></div>
    <button id="logout" type="button">Log out</button></section>`;
  const mfa = () => {
    const box = $("#mfa");
    box.innerHTML = me.mfa
      ? `<p class="note">On. Signing in asks for a code from your authenticator app.</p><form id="mfa-off" class="row"><label>Password <input name="password" type="password" autocomplete="current-password" required></label><button type="submit">Turn off</button></form><p id="mfa-msg" role="status"></p>`
      : `<button id="mfa-on" type="button">Set up an authenticator app</button><p id="mfa-msg" role="status"></p>`;
    const say = (t, bad) => { $("#mfa-msg").className = bad ? "err" : ""; $("#mfa-msg").textContent = t; };
    $("#mfa-on")?.addEventListener("click", async () => {
      try {
        const r = await api("POST", "/api/mfa/setup", {});
        box.innerHTML = `<p class="note">Add this key to an authenticator app (type it in, or open <a href="${esc(r.uri)}">this link</a> on the phone), then enter the 6-digit code it shows.</p><p><code>${esc(r.secret)}</code></p>
          <form id="mfa-ok" class="row"><label>Code <input name="code" inputmode="numeric" maxlength="6" pattern="[0-9]{6}" autocomplete="one-time-code" required></label><button type="submit">Turn on</button></form><p id="mfa-msg" role="status"></p>`;
        $("#mfa-ok").addEventListener("submit", async ev => {
          ev.preventDefault();
          try { await api("POST", "/api/mfa/enable", {code: new FormData(ev.target).get("code")}); me.mfa = true; mfa(); }
          catch (e) { say(e.message, true); }
        });
      } catch (e) { say(e.message, true); }
    });
    $("#mfa-off")?.addEventListener("submit", async ev => {
      ev.preventDefault();
      try { await api("POST", "/api/mfa/disable", {password: new FormData(ev.target).get("password")}); me.mfa = false; mfa(); }
      catch (e) { say(e.message, true); }
    });
  };
  mfa();
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
      <label>Tag <input id="f-tag" placeholder="area:boiler" autocomplete="off"></label>
      <label>Folder <input id="f-folder" placeholder="Plant/Boiler" autocomplete="off"></label>
      ${isUser() ? `<button type="button" id="f-save" title="Keep the points shown now as a watch list only you can see">Save view as my watch list</button> <button type="button" id="f-del" hidden>Delete my list</button> <output id="f-out" role="status"></output>` : ""}</form>
      <div id="points" class="status" data-live>Loading points...</div></section>
    <section class="wide" aria-labelledby="kpi-h"><h2 id="kpi-h">Alarm performance, last 24 hours</h2><div id="kpis" class="status" data-live>Loading...</div></section></div>`;
  const lists = async pick => {
    const w = await api("GET", "/api/watchlists");
    $("#f-wl").innerHTML = `<option value="">All points</option>` + w.map(x => `<option value="${esc(x.name)}" ${x.mine ? "data-mine" : ""}>${esc(x.name)}${x.mine ? " (mine)" : ""}</option>`).join("");
    if (pick) $("#f-wl").value = pick;
    if ($("#f-del")) $("#f-del").hidden = !($("#f-wl").selectedOptions[0]?.hasAttribute("data-mine"));
  };
  lists().catch(() => {});
  $("#f-save")?.addEventListener("click", async () => {
    const name = prompt("Name for this watch list (letters, digits, dot, dash, underscore):");
    if (!name) return;
    try { await api("PUT", `/api/mylists/${encodeURIComponent(name.trim())}`, {points: lastPts.map(p => p.name)}); $("#f-out").textContent = "saved"; await lists(name.trim()); ptSig = ""; refreshOverview().catch(showErr); }
    catch (e) { $("#f-out").textContent = e.message; }
  });
  $("#f-del")?.addEventListener("click", async () => {
    const name = $("#f-wl").value;
    if (!name || !confirm(`Delete your watch list ${name}?`)) return;
    try { await api("DELETE", `/api/mylists/${encodeURIComponent(name)}`); $("#f-out").textContent = "deleted"; await lists(); ptSig = ""; refreshOverview().catch(showErr); }
    catch (e) { $("#f-out").textContent = e.message; }
  });
  ptSig = "";
  $("#filter").addEventListener("input", () => { if ($("#f-del")) $("#f-del").hidden = !($("#f-wl").selectedOptions[0]?.hasAttribute("data-mine")); ptSig = ""; refreshOverview().catch(showErr); });
  bindAlarmActions($("#alarms"), refreshOverview);
  bindAckAll($("#alarms"), refreshOverview);
  bindSetForms($("#points"));
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

let lastPts = [];  // what the overview is showing, for "save view"
async function refreshOverview() {
  const q = new URLSearchParams();
  if ($("#f-wl").value) q.set("watchlist", $("#f-wl").value);
  if ($("#f-tag").value.includes(":")) q.set("tag", $("#f-tag").value.trim());
  if ($("#f-folder").value.trim()) q.set("folder", $("#f-folder").value.trim());
  const [pts, evs] = await Promise.all([api("GET", "/api/points?" + q), api("GET", "/api/events")]);
  lastPts = pts;
  renderPoints(pts, q.size > 0);
  renderAlarms(evs);
}

// Acknowledge, shelve and unshelve buttons inside `box`; `refresh` redraws the page afterwards.
function bindAlarmActions(box, refresh) {
  box.addEventListener("click", async ev => {
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
    try { await api(...call); await refresh(); refreshAlarmCount(); } catch (e) { ev.target.disabled = false; showErr(e, box); }
  });
}

// Set / Release forms (form[data-set]) inside `box`, including the confirm step a point may ask for.
function bindSetForms(box) {
  box.addEventListener("submit", async ev => {
    ev.preventDefault();
    const name = ev.target.dataset.set, v = ev.target.value.value, out = ev.target.querySelector("output");
    try {
      const url = `/api/points/${encodeURIComponent(name)}/set`, value = ev.submitter?.dataset.release ? null : ev.target.value.type === "text" ? v : Number(v);
      const reason = ev.target.reason?.value.trim() || undefined;
      let res = await api("POST", url, {value, reason});
      if (res && res.confirm) {  // the point asks for a second step: a native dialog, then the same value with the token
        if (!confirm(`Set ${name} to ${value}?`)) { out.textContent = "cancelled"; return; }
        res = await api("POST", url, {value, reason, confirm: res.confirm});
      }
      out.textContent = "sent";
    }
    catch (e) { out.textContent = e.message; }
  });
}

const reasonHtml = p => p.set_limits?.reason_required ? `<input name="reason" required maxlength="200" placeholder="reason" aria-label="Reason for changing ${esc(p.name)}"> ` : "";
function setFormHtml(p) {
  if (p.settable && isUser() && p.states) return `<form data-set="${esc(p.name)}"><label class="inl">New state <select name="value" aria-label="New state for ${esc(p.name)}">${Object.entries(p.states).map(([k, l]) => `<option value="${esc(k)}" ${p.last === Number(k) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>${reasonHtml(p)}<button type="submit">Set</button><button type="submit" data-release="1" formnovalidate>Release</button><output role="status"></output></form>`;
  return p.settable && isUser() ? `<form data-set="${esc(p.name)}"><input name="value" ${p.datatype === "text" ? 'type="text" maxlength="255"' : 'type="number" step="any"'} required placeholder="${p.datatype === "text" ? "" : p.set_limits.min ?? ""}${p.set_limits.min != null || p.set_limits.max != null ? " to " : "value"}${p.set_limits.max ?? ""}" ${p.set_limits.min != null ? `min="${p.set_limits.min}"` : ""} ${p.set_limits.max != null ? `max="${p.set_limits.max}"` : ""} aria-label="New value for ${esc(p.name)}${p.set_limits.max_step != null ? `, at most ${p.set_limits.max_step} away from the present value` : ""}">${reasonHtml(p)}<button type="submit">Set</button><button type="submit" data-release="1" formnovalidate>Release</button><output role="status"></output></form>` : "";
}

// ---- one point: what it is doing now, how it got here, what is wrong with it, and what an operator can do about it
async function viewPoint(name) {
  const q = encodeURIComponent(name);
  main.innerHTML = `<p class="crumb"><a href="#overview">Overview</a> &rsaquo; Point</p>
    <h2 id="pd-h">${esc(name)}</h2>
    <div id="pd-top" class="status" data-live>Loading point...</div>
    <div class="pd-grid">
      <section aria-labelledby="pd-t"><h3 id="pd-t">Trend</h3>
        <form class="row" id="pd-win"><label>Window <select id="pd-hours"><option value="3600">1 hour</option><option value="21600">6 hours</option><option value="86400" selected>24 hours</option><option value="604800">7 days</option></select></label></form>
        <div id="pd-chart" class="status">Loading trend...</div></section>
      <section aria-labelledby="pd-a"><h3 id="pd-a">Alarms on this point</h3><div id="pd-alarms" class="status" data-live>Loading alarms...</div></section>
      <section id="pd-control-box" aria-labelledby="pd-c" hidden><h3 id="pd-c">Set the value</h3><div id="pd-control"></div></section>
      <section aria-labelledby="pd-n"><h3 id="pd-n">Operator notes</h3>
        <div id="pd-notes" class="status" data-live>Loading notes...</div>
        <form id="pd-note-f" class="row" hidden><label>Add a note <input name="text" maxlength="2000" required autocomplete="off" placeholder="What the next shift should know"></label><button type="submit">Add</button><small class="fe" id="pd-note-e" role="alert"></small></form></section>
      <section aria-labelledby="pd-s"><h3 id="pd-s">Settings</h3><div id="pd-settings" class="status">Loading...</div></section>
    </div>`;
  const when = t => t == null ? "never" : fmt(t);
  const fix = (p, v) => v == null ? "none" : p.decimals == null || typeof v === "string" ? String(v) : Number(v).toFixed(p.decimals);
  const kinds = {HIGH_LIMIT: "above", LOW_LIMIT: "below", RATE_OF_CHANGE: "changing faster than", NO_UPDATE: "no update for", STATE: "in state", RANGE: "range", CHANGE: "every change", NO_CHANGE: "unchanged for", SMOOTHNESS: "scatter above", CUSUM: "drift from", ANOMALY: "outlier", TEXT: "text"};
  let drawn = "", current = null;  // `current` is what the trend needs for its limit lines
  async function refresh() {
    let p;
    try { p = await api("GET", `/api/points/${q}`); }
    catch (e) {
      if (e.status !== 404) throw e;
      stopTimers();  // retrying will not make a missing point appear
      main.innerHTML = `<p class="crumb"><a href="#overview">Overview</a> &rsaquo; Point</p><h2>No point named ${esc(name)}</h2><p class="status">It does not exist, or you cannot see it. Check the name, or ask an administrator.</p>`;
      return;
    }
    const over = p.last != null && ((p.limits.high != null && p.last > p.limits.high) || (p.limits.low != null && p.last < p.limits.low));
    const notes = [];
    if (p.quality !== "good") notes.push(`Quality is ${p.quality}: ${p.quality === "bad" ? "the source is not supplying this value, so the last known one is shown." : "treat this value with care."}`);
    if (p.source_down) notes.push(`Source ${p.source} is down: ${p.source_down}.`);
    for (const m of p.maintenance || []) notes.push(`In maintenance window ${m.name}: ${{suppress: "alarms are held", acknowledge: "alarms are acknowledged at once", drop: "alarms are not recorded"}[m.mode]}.`);
    if (p.hold?.kind === "oos") notes.push(`Out of service since ${fmt(p.hold.created)} by ${p.hold.user}: ${p.hold.reason}. Its alarms are not evaluated.`);
    if (p.hold?.kind === "shelved") notes.push(`Alarms shelved by ${p.hold.user} until ${fmt(p.hold.until)}: ${p.hold.reason}.`);
    current = p;
    $("#pd-top").className = "";
    $("#pd-top").innerHTML = `<p class="pd-val num ${over ? "over" : ""}">${p.last == null ? "no value yet" : esc(p.text ?? fix(p, p.last))}<span class="u"> ${p.text == null ? esc(p.unit) : ""}</span></p>
      ${p.description ? `<p>${esc(p.description)}</p>` : ""}
      <p class="note">Source ${esc(p.source)} (${esc(p.source_type)}). Value time ${esc(when(p.last_ts))}, received ${esc(when(p.received))}.${Object.keys(p.tags).length ? " Tags: " + esc(Object.entries(p.tags).map(([k, v]) => k + ":" + v).join(", ")) + "." : ""}</p>
      ${notes.map(n => `<p class="err">${esc(n)}</p>`).join("")}`;
    const sig = JSON.stringify([p.settable, p.set_limits]);
    if (sig !== drawn) {  // keep what a person is typing: rebuild the form only when its rules change
      drawn = sig;
      $("#pd-control-box").hidden = !(p.settable && isUser());
      $("#pd-control").innerHTML = setFormHtml(p) + (p.set_limits ? `<p class="note">${p.set_limits.min != null || p.set_limits.max != null ? `Accepts ${p.set_limits.min ?? "any"} to ${p.set_limits.max ?? "any"}. ` : ""}${p.set_limits.max_step != null ? `At most ${p.set_limits.max_step} away from the present value per write. ` : ""}${p.set_limits.confirm ? "Asks for confirmation. " : ""}Release hands the point back to its source.</p>` : "");
    }
    const arg = d => d.kind === "STATE" ? (d.states || []).map(x => p.states?.[String(x)] ?? x).join(" or ") : d.kind === "RANGE" ? `${d.mode === "inside" ? "inside" : "outside"} ${d.low} to ${d.high}` : d.kind === "CHANGE" ? "" : d.kind === "TEXT" ? `${d.mode === "contains" ? "contains " : ""}${d.values.join(" or ")}` : d.kind === "CUSUM" ? `${d.target} (sum over ${d.threshold})` : d.kind === "ANOMALY" ? `${d.z_limit} sd of ${d.window}` : d.limit ?? d.per_second ?? d.seconds;
    const lim = p.detectors.map(d => `${d.kind === "RANGE" ? "" : kinds[d.kind] || d.kind} ${arg(d)}${["NO_UPDATE", "NO_CHANGE"].includes(d.kind) ? " s" : ["HIGH_LIMIT", "LOW_LIMIT", "RATE_OF_CHANGE"].includes(d.kind) && p.unit ? " " + p.unit : ""} (${String(d.level || "WARNING").toLowerCase()})`.trim());
    $("#pd-settings").className = "";
    $("#pd-settings").innerHTML = `<dl class="kv"><dt>Alarms</dt><dd>${lim.length ? lim.map(esc).join("; ") : "none set"}</dd>
      <dt>Engineering range</dt><dd>${p.eu.low == null && p.eu.high == null ? "not set" : `${p.eu.low ?? "any"} to ${p.eu.high ?? "any"}${p.unit ? " " + esc(p.unit) : ""}`}</dd>
      <dt>Decimals shown</dt><dd>${p.decimals ?? "as received"}</dd></dl>
      ${isAdmin() ? `<p><a class="btn" href="#config/point/${q}">Edit settings</a></p>` : ""}`;
    await drawNotes();
    const evs = await api("GET", `/api/events?point=${q}`);
    $("#pd-alarms").className = evs.length ? "" : "status";
    $("#pd-alarms").innerHTML = evs.length ? alarmTableHtml(evs.slice(0, 20), false) : "No alarms recorded for this point.";
  }
  const chart = async () => {
    const box = $("#pd-chart"), win = Number($("#pd-hours").value);
    box._t = 0;
    if (current?.datatype === "text") { box.textContent = "A text point has no trend. Its changes are in History export."; return; }
    await WIDGETS.chart.render(box, {type: "chart", title: "Average, last " + $("#pd-hours").selectedOptions[0].text, points: [name], window: win, rollup: "avg"}, {points: current ? {[name]: current} : {}});
  };
  async function drawNotes() {
    const ns = await api("GET", `/api/notes/${q}`), box = $("#pd-notes");
    $("#pd-note-f").hidden = !isUser();
    box.className = ns.length ? "" : "status";
    box.innerHTML = ns.length ? `<ul class="list">${ns.map(n => `<li><p>${esc(n.text)}</p><p class="note">${esc(n.user)} at ${fmt(n.ts)}</p></li>`).join("")}</ul>`
      : "No notes yet. Anything a shift writes here stays with the point, visible to everyone who can see it.";
  }
  $("#pd-note-f").addEventListener("submit", async ev => {
    ev.preventDefault();
    const out = $("#pd-note-e"), text = ev.target.text.value;
    try { await api("POST", `/api/notes/${q}`, {text}); ev.target.reset(); out.textContent = ""; await drawNotes(); }
    catch (e) { out.textContent = (e.errors || []).map(x => x.message).join(". ") || e.message; }
  });
  bindAlarmActions($("#pd-alarms"), refresh);
  bindSetForms($("#pd-control"));
  $("#pd-hours").addEventListener("change", () => chart().catch(e => showErr(e, $("#pd-chart"))));
  every(refresh, 2000);
  every(chart, 6000);
}

// "Acknowledge all shown": one request for every unacknowledged alarm in `box`, after a confirmation that says how many.
function ackAllHtml(evs) {
  const n = evs.filter(e => e.acked_ts == null).length;
  return n > 1 && isUser() ? `<p><button type="button" data-ack-all="${evs.filter(e => e.acked_ts == null).map(e => e.id).join(",")}">Acknowledge all ${n} shown</button></p>` : "";
}
function bindAckAll(box, refresh) {
  box.addEventListener("click", async ev => {
    const ids = ev.target.dataset.ackAll;
    if (!ids) return;
    const list = ids.split(",").map(Number);
    if (!confirm(`Acknowledge ${list.length} alarms? They are recorded as acknowledged by you.`)) return;
    ev.target.disabled = true;
    try { await api("POST", "/api/events/ack-many", {ids: list}); await refresh(); refreshAlarmCount(); } catch (e) { ev.target.disabled = false; showErr(e, box); }
  });
}

function renderAlarms(evs) {
  const box = $("#alarms"), open = evs.filter(e => e.acked_ts == null).length;
  $("#al-count").textContent = evs.length ? `${open} unacknowledged of ${evs.length}` : "";
  if (!evs.length) { box.className = "status"; box.textContent = "No alarms. One is raised when a point breaks a limit or stops reporting, and it stays here until someone acknowledges it."; return; }
  box.className = "";
  box.innerHTML = ackAllHtml(evs) + alarmTableHtml(evs);
}

function alarmTableHtml(evs, showPoint = true) {
  const STATE = {active_unacked: e => "Active, not acknowledged", active_acked: e => `Active, acknowledged by ${esc(e.acked_by)} at ${fmt(e.acked_ts)}`,
    rtn_unacked: e => `Returned to normal at ${fmt(e.cleared_ts)}, not acknowledged`, closed: e => `Closed: returned to normal at ${fmt(e.cleared_ts)}, acknowledged by ${esc(e.acked_by)}`};
  const HELD = {shelved: "Shelved. ", suppressed: "Suppressed by design. ", maintenance: "In a maintenance window. "};
  const state = e => (e.held && !e.cleared_ts ? HELD[e.held] : "") + STATE[e.state](e);
  const shelving = e => !isUser() || e.cleared_ts != null ? "" : e.held === "shelved"
    ? `<button data-unshelve="${esc(e.point)}" aria-label="Unshelve alarm on ${esc(e.point)}">Unshelve</button>`
    : e.held ? "" : `<button data-shelve="${esc(e.point)}" aria-label="Shelve alarm on ${esc(e.point)}">Shelve</button>`;
  return `<table class="stack"><thead><tr><th>Level</th>${showPoint ? "<th>Point</th>" : ""}<th>Detail</th><th>Raised</th><th>State</th><th><span class="sr">Action</span></th></tr></thead><tbody>` +
    evs.map(e => `<tr class="${e.acked_ts == null ? "open" : "done"}"><td class="lvl" data-label="Level">${esc(e.level_name)}</td>${showPoint ? `<td data-label="Point"><a href="#point/${encodeURIComponent(e.point)}">${esc(e.point)}</a></td>` : ""}
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
  pts = [...pts].sort((a, b) => (a.folder || "\uffff").localeCompare(b.folder || "\uffff") || a.name.localeCompare(b.name));  // foldered points first, in folder order
  const sig = pts.map(p => p.name + (p.settable ? "*" : "") + "|" + (p.folder || "")).join();
  if (sig !== ptSig) {  // rebuild only when the list changes, so typing in a set field is never interrupted
    ptSig = sig;
    const folders = pts.some(p => p.folder);
    box.innerHTML = pts.map((p, i) => (folders && (i === 0 || (pts[i - 1].folder || "") !== (p.folder || "")) ? `<h3 class="fold">${esc((p.folder || "Not in a folder").split("/").join(" / "))}</h3>` : "") + `<div class="pt" data-pt="${esc(p.name)}"><div class="head"><span><a href="#point/${encodeURIComponent(p.name)}">${esc(p.name)}</a> <span class="tags">${esc(Object.entries(p.tags).map(([k, v]) => k + ":" + v).join(" "))}</span>${p.description ? `<small class="note"> ${esc(p.description)}</small>` : ""}</span><span class="val num"></span></div><div class="spark"></div>
      ${setFormHtml(p)}</div>`).join("");
  }
  for (const p of pts) {
    const row = [...box.children].find(r => r.dataset?.pt === p.name), v = row.querySelector(".val");
    v.textContent = p.last == null ? "no value yet" : p.text != null ? `${p.text}${p.quality && p.quality !== "good" ? " (" + p.quality + ")" : ""}` : `${p.decimals == null || typeof p.last === "string" ? p.last : Number(p.last).toFixed(p.decimals)}${p.unit ? " " + p.unit : ""}${p.quality && p.quality !== "good" ? " (" + p.quality + ")" : ""}`;
    v.classList.toggle("over", p.last != null && ((p.limits.high != null && p.last > p.limits.high) || (p.limits.low != null && p.last < p.limits.low)));
    row.querySelector(".spark").innerHTML = spark(p.history, p.limits);
  }
}

// ---- history chart
const ROLL = {avg: "Average", min: "Minimum", max: "Maximum", last: "Last value", first: "First value", delta: "Change (last minus first)", sum: "Sum", count: "Sample count"};
function viewHistory() {
  main.innerHTML = `<section aria-labelledby="h-h"><h2 id="h-h">History</h2><form id="hf" class="row"><label>Point <select id="h-point"></select></label>
    <label>Window <select id="h-win"><option value="300">5 minutes</option><option value="3600" selected>1 hour</option><option value="86400">24 hours</option></select></label>
    <label>Rollup <select id="h-roll">${Object.entries(ROLL).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
    <label>Compare with (up to 5) <select id="h-cmp" multiple size="3"></select></label></form>
    <div id="hist" class="status" data-live>Loading history...</div></section>`;
  let known = [];
  $("#hf").addEventListener("change", () => draw(known).catch(e => showErr(e)));
  every(async () => {
    const pts = await api("GET", "/api/points");
    if (pts.map(p => p.name).join() !== known.map(p => p.name).join()) {
      const cur = $("#h-point").value;
      $("#h-point").innerHTML = pts.map(p => `<option>${esc(p.name)}</option>`).join("");
      if (pts.some(p => p.name === cur)) $("#h-point").value = cur;
      const picked = new Set([...$("#h-cmp").selectedOptions].map(o => o.value));
      $("#h-cmp").innerHTML = pts.map(p => `<option ${picked.has(p.name) ? "selected" : ""}>${esc(p.name)}</option>`).join("");
    }
    known = pts;
    await draw(pts);
  }, 5000);
}

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

// ---- alarm journal: every alarm ever raised that you may see, filtered and paged, with a CSV export
function viewAlarms() {
  main.innerHTML = `<section aria-labelledby="aj-h"><h2 id="aj-h">Alarms</h2>
    <form id="aj" class="row">
      <label>Show <select name="state"><option value="all">All alarms</option><option value="unacked">Not acknowledged</option><option value="active">Still active</option><option value="closed">Closed (returned to normal and acknowledged)</option></select></label>
      <label>From level <select name="level"><option value="">Any</option>${["INFORMATION", "IMPORTANT", "WARNING", "URGENT", "CRITICAL", "LIFE_SAFETY"].map(l => `<option>${l}</option>`).join("")}</select></label>
      <label>Raised in <select name="hours"><option value="24">the last 24 hours</option><option value="168">the last 7 days</option><option value="720">the last 30 days</option><option value="">any time</option></select></label>
      <label>Search <input name="q" type="search" placeholder="point or message" autocomplete="off"></label>
      <a id="aj-csv" class="btn" href="#alarms" download>Download CSV</a></form>
    <div id="aj-out" class="status" data-live>Loading alarms...</div>
    <div class="row" id="aj-pager" hidden><button type="button" id="aj-newer">Newer</button><button type="button" id="aj-older">Older</button><span id="aj-range" class="note" role="status"></span></div></section>`;
  const PAGE = 50;
  let offset = 0;
  const params = () => { const f = new FormData($("#aj")), q = new URLSearchParams({limit: PAGE, offset}); for (const [k, v] of f) if (v) q.set(k, v); return q; };
  const load = async () => {
    const q = params(), r = await api("GET", "/api/events/search?" + q);
    q.delete("limit"); q.delete("offset"); q.set("format", "csv");
    $("#aj-csv").href = "/api/events/search?" + q;
    const box = $("#aj-out");
    box.className = r.items.length ? "" : "status";
    box.innerHTML = r.items.length ? ackAllHtml(r.items) + alarmTableHtml(r.items) : "No alarms match. Widen the time range or clear the search.";
    $("#aj-pager").hidden = r.total <= PAGE;
    $("#aj-newer").disabled = offset === 0;
    $("#aj-older").disabled = offset + PAGE >= r.total;
    $("#aj-range").textContent = r.total ? `${offset + 1} to ${Math.min(offset + PAGE, r.total)} of ${r.total}` : "";
    if (r.items.length) makeSortable(box.querySelector("table"));  // (the button above the table is outside it)
  };
  const reload = () => load().catch(e => showErr(e, $("#aj-out")));
  $("#aj").addEventListener("change", () => { offset = 0; reload(); });
  $("#aj").addEventListener("input", e => { if (e.target.name === "q") { offset = 0; clearTimeout(viewAlarms.t); viewAlarms.t = setTimeout(reload, 300); } });
  $("#aj").addEventListener("submit", e => e.preventDefault());
  $("#aj-newer").addEventListener("click", () => { offset = Math.max(0, offset - PAGE); reload(); });
  $("#aj-older").addEventListener("click", () => { offset += PAGE; reload(); });
  bindAlarmActions($("#aj-out"), load);
  bindAckAll($("#aj-out"), load);
  reload();  // no timer: a refresh would undo the sort you chose
}

// ---- reports: one row per point for a window
function viewReports() {
  main.innerHTML = `<section aria-labelledby="r-h"><h2 id="r-h">Reports</h2><form id="rf" class="row">
    <label>Watch list <select id="r-wl"><option value="">All points</option></select></label>
    <label>Tag <input id="r-tag" placeholder="area:boiler" autocomplete="off"></label>
    <label>Window <select id="r-hours"><option value="1">1 hour</option><option value="8">8 hours</option><option value="24" selected>24 hours</option><option value="168">7 days</option><option value="720">30 days</option></select></label>
    <button type="submit">Show report</button> <a id="r-csv" class="btn" href="#reports" download>Download CSV</a></form>
    <div id="rep" class="status" data-live>Choose a window and show the report.</div></section>`;
  api("GET", "/api/watchlists").then(w => { $("#r-wl").insertAdjacentHTML("beforeend", w.map(x => `<option value="${esc(x.name)}">${esc(x.name)}${x.mine ? " (mine)" : ""}</option>`).join("")); }).catch(() => {});
  const qs = () => { const q = new URLSearchParams({hours: $("#r-hours").value}); if ($("#r-wl").value) q.set("watchlist", $("#r-wl").value); if ($("#r-tag").value.includes(":")) q.set("tag", $("#r-tag").value.trim()); return q; };
  const show = async () => {
    const box = $("#rep"), q = qs();
    $("#r-csv").href = "/api/reports/summary?" + q + "&format=csv";
    const d = await api("GET", "/api/reports/summary?" + q);
    const f = v => v == null ? "none" : Number(v).toFixed(2).replace(/\.?0+$/, "");
    box.className = d.items.length ? "" : "status";
    box.innerHTML = d.items.length ? `<p class="note">${fmt(d.from)} to ${fmt(d.to)}. Bad samples are counted but left out of every figure.</p><table class="stack"><thead><tr><th>Point</th><th>Samples</th><th>Min</th><th>Max</th><th>Average</th><th>First</th><th>Last</th><th>Bad samples</th><th>Alarms</th></tr></thead><tbody>` +
      d.items.map(i => `<tr><td data-label="Point"><a href="#point/${encodeURIComponent(i.point)}">${esc(i.point)}</a>${i.unit ? ` <small class="note">${esc(i.unit)}</small>` : ""}</td>${["samples", "min", "max", "avg", "first", "last", "bad_samples", "alarms"].map(k => `<td class="num" data-label="${k}">${k === "samples" || k === "bad_samples" || k === "alarms" ? i[k] : f(i[k])}</td>`).join("")}</tr>`).join("") + "</tbody></table>"
      : "No points match, or you can see none.";
    box.querySelector("table") && makeSortable(box.querySelector("table"));
  };
  $("#rf").addEventListener("submit", ev => { ev.preventDefault(); show().catch(e => showErr(e, $("#rep"))); });
}

async function draw(pts) {
  const box = $("#hist"), point = $("#h-point").value;
  if (!point) { box.className = "status"; box.textContent = "No points yet. They appear here as soon as a data source reports a value."; return; }
  const win = $("#h-win").value, roll = $("#h-roll").value, bucket = Math.max(1, Math.floor(win / 120));
  const more = [...$("#h-cmp").selectedOptions].map(o => o.value).filter(n => n !== point).slice(0, 5);
  if (more.length) {  // several points on one chart: each line has its own dash pattern and a legend, so no colour is needed to tell them apart
    box._t = 0;
    box.className = "";
    return WIDGETS.chart.render(box, {type: "chart", title: `${ROLL[roll]}, last ${$("#h-win").selectedOptions[0].text}`, points: [point, ...more], window: Number(win), rollup: roll}, {points: Object.fromEntries(pts.map(x => [x.name, x]))});
  }
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

// ---- sortable tables: click a column heading to sort by it, again to reverse. Numbers sort as numbers, everything else as text.
function makeSortable(table) {
  const heads = [...table.tHead.rows[0].cells];
  const val = (row, n) => { const t = row.cells[n].textContent.trim(), m = /^-?\d+(?:\.\d+)?(?:\s*\S{1,6})?$/.test(t) ? parseFloat(t) : NaN; return Number.isNaN(m) ? t.toLowerCase() : m; };  // "83.3 C" is a number, "area:boiler" is text
  heads.forEach((th, n) => {
    if (!th.textContent.trim()) return;
    th.setAttribute("aria-sort", "none");
    th.innerHTML = `<button type="button" class="link sort">${th.innerHTML}</button>`;
    th.firstChild.addEventListener("click", () => {
      const dir = th.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending";
      heads.forEach(h => h.setAttribute("aria-sort", "none"));
      th.setAttribute("aria-sort", dir);
      const body = table.tBodies[0], rows = [...body.rows];
      rows.sort((a, b) => { const x = val(a, n), y = val(b, n); return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, {numeric: true})) * (dir === "ascending" ? 1 : -1); });
      rows.forEach(r => body.append(r));
    });
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

// ---- users and audit
async function viewUsers() {
  main.innerHTML = `<section aria-labelledby="u-h"><h2 id="u-h">Users</h2><div id="ul" class="status" data-live>Loading users...</div>
    <h2>Add or update a user</h2><p class="note">Leave the password empty to keep an existing user's password. Roles: superadmin, user, or your own names used in point read and set roles.</p>
    <form id="uf" class="row"><label>Name <input name="name" autocomplete="off" required pattern="[A-Za-z0-9_.\\-]{1,32}"></label><label>Roles (comma separated) <input name="roles" value="user" autocomplete="off" required></label>
    <label>Password (10+ characters) <input name="password" type="password" autocomplete="new-password"></label><button type="submit">Save user</button></form><p id="um" role="status"></p></section>`;
  const load = async () => {
    const us = await api("GET", "/api/users");
    $("#ul").className = "";
    $("#ul").innerHTML = `<table class="stack"><thead><tr><th>Name</th><th>Roles</th><th><span class="sr">Action</span></th></tr></thead><tbody>${us.map(u => `<tr><td data-label="Name">${esc(u.name)}</td><td data-label="Roles">${esc(u.roles.join(", "))}${u.mfa ? " (2FA)" : ""}${u.disabled ? " (disabled)" : ""}</td><td>${u.name !== me.name ? `<button data-rst="${esc(u.name)}">Reset password</button> ` : ""}${u.name !== me.name ? `<button data-dis="${esc(u.name)}" data-roles="${esc(u.roles.join(","))}" data-off="${u.disabled ? "1" : ""}">${u.disabled ? "Enable" : "Disable"}</button> ` : ""}${u.mfa && u.name !== me.name ? `<button data-mfa="${esc(u.name)}" data-roles="${esc(u.roles.join(","))}" aria-label="Reset two-factor for ${esc(u.name)}">Reset 2FA</button> ` : ""}${u.name === me.name ? "" : `<button data-del="${esc(u.name)}" aria-label="Delete user ${esc(u.name)}">Delete</button>`}</td></tr>`).join("")}</tbody></table>`;
  };
  $("#ul").addEventListener("click", async ev => {
    const r = ev.target.dataset.rst;
    if (r && confirm(`Make a one-time reset code for "${r}"? Give it to them in person or by phone; it works once for an hour.`)) {
      try { const x = await api("POST", `/api/users/${encodeURIComponent(r)}/reset`, {}); $("#um").className = ""; $("#um").textContent = `Reset code for ${r}: ${x.code} (they enter it under "Forgot your password?" on the sign-in page)`; }
      catch (e) { $("#um").className = "err"; $("#um").textContent = e.message; }
    }
    const g = ev.target.dataset.dis;
    if (g) {
      const off = ev.target.dataset.off === "1";
      if (confirm(`${off ? "Enable" : "Disable"} the account "${g}"?${off ? "" : " It is signed out at once and its API tokens stop working."}`)) {
        try { await api("PUT", `/api/users/${encodeURIComponent(g)}`, {roles: ev.target.dataset.roles.split(","), disabled: !off}); await load(); }
        catch (e) { $("#um").className = "err"; $("#um").textContent = e.message; }
      }
    }
    const m = ev.target.dataset.mfa;
    if (m && confirm(`Turn off two-factor sign-in for "${m}"?`)) { try { await api("PUT", `/api/users/${encodeURIComponent(m)}`, {roles: ev.target.dataset.roles.split(","), reset_mfa: true}); await load(); } catch (e) { $("#um").className = "err"; $("#um").textContent = e.message; } }
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

// ---- an unattended console logs itself out: the page polls constantly, so only real input counts as activity
const IDLE_MS = 30 * 60 * 1000;
let lastInput = Date.now();
for (const t of ["pointerdown", "keydown", "wheel", "touchstart"]) addEventListener(t, () => { lastInput = Date.now(); }, {passive: true});
setInterval(async () => {
  if (!me.name || Date.now() - lastInput < IDLE_MS) return;
  await api("POST", "/api/logout", {}).catch(() => {});
  me = {name: null, roles: ["anonymous"]};
  location.hash = "";
  route();
  const msg = $("#li-msg");
  if (msg) msg.textContent = "Logged out after 30 minutes without activity.";
}, 30000);

setInterval(refreshAlarmCount, 10000);
addEventListener("hashchange", () => { refreshAlarmCount(); });

// ---- start
(async () => {
  try { me = await api("GET", "/api/me"); } catch (e) { main.innerHTML = ""; const p = document.createElement("p"); p.className = "status err"; p.textContent = e.message; main.append(p); return; }
  route();
  refreshAlarmCount();
})();
