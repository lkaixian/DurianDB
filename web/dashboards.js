"use strict";
// Dashboards: declarative widgets on a grid. A widget type is one registerWidget() call here plus one validator in
// durian/dashboards.py. Nothing an author saves is ever run as code, so the strict content policy stays on.
// Uses helpers from app.js ($, esc, fmt, api, main, me, every, showErr, isUser, isAdmin), which loads after this file.

const WIDGETS = {};
function registerWidget(type, def) { WIDGETS[type] = def; }

const TONE_RANK = {normal: 0, warn: 1, alarm: 2};
const OPS = {">": (a, b) => a > b, "<": (a, b) => a < b, ">=": (a, b) => a >= b, "<=": (a, b) => a <= b, "==": (a, b) => a === b};
function toneOf(rules, v) {  // the most severe matching rule wins
  let t = "normal";
  if (v == null) return t;
  for (const r of rules || []) if (OPS[r.op]?.(v, r.value) && TONE_RANK[r.tone] > TONE_RANK[t]) t = r.tone;
  return t;
}
const tagMatch = (p, tag) => { const [k, ...v] = tag.split(":"); return p.tags[k] === v.join(":"); };
const heading = w => `<h3 class="wt">${esc(w.title || w.point || w.type)}</h3>`;
const noAccess = name => `<p class="note">No access to ${esc(name)}, or it does not exist.</p>`;
const QUALITY_NOTE = {bad: "Bad: the source is not supplying this value, showing the last known one", uncertain: "Uncertain: treat this value with care"};
const formatted = (p, w) => p.last == null ? "no value yet" : w.decimals == null ? String(p.last) : Number(p.last).toFixed(w.decimals);

registerWidget("value", {
  needs: ["points"], size: {w: 3, h: 2}, template: {type: "value", title: "Value", point: "boiler-temp", decimals: 1, rules: [{op: ">", value: 90, tone: "alarm"}]},
  render(box, w, ctx) {
    const p = ctx.points[w.point];
    if (!p) { box.innerHTML = heading(w) + noAccess(w.point); return; }
    const seen = p.received || p.last_ts, stale = seen && Date.now() / 1000 - seen > (w.stale_after || 60);
    box.innerHTML = heading(w) + `<p class="big num tone-${toneOf(w.rules, p.last)}">${esc(formatted(p, w))}<span class="u"> ${esc(p.unit)}</span></p>` +
      (QUALITY_NOTE[p.quality] ? `<p class="note">${QUALITY_NOTE[p.quality]}</p>` : "") +
      (stale ? `<p class="note">No update for ${Math.round(Date.now() / 1000 - seen)} s</p>` : "");
  },
});

registerWidget("gauge", {
  needs: ["points"], size: {w: 3, h: 2}, template: {type: "gauge", title: "Gauge", point: "line-pressure", min: 0, max: 10, zones: [{from: 7.5, to: 10, tone: "alarm"}]},
  render(box, w, ctx) {
    const p = ctx.points[w.point];
    if (!p) { box.innerHTML = heading(w) + noAccess(w.point); return; }
    const span = w.max - w.min, x = v => 4 + (Math.min(w.max, Math.max(w.min, v)) - w.min) / span * 192;
    const zones = (w.zones || []).map(z => `<rect class="zone-${z.tone}" x="${x(z.from)}" y="14" width="${x(z.to) - x(z.from)}" height="10"/>`).join("");
    const tone = p.last == null ? "normal" : (w.zones || []).filter(z => p.last >= z.from && p.last <= z.to).reduce((t, z) => TONE_RANK[z.tone] > TONE_RANK[t] ? z.tone : t, "normal");
    box.innerHTML = heading(w) + `<svg viewBox="0 0 200 44" role="img" aria-label="${esc(formatted(p, w))} on a scale from ${w.min} to ${w.max}"><rect class="gtrack" x="4" y="14" width="192" height="10"/>${zones}
      ${p.last == null ? "" : `<line class="gmark" x1="${x(p.last)}" x2="${x(p.last)}" y1="8" y2="30"/>`}<text x="4" y="42">${w.min}</text><text x="196" y="42" text-anchor="end">${w.max}</text></svg>
      <p class="num tone-${tone}">${esc(formatted(p, w))} ${esc(p.unit)}</p>`;
  },
});

