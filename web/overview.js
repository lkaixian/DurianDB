"use strict";
// Overview: live alarms and points, the watch list filter, alarm performance and the point list. Uses helpers from app.js and alarms.js.

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
    // A personal save is a static snapshot on purpose; a query personal list is API-only today (PUT /api/mylists with {"query": {...}}).
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
