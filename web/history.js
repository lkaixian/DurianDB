"use strict";
// History and forecast: the stored-history chart, the forecast renderer and the dashboard prediction widget. Uses helpers from app.js.

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

// Stored history with the server's forecast continuing it: a solid line, a dashed forecast and a translucent
// 1.96 sigma band. The dashboard prediction widget and the point page both draw from this answer, so the two
// charts speak the same visual language. The legend names both lines, so identity is never colour alone.
function forecastHtml(r, {showBand = true} = {}) {
  const hist = r.points || [], fc = r.forecast || [], unit = r.unit || "", point = r.point || "";
  const f = v => Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);
  const legend = `<ul class="legend"><li><svg viewBox="0 0 24 6" aria-hidden="true"><line x1="0" x2="24" y1="3" y2="3"/></svg>stored</li>` +
    `<li><svg viewBox="0 0 24 6" aria-hidden="true"><line x1="0" x2="24" y1="3" y2="3" stroke-dasharray="6 4"/></svg>forecast${fc.length ? "" : " (not available)"}</li></ul>`;
  if (!hist.length) return {html: `<p class="note">No history yet for ${esc(point)}.</p>` + legend};
  const lastH = hist[hist.length - 1];
  const future = fc.filter(i => i.ts > lastH.ts);  // a live stored line can outrun a forecast taken when the page opened
  const lastF = future.length ? future[future.length - 1] : null;
  const band = !!lastF && showBand;
  const vals = hist.map(i => i.value).concat(band ? future.flatMap(i => [i.low, i.high]) : []).filter(Number.isFinite);
  let lo = Math.min(...vals), hi = Math.max(...vals); if (hi === lo) { hi += 1; lo -= 1; }
  const W = 600, H = 200, L = 46, R = 92, T = 12, B = 22, t0 = hist[0].ts, t1 = (lastF ? lastF.ts : lastH.ts) || t0 + 1;
  const x = t => L + ((t - t0) / ((t1 - t0) || 1)) * (W - L - R), y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const line = items => items.map((p, k) => (k ? "L" : "M") + x(p.ts).toFixed(1) + " " + y(p.value).toFixed(1)).join("");
  const bandPath = band ? future.map((p, k) => (k ? "L" : "M") + x(p.ts).toFixed(1) + " " + y(p.high).toFixed(1)).join("") +
    future.slice().reverse().map(p => "L" + x(p.ts).toFixed(1) + " " + y(p.low).toFixed(1)).join("") + "Z" : "";
  const fp = lastF ? line([{ts: lastH.ts, value: lastH.value}, ...future]) : "";
  const aria = `${point}: stored last ${f(lastH.value)}${unit ? " " + unit : ""}${lastF ? `, forecast last ${f(lastF.value)}${unit ? " " + unit : ""}` : ", no forecast"}`;
  return {html: `<div class="chartbox"><svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(aria)}">
      <line class="axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/><line class="axis" x1="${L}" x2="${L}" y1="${T}" y2="${H - B}"/>
      <text x="${L - 4}" y="${T + 9}" text-anchor="end">${f(hi)}</text><text x="${L - 4}" y="${H - B}" text-anchor="end">${f(lo)}</text>
      <text x="${L}" y="${H - 4}">${fmt(t0)}</text><text x="${W - R}" y="${H - 4}" text-anchor="end">${fmt(t1)}</text>
      ${bandPath ? `<path class="pred-band" aria-hidden="true" d="${bandPath}"/>` : ""}<path class="line" d="${line(hist)}"/>
      ${fp ? `<path class="line" stroke-dasharray="6 4" d="${fp}"/>` : ""}
      <text x="${lastF ? x(lastH.ts) + 4 : W - R + 4}" y="${y(lastH.value) + 3}">${f(lastH.value)}</text>
      ${lastF ? `<text x="${W - R + 4}" y="${y(lastF.value) + 3}">${f(lastF.value)}</text>` : ""}</svg></div>` +
    (lastF ? "" : `<p class="note">${esc(r.note || "No forecast available.")}</p>`) + legend};
}

// ---- prediction: stored history plus the server's short-horizon forecast, taken from the same answer the point page
// uses (no model runs in the browser). The designer palette and the renderer registry live in dashboards.js.
PALETTE.prediction = "Forecast";
registerWidget("prediction", {
  needs: [], size: {w: 6, h: 4}, template: {type: "prediction", title: "Forecast", point: "boiler-temp", hours: 6, bucket: 60, show_band: true},
  async render(box, w) {
    if (box._busy || (box._t && Date.now() - box._t < 5000)) return;  // history and forecast change slowly: refetch every 5 s at most
    box._busy = true;
    try {
      const hours = w.hours || 6, bucket = w.bucket || 60;
      let r;
      try { r = await api("GET", `/api/points/${encodeURIComponent(w.point)}/forecast?hours=${hours}&bucket=${bucket}`); }
      catch (e) {
        box._t = Date.now();
        box.innerHTML = `<h3 class="wt">${esc(w.point)}</h3>` + (e.status === 404 ? noAccess(w.point) : `<p class="note">${esc(e.message)}</p>`);
        return;
      }
      box._t = Date.now();
      const unit = r.unit || "";
      const hd = `<h3 class="wt">${esc(w.point)}${w.title && w.title !== w.point ? " - " + esc(w.title) : ""}${unit ? ` <span class="u">${esc(unit)}</span>` : ""}</h3>`;
      box.innerHTML = hd + forecastHtml(r, {showBand: w.show_band !== false}).html;
    } finally { box._busy = false; }
  },
});
