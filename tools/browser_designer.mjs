// Browser check for the visual dashboard designer: add from the palette, drag to move, drag the corner to resize, the panel, the Code tab, save.
// Setup as in browser_forms.mjs. Env: DURIAN_BASE (default http://127.0.0.1:8766), DURIAN_ADMIN_PASSWORD. Expect no problems.
import fs from "node:fs";
const out = process.argv[2]; fs.mkdirSync(out, {recursive: true});
const base = process.env.DURIAN_BASE ?? "http://127.0.0.1:8766", pw = process.env.DURIAN_ADMIN_PASSWORD;
const targets = await (await fetch("http://127.0.0.1:9222/json/list")).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const pend = new Map(); const problems = [];
ws.addEventListener("message", e => { const m = JSON.parse(e.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m.result ?? m.error); pend.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") problems.push("EXC " + (m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text)); });
const send = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const ev = async x => (await send("Runtime.evaluate", {expression: x, returnByValue: true, awaitPromise: true})).result?.value;
const shot = async n => fs.writeFileSync(`${out}/${n}.png`, Buffer.from((await send("Page.captureScreenshot", {format: "png"})).data, "base64"));
const mouse = (type, x, y) => send("Input.dispatchMouseEvent", {type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1});
const centre = sel => ev(`(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2,r.right-4,r.bottom-4]})()`);
const widgets = async () => { await ev(`document.querySelector('#tc').click()`); const j = JSON.parse(await ev(`document.querySelector('#code').value`)); await ev(`document.querySelector('#tv').click()`); await sleep(600); return j.widgets; };
await send("Page.enable"); await send("Runtime.enable"); await send("Emulation.setDeviceMetricsOverride", {width: 1400, height: 900, deviceScaleFactor: 1, mobile: false});
await send("Page.navigate", {url: base + "/"}); await sleep(1500);
await ev(`(()=>{const f=document.querySelector('#login');f.username.value='admin';f.password.value=${JSON.stringify(pw)};f.requestSubmit()})()`); await sleep(2000);
await ev(`location.hash='#design/_new'`); await sleep(2000);
await ev(`(()=>{const f=document.querySelector('#df');f.name.value='built';f.title.value='Built by hand'})()`);
await ev(`document.querySelector('[data-add=value]').click()`); await sleep(1200);
let w = await widgets(); if (w.length !== 1 || w[0].x !== 0) problems.push("palette add: " + JSON.stringify(w));
// drag the widget 3 columns right and 2 rows down
let [cx, cy] = await centre(".hit.sel, .hit");
await mouse("mousePressed", cx, cy); for (let i = 1; i <= 10; i++) await mouse("mouseMoved", cx + i * 40, cy + i * 20); await mouse("mouseReleased", cx + 400, cy + 200); await sleep(800);
w = await widgets(); if (!(w[0].x >= 3 && w[0].y >= 2)) problems.push("drag did not move it: " + JSON.stringify(w[0]));
const moved = {x: w[0].x, y: w[0].y};
// resize from the corner
const g = await centre(".hit .grip");
await mouse("mousePressed", g[0], g[1]); for (let i = 1; i <= 8; i++) await mouse("mouseMoved", g[0] + i * 25, g[1] + i * 15); await mouse("mouseReleased", g[0] + 200, g[1] + 120); await sleep(800);
w = await widgets(); if (!(w[0].w > 3 && w[0].h > 2)) problems.push("resize did not grow it: " + JSON.stringify(w[0]));
[cx, cy] = await centre(".hit"); await mouse("mousePressed", cx, cy); await mouse("mouseReleased", cx, cy); await sleep(300);
// the panel edits a setting, and a second widget dropped on the first slides below instead of overlapping
await ev(`(()=>{const i=document.querySelector('#pp [data-f=title]');i.value='Boiler';i.dispatchEvent(new Event('change',{bubbles:true}))})()`); await sleep(600);
await ev(`document.querySelector('[data-add=gauge]').click()`); await sleep(1000);
w = await widgets(); const [a, b] = w;
if (w.length !== 2 || a.title !== "Boiler" || (a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h)) problems.push("overlap: " + JSON.stringify(w.map(({id, x, y, w: ww, h}) => ({id, x, y, ww, h}))));
// keyboard moves the focused widget
await ev(`document.querySelector('.hit').focus()`); const before = (await widgets())[0];
await ev(`document.querySelector('.hit').focus()`); await send("Input.dispatchKeyEvent", {type: "keyDown", key: "ArrowLeft", code: "ArrowLeft", windowsVirtualKeyCode: 37}); await send("Input.dispatchKeyEvent", {type: "keyUp", key: "ArrowLeft", code: "ArrowLeft", windowsVirtualKeyCode: 37}); await sleep(800);
const after = (await widgets())[0]; if (after.x === before.x) problems.push("arrow key did not move: " + before.x + " " + after.x);
await shot("designer");
// code tab: edit the JSON, apply, save
await ev(`document.querySelector('#tc').click()`);
await ev(`(()=>{const t=document.querySelector('#code');const d=JSON.parse(t.value);d.widgets=d.widgets.slice(0,1);d.title='From code';t.value=JSON.stringify(d)})()`);
await ev(`document.querySelector('#sv').click()`); await sleep(1200);
const msg = await ev(`document.querySelector('#dm').textContent`); if (!/Saved/.test(msg)) problems.push("save: " + msg);
const saved = await ev(`fetch('/api/dashboards/built').then(r=>r.json())`); if (saved.title !== "From code" || saved.widgets.length !== 1) problems.push("saved wrong: " + JSON.stringify(saved).slice(0, 200));
await ev(`document.querySelector('#tc').click()`); await ev(`document.querySelector('#code').value='{bad'`); await ev(`document.querySelector('#ap').click()`);
if (!/Not applied/.test(await ev(`document.querySelector('#ce').textContent`))) problems.push("bad JSON was not refused");
console.log(problems.length ? problems : "no problems"); ws.close();
