import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps";
import { z } from "zod";

export type RiskLevel = "read" | "write" | "destructive";

export interface ToolNodeMeta {
  id: string;
  tool: string;
  action?: string;
  title: string;
  group: string;
  risk: RiskLevel;
  description: string;
  required: string[];
  workflow: string;
  guardrail: string;
  exampleArgs?: Record<string, unknown>;
}

export function esc(raw: unknown): string {
  const s = String(raw ?? "");
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function showToast(msg: string, isError = false): void {
  let el = document.getElementById("po-toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "po-toast";
    el.className = "po-toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.hidden = false;
  window.clearTimeout((el as unknown as { _timer?: number })._timer);
  (el as unknown as { _timer?: number })._timer = window.setTimeout(() => {
    el!.hidden = true;
  }, 4200);
}

export function applyThemeFromHost(ctx?: McpUiHostContext): void {
  if (!ctx) return;
  if (ctx.theme) {
    applyDocumentTheme(ctx.theme);
    document.documentElement.dataset.theme = ctx.theme;
  } else if (!document.documentElement.dataset.theme) {
    document.documentElement.dataset.theme = "light";
  }
  if (ctx.styles?.variables) {
    applyHostStyleVariables(ctx.styles.variables);
  }
}

export class ServerBridge {
  private readonly app: App;
  private active = 0;
  private readonly queue: Array<() => void> = [];
  private readonly maxConcurrent = 2;

  constructor(name: string, version = "1.1.0") {
    this.app = new App({ name, version }, {});
  }

  get client(): App {
    return this.app;
  }

  onToolInput(cb: (args: Record<string, unknown>) => void): void {
    this.app.ontoolinput = (params) => {
      cb((params.arguments ?? {}) as Record<string, unknown>);
    };
  }

  onToolResult(cb: (data: Record<string, unknown>) => void): void {
    this.app.ontoolresult = (result) => {
      const parsed = this.extractPayload(result);
      if (parsed) cb(parsed);
    };
  }

  onToolCancelled(cb: (reason?: string) => void): void {
    this.app.ontoolcancelled = (params) => cb(params?.reason);
  }

  async connect(): Promise<void> {
    this.app.onhostcontextchanged = (ctx) => applyThemeFromHost(ctx);
    await this.app.connect();
    applyThemeFromHost(this.app.getHostContext());
  }

  canSendMessage(): boolean {
    return Boolean(this.app.getHostCapabilities()?.message);
  }

  canUpdateContext(): boolean {
    return Boolean(this.app.getHostCapabilities()?.updateModelContext);
  }

  hostName(): string {
    const raw = String((this.app as unknown as { getHostVersion?: () => { name?: string } }).getHostVersion?.()?.name || "");
    if (/claude/i.test(raw)) return "Claude Desktop";
    if (/antigravity/i.test(raw)) return "Antigravity";
    return raw.trim() || "MCP Host";
  }

  isAntigravityHost(): boolean {
    return this.hostName() === "Antigravity";
  }

  extractPayload(result: {
    structuredContent?: unknown;
    content?: Array<{ type?: string; text?: string }>;
    isError?: boolean;
  }): Record<string, unknown> {
    if (result.structuredContent && typeof result.structuredContent === "object") {
      const sc = result.structuredContent as Record<string, unknown>;
      if (result.isError && sc.ok !== true) {
        throw new Error(String(sc.error || "Tool returned error"));
      }
      return sc;
    }
    const textBlock = (result.content || []).find((c) => c.type === "text" && c.text);
    if (textBlock?.text) {
      const parsed = JSON.parse(textBlock.text) as Record<string, unknown>;
      if (result.isError || parsed.ok === false) {
        throw new Error(String(parsed.error || "Tool execution failed"));
      }
      return parsed;
    }
    if (result.isError) {
      throw new Error("Tool call returned an empty error response");
    }
    return {};
  }

  private async withSlot<T>(fn: () => Promise<T>): Promise<T> {
    if (this.active >= this.maxConcurrent) {
      await new Promise<void>((resolve) => this.queue.push(resolve));
    }
    this.active += 1;
    try {
      return await fn();
    } finally {
      this.active -= 1;
      const next = this.queue.shift();
      if (next) next();
    }
  }

  async callTool(name: string, args: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return this.withSlot(async () => {
      const res = await this.app.callServerTool({ name, arguments: args });
      return this.extractPayload(res as {
        structuredContent?: unknown;
        content?: Array<{ type?: string; text?: string }>;
        isError?: boolean;
      });
    });
  }

  async sendToChat(text: string): Promise<boolean> {
    if (!this.canSendMessage()) {
      await this.copyTextFallback(text, "当前视图（如侧边栏全屏模式）无对话输入通道，已复制指令文本。");
      return false;
    }
    const res = await this.app.sendMessage({
      role: "user",
      content: [{ type: "text", text }],
    });
    if (res?.isError) {
      showToast("宿主拒绝发送对话消息", true);
      return false;
    }
    showToast("已将指令发送到当前对话");
    return true;
  }

  async attachContext(text: string): Promise<boolean> {
    if (!this.canUpdateContext()) {
      await this.copyTextFallback(text, "当前视图不支持附加模型上下文，已复制文本。");
      return false;
    }
    await this.app.updateModelContext({
      content: [{ type: "text", text }],
    });
    showToast("已附加到下轮对话上下文");
    return true;
  }

  async openFileExternal(absPath: string): Promise<boolean> {
    if (!absPath) return false;
    try {
      await this.callTool("po_ui_snapshot", { action: "open_file_external", path: absPath });
      showToast("已使用系统默认应用打开文件");
      return true;
    } catch (e) {
      showToast(`打开文件失败：${String(e)}`, true);
      return false;
    }
  }

  async openFileInSidePane(
    absPath: string,
    opts?: { silent?: boolean; fallbackExternal?: boolean },
  ): Promise<boolean> {
    if (!absPath) return false;
    const fallbackExternal = opts?.fallbackExternal ?? true;
    const silent = opts?.silent ?? false;
    const isAg = this.isAntigravityHost();
    if (isAg) {
      const normalized = absPath.startsWith("/") ? absPath : `/${absPath}`;
      const uri = `file://${encodeURI(normalized)}`;
      try {
        const res = await this.app.request(
          {
            method: "com.google.antigravity/toggle-aux-pane",
            params: { tabType: "fileView", uri },
          },
          z.object({ isError: z.boolean().optional() }).passthrough(),
        );
        if (!res?.isError) {
          showToast("已在 Antigravity 侧栏打开文件");
          return true;
        }
      } catch {
        // Fall through to external / inline fallback
      }
    }
    if (fallbackExternal) {
      try {
        await this.callTool("po_ui_snapshot", { action: "open_file_external", path: absPath });
        showToast(isAg ? "当前视图无侧栏，已用系统默认应用打开" : "已用系统默认应用打开文件");
        return true;
      } catch {
        // Fall through
      }
    }
    if (!silent) {
      showToast(`文件路径：${absPath}`);
    }
    return false;
  }

  private async copyTextFallback(text: string, notice: string): Promise<void> {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        showToast(notice);
        return;
      }
    } catch {
      // Clipboard may be blocked inside opaque iframe
    }
    showToast(notice);
  }
}

export function statusPillHtml(status?: string | null): string {
  const s = String(status || "unknown").toLowerCase();
  if (["completed", "succeeded", "ok", "online", "ready"].includes(s)) {
    return `<span class="pill green"><span class="dot"></span>${esc(status || "完成")}</span>`;
  }
  if (["running", "pending", "queued", "in_progress"].includes(s)) {
    return `<span class="pill blue"><span class="dot"></span>${esc(status || "运行中")}</span>`;
  }
  if (["failed", "error", "cancelled", "stopped"].includes(s)) {
    return `<span class="pill red"><span class="dot"></span>${esc(status || "停止")}</span>`;
  }
  return `<span class="pill">${esc(status || "待启动")}</span>`;
}
