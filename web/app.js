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

// ---- small shared helpers
const store = {get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { /* private window: not remembered */ } }};

// A quiet confirmation or failure that does not move the page; read out politely by screen readers (#toasts is aria-live).
function toast(msg, bad) {
  const t = document.createElement("p");
  t.className = bad ? "toast bad" : "toast";
  t.textContent = msg;
  t.addEventListener("click", () => t.remove());
  $("#toasts").append(t);
  setTimeout(() => t.remove(), bad ? 9000 : 4000);
}

// Patch `el` to hold `html` by changing only the nodes that differ, so a poll never resets scroll, focus, a half-typed field or a hover.
function morph(a, b) {
  const x = [...a.childNodes], y = [...b.childNodes];
  y.forEach((n, i) => {
    const o = x[i];
    if (!o) return a.append(n);
    if (o.nodeType !== n.nodeType || o.nodeName !== n.nodeName) return o.replaceWith(n);
    if (n.nodeType !== 1) { if (o.data !== n.data) o.data = n.data; return; }
    if (o.isEqualNode(n)) return;
    for (const at of [...o.attributes]) if (!n.hasAttribute(at.name)) o.removeAttribute(at.name);
    for (const at of n.attributes) if (o.getAttribute(at.name) !== at.value) o.setAttribute(at.name, at.value);
    morph(o, n);
  });
  x.slice(y.length).forEach(o => o.remove());
}
function setHtml(el, html) {
  const t = document.createElement("template");
  t.innerHTML = html;
  morph(el, t.content);
}
// A consistent empty state: one sentence saying what is missing, and where to go next.
function empty(box, text, href, label) {
  box.className = "status empty";
  box.innerHTML = esc(text) + (href ? `<br><a class="btn" href="${esc(href)}">${esc(label)}</a>` : "");
}
const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

// ---- theme: follows the system until the toggle is used, then the choice is remembered
const root = document.documentElement;
const effectiveTheme = () => root.dataset.theme || (matchMedia("(prefers-color-scheme:dark)").matches ? "dark" : "light");
function drawTheme() {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  $("#theme").textContent = next === "dark" ? "Dark" : "Light";
  $("#theme").setAttribute("aria-label", `Switch to the ${next} theme`);
}
if (["light", "dark"].includes(store.get("durian.theme"))) root.dataset.theme = store.get("durian.theme");
$("#theme").addEventListener("click", () => { const t = effectiveTheme() === "dark" ? "light" : "dark"; root.dataset.theme = t; store.set("durian.theme", t); drawTheme(); });
matchMedia("(prefers-color-scheme:dark)").addEventListener("change", drawTheme);
drawTheme();

// ---- navigation: pages grouped by task. The third flag marks a group only administrators see.
const VIEWS = {overview: viewOverview, dashboards: viewDashList, d: viewDashboard, design: viewDesigner, history: viewHistory, reports: viewReports, point: viewPoint, alarms: viewAlarms, config: viewConfig, users: viewUsers, tokens: viewTokens, database: viewDatabase, audit: viewAudit, account: viewAccount, about: viewAbout};
const NAV = [["Monitor", [["overview", "Overview", "Ov"], ["alarms", "Alarms", "Al"], ["dashboards", "Dashboards", "Da"]]],
  ["Analyze", [["history", "History", "Hi"], ["reports", "Reports", "Re"]]],
  ["Configure", [["config", "Config", "Co"], ["users", "Users", "Us"], ["tokens", "Tokens", "To"], ["database", "Database", "Db"], ["audit", "Audit", "Au"]], true]];
