"use strict";
// Sign-in and the account page: credentials, password reset, two-factor and log out. Uses helpers from app.js.

// ---- six-digit code input: six boxes plus one visually hidden real input named `code`, kept in sync, so forms read it as usual
const codeHtml = label => `<div class="code" role="group" aria-label="${label}">${[1, 2, 3, 4, 5, 6].map(n =>
  `<input class="dg" inputmode="numeric" autocomplete="one-time-code" aria-label="digit ${n} of 6">`).join("")}<input class="sr" name="code" tabindex="-1" aria-hidden="true" autocomplete="off"></div>`;

// Paste (or an autofill that drops the whole code in one box) spreads over the boxes; the sixth digit calls onFull.
function codeBoxes(host, onFull) {
  const boxes = [...host.querySelectorAll(".dg")], real = host.querySelector("[name=code]");
  const sync = () => { real.value = boxes.map(b => b.value).join(""); };
  const fill = (i, text) => {
    const d = text.replace(/\D/g, "");
    if (!d) return;
    if (d.length >= 6) i = 0;
    for (let k = 0; k < d.length && i + k < 6; k++) boxes[i + k].value = d[k];
    sync(); boxes[Math.min(i + d.length, 5)].focus();
    if (/^\d{6}$/.test(real.value)) onFull();
  };
  boxes.forEach((b, i) => {
    b.addEventListener("input", () => { const v = b.value; b.value = ""; if (v) fill(i, v); sync(); });
    b.addEventListener("paste", ev => { ev.preventDefault(); fill(i, ev.clipboardData?.getData("text") ?? ""); });
    b.addEventListener("focus", () => b.select());
    b.addEventListener("keydown", ev => {
      if (ev.key === "Backspace" && !b.value && i > 0) { ev.preventDefault(); boxes[i - 1].value = ""; boxes[i - 1].focus(); sync(); }
      else if (ev.key === "ArrowLeft" && i > 0) { ev.preventDefault(); boxes[i - 1].focus(); }
      else if (ev.key === "ArrowRight" && i < 5) { ev.preventDefault(); boxes[i + 1].focus(); }
    });
  });
  return {clear() { boxes.forEach(b => { b.value = ""; }); sync(); boxes[0].focus(); }, focus() { boxes[0].focus(); }};
}

// ---- login and account
function viewLogin() {
  main.innerHTML = `<section class="login" aria-labelledby="li-h"><div class="card">
    <p class="brand">DurianDB</p><h2 id="li-h">Sign in</h2><p class="note">Plant monitoring and alarm console.</p>
    <form id="login" novalidate><label for="li-user">Username</label><input id="li-user" name="username" autocomplete="username" required>
    <label for="li-pw">Password</label><div class="pwrow"><input id="li-pw" name="password" type="password" autocomplete="current-password" required>
    <button type="button" id="li-show" aria-pressed="false" aria-controls="li-pw">Show</button></div>
    <div id="li-code" class="codeset" hidden><span class="lbl">Authentication code</span>${codeHtml("Authentication code")}</div>
    <p id="li-msg" role="alert" class="err msg"></p>
    <button type="submit" class="primary" id="li-go">Sign in</button></form>
    <div id="li-sso"></div>
    <details class="forgot"><summary>Forgot your password?</summary>
      <p class="note">Ask an administrator for a reset code (Users page, Reset password). The code works once and expires after an hour.</p>
      <form id="reset"><label>Username <input name="username" autocomplete="username" required></label>
      <label>Reset code <input name="code" autocomplete="off" required></label>
      <label>New password (10+ characters) <input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
      <button type="submit">Set new password</button><p id="rs-msg" role="status"></p></form></details></div></section>`;
  const form = $("#login"), msg = $("#li-msg"), codes = codeBoxes($("#li-code"), () => form.requestSubmit());
  let busy = false;
  $("#li-show").addEventListener("click", ev => {
    const pw = $("#li-pw"), show = pw.type === "password";
    pw.type = show ? "text" : "password"; ev.target.textContent = show ? "Hide" : "Show"; ev.target.setAttribute("aria-pressed", show);
  });
  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    if (busy) return;
    const f = new FormData(form), codeOpen = !$("#li-code").hidden;
    if (!f.get("username") || !f.get("password")) { msg.textContent = "Enter your username and password."; return; }
    if (codeOpen && !/^\d{6}$/.test(f.get("code"))) { msg.textContent = "Enter all 6 digits of the code."; codes.focus(); return; }
    busy = true; $("#li-go").disabled = true; msg.textContent = "";
    try { await api("POST", "/api/login", {username: f.get("username"), password: f.get("password"), code: f.get("code") || undefined}); me = await api("GET", "/api/me"); location.hash = "#overview"; route(); }
    catch (e) {
      if (/code required/.test(e.message)) {
        $("#li-code").hidden = false; msg.textContent = "Enter the 6-digit code from your authenticator app."; codes.focus();
      } else {
        msg.textContent = e.message || "Sign-in failed.";  // a 429 lockout too: the server's own sentence says how long
        if (!$("#li-code").hidden) codes.clear();
      }
    } finally { busy = false; const go = $("#li-go"); if (go) go.disabled = false; }
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
  $("#li-user").focus();
}

