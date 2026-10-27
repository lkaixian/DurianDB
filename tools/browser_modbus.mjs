// Browser check for the Modbus workflow in the console, through the config editor. Same setup as browser_check.mjs, plus
// a Modbus slave:  python -m durian.modbus_sim tcp 1502   (holding registers 0..3 are 10, 20, 30, 40)
// Env: DURIAN_BASE (default http://127.0.0.1:8767), DURIAN_ADMIN_PASSWORD.  Usage: node tools/browser_modbus.mjs <outdir>
import fs from "node:fs";
const out = process.argv[2]; fs.mkdirSync(out, {recursive: true});
const base = process.env.DURIAN_BASE ?? "http://127.0.0.1:8767";
const pw = process.env.DURIAN_ADMIN_PASSWORD;
if (!pw) { console.error("set DURIAN_ADMIN_PASSWORD"); process.exit(2); }
const targets = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const pending = new Map(); const problems = [];
ws.addEventListener("message", e => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result ?? m.error); pending.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") problems.push("EXC " + (m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text));
  if (m.method === "Log.entryAdded" && m.params.entry.level === "error") problems.push("LOG " + m.params.entry.text + " " + (m.params.entry.url ?? ""));
});
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const ev = async expr => (await send("Runtime.evaluate", {expression: expr, returnByValue: true, awaitPromise: true})).result?.value;
const shot = async n => fs.writeFileSync(`${out}/${n}.png`, Buffer.from((await send("Page.captureScreenshot", {format: "png"})).data, "base64"));
const go = async (h, ms = 2000) => { await ev(`location.hash=${JSON.stringify(h)}`); await sleep(ms); };
await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");
await send("Page.navigate", {url: base + "/"}); await sleep(1500);
await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value=${JSON.stringify(pw)};f.requestSubmit()})()`); await sleep(2500);

// 1. a Modbus source from the template, pointed at the simulator
await go("#config/source/_new");
console.log("TEMPLATES:", await ev(`[...document.querySelectorAll('#tpl option')].map(o=>o.textContent).join(' | ')`));
await ev(`(()=>{const s=document.querySelector('#tpl');s.value='Modbus TCP';s.dispatchEvent(new Event('change',{bubbles:true}))})()`);
await ev(`(()=>{const f=document.querySelector('#ed'),a=document.querySelector('#adv');const b=JSON.parse(a.value);b.link.port=1502;b.period=1;b.timeout=0.3;f.name.value='plc';a.value=JSON.stringify(b);a.dispatchEvent(new Event('input',{bubbles:true}));f.requestSubmit()})()`); await sleep(2000);
console.log("SOURCE SAVED:", await ev(`location.hash`), "|", await ev(`document.querySelector('#ed-msg').innerText||'(no message)'`));

// 2. a bad point first (shows the Modbus validation), then good ones
await go("#config/point/_new");
await ev(`(()=>{const s=document.querySelector('#tpl');s.value='Modbus holding register (uint16)';s.dispatchEvent(new Event('change',{bubbles:true}))})()`);
await ev(`(()=>{const f=document.querySelector('#ed'),a=document.querySelector('#adv');const b=JSON.parse(a.value);b.modbus.type='uint128';b.modbus.unit=0;f.name.value='bad';a.value=JSON.stringify(b);a.dispatchEvent(new Event('input',{bubbles:true}));f.requestSubmit()})()`); await sleep(1200);
console.log("VALIDATION:", (await ev(`document.querySelector('#ed-msg').innerText`)).replace(/\n/g, " / "));
await shot("m1-validation");
for (const [name, patch] of [["setpoint", {}], ["second", {"modbus.address": 1}], ["scaled", {"modbus.address": 2, "modbus.multiplier": 0.5, "settable": false}]]) {
  await go("#config/point/_new", 1500);
  await ev(`(()=>{const s=document.querySelector('#tpl');s.value='Modbus holding register (uint16)';s.dispatchEvent(new Event('change',{bubbles:true}))})()`);
  await ev(`(()=>{const f=document.querySelector('#ed'),a=document.querySelector('#adv');const b=JSON.parse(a.value);const p=${JSON.stringify(patch)};for(const [k,v] of Object.entries(p)){const parts=k.split('.');let o=b;while(parts.length>1)o=o[parts.shift()];o[parts[0]]=v}f.name.value=${JSON.stringify(name)};a.value=JSON.stringify(b);a.dispatchEvent(new Event('input',{bubbles:true}));f.requestSubmit()})()`); await sleep(1500);
}
await go("#config/source", 3000); console.log("SOURCE HEALTH:", (await ev(`[...document.querySelectorAll('table.dense tbody tr')].map(r=>r.innerText.replace(/\\s+/g,' ')).join(' / ')`))); await shot("m2-health");

// 3. values arrive on the overview, then write through the UI
await go("#overview", 4000);
console.log("VALUES:", await ev(`[...document.querySelectorAll('.pt')].map(p=>p.dataset.pt+'='+p.querySelector('.val').textContent).join(' ; ')`)); await shot("m3-overview");
await ev(`(()=>{const f=document.querySelector('form[data-set="setpoint"]');f.value.value='77';f.querySelector('button').click()})()`); await sleep(3500);
console.log("AFTER SET:", await ev(`document.querySelector('form[data-set="setpoint"] output')?.textContent`), "|", await ev(`document.querySelector('[data-pt="setpoint"] .val').textContent`));
console.log("PROBLEMS:", JSON.stringify(problems));
ws.close(); process.exit(0);
