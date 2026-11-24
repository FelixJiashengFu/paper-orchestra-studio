import { createServer } from "node:http";
import { randomBytes, randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { mkdir, open, readFile } from "node:fs/promises";
import { homedir, platform } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { createConnection } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { tools, validate } from "./vendor/tools.mjs";

const APP_ID = "com.paperorchestra.studio";
const BRIDGE_VERSION = 1;
const MAX_FRAME = 4 * 1024 * 1024;
const POLL_INTERVAL = 4000;
const PANEL_PORT = Number.parseInt(process.env.PAPER_ORCHESTRA_PANEL_PORT || "63584", 10);
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = join(HERE, "public");
const toolByName = new Map(tools.map((tool) => [tool.name, tool]));
const panelReadActions = new Set(tools.filter((tool) => tool.annotations.readOnlyHint && !tool.background && !tool.local).map((tool) => tool.name));
const panelReadJobIds = new Set();
const logStates = new Map();
let panelToken;
const clients = new Set();
let endpointCache;
let latestSnapshot;
let polling = false;
let lastSnapshotJson = "";

function dataDir() {
  const override = process.env.PAPER_ORCHESTRA_STUDIO_DATA_DIR;
  if (override) return resolve(override);
  if (platform() === "darwin") return join(homedir(), "Library", "Application Support", APP_ID);
  if (platform() === "win32") {
    return join(process.env.LOCALAPPDATA || join(homedir(), "AppData", "Local"), APP_ID);
  }
  throw new Error("PaperOrchestra Studio bridge supports macOS and Windows only.");
}

async function readPanelToken() {
  const file = await open(join(dataDir(), "control-panel-session.json"), constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const info = await file.stat();
    if (!info.isFile() || info.size > 4096 || (process.getuid && (info.uid !== process.getuid() || (info.mode & 0o077) !== 0))) {
      throw new Error("Control panel session file is not private to the current user.");
    }
    const session = JSON.parse(await file.readFile({ encoding: "utf8" }));
    if (session.version !== 1 || session.port !== PANEL_PORT || !/^[0-9a-f]{48}$/.test(session.token || "")) {
      throw new Error("Control panel session file is invalid.");
    }
    return session.token;
  } finally {
    await file.close();
  }
}

async function ensurePanelToken() {
  try {
    return await readPanelToken();
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }

  await mkdir(dataDir(), { recursive: true });
  const token = randomBytes(24).toString("hex");
  const sessionPath = join(dataDir(), "control-panel-session.json");
  try {
    const file = await open(
      sessionPath,
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0),
      0o600
    );
    try {
      await file.writeFile(JSON.stringify({ version: 1, port: PANEL_PORT, token }), "utf8");
    } finally {
      await file.close();
    }
    return token;
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    return readPanelToken();
  }
}

function panelUrl() {
  return `http://127.0.0.1:${PANEL_PORT}/?panel=1#key=${panelToken}`;
}

function openPanelUrl(url) {
  if (platform() === "win32") spawn("cmd.exe", ["/c", "start", "", url], { detached: true, stdio: "ignore", windowsHide: true }).unref();
  else spawn("open", [url], { detached: true, stdio: "ignore" }).unref();
}

