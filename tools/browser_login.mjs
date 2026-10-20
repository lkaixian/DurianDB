// Browser check for the sign-in page and the password reset path: an admin issues a code, the user redeems it on the login page.
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
const fill = (form, vals) => ev(`(()=>{const f=document.querySelector(${JSON.stringify(form)});for(const [k,v] of Object.entries(${JSON.stringify(vals)}))f[k].value=v;f.requestSubmit()})()`);
await send("Page.enable"); await send("Runtime.enable");
await send("Page.navigate", {url: base + "/"}); await sleep(1500);
await ev(`document.querySelector('.forgot').open = true`); await shot("login");
await fill("#login", {username: "admin", password: pw}); await sleep(2000);
await ev(`(async()=>{const h={'X-Requested-With':'durian','Content-Type':'application/json'};await fetch('/api/users/bob',{method:'PUT',headers:h,body:JSON.stringify({roles:['user'],password:'bob-password-123'})})})()`);
await ev(`location.hash='#users'`); await sleep(1500); await ev(`window.confirm=()=>true`);
await ev(`document.querySelector('[data-rst=bob]').click()`); await sleep(1200);
const msg = await ev(`document.querySelector('#um').textContent`);
const code = /(rst_[\w-]+)/.exec(msg)?.[1]; if (!code) problems.push("no code shown: " + msg);
await shot("users");
await ev(`fetch('/api/logout',{method:'POST',headers:{'X-Requested-With':'durian'}})`); await send("Page.navigate", {url: base + "/"}); await sleep(1500);
await ev(`document.querySelector('.forgot').open = true`);
await fill("#reset", {username: "bob", code: code ?? "x", new: "bob-new-password-9"}); await sleep(1200);
const rs = await ev(`document.querySelector('#rs-msg').textContent`); if (!/Password changed/.test(rs)) problems.push("reset: " + rs);
await fill("#login", {username: "bob", password: "bob-new-password-9"}); await sleep(2000);
if (!(await ev(`location.hash`)).includes("overview")) problems.push("bob could not sign in with the new password");
await shot("after"); console.log(problems.length ? problems : "no problems"); ws.close();