const DASHES = ["", "7 4", "2 3", "9 3 2 3", "4 6", "1 4"];  // series differ by line pattern and label, not by colour
registerWidget("chart", {
  needs: ["points"], size: {w: 6, h: 4}, template: {type: "chart", title: "Trend", points: ["boiler-temp"], window: 3600, rollup: "avg"},
  async render(box, w, ctx) {
    if (box._busy || (box._t && Date.now() - box._t < 5000)) return;  // history changes slowly: refetch every 5 s at most
    box._busy = true;
    try {
      const win = w.window || 3600, roll = w.rollup || "avg", bucket = Math.max(1, Math.floor(win / 120));
      const series = await Promise.all(w.points.map(n => api("GET", `/api/series?point=${encodeURIComponent(n)}&window=${win}&rollup=${roll}&bucket=${bucket}`)
        .then(r => ({name: n, items: r.items})).catch(() => ({name: n, items: null}))));
      box._t = Date.now();
      const ok = series.filter(s => s.items && s.items.length >= 2);
      const legend = `<ul class="legend">${series.map((s, i) => `<li><svg viewBox="0 0 24 6" aria-hidden="true"><line x1="0" x2="24" y1="3" y2="3" stroke-dasharray="${DASHES[i % 6]}"/></svg>${esc(s.name)}${s.items ? "" : " (no access)"}</li>`).join("")}</ul>`;
      if (!ok.length) { box.innerHTML = heading(w) + `<p class="note">Not enough history yet.</p>` + legend; return; }
      const lim = (ctx.points[w.points[0]] || {limits: {}}).limits, lims = roll === "count" ? [] : [lim.high, lim.low].filter(v => v != null);
      const all = ok.flatMap(s => s.items.map(i => i.value)).concat(lims);
      let lo = Math.min(...all), hi = Math.max(...all); if (hi === lo) { hi += 1; lo -= 1; }
      const t0 = Math.min(...ok.map(s => s.items[0].ts)), t1 = Math.max(...ok.map(s => s.items[s.items.length - 1].ts));
      const L = 44, R = 60, T = 6, B = 18, W = 600, H = 240, f = v => Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);
      const x = t => L + ((t - t0) / ((t1 - t0) || 1)) * (W - L - R), y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
      box.innerHTML = heading(w) + `<div class="chartbox"><svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc((w.title || "Trend") + ": " + ok.map(s => s.name).join(", "))}">
        <line class="axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/><line class="axis" x1="${L}" x2="${L}" y1="${T}" y2="${H - B}"/>
        <text x="${L - 4}" y="${T + 9}" text-anchor="end">${f(hi)}</text><text x="${L - 4}" y="${H - B}" text-anchor="end">${f(lo)}</text>
        <text x="${L}" y="${H - 4}">${fmt(t0)}</text><text x="${W - R}" y="${H - 4}" text-anchor="end">${fmt(t1)}</text>
        ${lims.map(v => `<line class="lim" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`).join("")}
        ${ok.map(s => { const i = series.indexOf(s), last = s.items[s.items.length - 1];
          return `<path class="line" stroke-dasharray="${DASHES[i % 6]}" d="${s.items.map((p, k) => (k ? "L" : "M") + x(p.ts).toFixed(1) + " " + y(p.value).toFixed(1)).join("")}"/><text x="${W - R + 4}" y="${y(last.value) + 3}">${esc(s.name.slice(0, 9))}</text>`; }).join("")}</svg></div>` + legend;
    } finally { box._busy = false; }
  },
});

registerWidget("alarms", {
  needs: ["points", "events"], size: {w: 6, h: 4}, template: {type: "alarms", title: "Alarms", state: "open", limit: 8},
  render(box, w, ctx) {
    let evs = ctx.events || [];
    if (w.state !== "all") evs = evs.filter(e => e.acked_ts == null);
    const scope = w.points ? new Set(w.points) : w.tag ? new Set(Object.values(ctx.points).filter(p => tagMatch(p, w.tag)).map(p => p.name)) : null;
    if (scope) evs = evs.filter(e => scope.has(e.point));
    evs = evs.slice(0, w.limit || 10);
    box.innerHTML = heading(w) + (evs.length ? `<ul class="rows">${evs.map(e => `<li class="${e.acked_ts == null ? "tone-alarm" : ""}"><b>${esc(e.level_name)}</b> ${esc(e.point)} <span class="num">${esc(e.message)}</span> <span class="note num">${fmt(e.ts)}${e.cleared_ts != null ? ", back to normal" : ""}</span></li>`).join("")}</ul>` : `<p class="note">No ${w.state === "all" ? "" : "open "}alarms.</p>`);
  },
});