async function openExistingPanel() {
  const token = await readPanelToken();
  const base = `http://127.0.0.1:${PANEL_PORT}`;
  const pairResponse = await fetch(`${base}/api/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token })
  });
  if (!pairResponse.ok) throw new Error("The service on the panel port is not this control panel.");
  const setCookie = pairResponse.headers.get("set-cookie") || "";
  const cookie = setCookie.match(/po_session=[0-9a-f]+/)?.[0];
  if (!cookie) throw new Error("The running control panel did not create a local session.");
  const snapshotResponse = await fetch(`${base}/api/snapshot`, { headers: { Cookie: cookie } });
  if (!snapshotResponse.ok) throw new Error("The running control panel rejected its saved local session.");
  openPanelUrl(panelUrl());
}

async function readEndpoint() {
  const file = await open(join(dataDir(), "codex-bridge.json"), constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const info = await file.stat();
    if (!info.isFile() || info.size > 4096 || (process.getuid && (info.uid !== process.getuid() || (info.mode & 0o077) !== 0))) {
      throw new Error("Studio bridge file is not private to the current user.");
    }
    const endpoint = JSON.parse(await file.readFile({ encoding: "utf8" }));
    if (endpoint.version !== BRIDGE_VERSION) throw new Error("Studio and panel bridge versions do not match.");
    if (!Number.isInteger(endpoint.port) || endpoint.port < 1 || endpoint.port > 65535 || !/^[0-9a-f]{64}$/.test(endpoint.token)) {
      throw new Error("Studio bridge file is invalid.");
    }
    endpointCache = endpoint;
    return endpoint;
  } finally {
    await file.close();
  }
}

function rpc(endpoint, payload) {
  return new Promise((resolvePromise, reject) => {
    const message = JSON.stringify({ ...payload, version: BRIDGE_VERSION, token: endpoint.token }) + "\n";
    if (Buffer.byteLength(message) > MAX_FRAME) {
      reject(new Error("Studio request exceeds 4 MiB."));
      return;
    }
    const socket = createConnection({ host: "127.0.0.1", port: endpoint.port });
    let size = 0;
    const chunks = [];
    socket.setTimeout(6000);
    socket.once("connect", () => socket.write(message));
    socket.once("error", reject);
    socket.once("timeout", () => socket.destroy(new Error("Studio bridge did not respond.")));
    socket.once("end", () => reject(new Error("Studio closed the bridge connection.")));
    socket.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_FRAME) {
        socket.destroy(new Error("Studio response exceeds 4 MiB."));
        return;
      }
      chunks.push(chunk);
      if (!chunk.includes(10)) return;
      socket.destroy();
      try {
        const response = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        if (response.error) reject(new Error(response.error));
        else resolvePromise(response.result);
      } catch {
        reject(new Error("Studio bridge returned invalid JSON."));
      }
    });
  });
}

async function bridgeStatus() {
  try {
    const endpoint = endpointCache ?? (await readEndpoint());
    const reply = await rpc(endpoint, { method: "ping" });
    return { connected: reply?.connected === true, bridgeVersion: BRIDGE_VERSION, port: endpoint.port };
  } catch (error) {
    endpointCache = undefined;
    return { connected: false, detail: error.message };
  }
}

async function bridgeRpc(method, args = {}) {
  const endpoint = endpointCache ?? (await readEndpoint());
  return rpc(endpoint, { ...args, method });
}

async function waitForJob(job, timeoutMs = 12000) {
  if (!job?.jobId) return job;
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const current = await bridgeRpc("job", { jobId: job.jobId });
    if (current.state === "succeeded") return current.result;
    if (current.state === "failed") throw new Error(current.error || "Studio job failed.");
    if (current.state === "cancelled") return current;
    await delay(150);
  }
  return job;
}

async function invokeTool(name, args = {}, options = {}) {
  const tool = toolByName.get(name);
  if (!tool) throw new Error(`Unknown tool: ${name}`);
  validate(args, tool.inputSchema);
  const mapped = tool.args(args);

  if (tool.local) {
    if (name === "studio_status") return bridgeStatus();
    if (name === "list_jobs") return bridgeRpc("list_jobs");
    if (name === "job_status") return bridgeRpc("job", { jobId: mapped.jobId });
    if (name === "cancel_job") return bridgeRpc("cancel", { jobId: mapped.jobId });
    if (name === "open_studio") return bridgeRpc("submit", { requestId: randomUUID(), command: "show_studio_view", args: { tab: mapped.tab ?? "workspace" } }).then(waitForJob);
    throw new Error(`Unsupported local tool: ${name}`);
  }

  const requestId = randomUUID();
  const job = await bridgeRpc("submit", { requestId, command: tool.command, args: mapped });
  if (options.panelRead && job?.jobId) {
    panelReadJobIds.add(job.jobId);
    if (panelReadJobIds.size > 1024) panelReadJobIds.delete(panelReadJobIds.values().next().value);
  }
  if (tool.background || options.background) return job;
  return waitForJob(job, options.timeoutMs ?? 12000);
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.value)) return value.value;
  if (Array.isArray(value?.items)) return value.items;
  return [];
}

function isWarningLog(entry) {
  const line = String(entry.line || "");
  if (/token usage:.*\b0 failed\b/i.test(line)) return false;
  return /warning|\berror\b|\bfailed\b|\bexception\b|no figures/i.test(line);
}

async function readRunLogTail(runId) {
  const previous = logStates.get(runId);
  let afterId = previous?.lastId || 0;
  let batch = asArray(await invokeTool("get_run_logs", { runId, afterId, limit: 200 }, { panelRead: true }));
  const recent = [...(previous?.recent || []), ...batch];
  const warnings = [...(previous?.warnings || []), ...batch.filter(isWarningLog)];
  while (batch.length === 200) {
    afterId = batch.at(-1).id;
    batch = asArray(await invokeTool("get_run_logs", { runId, afterId, limit: 200 }, { panelRead: true }));
    recent.push(...batch);
    warnings.push(...batch.filter(isWarningLog));
  }
  if (batch.length) afterId = batch.at(-1).id;
  const next = {
    lastId: afterId,
    recent: recent.slice(-500),
    warnings: warnings.slice(-6)
  };
  logStates.set(runId, next);
  if (logStates.size > 64) logStates.delete(logStates.keys().next().value);
  return { logs: next.recent.slice(-100), warnings: next.warnings };
}

function pickActiveRun(runs) {
  return runs.find((run) => run.status === "running") || runs[0] || null;
}

async function buildSnapshot() {
  const generatedAt = new Date().toISOString();
  const status = await bridgeStatus();
  if (!status.connected) {
    return { generatedAt, status, projects: [], jobs: [], runs: [], selectedRun: null, logs: [], artifacts: null, notices: ["Studio 未连接。请打开 PaperOrchestra Studio 后等待自动重连。"] };
  }

  const [projectsRaw, jobsRaw] = await Promise.allSettled([invokeTool("list_projects", {}, { panelRead: true }), invokeTool("list_jobs")]);
  const projects = projectsRaw.status === "fulfilled" ? asArray(projectsRaw.value) : [];
  const jobs = jobsRaw.status === "fulfilled"
    ? asArray(jobsRaw.value).filter((job) => !panelReadJobIds.has(job.jobId) && !panelReadActions.has(job.action)).slice(0, 16)
    : [];
  const notices = [];
  if (projectsRaw.status === "rejected") notices.push(`项目读取失败：${projectsRaw.reason.message}`);
  if (jobsRaw.status === "rejected") notices.push(`作业读取失败：${jobsRaw.reason.message}`);

  const runResults = await Promise.allSettled(
    projects.filter((project) => project.runCount > 0).map((project) => invokeTool("list_runs", { projectId: project.id }, { panelRead: true }))
  );
  const runs = runResults.flatMap((result) => result.status === "fulfilled" ? asArray(result.value) : []);
  runs.sort((a, b) => String(b.startedAt || "").localeCompare(String(a.startedAt || "")));
  const selectedRun = pickActiveRun(runs);

  let logs = [];
  let warningLines = [];
  let artifacts = null;
  if (selectedRun?.id) {
    const [logsResult, artifactsResult] = await Promise.allSettled([
      readRunLogTail(selectedRun.id),
      selectedRun.runDir ? invokeTool("get_run_artifacts_summary", { runDir: selectedRun.runDir }, { panelRead: true }) : Promise.resolve(null)
    ]);
    if (logsResult.status === "fulfilled") {
      logs = logsResult.value.logs;
      warningLines = logsResult.value.warnings;
    } else notices.push(`日志读取失败：${logsResult.reason.message}`);
    if (artifactsResult.status === "fulfilled") artifacts = artifactsResult.value;
  }

  return { generatedAt, status, projects, jobs, runs, selectedRun, logs, artifacts, notices, warningLines };
}

function sseSend(client, event, data) {
  client.write(`event: ${event}\n`);
  client.write(`data: ${JSON.stringify(data)}\n\n`);
}

async function refreshSnapshot(force = false) {
  if (polling) return latestSnapshot;
  polling = true;
  try {
    const snapshot = await buildSnapshot();
    const json = JSON.stringify(snapshot);
    latestSnapshot = snapshot;
    if (force || json !== lastSnapshotJson) {
      lastSnapshotJson = json;
      for (const client of clients) sseSend(client, "snapshot", snapshot);
    }
    return snapshot;
  } catch (error) {
    const fallback = { generatedAt: new Date().toISOString(), status: { connected: false, detail: error.message }, projects: [], jobs: [], runs: [], selectedRun: null, logs: [], artifacts: null, notices: [error.message] };
    latestSnapshot = fallback;
    for (const client of clients) sseSend(client, "snapshot", fallback);
    return fallback;
  } finally {
    polling = false;
  }
}

function sendJson(res, status, value) {
  const body = JSON.stringify(value);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff"
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolvePromise, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > 2 * 1024 * 1024) {
        req.destroy(new Error("Request body exceeds 2 MiB."));
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      try {
        resolvePromise(JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"));
      } catch {
        reject(new Error("Invalid JSON body."));
      }
    });
    req.on("error", reject);
  });
}

function cookieToken(req) {
  const cookie = req.headers.cookie || "";
  const match = cookie.match(/(?:^|;\s*)po_session=([0-9a-f]+)/);
  return match?.[1];
}

function authorized(req, write = false) {
  if (cookieToken(req) !== panelToken) return false;
  if (write && req.headers["x-panel-token"] !== panelToken) return false;
  return true;
}

function validOrigin(req, port) {
  const origin = req.headers.origin;
  if (!origin) return true;
  try {
    const url = new URL(origin);
    return (url.hostname === "127.0.0.1" || url.hostname === "localhost") && url.port === String(port);
  } catch {
    return false;
  }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "127.0.0.1"}`);
  const port = server.address()?.port;

  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");

  if (url.pathname === "/" || url.pathname === "/index.html") {
    try {
      const html = await readFile(join(PUBLIC_DIR, "index.html"));
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
      res.end(html);
    } catch {
      sendJson(res, 500, { error: "Panel UI is missing." });
    }
    return;
  }

  if (url.pathname === "/api/pair" && req.method === "POST") {
    if (!validOrigin(req, port)) return sendJson(res, 403, { error: "Invalid origin." });
    try {
      const body = await readBody(req);
      if (body.token !== panelToken) return sendJson(res, 403, { error: "Invalid panel key." });
      res.writeHead(200, {
        "Content-Type": "application/json; charset=utf-8",
        "Set-Cookie": `po_session=${panelToken}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200`,
        "Cache-Control": "no-store"
      });
      res.end(JSON.stringify({ ok: true }));
    } catch (error) {
      sendJson(res, 400, { error: error.message });
    }
    return;
  }

  if (!authorized(req, req.method !== "GET")) return sendJson(res, 401, { error: "Panel is not paired. Open the panel using the keyed URL." });
  if (!validOrigin(req, port)) return sendJson(res, 403, { error: "Invalid origin." });

  if (url.pathname === "/api/catalogue" && req.method === "GET") {
    return sendJson(res, 200, tools.map(({ name, description, inputSchema, annotations, background, local, command }) => ({ name, description, inputSchema, annotations, background, local, command })));
  }

  if (url.pathname === "/api/snapshot" && req.method === "GET") {
    return sendJson(res, 200, await refreshSnapshot());
  }

  if (url.pathname === "/api/run-logs" && req.method === "GET") {
    const runId = url.searchParams.get("runId");
    if (!runId || !/^[0-9a-fA-F-]{36}$/.test(runId)) return sendJson(res, 400, { error: "Invalid runId." });
    try {
      const result = await readRunLogTail(runId);
      return sendJson(res, 200, result);
    } catch (error) {
      return sendJson(res, 400, { error: error.message });
    }
  }

  if (url.pathname === "/api/events" && req.method === "GET") {
    res.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no"
    });
    res.write("retry: 3000\n\n");
    clients.add(res);
    if (latestSnapshot) sseSend(res, "snapshot", latestSnapshot);
    const heartbeat = setInterval(() => res.write(": heartbeat\n\n"), 15000);
    req.on("close", () => {
      clearInterval(heartbeat);
      clients.delete(res);
    });
    return;
  }

  if (url.pathname === "/api/tool" && req.method === "POST") {
    try {
      const body = await readBody(req);
      const tool = toolByName.get(body.name);
      if (!tool) return sendJson(res, 400, { error: "Unknown tool." });
      const readOnly = tool.annotations?.readOnlyHint === true;
      if (!readOnly && body.confirmed !== true) return sendJson(res, 400, { error: "This action needs explicit confirmation in the panel." });
      if (tool.annotations?.destructiveHint === true && body.confirmation !== tool.name) {
        return sendJson(res, 400, { error: `Type ${tool.name} to confirm this destructive action.` });
      }
      const result = await invokeTool(tool.name, body.args ?? {}, { background: body.background === true });
      await refreshSnapshot(true);
      return sendJson(res, 200, { result });
    } catch (error) {
      return sendJson(res, 400, { error: error.message });
    }
  }

  if (url.pathname === "/api/open-studio" && req.method === "POST") {
    try {
      const result = await invokeTool("open_studio", { tab: "workspace" });
      return sendJson(res, 200, { result });
    } catch (error) {
      return sendJson(res, 400, { error: error.message });
    }
  }

  return sendJson(res, 404, { error: "Not found." });
});

