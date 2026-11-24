import { execFile, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { access, open } from "node:fs/promises";
import { createConnection } from "node:net";
import { homedir, platform } from "node:os";
import { isAbsolute, join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { promisify } from "node:util";

const APP_ID = "com.paperorchestra.studio";
const VERSION = 1;
const MAX_FRAME = 4 * 1024 * 1024;
const macApp = "/Applications/PaperOrchestra Studio.app";
const windowsExe = "paper-orchestra-desktop.exe";
const exec = promisify(execFile);
let launchInProgress;

function dataDir() {
  const override = process.env.PAPER_ORCHESTRA_STUDIO_DATA_DIR;
  if (override) {
    if (!isAbsolute(override)) throw new Error("PAPER_ORCHESTRA_STUDIO_DATA_DIR must be absolute.");
    return override;
  }
  if (platform() === "darwin") return join(homedir(), "Library", "Application Support", APP_ID);
  if (platform() === "win32") {
    return join(process.env.LOCALAPPDATA || join(homedir(), "AppData", "Local"), APP_ID);
  }
  throw new Error("This Studio integration supports local macOS and Windows installations.");
}

async function readEndpoint() {
  const file = await open(join(dataDir(), "codex-bridge.json"), constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const info = await file.stat();
    if (!info.isFile() || info.size > 4096 || (process.getuid && (info.uid !== process.getuid() || (info.mode & 0o077) !== 0))) {
      throw new Error("Studio's bridge file is not private to the current user.");
    }
    const endpoint = JSON.parse(await file.readFile({ encoding: "utf8" }));
    if (endpoint.version !== VERSION) throw new Error("The Studio app and plugin require matching bridge versions.");
    if (!Number.isInteger(endpoint.port) || endpoint.port < 1 || endpoint.port > 65535 || !/^[0-9a-f]{64}$/.test(endpoint.token)) {
      throw new Error("Studio's bridge file is invalid.");
    }
    return endpoint;
  } finally {
    await file.close();
  }
}

function rpc(endpoint, payload) {
  return new Promise((resolve, reject) => {
    const message = JSON.stringify({ ...payload, version: VERSION, token: endpoint.token }) + "\n";
    if (Buffer.byteLength(message) > MAX_FRAME) {
      reject(new Error("The Studio request exceeds 4 MiB."));
      return;
    }
    const socket = createConnection({ host: "127.0.0.1", port: endpoint.port });
    let size = 0;
    const chunks = [];
    socket.setTimeout(4500);
    socket.once("connect", () => socket.write(message));
    socket.once("error", reject);
    socket.once("timeout", () => socket.destroy(new Error("Studio's bridge did not respond.")));
    socket.once("end", () => {
      reject(new Error("Studio closed the local bridge connection."));
    });
    socket.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_FRAME) {
        socket.destroy(new Error("Studio's response exceeds 4 MiB."));
        return;
      }
      chunks.push(chunk);
      if (!chunk.includes(10)) return;
      socket.destroy();
      try {
        const response = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        if (response.error) reject(new Error(response.error));
        else resolve(response.result);
      } catch {
        reject(new Error("Studio's bridge returned invalid JSON."));
      }
    });
  });
}

async function reachableEndpoint() {
  const endpoint = await readEndpoint();
  const reply = await rpc(endpoint, { method: "ping" });
  if (reply?.connected !== true) throw new Error("Studio's bridge sent an invalid handshake.");
  return endpoint;
}

async function launchStudio() {
  if (process.env.PAPER_ORCHESTRA_STUDIO_DATA_DIR) {
    throw new Error("Start Studio with the same PAPER_ORCHESTRA_STUDIO_DATA_DIR before calling the plugin.");
  }
  const explicit = process.env.PAPER_ORCHESTRA_STUDIO_APP;
  if (explicit && !isAbsolute(explicit)) throw new Error("PAPER_ORCHESTRA_STUDIO_APP must be absolute.");
  if (platform() === "darwin") {
    if (explicit) await exec("/usr/bin/open", ["-g", "-a", explicit]);
    else {
      try {
        await access(macApp);
      } catch {
        await exec("/usr/bin/open", ["-g", "-b", APP_ID]);
        return;
      }
      await exec("/usr/bin/open", ["-g", "-a", macApp]);
    }
    return;
  }
  if (platform() !== "win32") throw new Error("Studio requires macOS or Windows.");
  const { stdout } = await exec("tasklist.exe", ["/fo", "csv", "/nh", "/fi", `imagename eq ${windowsExe}`], { windowsHide: true });
  if (stdout.toLowerCase().includes(`"${windowsExe}"`)) return;
  const bases = [process.env.LOCALAPPDATA, process.env.LOCALAPPDATA && join(process.env.LOCALAPPDATA, "Programs"),
    process.env.ProgramFiles, process.env["ProgramFiles(x86)"]].filter(Boolean);
  const candidates = explicit ? [explicit] : bases.map((base) => join(base, "PaperOrchestra Studio", windowsExe));
  for (const app of candidates) {
    try {
      await access(app);
    } catch {
      continue;
    }
    await new Promise((resolve, reject) => {
      const child = spawn(app, [], { detached: true, stdio: "ignore", windowsHide: true });
      child.once("error", reject);
      child.once("spawn", () => {
        child.unref();
        resolve();
      });
    });
    return;
  }
  throw new Error("Start the updated PaperOrchestra Studio app or set PAPER_ORCHESTRA_STUDIO_APP to its executable.");
}

async function connect() {
  try {
    return await reachableEndpoint();
  } catch {
    // LaunchServices reuses the current app on macOS; Windows checks for the Studio process first.
  }
  if (!launchInProgress) {
    launchInProgress = (async () => {
      try {
        await launchStudio();
      } catch (error) {
        throw new Error(`Studio could not be started. Open the plugin-compatible Studio app. ${error.code ?? error.message}`);
      }
      const end = Date.now() + 6500;
      do {
        try {
          return await reachableEndpoint();
        } catch {
          await delay(200);
        }
      } while (Date.now() < end);
      throw new Error("Studio's local bridge is unavailable. Open the Studio build that includes the Codex bridge.");
    })().finally(() => { launchInProgress = undefined; });
  }
  return launchInProgress;
}

export async function status() {
  try {
    return await rpc(await readEndpoint(), { method: "ping" });
  } catch (error) {
    return {
      connected: false,
      detail: ["ENOENT", "ECONNREFUSED", "EACCES"].includes(error.code)
        ? "Open the plugin-compatible PaperOrchestra Studio app on this machine."
        : error.message,
    };
  }
}

export async function submit(command, args, background) {
  const endpoint = await connect();
  const requestId = randomUUID();
  const job = await rpc(endpoint, { method: "submit", requestId, command, args }).catch((error) => {
    throw new Error(`${error.message} Studio request ID: ${requestId}. Check job_status before retrying.`);
  });
  if (background) return job;
  const end = Date.now() + 4000;
  do {
    const current = await rpc(endpoint, { method: "job", jobId: job.jobId }).catch((error) => {
      throw new Error(`${error.message} Studio job ID: ${job.jobId}.`);
    });
    if (current.state === "succeeded") return current.result;
    if (current.state === "failed") throw new Error(current.error);
    await delay(150);
  } while (Date.now() < end);
  return job;
}

export function jobs(method, args) {
  // A status request for an old job should not relaunch Studio after a shutdown.
  return reachableEndpoint().then(
    (endpoint) => rpc(endpoint, { ...args, method }),
    () => { throw new Error("Studio is unavailable. Pipeline history is available via list_runs after reopening Studio."); },
  );
}
