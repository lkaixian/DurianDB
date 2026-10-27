// Runs the tools/browser_*.mjs checks headlessly, the way CI does.
//
// Each check gets its own throwaway database, its own throwaway browser profile, a free server port and, where
// needed, its own simulator. The checks still connect to CDP on 127.0.0.1:9222, so they run one at a time.
// Usage: node tools/browser_ci.mjs [check ...]      (default: every check)
// Env: CHROME (browser executable), DURIAN_ADMIN_PASSWORD, DURIAN_BROWSER_TIMEOUT_MS (default 180000).
import {spawn, spawnSync} from "node:child_process";
import {existsSync, mkdtempSync, rmSync} from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
const ADMIN_PW = process.env.DURIAN_ADMIN_PASSWORD ?? "durian-admin-123";
const TIMEOUT = Number(process.env.DURIAN_BROWSER_TIMEOUT_MS ?? 180000);
const CDP = "http://127.0.0.1:9222";
const PYTHON = process.env.PYTHON ?? "python";
const NODE = process.env.NODE ?? process.execPath;

// A check is self-contained unless it needs a second process: only the Modbus check needs the simulator.
const CHECKS = {
  account: {}, admin: {}, check: {}, dashboard: {}, designer: {}, forms: {}, login: {}, mango: {},
  mimic: {}, modbus: {simulator: "modbus"}, mylists: {}, point: {},
};
const wanted = process.argv.slice(2);
const names = wanted.length ? wanted : Object.keys(CHECKS);
for (const n of names) if (!(n in CHECKS)) fail(`unknown check '${n}'; choose from ${Object.keys(CHECKS).join(", ")}`);

const sleep = ms => new Promise(r => setTimeout(r, ms));

function fail(message) {
  console.error(message);
  if (process.env.GITHUB_ACTIONS) console.log("::error title=browser check::" + message.replace(/\n/g, "%0A"));
  process.exit(1);
}

function webPath() {
  const env = process.env.CHROME || process.env.EDGE;
  if (env) return env;
  const candidates = process.platform === "win32"
    ? [path.join(process.env["ProgramFiles(x86)"] ?? "", "Microsoft/Edge/Application/msedge.exe"),
       path.join(process.env.ProgramFiles ?? "", "Google/Chrome/Application/chrome.exe"),
       path.join(process.env.LOCALAPPDATA ?? "", "Google/Chrome/Application/chrome.exe")]
    : process.platform === "darwin"
      ? ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
         "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
      : ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"];
  const found = candidates.find(p => p && existsSync(p));
  if (!found) throw new Error("no Chrome/Chromium found; set CHROME to the executable");
  return found;
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => { const {port} = server.address(); server.close(() => resolve(port)); });
  });
}

async function waitFor(what, predicate, ms = 30000) {
  const until = Date.now() + ms;
  for (;;) {
    try { if (await predicate()) return; } catch {}
    if (Date.now() >= until) throw new Error(`timed out after ${ms}ms waiting for ${what}`);
    await sleep(100);
  }
}

function collect(proc) {
  const state = {stdout: "", stderr: ""};
  proc.stdout?.on("data", d => { state.stdout += d; });
  proc.stderr?.on("data", d => { state.stderr += d; });
  return state;
}

async function stop(proc) {
  if (!proc || proc.exitCode !== null || proc.signalCode !== null) return;
  if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(proc.pid), "/T", "/F"], {stdio: "ignore"});
  else { try { process.kill(-proc.pid, "SIGKILL"); } catch { proc.kill("SIGKILL"); } }
  await Promise.race([new Promise(r => proc.once("exit", r)), sleep(3000)]);
}

async function startServer(dir) {
  const port = await freePort();
  const env = {...process.env, DURIAN_DB: path.join(dir, "durian.db"), DURIAN_PORT: String(port),
               DURIAN_ADMIN_PASSWORD: ADMIN_PW, DURIAN_MQTT_CREDS: path.join(dir, "no-lab-creds.json")};
  const proc = spawn(PYTHON, ["-m", "durian.server"], {cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"],
                                                     detached: process.platform !== "win32"});
  const log = collect(proc);
  const base = `http://127.0.0.1:${port}`;
  await waitFor(`the server on port ${port}`, async () => {
    if (proc.exitCode !== null) throw new Error(`server exited (${proc.exitCode}): ${log.stderr || log.stdout}`);
    return (await fetch(base + "/")).ok;
  });
  return {proc, base, log};
}

