import {
  esc,
  fmtDate,
  ServerBridge,
  showToast,
  statusPillHtml,
  type ToolNodeMeta,
} from "./shared-app";
import { TOOL_GROUPS, TOOL_NODES } from "./tool-catalog";

type TabId =
  | "overview"
  | "projects"
  | "runs"
  | "library"
  | "figures"
  | "latex"
  | "exports"
  | "skills"
  | "tools";

const bridge = new ServerBridge("PaperOrchestra Workbench", "1.1.0");

const STAGE_ORDER = [
  "outline",
  "literature",
  "writing",
  "review",
  "refinement",
  "format",
  "compile",
  "completed",
];

const STAGE_LABELS: Record<string, string> = {
  outline: "大纲",
  literature: "文献",
  writing: "写作",
  review: "审阅",
  refinement: "精修",
  format: "排版",
  compile: "编译",
  completed: "完成",
};

const INTERVENTION_PRESETS = [
  {
    label: "核查运行健康度",
    target: "整体流程",
    prompt:
      "请检查当前 PaperOrchestra 运行的最新日志和产物，重点排查提供商 URL/端口错误、PDF 生成失败和缺图警告是否影响最终交付。先给出诊断与修复建议，不要停止运行，也不要直接修改文件，等我确认。",
  },
  {
    label: "只修方法学叙述",
    target: "方法学",
    prompt:
      "当前运行请继续。只调整论文的方法学叙述，使其与已完成的实验和代码一致；保留数据、结果和结论。先列出拟修改段落和理由，等我确认后再保存。",
  },
  {
    label: "补证据与引用",
    target: "文献证据",
    prompt:
      "请检查论文中的关键论断是否有充分证据支持，优先核对参考文献与正文引用是否匹配。先给出缺口、候选文献和推荐引用位置；不要自动改写正文。",
  },
  {
    label: "评估并重做图稿",
    target: "图稿",
    prompt:
      "请盘点当前运行的图稿、对应结论和缺失图。先评估哪些图需要重做以及原因，等待我指定具体图号后再修改，避免整批返工。",
  },
  {
    label: "从检查点恢复",
    target: "运行控制",
    prompt:
      "请列出当前运行可用的检查点、已完成阶段和未完成项，推荐最合适的恢复点并说明代价。先不要执行 resume_run，等我确认。",
  },
  {
    label: "停止前先盘点",
    target: "运行控制",
    prompt:
      "请先总结当前进度、已生成产物、未保存修改和下一步计划。暂时不要停止运行；把风险点列清楚后等我决定是否停止。",
  },
];

interface ModalInputSpec {
  key: string;
  label: string;
  value: string;
  placeholder?: string;
}

interface ConfirmState {
  title: string;
  body: string;
  requireText?: string;
  typed: string;
  inputs?: ModalInputSpec[];
  values: Record<string, string>;
  danger?: boolean;
  onConfirm: (values: Record<string, string>) => Promise<void>;
}

interface State {
  tab: TabId;
  loading: boolean;
  error: string;
  notice: string;
  autoPoll: boolean;
  pollTimer: number | null;
  snapshot: Record<string, any>;
  selectedProjectId: string;
  selectedProject: Record<string, any> | null;
  selectedRunId: string;
  selectedRunDir: string;
  runSummary: Record<string, any> | null;
  runLogs: Array<Record<string, any>>;
  artifactPreview: { path: string; content: string } | null;
  // Canvas intervention
  interventionTarget: string;
  interventionNote: string;
  interventionConservative: boolean;
  interventionHistory: Array<{ target: string; note: string; at: string }>;
  // Library pagination
  libFolder: string;
  libOffset: number;
  libLimit: number;
  libPage: Record<string, any> | null;
  libOutput: string;
  // Figure studio
  figDir: string;
  figBundles: Array<Record<string, any>>;
  selectedBundle: string;
  selectedImagePath: string;
  figDataUrl: string;
  bbox: { x: number; y: number; w: number; h: number };
  figInstruction: string;
  figResult: string;
  // LaTeX editor
  latexOpen: Record<string, any> | null;
  latexFile: string;
  latexContent: string;
  latexMtimeMs: number;
  latexHasBackup: boolean;
  latexCompileResult: Record<string, any> | null;
  latexResultText: string;
  latexPdfPath: string;
  latexPdfPage: number;
  latexPdfPageCount: number;
  latexPdfDataUrl: string;
  latexPdfLoading: boolean;
  latexPdfError: string;
  latexPdfZoom: number;
  latexSplitView: boolean;
  latexAutoSync: boolean;
  latexHasSyncTeX: boolean;
  latexCursorStartLine: number;
  latexCursorEndLine: number;
  latexCursorCol: number;
  latexPdfWidthPt: number;
  latexPdfHeightPt: number;
  latexSyncHighlight: { page: number; h: number; v: number; top: number; W: number; H: number; lineLabel: string } | null;
  latexSyncBusy: boolean;
  latexPolishResult: Record<string, any> | null;
  // Audit / Export
  auditResult: string;
  exportResult: string;
  // Skills / Settings
  selectedSkill: Record<string, any> | null;
  settingsData: Record<string, any> | null;
  readinessData: Record<string, any> | null;
  providersValidation: string;
  // Tools Console
  toolSearch: string;
  selectedToolId: string;
  toolArgsText: string;
  toolOutput: string;
  confirmModal: ConfirmState | null;
}

const state: State = {
  tab: "overview",
  loading: false,
  error: "",
  notice: "",
  autoPoll: false,
  pollTimer: null,
  snapshot: {},
  selectedProjectId: "",
  selectedProject: null,
  selectedRunId: "",
  selectedRunDir: "",
  runSummary: null,
  runLogs: [],
  artifactPreview: null,
  interventionTarget: "方法学",
  interventionNote: "",
  interventionConservative: true,
  interventionHistory: [],
  libFolder: "",
  libOffset: 0,
  libLimit: 25,
  libPage: null,
  libOutput: "",
  figDir: "",
  figBundles: [],
  selectedBundle: "",
  selectedImagePath: "",
  figDataUrl: "",
  bbox: { x: 0.06, y: 0.09, w: 0.28, h: 0.24 },
  figInstruction: "",
  figResult: "",
  latexOpen: null,
  latexFile: "",
  latexContent: "",
  latexMtimeMs: 0,
  latexHasBackup: false,
  latexCompileResult: null,
  latexResultText: "",
  latexPdfPath: "",
  latexPdfPage: 1,
  latexPdfPageCount: 0,
  latexPdfDataUrl: "",
  latexPdfLoading: false,
  latexPdfError: "",
  latexPdfZoom: 100,
  latexSplitView: true,
  latexAutoSync: true,
  latexHasSyncTeX: false,
  latexCursorStartLine: 1,
  latexCursorEndLine: 1,
  latexCursorCol: 1,
  latexPdfWidthPt: 612,
  latexPdfHeightPt: 792,
  latexSyncHighlight: null,
  latexSyncBusy: false,
  latexPolishResult: null,
  auditResult: "",
  exportResult: "",
  selectedSkill: null,
  settingsData: null,
  readinessData: null,
  providersValidation: "",
  toolSearch: "",
  selectedToolId: TOOL_NODES[0].id,
  toolArgsText: "{}",
  toolOutput: "",
  confirmModal: null,
};

let syncDebounceTimer: number | null = null;
let pendingEditorJumpLine: number | null = null;
let pendingScrollToHighlight = false;

function offsetToLineCol(text: string, offset: number): { line: number; col: number } {
  const clamped = Math.max(0, Math.min(text.length, offset));
  let line = 1;
  let lastNl = -1;
  for (let i = 0; i < clamped; i++) {
    if (text.charCodeAt(i) === 10) {
      line++;
      lastNl = i;
    }
  }
  return { line, col: clamped - lastNl };
}

function lineRangeOffsets(text: string, targetLine: number): { start: number; end: number } {
  const goal = Math.max(1, Math.floor(targetLine));
  let line = 1;
  let start = 0;
  while (line < goal && start < text.length) {
    const nl = text.indexOf("\n", start);
    if (nl < 0) {
      start = text.length;
      break;
    }
    start = nl + 1;
    line++;
  }
  const endNl = text.indexOf("\n", start);
  const end = endNl < 0 ? text.length : endNl;
  return { start, end };
}

const NAV_ITEMS: Array<{ id: TabId; label: string; icon: string }> = [
  { id: "overview", label: "总览与干预", icon: `<path d="M3 13h8V3H3zM13 21h8v-6h-8zM13 3v8h8V3zM3 21h8v-4H3z"/>` },
  { id: "projects", label: "项目与材料", icon: `<path d="M3 7h7l2 2h9v11H3zM3 7V4h7l2 3"/>` },
  { id: "runs", label: "运行与产物", icon: `<path d="M4 5h16v14H4zM8 9h8M8 13h5"/>` },
  { id: "library", label: "文献与引文", icon: `<path d="M4 4h6v16H4zM14 4h6v16h-6z"/>` },
  { id: "figures", label: "图稿圈选台", icon: `<path d="M4 19V5M4 19h16M7 15l4-5 3 3 5-7"/>` },
  { id: "latex", label: "LaTeX 协同", icon: `<path d="m8 6-6 6 6 6M16 6l6 6-6 6M14 4l-4 16"/>` },
  { id: "exports", label: "体检与导出", icon: `<path d="M12 3v12m0 0 4-4m-4 4-4-4M4 16v5h16v-5"/>` },
  { id: "skills", label: "技能与环境", icon: `<path d="M12 3l2.5 5.5L20 10l-4 4 .8 6-4.8-2.7L7.2 20 8 14 4 10l5.5-1.5z"/>` },
  { id: "tools", label: "工具控制台", icon: `<path d="M14 6a4 4 0 1 0-4 4L4 16a3 3 0 0 0 4 4l6-6a4 4 0 0 0 4-4z"/>` },
];

function applySnapshot(data: Record<string, any>): void {
  const snap = (data.snapshot && typeof data.snapshot === "object" ? data.snapshot : data) as Record<string, any>;
  state.snapshot = snap;
  if (typeof data.tab === "string" && NAV_ITEMS.some((n) => n.id === data.tab)) {
    state.tab = data.tab as TabId;
  }
  if (typeof data.notice === "string" && data.notice) state.notice = data.notice;
  const projects: any[] = snap.projects || [];
  if (typeof data.project_id === "string" && data.project_id) state.selectedProjectId = data.project_id;
  else if (!state.selectedProjectId && projects.length > 0) state.selectedProjectId = projects[0].id;

  const runs: any[] = snap.runs || [];
  if (typeof data.run_id === "string" && data.run_id) state.selectedRunId = data.run_id;
  else if (!state.selectedRunId && runs.length > 0) state.selectedRunId = runs[0].id;

  if (typeof data.run_dir === "string" && data.run_dir) state.selectedRunDir = data.run_dir;
  else if (!state.selectedRunDir) {
    const matched = runs.find((r) => r.id === state.selectedRunId) || runs[0];
    if (matched?.run_dir) state.selectedRunDir = matched.run_dir;
  }

  if (!state.figDir && state.selectedRunDir) {
    state.figDir = `${state.selectedRunDir}/raw_materials/figures`;
  }
}

async function refreshSnapshot(silent = false): Promise<void> {
  if (!silent) {
    state.loading = true;
    state.error = "";
    render();
  }
  try {
    const res = await bridge.callTool("po_ui_snapshot", {
      action: "snapshot",
      project_id: state.selectedProjectId || undefined,
      run_id: state.selectedRunId || undefined,
      run_dir: state.selectedRunDir || undefined,
    });
    applySnapshot(res);
    if (state.selectedProjectId && res.selectedProject) state.selectedProject = res.selectedProject as Record<string, any>;
    if (res.runSummary) state.runSummary = res.runSummary as Record<string, any>;
    if (Array.isArray(res.runLogs)) state.runLogs = res.runLogs as any[];
  } catch (err) {
    state.error = String(err);
    if (!silent) showToast(state.error, true);
  } finally {
    state.loading = false;
    render();
  }
}

function interventionText(): string {
  const safety = state.interventionConservative
    ? "请先检查并给出具体方案，不要停止当前运行，也不要直接修改文件；等我确认后再执行。"
    : "请按我的要求处理当前环节，并在写入或运行前说明将改动哪些文件。";
  const proj = (state.snapshot.projects || []).find((p: any) => p.id === state.selectedProjectId);
  return [
    `请基于当前 PaperOrchestra Studio 工作区状态（项目「${proj?.title || "未选项目"}」，run_dir: \`${state.selectedRunDir || "未指定"}\`）处理「${state.interventionTarget}」：`,
    state.interventionNote.trim() || "请先结合最近运行日志、产物完备度与引文审计给出诊断和下一步建议。",
    safety,
  ].join("\n\n");
}

function activeRun(): Record<string, any> | null {
  const runs: any[] = state.snapshot.runs || [];
  return runs.find((r) => r.id === state.selectedRunId || r.run_dir === state.selectedRunDir) || runs[0] || null;
}

function projectTitleById(pid?: string): string {
  const p = (state.snapshot.projects || []).find((x: any) => x.id === pid);
  return p?.title || pid || "PaperOrchestra 项目";
}

function tabCount(id: TabId): string {
  const counts = state.snapshot.env?.counts || {};
  if (id === "projects") return String(counts.projects ?? (state.snapshot.projects || []).length ?? 0);
  if (id === "runs") return String(counts.runs ?? (state.snapshot.runs || []).length ?? 0);
  if (id === "library") return String(counts.library_items ?? 0);
  if (id === "skills") return String((state.snapshot.skillsCount ?? 5));
  if (id === "tools") return String(TOOL_NODES.length);
  return "";
}

