"use strict";
// Command palette: Ctrl/Cmd+K (or the header button) opens a modal search over pages, points, dashboards and actions.
// A native <dialog> gives the focus trap, Escape and focus return; this file adds the combobox/listbox behaviour and ranking.
// app.js calls initPalette(ctx) once: ctx = {api, esc, store, me, pages, toast, logout, theme, refreshAlarmCount}.

// Rank `text` against `q`: prefix > word start > substring > subsequence. Returns {score, hits} (hits = matched indexes) or null.
function paletteMatch(q, text) {
  const t = text.toLowerCase();
  if (!q) return {score: 0, hits: []};
  const run = (at, n) => ({hits: Array.from({length: n}, (_, i) => at + i)});
  if (t.startsWith(q)) return {score: 4000 - t.length, ...run(0, q.length)};
  const w = t.search(new RegExp("[\\s/:_.-]" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  if (w >= 0) return {score: 3000 - t.length, ...run(w + 1, q.length)};
  const s = t.indexOf(q);
  if (s >= 0) return {score: 2000 - s - t.length, ...run(s, q.length)};
  const hits = []; let from = 0;
  for (const c of q) { const i = t.indexOf(c, from); if (i < 0) return null; hits.push(i); from = i + 1; }
  return {score: 1000 - (hits.at(-1) - hits[0]) - t.length, hits};
}

function initPalette(ctx) {
  const {api, esc, store} = ctx, RECENT = "durian.palette.recent", CAP = 50, STALE = 60000;
  let dlg = null, input, list, opener, items = [], shown = [], active = 0;
  const cache = {points: null, dashes: null, at: 0, failed: false, loading: null};

  const recent = () => { try { return JSON.parse(store.get(RECENT)) || []; } catch { return []; } };
  const remember = id => store.set(RECENT, JSON.stringify([id, ...recent().filter(x => x !== id)].slice(0, 6)));

  function build() {
    dlg = document.createElement("dialog");
    dlg.id = "cp";
    dlg.setAttribute("role", "dialog");
    dlg.setAttribute("aria-modal", "true");
    dlg.setAttribute("aria-label", "Command palette");
    dlg.innerHTML = `<input id="cp-in" type="text" role="combobox" aria-expanded="true" aria-controls="cp-list" aria-autocomplete="list" aria-label="Search pages, points, dashboards and actions" placeholder="Search pages, points, dashboards, actions" autocomplete="off" spellcheck="false">
      <div id="cp-list" role="listbox" aria-label="Results"></div><p id="cp-note" class="note" role="status"></p>`;
    document.body.append(dlg);
    input = dlg.querySelector("#cp-in"); list = dlg.querySelector("#cp-list");
    input.addEventListener("input", draw);
    input.addEventListener("keydown", ev => {
      const n = shown.length;
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") { ev.preventDefault(); if (n) move((active + (ev.key === "ArrowDown" ? 1 : n - 1)) % n); }
      else if (ev.key === "Enter") { ev.preventDefault(); if (shown[active]) pick(shown[active]); }
    });
    list.addEventListener("click", ev => { const o = ev.target.closest("[role=option]"); if (o) pick(shown[+o.dataset.i]); });
    list.addEventListener("mousemove", ev => { const o = ev.target.closest("[role=option]"); if (o && +o.dataset.i !== active) move(+o.dataset.i, true); });
    dlg.addEventListener("mousedown", ev => { if (ev.target === dlg) dlg.close(); });  // a click on the backdrop
    dlg.addEventListener("close", () => { dlg.classList.remove("in"); if (opener?.isConnected) opener.focus(); });
  }

  function move(i, keepScroll) {
    active = i;
    for (const o of list.querySelectorAll("[role=option]")) o.setAttribute("aria-selected", String(+o.dataset.i === i));
    const o = list.querySelector(`[data-i="${i}"]`);
    input.setAttribute("aria-activedescendant", o ? o.id : "");
    if (o && !keepScroll) o.scrollIntoView({block: "nearest"});
  }

  function pick(it) {
    remember(it.id);
    dlg.close();
    it.run();
  }

  function actions() {
    const A = [{id: "a:theme", label: "Toggle dark / light theme", run: ctx.theme},
      {id: "a:account", label: "Open Account and security", run: () => { location.hash = "#account"; }},
      {id: "a:logout", label: "Log out", run: ctx.logout}];
    if (document.querySelector("#collapse")) A.push({id: "a:collapse", label: "Collapse or expand the sidebar", run: () => document.querySelector("#collapse")?.click()});
    if (ctx.me().roles.includes("user")) {
      A.push({id: "a:unacked", label: "Show unacknowledged alarms", run: async () => {
        location.hash = "#alarms";
        for (let i = 0; i < 30 && !document.querySelector("#aj"); i++) await new Promise(r => setTimeout(r, 100));
        const s = document.querySelector("#aj select[name=state]");
        if (s) { s.value = "unacked"; s.dispatchEvent(new Event("change", {bubbles: true})); }
      }});
      A.push({id: "a:ackall", label: "Acknowledge all alarms", run: async () => {
        try {
          const ids = (await api("GET", "/api/events")).filter(e => e.acked_ts == null && e.state !== "closed").map(e => e.id);
          if (!ids.length) return ctx.toast("No alarms are waiting for acknowledgement.");
          await api("POST", "/api/events/ack-many", {ids});
          ctx.toast(`${ids.length} alarm${ids.length === 1 ? "" : "s"} acknowledged.`);
          ctx.refreshAlarmCount();
        } catch (e) { ctx.toast(e.message, true); }
      }});
    }
    return A.map(a => ({group: "Actions", ...a}));
  }

  function collect() {
    const go = h => () => { location.hash = h; };
    items = [
      ...ctx.pages().map(([h, l]) => ({group: "Pages", id: "p:" + h, label: l, run: go("#" + h)})),
      ...(cache.dashes || []).map(d => ({group: "Dashboards", id: "d:" + d.name, label: d.title || d.name, sub: d.name, run: go("#d/" + encodeURIComponent(d.name))})),
      ...actions(),
      ...(cache.points || []).map(p => ({group: "Points", id: "pt:" + p.name, label: p.name,
        sub: [p.folder, ...Object.entries(p.tags || {}).map(([k, v]) => k + ":" + v)].filter(Boolean).join("  "), run: go("#point/" + encodeURIComponent(p.name))})),
    ];
  }

  const mark = (text, hits) => { const set = new Set(hits); return [...text].map((c, i) => set.has(i) ? `<mark>${esc(c)}</mark>` : esc(c)).join(""); };

  function rank(q) {
    if (!q) {
      const r = recent(), byId = new Map(items.map(i => [i.id, i]));
      const rec = r.map(id => byId.get(id)).filter(Boolean).map(i => ({...i, group: "Recent", hits: [], sh: []}));
      return [...rec, ...items.filter(i => i.group !== "Points" && !r.includes(i.id)).map(i => ({...i, hits: [], sh: []}))];
    }
    const out = [];
    for (const i of items) {
      const m = paletteMatch(q, i.label), s = i.sub ? paletteMatch(q, i.sub) : null;
      if (m || s) out.push({...i, score: m ? m.score : s.score - 500, hits: m ? m.hits : [], sh: s && !m ? s.hits : []});
    }
    out.sort((a, b) => b.score - a.score);
    let pts = 0;  // the cap applies to points only; pages and actions are few
    return out.filter(i => i.group !== "Points" || ++pts <= CAP);
  }

  function draw() {
    const q = input.value.trim().toLowerCase();
    const rows = rank(q), order = ["Recent", "Pages", "Dashboards", "Actions", "Points"];
    shown = order.flatMap(g => rows.filter(r => r.group === g));
    list.innerHTML = order.map(g => {
      const mine = shown.map((r, i) => [r, i]).filter(([r]) => r.group === g);
      return mine.length ? `<div role="group" aria-labelledby="cp-g-${g}"><p class="cp-g" id="cp-g-${g}">${g}</p>` + mine.map(([r, i]) =>
        `<div role="option" id="cp-o-${i}" data-i="${i}" aria-selected="false"><span>${mark(r.label, r.hits)}</span>${r.sub ? `<small>${mark(r.sub, r.sh)}</small>` : ""}</div>`).join("") + "</div>" : "";
    }).join("") || `<p class="note cp-none">Nothing matches "${esc(input.value.trim())}".</p>`;
    const note = dlg.querySelector("#cp-note");
    note.textContent = cache.failed ? "Points could not be loaded; pages and actions still work." : "";
    note.classList.toggle("cp-warn", cache.failed);
    move(0, true);
  }

  // Points and dashboards load on first open (and again when stale); the palette is usable while they arrive.
  function load() {
    if (cache.loading || Date.now() - cache.at < STALE) return;
    cache.loading = Promise.all([api("GET", "/api/points").catch(() => null), api("GET", "/api/dashboards").catch(() => null)]).then(([p, d]) => {
      cache.failed = p === null; if (p) cache.points = p; if (d) cache.dashes = d; cache.at = Date.now(); cache.loading = null;
      if (dlg?.open) { collect(); draw(); }
    });
  }

  function open() {
    if (!ctx.me().name) return;
    if (!dlg) build();
    if (dlg.open) return dlg.close();
    opener = document.activeElement;
    collect(); input.value = ""; draw();
    dlg.showModal();
    dlg.getBoundingClientRect();  // commit the start state so the 120 ms fade runs
    dlg.classList.add("in");
    input.focus();
    load();
  }

  // Only Ctrl/Cmd+K is global; it works inside inputs too, and plain keys are never touched.
  document.addEventListener("keydown", ev => {
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && !ev.shiftKey && ev.key.toLowerCase() === "k") { ev.preventDefault(); open(); }
  });
  document.querySelector("#cmdk").addEventListener("click", open);
}