function viewAbout() {
  main.innerHTML = `<section aria-labelledby="ab-h"><h2 id="ab-h">About DurianDB</h2>
    <p class="note">Plant monitoring and alarm console.</p>
    ${me.version ? "" : `<p class="note">This server is running an older build that does not report its version. Restart it.</p>`}
    <dl class="kv"><dt>Version</dt><dd class="num">${esc(me.version || "unknown")}</dd>
    <dt>Build</dt><dd class="num">${esc(me.build || "unknown")}</dd>
    <dt>Source</dt><dd>${me.repo ? `<a href="${esc(me.repo)}" rel="noopener noreferrer">${esc(me.repo)}</a>` : "unknown"}</dd></dl></section>`;
}

async function copyText(t) {
  try { await navigator.clipboard.writeText(t); return true; } catch { /* no clipboard API or refused: try the old way */ }
  const ta = document.createElement("textarea");
  ta.value = t; ta.className = "sr"; document.body.append(ta); ta.select();
  let ok = false; try { ok = document.execCommand("copy"); } catch { /* ok stays false */ }
  ta.remove(); return ok;
}

function viewAccount() {
  main.innerHTML = `<section class="acct" aria-labelledby="ac-h"><h2 id="ac-h">Account: ${esc(me.name)}</h2>
    <p class="note">Roles: ${esc(me.roles.join(", "))}</p>
    <section class="card" aria-labelledby="pw-h"><h3 id="pw-h">Password</h3>
    <form id="pw" class="stack"><input class="sr" name="username" value="${esc(me.name)}" autocomplete="username" readonly tabindex="-1" aria-hidden="true">
    <label>Current password <input name="old" type="password" autocomplete="current-password" required></label>
    <label>New password (10+ characters) <input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
    <div><button type="submit">Change password</button></div></form><p id="pw-msg" role="status"></p></section>
    <section class="card" id="mfa" aria-labelledby="mfa-h"></section>
    <p><button id="logout" type="button">Log out</button></p></section>`;
  let step = 0, key = null;  // step 0 = not in setup; 1 add the key, 2 confirm a code, 3 done
  const say = t => { const m = $("#mfa-msg"); m.textContent = t; };
  const names = ["Add the key", "Confirm a code", "Done"];
  const stepper = () => `<ol class="steps">${names.map((n, i) => `<li${step === i + 1 ? ' aria-current="step"' : ""} class="${step > i + 1 ? "past" : ""}"><span class="n">${step > i + 1 ? "&#10003;" : i + 1}</span> ${n}</li>`).join("")}</ol>`;
  const body = () => {
    if (step === 1) {
      const grouped = key.secret.replace(/(.{4})(?=.)/g, "$1 ");
      return `${stepper()}<h4 tabindex="-1" id="mfa-step">Step 1: add the key</h4>
        <p>In your authenticator app choose Add account, then Enter a setup key.</p>
        <div class="key"><code id="mfa-key" aria-label="Setup key">${esc(grouped)}</code><button type="button" id="mfa-copy">Copy</button></div>
        <p><a href="${esc(key.uri)}">Open in authenticator app</a></p>
        <p class="actions"><button type="button" class="primary" id="mfa-next">Next</button><button type="button" id="mfa-cancel">Cancel</button></p>`;
    }
    if (step === 2) return `${stepper()}<h4 tabindex="-1" id="mfa-step">Step 2: confirm a code</h4>
        <form id="mfa-ok" novalidate><span class="lbl">Enter the 6-digit code your app shows now.</span>${codeHtml("Confirmation code")}
        <p class="actions"><button type="submit" class="primary">Turn on</button><button type="button" id="mfa-back">Back</button></p></form>`;
    if (step === 3) return `${stepper()}<div id="mfa-done" tabindex="-1"><h4>Step 3: done</h4>
        <p>Two-factor sign-in is on. Signing in now asks for a code from your authenticator app.</p>
        <p class="actions"><button type="button" class="primary" id="mfa-close">Done</button></p></div>`;
    return me.mfa
      ? `<p>Signing in asks for a code from your authenticator app.</p>
         <details class="off"><summary>Turn off two-factor sign-in</summary><form id="mfa-off" class="stack">
         <p class="warn">Turning this off means a password alone is enough to sign in to this account.</p>
         <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
         <div><button type="submit">Turn off</button></div></form></details>`
      : `<p>Adds a code from an authenticator app to signing in, so a stolen password is not enough.</p>
         <p><button id="mfa-on" class="primary" type="button">Set up an authenticator app</button></p>`;
  };
  const go = (n, focusId) => { step = n; render(focusId); };
  const render = focusId => {
    $("#mfa").innerHTML = `<div class="chead"><h3 id="mfa-h">Two-factor sign-in</h3><span id="mfa-chip" class="chip${me.mfa ? " on" : ""}">${me.mfa ? "On" : "Off"}</span></div>${body()}<p id="mfa-msg" role="alert" class="err msg"></p>`;
    let busy = false;
    $("#mfa-on")?.addEventListener("click", async () => {
      try { key = await api("POST", "/api/mfa/setup", {}); go(1, "mfa-step"); } catch (e) { say(e.message); }
    });
    $("#mfa-copy")?.addEventListener("click", async ev => {
      const ok = await copyText(key.secret);
      ev.target.textContent = ok ? "Copied" : "Copy failed";
      toast(ok ? "Setup key copied." : "Could not copy. Select the key and copy it by hand.", !ok);
      setTimeout(() => { if (ev.target.isConnected) ev.target.textContent = "Copy"; }, 2000);
    });
    $("#mfa-next")?.addEventListener("click", () => go(2, "mfa-step"));
    $("#mfa-back")?.addEventListener("click", () => go(1, "mfa-step"));
    $("#mfa-cancel")?.addEventListener("click", () => { key = null; go(0, "mfa-on"); });
    $("#mfa-close")?.addEventListener("click", () => go(0));
    const ok = $("#mfa-ok");
    if (ok) {
      const codes = codeBoxes(ok, () => ok.requestSubmit());
      ok.addEventListener("submit", async ev => {
        ev.preventDefault();
        const code = new FormData(ok).get("code");
        if (busy) return;
        if (!/^\d{6}$/.test(code)) { say("Enter all 6 digits of the code."); codes.focus(); return; }
        busy = true;
        try { await api("POST", "/api/mfa/enable", {code}); me.mfa = true; key = null; go(3, "mfa-done"); }
        catch (e) { say(e.message); codes.clear(); }
        finally { busy = false; }
      });
    }
    $("#mfa-off")?.addEventListener("submit", async ev => {
      ev.preventDefault();
      try { await api("POST", "/api/mfa/disable", {password: new FormData(ev.target).get("password")}); me.mfa = false; toast("Two-factor sign-in is off."); go(0, "mfa-on"); }
      catch (e) { say(e.message); }
    });
    if (focusId) document.getElementById(focusId)?.focus();
    if (step === 2) $("#mfa-ok .dg").focus();  // the code is what the user types next
  };
  render();
  $("#pw").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target), out = $("#pw-msg");
    try { await api("POST", "/api/password", {old: f.get("old"), new: f.get("new")}); out.className = ""; out.textContent = "Password changed."; ev.target.reset(); }
    catch (e) { out.className = "err"; out.textContent = (e.errors || []).map(x => x.message).join(" ") || e.message; }
  });
  $("#logout").addEventListener("click", async () => { await api("POST", "/api/logout", {}).catch(() => {}); me = {name: null, roles: ["anonymous"]}; location.hash = ""; route(); });
}
