import type { ToolNodeMeta } from "./shared-app";

export interface ToolGroupMeta {
  id: string;
  title: string;
  en: string;
  color: string;
  soft: string;
  summary: string;
}

export const TOOL_GROUPS: ToolGroupMeta[] = [
  {
    id: "connection",
    title: "连接、环境与作业",
    en: "Connection & Jobs",
    color: "#2f6b57",
    soft: "#e7f0ea",
    summary: "协作入口：检查本机环境、20+ 模型凭证、Desktop 桥接状态与后台作业。",
  },
  {
    id: "projects",
    title: "项目与研究材料",
    en: "Projects & Inputs",
    color: "#92631f",
    soft: "#f7efe1",
    summary: "管理研究想法、实验记录、19 套学术模板与附带图稿。",
  },
  {
    id: "pipeline",
    title: "写作流水线与产物",
    en: "Pipeline & Runs",
    color: "#3f6387",
    soft: "#e8eff6",
    summary: "启动/恢复论文多智能体流水线，追踪日志并读取或复制成果 PDF。",
  },
  {
    id: "latex",
    title: "LaTeX 协同编辑",
    en: "LaTeX Co-Editing",
    color: "#4f6673",
    soft: "#eaf0f2",
    summary: "防冲突读写、首改原稿备份、CJK XeLaTeX 自动编译与 SyncTeX 双向定位。",
  },
  {
    id: "audit",
    title: "投稿体检与学术核查",
    en: "Submission Audit",
    color: "#7a4457",
    soft: "#f6eaee",
    summary: "8 项审稿与合规工具：Nature 体检、联网 DOI 核查、arXiv 打包、双盲、latexdiff 与 Lean 4。",
  },
  {
    id: "research",
    title: "科研 SOP 与数据分析",
    en: "Research & Data SOPs",
    color: "#436d4b",
    soft: "#e9f2ea",
    summary: "5 条结构化科研工作流（六步精读/主题综述/选题/复现/数模）与 7 步统计分析。",
  },
  {
    id: "figures",
    title: "图稿工坊与局部重绘",
    en: "Figure Studio",
    color: "#8b5239",
    soft: "#f6ece8",
    summary: "三路由科研作图（Matplotlib / SVG / 概念图）、版本管理与红框 bbox 局部修改。",
  },
  {
    id: "library",
    title: "文献库与知识图谱",
    en: "Library & Graph",
    color: "#2d5f72",
    soft: "#e5f0f4",
    summary: "8 源学术检索、DOI/PDF 入库、分页浏览、引用图谱与 BibTeX / GB/T 7714 导出。",
  },
  {
    id: "skills_compute",
    title: "技能、记忆与算力",
    en: "Skills, Memory & GPU",
    color: "#635280",
    soft: "#eeeaf6",
    summary: "5 个内置研究技能 + 自定义 Markdown 技能、长期研究偏好与 SSH/GPU 集群控制。",
  },
  {
    id: "exports_ui",
    title: "文档导出与互动面板",
    en: "Exports & UI Views",
    color: "#28665a",
    soft: "#e4f2ef",
    summary: "导出离线交互式 HTML / Word / PDF / Drive，并在 Antigravity 内唤起工作台与工具树。",
  },
];