const TITLES = {point: "Point", d: "Dashboard", design: "Dashboard designer", account: "Account", about: "About"};
for (const g of NAV) for (const [v, l] of g[1]) TITLES[v] = l;
const ADMIN_ONLY = ["config", "users", "tokens", "database", "audit"];
let navSig = null;
function nav(view) {
  document.querySelector(".shell").classList.toggle("solo", !me.name);  // before sign-in there is nothing to navigate to
  const sig = me.name ? (isAdmin() ? "admin" : "user") : "";
  if (sig !== navSig) {  // rebuilt only when who is signed in changes; a page change just moves aria-current
    navSig = sig;
    $("#nav").innerHTML = sig ? NAV.filter(g => !g[2] || isAdmin()).map(([g, items]) => `<p class="ng">${esc(g)}</p>${items.map(([v, l, ic]) => `<a href="#${v}" data-ic="${ic}" title="${l}">${l}</a>`).join("")}`).join("") +
      `<button type="button" id="collapse" aria-expanded="true" title="Collapse the menu">Collapse menu</button>` : "";
    drawCollapse();
  }
  const cur = {point: "overview", d: "dashboards", design: "dashboards"}[view] || view;
  for (const a of $("#nav").querySelectorAll("a")) a.getAttribute("href") === "#" + cur ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
  const t = TITLES[view] || "";
  $("#ptitle").textContent = me.name ? t : "";
  document.title = (t && me.name ? t + " - " : "") + "DurianDB Console";
  drawChrome();
}
// Sidebar: collapsed to icons (remembered), or an overlay drawer on a narrow screen.
function drawCollapse() {
  const b = $("#collapse"), min = document.body.classList.contains("navmin");
  if (!b) return;
  b.setAttribute("aria-expanded", String(!min));
  b.dataset.ic = min ? "»" : "«";
  b.title = min ? "Expand the menu" : "Collapse the menu";
}
document.body.classList.toggle("navmin", store.get("durian.nav") === "min");
$("#nav").addEventListener("click", ev => {
  if (ev.target.closest("#collapse")) { const min = document.body.classList.toggle("navmin"); store.set("durian.nav", min ? "min" : "max"); drawCollapse(); }
  else if (ev.target.closest("a")) closeDrawer();
});
function closeDrawer(back) {
  if (!document.body.classList.contains("navopen")) return;
  document.body.classList.remove("navopen");
  $("#menu").setAttribute("aria-expanded", "false");
  if (back) $("#menu").focus();
}
$("#menu").addEventListener("click", () => {
  const open = document.body.classList.toggle("navopen");
  $("#menu").setAttribute("aria-expanded", String(open));
  if (open) ($("#nav a[aria-current=page]") || $("#nav a"))?.focus();
});
// The drawer is modal on a phone: a tap outside it or Escape closes it. The account menu closes the same ways.
document.addEventListener("click", ev => {
  if (document.body.classList.contains("navopen") && !ev.target.closest("#nav, #menu")) closeDrawer();
  if (!ev.target.closest("#acct")) $("#acct").open = false;
});
document.addEventListener("keydown", ev => {
  if (ev.key !== "Escape") return;
  if ($("#acct").open) { $("#acct").open = false; $("#acct summary").focus(); }
  closeDrawer(true);
});
$("#acct").addEventListener("click", ev => { if (ev.target.closest(".pop a, .pop button")) $("#acct").open = false; });
$("#logout-menu").addEventListener("click", logout);
async function logout() { await api("POST", "/api/logout", {}).catch(() => {}); me = {name: null, roles: ["anonymous"]}; location.hash = ""; route(); }

// Header pieces that exist only while someone is signed in.
function drawChrome() {
  const on = !!me.name && !document.querySelector(".shell").classList.contains("solo");
  for (const id of ["menu", "cmdk", "acct"]) $("#" + id).hidden = !on;
  $("#acct summary").textContent = me.name || "";
  $("#ver").textContent = me.version ? "Version " + me.version : "";
  drawSound();
  if (!on) { $("#conn").hidden = true; $("#banner").hidden = true; }
}

