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
const VIEWS = {overview: viewOverview, dashboards: viewDashList, d: viewDashboard, design: viewDesigner, history: viewHistory, reports: viewReports, point: viewPoint, alarms: viewAlarms, config: viewConfig, users: viewUsers, tokens: viewTokens, database: viewDatabase, audit: viewAudit, account: viewAccount, about: viewAbout};
function nav(view) {
  const groups = me.name ? [["Monitor", [["overview", "Overview"], ["alarms", "Alarms"], ["dashboards", "Dashboards"], ["history", "History"], ["reports", "Reports"]]],
    ...(isAdmin() ? [["Configure", [["config", "Config"], ["users", "Users"], ["tokens", "Tokens"]]], ["System", [["database", "Database"], ["audit", "Audit"]]]] : []),
    ["Signed in", [["account", me.name], ["about", "About"]]]] : [];
  document.querySelector(".shell").classList.toggle("solo", !me.name);  // before sign-in there is nothing to navigate to
  const cur = v => v === view || (view === "point" && v === "overview") || (view === "d" && v === "dashboards") || (view === "design" && v === "dashboards") ? 'aria-current="page"' : "";
  $("#nav").innerHTML = groups.map(([g, items]) => `<p class="ng">${esc(g)}</p>${items.map(([v, l]) => `<a href="#${v}" ${cur(v)}>${esc(l)}</a>`).join("")}`).join("");
  if (me.name) $("#nav").insertAdjacentHTML("beforeend", `<a class="navver" href="#about" title="About DurianDB">v${esc(me.version)}</a>`);  // pinned to the foot of the sidebar
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

// ---- start
(async () => {
  try { me = await api("GET", "/api/me"); } catch (e) { main.innerHTML = ""; const p = document.createElement("p"); p.className = "status err"; p.textContent = e.message; main.append(p); return; }
  route();
  refreshAlarmCount();
})();
