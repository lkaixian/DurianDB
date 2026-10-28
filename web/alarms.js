"use strict";
// Alarms: the alarm table and its actions, acknowledge/shelve buttons, point set forms, and the alarm journal page. Uses helpers from app.js.

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
    try { await api(...call); toast(d.ack ? "Alarm acknowledged." : d.shelve ? "Alarm shelved." : "Alarm unshelved."); await refresh(); refreshAlarmCount(); } catch (e) { ev.target.disabled = false; toast(e.message, true); }
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
      toast(value === null ? `Released ${name}.` : `Sent ${value} to ${name}.`);
    }
    catch (e) { out.textContent = e.message; toast(e.message, true); }
  });
}

const reasonHtml = p => p.set_limits?.reason_required ? `<input name="reason" required maxlength="200" placeholder="reason" aria-label="Reason for changing ${esc(p.name)}"> ` : "";
function setFormHtml(p) {
  if (p.settable && isUser() && p.states) return `<form data-set="${esc(p.name)}"><label class="inl">New state <select name="value" aria-label="New state for ${esc(p.name)}">${Object.entries(p.states).map(([k, l]) => `<option value="${esc(k)}" ${p.last === Number(k) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>${reasonHtml(p)}<button type="submit">Set</button><button type="submit" data-release="1" formnovalidate>Release</button><output role="status"></output></form>`;
  return p.settable && isUser() ? `<form data-set="${esc(p.name)}"><input name="value" ${p.datatype === "text" ? 'type="text" maxlength="255"' : 'type="number" step="any"'} required placeholder="${p.datatype === "text" ? "" : p.set_limits.min ?? ""}${p.set_limits.min != null || p.set_limits.max != null ? " to " : "value"}${p.set_limits.max ?? ""}" ${p.set_limits.min != null ? `min="${p.set_limits.min}"` : ""} ${p.set_limits.max != null ? `max="${p.set_limits.max}"` : ""} aria-label="New value for ${esc(p.name)}${p.set_limits.max_step != null ? `, at most ${p.set_limits.max_step} away from the present value` : ""}">${reasonHtml(p)}<button type="submit">Set</button><button type="submit" data-release="1" formnovalidate>Release</button><output role="status"></output></form>` : "";
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
    try { await api("POST", "/api/events/ack-many", {ids: list}); toast(`${list.length} alarms acknowledged.`); await refresh(); refreshAlarmCount(); } catch (e) { ev.target.disabled = false; toast(e.message, true); }
  });
}

function renderAlarms(evs) {
  const box = $("#alarms"), open = evs.filter(e => e.acked_ts == null).length;
  $("#al-count").textContent = evs.length ? `${open} unacknowledged of ${evs.length}` : "";
  if (!evs.length) { empty(box, "No alarms. One is raised when a point breaks a limit or stops reporting, and it stays here until someone acknowledges it.", "#alarms", "Open the alarm journal"); return; }
  box.className = "";
  setHtml(box, ackAllHtml(evs) + alarmTableHtml(evs));  // patched in place: a poll keeps focus, hover and scroll
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
    if (r.items.length) { box.className = ""; box.innerHTML = ackAllHtml(r.items) + alarmTableHtml(r.items); }
    else empty(box, "No alarms match. Widen the time range or clear the search.");
    $("#aj-pager").hidden = r.total <= PAGE;
    $("#aj-newer").disabled = offset === 0;
    $("#aj-older").disabled = offset + PAGE >= r.total;
    $("#aj-range").textContent = r.total ? `${offset + 1} to ${Math.min(offset + PAGE, r.total)} of ${r.total}` : "";
    if (r.items.length) makeSortable(box.querySelector("table"));  // (the button above the table is outside it)
  };
  const reload = () => load().catch(e => showErr(e, $("#aj-out")));
  $("#aj").addEventListener("change", () => { offset = 0; reload(); });
  const later = debounce(reload, 300);
  $("#aj").addEventListener("input", e => { if (e.target.name === "q") { offset = 0; later(); } });
  $("#aj").addEventListener("submit", e => e.preventDefault());
  $("#aj-newer").addEventListener("click", () => { offset = Math.max(0, offset - PAGE); reload(); });
  $("#aj-older").addEventListener("click", () => { offset += PAGE; reload(); });
  bindAlarmActions($("#aj-out"), load);
  bindAckAll($("#aj-out"), load);
  reload();  // no timer: a refresh would undo the sort you chose
}
