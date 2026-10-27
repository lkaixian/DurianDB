// Browser check for dashboards: viewing, designing, validation, public access, kiosk, mobile, dark mode.
// Setup as in browser_check.mjs (server with DURIAN_ADMIN_PASSWORD, headless browser on port 9222, fresh profile).
// Env: DURIAN_BASE (default http://127.0.0.1:8768), DURIAN_ADMIN_PASSWORD.  Usage: node tools/browser_dashboard.mjs <outdir>
import fs from "node:fs";
const out = process.argv[2]; fs.mkdirSync(out, {recursive: true});
const base = process.env.DURIAN_BASE ?? "http://127.0.0.1:8768";
const pw = process.env.DURIAN_ADMIN_PASSWORD;
if (!pw) { console.error("set DURIAN_ADMIN_PASSWORD"); process.exit(2); }
const targets = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const pending = new Map(); const problems = [];
// Expected 4xx: the browser's icon probe, the private dashboard a visitor must be denied, and the public chart asking
// for a point anonymous cannot read (the point keeps its own permissions). Any other console error fails the check.
const expected = ["/favicon.ico", "/api/dashboards/ui-made", "/api/series?point=boiler-temp&window=3600&rollup=avg&bucket=30"];
const want = s => expected.some(x => s.includes(x));
ws.addEventListener("message", e => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result ?? m.error); pending.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") problems.push("EXC " + (m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text));
  if (m.method === "Log.entryAdded" && m.params.entry.level === "error") { const s = m.params.entry.text + " " + (m.params.entry.url ?? ""); if (!want(s)) problems.push("LOG " + s); }
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") problems.push("CONSOLE " + m.params.args.map(a => a.value ?? a.description).join(" "));
});
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const ev = async expr => (await send("Runtime.evaluate", {expression: expr, returnByValue: true, awaitPromise: true})).result?.value;
const shot = async n => fs.writeFileSync(`${out}/${n}.png`, Buffer.from((await send("Page.captureScreenshot", {format: "png"})).data, "base64"));
const go = async (h, ms = 2500) => { await ev(`location.hash=${JSON.stringify(h)}`); await sleep(ms); };
const overflow = () => ev(`document.documentElement.scrollWidth > innerWidth`);
await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");
await send("Page.navigate", {url: base + "/"}); await sleep(1500);
await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value=${JSON.stringify(pw)};f.requestSubmit()})()`); await sleep(2500);

// 1. the list and the seeded dashboard
await go("#dashboards");
console.log("LIST:", (await ev(`document.querySelector('#dl').innerText`)).replace(/\s+/g, " "));
await go("#d/plant-overview", 4500);
console.log("WIDGETS:", await ev(`document.querySelectorAll('.widget').length`), "| types:", await ev(`[...document.querySelectorAll('.widget h3')].map(h=>h.textContent).join(' ; ')`));
console.log("VALUE:", await ev(`document.querySelector('[data-id=temp]').innerText.replace(/\\s+/g,' ')`));
console.log("CHART svg:", await ev(`!!document.querySelector('[data-id=trend] svg.chart path.line')`), "| gauge svg:", await ev(`!!document.querySelector('[data-id=press] svg rect.gtrack')`));
console.log("SETPOINT form:", await ev(`!!document.querySelector('[data-id=pump] form')`));
await ev(`(()=>{const f=document.querySelector('[data-id=pump] form');f.value.value='61';f.querySelector('button').click()})()`); await sleep(3500);
console.log("SETPOINT after set:", await ev(`document.querySelector('[data-id=pump] .v').textContent`));
console.log("grid placed via style object:", await ev(`getComputedStyle(document.querySelector('[data-id=trend]')).gridColumnStart + '/' + getComputedStyle(document.querySelector('[data-id=trend]')).gridColumnEnd`));
await shot("d1-plant-overview");

// 2. design a new dashboard: add from the palette, break it on purpose, fix it, save
await go("#design/_new", 2000);
await ev(`(()=>{const f=document.querySelector('#df');f.name.value='ui-made';f.title.value='Made in the designer';f.dispatchEvent(new Event('change',{bubbles:true}))})()`);
for (const t of ["value", "gauge", "text"]) { await ev(`(()=>document.querySelector('[data-add=${t}]').click())()`); await sleep(600); }
console.log("DRAFT widgets:", await ev(`document.querySelectorAll('#pv .widget').length`), "| layer boxes:", await ev(`document.querySelectorAll('#pv .hit').length`));
// select a widget on the canvas the way a pointer does: the panel then edits it
const pick = id => ev(`(()=>{const h=document.querySelector('#pv .hit[data-hit="${id}"]'),r=h.getBoundingClientRect();h.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerId:1,clientX:r.left+4,clientY:r.top+4}));document.querySelector('#pv').dispatchEvent(new PointerEvent('pointerup',{bubbles:true,button:0,pointerId:1}));return document.querySelector('#pp h3')?.textContent})()`);
// 2a. break it on purpose: the gauge is wider than the 12-column grid
console.log("panel for:", await pick("gauge1"));
await ev(`(()=>{const i=document.querySelector('#pp [data-f=w]');i.value='99';i.dispatchEvent(new Event('change',{bubbles:true}))})()`); await sleep(600);
await ev(`document.querySelector('#sv').click()`); await sleep(1200);
console.log("SAVE (broken on purpose):", (await ev(`document.querySelector('#dm').innerText`)).replace(/\n/g, " / "));
await shot("d2a-validation");
// 2b. fix the width; put a script in the note widget so the viewer must show it as text
await ev(`(()=>{const i=document.querySelector('#pp [data-f=w]');i.value='3';i.dispatchEvent(new Event('change',{bubbles:true}))})()`); await sleep(400);
console.log("panel for:", await pick("text1"));
const xss = '<img src=x onerror=window.__xss=1><script>window.__xss=1<\/script>';
await ev(`(()=>{const i=document.querySelector('#pp [data-s=text]');i.value=${JSON.stringify(xss)};i.dispatchEvent(new Event('change',{bubbles:true}))})()`); await sleep(400);
await ev(`document.querySelector('#sv').click()`); await sleep(1500);
console.log("SAVE (fixed):", (await ev(`document.querySelector('#dm').innerText`)).replace(/\n/g, " / "));
await shot("d2-designer");

// 3. a script in a text widget must stay text
await go("#d/ui-made", 3500);
console.log("MADE widgets:", await ev(`document.querySelectorAll('.widget').length`), "| text:", await ev(`document.querySelector('.plain')?.textContent`));
console.log("XSS executed:", await ev(`window.__xss === 1`), "| shown as text:", await ev(`document.querySelector('.plain')?.textContent.slice(0, 30)`), "| img elements injected:", await ev(`document.querySelectorAll('.widget img').length`));

// 4. public dashboard: visible to a visitor, but the data stays behind its own permissions
await go("#design/plant-overview", 2500);
await ev(`(()=>{const f=document.querySelector('#df');f.read_roles.value='user, anonymous';f.dispatchEvent(new Event('change',{bubbles:true}))})()`);
await ev(`document.querySelector('#sv').click()`); await sleep(1500);
console.log("PUBLISH:", (await ev(`document.querySelector('#dm').innerText`)).replace(/\n/g, " / ").slice(0, 330));
await send("Network.enable"); await send("Network.clearBrowserCookies");
await send("Page.navigate", {url: "about:blank"}); await sleep(500);
await send("Page.navigate", {url: base + "/#d/plant-overview"}); await sleep(4000);
console.log("VISITOR sees:", await ev(`document.querySelector('#dt')?.textContent`), "| nav:", await ev(`document.querySelector('#nav').innerText`), "| value widget:", (await ev(`document.querySelector('[data-id=temp]')?.innerText.replace(/\\s+/g,' ')`)));
await shot("d3-public-visitor");
await send("Page.navigate", {url: "about:blank"}); await sleep(500);
await send("Page.navigate", {url: base + "/#d/ui-made"}); await sleep(3000);
console.log("VISITOR on a private dashboard:", await ev(`document.querySelector('#dt')?.textContent + ' | ' + document.querySelector('#dm')?.textContent`));

// 5. log back in: kiosk, mobile, dark
await send("Page.navigate", {url: base + "/"}); await sleep(1500);
await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value=${JSON.stringify(pw)};f.requestSubmit()})()`); await sleep(2500);
await go("#d/plant-overview/kiosk", 3500);
console.log("KIOSK header hidden:", await ev(`getComputedStyle(document.querySelector('header')).display === 'none'`));
await go("#d/plant-overview", 2500);
await send("Emulation.setDeviceMetricsOverride", {width: 390, height: 844, deviceScaleFactor: 2, mobile: true});
for (const v of ["#d/plant-overview", "#dashboards", "#design/plant-overview"]) { await go(v, 3500); console.log("MOBILE", v, "overflow:", await overflow()); if (v === "#d/plant-overview") await shot("d4-mobile"); }
await send("Emulation.setEmulatedMedia", {features: [{name: "prefers-color-scheme", value: "dark"}]});
await send("Emulation.setDeviceMetricsOverride", {width: 1200, height: 800, deviceScaleFactor: 1, mobile: false});
await go("#d/plant-overview", 4000); await shot("d5-dark");
console.log("PROBLEMS:", JSON.stringify(problems));
ws.close(); process.exit(problems.length ? 1 : 0);