registerWidget("table", {
  needs: ["points"], size: {w: 6, h: 3}, template: {type: "table", title: "Points", tag: "area:line"},
  async render(box, w, ctx) {
    let pts;
    if (w.watchlist) {  // a watch list is resolved on the server; cache it briefly
      if (!box._busy && !(box._t && Date.now() - box._t < 2000)) {
        box._busy = true;
        try { box._rows = await api("GET", "/api/points?watchlist=" + encodeURIComponent(w.watchlist)); box._t = Date.now(); } catch { box._rows = null; } finally { box._busy = false; }
      }
      pts = box._rows;
    } else {
      pts = w.points ? w.points.map(n => ctx.points[n]).filter(Boolean) : Object.values(ctx.points).filter(p => tagMatch(p, w.tag));
    }
    box.innerHTML = heading(w) + (pts && pts.length ? `<table class="stack"><thead><tr><th>Point</th><th>Value</th></tr></thead><tbody>${pts.map(p => `<tr><td data-label="Point">${esc(p.name)}</td><td class="num" data-label="Value">${esc(formatted(p, {}))} ${esc(p.unit)}</td></tr>`).join("")}</tbody></table>` : `<p class="note">${pts ? "No points match." : "No access, or the watch list does not exist."}</p>`);
  },
});

registerWidget("setpoint", {
  needs: ["points"], size: {w: 3, h: 2}, template: {type: "setpoint", title: "Setpoint", point: "pump-setpoint"},
  render(box, w, ctx) {
    const p = ctx.points[w.point];
    if (!p) { box.dataset.key = ""; box.innerHTML = heading(w) + noAccess(w.point); return; }
    const key = w.point + "|" + (p.settable && isUser());
    if (box.dataset.key !== key) {  // build the form once so typing is never interrupted by a refresh
      box.dataset.key = key;
      box.innerHTML = heading(w) + `<p class="big num"><span class="v"></span><span class="u"> ${esc(p.unit)}</span></p>` + (p.settable && isUser()
        ? `<form class="inline"><input name="value" type="number" step="any" required placeholder="value" aria-label="New value for ${esc(p.name)}"><button type="submit">Set</button><button type="submit" data-release="1" formnovalidate>Release</button><output role="status"></output></form>`
        : `<p class="note">Read-only for you.</p>`);
      const form = box.querySelector("form");
      form?.addEventListener("submit", async ev => {
        ev.preventDefault();
        const out = form.querySelector("output");
        try { await api("POST", `/api/points/${encodeURIComponent(w.point)}/set`, {value: ev.submitter?.dataset.release ? null : Number(form.value.value)}); out.textContent = "sent"; }
        catch (e) { out.textContent = e.message; }
      });
    }
    box.querySelector(".v").textContent = formatted(p, w);
  },
});

registerWidget("text", {
  needs: [], size: {w: 4, h: 2}, template: {type: "text", title: "Note", text: "Write plain text here."},
  render(box, w) { box.innerHTML = heading(w) + `<p class="plain"></p>`; box.querySelector(".plain").textContent = w.text; },  // textContent: never parsed as HTML
});

registerWidget("status", {
  needs: ["sources"], size: {w: 3, h: 2}, template: {type: "status", title: "Sources"},
  render(box, w, ctx) {
    const srcs = (ctx.sources || []).filter(s => !w.source || s.name === w.source);
    box.innerHTML = heading(w) + (srcs.length ? `<ul class="rows">${srcs.map(s => `<li class="${s.error || s.connected === false ? "tone-alarm" : ""}">${esc(s.name)}: ${s.error ? esc(s.error) : s.connected === false ? "not connected" : "ok"}</li>`).join("")}</ul>` : `<p class="note">No sources you can see.</p>`);
  },
});

