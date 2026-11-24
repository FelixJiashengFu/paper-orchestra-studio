import {
  esc,
  ServerBridge,
  showToast,
  type RiskLevel,
  type ToolNodeMeta,
} from "./shared-app";
import { TOOL_GROUPS, TOOL_NODES } from "./tool-catalog";

const bridge = new ServerBridge("PaperOrchestra Tool Tree", "1.1.0");

interface TreeState {
  search: string;
  risk: "all" | RiskLevel;
  collapsed: Set<string>;
  selectedId: string | null;
}

const state: TreeState = {
  search: "",
  risk: "all",
  collapsed: new Set<string>(),
  selectedId: null,
};

function riskPill(risk: RiskLevel): string {
  if (risk === "read") return `<span class="pill green">只读</span>`;
  if (risk === "write") return `<span class="pill amber">写入/执行</span>`;
  return `<span class="pill red">破坏性 · 需确认</span>`;
}

function visibleNodes(groupId: string): ToolNodeMeta[] {
  const q = state.search.trim().toLowerCase();
  return TOOL_NODES.filter((n) => {
    if (n.group !== groupId) return false;
    if (state.risk !== "all" && n.risk !== state.risk) return false;
    if (!q) return true;
    return [n.tool, n.action || "", n.title, n.description, n.workflow, n.guardrail]
      .join(" ")
      .toLowerCase()
      .includes(q);
  });
}

