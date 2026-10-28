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
  // 2. setup step 1 shows the key in 4-character blocks, a copy button and the otpauth link (grouped key: spaces are stripped here)
  await ev(`document.querySelector('#mfa-on').click()`);
  need(await waitFor(`!!document.querySelector('#mfa-key')`), "clicking setup showed no key");
  const secret = (await ev(`document.querySelector('#mfa-key')?.textContent`) ?? "").replace(/\s+/g, "");
  need(/^[A-Z2-7]+=*$/.test(secret), "the setup step showed no base32 key: " + JSON.stringify(secret));
  need(await ev(`/^[A-Z2-7]{4}( [A-Z2-7]{1,4})+$/.test(document.querySelector('#mfa-key').textContent)`), "the key is not grouped in 4-character blocks");
  need(await ev(`!!document.querySelector('#mfa-copy')`), "the key has no Copy button");
  need(await ev(`document.querySelector('#mfa-chip')?.textContent === 'Off'`), "the status chip does not say Off");
  need(await ev(`(document.querySelector('#mfa a')?.getAttribute('href') || '').startsWith('otpauth://totp/')`), "the setup step showed no otpauth link");
  need(await ev(`document.querySelector('.steps [aria-current=step]')?.textContent.includes('Add the key')`), "step 1 is not marked current");
  await ev(`navigator.clipboard.writeText=()=>Promise.resolve()`);  // headless may refuse the real clipboard; the button's own feedback is what is checked
  await ev(`document.querySelector('#mfa-copy').click()`);
  need(await waitFor(`document.querySelector('#mfa-copy')?.textContent === 'Copied'`), "Copy did not change to Copied");
  need(await ev(`!!document.querySelector('#toasts')?.textContent.includes('copied')`), "Copy was not announced in the live region");
  await shot("0-mfa-setup");
  await ev(`document.querySelector('#mfa-next').click()`);
  need(await waitFor(`document.querySelectorAll('#mfa-ok .dg').length === 6`), "step 2 has no six code boxes");
  need(await ev(`document.activeElement === document.querySelector('#mfa-ok .dg')`), "step 2 did not focus the first digit box");
  need(await ev(`[...document.querySelectorAll('#mfa-ok .dg')].every((b,i) => b.getAttribute('aria-label') === 'digit '+(i+1)+' of 6')`), "digit boxes lack their aria-label");
  // 3. a wrong code pasted into the boxes fills all six, auto-submits, is refused on screen and the step stays open
  const paste = (sel, text) => ev(`(()=>{const dt=new DataTransfer();dt.setData('text','${text}');document.querySelector('${sel}').dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}))})()`);
  const bad = totp(secret) === "000000" ? "000001" : "000000";
  await ev(`window.__sync=[];document.querySelector('#mfa-ok [name=code]').closest('form').addEventListener('submit',()=>window.__sync.push(document.querySelector('#mfa-ok [name=code]').value),true)`);
  await paste("#mfa-ok .dg", bad);
  need(await waitFor(`window.__sync.length === 1`), "pasting six digits did not auto-submit");
  need(await ev(`window.__sync[0] === '${bad}'`), "paste did not fill all six boxes: " + await ev(`window.__sync[0]`));
  need(await waitFor(`/does not match/.test(document.querySelector('#mfa-msg')?.textContent || '')`), "a wrong pasted code was not refused on screen");
  need(await ev(`!!document.querySelector('#mfa-ok') && [...document.querySelectorAll('#mfa-ok .dg')].every(b=>b.value==='')`), "a wrong code closed the setup step or left digits behind");
  // typing moves forward; backspace on an empty box moves back and clears
  await ev(`(()=>{const b=document.querySelectorAll('#mfa-ok .dg');b[0].focus();b[0].value='1';b[0].dispatchEvent(new Event('input',{bubbles:true}))})()`);
  need(await ev(`document.activeElement === document.querySelectorAll('#mfa-ok .dg')[1]`), "typing a digit did not move to the next box");
  await ev(`document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Backspace',bubbles:true,cancelable:true}))`);
  need(await ev(`document.activeElement === document.querySelectorAll('#mfa-ok .dg')[0] && document.querySelector('#mfa-ok [name=code]').value === ''`), "backspace did not move back and clear");
  // 4. the code computed from the key on screen, pasted, turns it on and the stepper reaches step 3
  await paste("#mfa-ok .dg", totp(secret));
  need(await waitFor(`!!document.querySelector('#mfa-done')`), "a valid pasted code did not reach step 3: " + await ev(`document.querySelector('#mfa-msg')?.textContent`));
  need(/Signing in now asks for a code/.test(await ev(`document.querySelector('#mfa-done').textContent`)), "step 3 does not say what happens next");
  need(await ev(`document.querySelector('.steps [aria-current=step]')?.textContent.includes('Done') && document.querySelector('#mfa-chip').textContent === 'On'`), "step 3 is not current or the chip is not On");
  await shot("1-mfa-done");
  await ev(`document.querySelector('#mfa-close').click()`);
  need(await waitFor(`!!document.querySelector('#mfa-off')`), "closing step 3 did not show the on state");
  // 5. it survives a reload (the server reports it), so the screen is reading real state, not just its own flag
  await send("Page.reload");
  need(await waitFor(`!!document.querySelector('#mfa-off')`, 10000), "after a reload the account page does not show two-factor as on");
  // 5b. sign out and in again: the code field appears only after the server asks; six boxes, paste fills and auto-submits
  await ev(`document.querySelector('#logout').click()`);
  need(await waitFor(`!!document.querySelector('#login')`), "logging out did not show the sign-in page");
  need(await ev(`document.querySelector('#li-code').hidden`), "the code field is visible before the server asked for it");
  await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value=${JSON.stringify(pw)};f.requestSubmit()})()`);
  need(await waitFor(`!document.querySelector('#li-code').hidden`), "the code field did not appear when a code was required");
  need(await ev(`document.activeElement === document.querySelector('#li-code .dg') && document.querySelectorAll('#li-code .dg').length === 6`), "the code boxes are not six or not focused");
  await shot("1b-login-code");
  await paste("#li-code .dg", totp(secret, Date.now() / 1000 + 30));
  need(await waitFor(`location.hash === "#overview"`), "a pasted code did not sign in on its own: " + await ev(`document.querySelector('#li-msg')?.textContent`));
  await ev(`location.hash='#account'`);
  need(await waitFor(`!!document.querySelector('#mfa-off')`), "the account page did not come back after the two-factor sign-in");
  // 6. turn it back off through the inline password form (no confirm()), and prove that persisted too
  await ev(`window.confirm=()=>{throw new Error('confirm used')}`);
  need(await ev(`/password alone is enough/.test(document.querySelector('#mfa-off .warn')?.textContent || '')`), "the turn-off form has no plain warning");
  await ev(`(()=>{const f=document.querySelector('#mfa-off');f.password.value='wrong-password-x';f.requestSubmit()})()`);
  need(await waitFor(`(document.querySelector('#mfa-msg')?.textContent || '') !== ''`), "a wrong password for turn-off showed no message");
  need(await ev(`!!document.querySelector('#mfa-off')`), "a wrong password turned two-factor off");
  await ev(`(()=>{const f=document.querySelector('#mfa-off');f.password.value=${JSON.stringify(pw)};f.requestSubmit()})()`);
  need(await waitFor(`!!document.querySelector('#mfa-on')`), "turning two-factor off did not return the setup button: " + await ev(`document.querySelector('#mfa-msg')?.textContent`));
  need(await ev(`document.querySelector('#mfa-chip').textContent === 'Off'`), "the chip does not say Off after turning off");
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