// ---- running a dashboard
async function loadData(d) {
  const need = new Set(d.widgets.flatMap(w => WIDGETS[w.type]?.needs ?? []));
  const out = {points: {}, events: [], sources: []};
  if (need.has("points")) out.points = Object.fromEntries((await api("GET", "/api/points")).map(p => [p.name, p]));
  if (need.has("events")) out.events = await api("GET", "/api/events");
  if (need.has("sources")) out.sources = await api("GET", "/api/sources");
  return out;
}

function paintDashboard(root, d, data) {
  const cols = d.columns || 12, ws = [...d.widgets].sort((a, b) => a.y - b.y || a.x - b.x);
  const sig = cols + JSON.stringify(ws.map(w => [w.id, w.type, w.x, w.y, w.w, w.h]));
  if (root._sig !== sig) {  // the grid is rebuilt only when the layout changes
    root._sig = sig;
    root.innerHTML = "";
    root.classList.add("dgrid");
    root.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;  // set through the style object, which the content policy allows
    for (const w of ws) {
      const el = document.createElement("section");
      el.className = "widget";
      el.dataset.id = w.id;
      el.style.gridColumn = `${w.x + 1} / span ${w.w}`;
      el.style.gridRow = `${w.y + 1} / span ${w.h}`;
      el.style.setProperty("--rows", w.h);
      root.append(el);
    }
  }
  for (const w of ws) {
    const el = [...root.children].find(c => c.dataset.id === w.id), def = WIDGETS[w.type];
    if (!el) continue;
    try { const r = def.render(el, w, data); if (r?.catch) r.catch(e => { el.textContent = e.message; }); }
    catch (e) { el.textContent = `Widget error: ${e.message}`; }
  }
}

async function viewDashboard(name, mode) {
  document.body.classList.toggle("kiosk", mode === "kiosk");
  main.innerHTML = `<section class="dash"><div class="dhead"><h2 id="dt">Loading...</h2><p id="dm" class="note"></p></div><div id="dg" data-live></div></section>`;
  let d;
  try { d = await api("GET", `/api/dashboards/${encodeURIComponent(name)}`); }
  catch (e) { $("#dt").textContent = "Dashboard not available"; $("#dm").textContent = e.status === 404 ? "It does not exist, or you cannot see it." + (me.name ? "" : " Log in to see more.") : e.message; return; }
  $("#dt").textContent = d.title;
  $("#dm").innerHTML = (d.read_roles || []).includes("anonymous") ? "Public. " : "" ;
  if (d.editable) $("#dm").insertAdjacentHTML("beforeend", `<a href="#design/${encodeURIComponent(name)}">Edit this dashboard</a> `);
  if (me.name && mode !== "kiosk") $("#dm").insertAdjacentHTML("beforeend", `<a href="#d/${encodeURIComponent(name)}/kiosk">Full screen</a>`);
  every(async () => paintDashboard($("#dg"), d, await loadData(d)), (d.refresh || 2) * 1000);
}

async function viewDashList() {
  main.innerHTML = `<section aria-labelledby="dl-h"><h2 id="dl-h">Dashboards</h2><div id="dl" class="status" data-live>Loading...</div>
    ${isAdmin() || me.roles.includes("designer") ? `<p><a class="btn" href="#design/_new">New dashboard</a></p>` : `<p class="note">Ask an administrator for the designer role to create dashboards.</p>`}</section>`;
  const ds = await api("GET", "/api/dashboards").catch(e => { showErr(e); return null; });
  if (!ds) return;
  $("#dl").className = ds.length ? "" : "status";
  $("#dl").innerHTML = ds.length ? `<table class="stack"><thead><tr><th>Dashboard</th><th>Owner</th><th>Visibility</th><th><span class="sr">Actions</span></th></tr></thead><tbody>${ds.map(d => `<tr><td data-label="Dashboard"><a href="#d/${encodeURIComponent(d.name)}">${esc(d.title)}</a></td><td data-label="Owner">${esc(d.owner || "none")}</td><td data-label="Visibility">${d.public ? "Public" : "Signed in"}</td><td>${d.editable ? `<a class="btn" href="#design/${encodeURIComponent(d.name)}" aria-label="Edit ${esc(d.title)}">Edit</a> <button data-del="${esc(d.name)}" aria-label="Delete ${esc(d.title)}">Delete</button>` : ""}</td></tr>`).join("")}</tbody></table>` : "No dashboards you can see yet.";
  $("#dl").addEventListener("click", async ev => {
    const n = ev.target.dataset.del;
    if (n && confirm(`Delete dashboard "${n}"?`)) { try { await api("DELETE", `/api/dashboards/${encodeURIComponent(n)}`); viewDashList(); } catch (e) { showErr(e, $("#dl")); } }
  });
}