function render(): void {
  const root = document.getElementById("root")!;
  const readCnt = TOOL_NODES.filter((n) => n.risk === "read").length;
  const writeCnt = TOOL_NODES.filter((n) => n.risk === "write").length;
  const destrCnt = TOOL_NODES.filter((n) => n.risk === "destructive").length;
  const uniqueTools = new Set(TOOL_NODES.map((n) => n.tool)).size;

  const groupsHtml = TOOL_GROUPS.map((g) => {
    const nodes = visibleNodes(g.id);
    if (nodes.length === 0 && (state.search || state.risk !== "all")) return "";
    const collapsed = state.collapsed.has(g.id);
    return `
      <section class="tree-group" style="--group:${esc(g.color)};--group-soft:${esc(g.soft)}">
        <div class="tree-group-head" data-toggle-group="${esc(g.id)}">
          <div>
            <strong>${esc(g.title)}</strong>
            <span class="mono" style="margin-left:6px;color:var(--muted)">${esc(g.en)}</span>
            <div style="font-size:12px;color:var(--muted);margin-top:2px">${esc(g.summary)}</div>
          </div>
          <div class="btn-row">
            <span class="pill">${nodes.length} 节点</span>
            <button class="btn small" type="button">${collapsed ? "展开" : "收起"}</button>
          </div>
        </div>
        ${
          collapsed
            ? ""
            : `<div class="tree-nodes">
                ${
                  nodes.length === 0
                    ? `<div style="color:var(--muted);font-size:12px">当前筛选下无匹配节点</div>`
                    : nodes
                        .map(
                          (n) => `
                      <button class="tree-node" type="button" data-node-id="${esc(n.id)}">
                        <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;width:100%">
                          <strong style="font-size:12.5px">${esc(n.title)}</strong>
                          ${riskPill(n.risk)}
                        </div>
                        <span class="mono" style="color:var(--green)">${esc(n.tool)}${n.action ? `.${esc(n.action)}` : ""}</span>
                        <span style="font-size:11.5px;color:var(--muted)">${esc(n.description)}</span>
                      </button>
                    `,
                        )
                        .join("")
                }
              </div>`
        }
      </section>
    `;
  }).join("");

  const sel = state.selectedId ? TOOL_NODES.find((n) => n.id === state.selectedId) : null;
  const modalHtml = sel
    ? `
      <div class="po-modal-backdrop" id="node-modal-backdrop">
        <div class="po-modal" role="dialog" aria-modal="true" aria-labelledby="node-modal-title">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px">
            <div>
              <div class="eyebrow">${esc(TOOL_GROUPS.find((g) => g.id === sel.group)?.title || sel.group)}</div>
              <h2 id="node-modal-title" style="margin:0;font-size:17px">${esc(sel.title)}</h2>
            </div>
            <button class="btn small" id="node-close" type="button">关闭</button>
          </div>
          <div class="btn-row" style="margin-bottom:12px">
            <span class="mono pill">${esc(sel.tool)}${sel.action ? ` · action="${esc(sel.action)}"` : ""}</span>
            ${riskPill(sel.risk)}
          </div>
          <div class="field"><label>工具能力</label><div>${esc(sel.description)}</div></div>
          <div class="field"><label>必填参数</label><div class="mono">${sel.required.length ? esc(sel.required.join(", ")) : "（无强制必填参数）"}</div></div>
          <div class="field"><label>典型调用路径</label><div>${esc(sel.workflow)}</div></div>
          <div class="field"><label>安全边界与注意事项</label><div class="callout ${sel.risk === "destructive" ? "error" : "warning"}" style="margin:0">${esc(sel.guardrail)}</div></div>
          <div class="field"><label>参数示例</label><pre class="po-pre">${esc(JSON.stringify(sel.exampleArgs || {}, null, 2))}</pre></div>
          <div class="btn-row" style="margin-top:12px">
            <button class="btn primary" id="node-ask" type="button">${bridge.canSendMessage() ? `让 ${esc(bridge.hostName())} 使用此工具` : "复制调用提示词"}</button>
            <button class="btn" id="node-ctx" type="button">附加到对话上下文</button>
            <button class="btn" id="node-open-wb" type="button">前往实时工作台控制台</button>
          </div>
        </div>
      </div>
    `
    : "";

  root.innerHTML = `
    <div class="po-content">
      <div class="page-head">
        <div>
          <div class="eyebrow">PAPERORCHESTRA STUDIO × ${esc(bridge.hostName().toUpperCase())}</div>
          <h1>${uniqueTools} 个 MCP 工具 · ${TOOL_NODES.length} 个能力节点地图</h1>
          <p>复刻 Studio 原生工具树视图：覆盖 10 大科研工作组、后台作业与防冲突保存路径，点击任意节点可查看参数规范或直接委派 ${esc(bridge.hostName())} 执行。</p>
        </div>
        <div class="btn-row">
          <button class="btn small" id="expand-all" type="button">全部展开</button>
          <button class="btn small" id="collapse-all" type="button">全部折叠</button>
        </div>
      </div>

      <div class="grid grid-4" style="margin-bottom:14px">
        <div class="stat-card"><span class="stat-label">工作组</span><strong class="stat-value">${TOOL_GROUPS.length}</strong><span class="stat-note">从立项到投稿全流程</span></div>
        <div class="stat-card"><span class="stat-label">只读探针</span><strong class="stat-value" style="color:var(--green)">${readCnt}</strong><span class="stat-note">安全查看、检索与校验</span></div>
        <div class="stat-card"><span class="stat-label">写入 / 流水线</span><strong class="stat-value" style="color:var(--amber)">${writeCnt}</strong><span class="stat-note">落盘带冲突护栏与备份</span></div>
        <div class="stat-card"><span class="stat-label">高风险破坏性</span><strong class="stat-value" style="color:var(--red)">${destrCnt}</strong><span class="stat-note">删除 / 回滚 / 急停需二次确认</span></div>
      </div>

      <div class="grid grid-2" style="margin-bottom:14px">
        <div class="callout success" style="margin:0"><div><strong>回路 A · 后台长作业</strong><br>先用 <span class="mono">po_start_paper_run</span> / <span class="mono">po_data_analysis</span> 启动并返回 <span class="mono">job_id</span>，再通过 <span class="mono">po_job_manage</span> 与实时工作台轮询日志与 Token，不阻塞主对话。</div></div>
        <div class="callout warning" style="margin:0"><div><strong>安全边界 · 原稿保护</strong><br>更新项目传 <span class="mono">expected_updated_at</span>，编辑 LaTeX 必须先 <span class="mono">read</span> 取 <span class="mono">mtimeMs</span> 再 <span class="mono">save</span>，首次改动自动备份到 <span class="mono">.edit_backup/</span>，文件导出绝不覆写旧稿。</div></div>
      </div>

      <div class="card card-pad" style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between">
        <input id="tree-search" type="text" placeholder="搜索工具名、中文标题、能力或参数（如 xelatex / bbox / cite / cvpr）…" value="${esc(state.search)}" style="max-width:420px;flex:1 1 240px">
        <div class="btn-row">
          <button class="chip ${state.risk === "all" ? "active" : ""}" data-risk="all" type="button">全部</button>
          <button class="chip ${state.risk === "read" ? "active" : ""}" data-risk="read" type="button">只读</button>
          <button class="chip ${state.risk === "write" ? "active" : ""}" data-risk="write" type="button">写入/执行</button>
          <button class="chip ${state.risk === "destructive" ? "active" : ""}" data-risk="destructive" type="button">破坏性</button>
        </div>
      </div>

      ${groupsHtml || `<div class="card card-pad">未找到匹配工具节点，请更换关键词。</div>`}
    </div>
    ${modalHtml}
  `;

  bindEvents();
}