// ---- connection and health: a calm dot while all is well, a banner that says what is wrong when it is not
async function refreshHealth() {
  if (!me.name) return;
  let label = "Live", msgs = [], bad = false, admin = false;
  try {
    const h = await api("GET", "/api/health"), n = (v, w) => `${v} ${w}${v === 1 ? "" : "s"}`;
    if (h.safe) { label = "Safe mode"; bad = true; msgs.push("Safe mode: nothing is being polled, published or backed up. Fix the configuration, then restart the server without safe mode."); }
    if (h.tick_failures) msgs.push(`Data collection is failing${h.tick_error ? ": " + h.tick_error : ""}.`);
    for (const f of h.faults || []) msgs.push(`Delivery to ${f.name} is failing.`);
    if (h.lost_samples) msgs.push(`${n(h.lost_samples, "sample")} could not be stored and were lost. Check the database disk.`), admin = true;
    if (h.unstored_events || h.events_lost) msgs.push(`${n(h.unstored_events || 0, "alarm event")} waiting to be stored${h.events_lost ? `, ${h.events_lost} lost` : ""}. Check the database.`), admin = true;
    if (h.spool) msgs.push(`The database was unavailable: ${n(h.spool.rows, "sample")} waiting in the spool${h.spool.dropped ? `, ${h.spool.dropped} dropped` : ""}.`), admin = true;
    if (msgs.length && !bad) { label = "Degraded"; bad = true; }
  } catch (e) {
    if (e.status === 401) return;
    if (e.status === 0) { label = "Offline"; bad = true; msgs.push("Cannot reach the DurianDB server. The values shown are stale; trying again every few seconds."); }
  }
  const c = $("#conn"), b = $("#banner");
  c.hidden = document.querySelector(".shell").classList.contains("solo");
  c.className = bad ? "conn bad" : "conn";
  if (c.textContent !== label) c.textContent = label;
  const html = msgs.map(m => `<p>${esc(m)}</p>`).join("") + (admin && isAdmin() ? `<p><a href="#database">Open the database page</a></p>` : "");
  if (b.dataset.h !== html) { b.dataset.h = html; b.innerHTML = html; }  // unchanged text is not re-announced
  b.hidden = !msgs.length || document.body.classList.contains("kiosk");
}
setInterval(refreshHealth, 5000);

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
    a.textContent = n ? `${n} unacknowledged` : "All acknowledged";
    a.setAttribute("aria-label", n ? `${n} alarm${n === 1 ? "" : "s"} not acknowledged` : "No unacknowledged alarms");
  } catch { a.hidden = true; }
}
function paint(view, render) {  // one place for what every page change shares: title, a 150 ms fade, loading skeletons, scroll to top
  nav(view);
  scrollTo(0, 0);
  main.classList.remove("enter"); void main.offsetWidth; main.classList.add("enter");
  render();
  for (const el of main.querySelectorAll("[data-live]")) if (/^Loading/.test(el.textContent)) el.classList.add("skel");
}
function route() {
  stopTimers();
  document.body.classList.remove("kiosk");
  closeDrawer();
  const [view, ...rest] = (location.hash.slice(1) || "overview").split("/");
  if (!me.name && view === "d" && rest[0]) return paint("d", () => viewDashboard(decodeURIComponent(rest[0]), rest[1]));  // public dashboards need no login
  if (!me.name) return paint("", viewLogin);
  const v = VIEWS[view] ? view : "overview";
  if (ADMIN_ONLY.includes(v) && !isAdmin()) { location.hash = "#overview"; return; }
  paint(v, () => VIEWS[v](...rest.map(decodeURIComponent)));
  main.focus({preventScroll: true});
  refreshHealth();
}
window.addEventListener("hashchange", route);

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

// ---- command palette (web/palette.js): the same role-aware page list as the sidebar, plus Account and About
initPalette({api, esc, store, me: () => me, toast, logout, refreshAlarmCount, theme: () => $("#theme").click(),
  pages: () => [...NAV.filter(g => !g[2] || isAdmin()).flatMap(g => g[1].map(([v, l]) => [v, l])), ["account", "Account"], ["about", "About"]]});

// ---- start
(async () => {
  try { me = await api("GET", "/api/me"); } catch (e) { main.innerHTML = ""; const p = document.createElement("p"); p.className = "status err"; p.textContent = e.message; main.append(p); return; }
  route();
  refreshAlarmCount();
})();