function renderOverview(): string {
  const run = activeRun();
  const counts = state.snapshot.env?.counts || {};
  const jobs: any[] = state.snapshot.jobs || [];
  const runningJobs = jobs.filter((j) => j.running).length;
  const stageIdx = Math.max(0, STAGE_ORDER.indexOf(String(run?.current_stage || "outline").toLowerCase()));
  const recentLogs = (state.runLogs || []).slice(-12).reverse();
  const prompt = interventionText();

  return `
    <div class="page-head">
      <div>
        <div class="eyebrow">LIVE ORCHESTRATION · ANTIGRAVITY STUDIO</div>
        <h1>把论文流水线变成可实时干预的控制台</h1>
        <p>实时读取本机 PaperOrchestra 项目、运行阶段、后台作业与产物；任何环节不满意，直接在此生成并发送结构化干预指令。</p>
      </div>
      <div class="btn-row">
        <button class="btn small" id="ov-open-tree" type="button">查看工具树</button>
        <button class="btn small" id="ov-open-desktop" type="button">唤起 Desktop App</button>
      </div>
    </div>

    ${
      run
        ? `
      <div class="run-hero">
        <div class="eyebrow">CURRENT RUN · ${esc(String(run.id || "").slice(0, 8))}</div>
        <h2>${esc(projectTitleById(run.project_id))}</h2>
        <div>${statusPillHtml(run.status)} &nbsp; 阶段：<strong>${esc(STAGE_LABELS[run.current_stage] || run.current_stage)}</strong> &nbsp; 开始于 ${esc(fmtDate(run.started_at))}</div>
        <div class="run-meta">
          <span class="pill">${esc(run.template_name || "默认模板")}</span>
          <span class="pill">写作模型 ${esc(run.writer_model || "auto")}</span>
          <span class="pill">${run.use_plotting ? "启用绘图 Critic 环" : "未启用自动绘图"}</span>
          <span class="pill mono">${esc(run.run_dir || "")}</span>
        </div>
        <div class="stage-track">
          ${STAGE_ORDER.map((s, idx) => `<div class="stage-step ${idx < stageIdx ? "done" : idx === stageIdx ? "current" : ""}">${esc(STAGE_LABELS[s] || s)}</div>`).join("")}
        </div>
        <div class="btn-row" style="margin-top:12px">
          <button class="btn small" data-jump="runs" type="button">查看日志与产物</button>
          <button class="btn small" data-jump="latex" type="button">打开 LaTeX 协同编辑</button>
          <button class="btn small" data-jump="exports" type="button">投稿体检 (8 工具)</button>
          ${run.status === "running" ? `<button class="btn small danger" id="ov-resume-note" type="button">前往停止控制</button>` : `<button class="btn small primary" id="ov-resume-run" type="button">断点续跑 (resume)</button>`}
        </div>
      </div>`
        : `<div class="card card-pad"><div><strong>当前还没有运行记录</strong> · 请前往「项目与材料」导入 CVPR 示例或新建项目后启动流水线。</div></div>`
    }

    <div class="grid grid-4" style="margin-bottom:14px">
      <div class="stat-card"><span class="stat-label">项目</span><strong class="stat-value">${esc(counts.projects ?? 0)}</strong><span class="stat-note">SQLite 共享工作区</span></div>
      <div class="stat-card"><span class="stat-label">运行 / 后台作业</span><strong class="stat-value">${esc(counts.runs ?? 0)} / ${runningJobs}</strong><span class="stat-note">进行中后台任务数：${runningJobs}</span></div>
      <div class="stat-card"><span class="stat-label">文献库条目</span><strong class="stat-value">${esc(counts.library_items ?? 0)}</strong><span class="stat-note">支持 8 源检索与图谱</span></div>
      <div class="stat-card"><span class="stat-label">记忆 / 算力主机</span><strong class="stat-value">${esc(counts.research_memories ?? 0)} / ${esc(counts.ssh_hosts ?? 0)}</strong><span class="stat-note">自动注入写作上下文</span></div>
    </div>

    <div class="card">
      <div class="card-head">
        <div><h3>Canvas 协作干预台</h3><p>复刻 Studio Canvas Console：选预设或输入微调目标，直接下发给 Antigravity 对话。</p></div>
        <label style="display:inline-flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
          <input type="checkbox" id="int-cons" ${state.interventionConservative ? "checked" : ""}> 保守模式（先诊断给方案，不直接改文件/停运行）
        </label>
      </div>
      <div class="card-body">
        <div class="preset-grid">
          ${INTERVENTION_PRESETS.map((p, i) => `<button class="chip" data-preset="${i}" type="button">${esc(p.label)}</button>`).join("")}
        </div>
        <div class="grid grid-2">
          <div class="field">
            <label>干预环节</label>
            <select id="int-target">
              ${["整体流程", "方法学", "文献证据", "图稿", "运行控制", "LaTeX 源码", "投稿检查"]
                .map((opt) => `<option value="${esc(opt)}" ${state.interventionTarget === opt ? "selected" : ""}>${esc(opt)}</option>`)
                .join("")}
            </select>
          </div>
          <div class="field">
            <label>自定义指令补充</label>
            <textarea id="int-note" placeholder="输入具体修改意见、要核查的指标或想聚焦的章节…">${esc(state.interventionNote)}</textarea>
          </div>
        </div>
        <div class="field"><label>将发送给对话的完整指令预览</label><pre class="po-pre">${esc(prompt)}</pre></div>
        <div class="btn-row">
          <button class="btn primary" id="int-send" type="button">${bridge.canSendMessage() ? "发送到当前对话" : "复制干预指令"}</button>
          <button class="btn" id="int-ctx" type="button">附加到模型上下文</button>
          <button class="btn small" id="int-clear" type="button">清空</button>
        </div>
      </div>
    </div>

    ${
      recentLogs.length
        ? `<div class="card">
            <div class="card-head"><div><h3>最近运行日志（${recentLogs.length} 行）</h3><p>已自动过滤敏感密钥与 Token</p></div></div>
            <div class="card-body"><pre class="po-pre">${esc(recentLogs.map((l) => `[${l.stage || "log"}] ${l.line || ""}`).join("\n"))}</pre></div>
          </div>`
        : ""
    }
  `;
}

function renderProjects(): string {
  const projects: any[] = state.snapshot.projects || [];
  const p = state.selectedProject || projects.find((x) => x.id === state.selectedProjectId);
  const tpls: any[] = state.snapshot.templates || [];
  return `
    <div class="page-head">
      <div>
        <div class="eyebrow">PROJECTS & INPUTS</div>
        <h1>项目与研究材料</h1>
        <p>修改前保留 updatedAt 乐观锁，避免桌面端与 Antigravity 双端同时编辑造成互相覆盖。</p>
      </div>
      <div class="btn-row">
        <button class="btn" id="proj-import-cvpr" type="button">导入 CVPR 官方示例</button>
        <button class="btn primary" id="proj-toggle-new" type="button">+ 新建项目</button>
      </div>
    </div>

    <div class="card card-pad" id="proj-new-box" hidden>
      <div class="grid grid-2">
        <div class="field"><label>项目标题</label><input id="new-proj-title" type="text" placeholder="如：Physics-Informed Tensor Decomposition for …"></div>
        <div class="field"><label>目标模板</label><select id="new-proj-tpl">${(tpls.length ? tpls : [{ name: "cvpr2025" }, { name: "journal-nature" }]).map((t) => `<option value="${esc(t.name)}">${esc(t.name)}</option>`).join("")}</select></div>
      </div>
      <div class="btn-row">
        <button class="btn primary small" id="proj-create-submit" type="button">确认创建</button>
        <button class="btn small" id="proj-intake-parse" type="button">解析立项单文件路径</button>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><div><h3>全部项目（${projects.length}）</h3></div></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>项目名称</th><th>模板</th><th>图稿 / 运行</th><th>最近更新</th><th></th></tr></thead>
          <tbody>
            ${
              projects.length === 0
                ? `<tr><td colspan="5" style="color:var(--muted)">暂无项目。可点击右上角「导入 CVPR 官方示例」或「新建项目」。</td></tr>`
                : projects
                    .map(
                      (item) => `
                  <tr>
                    <td><strong>${esc(item.title)}</strong><div class="mono" style="color:var(--muted)">${esc(item.id)}</div></td>
                    <td class="mono">${esc(item.template_name)}</td>
                    <td>${esc(item.figure_count ?? (item.figures?.length ?? 0))} 图 / ${esc(item.run_count ?? 0)} 次运行</td>
                    <td>${esc(fmtDate(item.updated_at))}</td>
                    <td><button class="btn small ${item.id === state.selectedProjectId ? "primary" : ""}" data-select-proj="${esc(item.id)}" type="button">${item.id === state.selectedProjectId ? "已选中" : "打开"}</button></td>
                  </tr>`,
                    )
                    .join("")
            }
          </tbody>
        </table>
      </div>
    </div>

    ${
      p
        ? `
      <div class="card">
        <div class="card-head">
          <div><h3>编辑当前项目 · ${esc(p.title)}</h3><p class="mono">updatedAt lock: ${esc(p.updated_at || "")}</p></div>
          <div class="btn-row">
            <button class="btn primary small" id="proj-start-run" type="button">启动论文生成流水线</button>
            <button class="btn danger small" id="proj-delete" type="button">删除项目</button>
          </div>
        </div>
        <div class="card-body">
          <div class="grid grid-2">
            <div class="field"><label>标题</label><input id="ep-title" type="text" value="${esc(p.title || "")}"></div>
            <div class="field"><label>模板</label><input id="ep-tpl" type="text" value="${esc(p.template_name || "cvpr2025")}"></div>
            <div class="field"><label>写作模型 (writer_model)</label><input id="ep-writer" type="text" value="${esc(p.writer_model || "auto")}"></div>
            <div class="field"><label>审阅模型 (reflection_model)</label><input id="ep-refl" type="text" value="${esc(p.reflection_model || "auto")}"></div>
          </div>
          <label style="display:inline-flex;align-items:center;gap:6px;margin-bottom:10px;cursor:pointer">
            <input type="checkbox" id="ep-plot" ${p.use_plotting ? "checked" : ""}> 启用多轮图稿 Critic 闭环
          </label>
          <div class="field"><label>核心研究想法 (idea_markdown)</label><textarea id="ep-idea" rows="5">${esc(p.idea_markdown || "")}</textarea></div>
          <div class="field"><label>实验记录与数据 (experimental_log_markdown)</label><textarea id="ep-log" rows="5">${esc(p.experimental_log_markdown || "")}</textarea></div>
          <div class="btn-row">
            <button class="btn primary" id="ep-save" type="button">防冲突保存修改</button>
            <button class="btn" id="ep-add-fig" type="button">挂载图稿文件</button>
          </div>
          ${
            (p.figures || []).length
              ? `<div style="margin-top:14px">
                  <label style="font-size:12px;font-weight:700;color:var(--muted)">已挂载图稿（${p.figures.length}）</label>
                  <div class="grid grid-2" style="margin-top:6px">
                    ${p.figures
                      .map(
                        (f: any) => `
                      <div class="stat-card">
                        <div class="mono">${esc(f.file_name)}</div>
                        <div class="mono" style="font-size:11px;color:var(--muted)">${esc(f.source_path)}</div>
                        <div class="field" style="margin:8px 0"><input type="text" data-fig-cap="${esc(f.id)}" value="${esc(f.caption || "")}" placeholder="填写图注…"></div>
                        <div class="btn-row">
                          <button class="btn small" data-save-cap="${esc(f.id)}" type="button">更新图注</button>
                          <button class="btn small danger" data-del-fig="${esc(f.id)}" type="button">移除</button>
                        </div>
                      </div>`,
                      )
                      .join("")}
                  </div>
                </div>`
              : ""
          }
        </div>
      </div>`
        : ""
    }
  `;
}