function bindEvents(): void {
  const search = document.getElementById("tree-search") as HTMLInputElement | null;
  search?.addEventListener("input", () => {
    state.search = search.value;
    render();
    const again = document.getElementById("tree-search") as HTMLInputElement | null;
    if (again) {
      again.focus();
      again.setSelectionRange(again.value.length, again.value.length);
    }
  });

  document.querySelectorAll<HTMLElement>("[data-risk]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.risk = (btn.dataset.risk as "all" | RiskLevel) || "all";
      render();
    });
  });

  document.getElementById("expand-all")?.addEventListener("click", () => {
    state.collapsed.clear();
    render();
  });
  document.getElementById("collapse-all")?.addEventListener("click", () => {
    TOOL_GROUPS.forEach((g) => state.collapsed.add(g.id));
    render();
  });

  document.querySelectorAll<HTMLElement>("[data-toggle-group]").forEach((el) => {
    el.addEventListener("click", () => {
      const gid = el.dataset.toggleGroup!;
      if (state.collapsed.has(gid)) state.collapsed.delete(gid);
      else state.collapsed.add(gid);
      render();
    });
  });

  document.querySelectorAll<HTMLElement>("[data-node-id]").forEach((el) => {
    el.addEventListener("click", () => {
      state.selectedId = el.dataset.nodeId || null;
      render();
    });
  });

  document.getElementById("node-close")?.addEventListener("click", () => {
    state.selectedId = null;
    render();
  });
  document.getElementById("node-modal-backdrop")?.addEventListener("click", (ev) => {
    if (ev.target === ev.currentTarget) {
      state.selectedId = null;
      render();
    }
  });

  const sel = state.selectedId ? TOOL_NODES.find((n) => n.id === state.selectedId) : null;
  if (sel) {
    document.getElementById("node-ask")?.addEventListener("click", async () => {
      const text = `请帮我调用 \`${sel.tool}\`${sel.action ? `（action="${sel.action}"）` : ""} 完成「${sel.title}」；执行前请结合当前上下文检查必填参数（${sel.required.join(", ") || "无"}）。`;
      await bridge.sendToChat(text);
    });
    document.getElementById("node-ctx")?.addEventListener("click", async () => {
      const ctxText = `PaperOrchestra 工具参考：${sel.title}\n工具：${sel.tool}${sel.action ? ` (action=${sel.action})` : ""}\n必填参数：${sel.required.join(", ") || "无"}\n安全说明：${sel.guardrail}`;
      await bridge.attachContext(ctxText);
    });
    document.getElementById("node-open-wb")?.addEventListener("click", async () => {
      try {
        await bridge.callTool("po_open_workbench", { tab: "tools" });
        showToast("已请求打开实时工作台工具控制台");
      } catch (e) {
        showToast(String(e), true);
      }
    });
  }
}

async function init(): Promise<void> {
  bridge.onToolInput((args) => {
    if (typeof args.search === "string") state.search = args.search;
    if (args.risk === "read" || args.risk === "write" || args.risk === "destructive") state.risk = args.risk;
    if (typeof args.group === "string" && args.group) {
      TOOL_GROUPS.forEach((g) => {
        if (g.id !== args.group) state.collapsed.add(g.id);
      });
    }
    render();
  });
  bridge.onToolResult((data) => {
    if (typeof data.search === "string") state.search = data.search;
    if (typeof data.group === "string" && data.group) {
      TOOL_GROUPS.forEach((g) => {
        if (g.id !== data.group) state.collapsed.add(g.id);
        else state.collapsed.delete(g.id);
      });
    }
    render();
  });
  render();
  try {
    await bridge.connect();
  } catch (err) {
    showToast(`MCP App 连接异常: ${err}`, true);
  }
}

void init();
