"use strict";
// Administrator pages: machine tokens and database health. Uses helpers from app.js ($, esc, api, main, me, every, showErr).

const SCOPE_HELP = {
  ingest: "Push values into one http_in source. Nothing else.",
  read: "Read points, history, alarms and dashboards the owner may see.",
  write: "Everything read does, plus set points the owner may set.",
};
const when = t => (t ? new Date(t * 1000).toLocaleString() : "never");
const bytes = n => (n > 1e9 ? (n / 1e9).toFixed(2) + " GB" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : n > 1e3 ? (n / 1e3).toFixed(0) + " KB" : n + " B");
const errList = (e, head) => esc(e.status === 422 ? head : e.message) + (e.errors ? `<ul class="errors">${e.errors.slice(0, 20).map(x => `<li><code>${esc(x.property)}</code>: ${esc(x.message)}</li>`).join("")}</ul>` : "");

// ---- machine tokens: for Node-RED, scripts and other systems
async function viewTokens() {
  main.innerHTML = `<section aria-labelledby="tk-h"><h2 id="tk-h">Machine tokens</h2>
    <p class="note">A token lets Node-RED, a script or another system talk to DurianDB without a login. It is shown once when created and only its hash is stored. A token never gets administration, whoever owns it.</p>
    <div id="tl" class="status" data-live>Loading tokens...</div>
    <h3>Create a token</h3>
    <form id="tf" class="row"><label>Name <input name="name" required pattern="[A-Za-z0-9_.\\-]{1,64}" autocomplete="off"></label>
      <label>Scope <select name="scope">${Object.keys(SCOPE_HELP).map(k => `<option>${k}</option>`).join("")}</select></label>
      <label>Source (ingest only) <select name="source"><option value="">none</option></select></label>
      <label>Acts as user <select name="owner"></select></label>
      <label>Expires in days (empty: never) <input name="days" type="number" min="1" max="3650"></label>
      <button type="submit">Create token</button></form>
    <p id="scope-help" class="note"></p><div id="tnew" role="status"></div></section>`;
  const f = $("#tf");
  const loaded = await Promise.all([api("GET", "/api/config/source"), api("GET", "/api/users")]).catch(e => { showErr(e); return null; });
  if (!loaded) return;
  const [srcs, users] = loaded;
  f.source.insertAdjacentHTML("beforeend", srcs.filter(s => s.type === "http_in").map(s => `<option>${esc(s.name)}</option>`).join(""));
  f.owner.innerHTML = users.map(u => `<option ${u.name === me.name ? "selected" : ""}>${esc(u.name)}</option>`).join("");
  const help = () => {
    $("#scope-help").textContent = SCOPE_HELP[f.scope.value] + (f.scope.value === "ingest" && f.source.options.length < 2 ? " Create an http_in source under Config first." : "");
    f.source.disabled = f.scope.value !== "ingest";
  };
  f.scope.addEventListener("change", help);
  help();
  const load = async () => {
    const ts = await api("GET", "/api/tokens");
    $("#tl").className = ts.length ? "" : "status";
    $("#tl").innerHTML = ts.length
      ? `<table class="stack"><thead><tr><th>Name</th><th>Scope</th><th>Acts as</th><th>Created</th><th>Last used</th><th>Expires</th><th>State</th><th><span class="sr">Actions</span></th></tr></thead><tbody>${ts.map(t =>
        `<tr class="${t.state === "active" ? "" : "done"}"><td data-label="Name">${esc(t.name)}</td><td data-label="Scope">${esc(t.scope)}${t.source ? " on " + esc(t.source) : ""}</td><td data-label="Acts as">${esc(t.owner)}</td><td class="num" data-label="Created">${when(t.created)}</td><td class="num" data-label="Last used">${when(t.last_used)}</td><td class="num" data-label="Expires">${when(t.expires)}</td><td data-label="State">${esc(t.state)}</td>
        <td><button data-toggle="${esc(t.name)}" data-to="${t.disabled ? 0 : 1}">${t.disabled ? "Enable" : "Disable"}</button> <button data-del="${esc(t.name)}" aria-label="Delete token ${esc(t.name)}">Delete</button></td></tr>`).join("")}</tbody></table>`
      : "No tokens yet. Create one below.";
  };
  $("#tl").addEventListener("click", async ev => {
    const d = ev.target.dataset;
    try {
      if (d.toggle) { await api("PUT", `/api/tokens/${encodeURIComponent(d.toggle)}`, {disabled: d.to === "1"}); await load(); }
      if (d.del && confirm(`Delete token "${d.del}"? Anything using it stops working.`)) { await api("DELETE", `/api/tokens/${encodeURIComponent(d.del)}`); await load(); }
    } catch (e) { showErr(e, $("#tl")); }
  });
  f.addEventListener("submit", async ev => {
    ev.preventDefault();
    const body = {name: f.name.value.trim(), scope: f.scope.value, owner: f.owner.value}, out = $("#tnew");
    if (f.scope.value === "ingest") body.source = f.source.value || undefined;
    if (f.days.value) body.days = Number(f.days.value);
    try {
      const r = await api("POST", "/api/tokens", body);
      out.className = "";
      out.innerHTML = `<p><b>Copy this token now.</b> It is shown once.</p><p><code id="tk-val"></code> <button type="button" id="tk-copy">Copy</button></p><p class="note">Use it as the header <code>Authorization: Bearer &lt;token&gt;</code>.</p>`;
      $("#tk-val").textContent = r.token;
      $("#tk-copy").addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(r.token); $("#tk-copy").textContent = "Copied"; } catch { $("#tk-copy").textContent = "Select the text and copy it by hand"; }
      });
      f.reset(); help(); await load();
    } catch (e) { out.className = "err"; out.innerHTML = errList(e, "Not created. Fix these:"); }
  });
  await load().catch(e => showErr(e));
}