function renderRuns(): string {
  const runs: any[] = state.snapshot.runs || [];
  const jobs: any[] = state.snapshot.jobs || [];
  const sum = state.runSummary;
  const selRun = activeRun();
  return `
    <div class="page-head">
      <div>
        <div class="eyebrow">PIPELINE RUNS & ARTIFACTS</div>
        <h1>写作运行、日志与产物盘点</h1>
        <p>查看 SQLite 历史运行与后台作业；支持恢复断点、安全复制最终 PDF 或在 Antigravity 侧栏直接预览产物。</p>
      </div>
      <div class="btn-row">
        <button class="btn small ${state.autoPoll ? "primary" : ""}" id="runs-toggle-poll" type="button">${state.autoPoll ? "停止自动轮询 (6s)" : "开启自动轮询"}</button>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><div><h3>SQLite 流水线运行记录（${runs.length}）</h3></div></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>项目 / ID</th><th>状态 · 阶段</th><th>模板</th><th>开始时间</th><th></th></tr></thead>
          <tbody>
            ${
              runs.length === 0
                ? `<tr><td colspan="5" style="color:var(--muted)">暂无运行记录。</td></tr>`
                : runs
                    .map(
                      (r) => `
                  <tr>
                    <td><strong>${esc(projectTitleById(r.project_id))}</strong><div class="mono" style="color:var(--muted)">${esc(r.run_dir || r.id)}</div></td>
                    <td>${statusPillHtml(r.status)} <span class="pill">${esc(STAGE_LABELS[r.current_stage] || r.current_stage)}</span></td>
                    <td class="mono">${esc(r.template_name)}</td>
                    <td>${esc(fmtDate(r.started_at))}</td>
                    <td class="btn-row">
                      <button class="btn small ${r.id === state.selectedRunId ? "primary" : ""}" data-select-run="${esc(r.id)}" data-select-dir="${esc(r.run_dir || "")}" type="button">检视</button>
                      <button class="btn small danger" data-del-run="${esc(r.id)}" type="button">删记录</button>
                    </td>
                  </tr>`,
                    )
                    .join("")
            }
          </tbody>
        </table>
      </div>
    </div>

    ${
      jobs.length
        ? `<div class="card">
            <div class="card-head"><div><h3>插件后台作业（${jobs.length}）</h3></div></div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Job ID / 类型</th><th>状态</th><th>PID</th><th>运行目录</th><th></th></tr></thead>
                <tbody>
                  ${jobs
                    .map(
                      (j) => `
                    <tr>
                      <td><div class="mono">${esc(j.job_id)}</div><div style="font-size:11.5px;color:var(--muted)">${esc(j.kind || j.title || "")}</div></td>
                      <td>${statusPillHtml(j.running ? "running" : j.stopped ? "stopped" : j.returncode === 0 ? "completed" : "failed")}</td>
                      <td class="mono">${esc(j.pid ?? "—")}</td>
                      <td class="mono">${esc(j.run_dir || "")}</td>
                      <td>${j.running ? `<button class="btn small danger" data-stop-job="${esc(j.job_id)}" type="button">急停</button>` : ""}</td>
                    </tr>`,
                    )
                    .join("")}
                </tbody>
              </table>
            </div>
          </div>`
        : ""
    }

    ${
      state.selectedRunDir
        ? `
      <div class="card">
        <div class="card-head">
          <div><h3>当前运行产物盘点</h3><p class="mono">${esc(state.selectedRunDir)}</p></div>
          <div class="btn-row">
            <button class="btn small" id="run-load-summary" type="button">刷新产物列表</button>
            ${selRun ? `<button class="btn small" id="run-load-logs" type="button">拉取数据库日志</button>` : ""}
            <button class="btn small primary" id="run-copy-pdf" type="button">安全导出最终 PDF</button>
          </div>
        </div>
        <div class="card-body">
          ${
            sum
              ? `
            <div class="btn-row" style="margin-bottom:10px">
              <span class="pill ${sum.finalPdfPath ? "green" : "amber"}">终稿 PDF: ${sum.finalPdfPath ? "已生成" : "未生成"}</span>
              <span class="pill ${sum.hasOutline ? "green" : ""}">Outline: ${sum.hasOutline ? "有" : "无"}</span>
              <span class="pill ${sum.hasCitationMap ? "green" : ""}">Citation Map: ${sum.hasCitationMap ? "有" : "无"}</span>
              ${sum.citationAudit ? `<span class="pill blue">引用核查: ${esc(sum.citationAudit.cited_count ?? sum.citationAudit.total_citations ?? "已生成")}</span>` : ""}
              ${sum.finalPdfPath ? `<button class="btn small" id="run-open-pdf-side" type="button">在侧栏打开 PDF</button>` : ""}
            </div>
            <div class="field"><label>点击以下相对产物可安全预览前 40 KB 文本</label>
              <div class="preset-grid">
                ${[
                  ...(sum.citationAudit ? ["citation_audit.json"] : []),
                  ...(sum.citationAuditMd ? ["citation_audit.md"] : []),
                  ...(sum.usageReport ? ["usage_report.json"] : []),
                  ...(sum.peerReviews || []),
                  ...(sum.latexFiles || []).slice(0, 12),
                ]
                  .map((rel: string) => `<button class="chip" data-read-art="${esc(rel)}" type="button">${esc(rel)}</button>`)
                  .join("")}
              </div>
            </div>`
              : `<div style="color:var(--muted)">点击「刷新产物列表」载入该目录文件状态。</div>`
          }
          ${
            state.artifactPreview
              ? `<div style="margin-top:10px">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                    <strong class="mono">${esc(state.artifactPreview.path)}</strong>
                    <button class="btn small" id="art-open-side" type="button">在 Antigravity 侧栏打开此文件</button>
                  </div>
                  <pre class="po-pre">${esc(state.artifactPreview.content)}</pre>
                </div>`
              : ""
          }
          ${
            state.runLogs.length
              ? `<div style="margin-top:12px"><label style="font-size:12px;font-weight:700;color:var(--muted)">SQLite run_logs（${state.runLogs.length} 条）</label><pre class="po-pre">${esc(state.runLogs.map((l) => `[#${l.id} ${l.stage}] ${l.line}`).join("\n"))}</pre></div>`
              : ""
          }
        </div>
      </div>`
        : ""
    }
  `;
}

function renderLibrary(): string {
  const page = state.libPage;
  const items: any[] = page?.items || state.snapshot.libraryPreview || [];
  const total: number = page?.total ?? state.snapshot.env?.counts?.library_items ?? items.length;
  return `
    <div class="page-head">
      <div>
        <div class="eyebrow">LITERATURE LIBRARY & CITATIONS</div>
        <h1>8 源学术文献库、分页浏览与引文导出</h1>
        <p>支持关键词 8 源联查、DOI/PDF 一键入库、按文献夹分页、知识图谱与 BibTeX / GB/T 7714 导出。</p>
      </div>
      <div class="btn-row">
        <button class="btn small" id="lib-export-bib" type="button">导出 BibTeX</button>
        <button class="btn small" id="lib-export-gb" type="button">导出 GB/T 7714</button>
        <button class="btn small primary" id="lib-graph" type="button">生成知识图谱</button>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card card-pad">
        <div class="field"><label>8 源在线检索（Semantic Scholar / arXiv / OpenAlex / Crossref / dblp / PMC / PubMed / CORE）</label><input id="lib-query" type="text" placeholder="输入英文/中文关键词…"></div>
        <button class="btn primary small" id="lib-search-btn" type="button">开始检索</button>
      </div>
      <div class="card card-pad">
        <div class="field"><label>按 DOI 快速解析入库</label><input id="lib-doi" type="text" placeholder="例如 10.1038/s41586-023-06747-5"></div>
        <button class="btn small" id="lib-doi-btn" type="button">解析 DOI 并存入库</button>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <div><h3>本地文献库（共 ${esc(total)} 篇）</h3></div>
        <div class="btn-row">
          <input id="lib-folder" type="text" value="${esc(state.libFolder)}" placeholder="按文献夹筛选…" style="width:150px">
          <button class="btn small" id="lib-reload" type="button">查询</button>
          <button class="btn small" id="lib-prev" type="button" ${state.libOffset <= 0 ? "disabled" : ""}>上一页</button>
          <button class="btn small" id="lib-next" type="button" ${page?.nextOffset == null ? "disabled" : ""}>下一页</button>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>文献标题 / 作者</th><th>年份 / 期刊 / DOI</th><th>文献夹</th><th></th></tr></thead>
          <tbody>
            ${
              items.length === 0
                ? `<tr><td colspan="4" style="color:var(--muted)">暂无匹配文献。可先输入 DOI 或点击「查询」。</td></tr>`
                : items
                    .map(
                      (it) => `
                  <tr>
                    <td><strong>${esc(it.title)}</strong><div style="font-size:11.5px;color:var(--muted)">${esc(it.authors || "")}</div></td>
                    <td class="mono">${esc(it.year || "—")} · ${esc(it.venue || "—")}<br>${esc(it.doi || it.arxiv_id || "")}</td>
                    <td><span class="pill">${esc(it.folder || "默认文献夹")}</span></td>
                    <td><button class="btn small danger" data-del-lib="${esc(it.id)}" type="button">删除</button></td>
                  </tr>`,
                    )
                    .join("")
            }
          </tbody>
        </table>
      </div>
    </div>

    ${state.libOutput ? `<div class="card"><div class="card-head"><div><h3>文献工具结果</h3></div></div><div class="card-body"><pre class="po-pre">${esc(state.libOutput)}</pre></div></div>` : ""}
  `;
}

function renderFigures(): string {
  const b = state.bbox;
  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
  return `
    <div class="page-head">
      <div>
        <div class="eyebrow">FIGURE REVIEW BENCH · INTERACTIVE BBOX</div>
        <h1>图稿审阅台：拖拽红框局部修改与版本回滚</h1>
        <p>直接在图面上拖拽红色虚线框选出需要微调的局部（图例/子图/标注），仅局部重写绘图脚本保留其余布局。</p>
      </div>
    </div>

    <div class="card card-pad">
      <div class="grid grid-2">
        <div class="field"><label>图稿目录 (dir_path)</label><input id="fig-dir-input" type="text" value="${esc(state.figDir)}"></div>
        <div class="field" style="align-self:end"><div class="btn-row"><button class="btn primary" id="fig-scan" type="button">扫描版本束 (list)</button><button class="btn" id="fig-create-btn" type="button">新建图稿 (create)</button></div></div>
      </div>
      ${
        state.figBundles.length
          ? `<div class="preset-grid" style="margin-top:8px">
              ${state.figBundles
                .map((bun: any) => `<button class="chip ${bun.bundle === state.selectedBundle ? "active" : ""}" data-select-bundle="${esc(bun.bundle)}" data-img="${esc(bun.currentImage || bun.image || "")}" type="button">${esc(bun.title || bun.bundleName || bun.bundle)}</button>`)
                .join("")}
            </div>`
          : `<div style="font-size:12px;color:var(--muted)">输入包含图稿的目录并点击「扫描版本束」。</div>`
      }
    </div>

    ${
      state.selectedBundle
        ? `
      <div class="card">
        <div class="card-head">
          <div><h3>局部圈选与版本微调</h3><p class="mono">红框坐标：x=${pct(b.x)}, y=${pct(b.y)}, w=${pct(b.w)}, h=${pct(b.h)}</p></div>
          <div class="btn-row">
            <button class="btn small" id="fig-reset-bbox" type="button">重设默认框</button>
            <button class="btn small" id="fig-restore-v1" type="button">回滚版本</button>
          </div>
        </div>
        <div class="card-body">
          <div class="grid grid-2">
            <div class="bbox-stage-wrap">
              <div class="bbox-stage" id="bbox-stage">
                ${
                  state.figDataUrl
                    ? `<img src="${esc(state.figDataUrl)}" alt="Figure preview">`
                    : `<div style="padding:36px 18px;text-align:center;color:var(--muted)">图片预览未加载或体积大于 600 KB（仍可输入坐标提交 revise）</div>`
                }
                <div class="bbox-rect" id="bbox-rect" style="left:${(b.x * 100).toFixed(2)}%;top:${(b.y * 100).toFixed(2)}%;width:${(b.w * 100).toFixed(2)}%;height:${(b.h * 100).toFixed(2)}%">
                  <span class="bbox-label">${pct(b.x)}, ${pct(b.y)} · ${pct(b.w)}×${pct(b.h)}</span>
                </div>
              </div>
              <div class="grid" style="grid-template-columns:repeat(4,1fr);gap:6px">
                <div class="field"><label>x</label><input id="bx-x" type="number" step="0.01" min="0" max="1" value="${b.x.toFixed(3)}"></div>
                <div class="field"><label>y</label><input id="bx-y" type="number" step="0.01" min="0" max="1" value="${b.y.toFixed(3)}"></div>
                <div class="field"><label>w</label><input id="bx-w" type="number" step="0.01" min="0.01" max="1" value="${b.w.toFixed(3)}"></div>
                <div class="field"><label>h</label><input id="bx-h" type="number" step="0.01" min="0.01" max="1" value="${b.h.toFixed(3)}"></div>
              </div>
            </div>
            <div>
              <div class="field"><label>局部修改指令 (instruction)</label><textarea id="fig-inst" rows="5" placeholder="例如：把红框内的图例字体改大并避免遮挡右上角实验曲线；保持其余子图完全不变。">${esc(state.figInstruction)}</textarea></div>
              <div class="btn-row">
                <button class="btn primary" id="fig-revise-run" type="button">执行红框局部重绘 (revise)</button>
                <button class="btn" id="fig-send-chat" type="button">带红框坐标发到对话</button>
              </div>
            </div>
          </div>
        </div>
      </div>`
        : ""
    }

    ${state.figResult ? `<div class="card"><div class="card-head"><div><h3>图稿工坊响应</h3></div></div><div class="card-body"><pre class="po-pre">${esc(state.figResult)}</pre></div></div>` : ""}
  `;
}

function renderLatex(): string {
  const open = state.latexOpen;
  const comp = state.latexCompileResult;
  const hasPdfPreview = Boolean(state.latexPdfLoading || state.latexPdfDataUrl || state.latexPdfError);
  const showSplit = Boolean(state.latexFile || hasPdfPreview);
  const cursorLabel =
    state.latexCursorStartLine === state.latexCursorEndLine
      ? `L${state.latexCursorStartLine}`
      : `L${state.latexCursorStartLine}–${state.latexCursorEndLine}`;

  let highlightHtml = "";
  const hl = state.latexSyncHighlight;
  if (hl && hl.page === state.latexPdfPage && state.latexPdfWidthPt > 0 && state.latexPdfHeightPt > 0) {
    const leftPct = Math.max(0, Math.min(98, (hl.h / state.latexPdfWidthPt) * 100));
    const topPct = Math.max(0, Math.min(98, (hl.top / state.latexPdfHeightPt) * 100));
    const widthPct = Math.max(2, Math.min(100 - leftPct, (hl.W / state.latexPdfWidthPt) * 100));
    const heightPct = Math.max(1.2, Math.min(100 - topPct, (hl.H / state.latexPdfHeightPt) * 100));
    highlightHtml = `<div id="ltx-sync-highlight" class="synctex-highlight" style="left:${leftPct.toFixed(3)}%;top:${topPct.toFixed(3)}%;width:${widthPct.toFixed(3)}%;height:${heightPct.toFixed(3)}%"><span class="synctex-highlight-tag">${esc(hl.lineLabel)}</span></div>`;
  }

  const editorCardHtml = state.latexFile
    ? `<div class="card">
        <div class="card-head">
          <div>
            <h3 class="mono">${esc(state.latexFile)}</h3>
            <p class="mono">mtimeMs: ${esc(state.latexMtimeMs)} · ${state.latexHasBackup ? "存在 .edit_backup 原稿" : "尚未修改"}</p>
          </div>
          <div class="btn-row">
            <span class="pill" id="ltx-cursor-badge">${esc(cursorLabel)}</span>
            <button class="btn small primary" id="ltx-sync-to-pdf" type="button" ${state.latexSyncBusy ? "disabled" : ""}>定位到 PDF (SyncTeX)</button>
            <label class="ltx-toggle"><input id="ltx-auto-sync" type="checkbox" ${state.latexAutoSync ? "checked" : ""}> 选中自动跟随 PDF</label>
            <button class="btn small" id="ltx-reload" type="button">重新读取</button>
            <button class="btn small" id="ltx-open-side" type="button">在侧栏打开源码</button>
            <button class="btn small primary" id="ltx-save" type="button">防冲突保存 (save)</button>
            <button class="btn small danger" id="ltx-restore" type="button" ${state.latexHasBackup ? "" : "disabled"}>恢复原稿 (restore)</button>
          </div>
        </div>
        <div class="card-body">
          <textarea id="ltx-editor" class="code-editor">${esc(state.latexContent)}</textarea>
        </div>
      </div>`
    : "";

  const pdfCardHtml =
    state.latexFile || hasPdfPreview
      ? `<div class="card">
          <div class="card-head">
            <div>
              <h3>内置分页 PDF 预览</h3>
              <p class="mono">${
                state.latexPdfPath
                  ? `${esc(state.latexPdfPath.split("/").pop() || "preview.pdf")} · 第 ${state.latexPdfPage} / ${state.latexPdfPageCount || "?"} 页${state.latexHasSyncTeX ? " · 点击 PDF 可反向定位源码" : ""}`
                  : "尚未加载 PDF 预览"
              }</p>
            </div>
            <div class="btn-row">
              <button class="btn small" id="ltx-pdf-first" type="button" ${!state.latexPdfPath || state.latexPdfPage <= 1 || state.latexPdfLoading ? "disabled" : ""}>首页</button>
              <button class="btn small" id="ltx-pdf-prev" type="button" ${!state.latexPdfPath || state.latexPdfPage <= 1 || state.latexPdfLoading ? "disabled" : ""}>上一页</button>
              <input class="pdf-jump-input" id="ltx-pdf-jump" type="number" min="1" max="${Math.max(1, state.latexPdfPageCount || 1)}" value="${state.latexPdfPage}" ${state.latexPdfPath ? "" : "disabled"}>
              <button class="btn small" id="ltx-pdf-jump-go" type="button" ${!state.latexPdfPath || state.latexPdfLoading ? "disabled" : ""}>跳转</button>
              <button class="btn small" id="ltx-pdf-next" type="button" ${!state.latexPdfPath || (state.latexPdfPageCount > 0 && state.latexPdfPage >= state.latexPdfPageCount) || state.latexPdfLoading ? "disabled" : ""}>下一页</button>
              <button class="btn small" id="ltx-pdf-last" type="button" ${!state.latexPdfPath || state.latexPdfPageCount <= 0 || state.latexPdfPage >= state.latexPdfPageCount || state.latexPdfLoading ? "disabled" : ""}>末页</button>
              <button class="btn small" id="ltx-pdf-zoom-out" type="button" ${!state.latexPdfDataUrl || state.latexPdfZoom <= 80 ? "disabled" : ""}>-</button>
              <span class="pill">${state.latexPdfZoom}%</span>
              <button class="btn small" id="ltx-pdf-zoom-in" type="button" ${!state.latexPdfDataUrl || state.latexPdfZoom >= 180 ? "disabled" : ""}>+</button>
              ${state.latexPdfPath ? `<button class="btn small" id="ltx-pdf-ext-2" type="button">用系统预览打开</button>` : ""}
              ${hasPdfPreview ? `<button class="btn small" id="ltx-pdf-close" type="button">关闭</button>` : ""}
            </div>
          </div>
          <div class="card-body">
            ${
              state.latexPdfError
                ? `<div class="callout error"><div>PDF 渲染失败：${esc(state.latexPdfError)}</div></div>`
                : state.latexPdfLoading && !state.latexPdfDataUrl
                  ? `<p>正在渲染 PDF 第 ${state.latexPdfPage} 页…</p>`
                  : state.latexPdfDataUrl
                    ? `<div class="pdf-preview-stage">
                        ${state.latexPdfLoading ? `<div class="pill blue" style="margin-bottom:8px">正在加载第 ${state.latexPdfPage} 页…</div>` : ""}
                        <div class="pdf-page-wrap" style="width:${state.latexPdfZoom}%">
                          <img id="ltx-pdf-img" class="pdf-page-img ${state.latexHasSyncTeX ? "clickable" : ""}" src="${state.latexPdfDataUrl}" alt="PDF Page ${state.latexPdfPage}" title="${state.latexHasSyncTeX ? "点击 PDF 任意位置反向定位到左侧 LaTeX 源码行 (Inverse SyncTeX)" : ""}">
                          ${highlightHtml}
                        </div>
                      </div>`
                    : `<div class="callout"><div>点击「编译预览」即可在右侧实时对照 PDF 并启用双向 SyncTeX 定位。</div></div>`
            }
          </div>
        </div>`
      : "";

  return `
    <div class="page-head">
      <div>
        <div class="eyebrow">LATEX CO-EDITING & XELATEX PREVIEW</div>
        <h1>LaTeX 防冲突协同编辑 · CJK 自动 XeLaTeX 编译</h1>
        <p>遵循 Desktop 同源安全协议：open → read (取 mtimeMs) → save (首改备份 .edit_backup/) → -pdfxe/-pdf 编译与双向 SyncTeX。</p>
      </div>
    </div>

    <div class="card card-pad">
      <div class="grid grid-2">
        <div class="field"><label>运行目录 (run_dir)</label><input id="ltx-run-dir" type="text" value="${esc(state.selectedRunDir)}"></div>
        <div class="field" style="align-self:end">
          <div class="btn-row">
            <button class="btn primary" id="ltx-open-btn" type="button">打开源码工程 (open)</button>
            <button class="btn" id="ltx-compile-btn" type="button">编译预览 (auto XeLaTeX)</button>
            <button class="btn" id="ltx-trace-btn" type="button">引文溯源</button>
            <button class="btn" id="ltx-polish-diag-btn" type="button">摘要诊断 (离线)</button>
            <button class="btn" id="ltx-polish-rewrite-btn" type="button">摘要逐句改写 (AI)</button>
            <button class="btn" id="ltx-overleaf-btn" type="button">导出 Overleaf ZIP</button>
          </div>
        </div>
      </div>
      ${
        open
          ? `<div class="btn-row" style="margin-top:8px">
              <span class="pill">主文件: ${esc(open.mainFile)}</span>
              <span class="pill ${open.runActive ? "red" : "green"}">${open.runActive ? "论文运行中 · 只读锁定" : "可安全保存"}</span>
              <span class="pill">已备份原稿: ${(open.backedUp || []).length}</span>
              <span class="pill ${state.latexHasSyncTeX ? "green" : ""}">${state.latexHasSyncTeX ? "SyncTeX 已就绪" : "待编译 SyncTeX"}</span>
              <button class="btn small" id="ltx-toggle-split" type="button">${state.latexSplitView ? "切换上下布局" : "切换左右分栏"}</button>
            </div>
            <div class="preset-grid" style="margin-top:10px">
              ${(open.files || []).map((f: string) => `<button class="chip ${f === state.latexFile ? "active" : ""}" data-ltx-file="${esc(f)}" type="button">${esc(f)}</button>`).join("")}
            </div>`
          : ""
      }
    </div>

    ${
      showSplit
        ? `<div class="latex-split ${state.latexSplitView ? "" : "stacked"}">
            ${editorCardHtml}
            ${pdfCardHtml}
          </div>`
        : ""
    }

    ${
      state.latexPolishResult
        ? (() => {
            const pol = state.latexPolishResult;
            const bm = pol.beforeAudit?.metrics || {};
            const am = pol.afterAudit?.metrics || null;
            const fmtMetrics = (m: any, prefix: string) =>
              `<div class="btn-row" style="margin-bottom:8px">
                <span class="pill blue">${esc(prefix)}</span>
                <span class="pill">词数: ${esc(m.words ?? 0)}</span>
                <span class="pill">句尾分词占比: ${((Number(m.participleTailRatio || 0)) * 100).toFixed(1)}%</span>
                <span class="pill">排比对比占比: ${((Number(m.antithesisRatio || 0)) * 100).toFixed(1)}%</span>
                <span class="pill">连接词/百词: ${esc(m.connectorPer100w ?? 0)}</span>
                <span class="pill">数字个数: ${esc(m.numbers ?? 0)}</span>
              </div>`;
            const diagRows: any[] = pol.fieldDiagnosis || [];
            const changes: any[] = pol.changeTable || [];
            const lims: string[] = pol.limitationsNotes || [];
            const qs: string[] = pol.missingQuestions || [];
            const issues: string[] = pol.beforeAudit?.issues || [];
            return `<div class="card">
              <div class="card-head">
                <div>
                  <h3>摘要诊断与获奖论文风格改写 (${esc(pol.mode === "rewrite" ? "AI 逐句改写对照" : "离线结构诊断")})</h3>
                  <p class="mono">${esc(pol.summary || "")}</p>
                </div>
                <button class="btn small" id="ltx-polish-close" type="button">关闭</button>
              </div>
              <div class="card-body">
                ${fmtMetrics(bm, "改写前 / 当前")}
                ${am ? fmtMetrics(am, "改写后") : ""}
                ${
                  issues.length
                    ? `<div style="margin-bottom:10px">${issues
                        .map((iss: string) => `<div class="callout warning" style="margin-bottom:6px"><div>${esc(iss)}</div></div>`)
                        .join("")}</div>`
                    : `<div class="callout" style="margin-bottom:10px"><div>当前摘要未检出结构性 AI 腔或标点越界。</div></div>`
                }
                ${
                  diagRows.length
                    ? `<table class="po-table" style="width:100%;margin-bottom:12px">
                        <thead><tr><th>字段</th><th>状态</th><th>识别摘要</th><th>补充建议</th></tr></thead>
                        <tbody>
                          ${diagRows
                            .map(
                              (r) => `<tr>
                                <td><strong>${esc(r.label)}</strong></td>
                                <td><span class="pill ${r.status === "present" ? "green" : r.status === "vague" ? "blue" : "red"}">${esc(r.status)}</span></td>
                                <td class="mono">${esc(r.evidence || "-")}</td>
                                <td>${esc(r.suggestion || "完整")}</td>
                              </tr>`,
                            )
                            .join("")}
                        </tbody>
                      </table>`
                    : ""
                }
                ${
                  pol.revisedAbstract
                    ? `<div class="callout" style="margin-bottom:10px"><div><strong>改写后摘要建议：</strong><br>${esc(pol.revisedAbstract)}</div></div>`
                    : ""
                }
                ${
                  changes.length
                    ? `<table class="po-table" style="width:100%;margin-bottom:12px">
                        <thead><tr><th>原句</th><th>改后句</th><th>修改理由 / 获奖论文模式</th></tr></thead>
                        <tbody>
                          ${changes
                            .map(
                              (c) => `<tr>
                                <td>${esc(c.original || "")}</td>
                                <td>${esc(c.revised || "")}</td>
                                <td>${esc(c.reason || "")}${c.pattern ? ` <span class="pill">${esc(c.pattern)}</span>` : ""}</td>
                              </tr>`,
                            )
                            .join("")}
                        </tbody>
                      </table>`
                    : ""
                }
                ${
                  qs.length
                    ? `<div class="callout warning" style="margin-bottom:8px"><div><strong>建议作者补充的关键事实：</strong><ul style="margin:6px 0 0 18px">${qs.map((q) => `<li>${esc(q)}</li>`).join("")}</ul></div></div>`
                    : ""
                }
                ${
                  lims.length
                    ? `<div class="callout"><div><strong>留给 Limitations 章节的要点：</strong><ul style="margin:6px 0 0 18px">${lims.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></div></div>`
                    : ""
                }
              </div>
            </div>`;
          })()
        : ""
    }

    ${
      comp
        ? `<div class="card">
            <div class="card-head">
              <div><h3>编译结果</h3></div>
              <div class="btn-row">
                ${statusPillHtml(comp.ok ? "completed" : "failed")}
                <span class="pill blue">引擎: ${esc(comp.engine || "pdflatex")}</span>
                ${comp.pdfPath ? `<button class="btn small primary" id="ltx-preview-inline" type="button">内置分页预览</button>` : ""}
                ${comp.pdfPath ? `<button class="btn small" id="ltx-open-pdf-external" type="button">用系统预览打开</button>` : ""}
                ${comp.pdfPath ? `<button class="btn small" id="ltx-open-pdf-side" type="button">在侧栏打开预览 PDF</button>` : ""}
              </div>
            </div>
            <div class="card-body">
              ${
                (comp.issues || []).length
                  ? `<div style="margin-bottom:10px">${comp.issues
                      .slice(0, 12)
                      .map((iss: any) => `<div class="callout ${iss.severity === "error" ? "error" : "warning"}" style="margin-bottom:6px"><div><span class="mono">${esc(iss.file || comp.mainTex || "")}${iss.line ? `:${iss.line}` : ""}</span> — ${esc(iss.message)}</div></div>`)
                      .join("")}</div>`
                  : ""
              }
              <pre class="po-pre">${esc(comp.logTail || "")}</pre>
            </div>
          </div>`
        : ""
    }
    ${state.latexResultText ? `<div class="card"><div class="card-head"><div><h3>LaTeX 工具输出</h3></div></div><div class="card-body"><pre class="po-pre">${esc(state.latexResultText)}</pre></div></div>` : ""}
  `;
}

function renderExports(): string {
  const env = state.snapshot.env || {};
  return `
    <div class="page-head">
      <div>
        <div class="eyebrow">SUBMISSION AUDIT & ARTIFACT EXPORT</div>
        <h1>投稿体检（8+ 工具）与多格式成果导出</h1>
        <p>一键对运行目录执行 Nature + 顶会文风/配图体检、生成作者交付报告 report.md、联网核对 DOI、双盲泄密检查、arXiv 打包，或导出多格式报告。</p>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><div><h3>投稿核查工具箱</h3><p class="mono">目标目录：${esc(state.selectedRunDir || "（请先填写入 run_dir）")}</p></div></div>
      <div class="card-body">
        <div class="field"><label>目标 run_dir</label><input id="aud-run-dir" type="text" value="${esc(state.selectedRunDir)}"></div>
        <div class="btn-row">
          ${[
            ["manuscript-audit", "全面手稿体检 (Nature + 顶会文风/配图)"],
            ["author-report", "生成作者交付报告 (report.md)"],
            ["cite-online", "联网核验引用"],
            ["blind", "双盲匿名检查"],
            ["diagnose", "TeX 日志诊断"],
            ["versions", "版本盘点"],
            ["arxiv", "生成 arXiv ZIP"],
          ]
            .map(([act, lbl]) => `<button class="btn small" data-audit-act="${esc(act)}" type="button">${esc(lbl)}</button>`)
            .join("")}
        </div>
        ${state.auditResult ? `<pre class="po-pre" style="margin-top:12px">${esc(state.auditResult)}</pre>` : ""}
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <div><h3>Markdown → 交互网页 / Word / PDF / Google Drive</h3></div>
        <div class="btn-row">
          <span class="pill ${env.defaultPdfDirReady ? "green" : ""}">默认 PDF 目录: ${env.defaultPdfDirReady ? "就绪" : "默认"}</span>
          <span class="pill ${env.defaultHtmlDirReady ? "green" : ""}">默认 HTML 目录: ${env.defaultHtmlDirReady ? "就绪" : "默认"}</span>
        </div>
      </div>
      <div class="card-body">
        <div class="grid grid-2">
          <div class="field"><label>文档标题</label><input id="exp-title" type="text" placeholder="PaperOrchestra_交付报告"></div>
          <div class="field"><label>格式</label><select id="exp-fmt"><option value="html">交互式 HTML（Artifacts 风格）</option><option value="pdf">出版级 PDF</option><option value="docx">Word (.docx)</option></select></div>
        </div>
        <div class="field"><label>Markdown 源文件路径（优先）或下方直接粘贴内容</label><input id="exp-md-path" type="text" placeholder="/path/to/report.md"></div>
        <div class="field"><label>Markdown 内容</label><textarea id="exp-md-body" rows="4" placeholder="# 标题&#10;粘贴内容或直接填上方路径…"></textarea></div>
        <div class="btn-row">
          <button class="btn primary" id="exp-run-btn" type="button">导出文档</button>
          <button class="btn" id="exp-drive-btn" type="button">同步到 Google Drive</button>
        </div>
        ${state.exportResult ? `<pre class="po-pre" style="margin-top:12px">${esc(state.exportResult)}</pre>` : ""}
      </div>
    </div>
  `;
}

function renderSkills(): string {
  const skills: any[] = state.snapshot.skills || [];
  const memories: any[] = state.snapshot.memories || [];
  const hosts: any[] = state.snapshot.hosts || [];
  const readiness = state.readinessData;
  const settings = state.settingsData;

  return `
    <div class="page-head">
      <div>
        <div class="eyebrow">SKILLS, MEMORIES & SETUP</div>
        <h1>5 个内置技能 · 记忆偏好 · 环境配置与算力</h1>
        <p>与 Studio Desktop 共享内置/自定义技能库、研究偏好及 20+ 平台布尔就绪检测（从不显示密钥值）。</p>
      </div>
      <div class="btn-row">
        <button class="btn small" id="sk-load-env" type="button">刷新设置与 20+ 平台检测</button>
        <button class="btn small" id="sk-val-prov" type="button">校验 providers.json</button>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><div><h3>科研技能库（${skills.length}）</h3><p>包含 5 个官方内置技能与自定义技能</p></div></div>
      <div class="card-body">
        <div class="preset-grid">
          ${skills
            .map(
              (s) =>
                `<button class="chip ${state.selectedSkill?.id === s.id ? "active" : ""}" data-get-skill="${esc(s.id)}" type="button">${s.builtin ? "内置 · " : ""}${esc(s.name)}</button>`,
            )
            .join("")}
        </div>
        ${
          state.selectedSkill
            ? `<div style="margin-top:10px">
                <div class="btn-row" style="margin-bottom:6px">
                  <strong>${esc(state.selectedSkill.name)}</strong>
                  <span class="pill">${esc(state.selectedSkill.category || "")}</span>
                  <span class="mono">${esc(state.selectedSkill.id)}</span>
                  <button class="btn small primary" id="sk-use-chat" type="button">在对话中使用此技能</button>
                </div>
                <pre class="po-pre">${esc(state.selectedSkill.content || "")}</pre>
              </div>`
            : ""
        }
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><div><h3>长期研究记忆（${memories.length}）</h3></div></div>
        <div class="card-body">
          ${memories.length === 0 ? `<div style="color:var(--muted)">暂无长期记忆条目。</div>` : memories.map((m) => `<div style="padding:6px 0;border-bottom:1px solid var(--line)"><strong>${esc(m.title)}</strong> <span class="pill ${m.enabled ? "green" : ""}">${m.enabled ? "已启用" : "停用"}</span><div style="font-size:12px;color:var(--muted)">${esc((m.content || "").slice(0, 120))}</div></div>`).join("")}
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div><h3>SSH / GPU 算力主机（${hosts.length}）</h3></div></div>
        <div class="card-body">
          ${
            hosts.length === 0
              ? `<div style="color:var(--muted)">尚未配置远程算力主机。</div>`
              : hosts
                  .map(
                    (h) => `<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--line)">
                      <div><strong>${esc(h.alias)}</strong> <span class="mono" style="font-size:11.5px;color:var(--muted)">${esc(h.username)}@${esc(h.host)}:${esc(h.port)}</span></div>
                      <button class="btn small" data-test-host="${esc(h.id)}" type="button">测速/GPU</button>
                    </div>`,
                  )
                  .join("")
          }
        </div>
      </div>
    </div>

    ${
      settings || readiness
        ? `<div class="card">
            <div class="card-head"><div><h3>设置、20+ 模型平台就绪度与安装脚本</h3></div></div>
            <div class="card-body">
              ${readiness ? `<div class="preset-grid" style="margin-bottom:10px">${(readiness.builtins || []).map((b: any) => `<span class="pill ${b.configured ? "green" : ""}">${esc(b.name)}: ${b.configured ? "已配" : "未配"}</span>`).join("")}</div>` : ""}
              <pre class="po-pre">${esc(JSON.stringify({ settings, scholar: readiness?.scholarEnv, setupScripts: settings?.setupScripts || readiness?.setupScripts }, null, 2))}</pre>
            </div>
          </div>`
        : ""
    }
    ${state.providersValidation ? `<div class="card"><div class="card-head"><div><h3>providers.json 校验结果</h3></div></div><div class="card-body"><pre class="po-pre">${esc(state.providersValidation)}</pre></div></div>` : ""}
  `;
}

