// Step 2 of scripts/demo/README.md: walk every page on the seeded server, record each GET /api answer and the chain feed.
// Usage: node scripts/demo/record.js raw.json   (needs Playwright)
const { chromium } = require("playwright");
const fs = require("fs");
const BASE = "http://127.0.0.1:8766/terminal/";
const TOKEN = "t".repeat(24);
const OUT = process.argv[2];

(async () => {
  const b = await chromium.launch();
  const get = {};
  const ws = [];
  const errors = [];
  const pending = new Set();
  async function newPage(viewport) {
    const ctx = await b.newContext({ viewport });
    await ctx.addInitScript((t) => sessionStorage.setItem("cs-token", t), TOKEN);
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    p.on("response", (r) => {
      const u = new URL(r.url());
      if (!u.pathname.startsWith("/api/") || r.request().method() !== "GET" || u.pathname === "/api/wf/stream") return;
      const key = u.pathname + u.search;
      const job = r.json().then((j) => { if (r.ok()) get[key] = j; }).catch(() => {});
      pending.add(job); job.finally(() => pending.delete(job));
    });
    p.on("websocket", (s) => s.on("framereceived", (f) => { try { const m = JSON.parse(f.payload); if (m.type !== "pong") ws.push(m); } catch {} }));
    return p;
  }
  const p = await newPage({ width: 1440, height: 900 });
  await p.goto(BASE + "#/overview");
  await p.waitForTimeout(1500);
  const api = (path) => p.evaluate(async ([path, t]) => (await fetch(path, { headers: { Authorization: "Bearer " + t } })).json(), [path, TOKEN]);
  const agents = await api("/api/wf/agents");
  const missions = await api("/api/wf/missions");
  const squads = await api("/api/wf/squads");
  const tasks = await api("/api/wf/tasks?limit=200");
  const agentList = agents.agents || agents;
  const taskList = tasks.tasks || tasks.rows || tasks;
  const routes = ["#/terminal", "#/agents", "#/squads", "#/workspace", "#/tasks", "#/tasks?kind=goal", "#/tasks?f=review&kind=goal",
    "#/tasks?f=running&kind=goal", "#/tasks?f=running", "#/onchain", "#/onchain?tab=live", "#/onchain?tab=tx", "#/onchain?tab=registry",
    "#/onchain?tab=events", "#/onchain?tab=wallet", "#/skills", "#/activity", "#/settings", "#/more"];
  for (const a of agentList) routes.push(`#/agents/${a.id}`, `#/terminal/${a.id}`);
  for (const s of squads) routes.push(`#/squads/${s.id}`);
  for (const m of missions) {
    routes.push(`#/workspace/${m.workspace_id}?m=${m.id}`, `#/tasks/${m.id}`);
    const detail = await api(`/api/wf/missions/${m.id}`);
    for (const t of detail.subtasks || detail.tasks || []) routes.push(`#/workspace/${m.workspace_id}?m=${m.id}&t=${t.id}`, `#/tasks/${t.id}`);
  }
  for (const t of Array.isArray(taskList) ? taskList : []) routes.push(`#/tasks/${t.id}`);
  // Autonomous workers (/api/at): every page of the current navigation, each worker's tabs, each mission and task.
  routes.unshift("#/overview");
  routes.push("#/missions", "#/missions?status=running", "#/missions?status=completed,failed,stopped", "#/missions?type=scheduled&status=active,paused",
    "#/missions?tab=tasks", "#/activity", "#/activity?human=1", "#/onchain?tab=contract", "#/more");
  for (const s of ["models", "keys", "tools", "permissions", "notifications", "runtime", "billing", "audit", "legacy"]) routes.push(`#/settings?s=${s}`);
  for (const a of await api("/api/at/agents?archived=false"))
    for (const tab of ["", "work", "activity", "tools", "memory", "settings"]) routes.push(`#/agents/${a.id}${tab ? "?tab=" + tab : ""}`);
  for (const w of await api("/api/at/workspaces"))
    for (const tab of ["", "watchers", "events", "agents"]) routes.push(`#/workspace/${w.id}${tab ? "?tab=" + tab : ""}`);
  for (const m of await api("/api/at/missions?status=")) routes.push(`#/missions/${m.id}`);
  for (const t of (await api("/api/at/tasks?limit=200")).items || []) routes.push(`#/missions?task=${t.id}`);
  const seen = new Set();
  for (const r of routes) {
    if (seen.has(r)) continue;
    seen.add(r);
    await p.goto(BASE + r);
    await p.waitForTimeout(r.includes("tab=live") ? 15000 : 900);
  }
  const m = await newPage({ width: 390, height: 844 });
  for (const r of ["#/overview", "#/terminal", "#/workspace", "#/tasks", "#/onchain?tab=live", "#/agents", "#/missions", "#/more"]) {
    await m.goto(BASE + r);
    await m.waitForTimeout(900);
  }
  await Promise.all([...pending]);
  const out = { recorded_at: new Date().toISOString(), get, ws: ws.slice().reverse() };
  fs.writeFileSync(OUT, JSON.stringify(out));
  console.log("routes", seen.size, "endpoints", Object.keys(get).length, "ws", ws.length, "errors", errors);
  await b.close();
})();