async function startPanel() {
  if (!Number.isInteger(PANEL_PORT) || PANEL_PORT < 1 || PANEL_PORT > 65535) {
    throw new Error("PAPER_ORCHESTRA_PANEL_PORT must be an integer from 1 to 65535.");
  }
  panelToken = await ensurePanelToken();
  server.listen(PANEL_PORT, "127.0.0.1", async () => {
    const url = panelUrl();
    console.log(`PaperOrchestra control panel: ${url}`);
    await refreshSnapshot(true);
    setInterval(() => { void refreshSnapshot(); }, POLL_INTERVAL);
    if (process.argv.includes("--open")) openPanelUrl(url);
  });
}

server.on("error", (error) => {
  if (error.code === "EADDRINUSE") {
    void (async () => {
      try {
        await openExistingPanel();
        console.log(`PaperOrchestra control panel is already running on port ${PANEL_PORT}; opened a fresh paired tab.`);
        process.exit(0);
      } catch (detail) {
        console.error(`Panel port ${PANEL_PORT} is occupied by another service: ${detail.message}`);
        process.exit(1);
      }
    })();
    return;
  }
  console.error(`PaperOrchestra control panel failed to start: ${error.message}`);
  process.exit(1);
});

startPanel().catch((error) => {
  console.error(`PaperOrchestra control panel failed to start: ${error.message}`);
  process.exit(1);
});

function shutdown() {
  for (const client of clients) client.end();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1000).unref();
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