function renderTools(): string {
  const q = state.toolSearch.trim().toLowerCase();
  const list = TOOL_NODES.filter(
    (n) =>
      !q ||
      [n.tool, n.action || "", n.title, n.description].join(" ").toLowerCase().includes(q),
  );
  const sel: ToolNodeMeta = TOOL_NODES.find((n) => n.id === state.selectedToolId) || TOOL_NODES[0];
  return `
    <div class="page-head">
      <div>
        <div class="eyebrow">SAFE TOOL CONSOLE</div>
        <h1>全工具安全控制台（${TOOL_NODES.length} 能力节点）</h1>
        <p>按只读 / 写入 / 破坏性三档安全护栏执行任意 MCP 工具，破坏性动作必须手动输入准确名才可执行。</p>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><div><h3>选择工具节点</h3></div><input id="tc-search" type="text" placeholder="筛选…" value="${esc(state.toolSearch)}" style="width:180px"></div>
        <div class="card-body" style="max-height:460px;overflow:auto;display:grid;gap:6px">
          ${list
            .map(
              (n) => `<button class="tree-node" data-tc-pick="${esc(n.id)}" type="button" style="${n.id === sel.id ? "border-color:var(--green);background:var(--green-soft)" : ""}">
                <div style="display:flex;justify-content:space-between;gap:6px;width:100%"><strong>${esc(n.title)}</strong><span class="pill ${n.risk === "read" ? "green" : n.risk === "write" ? "amber" : "red"}">${n.risk === "read" ? "只读" : n.risk === "write" ? "写入" : "破坏性"}</span></div>
                <span class="mono" style="color:var(--green)">${esc(n.tool)}${n.action ? ` (${esc(n.action)})` : ""}</span>
              </button>`,
            )
            .join("")}
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div><h3>${esc(sel.title)}</h3><p class="mono">${esc(sel.tool)}${sel.action ? ` · ${esc(sel.action)}` : ""}</p></div>
          <span class="pill ${sel.risk === "read" ? "green" : sel.risk === "write" ? "amber" : "red"}">${esc(sel.risk)}</span>
        </div>
        <div class="card-body">
          <div style="font-size:12.5px;margin-bottom:8px">${esc(sel.description)}</div>
          <div class="callout ${sel.risk === "destructive" ? "error" : "warning"}" style="margin-bottom:10px">${esc(sel.guardrail)}</div>
          <div class="field"><label>JSON 参数 (arguments)</label><textarea id="tc-args" rows="6" class="mono">${esc(state.toolArgsText)}</textarea></div>
          <button class="btn ${sel.risk === "destructive" ? "danger" : "primary"}" id="tc-exec" type="button">${sel.risk === "read" ? "立即执行 (只读)" : sel.risk === "write" ? "执行写入操作…" : "破坏性操作 (需输入确认)…"}</button>
          ${state.toolOutput ? `<div style="margin-top:10px"><pre class="po-pre">${esc(state.toolOutput)}</pre></div>` : ""}
        </div>
      </div>
    </div>
  `;
}

