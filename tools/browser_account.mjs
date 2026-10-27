// Browser check for the two screens that had only a syntax check and unit tests: two-factor sign-in on the account
// page and the scheduled-report handler form. Setup as in browser_forms.mjs (headless Edge on 127.0.0.1:9222, a
// throwaway server). Env: DURIAN_BASE (default http://127.0.0.1:8766), DURIAN_ADMIN_PASSWORD. Expect no problems.
import fs from "node:fs";
import crypto from "node:crypto";
const out = process.argv[2]; fs.mkdirSync(out, {recursive: true});
const base = process.env.DURIAN_BASE ?? "http://127.0.0.1:8766", pw = process.env.DURIAN_ADMIN_PASSWORD;
const targets = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const pend = new Map(); const problems = [];
ws.addEventListener("message", e => { const m = JSON.parse(e.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m.result ?? m.error); pend.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") problems.push("EXC " + (m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text));
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") problems.push("CONSOLE " + m.params.args.map(a => a.value ?? a.description).join(" ")); });
const send = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const ev = async x => (await send("Runtime.evaluate", {expression: x, returnByValue: true, awaitPromise: true})).result?.value;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const shot = async n => fs.writeFileSync(`${out}/${n}.png`, Buffer.from((await send("Page.captureScreenshot", {format: "png"})).data, "base64"));
const need = (ok, msg) => { if (!ok) throw new Error(msg); };
const waitFor = async (expr, ms = 8000) => { const end = Date.now() + ms; while (Date.now() < end) { if (await ev(expr)) return true; await sleep(150); } return false; };
const set = (path, v) => ev(`(()=>{const e=document.querySelector('[data-path="${path}"]');if(!e)return 'missing ${path}';e.value=${JSON.stringify(String(v))};e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'})()`);
// RFC 6238 (SHA-1, 6 digits), the same code an authenticator app shows for the secret the screen displays.
const b32 = s => { let bits = 0, v = 0; const o = [];
  for (const c of s.toUpperCase().replace(/=+$/, "")) { v = (v << 5) | "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567".indexOf(c); bits += 5;
    if (bits >= 8) { o.push((v >>> (bits - 8)) & 255); bits -= 8; } }
  return Buffer.from(o); };
const totp = (secret, t = Date.now() / 1000) => { const b = Buffer.alloc(8); b.writeBigUInt64BE(BigInt(Math.floor(t / 30)));
  const h = crypto.createHmac("sha1", b32(secret)).update(b).digest(), o = h[h.length - 1] & 15;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1e6).padStart(6, "0"); };

try {
  console.log("totp self-check:", totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59) === "287082" ? "ok" : "BROKEN");
  need(totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59) === "287082", "the TOTP helper is wrong");
  await send("Page.enable"); await send("Runtime.enable");
  await send("Network.enable"); await send("Network.setCacheDisabled", {cacheDisabled: true});
  await send("Page.navigate", {url: base + "/"}); await sleep(2000);
  await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value=${JSON.stringify(pw)};f.requestSubmit()})()`);
  need(await waitFor(`location.hash === "#overview"`), "sign-in as admin did not reach the overview");

  // 1. the account page starts with two-factor off and offers the setup button
  await ev(`location.hash='#account'`); await sleep(1000);
  need(await waitFor(`!!document.querySelector('#mfa-on')`), "the account page has no 'Set up an authenticator app' button");
  need(!(await ev(`!!document.querySelector('#mfa-off')`)), "two-factor was already on in a fresh database");
  // 2. setup shows the manual key and the otpauth link
  await ev(`document.querySelector('#mfa-on').click()`);
  need(await waitFor(`!!document.querySelector('#mfa-ok input[name=code]')`), "clicking setup showed no 6-digit code box");
  const secret = await ev(`document.querySelector('#mfa code')?.textContent.trim()`);
  need(typeof secret === "string" && /^[A-Z2-7]+=*$/.test(secret), "the setup step showed no base32 key: " + JSON.stringify(secret));
  need(await ev(`(document.querySelector('#mfa a')?.getAttribute('href') || '').startsWith('otpauth://totp/')`), "the setup step showed no otpauth link");
  await shot("0-mfa-setup");
  // 3. a wrong code is refused on screen and the setup step stays open
  const bad = totp(secret) === "000000" ? "000001" : "000000";
  await ev(`(()=>{const f=document.querySelector('#mfa-ok');f.code.value=${JSON.stringify(bad)};f.requestSubmit()})()`);
  need(await waitFor(`/does not match/.test(document.querySelector('#mfa-msg')?.textContent || '')`), "a wrong code was not refused on screen");
  need(await ev(`!!document.querySelector('#mfa-ok')`), "a wrong code closed the setup step");
  // 4. the code computed from the key on screen turns it on; the page shows the on state
  await ev(`(()=>{const f=document.querySelector('#mfa-ok');f.code.value='${totp(secret)}';f.requestSubmit()})()`);
  need(await waitFor(`!!document.querySelector('#mfa-off')`), "a valid code did not switch the page to the on state: " + await ev(`document.querySelector('#mfa-msg')?.textContent`));
  need(/On\./.test(await ev(`document.querySelector('#mfa .note')?.textContent || ''`)), "the on state does not say two-factor is on");
  await shot("1-mfa-on");
  // 5. it survives a reload (the server reports it), so the screen is reading real state, not just its own flag
  await send("Page.reload");
  need(await waitFor(`!!document.querySelector('#mfa-off')`, 10000), "after a reload the account page does not show two-factor as on");
  // 6. turn it back off so the account starts clean, and prove that persisted too
  await ev(`(()=>{const f=document.querySelector('#mfa-off');f.password.value=${JSON.stringify(pw)};f.requestSubmit()})()`);
  need(await waitFor(`!!document.querySelector('#mfa-on')`), "turning two-factor off did not return the setup button: " + await ev(`document.querySelector('#mfa-msg')?.textContent`));
  await shot("2-mfa-off");
  await send("Page.reload");
  need(await waitFor(`!!document.querySelector('#mfa-on')`, 10000) && !(await ev(`!!document.querySelector('#mfa-off')`)), "two-factor came back on after a reload");

  // 7. the report handler form: pick the template, check its fields, save with a bad recipient and run-time
  await ev(`location.hash='#config/handler/_new'`); await sleep(1500);
  need(await waitFor(`!!document.querySelector('#tpl')`), "the new handler form has no 'Start from' template picker");
  await ev(`(()=>{const s=document.querySelector('#tpl');s.value='Scheduled report';s.dispatchEvent(new Event('change',{bubbles:true}))})()`); await sleep(500);
  const paths = await ev(`JSON.stringify([...document.querySelectorAll('[data-path]')].map(e => e.dataset.path))`);
  for (const p of ["to", "from", "smtp.host", "smtp.port", "smtp.security", "hour", "hours", "watchlist"])
    need(JSON.parse(paths).includes(p), "the report form has no field " + p);
  await ev(`document.querySelector('#ed input[name="name"]').value='nightly-report'`);
  need((await set("to", "not-an-address")) === "ok", "could not type into the To field");
  need((await set("hour", 25)) === "ok", "could not type into the hour field");
  await shot("3-report-form");
  await ev(`document.querySelector('#ed').requestSubmit()`);
  need(await waitFor(`/Not saved/.test(document.querySelector('#ed-msg')?.textContent || '')`), "an invalid report was not refused on screen");
  const toErr = await ev(`document.querySelector('[data-err="to"]')?.textContent`);
  const hourErr = await ev(`document.querySelector('[data-err="hour"]')?.textContent`);
  const formMsg = await ev(`document.querySelector('#ed-msg')?.textContent`);
  need(toErr === "must be 1 to 20 email addresses", "the To error was not shown on its field: " + JSON.stringify(toErr));
  need(hourErr === "must be a whole hour from 0 to 23 (server local time)", "the hour error was not shown on its field: " + JSON.stringify(hourErr));
  need(/marked on the fields above/.test(formMsg || ""), "the save message is not the one the form produces: " + JSON.stringify(formMsg));
  need(await ev(`document.querySelector('[data-path="to"]')?.getAttribute('aria-invalid') === 'true'`), "the To field was not marked invalid");
  need(await ev(`document.querySelector('[data-path="hour"]')?.getAttribute('aria-invalid') === 'true'`), "the hour field was not marked invalid");
  need((await ev(`location.hash`)).endsWith("/_new"), "the invalid report handler was saved anyway");
  await shot("4-report-errors");
  // 8. fix the two fields and save for real, then delete it so the handlers list starts clean
  await set("to", "nightly@example.com"); await set("hour", 7);
  await ev(`document.querySelector('#ed').requestSubmit()`);
  need(await waitFor(`location.hash === "#config/handler/nightly-report"`), "the report handler did not save: " + await ev(`document.querySelector('#ed-msg')?.textContent`));
  await ev(`location.hash='#config/handler'`); await sleep(1200);
  need(await waitFor(`[...document.querySelectorAll('table.dense tbody a')].some(a => a.textContent === 'nightly-report')`), "the saved report handler is not in the handlers list");
  await ev(`location.hash='#config/handler/nightly-report'`); await sleep(1200);
  await ev(`window.confirm=()=>true`); await ev(`document.querySelector('#del').click()`);
  need(await waitFor(`location.hash === "#config/handler"`), "delete did not return to the handler list");
  need(await waitFor(`![...document.querySelectorAll('table.dense tbody a')].some(a => a.textContent === 'nightly-report')`), "the report handler is still listed after delete");
  await shot("5-after");
  console.log("2FA setup/confirm/off and the report handler form: ok");
} catch (e) {
  problems.push("FAIL " + e.message);
}
console.log(problems.length ? problems : "no problems");
ws.close();
process.exit(problems.length ? 1 : 0);
