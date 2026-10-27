"use strict";
// Sign-in and the account page: credentials, password reset, two-factor and log out. Uses helpers from app.js.

// ---- login and account
function viewLogin() {
  main.innerHTML = `<section class="login" aria-labelledby="li-h"><div class="card">
    <p class="brand">DurianDB</p><h2 id="li-h">Sign in</h2><p class="note">Plant monitoring and alarm console.</p>
    <form id="login"><label>Username <input name="username" autocomplete="username" required></label>
    <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
    <label id="li-code" hidden>Authentication code <input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}"></label>
    <button type="submit" class="primary">Sign in</button><p id="li-msg" role="alert" class="err"></p></form>
    <div id="li-sso"></div>
    <details class="forgot"><summary>Forgot your password?</summary>
      <p class="note">Ask an administrator for a reset code (Users page, Reset password). The code works once and expires after an hour.</p>
      <form id="reset"><label>Username <input name="username" autocomplete="username" required></label>
      <label>Reset code <input name="code" autocomplete="off" required></label>
      <label>New password (10+ characters) <input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
      <button type="submit">Set new password</button><p id="rs-msg" role="status"></p></form></details></div></section>`;
  $("#login").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try { me = await api("POST", "/api/login", {username: f.get("username"), password: f.get("password"), code: f.get("code") || undefined}); location.hash = "#overview"; route(); }
    catch (e) {
      $("#li-msg").textContent = e.status === 429 ? "Too many failed attempts. Wait a few minutes." : e.message;
      if (/code required/.test(e.message)) { $("#li-code").hidden = false; $("#li-code input").required = true; $("#li-code input").focus(); }
    }
  });
  $("#reset").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target), out = $("#rs-msg");
    try {
      await api("POST", "/api/reset", {username: f.get("username"), code: f.get("code").trim(), new: f.get("new")});
      out.className = ""; out.textContent = "Password changed. Sign in with the new one.";
      ev.target.reset();
    } catch (e) { out.className = "err"; out.textContent = (e.errors || []).map(x => x.message).join(" ") || e.message; }
  });
  api("GET", "/api/auth/sso").then(s => {  // a link only when an administrator configured single sign-on
    if (s.configured) $("#li-sso").innerHTML = `<p class="note">or</p><a class="btn" href="/api/auth/sso/start">Sign in with ${esc(s.issuer)}</a>`;
  }).catch(() => { /* no SSO status: password sign-in still works */ });
  $("#login input").focus();
}

function viewAbout() {
  main.innerHTML = `<section aria-labelledby="ab-h"><h2 id="ab-h">About DurianDB</h2>
    <p class="note">Plant monitoring and alarm console.</p>
    <dl class="kv"><dt>Version</dt><dd class="num">${esc(me.version)}</dd>
    <dt>Build</dt><dd class="num">${esc(me.build)}</dd>
    <dt>Source</dt><dd><a href="${esc(me.repo)}" rel="noopener noreferrer">${esc(me.repo)}</a></dd></dl></section>`;
}

function viewAccount() {
  main.innerHTML = `<section aria-labelledby="ac-h"><h2 id="ac-h">Account: ${esc(me.name)}</h2>
    <p class="note">Roles: ${esc(me.roles.join(", "))}</p>
    <form id="pw" class="row"><input class="sr" name="username" value="${esc(me.name)}" autocomplete="username" readonly tabindex="-1" aria-hidden="true"><label>Current password <input name="old" type="password" autocomplete="current-password" required></label>
    <label>New password (10+ characters) <input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
    <button type="submit">Change password</button></form><p id="pw-msg" role="status"></p>
    <h3>Two-factor sign-in</h3><div id="mfa"></div>
    <button id="logout" type="button">Log out</button></section>`;
  const mfa = () => {
    const box = $("#mfa");
    box.innerHTML = me.mfa
      ? `<p class="note">On. Signing in asks for a code from your authenticator app.</p><form id="mfa-off" class="row"><label>Password <input name="password" type="password" autocomplete="current-password" required></label><button type="submit">Turn off</button></form><p id="mfa-msg" role="status"></p>`
      : `<button id="mfa-on" type="button">Set up an authenticator app</button><p id="mfa-msg" role="status"></p>`;
    const say = (t, bad) => { $("#mfa-msg").className = bad ? "err" : ""; $("#mfa-msg").textContent = t; };
    $("#mfa-on")?.addEventListener("click", async () => {
      try {
        const r = await api("POST", "/api/mfa/setup", {});
        box.innerHTML = `<p class="note">Add this key to an authenticator app (type it in, or open <a href="${esc(r.uri)}">this link</a> on the phone), then enter the 6-digit code it shows.</p><p><code>${esc(r.secret)}</code></p>
          <form id="mfa-ok" class="row"><label>Code <input name="code" inputmode="numeric" maxlength="6" pattern="[0-9]{6}" autocomplete="one-time-code" required></label><button type="submit">Turn on</button></form><p id="mfa-msg" role="status"></p>`;
        $("#mfa-ok").addEventListener("submit", async ev => {
          ev.preventDefault();
          try { await api("POST", "/api/mfa/enable", {code: new FormData(ev.target).get("code")}); me.mfa = true; mfa(); }
          catch (e) { say(e.message, true); }
        });
      } catch (e) { say(e.message, true); }
    });
    $("#mfa-off")?.addEventListener("submit", async ev => {
      ev.preventDefault();
      try { await api("POST", "/api/mfa/disable", {password: new FormData(ev.target).get("password")}); me.mfa = false; mfa(); }
      catch (e) { say(e.message, true); }
    });
  };
  mfa();
  $("#pw").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target), out = $("#pw-msg");
    try { await api("POST", "/api/password", {old: f.get("old"), new: f.get("new")}); out.className = ""; out.textContent = "Password changed."; ev.target.reset(); }
    catch (e) { out.className = "err"; out.textContent = (e.errors || []).map(x => x.message).join(" ") || e.message; }
  });
  $("#logout").addEventListener("click", async () => { await api("POST", "/api/logout", {}).catch(() => {}); me = {name: null, roles: ["anonymous"]}; location.hash = ""; route(); });
}