export const TOOL_NODES: ToolNodeMeta[] = [
  // 1. connection
  {
    id: "check_env", tool: "po_check_environment", title: "检查本机环境", group: "connection", risk: "read",
    description: "检测后端仓库、Python、.env 存在性（绝不回传密钥）、TeX/Pandoc/Chrome/Lean/SSH 与 SQLite 计数。",
    required: [], workflow: "所有写作与编译前的首选探针。", guardrail: "只读检查，永远不会暴露 API 密钥。", exampleArgs: {},
  },
  {
    id: "check_prov", tool: "po_check_providers", title: "探测模型与文献源", group: "connection", risk: "read",
    description: "检查 20+ 模型平台与 8 个学术源配置；支持按需 ping 测试与模型列表查询。",
    required: [], workflow: "在启动流水线前确认所选模型链路可用。", guardrail: "设置 ping=true 会发出极小测试请求。", exampleArgs: { ping: false, scholar: false, list_models: true },
  },
  {
    id: "settings_get", tool: "po_settings_and_setup", action: "get_settings", title: "读取桌面端设置", group: "connection", risk: "read",
    description: "读取 SQLite app_settings 与解析后的后端路径、providers.json 位置和安装脚本。",
    required: [], workflow: "核对 Desktop 与 Antigravity 是否共享同一套数据库与后端目录。", guardrail: "只读操作。", exampleArgs: { action: "get_settings" },
  },
  {
    id: "settings_env", tool: "po_settings_and_setup", action: "env_readiness", title: "20+ 平台就绪矩阵", group: "connection", risk: "read",
    description: "返回 20 个内置平台与自定义提供商的布尔凭证就绪状态（绝不泄露密钥值）。",
    required: [], workflow: "排查模型缺 Key 或缺少 BASE_URL。", guardrail: "仅返回布尔值与变量名。", exampleArgs: { action: "env_readiness" },
  },
  {
    id: "settings_val", tool: "po_settings_and_setup", action: "validate_providers_json", title: "校验 providers.json", group: "connection", risk: "read",
    description: "按 Desktop providers.rs 同款规则严格校验自定义网关字段、类型与任务路由表。",
    required: [], workflow: "修改中转站或模型路由后先做静态校验。", guardrail: "不展示内联 api_key。", exampleArgs: { action: "validate_providers_json" },
  },
  {
    id: "settings_upd", tool: "po_settings_and_setup", action: "update_settings", title: "更新后端路径配置", group: "connection", risk: "write",
    description: "写入 paper_orchestra_dir / python_path / output_root_dir 到 app_settings。",
    required: [], workflow: "需要切换本地后端目录或输出根目录时使用。", guardrail: "写入前默认校验可执行性与 CLI 文件存在性。", exampleArgs: { action: "update_settings", validate: true },
  },
  {
    id: "settings_setup", tool: "po_settings_and_setup", action: "setup_info", title: "发现一键安装脚本", group: "connection", risk: "read",
    description: "探测 scripts/setup-backend.sh / .ps1 并返回推荐终端安装命令。",
    required: [], workflow: "新机器部署 Python 后端时获取命令。", guardrail: "仅返回路径与命令说明，不擅自覆盖环境。", exampleArgs: { action: "setup_info" },
  },
  {
    id: "bridge_status", tool: "po_studio_bridge", action: "status", title: "Desktop 桥接状态", group: "connection", risk: "read",
    description: "探测 codex-bridge.json 回环 TCP 端口、Desktop App 与 integrations 套件状态。",
    required: [], workflow: "联动桌面端窗口前先检查桥接是否在线。", guardrail: "只连接本机 127.0.0.1。", exampleArgs: { action: "status" },
  },
  {
    id: "bridge_open", tool: "po_studio_bridge", action: "open_studio", title: "唤起 Desktop 桌面端", group: "connection", risk: "write",
    description: "若未运行则启动 PaperOrchestra Studio 桌面应用，并切换到指定视图标签页。",
    required: [], workflow: "用户需要在桌面端交互圈选图稿或查看界面时调用。", guardrail: "仅当明确需要桌面 App 时拉起进程。", exampleArgs: { action: "open_studio", tab: "workspace" },
  },
  {
    id: "bridge_wb", tool: "po_studio_bridge", action: "launch_workbench", title: "发现/启动伴随工作台", group: "connection", risk: "read",
    description: "定位或启动 integrations/live-workbench、tool-tree、canvas-console、qoder-plugin。",
    required: [], workflow: "配合 check_only=true 查看路径，或在用户要求时在浏览器打开独立工作台。", guardrail: "默认不自动开启后台常驻端口。", exampleArgs: { action: "launch_workbench", target: "live_workbench", check_only: true },
  },
  {
    id: "bridge_jobs", tool: "po_studio_bridge", action: "list_bridge_jobs", title: "列出 Desktop 桥接作业", group: "connection", risk: "read",
    description: "查询当前桌面端本地 TCP 桥接正在执行或已完成的异步作业。",
    required: [], workflow: "追踪从 bridge 下发的桌面端任务。", guardrail: "只读。", exampleArgs: { action: "list_bridge_jobs" },
  },
  {
    id: "job_list", tool: "po_job_manage", action: "list", title: "列出插件后台作业", group: "connection", risk: "read",
    description: "查看通过插件启动的论文运行、SOP 与数据分析后台作业列表。",
    required: [], workflow: "随时掌握异步任务状态与 PID。", guardrail: "只读。", exampleArgs: { action: "list" },
  },
  {
    id: "job_status", tool: "po_job_manage", action: "status", title: "查看作业状态与日志", group: "connection", risk: "read",
    description: "轮询指定 job_id 的运行阶段、退出码、Token 统计与脱敏日志尾部。",
    required: ["job_id"], workflow: "论文生成或数据分析期间定期查看进度。", guardrail: "日志自动脱敏。", exampleArgs: { action: "status", job_id: "" },
  },
  {
    id: "job_stop", tool: "po_job_manage", action: "stop", title: "终止后台作业", group: "connection", risk: "destructive",
    description: "向指定 job_id 的进程组发送 SIGTERM（必要时 SIGKILL）以中止运行。",
    required: ["job_id"], workflow: "发现模型死循环、配错模板或用户明确叫停时使用。", guardrail: "破坏性操作，必须二次确认。", exampleArgs: { action: "stop", job_id: "" },
  },

  // 2. projects
  {
    id: "tpl_list", tool: "po_list_templates", title: "列出 19 套论文模板", group: "projects", risk: "read",
    description: "列出全部会议、Nature/ACS/AIP/RSC/Elsevier 期刊、中文 GB/T 7714 与数模模板。",
    required: [], workflow: "新建项目前挑选合适的 template_name。", guardrail: "只读。", exampleArgs: {},
  },
  {
    id: "intake_parse", tool: "po_parse_intake_form", title: "解析科研立项单", group: "projects", risk: "read",
    description: "从 .pdf/.docx/.md/.txt 立项材料抽取标题、模板、Idea 与实验记录。",
    required: ["file_path"], workflow: "拿到用户文档后快速填充新项目。", guardrail: "只读解析源文档。", exampleArgs: { file_path: "" },
  },
  {
    id: "proj_list", tool: "po_projects", action: "list", title: "列出全部项目", group: "projects", risk: "read",
    description: "从共享 SQLite 读取所有论文项目概览、图表数与运行次数。",
    required: [], workflow: "选择要查看、修改或启动的项目。", guardrail: "只读。", exampleArgs: { action: "list" },
  },
  {
    id: "proj_get", tool: "po_projects", action: "get", title: "读取项目详情", group: "projects", risk: "read",
    description: "读取项目完整 idea_markdown、experimental_log_markdown、图稿与历史 runs。",
    required: ["project_id"], workflow: "修改项目参数前先获取最新的 updated_at。", guardrail: "只读。", exampleArgs: { action: "get", project_id: "" },
  },
  {
    id: "proj_create", tool: "po_projects", action: "create", title: "创建论文项目", group: "projects", risk: "write",
    description: "在 SQLite 中创建新的论文项目记录。",
    required: ["title"], workflow: "立项第一步。", guardrail: "新建条目不影响已有项目。", exampleArgs: { action: "create", title: "New Paper Project", template_name: "cvpr2025" },
  },
  {
    id: "proj_update", tool: "po_projects", action: "update", title: "更新项目与防冲突保存", group: "projects", risk: "write",
    description: "更新想法、实验记录、模型或模板；支持 expected_updated_at 防止多人/双端并发覆盖。",
    required: ["project_id"], workflow: "先 get 获取 updated_at，再带 expected_updated_at 提交更新。", guardrail: "版本号不一致将拒绝写入。", exampleArgs: { action: "update", project_id: "" },
  },
  {
    id: "proj_cvpr", tool: "po_projects", action: "import_cvpr_example", title: "导入 CVPR 官方示例", group: "projects", risk: "write",
    description: "从 frontend/examples/cvpr_example.json 一键导入 TSAM 完整示例项目与图稿。",
    required: [], workflow: "初次体验或演示完整流水线时快速建档。", guardrail: "会新增一条项目记录。", exampleArgs: { action: "import_cvpr_example" },
  },
  {
    id: "proj_fig_add", tool: "po_projects", action: "add_figure", title: "添加项目原始图稿", group: "projects", risk: "write",
    description: "将本地图片挂载到指定项目并附带初始图注。",
    required: ["project_id", "figure_path"], workflow: "为实验日志补充已有可视化图片。", guardrail: "仅记录路径并关联项目。", exampleArgs: { action: "add_figure", project_id: "", figure_path: "" },
  },
  {
    id: "proj_fig_cap", tool: "po_projects", action: "update_figure_caption", title: "更新项目图注", group: "projects", risk: "write",
    description: "修改已挂载项目图稿的图注文本，并刷新项目 updated_at。",
    required: ["figure_id", "figure_caption"], workflow: "按 Nature/顶会规范完善图注说明。", guardrail: "仅改数据库图注文字。", exampleArgs: { action: "update_figure_caption", figure_id: "", figure_caption: "" },
  },
  {
    id: "proj_delete", tool: "po_projects", action: "delete", title: "删除项目", group: "projects", risk: "destructive",
    description: "从 SQLite 删除指定项目及其图稿、运行索引与日志行。",
    required: ["project_id"], workflow: "清理作废的测试项目。", guardrail: "高风险破坏性操作，必须明确确认。", exampleArgs: { action: "delete", project_id: "" },
  },

  // 3. pipeline
  {
    id: "run_start", tool: "po_start_paper_run", title: "启动完整论文流水线", group: "pipeline", risk: "write",
    description: "物化材料并在后台运行 19 步可控的多智能体写作流水线（含绘图批评环与编译）。",
    required: [], workflow: "确认项目 idea 与 experimental_log 非空后启动。", guardrail: "消耗模型额度，在后台异步执行并返回 job_id/run_dir。", exampleArgs: { project_id: "" },
  },
  {
    id: "run_resume", tool: "po_projects", action: "resume_run", title: "从检查点恢复运行", group: "pipeline", risk: "write",
    description: "携带 --resume 从已有 run_dir 的检查点断点续跑。",
    required: ["run_id"], workflow: "中断、修好 API 或材料后继续未完成运行。", guardrail: "会复用既有 run_dir 继续写入。", exampleArgs: { action: "resume_run", run_id: "" },
  },
  {
    id: "run_list", tool: "po_projects", action: "list_runs", title: "查询运行历史", group: "pipeline", risk: "read",
    description: "查询指定项目或最近 30 条流水线运行记录。",
    required: [], workflow: "查找要检视、恢复或打开的 run_dir。", guardrail: "只读。", exampleArgs: { action: "list_runs" },
  },
  {
    id: "run_logs", tool: "po_projects", action: "get_run_logs", title: "增量拉取运行日志", group: "pipeline", risk: "read",
    description: "按 after_id 分页读取 SQLite run_logs 表记录并自动脱敏。",
    required: ["run_id"], workflow: "复盘桌面端或流水线运行期间的分阶段日志。", guardrail: "自动脱敏。", exampleArgs: { action: "get_run_logs", run_id: "" },
  },
  {
    id: "run_summary", tool: "po_inspect_run", action: "summary", title: "盘点运行产物", group: "pipeline", risk: "read",
    description: "列出 final_paper.pdf、TeX 源码目录、citation_audit.json、usage_report.json 与审稿报告。",
    required: ["run_dir"], workflow: "运行结束后第一站，快速确认产出物完备度。", guardrail: "只读。", exampleArgs: { action: "summary", run_dir: "" },
  },
  {
    id: "run_read_art", tool: "po_inspect_run", action: "read_artifact", title: "安全读取运行文本产物", group: "pipeline", risk: "read",
    description: "限定在 run_dir 边界内读取大纲、审稿意见、引文审计或用量 JSON。",
    required: ["run_dir", "relative_path"], workflow: "检查 citation_audit.md 或 peer_reviews/*.md。", guardrail: "拒绝目录穿越，自动截断与脱敏。", exampleArgs: { action: "read_artifact", run_dir: "", relative_path: "citation_audit.json" },
  },
  {
    id: "run_copy_pdf", tool: "po_inspect_run", action: "copy_pdf", title: "安全导出最终 PDF", group: "pipeline", risk: "write",
    description: "以独占新建模式（不覆盖已有文件）将 final_paper.pdf 复制到目标目录或默认 PDF 目录。",
    required: ["run_dir"], workflow: "交付论文终稿 PDF 到用户指定目录。", guardrail: "若目标文件已存在则拒绝覆盖。", exampleArgs: { action: "copy_pdf", run_dir: "" },
  },
  {
    id: "run_delete", tool: "po_projects", action: "delete_run", title: "删除运行记录", group: "pipeline", risk: "destructive",
    description: "从数据库移除非运行中状态的 run 记录及 run_logs（不删除磁盘 run_dir 文件）。",
    required: ["run_id"], workflow: "清理数据库里冗余的失败记录。", guardrail: "运行中禁止删除；高风险动作需确认。", exampleArgs: { action: "delete_run", run_id: "" },
  },

  // 4. latex
  {
    id: "ltx_open", tool: "po_latex_workbench", action: "open", title: "打开 LaTeX 源码工程", group: "latex", risk: "read",
    description: "探测主 .tex、可编辑文件列表、已备份原稿及当前运行锁状态。",
    required: ["run_dir"], workflow: "协同编辑前先确认主文件和是否有运行锁。", guardrail: "只读。", exampleArgs: { action: "open", run_dir: "" },
  },
  {
    id: "ltx_read", tool: "po_latex_workbench", action: "read", title: "读取 TeX/Bib 与 mtime", group: "latex", risk: "read",
    description: "读取安全边界内的 .tex/.bib 源码并返回精确 mtimeMs 用于防冲突保存。",
    required: ["run_dir"], workflow: "修改前必读，拿到 mtimeMs。", guardrail: "仅限源码目录内 UTF-8 文本。", exampleArgs: { action: "read", run_dir: "" },
  },
  {
    id: "ltx_save", tool: "po_latex_workbench", action: "save", title: "防冲突保存并备份原稿", group: "latex", risk: "write",
    description: "校验 expected_mtime_ms 与运行锁，首次修改自动将原稿原子备份至 .edit_backup/。",
    required: ["run_dir", "content", "expected_mtime_ms"], workflow: "改完源码后安全落盘，随时可 restore 回滚。", guardrail: "时间戳不一致或论文生成中均拒绝覆盖。", exampleArgs: { action: "save", run_dir: "", content: "", expected_mtime_ms: 0 },
  },
  {
    id: "ltx_restore", tool: "po_latex_workbench", action: "restore", title: "从原稿备份恢复", group: "latex", risk: "destructive",
    description: "从 .edit_backup/ 将文件恢复到首次在线修改前的版本。",
    required: ["run_dir"], workflow: "误改源码需一键回滚初始生成版本时使用。", guardrail: "会覆盖当前未提交修改，需确认。", exampleArgs: { action: "restore", run_dir: "" },
  },
  {
    id: "ltx_compile", tool: "po_latex_workbench", action: "compile", title: "自动 CJK XeLaTeX 编译", group: "latex", risk: "write",
    description: "编译至 .live_preview/：含中文/ctex 时自动启用 -pdfxe (xelatex)，并结构化解析 .log/.blg 错误。",
    required: ["run_dir"], workflow: "每次改写 TeX 后即时编译并定位错误行号。", guardrail: "产物隔离在 .live_preview/，不污染源目录。", exampleArgs: { action: "compile", run_dir: "" },
  },
  {
    id: "ltx_sync", tool: "po_latex_workbench", action: "sync_view", title: "双向 SyncTeX 高亮与点击跳转", group: "latex", risk: "read",
    description: "支持 end_line 多行区间高亮（sync_view）与点击 PDF 反跳源码行（sync_edit），含 pdf_path 显式绑定。",
    required: ["run_dir"], workflow: "Overleaf 式左右分栏对照定位源码与 PDF 页面坐标。", guardrail: "只读定位。", exampleArgs: { action: "sync_view", run_dir: "", line: 1, end_line: 1, column: 1 },
  },
  {
    id: "ltx_overleaf", tool: "po_latex_workbench", action: "export_overleaf", title: "干净导出 Overleaf ZIP", group: "latex", risk: "write",
    description: "仅打包主文档所需源码与图片、自动替换为 references_cited.bib 并剔除编译垃圾。",
    required: ["run_dir"], workflow: "交付给合作者或上传 Overleaf。", guardrail: "若指定已有路径且未允许覆盖则拒绝写。", exampleArgs: { action: "export_overleaf", run_dir: "" },
  },
  {
    id: "ltx_copilot_edit", tool: "po_latex_copilot", action: "edit", title: "AI LaTeX 局部改写", group: "latex", risk: "write",
    description: "结合选定行号区间与编译报错日志对指定 .tex 文件进行局部 AI 修订。",
    required: ["file_path", "instruction"], workflow: "修复复杂表格/排版错误或按审稿意见改段。", guardrail: "建议配合 workbench read/save 保留原稿备份。", exampleArgs: { action: "edit", file_path: "", instruction: "" },
  },
  {
    id: "ltx_polish", tool: "po_latex_copilot", action: "polish_abstract", title: "摘要结构诊断与获奖文风改写对照", group: "latex", risk: "read",
    description: "按获奖论文 7 字段离线诊断摘要并计算 AI 节奏指标（diagnose），或 AI 生成逐句修改对照表与 Limitations 笔记（rewrite）。",
    required: [], workflow: "投稿前打磨摘要与引言主张表达，杜绝编造数字。", guardrail: "diagnose 模式纯离线不耗额度；rewrite 保留原始数据。", exampleArgs: { action: "polish_abstract", mode: "diagnose", run_dir: "" },
  },
  {
    id: "ltx_trace", tool: "po_latex_copilot", action: "trace_citations", title: "正文引文行级溯源", group: "latex", risk: "read",
    description: "建立正文 \\cite 论断与 citation_map / 文献证据的逐处映射。",
    required: ["run_dir"], workflow: "核查某句话引文是否张冠李戴。", guardrail: "只读分析。", exampleArgs: { action: "trace_citations", run_dir: "" },
  },

  // 5. audit
  {
    id: "audit_manuscript", tool: "po_submission_audit", action: "manuscript-audit", title: "Nature + 顶会获奖文风/配图体检", group: "audit", risk: "read",
    description: "核查句尾分词占比、对比排比、工作流泄露、摘要数字预算、图注首句结论、首次引用顺序、栅格图与统计规范。",
    required: ["run_dir"], workflow: "投稿前全文质量与 AI 痕迹把关。", guardrail: "离线只读，自动区分中英文与期刊/顶会规则。", exampleArgs: { action: "manuscript-audit", run_dir: "" },
  },
  {
    id: "audit_report", tool: "po_submission_audit", action: "author-report", title: "生成作者交付报告 (report.md)", group: "audit", risk: "write",
    description: "自动汇总论文叙事路由、主张-证据表、配图规划、量化文风摘要、[TODO] 清单与 Limitations 要点。",
    required: ["run_dir"], workflow: "成稿或润色后导出给作者复核的完整检查报告。", guardrail: "仅生成 report.md / author_report.json，不改源码。", exampleArgs: { action: "author-report", run_dir: "" },
  },
  {
    id: "audit_cite", tool: "po_submission_audit", action: "cite-online", title: "联网核验全部参考文献", group: "audit", risk: "read",
    description: "逐条联网 Crossref / arXiv / Semantic Scholar 核对 DOI、作者与年份。",
    required: ["run_dir"], workflow: "杜绝编造或陈旧错配引文。", guardrail: "仅发起公开学术库只读查询。", exampleArgs: { action: "cite-online", run_dir: "" },
  },
  {
    id: "audit_blind", tool: "po_submission_audit", action: "blind", title: "双盲匿名泄密检查", group: "audit", risk: "read",
    description: "扫描 TeX 与附属文件中的作者名、单位、基金号、致谢或个人链接。",
    required: ["run_dir"], workflow: "投 CVPR/ICLR/NeurIPS/ACL 前必查。", guardrail: "离线只读。", exampleArgs: { action: "blind", run_dir: "" },
  },
  {
    id: "audit_diag", tool: "po_submission_audit", action: "diagnose", title: "LaTeX 日志诊断", group: "audit", risk: "read",
    description: "系统性汇总 TeX 日志中的未定义引用、过宽盒子与字体问题。",
    required: ["run_dir"], workflow: "排查隐藏排版缺陷。", guardrail: "离线只读。", exampleArgs: { action: "diagnose", run_dir: "" },
  },
  {
    id: "audit_arxiv", tool: "po_submission_audit", action: "arxiv", title: "生成 arXiv 投稿包", group: "audit", risk: "write",
    description: "清理注释并打包符合 arXiv 要求的纯源码投稿压缩包。",
    required: ["run_dir"], workflow: "论文终稿上传 arXiv。", guardrail: "写入新路径。", exampleArgs: { action: "arxiv", run_dir: "" },
  },
  {
    id: "audit_versions", tool: "po_submission_audit", action: "versions", title: "盘点手稿版本快照", group: "audit", risk: "read",
    description: "列出 run_dir 中的多轮初稿与精修 PDF/TeX 演化版本。",
    required: ["run_dir"], workflow: "挑出两版做 diff 对比。", guardrail: "只读。", exampleArgs: { action: "versions", run_dir: "" },
  },
  {
    id: "audit_diff", tool: "po_submission_audit", action: "diff", title: "latexdiff 修订对照 PDF", group: "audit", risk: "write",
    description: "对比 old_tex 与 new_tex 生成带红蓝修订标记的 PDF（可选仅保留变动页）。",
    required: ["old_tex", "new_tex"], workflow: "Rebuttal / Major Revision 交付审稿人。", guardrail: "依赖系统 latexdiff，输出到新 PDF。", exampleArgs: { action: "diff", old_tex: "", new_tex: "", only_changed_pages: true },
  },
  {
    id: "audit_lean", tool: "po_submission_audit", action: "lean-audit", title: "Lean 4 形式化证明核查", group: "audit", risk: "read",
    description: "检查形式化数学项目中是否存在 sorry、admit 或可疑自定义公理。",
    required: ["project_dir"], workflow: "含 Lean 4 证明附件时使用。", guardrail: "只读审计。", exampleArgs: { action: "lean-audit", project_dir: "" },
  },
  {
    id: "rev_matrix", tool: "po_revision_toolkit", action: "review_matrix", title: "审稿意见分拣矩阵", group: "audit", risk: "read",
    description: "把真实或模拟审稿意见拆成逐条意见（R1.1、R1.2…），标注类型、严重程度与是否需要作者补充数据，输出 Markdown 回复矩阵。",
    required: [], workflow: "收到审稿意见后先分拣，再逐条撰写回复信。", guardrail: "纯离线规则判断，不调用模型；需要新实验的条目绝不编造结果。", exampleArgs: { action: "review_matrix", reviews_text: "", run_dir: "" },
  },
  {
    id: "rev_preflight", tool: "po_revision_toolkit", action: "preflight", title: "投稿预检（页数/字数/占位符/匿名/声明）", group: "audit", risk: "read",
    description: "按传入的页数、字数、摘要上限核对手稿，并检查 TODO 占位符、未定义引用、双盲泄露与必需声明。",
    required: ["run_dir"], workflow: "投稿前排除 Desk-Reject 风险。", guardrail: "离线只读；上限以你传入的数值为准，请对照会议/期刊最新要求。", exampleArgs: { action: "preflight", run_dir: "", max_pages: 9, anonymous: true },
  },
  {
    id: "rev_retraction", tool: "po_revision_toolkit", action: "retraction_check", title: "参考文献撤稿检查", group: "audit", risk: "read",
    description: "把 .bib 中每个 DOI 送 Crossref 查询撤稿、关注声明与更正通知。",
    required: [], workflow: "投稿前确认没有引用已撤稿论文。", guardrail: "联网只读；查询失败的条目标为 unverified，不会当作通过。", exampleArgs: { action: "retraction_check", run_dir: "" },
  },
  {
    id: "rev_checklist", tool: "po_revision_toolkit", action: "reporting_checklist", title: "报告规范清单（CONSORT/PRISMA/STROBE/ARRIVE/ML）", group: "audit", risk: "read",
    description: "按报告规范的条目主题扫描手稿，给出命中行与缺失项。",
    required: ["guideline"], workflow: "临床试验、系统综述、观察性研究、动物实验与机器学习论文投稿前自查。", guardrail: "离线只读；条目为简化主题，正式投稿需填写官方清单。", exampleArgs: { action: "reporting_checklist", guideline: "ml_reproducibility", run_dir: "" },
  },

  // 6. research
  {
    id: "data_profile", tool: "po_data_analysis", action: "profile", title: "数据集离线体检", group: "research", risk: "read",
    description: "对 .csv/.tsv/.xlsx/.parquet/.json 执行列类型、缺失率、分位数与异常值扫描。",
    required: ["file_path"], workflow: "写实验或跑回归分析前先了解数据分布。", guardrail: "纯离线本地统计，不花模型额度。", exampleArgs: { action: "profile", file_path: "" },
  },
  {
    id: "data_analyze", tool: "po_data_analysis", action: "analyze", title: "7 步统计与自修复分析", group: "research", risk: "write",
    description: "在后台运行 7 步可复现 Python 数据分析、假设检验（含效应量/CI）与绘图流水线。",
    required: ["file_path"], workflow: "生成带审计报告与图表的完整实验分析包。", guardrail: "默认后台异步执行，写入新目录。", exampleArgs: { action: "analyze", file_path: "", question: "", async_mode: true },
  },
  {
    id: "sop_read", tool: "po_run_sop", action: "paper_reading", title: "六步论文精读 SOP", group: "research", risk: "write",
    description: "按严格出处标签生成六步证据化精读报告（支持转交互版 HTML）。",
    required: [], workflow: "输入 PDF/HTML/Markdown 论文深度精读。", guardrail: "严格保留【原文】【审读推导】【不可核实】。", exampleArgs: { sop: "paper_reading", files: [] },
  },
  {
    id: "sop_lit", tool: "po_run_sop", action: "lit_review", title: "防编造主题综述 SOP", group: "research", risk: "write",
    description: "执行 Q->E->T->A 防编造主题综述，先建矩阵后成文。",
    required: [], workflow: "结合本地文献库与在线检索撰写综述。", guardrail: "可设 query_online=false 关闭联网检索。", exampleArgs: { sop: "lit_review", topic: "" },
  },
  {
    id: "sop_idea", tool: "po_run_sop", action: "topic_ideation", title: "0-to-1 选题立项 SOP", group: "research", risk: "write",
    description: "从研究方向、痛点与算力边界产出现实可行的研究提案与实验验证设计。",
    required: [], workflow: "开题构思阶段使用。", guardrail: "输出新方案到指定 out_dir。", exampleArgs: { sop: "topic_ideation", topic: "" },
  },
  {
    id: "sop_repro", tool: "po_run_sop", action: "reproduction", title: "算法复现 7 步 SOP", group: "research", risk: "write",
    description: "拆分可测模块、对齐指标语义并产出分阶段复现工程计划。",
    required: [], workflow: "复现已有论文或开源代码时使用。", guardrail: "生成复现清单与测试协议。", exampleArgs: { sop: "reproduction", topic: "" },
  },
  {
    id: "sop_math", tool: "po_run_sop", action: "math_modeling", title: "数模竞赛 SOP (MCM/CUMCM)", group: "research", risk: "write",
    description: "面向美赛/国赛的建模拆解、敏感性分析与公式推导工作流。",
    required: [], workflow: "配合 math-modeling-* 模板使用。", guardrail: "支持注入研究记忆。", exampleArgs: { sop: "math_modeling", topic: "" },
  },

  // 7. figures
  {
    id: "fig_list", tool: "po_figure_studio", action: "list", title: "列出图稿版本束", group: "figures", risk: "read",
    description: "扫描指定目录下的图稿 bundle、版本历史与主预览图路径。",
    required: ["dir_path"], workflow: "先列出图稿，再进行红框局部重绘或回滚。", guardrail: "只读。", exampleArgs: { action: "list", dir_path: "" },
  },
  {
    id: "fig_bundle", tool: "po_figure_studio", action: "ensure_bundle", title: "纳管单图为版本束", group: "figures", risk: "write",
    description: "将既有 PNG/JPG/SVG 与可选绘图脚本纳管进 versioned bundle 以便局部重绘。",
    required: ["dir_path", "image"], workflow: "将外部已有图接入局部重绘工作台。", guardrail: "在 bundle 目录生成版本快照。", exampleArgs: { action: "ensure_bundle", dir_path: "", image: "" },
  },
  {
    id: "fig_create", tool: "po_figure_studio", action: "create", title: "三路由生成科学图表", group: "figures", risk: "write",
    description: "按 data_code（Matplotlib）、schematic_svg（矢量架构）或 concept_image 生成高质量科研图。",
    required: [], workflow: "根据数据或描述创建首版科研图（v1）。", guardrail: "生成耗时较长且调用图像/代码模型。", exampleArgs: { action: "create", mode: "data_code", prompt: "" },
  },
  {
    id: "fig_revise", tool: "po_figure_studio", action: "revise", title: "红框 bbox 局部重绘", group: "figures", risk: "write",
    description: "支持传 {x,y,w,h} 或 'x,y,w,h' 归一化红框，仅针对圈选局部执行精准重构（vN+1）。",
    required: ["bundle", "instruction"], workflow: "在本面板「图稿」页拖拽圈选后直调。", guardrail: "严格校验 bbox ∈ [0,1]，保留旧版可回滚。", exampleArgs: { action: "revise", bundle: "", instruction: "", bbox: { x: 0.06, y: 0.09, w: 0.28, h: 0.24 } },
  },
  {
    id: "fig_restore", tool: "po_figure_studio", action: "restore", title: "回滚图稿历史版本", group: "figures", risk: "write",
    description: "将图稿 bundle 的活跃版本切回指定历史版本（如 v1）。",
    required: ["bundle", "version"], workflow: "新版重绘不如上一版时安全回退。", guardrail: "保留历史快照，可逆操作。", exampleArgs: { action: "restore", bundle: "", version: "v1" },
  },

  // 8. library
  {
    id: "lib_search", tool: "po_literature_library", action: "search_online", title: "8 源限速在线检索", group: "library", risk: "read",
    description: "聚合检索 Semantic Scholar / arXiv / OpenAlex / Crossref / dblp / Europe PMC / PubMed / CORE。",
    required: ["query"], workflow: "按关键词找前沿/奠基文献，支持自动扩展查询。", guardrail: "共享限速器防止触发 429。", exampleArgs: { action: "search_online", query: "", limit: 10 },
  },
  {
    id: "lib_doi", tool: "po_literature_library", action: "import_doi", title: "按 DOI 解析并入库", group: "library", risk: "write",
    description: "通过 Crossref/DOI 解析完整元数据、BibTeX 与 GB/T 7714 并存入文献库。",
    required: ["doi"], workflow: "快速收录已知 DOI 的正规出版文献。", guardrail: "写入本地 library_items 表。", exampleArgs: { action: "import_doi", doi: "", save_to_library: true },
  },
  {
    id: "lib_pdf", tool: "po_literature_library", action: "import_pdf", title: "解析本地 PDF 入库", group: "library", risk: "write",
    description: "抽取本地 PDF 标题、摘要、关键论断与元信息并可选存入 SQLite 文献库。",
    required: ["pdf_path"], workflow: "批量整理本地文献 PDF。", guardrail: "可选 no_llm=true 纯本地解析。", exampleArgs: { action: "import_pdf", pdf_path: "", save_to_library: true },
  },
  {
    id: "lib_list", tool: "po_literature_library", action: "list", title: "分页浏览本地文献库", group: "library", risk: "read",
    description: "按文献夹 folder 筛选并带 offset/limit（默认 25）分页读取已保存条目。",
    required: [], workflow: "高效浏览百篇级文献收藏。", guardrail: "只读分页。", exampleArgs: { action: "list", offset: 0, limit: 25 },
  },
  {
    id: "lib_graph", tool: "po_literature_library", action: "build_graph", title: "生成引文知识图谱", group: "library", risk: "read",
    description: "区分综述/奠基/前沿/概念节点，产出推荐阅读路线与对比矩阵。",
    required: [], workflow: "选定多篇文献后宏观把握技术脉络。", guardrail: "只读生成分析结果。", exampleArgs: { action: "build_graph" },
  },
  {
    id: "lib_export", tool: "po_literature_library", action: "export_bib", title: "导出 BibTeX / GB/T 7714", group: "library", risk: "read",
    description: "一键生成格式化 BibTeX 或中文国标 GB/T 7714 顺序编码参考文献。",
    required: [], workflow: "整理完文献后导出直接粘进论文。", guardrail: "只读文本返回。", exampleArgs: { action: "export_bib", format: "bibtex" },
  },
  {
    id: "lib_del", tool: "po_literature_library", action: "delete", title: "删除文献库条目", group: "library", risk: "destructive",
    description: "从 SQLite library_items 删除指定 item_id。",
    required: ["item_id"], workflow: "清理重复或错录条目。", guardrail: "需明确确认，不影响外部原 PDF。", exampleArgs: { action: "delete", item_id: "" },
  },

  // 9. skills_compute
  {
    id: "skill_list", tool: "po_custom_skills", action: "list", title: "列出 5 个内置与自定义技能", group: "skills_compute", risk: "read",
    description: "同时列出 Desktop 同源 5 个内置科研技能与个人自定义 Markdown 技能。",
    required: [], workflow: "查看可用方法学 SOP 技能。", guardrail: "内置技能只读受保护。", exampleArgs: { action: "list" },
  },
  {
    id: "skill_get", tool: "po_custom_skills", action: "get", title: "读取技能完整内容", group: "skills_compute", risk: "read",
    description: "根据 skill_id 读取完整 Markdown 提示词与 Frontmatter 元数据。",
    required: ["skill_id"], workflow: "执行或修改前核对技能指令原文。", guardrail: "只读。", exampleArgs: { action: "get", skill_id: "builtin-paper-deep-read" },
  },
  {
    id: "skill_save", tool: "po_custom_skills", action: "save", title: "保存/导入自定义技能", group: "skills_compute", risk: "write",
    description: "保存或导入本地 .md 技能文件（save / import_file / fetch_url）到共享技能目录。",
    required: ["name", "content"], workflow: "沉淀实验室独有的审稿或分析规范。", guardrail: "不允许覆盖 builtin-* 前缀。", exampleArgs: { action: "save", name: "", content: "" },
  },
  {
    id: "skill_run", tool: "po_custom_skills", action: "run", title: "执行内置或自定义技能", group: "skills_compute", risk: "write",
    description: "挂载用户文件与研究记忆，执行任意内置（builtin-*）或自定义技能。",
    required: ["prompt"], workflow: "以标准化科研规范处理附件文档。", guardrail: "写入新输出目录。", exampleArgs: { action: "run", skill_id: "builtin-nature-de-ai-polish", prompt: "" },
  },
  {
    id: "mem_prefs", tool: "po_compute_and_memory", action: "list_memories", title: "管理研究记忆与偏好", group: "skills_compute", risk: "read",
    description: "读写实验室长期研究偏好（语言、引文规范、规则）与启用记忆条目。",
    required: [], workflow: "所有 SOP 与论文写作自动继承已启用记忆。", guardrail: "支持增删改查。", exampleArgs: { action: "list_memories" },
  },
  {
    id: "ssh_list", tool: "po_compute_and_memory", action: "list_hosts", title: "列出算力主机", group: "skills_compute", risk: "read",
    description: "读取已登记的远程 SSH/GPU 服务器清单、最近状态与 GPU 信息。",
    required: [], workflow: "查看实验室 GPU 节点配置。", guardrail: "从不回传私钥文件原文或 PEM 内容。", exampleArgs: { action: "list_hosts" },
  },
  {
    id: "ssh_test", tool: "po_compute_and_memory", action: "test_host", title: "探测远程 GPU / nvidia-smi", group: "skills_compute", risk: "read",
    description: "安全检测指定算力主机的连通性与 GPU 卡型/显存占用。",
    required: ["host_id"], workflow: "远程跑大规模消融前先检查空余显存。", guardrail: "只执行只读探针命令。", exampleArgs: { action: "test_host", host_id: "" },
  },
  {
    id: "ssh_exec", tool: "po_compute_and_memory", action: "exec_ssh", title: "远程算力执行与 SCP 传输", group: "skills_compute", risk: "write",
    description: "在已配置远程主机运行实验命令（exec_ssh）或双向传输数据/权重（scp_transfer）。",
    required: ["host_id", "command"], workflow: "将论文实验脚本推至集群执行并拉回 CSV。", guardrail: "写入/执行前应明确目标路径与工作目录。", exampleArgs: { action: "exec_ssh", host_id: "", command: "nvidia-smi" },
  },

  // 10. exports_ui
  {
    id: "exp_status", tool: "po_export_document", action: "status", title: "检查导出环境与目录", group: "exports_ui", risk: "read",
    description: "检查默认 PDF / HTML / Google Drive 导出路径与 Pandoc / Chrome 就绪状态。",
    required: [], workflow: "决定导出路径前先查看默认目录。", guardrail: "只读。", exampleArgs: { action: "status" },
  },
  {
    id: "exp_run", tool: "po_export_document", action: "export", title: "导出交互 HTML / DOCX / PDF", group: "exports_ui", risk: "write",
    description: "导出离线类 Artifacts 交互式精读网页（侧边目录、多主题、证据徽章、MathML）、Word 或 PDF。",
    required: ["format"], workflow: "将 SOP 报告或任意 Markdown 美观交付。", guardrail: "自动写入唯一定名文件，不覆盖旧导出。", exampleArgs: { action: "export", format: "html", interactive_html: true },
  },
  {
    id: "exp_drive", tool: "po_export_document", action: "export_google_drive", title: "同步导出到 Google Drive", group: "exports_ui", risk: "write",
    description: "直接将交付成果导出至本地 Google Drive 同步文件夹。",
    required: ["format"], workflow: "跨设备阅读或团队共享时使用。", guardrail: "不覆盖现有文件。", exampleArgs: { action: "export_google_drive", format: "pdf" },
  },
  {
    id: "ui_wb", tool: "po_open_workbench", title: "打开 PaperOrchestra 实时工作台", group: "exports_ui", risk: "read",
    description: "在 Antigravity 侧栏 / 全局侧边栏 / 对话内直接渲染交互式 9 页签工作台。",
    required: [], workflow: "需要可视化监控、Canvas 协同干预、圈选图稿或编辑 LaTeX 时随时唤起。", guardrail: "支持空参数 {} 一键打开。", exampleArgs: {},
  },
  {
    id: "ui_tree", tool: "po_open_tool_tree", title: "打开 PaperOrchestra 工具树地图", group: "exports_ui", risk: "read",
    description: "在 Antigravity 内渲染按 10 大工作组划分的交互式工具导航图与安全规范。",
    required: [], workflow: "新手上手或快速查找特定参数/调用链路时打开。", guardrail: "支持空参数 {} 一键打开。", exampleArgs: {},
  },
];
