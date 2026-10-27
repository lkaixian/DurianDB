// Browser check for the form editors on the Config page: new source and point through the form, inline server errors,
// detector rows, the Advanced JSON box staying in step, labels on every control, mobile overflow.
// Start Edge with --headless=new --remote-debugging-port=9222 and a fresh --user-data-dir, start the server with
// DURIAN_ADMIN_PASSWORD set and a throwaway DURIAN_DB, then: node tools/browser_forms.mjs <outdir>
// Env: DURIAN_BASE (default http://127.0.0.1:8766), DURIAN_ADMIN_PASSWORD. Expect no problems.
import fs from "node:fs";
const out = process.argv[2]; fs.mkdirSync(out, {recursive: true});
const targets = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const pend = new Map(); const problems = [];
ws.addEventListener("message", e => { const m = JSON.parse(e.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m.result ?? m.error); pend.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") problems.push("EXC " + m.params.exceptionDetails.text + " " + (m.params.exceptionDetails.exception?.description ?? ""));
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") problems.push("CONSOLE " + m.params.args.map(a => a.value ?? a.description).join(" ")); });
const send = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const ev = async expr => (await send("Runtime.evaluate", {expression: expr, returnByValue: true, awaitPromise: true})).result?.value;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const shot = async n => fs.writeFileSync(`${out}/${n}.png`, Buffer.from((await send("Page.captureScreenshot", {format: "png"})).data, "base64"));
const go = async h => { await ev(`location.hash=${JSON.stringify(h)}`); await sleep(1800); };
// set a form control the way a person does: value, then input + change events
const set = (path, v) => ev(`(()=>{const e=document.querySelector('[data-path="${path}"]');if(!e)return 'missing ${path}';if(e.type==='checkbox'){e.checked=${JSON.stringify(v)}}else{e.value=${JSON.stringify(String(v))}}e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'})()`);
const adv = () => ev(`document.querySelector('#adv').value`);
const save = async () => { await ev(`document.querySelector('#ed').requestSubmit()`); await sleep(1500); };
const msg = () => ev(`document.querySelector('#ed-msg')?.innerText`);
await send("Page.enable"); await send("Runtime.enable");
await send("Network.enable"); await send("Network.setCacheDisabled", {cacheDisabled: true}); await send("Page.navigate", {url: (process.env.DURIAN_BASE ?? "http://127.0.0.1:8766") + "/"}); await sleep(2000);
await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value=${JSON.stringify(process.env.DURIAN_ADMIN_PASSWORD)};f.requestSubmit()})()`); await sleep(2500);

const putPoint = n => ev(`fetch('/api/config/point/${n}',{method:'PUT',headers:{'Content-Type':'application/json','X-Requested-With':'durian'},body:JSON.stringify({source:'demo',generator:{kind:'constant',value:1}})}).then(r=>r.status)`);
const putCfg = (path, b) => ev(`fetch('/api/config/${path}',{method:'PUT',headers:{'Content-Type':'application/json','X-Requested-With':'durian'},body:JSON.stringify(${JSON.stringify(b)})}).then(r=>r.status)`);
for (const n of ["list-a", "list-b", "list-c"]) await putPoint(n);   // enough rows for the filter box to appear
// 0. the lists are dense tables with a filter; a row opens its editor
await go("#config/point");
await shot("0-list");
console.log("point list:", await ev(`JSON.stringify({rows:document.querySelectorAll('table.dense tbody tr').length,head:[...document.querySelectorAll('table.dense th')].map(t=>t.textContent).join(),first:document.querySelector('table.dense tbody tr')?.innerText.replace(/\s+/g,' ')})`));
await ev(`(()=>{const f=document.querySelector('#lf');if(f){f.value='boiler';f.dispatchEvent(new Event('input',{bubbles:true}))}})()`); await sleep(300);
console.log("filtered:", await ev(`document.querySelector('#lcount')?.textContent`), "| visible:", await ev(`[...document.querySelectorAll('table.dense tbody tr')].filter(r=>!r.hidden).length`));
await go("#config/source");
console.log("source list head:", await ev(`[...document.querySelectorAll('table.dense th')].map(t=>t.textContent).join()`), "| states:", await ev(`[...document.querySelectorAll('table.dense tbody tr')].map(r=>r.cells[4].textContent).join(' / ')`));
await ev(`document.querySelector('table.dense tbody a').click()`); await sleep(1500);
console.log("row opens editor:", await ev(`location.hash`), "| back link:", await ev(`document.querySelector('.crumb a')?.textContent`));
// 1. new Modbus source through the form
await go("#config/source/_new");
console.log("fields on new source:", await ev(`[...document.querySelectorAll('#fe-fields legend')].map(l=>l.textContent).join(' | ')`));
await ev(`document.querySelector('#ed').name.value='plc'`);
await ev(`(()=>{const s=document.querySelector('#tpl');s.value='Modbus TCP';s.dispatchEvent(new Event('change',{bubbles:true}))})()`); await sleep(500);
console.log("host field:", await set("link.host", "127.0.0.1"), "port:", await set("link.port", 1502), "period:", await set("period", 1));
await set("link.kind", "serial"); console.log("serial fields:", await ev(`[...document.querySelectorAll('#fe-fields [data-path^="link."]')].map(e=>e.dataset.path).join()`));
await set("link.kind", "tcp"); await set("link.host", "127.0.0.1"); await set("link.port", 1502);
console.log("json has:", (await adv()).replace(/\s+/g, " ").slice(0, 200));
await set("heartbeat_seconds", 2); await set("period", 99999);   // too large on purpose: the server must say so on the field
await shot("1-source");
await save(); console.log("save result:", (await msg())?.replace(/\n/g, " "), "| hash:", await ev(`location.hash`));

// 2. validation error appears on the field
await go("#config/source/_new");
await ev(`document.querySelector('#ed').name.value='bad'`);
await ev(`(()=>{const s=document.querySelector('#tpl');s.value='HTTP JSON document';s.dispatchEvent(new Event('change',{bubbles:true}))})()`); await sleep(400);
await set("url", "ftp://nope"); await save();
console.log("inline error:", await ev(`document.querySelector('[data-err="url"]')?.textContent`), "| aria-invalid:", await ev(`document.querySelector('#f-url')?.getAttribute('aria-invalid')`), "| msg:", (await msg())?.replace(/\n/g, " "));
await shot("2-error");

// 3. new point: modbus source selected, change to demo, edit detectors
await go("#config/point/_new");
console.log("point groups:", await ev(`[...document.querySelectorAll('#fe-fields legend')].map(l=>l.textContent).join(' | ')`));
console.log("source options:", await ev(`[...document.querySelectorAll('#f-source option')].map(o=>o.value).join()`));
await set("source", "demo");
console.log("generator fields:", await ev(`[...document.querySelectorAll('#fe-fields [data-path^="generator."]')].map(e=>e.dataset.path).join()`));
await ev(`document.querySelector('#ed').name.value='form-point'`);
await set("unit", "bar"); await set("description", "Made with the form"); await set("tags", "area:test, line:2"); await set("eu_low", 0); await set("eu_high", 10);
await set("settable", true); await set("set_max", 8); await set("max_step", 2); await set("confirm", true);
await ev(`document.querySelector('[data-add-det]').click()`); await sleep(300);
await ev(`(()=>{const e=document.querySelector('#d0-limit');e.value='7.5';e.dispatchEvent(new Event('change',{bubbles:true}));const l=document.querySelector('#d0-level');l.value='URGENT';l.dispatchEvent(new Event('change',{bubbles:true}));const d=document.querySelector('#d0-deadband');d.value='0.5';d.dispatchEvent(new Event('change',{bubbles:true}))})()`);
const j = JSON.parse(await adv()); console.log("point json:", JSON.stringify(j));
await shot("3-point");
await save(); console.log("saved ->", await ev(`location.hash`), (await msg())?.replace(/\n/g, " "));
// 4. bad number gives a message on its field
await set("eu_high", -5); await save();
console.log("range error:", await ev(`document.querySelector('[data-err="eu_high"]')?.textContent`));
// 5. editing the JSON updates the form
await ev(`(()=>{const a=document.querySelector('#adv');a.focus();const o=JSON.parse(a.value);o.unit='psi';a.value=JSON.stringify(o);a.dispatchEvent(new Event('input',{bubbles:true}))})()`); await sleep(300);
console.log("unit field after JSON edit:", await ev(`document.querySelector('#f-unit').value`));
// 6. existing point opens with values
await go("#config/point/boiler-temp");
console.log("existing:", await ev(`JSON.stringify({unit:document.querySelector('#f-unit').value,gen:document.querySelector('#f-generator-kind')?.value,dets:document.querySelectorAll('fieldset.det').length,name:document.querySelector('#ed').name.value})`));
await shot("4-existing");
// 7. keyboard: every control is focusable and labelled
console.log("unlabelled controls:", await ev(`[...document.querySelectorAll('#ed input,#ed select,#ed textarea')].filter(e=>!(e.labels&&e.labels.length)&&!e.getAttribute('aria-label')).map(e=>e.id||e.dataset.path).join()`));
// 8. out of service from the point page, then back
await go("#config/point/boiler-temp");
await ev(`(()=>{document.querySelector('#svc-r').value='sensor replacement';document.querySelector('#svc').requestSubmit()})()`); await sleep(1500);
console.log("out of service:", await ev(`document.querySelector('#svc').textContent`));
await ev(`document.querySelector('#svc').requestSubmit()`); await sleep(1500);
console.log("back in service:", await ev(`document.querySelector('#svc').textContent.includes('Take out of service')`));
// 9. Test settings on a new source reports an answer (a device or a clear failure), never nothing
await go("#config/source/_new");
await ev(`(()=>{const s=document.querySelector('#tpl');s.value='Modbus TCP';s.dispatchEvent(new Event('change',{bubbles:true}))})()`); await sleep(400);
await set("link.port", 1); await set("timeout", 0.5); await set("retries", 0);
await ev(`document.querySelector('#test').click()`); await sleep(2500);
console.log("test result:", await ev(`document.querySelector('#test-out').textContent`));
// 10. a shared watch list holds a read-time query, and /api/points resolves it (durian/app.py query_match)
for (const [n, f] of [["zone-fan", "plant/boiler"], ["zone-pump", "plant/line"]]) await putCfg(`point/${n}`, {source: "demo", folder: f, generator: {kind: "constant", value: 1}});
await sleep(1000);
await go("#config/watchlist/_new");
console.log("watchlist query fields:", await ev(`[...document.querySelectorAll('#fe-fields [data-path^="query."]')].map(e=>e.dataset.path).join()`));
await ev(`document.querySelector('#ed').name.value='plant-boiler'`);
await set("tags", "");   // the template's area:boiler tag would also match; the folder query alone is what we test
const folderField = await set("query.folder", "plant/boiler");
console.log("watchlist query field:", folderField);
if (folderField !== "ok") { console.log("FAIL: the watch list form has no folder query field"); process.exit(1); }
await shot("6-watchlist");
await save();
console.log("watchlist saved:", (await msg())?.replace(/\n/g, " "), "| hash:", await ev(`location.hash`));
const wlCfg = await ev(`fetch('/api/config/watchlist/plant-boiler').then(r=>r.json())`);
console.log("stored query:", JSON.stringify(wlCfg?.query));
const resolved = await ev(`fetch('/api/points?watchlist=plant-boiler').then(r=>r.json()).then(x=>x.map(p=>p.name).join())`);
console.log("points by watch list:", resolved);
if (JSON.stringify(wlCfg?.query) !== JSON.stringify({folder: "plant/boiler"})) { console.log("FAIL: the saved watch list did not keep its query"); process.exit(1); }
if (!String(resolved).includes("zone-fan") || String(resolved).includes("zone-pump")) { console.log("FAIL: the watch list query did not resolve the folder subtree at read time"); process.exit(1); }
// mobile
await send("Emulation.setDeviceMetricsOverride", {width: 390, height: 800, deviceScaleFactor: 2, mobile: true});
await sleep(500); console.log("mobile overflow:", await ev(`document.documentElement.scrollWidth>innerWidth`)); await shot("5-mobile");
console.log("problems:", problems);
process.exit(problems.length ? 1 : 0);