async function startSimulator(dir) {
  const proc = spawn(PYTHON, ["-m", "durian.modbus_sim", "tcp", "1502"], {cwd: ROOT, stdio: ["ignore", "pipe", "pipe"],
                                                                         detached: process.platform !== "win32"});
  const log = collect(proc);
  await waitFor("the Modbus simulator on port 1502", async () => {
    if (proc.exitCode !== null) throw new Error(`simulator exited (${proc.exitCode}): ${log.stderr || log.stdout}`);
    return new Promise(resolve => {
      const socket = net.connect(1502, "127.0.0.1");
      socket.once("connect", () => { socket.destroy(); resolve(true); });
      socket.once("error", () => resolve(false));
    });
  });
  return {proc, log};
}

async function startBrowser(dir) {
  const exe = webPath();
  const proc = spawn(exe, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", "--disable-component-update", "--disable-sync", "--disable-extensions",
    "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=9222",
    "--user-data-dir=" + path.join(dir, "profile"), "about:blank"],
    {stdio: ["ignore", "pipe", "pipe"], detached: process.platform !== "win32"});
  const log = collect(proc);
  await waitFor("Chrome DevTools Protocol on 9222", async () => {
    if (proc.exitCode !== null) throw new Error(`browser exited (${proc.exitCode}): ${log.stderr}`);
    const targets = await (await fetch(CDP + "/json/list")).json();
    return targets.some(t => t.type === "page");
  });
  return {proc, log};
}

async function waitForCdpGone() {
  await waitFor("port 9222 to be released", async () => {
    try { await fetch(CDP + "/json/version"); return false; } catch { return true; }
  }, 10000);
}

function runCheck(name, out, base) {
  return new Promise(resolve => {
    const started = Date.now();
    const child = spawn(NODE, [path.join(ROOT, "tools", `browser_${name}.mjs`), out], {
      cwd: ROOT, env: {...process.env, DURIAN_BASE: base, DURIAN_ADMIN_PASSWORD: ADMIN_PW},
      stdio: ["ignore", "pipe", "pipe"],
    });
    const log = collect(child);
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; child.kill("SIGKILL"); }, TIMEOUT);
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      resolve({code, signal, timedOut, ms: Date.now() - started, ...log});
    });
  });
}

async function run(name) {
  const dir = mkdtempSync(path.join(os.tmpdir(), `durian-${name}-`));
  const out = path.join(dir, "screens");
  let server, browser, simulator, keep = false;
  try {
    server = await startServer(dir);
    if (CHECKS[name].simulator === "modbus") simulator = await startSimulator(dir);
    browser = await startBrowser(dir);
    const result = await runCheck(name, out, server.base);
    if (result.code !== 0 || result.timedOut) {
      keep = true;
      const why = result.timedOut ? `timed out after ${TIMEOUT}ms` : `exited ${result.code}${result.signal ? " (" + result.signal + ")" : ""}`;
      const detail = [
        `browser_${name}.mjs ${why} after ${result.ms}ms`,
        "--- stdout ---", result.stdout.trim() || "(empty)",
        "--- stderr ---", result.stderr.trim() || "(empty)",
        server.log.stderr ? "--- server stderr ---\n" + server.log.stderr.trim() : "",
        `--- artifacts: ${dir}`,
      ].join("\n");
      return {name, ok: false, detail};
    }
    if (process.env.DURIAN_BROWSER_VERBOSE) console.log(`\n${result.stdout.trim()}`);
    if (result.stderr.trim()) console.log(`[${name}] stderr:\n${result.stderr.trim()}`);
    return {name, ok: true, ms: result.ms};
  } catch (ex) {
    keep = true;
    return {name, ok: false, detail: `browser_${name}.mjs could not start: ${ex.message}\n--- artifacts: ${dir}`};
  } finally {
    await stop(browser?.proc);
    if (browser) {
      try { await waitForCdpGone(); } catch (ex) { console.error(`[${name}] cleanup: ${ex.message}`); }
    }
    await stop(simulator?.proc);
    await stop(server?.proc);
    if (!keep) try { rmSync(dir, {recursive: true, force: true}); } catch {}
  }
}

try { await fetch(CDP + "/json/version"); fail(`port 9222 is already serving CDP; stop that browser before running the checks`); } catch {}

const results = [];
for (const name of names) {
  process.stdout.write(`[${name}] `);
  const r = await run(name);
  results.push(r);
  if (r.ok) console.log(`PASS (${(r.ms / 1000).toFixed(1)}s)`);
  else { console.log("FAIL"); console.log(r.detail); }
}

const bad = results.filter(r => !r.ok);
console.log(`\n${results.length - bad.length}/${results.length} browser checks passed`);
if (bad.length) {
  fail(bad.map(r => r.name).join(", ") + " failed");
}
