// Browser check for the Ctrl+K command palette: open, search a point, Enter navigates, Esc restores focus, "dark" runs the theme action.
// Same setup as browser_mylists.mjs. Expect no problems.
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
const key = async (k, code, mods = 0, text) => {
  await send("Input.dispatchKeyEvent", {type: "rawKeyDown", key: k, code, windowsVirtualKeyCode: k.length === 1 ? k.toUpperCase().charCodeAt(0) : ({Enter: 13, Escape: 27}[k]), modifiers: mods});
  await send("Input.dispatchKeyEvent", {type: "keyUp", key: k, code, modifiers: mods});
};
const type = async s => { for (const c of s) await send("Input.dispatchKeyEvent", {type: "char", text: c, key: c}); await sleep(250); };
let bad = 0;
const check = (what, ok) => { console.log(ok ? "ok  " : "FAIL", what); if (!ok) bad++; };
await send("Page.enable"); await send("Runtime.enable");
await send("Network.enable"); await send("Network.setCacheDisabled", {cacheDisabled: true}); await send("Page.navigate", {url: (process.env.DURIAN_BASE ?? "http://127.0.0.1:8766") + "/"}); await sleep(2000);
await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value=${JSON.stringify(process.env.DURIAN_ADMIN_PASSWORD)};f.requestSubmit()})()`); await sleep(2500);
const pt = await ev(`fetch('/api/points').then(r=>r.json()).then(p=>p[0]?.name)`);
check("a point exists to search for: " + pt, !!pt);
await ev(`location.hash='#alarms'`); await sleep(800);
await ev(`document.querySelector('#theme').focus()`);

await key("k", "KeyK", 2); await sleep(400);  // Ctrl+K
check("dialog opens", await ev(`!!document.querySelector('dialog[open][aria-modal=true], [role=dialog][aria-modal=true]:not([hidden])')`));
check("input focused", await ev(`document.activeElement?.getAttribute('role')==='combobox'`));
await type(pt); await sleep(300);
check("point result listed", await ev(`[...document.querySelectorAll('[role=option]')].some(o=>o.textContent.includes(${JSON.stringify(pt)}))`));
await shot("palette");
await key("Enter", "Enter"); await sleep(1200);
check("Enter navigates to the point page", await ev(`location.hash`) === "#point/" + encodeURIComponent(pt));
check("palette closed after Enter", await ev(`!document.querySelector('[role=dialog][aria-modal=true]:not([hidden])')?.open && !document.querySelector('dialog[open]')`));

await ev(`document.querySelector('#theme').focus()`);
await key("k", "KeyK", 2); await sleep(400);
check("reopens", await ev(`!!document.querySelector('dialog[open]')`));
await key("Escape", "Escape"); await sleep(400);
check("Esc closes", await ev(`!document.querySelector('dialog[open]')`));
check("focus returns to #theme", await ev(`document.activeElement?.id`) === "theme");

const before = await ev(`document.documentElement.dataset.theme || ''`);
await key("k", "KeyK", 2); await sleep(400);
await type("dark"); await key("Enter", "Enter"); await sleep(500);
const after = await ev(`document.documentElement.dataset.theme`);
check("typing dark runs the theme action", !!after && after !== before);
console.log("problems:", JSON.stringify(problems));
ws.close();
process.exit(problems.length || bad ? 1 : 0);