// ---- designer
async function viewDesigner(name) {
  const isNew = !name || name === "_new";
  let draft = {title: "", columns: 12, refresh: 2, read_roles: ["user"], edit_roles: [], widgets: []}, data = null;
  if (!isNew) {
    try { const {name: _n, editable, owner, ...rest} = await api("GET", `/api/dashboards/${encodeURIComponent(name)}`); if (!editable) throw Object.assign(new Error("You may view this dashboard but not edit it."), {status: 403}); draft = rest; }
    catch (e) { main.innerHTML = `<p class="status err"></p>`; main.firstChild.textContent = e.message; return; }
  }
  const open = new Set();
  main.innerHTML = `<section aria-labelledby="ds-h"><h2 id="ds-h">Dashboard designer</h2>
    <form id="df" class="row"><label>Name <input name="name" value="${esc(isNew ? "" : name)}" ${isNew ? "" : "readonly"} required pattern="[A-Za-z0-9_.\\-]{1,64}" autocomplete="off"></label>
      <label>Title <input name="title" value="${esc(draft.title)}" required maxlength="80" autocomplete="off"></label>
      <label>Columns <input name="columns" type="number" min="4" max="24" value="${draft.columns ?? 12}"></label>
      <label>Refresh (s) <input name="refresh" type="number" min="1" max="3600" value="${draft.refresh ?? 2}"></label>
      <label>Who can view (roles) <input name="read_roles" value="${esc((draft.read_roles || []).join(", "))}" autocomplete="off"></label>
      <label>Who can edit (roles) <input name="edit_roles" value="${esc((draft.edit_roles || []).join(", "))}" autocomplete="off"></label></form>
    <p class="note">Public means the role <code>anonymous</code>, and only an administrator can set it. Viewing a dashboard never grants access to data: each point keeps its own permissions.</p>
    <h3>Widgets</h3><div class="row"><label>Add <select id="at">${Object.keys(WIDGETS).map(t => `<option>${t}</option>`).join("")}</select></label><button id="aw" type="button">Add widget</button></div>
    <div id="wl"></div>
    <div class="row"><button id="sv" type="button">Save</button><a class="btn" id="ol" href="#d/${encodeURIComponent(name || "")}" ${isNew ? "hidden" : ""}>Open dashboard</a></div><div id="dm" role="status"></div>
    <h3>Preview <small class="note">live data, grid as saved</small></h3><div id="pv" data-live>Loading preview...</div></section>`;
  const csv = s => s.split(",").map(x => x.trim()).filter(Boolean), cols = () => Number($("#df").columns.value) || 12;
  const paintPreview = () => { if (data) paintDashboard($("#pv"), {...draft, columns: cols()}, data); };
  const repaint = async () => { try { data = await loadData(draft); } catch (e) { showErr(e, $("#pv")); return; } paintPreview(); };

  function paintList() {
    $("#wl").innerHTML = draft.widgets.length ? draft.widgets.map((w, i) => { const {id, type, title, x, y, w: ww, h, ...rest} = w;
      return `<details class="we" data-i="${i}" ${open.has(id) ? "open" : ""}><summary>${esc(type)}: ${esc(title || id)} <span class="note num">&nbsp;at ${x},${y} size ${ww} by ${h}</span></summary>
      <div class="row"><label>Id <input data-f="id" value="${esc(id)}"></label><label>Title <input data-f="title" value="${esc(title || "")}"></label>
        ${["x", "y", "w", "h"].map(k => `<label>${k.toUpperCase()} <input data-f="${k}" type="number" min="0" value="${w[k]}"></label>`).join("")}</div>
      <div class="row">${[["left", "Move left"], ["right", "Move right"], ["up", "Move up"], ["down", "Move down"], ["wider", "Wider"], ["narrower", "Narrower"], ["taller", "Taller"], ["shorter", "Shorter"], ["dup", "Duplicate"], ["rm", "Remove"]].map(([a, l]) => `<button type="button" data-act="${a}">${l}</button>`).join("")}</div>
      <label>Settings for this widget (JSON) <textarea data-f="rest" spellcheck="false" rows="6">${esc(JSON.stringify(rest, null, 2))}</textarea></label><p class="err" data-err></p></details>`; }).join("") : `<p class="note">No widgets yet. Pick a type above and add one.</p>`;
  }
  const resolve = el => { const d = el.closest("details"); return d ? Number(d.dataset.i) : -1; };
  const freeY = () => draft.widgets.reduce((m, w) => Math.max(m, w.y + w.h), 0);

  $("#aw").addEventListener("click", () => {
    const type = $("#at").value, def = WIDGETS[type];
    let n = 1; while (draft.widgets.some(w => w.id === type + n)) n++;
    draft.widgets.push({id: type + n, ...def.template, x: 0, y: freeY(), w: Math.min(def.size.w, cols()), h: def.size.h});
    open.add(type + n); paintList(); repaint();
  });
  $("#wl").addEventListener("toggle", ev => { const i = resolve(ev.target); if (i >= 0) { const id = draft.widgets[i].id; ev.target.open ? open.add(id) : open.delete(id); } }, true);
  $("#wl").addEventListener("click", ev => {
    const act = ev.target.dataset.act, i = resolve(ev.target);
    if (!act) return;
    const w = draft.widgets[i], step = {left: ["x", -1], right: ["x", 1], up: ["y", -1], down: ["y", 1], wider: ["w", 1], narrower: ["w", -1], taller: ["h", 1], shorter: ["h", -1]}[act];
    if (step) w[step[0]] = Math.max(step[0] === "w" || step[0] === "h" ? 1 : 0, w[step[0]] + step[1]);
    else if (act === "rm") draft.widgets.splice(i, 1);
    else if (act === "dup") { let n = 2; while (draft.widgets.some(o => o.id === w.id + "-" + n)) n++; draft.widgets.push({...JSON.parse(JSON.stringify(w)), id: w.id + "-" + n, y: freeY()}); }
    paintList(); repaint();
  });
  $("#wl").addEventListener("change", ev => {
    const f = ev.target.dataset.f, i = resolve(ev.target);
    if (!f || i < 0) return;
    const w = draft.widgets[i], err = ev.target.closest("details").querySelector("[data-err]");
    if (f === "rest") {
      let parsed;
      try { parsed = JSON.parse(ev.target.value); if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("must be a JSON object"); } catch (e) { err.textContent = "Not applied: " + e.message; return; }
      err.textContent = "";
      const {id, type, title, x, y, w: ww, h} = w;
      draft.widgets[i] = {id, type, title, x, y, w: ww, h, ...parsed, type: type};
    } else w[f] = ["x", "y", "w", "h"].includes(f) ? Number(ev.target.value) : ev.target.value;
    paintList(); repaint();
  });
  $("#df").addEventListener("change", repaint);

  $("#sv").addEventListener("click", async () => {
    const f = $("#df"), msg = $("#dm"), nm = f.name.value.trim();
    const body = {title: f.title.value, columns: Number(f.columns.value), refresh: Number(f.refresh.value), read_roles: csv(f.read_roles.value), edit_roles: csv(f.edit_roles.value), widgets: draft.widgets};
    try {
      const r = await api("PUT", `/api/dashboards/${encodeURIComponent(nm)}`, body);
      msg.className = "";
      msg.innerHTML = "Saved." + (r.warnings?.length ? `<ul class="errors">${r.warnings.map(x => `<li>Warning: ${esc(x.message)}</li>`).join("")}</ul>` : "");
      $("#ol").href = `#d/${encodeURIComponent(nm)}`; $("#ol").hidden = false;
      if (isNew) history.replaceState(null, "", `#design/${encodeURIComponent(nm)}`);
    } catch (e) {
      msg.className = "err";
      msg.innerHTML = esc(e.status === 422 ? "Not saved. Fix these:" : e.message) + (e.errors ? `<ul class="errors">${e.errors.map(x => `<li><code>${esc(x.property)}</code>: ${esc(x.message)}</li>`).join("")}</ul>` : "");
    }
  });
  paintList();
  every(repaint, 3000);
}