// ---- database health, retention, backups, configuration transfer
async function viewDatabase() {
  main.innerHTML = `<section aria-labelledby="db-h"><h2 id="db-h">Database</h2><div id="db" class="status" data-live>Loading...</div></section>`;
  const draw = async () => {
    const d = await api("GET", "/api/database"), s = d.settings;
    $("#db").className = "";
    $("#db").innerHTML = `<table class="stack"><tbody>
      <tr><td data-label="Where">${esc(d.backend)}: ${esc(d.location)}</td><td data-label="Schema">version ${d.schema_version}</td><td data-label="Size">${bytes(d.size_bytes)}</td></tr>
      <tr><td data-label="History">${d.history.rows.toLocaleString()} samples across ${d.history.points} points</td><td data-label="Oldest">${when(d.history.oldest)}</td><td data-label="Newest">${when(d.history.newest)}</td></tr>
      <tr><td data-label="Waiting to be saved" class="${d.buffered_samples ? "err" : ""}">${d.buffered_samples} samples</td><td data-label="Lost" class="${d.lost_samples ? "err" : ""}">${d.lost_samples} samples</td><td data-label="Events / audit">${d.counts.events} / ${d.counts.audit}</td></tr></tbody></table>
      <h3>Retention and schedule</h3>
      <form id="dbs" class="row"><label>Keep history for (days, 0 forever) <input name="retention_days" type="number" min="0" max="36500" value="${s.retention_days}"></label>
        <label>Purge at hour <input name="purge_hour" type="number" min="0" max="23" value="${s.purge_hour}"></label>
        <label>Backup at hour (-1 off) <input name="backup_hour" type="number" min="-1" max="23" value="${s.backup_hour}"></label>
        <label>Backups kept <input name="backup_keep" type="number" min="1" max="365" value="${s.backup_keep}"></label><button type="submit">Save settings</button></form>
      <p class="note">A point can override the retention with its own <code>retention_days</code> under Config. Last purge: ${d.last_purge ? when(d.last_purge.ts) + ", " + d.last_purge.detail + " rows" : "never"}.</p>
      <div class="row"><button id="purge" type="button">Purge now</button><button id="backup" type="button" ${d.backups_supported ? "" : "disabled"}>Back up now</button></div>
      ${d.backups_supported ? "" : `<p class="note">In-app backups cover SQLite files. For ${esc(d.backend)} use the database's own backup tooling.</p>`}
      <div id="dbm" role="status"></div>
      ${d.backups_supported ? `<h3>Backups</h3>${d.backups.length ? `<ul class="rows">${d.backups.map(b => `<li><a href="/api/database/backups/${encodeURIComponent(b.name)}" download>${esc(b.name)}</a> <span class="note num">${bytes(b.bytes)}, ${when(b.ts)}</span></li>`).join("")}</ul>` : `<p class="note">None yet.</p>`}` : ""}
      <h3>Move configuration between servers</h3>
      <p class="note">Export every source, point, handler, publisher, watch list and dashboard as one file. Importing checks everything first and shows what would change. Nothing is applied until you confirm.</p>
      <div class="row"><a class="btn" href="/api/export" download>Export configuration</a><label>Import file <input id="imp" type="file" accept="application/json,.json"></label>
        <label>Mode <select id="impmode"><option value="merge">Merge: add and update</option><option value="replace">Replace: also remove what is not in the file</option></select></label></div>
      <div id="impout" role="status"></div>`;
    const msg = (t, bad) => { const m = $("#dbm"); m.className = bad ? "err" : ""; m.textContent = t; };
    $("#dbs").addEventListener("submit", async ev => {
      ev.preventDefault();
      const body = Object.fromEntries([...new FormData(ev.target)].map(([k, v]) => [k, Number(v)]));
      try { await api("PUT", "/api/database/settings", body); msg("Settings saved."); } catch (e) { msg((e.errors || []).map(x => `${x.property}: ${x.message}`).join(". ") || e.message, true); }
    });
    $("#purge").addEventListener("click", async () => {
      try { const r = await api("POST", "/api/database/purge", {}); msg(`Purged ${r.deleted} samples${r.complete ? "" : " (stopped early, run it again)"}.`); } catch (e) { msg(e.message, true); }
    });
    $("#backup").addEventListener("click", async () => {
      try { const r = await api("POST", "/api/database/backup", {}); msg(`Backup ${r.name} written (${bytes(r.bytes)}).`); } catch (e) { msg(e.message, true); }
    });
    $("#imp").addEventListener("change", async ev => {
      const out = $("#impout"), file = ev.target.files[0], mode = $("#impmode").value;
      if (!file) return;
      let doc;
      try { doc = JSON.parse(await file.text()); } catch { out.className = "err"; out.textContent = "That file is not valid JSON."; return; }
      try {
        const r = await api("POST", `/api/import?mode=${mode}`, doc);
        const rows = Object.entries(r.summary).filter(([, v]) => Object.values(v).some(Boolean))
          .map(([k, v]) => `<li>${esc(k)}: ${Object.entries(v).filter(([, n]) => n).map(([a, n]) => n + " " + a).join(", ")}</li>`).join("");
        out.className = "";
        out.innerHTML = `<p>Nothing has been changed yet. This is what would happen:</p><ul class="rows">${rows || "<li>Nothing: the file matches this server.</li>"}</ul>${rows ? `<button type="button" id="apply">Apply these changes</button>` : ""}`;
        $("#apply")?.addEventListener("click", async () => {
          try { await api("POST", `/api/import?mode=${mode}&apply=1`, doc); out.textContent = "Imported. The engine was rebuilt."; } catch (e) { out.className = "err"; out.textContent = e.message; }
        });
      } catch (e) { out.className = "err"; out.innerHTML = errList(e, "Not imported. Fix these in the file:"); }
    });
  };
  await draw().catch(e => showErr(e));
}
