"use strict";
// Reports: one row per point for a chosen window. Uses helpers from app.js.

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
