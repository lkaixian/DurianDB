// Browser check for the Tokens and Database pages. Setup as in browser_check.mjs.
// Env: DURIAN_BASE (default http://127.0.0.1:8770), DURIAN_ADMIN_PASSWORD.  Usage: node tools/browser_admin.mjs <outdir>
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
const out = process.argv[2]; fs.mkdirSync(out, {recursive: true});
const base = process.env.DURIAN_BASE ?? "http://127.0.0.1:8770";
const pw = process.env.DURIAN_ADMIN_PASSWORD;
if (!pw) { console.error("set DURIAN_ADMIN_PASSWORD"); process.exit(2); }
const targets = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const pending = new Map(); const problems = [];
// The empty token source and the bad import file are this check's own 4xx tests; the icon probe is the browser's.
const expected = ["/favicon.ico", "/api/tokens", "/api/import?mode=merge"];
const want = s => expected.some(x => s.includes(x));
ws.addEventListener("message", e => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result ?? m.error); pending.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") problems.push("EXC " + (m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text));
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") problems.push("CONSOLE " + m.params.args.map(a => a.value ?? a.description).join(" "));
  if (m.method === "Log.entryAdded" && m.params.entry.level === "error") { const s = m.params.entry.text + " " + (m.params.entry.url ?? ""); if (!want(s)) problems.push("LOG " + s); }
});
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const ev = async expr => (await send("Runtime.evaluate", {expression: expr, returnByValue: true, awaitPromise: true})).result?.value;
const shot = async n => fs.writeFileSync(`${out}/${n}.png`, Buffer.from((await send("Page.captureScreenshot", {format: "png"})).data, "base64"));
const go = async (h, ms = 2500) => { await ev(`location.hash=${JSON.stringify(h)}`); await sleep(ms); };
const text = sel => ev(`document.querySelector(${JSON.stringify(sel)})?.innerText.replace(/\\s+/g,' ')`);
await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable"); await send("DOM.enable");
await send("Page.navigate", {url: base + "/"}); await sleep(1500);
await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value=${JSON.stringify(pw)};f.requestSubmit()})()`); await sleep(2500);
await ev(`window.confirm = () => true`);
const call = (m, p, b) => ev(`fetch(${JSON.stringify(p)}, {method:${JSON.stringify(m)}, headers:{'X-Requested-With':'durian','Content-Type':'application/json'}, body:${b === undefined ? "undefined" : JSON.stringify(JSON.stringify(b))}}).then(r=>r.status)`);
console.log("setup http_in source:", await call("PUT", "/api/config/source/nodered", {type: "http_in", read_roles: ["user"]}));

// tokens
await go("#tokens");
console.log("TOKENS empty:", await text("#tl"));
console.log("source options:", await ev(`[...document.querySelectorAll('#tf [name=source] option')].map(o=>o.textContent).join(',')`));
await ev(`(()=>{const f=document.querySelector('#tf');f.scope.value='ingest';f.scope.dispatchEvent(new Event('change'));f.name.value='node-red-line1';f.source.value='nodered';f.requestSubmit()})()`); await sleep(1500);
const shown = await ev(`document.querySelector('#tk-val')?.textContent`);
console.log("TOKEN shown once, looks right:", /^dtk_[A-Za-z0-9_-]{40,}$/.test(shown ?? ""), "| table:", await text("#tl"));
await shot("t1-created");
await go("#overview", 1500); await go("#tokens", 1500);
console.log("after leaving and coming back the token text is gone:", !(await ev(`document.body.innerText.includes(${JSON.stringify(shown)})`)));
await ev(`(()=>{const f=document.querySelector('#tf');f.scope.value='ingest';f.scope.dispatchEvent(new Event('change'));f.name.value='bad';f.source.value='';f.requestSubmit()})()`); await sleep(1000);
console.log("VALIDATION:", await text("#tnew"));
await ev(`document.querySelector('[data-toggle]').click()`); await sleep(1000);
console.log("DISABLED:", await text("#tl"));
await ev(`document.querySelector('[data-del]').click()`); await sleep(1000);
console.log("DELETED:", await text("#tl"));

// database
await go("#database", 2500);
console.log("DATABASE:", (await text("#db")).slice(0, 260));
await ev(`(()=>{const f=document.querySelector('#dbs');f.retention_days.value='30';f.requestSubmit()})()`); await sleep(800);
console.log("SETTINGS:", await text("#dbm"));
console.log("BAD SETTING blocked by the browser's own validation:", await ev(`(()=>{const f=document.querySelector('#dbs');f.retention_days.value='-5';return !f.checkValidity()})()`));
await ev(`document.querySelector('#purge').click()`); await sleep(1000); console.log("PURGE:", await text("#dbm"));
await ev(`document.querySelector('#backup').click()`); await sleep(1500); console.log("BACKUP:", await text("#dbm"));
console.log("BACKUP LISTED without leaving the page:", await ev(`[...document.querySelectorAll('a[href^="/api/database/backups/"]')].length`));
await shot("t2-database");
// import: export first, change one thing, import it through the file input
const exported = await ev(`fetch('/api/export').then(r=>r.json())`);
exported.objects.point.push({name: "from-file", source: "nodered", unit: "kg"});
const file = path.join(os.tmpdir(), "durian-import-a.json"), file2 = path.join(os.tmpdir(), "durian-import-b.json"); fs.writeFileSync(file, JSON.stringify(exported));
const doc = await send("DOM.getDocument"); const sel = await send("DOM.querySelector", {nodeId: doc.root.nodeId, selector: "#imp"});
await send("DOM.setFileInputFiles", {files: [file], nodeId: sel.nodeId}); await sleep(1500);
console.log("IMPORT PREVIEW:", await text("#impout"));
await ev(`document.querySelector('#apply').click()`); await sleep(1500);
console.log("IMPORT APPLIED:", await text("#impout"), "| point exists:", await ev(`fetch('/api/config/point/from-file').then(r=>r.status)`));
fs.writeFileSync(file2, JSON.stringify({format: "durian-config", version: 1, objects: {point: [{name: "x", source: "ghost"}], handler: [{name: "h", type: "webhook", url: "file:///x"}]}}));
await go("#overview", 1500); await go("#database", 2500);
const sel2 = await send("DOM.querySelector", {nodeId: (await send("DOM.getDocument")).root.nodeId, selector: "#imp"});
await send("DOM.setFileInputFiles", {files: [file2], nodeId: sel2.nodeId}); await sleep(1500);
console.log("IMPORT ERRORS:", await text("#impout"));
await shot("t3-import-errors");
// mobile
await send("Emulation.setDeviceMetricsOverride", {width: 390, height: 844, deviceScaleFactor: 2, mobile: true});
for (const v of ["#tokens", "#database"]) { await go(v, 2500); console.log("MOBILE", v, "overflow:", await ev(`document.documentElement.scrollWidth > innerWidth`)); }
console.log("PROBLEMS:", JSON.stringify(problems));
ws.close(); process.exit(problems.length ? 1 : 0);
