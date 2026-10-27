// Browser check for the console via Chrome DevTools Protocol (no dependencies, Node 22+).
// Start Edge/Chrome with --headless=new --remote-debugging-port=9222 and a fresh --user-data-dir, start the server
// with DURIAN_ADMIN_PASSWORD set, then: node tools/browser_check.mjs <outdir>
// Env: DURIAN_BASE (default http://127.0.0.1:8766), DURIAN_ADMIN_PASSWORD. Expect two logged errors: the bad login and the invalid config.
import fs from "node:fs";
const out = process.argv[2]; fs.mkdirSync(out, {recursive: true});
const base = process.env.DURIAN_BASE ?? "http://127.0.0.1:8766";
const adminPw = process.env.DURIAN_ADMIN_PASSWORD;
if (!adminPw) { console.error("set DURIAN_ADMIN_PASSWORD"); process.exit(2); }
const targets = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const pending = new Map(); const problems = [];
// The wrong password and the deliberately invalid point are this check's own 4xx tests; the icon probe is the browser's. Nothing else is expected.
const expected = ["/favicon.ico", "/api/login", "/api/config/point/ui-test"];
const want = s => expected.some(x => s.includes(x));
ws.addEventListener("message", e => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result ?? m.error); pending.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") problems.push("EXC " + m.params.exceptionDetails.text + " " + (m.params.exceptionDetails.exception?.description ?? ""));
  if (m.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(m.params.type)) problems.push("CONSOLE " + m.params.type + " " + m.params.args.map(a => a.value ?? a.description).join(" "));
  if (m.method === "Log.entryAdded" && m.params.entry.level !== "info") { const l = m.params.entry, s = l.text + " " + (l.url ?? ""); if (!want(s)) problems.push("LOG " + l.level + " " + s); }
});
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const ev = async expr => (await send("Runtime.evaluate", {expression: expr, returnByValue: true, awaitPromise: true})).result?.value;
const shot = async name => fs.writeFileSync(`${out}/${name}.png`, Buffer.from((await send("Page.captureScreenshot", {format: "png"})).data, "base64"));
await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");

async function go(hash, wait = 2500) { await ev(`location.hash=${JSON.stringify(hash)}`); await sleep(wait); }
const summary = async () => ev(`JSON.stringify({view:location.hash,h:document.querySelector('h2')?.textContent,text:document.querySelector('main').innerText.slice(0,500),overflow:document.documentElement.scrollWidth>innerWidth})`);

await send("Page.navigate", {url: base + "/"}); await sleep(1500);
console.log("LOGIN PAGE", await summary()); await shot("01-login");

// wrong password first, then the right one
await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value='wrong-password';f.requestSubmit()})()`); await sleep(800);
console.log("BAD LOGIN MSG:", await ev(`document.querySelector('#li-msg').textContent`));
await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value=${JSON.stringify(adminPw)};f.requestSubmit()})()`); await sleep(5000);
console.log("OVERVIEW", await summary()); await shot("02-overview");

// acknowledge the first active alarm if any
console.log("ACK buttons:", await ev(`document.querySelectorAll('[data-ack]').length`));
await ev(`document.querySelector('[data-ack]')?.click()`); await sleep(1500);
// set the settable point
console.log("SET FORM:", await ev(`(()=>{const f=document.querySelector('form[data-set]');if(!f)return 'none';f.value.value='55';f.querySelector('button').click();return 'clicked'})()`)); await sleep(3500);
console.log("SET RESULT:", await ev(`document.querySelector('form[data-set] output')?.textContent`));
console.log("PUMP VALUE:", await ev(`document.querySelector('[data-pt="pump-setpoint"] .val')?.textContent`));
// filter by tag
await ev(`(()=>{const t=document.querySelector('#f-tag');t.value='area:line';t.dispatchEvent(new Event('input',{bubbles:true}))})()`); await sleep(2500);
console.log("TAG FILTER points:", await ev(`[...document.querySelectorAll('.pt')].map(p=>p.dataset.pt).join()`)); await shot("03-filtered");

await go("#history", 3500); console.log("HISTORY", await summary()); await shot("04-history");
await go("#config/source", 2500); console.log("CONFIG SOURCE", await summary()); await shot("05-config-source");
await go("#config/point/hot2", 1500);
// create a new point with bad config to see validation, then a valid one
await go("#config/point/_new", 2000);
await ev(`(()=>{const f=document.querySelector('#ed');f.name.value='ui-test';(()=>{const a=document.querySelector('#adv');a.value=JSON.stringify({source:'ghost',tolerance:-1,detectors:[{kind:'HIGH_LIMIT'}]});a.dispatchEvent(new Event('input',{bubbles:true}))})();f.requestSubmit()})()`); await sleep(1200);
console.log("VALIDATION:", await ev(`document.querySelector('#ed-msg').innerText`)); await shot("06-validation");
await ev(`(()=>{const f=document.querySelector('#ed');(()=>{const a=document.querySelector('#adv');a.value=JSON.stringify({source:'demo',unit:'C',tags:{area:'ui'},generator:{kind:'sine',offset:50,amplitude:10,period:60},detectors:[{kind:'LOW_LIMIT',limit:45,level:'WARNING'}]});a.dispatchEvent(new Event('input',{bubbles:true}))})();f.requestSubmit()})()`); await sleep(2500);
console.log("AFTER SAVE:", await summary());
await go("#users", 2000); console.log("USERS", await summary()); await shot("07-users");
await go("#audit", 2000); console.log("AUDIT", await summary()); await shot("08-audit");
await go("#account", 1500); console.log("ACCOUNT", await summary());

// mobile width and dark mode
await send("Emulation.setDeviceMetricsOverride", {width: 390, height: 844, deviceScaleFactor: 2, mobile: true});
for (const v of ["#overview", "#history", "#config/source", "#users", "#audit"]) { await go(v, 3000); const s = JSON.parse(await summary()); console.log("MOBILE", v, "overflow:", s.overflow); if (v === "#overview") await shot("09-mobile-overview"); if (v === "#audit") await shot("10-mobile-audit"); }
await send("Emulation.setEmulatedMedia", {features: [{name: "prefers-color-scheme", value: "dark"}]});
await go("#overview", 3000); await shot("11-dark-mobile");
await send("Emulation.setDeviceMetricsOverride", {width: 1200, height: 800, deviceScaleFactor: 1, mobile: false});
await go("#history", 3500); await shot("12-dark-history");
// keyboard: tab order starts with the skip link, focus ring is visible on buttons
console.log("FOCUS after load:", await ev(`document.activeElement.tagName+':'+(document.activeElement.id||document.activeElement.className)`));
await send("Emulation.setEmulatedMedia", {features: [{name: "prefers-color-scheme", value: "light"}]});
await go("#overview", 3000); await shot("13-light-overview");
console.log("PROBLEMS:", JSON.stringify(problems, null, 1));
ws.close(); process.exit(problems.length ? 1 : 0);
