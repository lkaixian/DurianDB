// Browser check for the point detail page: opening it from the overview, value and quality, trend, alarms, the set form with
// its confirm step, settings and the edit link, an unknown point, mobile overflow.
// Start Edge with --headless=new --remote-debugging-port=9222 and a fresh --user-data-dir, start the server with
// DURIAN_ADMIN_PASSWORD set and a throwaway DURIAN_DB, then: node tools/browser_point.mjs <outdir>
// Env: DURIAN_BASE (default http://127.0.0.1:8766), DURIAN_ADMIN_PASSWORD. Expect no problems.
import fs from "node:fs";
const out = process.argv[2]; fs.mkdirSync(out, {recursive: true});
const base = process.env.DURIAN_BASE ?? "http://127.0.0.1:8766";
const targets = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const pend = new Map(); const problems = []; const dialogs = [];
ws.addEventListener("message", e => { const m = JSON.parse(e.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m.result ?? m.error); pend.delete(m.id); }
  if (m.method === "Page.javascriptDialogOpening") { dialogs.push(m.params.message); send("Page.handleJavaScriptDialog", {accept: true}); }
  if (m.method === "Runtime.exceptionThrown") problems.push("EXC " + m.params.exceptionDetails.text + " " + (m.params.exceptionDetails.exception?.description ?? ""));
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") problems.push("CONSOLE " + m.params.args.map(a => a.value ?? a.description).join(" ")); });
const send = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const ev = async expr => (await send("Runtime.evaluate", {expression: expr, returnByValue: true, awaitPromise: true})).result?.value;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const shot = async n => fs.writeFileSync(`${out}/${n}.png`, Buffer.from((await send("Page.captureScreenshot", {format: "png"})).data, "base64"));
const text = sel => ev(`(document.querySelector(${JSON.stringify(sel)})?.textContent ?? 'MISSING').replace(/\\s+/g,' ').trim()`);
await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable"); await send("Network.setCacheDisabled", {cacheDisabled: true});
await send("Emulation.setDeviceMetricsOverride", {width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false});
await send("Page.navigate", {url: base + "/"}); await sleep(2000);
await ev(`(()=>{const f=document.querySelector('#login');if(f){f.username.value='admin';f.password.value=${JSON.stringify(process.env.DURIAN_ADMIN_PASSWORD)};f.requestSubmit()}})()`); await sleep(3000);
const H = {"Content-Type": "application/json", "X-Requested-With": "durian"};
const call = (m, p, b) => ev(`fetch(${JSON.stringify(p)},{method:${JSON.stringify(m)},headers:${JSON.stringify(H)},body:${b ? JSON.stringify(JSON.stringify(b)) : "undefined"}}).then(r=>r.status)`);
console.log("setup", await call("PUT", "/api/config/point/sp", {source: "demo", unit: "bar", description: "Header pressure setpoint", settable: true, set_roles: ["user"], generator: {kind: "constant", value: 5},
  eu_low: 0, eu_high: 10, decimals: 1, set_max: 8, max_step: 2, confirm: true, detectors: [{kind: "HIGH_LIMIT", limit: 7, level: "URGENT"}]}));
await ev(`location.hash="#overview"`); await sleep(4000);
console.log("tile link:", await ev(`document.querySelector('#points a[href="#point/sp"]')?.textContent`));
await ev(`document.querySelector('#points a[href="#point/sp"]').click()`); await sleep(3500);
console.log("hash:", await ev(`location.hash`), "| nav current:", await text('nav a[aria-current=page]'));
console.log("value:", await text('.pd-val'), "| desc:", await text('#pd-top p:nth-of-type(2)'));
console.log("source line:", await text('#pd-top .note'));
console.log("chart svg:", await ev(`!!document.querySelector('#pd-chart svg')`), "| chart text:", (await text('#pd-chart')).slice(0, 80));
console.log("settings:", await text('#pd-settings'));
console.log("control:", await text('#pd-control'));
console.log("alarms:", (await text('#pd-alarms')).slice(0, 120));
// set 7: within step (5 -> 7), asks for confirmation
await ev(`(()=>{const f=document.querySelector('#pd-control form');f.value.value='7';f.requestSubmit()})()`); await sleep(2000);
console.log("set 7:", await text('#pd-control output'), "| dialogs:", dialogs);
await sleep(3000); console.log("value after:", await text('.pd-val'), "| alarm now:", (await text('#pd-alarms')).slice(0, 140));
// set 9: refused above set_max, message on the page
await ev(`(()=>{const f=document.querySelector('#pd-control form');f.value.removeAttribute('max');f.value.value='9';f.requestSubmit()})()`); await sleep(1500);
console.log("set 9:", await text('#pd-control output'));
await shot("1-point");
console.log("edit link:", await ev(`document.querySelector('#pd-settings a.btn')?.getAttribute('href')`));
// a point with history draws its trend with the alarm limit lines
await ev(`location.hash="#point/boiler-temp"`); await sleep(7500);
console.log("trend with history:", await ev(`JSON.stringify({paths:document.querySelectorAll('#pd-chart svg path').length,text:document.querySelector('#pd-chart').textContent.slice(0,60)})`));
// unknown point
await ev(`location.hash="#point/ghost"`); await sleep(2500);
console.log("unknown:", await text('main'));
// mobile
await ev(`location.hash="#point/sp"`); await sleep(2500);
await send("Emulation.setDeviceMetricsOverride", {width: 390, height: 800, deviceScaleFactor: 2, mobile: true}); await sleep(600);
console.log("mobile overflow:", await ev(`document.documentElement.scrollWidth>innerWidth`)); await shot("2-mobile");
console.log("problems:", problems);
process.exit(0);
