"use strict";
// Users and the audit trail. Uses helpers from app.js.

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