function renderModal(): string {
  const m = state.confirmModal;
  if (!m) return "";
  const locked = Boolean(m.requireText && m.typed.trim() !== m.requireText);
  return `
    <div class="po-modal-backdrop" id="confirm-backdrop">
      <div class="po-modal" role="dialog" aria-modal="true">
        <h2 style="margin:0 0 8px;font-size:16px">${esc(m.title)}</h2>
        <p style="margin:0 0 12px;color:var(--muted)">${esc(m.body)}</p>
        ${(m.inputs || [])
          .map(
            (inp) => `
          <div class="field">
            <label>${esc(inp.label)}</label>
            <input type="text" data-modal-input="${esc(inp.key)}" value="${esc(m.values[inp.key] ?? inp.value)}" placeholder="${esc(inp.placeholder || "")}">
          </div>`,
          )
          .join("")}
        ${
          m.requireText
            ? `<div class="field"><label>请输入 <span class="mono">${esc(m.requireText)}</span> 以确认此操作：</label><input id="confirm-typed" type="text" value="${esc(m.typed)}" placeholder="${esc(m.requireText)}"></div>`
            : ""
        }
        <div class="btn-row" style="justify-content:flex-end">
          <button class="btn" id="confirm-cancel" type="button">取消</button>
          <button class="btn ${m.danger ? "danger" : "primary"}" id="confirm-ok" type="button" ${locked ? "disabled" : ""}>确认执行</button>
        </div>
      </div>
    </div>
  `;
}

function render(): void {
  const root = document.getElementById("root")!;
  const bridgeOnline = Boolean(state.snapshot.bridge?.online);
  const existingEd = document.getElementById("ltx-editor") as HTMLTextAreaElement | null;
  const prevEd = existingEd
    ? {
        selStart: existingEd.selectionStart ?? 0,
        selEnd: existingEd.selectionEnd ?? 0,
        scrollTop: existingEd.scrollTop,
        focused: document.activeElement === existingEd,
      }
    : null;
  const existingStage = document.querySelector(".pdf-preview-stage") as HTMLElement | null;
  const prevStage = existingStage
    ? { scrollTop: existingStage.scrollTop, scrollLeft: existingStage.scrollLeft }
    : null;

  const tabContentMap: Record<TabId, () => string> = {
    overview: renderOverview,
    projects: renderProjects,
    runs: renderRuns,
    library: renderLibrary,
    figures: renderFigures,
    latex: renderLatex,
    exports: renderExports,
    skills: renderSkills,
    tools: renderTools,
  };

  root.innerHTML = `
    <div class="po-shell">
      <aside class="po-sidebar">
        <div class="po-brand">
          <div class="po-brand-mark">PO</div>
          <div><strong>PaperOrchestra</strong><span>实时论文工作台 v1.1</span></div>
        </div>
        <nav class="po-nav">
          ${NAV_ITEMS.map((item) => {
            const cnt = tabCount(item.id);
            return `<button class="po-nav-btn ${state.tab === item.id ? "active" : ""}" data-tab="${item.id}" type="button">
              <span class="po-nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">${item.icon}</svg></span>
              <span>${esc(item.label)}</span>
              ${cnt ? `<span class="po-nav-count">${esc(cnt)}</span>` : ""}
            </button>`;
          }).join("")}
        </nav>
        <div class="po-sidebar-foot">
          <strong>原生 Antigravity MCP App</strong>
          <div class="mono" style="font-size:11px">DB: ${esc((state.snapshot.env?.dbPath || "").split("/").slice(-2).join("/"))}</div>
        </div>
      </aside>

      <div class="po-main">
        <div class="po-compact-bar">
          <div class="po-compact-brand">
            <strong>PaperOrchestra · 实时工作台</strong>
            <span class="pill ${bridgeOnline ? "green" : ""}">${bridgeOnline ? "Desktop 在线" : "直连后端"}</span>
          </div>
          <div class="po-compact-tabs">
            ${NAV_ITEMS.map((item) => `<button class="po-nav-btn ${state.tab === item.id ? "active" : ""}" data-tab="${item.id}" type="button">${esc(item.label)}</button>`).join("")}
          </div>
        </div>

        <header class="po-topbar">
          <div class="po-topbar-title">
            <strong>${esc(NAV_ITEMS.find((n) => n.id === state.tab)?.label || "工作台")}</strong>
            <span>${state.selectedRunDir ? esc(state.selectedRunDir) : esc(state.snapshot.env?.repoDir || "本地直连 PaperOrchestra Studio")}</span>
          </div>
          <div class="po-topbar-actions">
            <span class="pill ${bridgeOnline ? "green" : ""}">${bridgeOnline ? "Desktop 桥接在线" : "MCP 直连模式"}</span>
            <button class="btn small" id="tb-refresh" type="button" ${state.loading ? "disabled" : ""}>${state.loading ? "刷新中…" : "刷新状态"}</button>
          </div>
        </header>

        <main class="po-content">
          ${state.notice ? `<div class="callout warning"><div>${esc(state.notice)}</div></div>` : ""}
          ${state.error ? `<div class="callout error"><div>${esc(state.error)}</div></div>` : ""}
          ${tabContentMap[state.tab]()}
        </main>
      </div>
    </div>
    ${renderModal()}
  `;

  bindEvents();

  const nextEd = document.getElementById("ltx-editor") as HTMLTextAreaElement | null;
  if (nextEd) {
    if (pendingEditorJumpLine !== null) {
      const targetLine = pendingEditorJumpLine;
      pendingEditorJumpLine = null;
      const { start, end } = lineRangeOffsets(nextEd.value, targetLine);
      nextEd.focus({ preventScroll: true });
      nextEd.setSelectionRange(start, end);
      const lineHeight = parseFloat(getComputedStyle(nextEd).lineHeight) || 19;
      nextEd.scrollTop = Math.max(0, (targetLine - 1) * lineHeight - nextEd.clientHeight * 0.35);
      state.latexCursorStartLine = targetLine;
      state.latexCursorEndLine = targetLine;
      state.latexCursorCol = 1;
      const badge = document.getElementById("ltx-cursor-badge");
      if (badge) badge.textContent = `L${targetLine}`;
    } else if (prevEd) {
      nextEd.scrollTop = prevEd.scrollTop;
      if (prevEd.focused) {
        nextEd.focus({ preventScroll: true });
        nextEd.setSelectionRange(prevEd.selStart, prevEd.selEnd);
      }
    }
  }

  const nextStage = document.querySelector(".pdf-preview-stage") as HTMLElement | null;
  if (nextStage) {
    const hlEl = document.getElementById("ltx-sync-highlight");
    if (pendingScrollToHighlight && hlEl) {
      pendingScrollToHighlight = false;
      const stageRect = nextStage.getBoundingClientRect();
      const hlRect = hlEl.getBoundingClientRect();
      const targetTop = nextStage.scrollTop + (hlRect.top - stageRect.top) - nextStage.clientHeight * 0.38;
      nextStage.scrollTo({ top: Math.max(0, targetTop), behavior: "smooth" });
    } else if (prevStage) {
      nextStage.scrollTop = prevStage.scrollTop;
      nextStage.scrollLeft = prevStage.scrollLeft;
    }
  }
}

function askConfirm(opts: Omit<ConfirmState, "typed" | "values">): void {
  const values: Record<string, string> = {};
  for (const inp of opts.inputs || []) values[inp.key] = inp.value;
  state.confirmModal = { ...opts, typed: "", values };
  render();
}

async function runToolJson(name: string, args: Record<string, unknown>, targetKey: "toolOutput" | "libOutput" | "figResult" | "latexResultText" | "auditResult" | "exportResult" = "toolOutput"): Promise<Record<string, any>> {
  const res = await bridge.callTool(name, args);
  state[targetKey] = JSON.stringify(res, null, 2);
  render();
  return res;
}

