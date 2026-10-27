"use strict";
// One point: its value, trend, alarms, notes, settings and set-value controls. Uses helpers from app.js and alarms.js.

// ---- one point: what it is doing now, how it got here, what is wrong with it, and what an operator can do about it
let pointViewSeq = 0;  // a forecast answer for a point you have left must not draw on the next point's page
async function viewPoint(name) {
  const q = encodeURIComponent(name), mine = ++pointViewSeq;
  let forecastOn = true;
  try { forecastOn = localStorage.getItem("durian.pointForecast") !== "0"; } catch { /* private window: stays on */ }
  main.innerHTML = `<p class="crumb"><a href="#overview">Overview</a> &rsaquo; Point</p>
    <h2 id="pd-h">${esc(name)}</h2>
    <div id="pd-top" class="status" data-live>Loading point...</div>
    <div class="pd-grid">
      <section aria-labelledby="pd-t"><h3 id="pd-t">Trend</h3>
        <form class="row" id="pd-win"><label>Window <select id="pd-hours"><option value="3600">1 hour</option><option value="21600">6 hours</option><option value="86400" selected>24 hours</option><option value="604800">7 days</option></select></label><button type="button" id="pd-fc" aria-pressed="${forecastOn}">Forecast: ${forecastOn ? "on" : "off"}</button></form>
        <p class="note" id="pd-drift" hidden></p>
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
  let fcData = null, fcFor = "", fcSeq = 0;  // kept for the life of this page: the forecast is fetched on open, never polled
  const chart = async (fresh = false) => {
    const box = $("#pd-chart"), win = Number($("#pd-hours").value), bucket = Math.max(1, Math.floor(win / 120));
    box._t = 0;
    if (current?.datatype === "text") { box.textContent = "A text point has no trend. Its changes are in History export."; return; }
    if (!forecastOn) {  // stored history alone, as before: one series request on the live cadence
      fcData = null; fcSeq++;  // a forecast already on its way must not draw after the operator turned it off
      await WIDGETS.chart.render(box, {type: "chart", title: "Average, last " + $("#pd-hours").selectedOptions[0].text, points: [name], window: win, rollup: "avg"}, {points: current ? {[name]: current} : {}});
      return;
    }
    if (fresh || !fcData || fcFor !== String(win)) {  // opening the page, changing the window, or turning the forecast back on
      const seq = ++fcSeq;
      try {
        const r = await api("GET", `/api/points/${q}/forecast?hours=${win / 3600}&bucket=${bucket}`);
        if (mine !== pointViewSeq || seq !== fcSeq) return;  // you left this point, or asked again
        fcData = r; fcFor = String(win);
      } catch (e) {
        if (mine !== pointViewSeq || seq !== fcSeq) return;
        fcData = null; fcFor = "";  // try again on the next live tick
        showErr(e, box); return;
      }
    } else {  // the stored line still follows the live cadence; the forecast itself does not
      const key = String(win);
      try {
        const s = await api("GET", `/api/series?point=${q}&window=${win}&rollup=avg&bucket=${bucket}`);
        if (mine !== pointViewSeq || key !== fcFor) return;
        fcData = {...fcData, points: s.items};
      } catch { /* the stored rows from the forecast answer still draw */ }
    }
    if (mine !== pointViewSeq) return;
    box.className = "";
    box.innerHTML = `<h3 class="wt">Average, last ${esc($("#pd-hours").selectedOptions[0].text)}</h3>` + forecastHtml(fcData).html;
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
  $("#pd-hours").addEventListener("change", () => chart(true).catch(e => showErr(e, $("#pd-chart"))));
  $("#pd-fc").addEventListener("click", () => {
    forecastOn = !forecastOn;
    try { localStorage.setItem("durian.pointForecast", forecastOn ? "1" : "0"); } catch { /* not saved */ }
    $("#pd-fc").setAttribute("aria-pressed", String(forecastOn));
    $("#pd-fc").textContent = "Forecast: " + (forecastOn ? "on" : "off");
    chart(true).catch(e => showErr(e, $("#pd-chart")));
  });
  // One quiet line, fetched once: whether this point's own baseline moved since the preceding day.
  const driftLine = async () => {
    try {
      const r = await api("GET", `/api/points/${q}/drift?hours=24&compare=24`);
      if (mine !== pointViewSeq) return;
      const note = r.note ? " " + r.note.charAt(0).toUpperCase() + r.note.slice(1) : "";
      const box = $("#pd-drift");
      box.hidden = false;
      box.textContent = r.verdict === "unknown" ? `Baseline: ${r.note.charAt(0).toUpperCase() + r.note.slice(1)}`
        : `Baseline ${r.verdict === "steady" ? "steady" : `drifting ${r.direction}${r.z == null ? "" : ` ${r.z.toFixed(1)}σ`}`} over the last ${r.hours} h.${note}`;
    } catch { /* the trend and the forecast still stand without this line */ }
  };
  driftLine();
  every(refresh, 2000);
  every(chart, 6000);
}
