// Browser check for the Mango-inspired features: sortable lists, state labels and the extra alarm conditions in the point form,
// the multistate set control, and maintenance windows (form, list, point page note).
// Start Edge with --headless=new --remote-debugging-port=9222 and a fresh --user-data-dir, start the server with
// DURIAN_ADMIN_PASSWORD set and a throwaway DURIAN_DB, then: node tools/browser_mango.mjs <outdir>
// Env: DURIAN_BASE (default http://127.0.0.1:8766), DURIAN_ADMIN_PASSWORD. Expect no problems.
import fs from "node:fs";
const out = process.argv[2]; fs.mkdirSync(out, {recursive: true});
const base = process.env.DURIAN_BASE ?? "http://127.0.0.1:8766";
const targets = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const pend = new Map(); const problems = [];
ws.addEventListener("message", e => { const m = JSON.parse(e.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m.result ?? m.error); pend.delete(m.id); }
  if (m.method === "Page.javascriptDialogOpening") send("Page.handleJavaScriptDialog", {accept: true});
  if (m.method === "Runtime.exceptionThrown") problems.push("EXC " + m.params.exceptionDetails.text + " " + (m.params.exceptionDetails.exception?.description ?? ""));
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") problems.push("CONSOLE " + m.params.args.map(a => a.value ?? a.description).join(" ")); });
const send = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const ev = async expr => (await send("Runtime.evaluate", {expression: expr, returnByValue: true, awaitPromise: true})).result?.value;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const shot = async n => fs.writeFileSync(`${out}/${n}.png`, Buffer.from((await send("Page.captureScreenshot", {format: "png"})).data, "base64"));
const go = async h => { await ev(`location.hash=${JSON.stringify(h)}`); await sleep(1800); };
const set = (sel, v) => ev(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)return 'missing ${sel}';if(e.type==='checkbox'){e.checked=${JSON.stringify(v)}}else{e.value=${JSON.stringify(String(v))}}e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'})()`);
const field = (path, v) => set(`[data-path="${path}"]`, v);
const text = sel => ev(`(document.querySelector(${JSON.stringify(sel)})?.textContent ?? 'MISSING').replace(/\\s+/g,' ').trim()`);
const adv = async () => JSON.parse(await ev(`document.querySelector('#adv').value`));
const call = (m, p, b) => ev(`fetch(${JSON.stringify(p)},{method:${JSON.stringify(m)},headers:{'Content-Type':'application/json','X-Requested-With':'durian'},body:${b ? JSON.stringify(JSON.stringify(b)) : "undefined"}}).then(r=>r.status)`);
await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable"); await send("Network.setCacheDisabled", {cacheDisabled: true});
await send("Emulation.setDeviceMetricsOverride", {width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false});
await send("Page.navigate", {url: base + "/"}); await sleep(2000);
await ev(`(()=>{const f=document.querySelector('#login');if(f){f.username.value='admin';f.password.value=${JSON.stringify(process.env.DURIAN_ADMIN_PASSWORD)};f.requestSubmit()}})()`); await sleep(3000);

// 1. sorting
for (const n of ["zeta", "alpha", "mid"]) await call("PUT", `/api/config/point/s-${n}`, {source: "demo", generator: {kind: "constant", value: {zeta: 3, alpha: 100, mid: 20}[n]}});
await sleep(2500);
await go("#config/point");
const names = () => ev(`[...document.querySelectorAll('table.dense tbody tr')].map(r=>r.cells[0].textContent).filter(n=>n.startsWith('s-')).join()`);
await ev(`[...document.querySelectorAll('th button.sort')].find(b=>b.textContent.trim()==='Name').click()`);
console.log("name asc:", await names(), "| aria-sort:", await ev(`document.querySelector('th[aria-sort=ascending]')?.textContent.trim()`));
await ev(`[...document.querySelectorAll('th button.sort')].find(b=>b.textContent.trim()==='Name').click()`);
console.log("name desc:", await names());
await ev(`[...document.querySelectorAll('th button.sort')].find(b=>b.textContent.trim()==='Value').click()`);
console.log("value asc (numbers, not text):", await names());

// 2. states and the extra alarm conditions through the form
await go("#config/point/_new");
await ev(`document.querySelector('#ed').name.value='pump-state'`);
await field("states", "0=Stopped, 1=Running, 2=Fault"); await field("settable", true); await field("set_roles", "user");
await ev(`document.querySelector('[data-add-det]').click()`); await sleep(300);
await set("#d0-kind", "STATE"); await sleep(300);
console.log("state fields:", await ev(`[...document.querySelectorAll('fieldset.det [data-dpath]')].map(e=>e.dataset.dpath).join()`));
await set("#d0-states", "2"); await set("#d0-level", "URGENT");
await ev(`document.querySelector('[data-add-det]').click()`); await sleep(300);
await set("#d1-kind", "NO_CHANGE"); await sleep(300); await set("#d1-seconds", "900");
await ev(`document.querySelector('[data-add-det]').click()`); await sleep(300);
await set("#d2-kind", "RANGE"); await sleep(300);
console.log("range fields:", await ev(`[...document.querySelectorAll('#d2-kind')].length && [...document.querySelectorAll('fieldset.det')][2].querySelectorAll('[data-dpath]').length`));
const j = await adv(); console.log("json:", JSON.stringify({states: j.states, det: j.detectors}));
await ev(`document.querySelector('#ed').requestSubmit()`); await sleep(2000);
console.log("saved:", await ev(`location.hash`), await text('#ed-msg'));
await call("PUT", "/api/config/point/pump-state", {...j, generator: {kind: "constant", value: 1}}); await sleep(2500);

// 3. point page shows the label and a select to set a state
await go("#point/pump-state");
console.log("label:", await text('.pd-val'), "| settings:", (await text('#pd-settings')).slice(0, 140));
console.log("set control:", await ev(`[...document.querySelectorAll('#pd-control select option')].map(o=>o.textContent).join('/')`));
await shot("1-pump");

// 4. maintenance window through the form
await go("#config/maintenance/_new");
await ev(`document.querySelector('#ed').name.value='night-shift'`);
console.log("maintenance groups:", await ev(`[...document.querySelectorAll('#fe-fields legend')].map(l=>l.textContent).join(' | ')`));
await field("scope.points", "pump-state"); await field("mode", "acknowledge");
await field("schedule.days", "Mon, Wed, Fri"); await field("schedule.start", "22:00"); await field("schedule.end", "06:00");
console.log("weekly json:", JSON.stringify((await adv()).schedule), JSON.stringify((await adv()).scope));
await field("schedule.type", "once"); console.log("once fields:", await ev(`[...document.querySelectorAll('[data-path^="schedule."]')].map(e=>e.dataset.path+':'+e.type).join()`));
await field("schedule.type", "manual"); await field("active", true);
await ev(`document.querySelector('#ed').requestSubmit()`); await sleep(2000);
await go("#config/maintenance");
console.log("list:", await text('table.dense tbody'));
await sleep(2500); await go("#point/pump-state");
console.log("point page note:", await text('#pd-top'));
await shot("2-point-maintenance");
console.log("problems:", problems);
process.exit(0);