function bindEvents(): void {
  // Navigation
  document.querySelectorAll<HTMLElement>("[data-tab], [data-jump]").forEach((el) => {
    el.addEventListener("click", () => {
      const next = (el.dataset.tab || el.dataset.jump) as TabId;
      if (next) {
        state.tab = next;
        render();
        if (next === "library" && !state.libPage) void loadLibraryPage();
        if (next === "skills" && !state.readinessData) void loadEnvSetup();
      }
    });
  });

  document.getElementById("tb-refresh")?.addEventListener("click", () => void refreshSnapshot(false));

  // Overview
  document.getElementById("ov-open-tree")?.addEventListener("click", async () => {
    try {
      await bridge.callTool("po_open_tool_tree", {});
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("ov-open-desktop")?.addEventListener("click", async () => {
    try {
      await bridge.callTool("po_studio_bridge", { action: "open_studio", tab: "workspace" });
      showToast("已请求打开 PaperOrchestra Studio 桌面端");
      await refreshSnapshot(true);
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("ov-resume-run")?.addEventListener("click", () => {
    const r = activeRun();
    if (!r) return;
    askConfirm({
      title: "确认从检查点恢复流水线",
      body: `将携带 --resume 继续执行 ${r.run_dir}。这会调用已配置的 LLM 模型。`,
      onConfirm: async () => {
        await bridge.callTool("po_projects", { action: "resume_run", run_id: r.id });
        showToast("已触发断点续跑");
        await refreshSnapshot(true);
      },
    });
  });

  document.querySelectorAll<HTMLElement>("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const p = INTERVENTION_PRESETS[Number(btn.dataset.preset)];
      if (p) {
        state.interventionTarget = p.target;
        state.interventionNote = p.prompt;
        render();
      }
    });
  });
  document.getElementById("int-target")?.addEventListener("change", (ev) => {
    state.interventionTarget = (ev.target as HTMLSelectElement).value;
    render();
  });
  document.getElementById("int-note")?.addEventListener("input", (ev) => {
    state.interventionNote = (ev.target as HTMLTextAreaElement).value;
    const pre = document.querySelector(".card pre.po-pre");
    if (pre) pre.textContent = interventionText();
  });
  document.getElementById("int-cons")?.addEventListener("change", (ev) => {
    state.interventionConservative = (ev.target as HTMLInputElement).checked;
    render();
  });
  document.getElementById("int-clear")?.addEventListener("click", () => {
    state.interventionNote = "";
    render();
  });
  document.getElementById("int-send")?.addEventListener("click", async () => {
    const txt = interventionText();
    await bridge.sendToChat(txt);
    state.interventionHistory.unshift({ target: state.interventionTarget, note: txt.slice(0, 80), at: fmtDate(new Date().toISOString()) });
  });
  document.getElementById("int-ctx")?.addEventListener("click", async () => {
    await bridge.attachContext(interventionText());
  });

  // Projects
  document.getElementById("proj-toggle-new")?.addEventListener("click", () => {
    const box = document.getElementById("proj-new-box");
    if (box) box.hidden = !box.hidden;
  });
  document.getElementById("proj-import-cvpr")?.addEventListener("click", () => {
    askConfirm({
      title: "导入 CVPR 官方示例项目",
      body: "将在本地数据库新建一条 TSAM (CVPR 2025) 示例项目记录并挂载示例图。",
      onConfirm: async () => {
        const res = await bridge.callTool("po_projects", { action: "import_cvpr_example" });
        state.selectedProjectId = String((res.project as any)?.id || "");
        showToast("CVPR 示例项目已导入");
        await refreshSnapshot(true);
      },
    });
  });
  document.getElementById("proj-create-submit")?.addEventListener("click", async () => {
    const title = (document.getElementById("new-proj-title") as HTMLInputElement)?.value.trim();
    const tpl = (document.getElementById("new-proj-tpl") as HTMLSelectElement)?.value || "cvpr2025";
    if (!title) return showToast("请输入项目标题", true);
    try {
      const res = await bridge.callTool("po_projects", { action: "create", title, template_name: tpl });
      state.selectedProjectId = String((res.project as any)?.id || "");
      showToast("项目已创建");
      await refreshSnapshot(true);
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("proj-intake-parse")?.addEventListener("click", () => {
    askConfirm({
      title: "解析立项单文档",
      body: "支持 .pdf / .docx / .md / .txt，自动抽取标题、模板、研究想法与实验记录。",
      inputs: [{ key: "file_path", label: "立项单绝对路径", value: "", placeholder: "/path/to/intake_form.pdf" }],
      onConfirm: async (vals) => {
        const p = (vals.file_path || "").trim();
        if (!p) return showToast("请输入立项单路径", true);
        const res = await bridge.callTool("po_parse_intake_form", { file_path: p });
        showToast("已解析立项单：" + String((res as any).title || "完成"));
      },
    });
  });
  document.querySelectorAll<HTMLElement>("[data-select-proj]").forEach((el) => {
    el.addEventListener("click", async () => {
      state.selectedProjectId = el.dataset.selectProj!;
      await refreshSnapshot(true);
    });
  });
  document.getElementById("ep-save")?.addEventListener("click", async () => {
    const p = state.selectedProject;
    if (!p) return;
    try {
      await bridge.callTool("po_projects", {
        action: "update",
        project_id: p.id,
        expected_updated_at: p.updated_at,
        title: (document.getElementById("ep-title") as HTMLInputElement).value,
        template_name: (document.getElementById("ep-tpl") as HTMLInputElement).value,
        writer_model: (document.getElementById("ep-writer") as HTMLInputElement).value,
        reflection_model: (document.getElementById("ep-refl") as HTMLInputElement).value,
        use_plotting: (document.getElementById("ep-plot") as HTMLInputElement).checked,
        idea_markdown: (document.getElementById("ep-idea") as HTMLTextAreaElement).value,
        experimental_log_markdown: (document.getElementById("ep-log") as HTMLTextAreaElement).value,
      });
      showToast("已通过 updatedAt 锁验证并保存项目");
      await refreshSnapshot(true);
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("proj-start-run")?.addEventListener("click", () => {
    const p = state.selectedProject;
    if (!p) return;
    askConfirm({
      title: "启动论文多智能体流水线",
      body: `将为项目「${p.title}」在后台物化材料并启动写作流水线（会消耗已配模型额度）。`,
      onConfirm: async () => {
        const res = await bridge.callTool("po_start_paper_run", { project_id: p.id });
        state.selectedRunDir = String(res.run_dir || "");
        state.tab = "runs";
        showToast("已启动后台运行: " + String(res.job_id || ""));
        await refreshSnapshot(true);
      },
    });
  });
  document.getElementById("proj-delete")?.addEventListener("click", () => {
    const p = state.selectedProject;
    if (!p) return;
    askConfirm({
      title: "删除项目（破坏性操作）",
      body: `将从数据库删除项目「${p.title}」及其运行索引。`,
      requireText: "delete",
      danger: true,
      onConfirm: async () => {
        await bridge.callTool("po_projects", { action: "delete", project_id: p.id });
        state.selectedProjectId = "";
        state.selectedProject = null;
        showToast("项目已删除");
        await refreshSnapshot(true);
      },
    });
  });
  document.getElementById("ep-add-fig")?.addEventListener("click", () => {
    if (!state.selectedProjectId) return;
    askConfirm({
      title: "挂载项目原始图稿",
      body: "填写图片本机绝对路径与可选图注：",
      inputs: [
        { key: "figure_path", label: "图片文件路径", value: "", placeholder: "/path/to/figure.png" },
        { key: "figure_caption", label: "图注 (可选)", value: "", placeholder: "例如：主实验对比曲线" },
      ],
      onConfirm: async (vals) => {
        const fp = (vals.figure_path || "").trim();
        if (!fp) return showToast("请输入图片路径", true);
        await bridge.callTool("po_projects", {
          action: "add_figure",
          project_id: state.selectedProjectId,
          figure_path: fp,
          figure_caption: (vals.figure_caption || "").trim(),
        });
        showToast("已挂载图稿");
        await refreshSnapshot(true);
      },
    });
  });
  document.querySelectorAll<HTMLElement>("[data-save-cap]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const fid = btn.dataset.saveCap!;
      const inp = document.querySelector<HTMLInputElement>(`[data-fig-cap="${fid}"]`);
      try {
        await bridge.callTool("po_projects", { action: "update_figure_caption", figure_id: fid, figure_caption: inp?.value || "" });
        showToast("图注已更新");
        await refreshSnapshot(true);
      } catch (e) {
        showToast(String(e), true);
      }
    });
  });
  document.querySelectorAll<HTMLElement>("[data-del-fig]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await bridge.callTool("po_projects", { action: "delete_figure", figure_id: btn.dataset.delFig! });
        showToast("已移除图稿");
        await refreshSnapshot(true);
      } catch (e) {
        showToast(String(e), true);
      }
    });
  });

  // Runs
  document.getElementById("runs-toggle-poll")?.addEventListener("click", () => {
    state.autoPoll = !state.autoPoll;
    if (state.autoPoll) {
      state.pollTimer = window.setInterval(() => {
        if (!document.hidden && (state.tab === "runs" || state.tab === "overview")) {
          void refreshSnapshot(true);
        }
      }, 6000);
    } else if (state.pollTimer !== null) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
    render();
  });
  document.querySelectorAll<HTMLElement>("[data-select-run]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.selectedRunId = btn.dataset.selectRun!;
      state.selectedRunDir = btn.dataset.selectDir || "";
      state.artifactPreview = null;
      await refreshSnapshot(true);
    });
  });
  document.querySelectorAll<HTMLElement>("[data-del-run]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const rid = btn.dataset.delRun!;
      askConfirm({
        title: "删除运行数据库记录",
        body: "仅从 SQLite 删除运行与日志索引（不删除磁盘产出目录）。",
        requireText: "delete_run",
        danger: true,
        onConfirm: async () => {
          await bridge.callTool("po_projects", { action: "delete_run", run_id: rid });
          showToast("运行记录已移除");
          await refreshSnapshot(true);
        },
      });
    });
  });
  document.querySelectorAll<HTMLElement>("[data-stop-job]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const jid = btn.dataset.stopJob!;
      askConfirm({
        title: "急停后台作业",
        body: `将向进程 ${jid} 发送终止信号。`,
        requireText: "stop",
        danger: true,
        onConfirm: async () => {
          await bridge.callTool("po_job_manage", { action: "stop", job_id: jid });
          showToast("已终止后台作业");
          await refreshSnapshot(true);
        },
      });
    });
  });
  document.getElementById("run-load-summary")?.addEventListener("click", async () => {
    if (!state.selectedRunDir) return;
    try {
      state.runSummary = await bridge.callTool("po_inspect_run", { action: "summary", run_dir: state.selectedRunDir });
      render();
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("run-load-logs")?.addEventListener("click", async () => {
    if (!state.selectedRunId) return;
    try {
      const res = await bridge.callTool("po_projects", { action: "get_run_logs", run_id: state.selectedRunId, limit: 200 });
      state.runLogs = (res.logs as any[]) || [];
      render();
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("run-copy-pdf")?.addEventListener("click", async () => {
    if (!state.selectedRunDir) return;
    try {
      const res = await bridge.callTool("po_inspect_run", { action: "copy_pdf", run_dir: state.selectedRunDir });
      showToast("PDF 已安全复制到：" + String(res.destPath || ""));
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("run-open-pdf-side")?.addEventListener("click", async () => {
    if (state.runSummary?.finalPdfPath) await bridge.openFileInSidePane(String(state.runSummary.finalPdfPath));
  });
  document.querySelectorAll<HTMLElement>("[data-read-art]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const res = await bridge.callTool("po_inspect_run", {
          action: "read_artifact",
          run_dir: state.selectedRunDir,
          relative_path: btn.dataset.readArt!,
          max_chars: 40000,
        });
        state.artifactPreview = { path: String(res.path || ""), content: String(res.content || "") };
        render();
      } catch (e) {
        showToast(String(e), true);
      }
    });
  });
  document.getElementById("art-open-side")?.addEventListener("click", async () => {
    if (state.artifactPreview?.path) await bridge.openFileInSidePane(state.artifactPreview.path);
  });

  // Library
  document.getElementById("lib-folder")?.addEventListener("input", (ev) => {
    state.libFolder = (ev.target as HTMLInputElement).value;
  });
  document.getElementById("lib-reload")?.addEventListener("click", () => {
    state.libOffset = 0;
    void loadLibraryPage();
  });
  document.getElementById("lib-prev")?.addEventListener("click", () => {
    state.libOffset = Math.max(0, state.libOffset - state.libLimit);
    void loadLibraryPage();
  });
  document.getElementById("lib-next")?.addEventListener("click", () => {
    if (state.libPage?.nextOffset != null) {
      state.libOffset = Number(state.libPage.nextOffset);
      void loadLibraryPage();
    }
  });
  document.getElementById("lib-search-btn")?.addEventListener("click", async () => {
    const q = (document.getElementById("lib-query") as HTMLInputElement)?.value.trim();
    if (!q) return showToast("请输入检索关键词", true);
    try {
      await runToolJson("po_literature_library", { action: "search_online", query: q, limit: 10 }, "libOutput");
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("lib-doi-btn")?.addEventListener("click", async () => {
    const doi = (document.getElementById("lib-doi") as HTMLInputElement)?.value.trim();
    if (!doi) return showToast("请输入 DOI", true);
    try {
      await runToolJson("po_literature_library", { action: "import_doi", doi, save_to_library: true }, "libOutput");
      await loadLibraryPage();
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("lib-export-bib")?.addEventListener("click", async () => {
    try {
      await runToolJson("po_literature_library", { action: "export_bib", format: "bibtex" }, "libOutput");
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("lib-export-gb")?.addEventListener("click", async () => {
    try {
      await runToolJson("po_literature_library", { action: "export_bib", format: "gb7714" }, "libOutput");
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("lib-graph")?.addEventListener("click", async () => {
    try {
      await runToolJson("po_literature_library", { action: "build_graph" }, "libOutput");
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.querySelectorAll<HTMLElement>("[data-del-lib]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const iid = btn.dataset.delLib!;
      askConfirm({
        title: "删除文献库条目",
        body: `确定要删除文献条目 ${iid} 吗？（不删除本地原 PDF）`,
        requireText: "delete",
        danger: true,
        onConfirm: async () => {
          await bridge.callTool("po_literature_library", { action: "delete", item_id: iid });
          await loadLibraryPage();
        },
      });
    });
  });

  // Figures & BBox Drag
  document.getElementById("fig-dir-input")?.addEventListener("input", (ev) => {
    state.figDir = (ev.target as HTMLInputElement).value;
  });
  document.getElementById("fig-scan")?.addEventListener("click", async () => {
    if (!state.figDir) return showToast("请填写图稿目录", true);
    try {
      const res = await bridge.callTool("po_figure_studio", { action: "list", dir_path: state.figDir });
      state.figBundles = (res.bundles as any[]) || [];
      if (state.figBundles.length > 0) {
        const first = state.figBundles[0];
        await selectBundle(first.bundle, first.currentImage || first.image || "");
      } else {
        render();
      }
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("fig-create-btn")?.addEventListener("click", () => {
    askConfirm({
      title: "新建科学图稿 (po_figure_studio)",
      body: "支持 data_code (Matplotlib) / schematic_svg (矢量架构图) / concept_image 路由。",
      inputs: [
        { key: "mode", label: "绘图路由 (data_code / schematic_svg / concept_image)", value: "data_code" },
        { key: "prompt", label: "图稿内容与视觉需求", value: "", placeholder: "描述坐标轴、数据系列、配色与构图…" },
      ],
      onConfirm: async (vals) => {
        const promptText = (vals.prompt || "").trim();
        if (!promptText) return showToast("请输入绘图描述", true);
        const mode = (vals.mode || "data_code").trim() || "data_code";
        await runToolJson("po_figure_studio", { action: "create", dir_path: state.figDir || undefined, mode, prompt: promptText }, "figResult");
        showToast("已创建新图稿");
      },
    });
  });
  document.querySelectorAll<HTMLElement>("[data-select-bundle]").forEach((btn) => {
    btn.addEventListener("click", () => void selectBundle(btn.dataset.selectBundle!, btn.dataset.img || ""));
  });
  document.getElementById("fig-reset-bbox")?.addEventListener("click", () => {
    state.bbox = { x: 0.06, y: 0.09, w: 0.28, h: 0.24 };
    render();
  });
  document.getElementById("fig-inst")?.addEventListener("input", (ev) => {
    state.figInstruction = (ev.target as HTMLTextAreaElement).value;
  });
  (["x", "y", "w", "h"] as const).forEach((axis) => {
    document.getElementById(`bx-${axis}`)?.addEventListener("change", (ev) => {
      const v = Number((ev.target as HTMLInputElement).value);
      if (Number.isFinite(v)) {
        state.bbox[axis] = Math.max(axis === "w" || axis === "h" ? 0.01 : 0, Math.min(1, v));
        if (state.bbox.x + state.bbox.w > 1) state.bbox.w = Math.max(0.01, 1 - state.bbox.x);
        if (state.bbox.y + state.bbox.h > 1) state.bbox.h = Math.max(0.01, 1 - state.bbox.y);
        render();
      }
    });
  });

  const stage = document.getElementById("bbox-stage");
  if (stage) {
    let startPt: { x: number; y: number } | null = null;
    stage.addEventListener("pointerdown", (ev) => {
      const rect = stage.getBoundingClientRect();
      startPt = {
        x: Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width)),
        y: Math.max(0, Math.min(1, (ev.clientY - rect.top) / rect.height)),
      };
      stage.setPointerCapture(ev.pointerId);
    });
    stage.addEventListener("pointermove", (ev) => {
      if (!startPt) return;
      const rect = stage.getBoundingClientRect();
      const cx = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
      const cy = Math.max(0, Math.min(1, (ev.clientY - rect.top) / rect.height));
      state.bbox = {
        x: Number(Math.min(startPt.x, cx).toFixed(4)),
        y: Number(Math.min(startPt.y, cy).toFixed(4)),
        w: Number(Math.max(0.02, Math.abs(cx - startPt.x)).toFixed(4)),
        h: Number(Math.max(0.02, Math.abs(cy - startPt.y)).toFixed(4)),
      };
      const boxEl = document.getElementById("bbox-rect");
      if (boxEl) {
        boxEl.style.left = `${(state.bbox.x * 100).toFixed(2)}%`;
        boxEl.style.top = `${(state.bbox.y * 100).toFixed(2)}%`;
        boxEl.style.width = `${(state.bbox.w * 100).toFixed(2)}%`;
        boxEl.style.height = `${(state.bbox.h * 100).toFixed(2)}%`;
        const lbl = boxEl.querySelector(".bbox-label");
        if (lbl) lbl.textContent = `${(state.bbox.x * 100).toFixed(1)}%, ${(state.bbox.y * 100).toFixed(1)}% · ${(state.bbox.w * 100).toFixed(1)}%×${(state.bbox.h * 100).toFixed(1)}%`;
      }
    });
    stage.addEventListener("pointerup", () => {
      if (startPt) {
        startPt = null;
        render();
      }
    });
  }

  document.getElementById("fig-revise-run")?.addEventListener("click", async () => {
    if (!state.selectedBundle) return;
    if (!state.figInstruction.trim()) return showToast("请输入局部修改指令", true);
    try {
      await runToolJson(
        "po_figure_studio",
        { action: "revise", bundle: state.selectedBundle, instruction: state.figInstruction, bbox: state.bbox },
        "figResult",
      );
      showToast("图稿局部重绘完成");
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("fig-send-chat")?.addEventListener("click", async () => {
    const b = state.bbox;
    await bridge.sendToChat(
      `请针对图稿 bundle \`${state.selectedBundle}\` 的红框局部区域 \`bbox=${JSON.stringify(b)}\` 执行局部修订：${state.figInstruction || "优化图例与标注重叠并保持其余区域不变"}。`,
    );
  });
  document.getElementById("fig-restore-v1")?.addEventListener("click", () => {
    if (!state.selectedBundle) return;
    askConfirm({
      title: "回滚图稿版本 (restore)",
      body: `目标版本束：${state.selectedBundle}`,
      inputs: [{ key: "version", label: "目标版本号", value: "v1", placeholder: "例如 v1 或 v2" }],
      onConfirm: async (vals) => {
        const ver = (vals.version || "").trim();
        if (!ver) return showToast("请输入版本号", true);
        await runToolJson("po_figure_studio", { action: "restore", bundle: state.selectedBundle, version: ver }, "figResult");
        showToast(`已回滚至 ${ver}`);
      },
    });
  });

  // LaTeX
  document.getElementById("ltx-run-dir")?.addEventListener("input", (ev) => {
    state.selectedRunDir = (ev.target as HTMLInputElement).value;
  });
  document.getElementById("ltx-open-btn")?.addEventListener("click", () => void openLatexDir());
  document.getElementById("ltx-toggle-split")?.addEventListener("click", () => {
    state.latexSplitView = !state.latexSplitView;
    render();
  });
  document.querySelectorAll<HTMLElement>("[data-ltx-file]").forEach((btn) => {
    btn.addEventListener("click", () => void readLatexFile(btn.dataset.ltxFile!));
  });
  document.getElementById("ltx-reload")?.addEventListener("click", () => void readLatexFile(state.latexFile));
  const edEl = document.getElementById("ltx-editor") as HTMLTextAreaElement | null;
  edEl?.addEventListener("input", () => {
    state.latexContent = edEl.value;
  });
  const handleEditorSelectionChange = (triggerSync: boolean) => {
    if (!edEl) return;
    const selStart = edEl.selectionStart ?? 0;
    const selEnd = edEl.selectionEnd ?? selStart;
    const startLC = offsetToLineCol(edEl.value, selStart);
    const endOffset = selEnd > selStart ? selEnd - 1 : selStart;
    const endLC = offsetToLineCol(edEl.value, endOffset);
    state.latexCursorStartLine = startLC.line;
    state.latexCursorEndLine = Math.max(startLC.line, endLC.line);
    state.latexCursorCol = startLC.col;
    const badge = document.getElementById("ltx-cursor-badge");
    if (badge) {
      badge.textContent =
        state.latexCursorStartLine === state.latexCursorEndLine
          ? `L${state.latexCursorStartLine}`
          : `L${state.latexCursorStartLine}–${state.latexCursorEndLine}`;
    }
    if (triggerSync && state.latexAutoSync && state.latexHasSyncTeX && state.latexFile.endsWith(".tex")) {
      if (syncDebounceTimer !== null) window.clearTimeout(syncDebounceTimer);
      syncDebounceTimer = window.setTimeout(() => {
        syncDebounceTimer = null;
        void syncLatexToPdf({ silent: true });
      }, 350);
    }
  };
  edEl?.addEventListener("mouseup", () => handleEditorSelectionChange(true));
  edEl?.addEventListener("keyup", (ev) => {
    const navKeys = new Set([
      "ArrowUp",
      "ArrowDown",
      "ArrowLeft",
      "ArrowRight",
      "PageUp",
      "PageDown",
      "Home",
      "End",
      "Enter",
    ]);
    const hasSel = (edEl.selectionEnd ?? 0) !== (edEl.selectionStart ?? 0);
    handleEditorSelectionChange(navKeys.has(ev.key) || hasSel);
  });
  document.getElementById("ltx-auto-sync")?.addEventListener("change", (ev) => {
    state.latexAutoSync = (ev.target as HTMLInputElement).checked;
  });
  document.getElementById("ltx-sync-to-pdf")?.addEventListener("click", () => {
    void syncLatexToPdf({ silent: false });
  });
  document.getElementById("ltx-open-side")?.addEventListener("click", async () => {
    if (state.latexOpen?.sourceDir && state.latexFile) {
      await bridge.openFileInSidePane(`${state.latexOpen.sourceDir}/${state.latexFile}`);
    }
  });
  document.getElementById("ltx-save")?.addEventListener("click", async () => {
    try {
      const res = await bridge.callTool("po_latex_workbench", {
        action: "save",
        run_dir: state.selectedRunDir,
        tex_file: state.latexFile,
        content: state.latexContent,
        expected_mtime_ms: state.latexMtimeMs,
      });
      state.latexMtimeMs = Number(res.mtimeMs || state.latexMtimeMs);
      state.latexHasBackup = Boolean(res.hasBackup);
      showToast("已通过冲突检查并保存到源码（原稿保留于 .edit_backup/）");
      render();
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("ltx-restore")?.addEventListener("click", () => {
    askConfirm({
      title: "恢复原稿备份",
      body: `将把 ${state.latexFile} 恢复为首次在线修改前的版本（丢弃当前修改）。`,
      requireText: "restore",
      danger: true,
      onConfirm: async () => {
        const res = await bridge.callTool("po_latex_workbench", {
          action: "restore",
          run_dir: state.selectedRunDir,
          tex_file: state.latexFile,
        });
        state.latexContent = String(res.content || "");
        state.latexMtimeMs = Number(res.mtimeMs || 0);
        showToast("已从 .edit_backup/ 恢复原稿");
        render();
      },
    });
  });
  document.getElementById("ltx-compile-btn")?.addEventListener("click", async () => {
    if (!state.selectedRunDir) return showToast("请先填写 run_dir", true);
    try {
      showToast("正在编译 .live_preview/（自动识别 CJK XeLaTeX）…");
      const res = await bridge.callTool("po_latex_workbench", {
        action: "compile",
        run_dir: state.selectedRunDir,
        tex_file: state.latexFile || undefined,
      });
      state.latexCompileResult = res;
      state.latexHasSyncTeX = Boolean(res.hasSyncTeX);
      render();
      if (res.ok && res.pdfPath) {
        void loadLatexPdfPage(String(res.pdfPath), state.latexPdfPage || 1, { preserveHighlight: true });
      }
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.getElementById("ltx-preview-inline")?.addEventListener("click", () => {
    if (state.latexCompileResult?.pdfPath) {
      void loadLatexPdfPage(String(state.latexCompileResult.pdfPath), state.latexPdfPage || 1);
    }
  });
  document.getElementById("ltx-open-pdf-external")?.addEventListener("click", async () => {
    if (state.latexCompileResult?.pdfPath) await bridge.openFileExternal(String(state.latexCompileResult.pdfPath));
  });
  document.getElementById("ltx-open-pdf-side")?.addEventListener("click", async () => {
    const pdfPath = String(state.latexCompileResult?.pdfPath || "");
    if (!pdfPath) return;
    const opened = await bridge.openFileInSidePane(pdfPath, { silent: true, fallbackExternal: false });
    if (!opened) {
      void loadLatexPdfPage(pdfPath, state.latexPdfPage || 1);
      showToast("当前为全屏视图无侧边文件栏，已切换至内置分页 PDF 预览（也可点「用系统预览打开」）");
    }
  });
  document.getElementById("ltx-pdf-img")?.addEventListener("click", (ev) => {
    void syncPdfClickToLatex(ev as MouseEvent);
  });
  document.getElementById("ltx-pdf-first")?.addEventListener("click", () => {
    if (state.latexPdfPath) void loadLatexPdfPage(state.latexPdfPath, 1);
  });
  document.getElementById("ltx-pdf-prev")?.addEventListener("click", () => {
    if (state.latexPdfPath && state.latexPdfPage > 1) void loadLatexPdfPage(state.latexPdfPath, state.latexPdfPage - 1);
  });
  document.getElementById("ltx-pdf-next")?.addEventListener("click", () => {
    if (state.latexPdfPath && state.latexPdfPage < state.latexPdfPageCount) void loadLatexPdfPage(state.latexPdfPath, state.latexPdfPage + 1);
  });
  document.getElementById("ltx-pdf-last")?.addEventListener("click", () => {
    if (state.latexPdfPath && state.latexPdfPageCount > 0) void loadLatexPdfPage(state.latexPdfPath, state.latexPdfPageCount);
  });
  const jumpToPdfPage = () => {
    const el = document.getElementById("ltx-pdf-jump") as HTMLInputElement | null;
    const num = Number(el?.value || state.latexPdfPage || 1);
    if (state.latexPdfPath && Number.isFinite(num)) {
      const clamped = Math.max(1, Math.min(state.latexPdfPageCount || num, Math.floor(num)));
      void loadLatexPdfPage(state.latexPdfPath, clamped);
    }
  };
  document.getElementById("ltx-pdf-jump-go")?.addEventListener("click", jumpToPdfPage);
  document.getElementById("ltx-pdf-jump")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") jumpToPdfPage();
  });
  document.getElementById("ltx-pdf-zoom-out")?.addEventListener("click", () => {
    state.latexPdfZoom = Math.max(80, state.latexPdfZoom - 20);
    render();
  });
  document.getElementById("ltx-pdf-zoom-in")?.addEventListener("click", () => {
    state.latexPdfZoom = Math.min(180, state.latexPdfZoom + 20);
    render();
  });
  document.getElementById("ltx-pdf-ext-2")?.addEventListener("click", async () => {
    if (state.latexPdfPath) await bridge.openFileExternal(state.latexPdfPath);
  });
  document.getElementById("ltx-pdf-close")?.addEventListener("click", () => {
    state.latexPdfDataUrl = "";
    state.latexPdfLoading = false;
    state.latexPdfError = "";
    state.latexSyncHighlight = null;
    render();
  });
  document.getElementById("ltx-trace-btn")?.addEventListener("click", async () => {
    if (!state.selectedRunDir) return;
    try {
      await runToolJson("po_latex_copilot", { action: "trace_citations", run_dir: state.selectedRunDir }, "latexResultText");
    } catch (e) {
      showToast(String(e), true);
    }
  });
  const runPolishAbstract = async (mode: "diagnose" | "rewrite") => {
    const filePath =
      state.latexOpen?.sourceDir && state.latexFile && state.latexFile.endsWith(".tex")
        ? `${state.latexOpen.sourceDir}/${state.latexFile}`
        : undefined;
    if (!state.selectedRunDir && !filePath && !state.latexContent.trim()) {
      return showToast("请先填写 run_dir 或打开 .tex 文件", true);
    }
    try {
      showToast(mode === "rewrite" ? "正在按获奖论文文风生成摘要逐句改写对照表…" : "正在运行离线摘要结构诊断与 AI 节奏指标计算…");
      const res = await bridge.callTool("po_latex_copilot", {
        action: "polish_abstract",
        mode,
        run_dir: state.selectedRunDir || undefined,
        file_path: filePath,
      });
      if (res?.error) {
        showToast(String(res.error), true);
        return;
      }
      state.latexPolishResult = res;
      render();
      showToast(String(res.summary || "摘要分析完成"));
    } catch (e) {
      showToast(String(e), true);
    }
  };
  document.getElementById("ltx-polish-diag-btn")?.addEventListener("click", () => void runPolishAbstract("diagnose"));
  document.getElementById("ltx-polish-rewrite-btn")?.addEventListener("click", () => void runPolishAbstract("rewrite"));
  document.getElementById("ltx-polish-close")?.addEventListener("click", () => {
    state.latexPolishResult = null;
    render();
  });
  document.getElementById("ltx-overleaf-btn")?.addEventListener("click", async () => {
    if (!state.selectedRunDir) return;
    try {
      await runToolJson("po_latex_workbench", { action: "export_overleaf", run_dir: state.selectedRunDir }, "latexResultText");
    } catch (e) {
      showToast(String(e), true);
    }
  });

  // Exports & Audit
  document.getElementById("aud-run-dir")?.addEventListener("input", (ev) => {
    state.selectedRunDir = (ev.target as HTMLInputElement).value;
  });
  document.querySelectorAll<HTMLElement>("[data-audit-act]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!state.selectedRunDir) return showToast("请先输入 run_dir", true);
      try {
        await runToolJson("po_submission_audit", { action: btn.dataset.auditAct!, run_dir: state.selectedRunDir }, "auditResult");
      } catch (e) {
        showToast(String(e), true);
      }
    });
  });
  const doExport = async (action: "export" | "export_google_drive") => {
    const title = (document.getElementById("exp-title") as HTMLInputElement)?.value.trim() || "PaperOrchestra_Report";
    const format = (document.getElementById("exp-fmt") as HTMLSelectElement)?.value || "html";
    const mdPath = (document.getElementById("exp-md-path") as HTMLInputElement)?.value.trim();
    const mdBody = (document.getElementById("exp-md-body") as HTMLTextAreaElement)?.value;
    try {
      await runToolJson(
        "po_export_document",
        {
          action,
          title,
          format,
          input_md: mdPath || undefined,
          markdown_content: mdPath ? undefined : mdBody,
        },
        "exportResult",
      );
      showToast("导出完成");
    } catch (e) {
      showToast(String(e), true);
    }
  };
  document.getElementById("exp-run-btn")?.addEventListener("click", () => void doExport("export"));
  document.getElementById("exp-drive-btn")?.addEventListener("click", () => void doExport("export_google_drive"));

  // Skills & Setup
  document.querySelectorAll<HTMLElement>("[data-get-skill]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const res = await bridge.callTool("po_custom_skills", { action: "get", skill_id: btn.dataset.getSkill! });
        state.selectedSkill = (res.skill as Record<string, any>) || null;
        render();
      } catch (e) {
        showToast(String(e), true);
      }
    });
  });
  document.getElementById("sk-use-chat")?.addEventListener("click", async () => {
    if (!state.selectedSkill) return;
    await bridge.sendToChat(`请应用 PaperOrchestra 技能「${state.selectedSkill.name}」（skill_id="${state.selectedSkill.id}"）处理我接下来的材料。`);
  });
  document.getElementById("sk-load-env")?.addEventListener("click", () => void loadEnvSetup());
  document.getElementById("sk-val-prov")?.addEventListener("click", async () => {
    try {
      const res = await bridge.callTool("po_settings_and_setup", { action: "validate_providers_json" });
      state.providersValidation = JSON.stringify(res, null, 2);
      render();
    } catch (e) {
      showToast(String(e), true);
    }
  });
  document.querySelectorAll<HTMLElement>("[data-test-host]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const res = await bridge.callTool("po_compute_and_memory", { action: "test_host", host_id: btn.dataset.testHost! });
        showToast(`主机检测完成: ${String((res as any).gpu_info || (res as any).status || "ok")}`);
      } catch (e) {
        showToast(String(e), true);
      }
    });
  });

  // Tools Console
  document.getElementById("tc-search")?.addEventListener("input", (ev) => {
    state.toolSearch = (ev.target as HTMLInputElement).value;
    render();
    const el = document.getElementById("tc-search") as HTMLInputElement | null;
    if (el) {
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  });
  document.querySelectorAll<HTMLElement>("[data-tc-pick]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const found = TOOL_NODES.find((n) => n.id === btn.dataset.tcPick);
      if (found) {
        state.selectedToolId = found.id;
        state.toolArgsText = JSON.stringify(found.exampleArgs || {}, null, 2);
        state.toolOutput = "";
        render();
      }
    });
  });
  document.getElementById("tc-args")?.addEventListener("input", (ev) => {
    state.toolArgsText = (ev.target as HTMLTextAreaElement).value;
  });
  document.getElementById("tc-exec")?.addEventListener("click", async () => {
    const sel = TOOL_NODES.find((n) => n.id === state.selectedToolId) || TOOL_NODES[0];
    let parsed: Record<string, unknown> = {};
    try {
      parsed = JSON.parse(state.toolArgsText || "{}");
    } catch (e) {
      return showToast(`参数 JSON 语法错误: ${e}`, true);
    }
    const execNow = async () => {
      try {
        await runToolJson(sel.tool, parsed, "toolOutput");
        showToast(`已执行 ${sel.tool}`);
      } catch (e) {
        state.toolOutput = String(e);
        showToast(String(e), true);
        render();
      }
    };
    if (sel.risk === "read") {
      await execNow();
    } else if (sel.risk === "write") {
      askConfirm({
        title: `确认执行写入操作：${sel.title}`,
        body: `即将调用 ${sel.tool}${sel.action ? ` (${sel.action})` : ""}。${sel.guardrail}`,
        onConfirm: execNow,
      });
    } else {
      askConfirm({
        title: `破坏性操作二次确认：${sel.title}`,
        body: `${sel.guardrail}`,
        requireText: sel.action || sel.tool,
        danger: true,
        onConfirm: execNow,
      });
    }
  });

  // Modal
  if (state.confirmModal) {
    document.querySelectorAll<HTMLInputElement>("[data-modal-input]").forEach((inp) => {
      inp.addEventListener("input", () => {
        state.confirmModal!.values[inp.dataset.modalInput!] = inp.value;
      });
    });
    document.getElementById("confirm-typed")?.addEventListener("input", (ev) => {
      state.confirmModal!.typed = (ev.target as HTMLInputElement).value;
      const okBtn = document.getElementById("confirm-ok") as HTMLButtonElement | null;
      if (okBtn && state.confirmModal?.requireText) {
        okBtn.disabled = state.confirmModal.typed.trim() !== state.confirmModal.requireText;
      }
    });
    document.getElementById("confirm-cancel")?.addEventListener("click", () => {
      state.confirmModal = null;
      render();
    });
    document.getElementById("confirm-backdrop")?.addEventListener("click", (ev) => {
      if (ev.target === ev.currentTarget) {
        state.confirmModal = null;
        render();
      }
    });
    document.getElementById("confirm-ok")?.addEventListener("click", async () => {
      const m = state.confirmModal;
      state.confirmModal = null;
      render();
      if (m) {
        try {
          await m.onConfirm(m.values);
        } catch (e) {
          showToast(String(e), true);
        }
      }
    });
  }
}

async function loadLibraryPage(): Promise<void> {
  try {
    state.libPage = await bridge.callTool("po_literature_library", {
      action: "list",
      folder: state.libFolder.trim() || undefined,
      offset: state.libOffset,
      limit: state.libLimit,
    });
    render();
  } catch (e) {
    showToast(String(e), true);
  }
}

async function loadEnvSetup(): Promise<void> {
  try {
    state.settingsData = await bridge.callTool("po_settings_and_setup", { action: "get_settings" });
    state.readinessData = await bridge.callTool("po_settings_and_setup", { action: "env_readiness" });
    render();
  } catch (e) {
    showToast(String(e), true);
  }
}

async function selectBundle(bundlePath: string, imagePath: string): Promise<void> {
  state.selectedBundle = bundlePath;
  state.selectedImagePath = imagePath;
  state.figDataUrl = "";
  render();
  if (imagePath) {
    try {
      const res = await bridge.callTool("po_ui_snapshot", { action: "read_image_data_url", path: imagePath });
      state.figDataUrl = String(res.dataUrl || "");
      render();
    } catch {
      // Fallback gracefully when image > 600KB or unreadable
    }
  }
}

async function openLatexDir(): Promise<void> {
  if (!state.selectedRunDir) return showToast("请填写 run_dir", true);
  try {
    const res = await bridge.callTool("po_latex_workbench", { action: "open", run_dir: state.selectedRunDir });
    state.latexOpen = res;
    state.latexHasSyncTeX = Boolean(res.hasSyncTeX);
    state.latexSyncHighlight = null;
    const first = String(res.mainFile || (res.files || [])[0] || "");
    if (first) await readLatexFile(first);
    else render();
    if (res.previewPdfPath && state.latexPdfPath !== String(res.previewPdfPath)) {
      void loadLatexPdfPage(String(res.previewPdfPath), 1);
    }
  } catch (e) {
    showToast(String(e), true);
  }
}

async function readLatexFile(rel: string): Promise<void> {
  if (!state.selectedRunDir || !rel) return;
  try {
    const res = await bridge.callTool("po_latex_workbench", {
      action: "read",
      run_dir: state.selectedRunDir,
      tex_file: rel,
    });
    state.latexFile = String(res.file || rel);
    state.latexContent = String(res.content || "");
    state.latexMtimeMs = Number(res.mtimeMs || 0);
    state.latexHasBackup = Boolean(res.hasBackup);
    state.latexHasSyncTeX = Boolean(res.hasSyncTeX || state.latexHasSyncTeX);
    if (pendingEditorJumpLine === null) {
      state.latexCursorStartLine = 1;
      state.latexCursorEndLine = 1;
      state.latexCursorCol = 1;
    }
    render();
    if (
      res.previewPdfPath &&
      rel.endsWith(".tex") &&
      (!state.latexPdfPath || String(res.previewPdfPath) !== state.latexPdfPath || !state.latexPdfDataUrl)
    ) {
      void loadLatexPdfPage(String(res.previewPdfPath), 1);
    }
  } catch (e) {
    showToast(String(e), true);
  }
}

async function loadLatexPdfPage(pdfPath: string, page = 1, opts?: { preserveHighlight?: boolean }): Promise<void> {
  if (!pdfPath) return;
  const reqPage = Math.max(1, Math.floor(page));
  if (!opts?.preserveHighlight || (state.latexSyncHighlight && state.latexSyncHighlight.page !== reqPage)) {
    state.latexSyncHighlight = null;
  }
  state.latexPdfPath = pdfPath;
  state.latexPdfPage = reqPage;
  state.latexPdfLoading = true;
  state.latexPdfError = "";
  render();
  try {
    const res = await bridge.callTool("po_ui_snapshot", {
      action: "render_pdf_page",
      path: pdfPath,
      page: reqPage,
      dpi: 140,
    });
    state.latexPdfPage = Number(res.page || reqPage);
    state.latexPdfPageCount = Number(res.pageCount || 0);
    state.latexPdfWidthPt = Number(res.pageWidthPt || 612);
    state.latexPdfHeightPt = Number(res.pageHeightPt || 792);
    state.latexPdfDataUrl = String(res.dataUrl || "");
    state.latexPdfLoading = false;
    if (state.latexSyncHighlight && state.latexSyncHighlight.page === state.latexPdfPage) {
      pendingScrollToHighlight = true;
    }
    render();
  } catch (e) {
    state.latexPdfLoading = false;
    state.latexPdfError = String(e);
    render();
  }
}

async function syncLatexToPdf(opts?: { silent?: boolean }): Promise<void> {
  if (!state.selectedRunDir || !state.latexFile) return;
  if (!state.latexFile.endsWith(".tex")) {
    if (!opts?.silent) showToast("SyncTeX 仅支持 .tex 文件定位", true);
    return;
  }
  if (state.latexSyncBusy) return;

  const ed = document.getElementById("ltx-editor") as HTMLTextAreaElement | null;
  let startLine = state.latexCursorStartLine;
  let endLine = state.latexCursorEndLine;
  let col = state.latexCursorCol;
  if (ed) {
    const selStart = ed.selectionStart ?? 0;
    const selEnd = ed.selectionEnd ?? selStart;
    const s = offsetToLineCol(ed.value, selStart);
    const e = offsetToLineCol(ed.value, selEnd > selStart ? selEnd - 1 : selStart);
    startLine = s.line;
    endLine = Math.max(s.line, e.line);
    col = s.col;
    state.latexCursorStartLine = startLine;
    state.latexCursorEndLine = endLine;
    state.latexCursorCol = col;
  }

  state.latexSyncBusy = true;
  try {
    const res = await bridge.callTool("po_latex_workbench", {
      action: "sync_view",
      run_dir: state.selectedRunDir,
      tex_file: state.latexFile,
      pdf_path: state.latexPdfPath || undefined,
      line: startLine,
      end_line: endLine,
      column: col,
    });
    if (!res.ok || !res.page) {
      if (!opts?.silent) showToast("未能定位到 PDF 对应位置（请先编译生成 .synctex.gz 或换一行重试）", true);
      return;
    }
    state.latexHasSyncTeX = true;
    const lineLabel = startLine === endLine ? `L${startLine}` : `L${startLine}–${endLine}`;
    state.latexSyncHighlight = res.highlight ? { ...res.highlight, lineLabel } : null;
    pendingScrollToHighlight = true;
    const targetPdf = String(res.pdfPath || state.latexPdfPath || "");
    const targetPage = Number(res.page);
    if (targetPdf && (targetPdf !== state.latexPdfPath || targetPage !== state.latexPdfPage || !state.latexPdfDataUrl)) {
      await loadLatexPdfPage(targetPdf, targetPage, { preserveHighlight: true });
    } else {
      render();
    }
  } catch (e) {
    if (!opts?.silent) showToast(String(e), true);
  } finally {
    state.latexSyncBusy = false;
  }
}

async function syncPdfClickToLatex(ev: MouseEvent): Promise<void> {
  if (!state.selectedRunDir || !state.latexPdfPath || state.latexPdfWidthPt <= 0 || state.latexPdfHeightPt <= 0) return;
  const img = ev.currentTarget as HTMLImageElement | null;
  if (!img) return;
  const rect = img.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return;

  const xPt = ((ev.clientX - rect.left) / rect.width) * state.latexPdfWidthPt;
  const yPt = ((ev.clientY - rect.top) / rect.height) * state.latexPdfHeightPt;
  try {
    const res = await bridge.callTool("po_latex_workbench", {
      action: "sync_edit",
      run_dir: state.selectedRunDir,
      tex_file: state.latexFile || undefined,
      pdf_path: state.latexPdfPath,
      page: state.latexPdfPage,
      x: Number(xPt.toFixed(2)),
      y: Number(yPt.toFixed(2)),
    });
    if (!res.ok || !res.line) {
      showToast("当前位置未匹配到 LaTeX 源码行（可能为空白页边距或未编译 .synctex.gz）", true);
      return;
    }
    const targetLine = Number(res.line);
    const targetFile = String(res.relFile || state.latexFile || "");
    pendingEditorJumpLine = targetLine;
    state.latexSyncHighlight = {
      page: state.latexPdfPage,
      h: Math.max(0, xPt - 18),
      v: yPt + 7,
      top: Math.max(0, yPt - 7),
      W: 36,
      H: 14,
      lineLabel: `L${targetLine}`,
    };
    if (res.relFile && String(res.relFile) !== state.latexFile) {
      await readLatexFile(String(res.relFile));
    } else {
      render();
    }
    showToast(`已反向定位至 ${targetFile} 第 ${targetLine} 行`);
  } catch (e) {
    showToast(String(e), true);
  }
}

async function init(): Promise<void> {
  bridge.onToolInput((args) => {
    if (typeof args.tab === "string" && NAV_ITEMS.some((n) => n.id === args.tab)) state.tab = args.tab as TabId;
    if (typeof args.project_id === "string") state.selectedProjectId = args.project_id;
    if (typeof args.run_id === "string") state.selectedRunId = args.run_id;
    if (typeof args.run_dir === "string") state.selectedRunDir = args.run_dir;
    if (typeof args.notice === "string") state.notice = args.notice;
    render();
  });

  bridge.onToolResult((data) => {
    applySnapshot(data);
    render();
  });

  render();
  try {
    await bridge.connect();
    await refreshSnapshot(true);
  } catch (err) {
    state.error = `MCP App 握手失败: ${err}`;
    render();
  }
}

void init();
