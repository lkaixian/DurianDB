// Browser check for user-owned watch lists: save the overview as a list, filter by it, delete it. Same setup as browser_forms.mjs.
// Expect no problems.
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
await send("Page.enable"); await send("Runtime.enable");
await send("Network.enable"); await send("Network.setCacheDisabled", {cacheDisabled: true}); await send("Page.navigate", {url: (process.env.DURIAN_BASE ?? "http://127.0.0.1:8766") + "/"}); await sleep(2000);
await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value=${JSON.stringify(process.env.DURIAN_ADMIN_PASSWORD)};f.requestSubmit()})()`); await sleep(2500);
await go("#overview");
await ev(`window.prompt=()=>'mine-a';window.confirm=()=>true`);
await ev(`(()=>{const t=document.querySelector('#f-tag');t.value='area:boiler';t.dispatchEvent(new Event('input',{bubbles:true}))})()`); await sleep(2500);
const shown = await ev(`document.querySelectorAll('#points [data-pt]').length`);
await ev(`document.querySelector('#f-save').click()`); await sleep(2000);
console.log("saved:", await ev(`document.querySelector('#f-out').textContent`), "| options:", await ev(`[...document.querySelectorAll('#f-wl option')].map(o=>o.textContent).join()`), "| delete shown:", await ev(`!document.querySelector('#f-del').hidden`));
await ev(`(()=>{const t=document.querySelector('#f-tag');t.value='';t.dispatchEvent(new Event('input',{bubbles:true}))})()`); await sleep(2500);
console.log("rows with list:", await ev(`document.querySelectorAll('#points [data-pt]').length`), "expected", shown);
await shot("my-list");
await ev(`document.querySelector('#f-del').click()`); await sleep(2000);
console.log("after delete:", await ev(`[...document.querySelectorAll('#f-wl option')].map(o=>o.textContent).join()`));
console.log("problems:", JSON.stringify(problems));
ws.close();
process.exit(problems.length ? 1 : 0);
