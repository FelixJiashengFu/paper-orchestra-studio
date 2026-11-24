import { createInterface } from "node:readline";
import { tools, validate } from "./tools.mjs";
import * as studio from "./studio.mjs";

const VERSION = "0.1.0";
const PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"];
const byName = new Map(tools.map((tool) => [tool.name, tool]));
const catalogue = tools.map(({ name, description, inputSchema, annotations }) => ({ name, description, inputSchema, annotations }));
const send = (message) => process.stdout.write(JSON.stringify({ jsonrpc: "2.0", ...message }) + "\n");

function toolResult(result) {
  return {
    content: [{ type: "text", text: JSON.stringify(result ?? null) }],
    structuredContent: result && !Array.isArray(result) && typeof result === "object" ? result : { value: result ?? null },
  };
}

async function callTool(params) {
  const tool = byName.get(params?.name);
  if (!tool) return { isError: true, content: [{ type: "text", text: "Unknown PaperOrchestra Studio tool." }] };
  try {
    const args = params.arguments ?? {};
    validate(args, tool.inputSchema);
    if (!tool.local) return toolResult(await studio.submit(tool.command, tool.args(args), tool.background));
    if (tool.name === "studio_status") return toolResult(await studio.status());
    if (tool.name === "open_studio") return toolResult(await studio.submit("show_studio_view", { tab: args.tab ?? "workspace" }, false));
    if (tool.name === "list_jobs") return toolResult(await studio.jobs("list_jobs", {}));
    if (tool.name === "job_status") return toolResult(await studio.jobs("job", args));
    if (tool.name === "cancel_job") return toolResult(await studio.jobs("cancel", args));
    throw new Error("Unknown local Studio action.");
  } catch (error) {
    return { isError: true, content: [{ type: "text", text: error.message }] };
  }
}

async function route(message) {
  if (message?.jsonrpc !== "2.0" || typeof message.method !== "string") {
    send({ id: message?.id ?? null, error: { code: -32600, message: "Invalid Request" } });
    return;
  }
  // MCP notifications, including notifications/initialized, do not receive a reply.
  if (!Object.hasOwn(message, "id")) return;
  const { id, method, params } = message;
  switch (method) {
    case "initialize":
      send({ id, result: {
        protocolVersion: PROTOCOL_VERSIONS.includes(params?.protocolVersion) ? params.protocolVersion : PROTOCOL_VERSIONS[0],
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "paper-orchestra-studio", version: VERSION },
        instructions: "Use the installed Studio app for its existing projects and providers. Check Studio job status before reporting completion.",
      } });
      break;
    case "ping":
      send({ id, result: {} });
      break;
    case "tools/list":
      send({ id, result: { tools: catalogue } });
      break;
    case "tools/call":
      send({ id, result: await callTool(params) });
      break;
    default:
      send({ id, error: { code: -32601, message: "Method not found" } });
  }
}

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity, terminal: false });
for await (const line of lines) {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    send({ id: null, error: { code: -32700, message: "Parse error" } });
    continue;
  }
  void route(message).catch(() => {
    if (Object.hasOwn(message ?? {}, "id")) send({ id: message.id, error: { code: -32603, message: "Internal error" } });
  });
}
