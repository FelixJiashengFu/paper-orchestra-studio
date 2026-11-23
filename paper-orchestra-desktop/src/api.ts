import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { open, save } from "@tauri-apps/plugin-dialog";
import type {
  AppSettings,
  ConnectionCheckReport,
  DataAnalysisResult,
  DataProfile,
  DocumentExportResult,
  EnvSecretsConfig,
  EnvironmentStatus,
  ExportEnvironmentInfo,
  FigureStudioListResult,
  GoogleDriveExportResult,
  IntakeFormResult,
  KnowledgeGraph,
  LatexCopilotEditResult,
  LibraryItem,
  LiveCompileResult,
  LiveEditFile,
  LiveEditSession,
  Project,
  ProjectFigure,
  ProjectSummary,
  ResearchMemory,
  ResearchPreferences,
  RunArtifactsSummary,
  RunLogEntry,
  RunLogEventPayload,
  RunRecord,
  RunStatusEventPayload,
  SaveLibraryItemInput,
  SaveProjectInput,
  SaveResearchMemoryInput,
  SaveSkillInput,
  SaveSshHostInput,
  SkillEntry,
  SopKind,
  SopWorkflowResult,
  SshExecResult,
  SshHost,
  SshTestResult,
  StudioEventPayload,
  SyncBox,
  SyncSource,
  UpdateAppSettingsInput,
  UsageReport,
  CitationTraceItem,
  FigureBundle,
} from "./types";

export const api = {
  getSettings: () => invoke<AppSettings>("get_settings"),
  updateSettings: (input: UpdateAppSettingsInput) =>
    invoke<AppSettings>("update_settings", { input }),
  getEnvConfig: () => invoke<EnvSecretsConfig>("get_env_config"),
  saveEnvConfig: (config: EnvSecretsConfig) =>
    invoke<void>("save_env_config", { config }),
  checkEnvironment: () => invoke<EnvironmentStatus>("check_environment"),
  /**
   * Runs tools/check_providers.py --json (with --ping / --scholar) against the
   * saved configuration. `pingModels` ({provider: model}) picks the model a
   * provider is tested with (--ping-model). Rejects with a Chinese message when the script is
   * missing, Python fails, the output is not JSON or it takes over 120 s.
   */
  checkConnections: (ping: boolean, scholar: boolean, pingModels?: Record<string, string>) =>
    invoke<ConnectionCheckReport>("check_connections", { ping, scholar, pingModels: pingModels ?? null }),
  /**
   * Runs tools/check_providers.py --json --list-models: every configured
   * provider's model list under `models` (free: no generation request).
   */
  listModels: () =>
    invoke<ConnectionCheckReport>("check_connections", { ping: false, scholar: false, listModels: true }),
  /** Checks providers.json text without saving; resolves to "" when valid or blank, else the error message. */
  validateProvidersJson: (text: string) =>
    invoke<string>("validate_providers_json", { text }),

  listProjects: () => invoke<ProjectSummary[]>("list_projects"),
  getProject: (id: string) => invoke<Project>("get_project", { id }),
  createProject: (title: string, templateName: string) =>
    invoke<Project>("create_project", { title, templateName }),
  saveProject: (input: SaveProjectInput) =>
    invoke<Project>("save_project", { input }),
  deleteProject: (id: string) => invoke<void>("delete_project", { id }),

  addProjectFigure: (projectId: string, sourcePath: string, caption: string) =>
    invoke<ProjectFigure>("add_project_figure", {
      input: { projectId, sourcePath, caption },
    }),
  updateProjectFigureCaption: (figureId: string, caption: string) =>
    invoke<void>("update_project_figure_caption", { figureId, caption }),
  deleteProjectFigure: (figureId: string) =>
    invoke<void>("delete_project_figure", { figureId }),

  startRun: (projectId: string) => invoke<RunRecord>("start_run", { projectId }),
  resumeRun: (runId: string) => invoke<RunRecord>("resume_run", { runId }),
  stopRun: (runId: string) => invoke<void>("stop_run", { runId }),
  listRuns: (projectId: string) => invoke<RunRecord[]>("list_runs", { projectId }),
  deleteRun: (runId: string) => invoke<void>("delete_run", { runId }),
  getRunLogs: (runId: string, afterId?: number, limit?: number) =>
    invoke<RunLogEntry[]>("get_run_logs", { runId, afterId, limit }),

  getRunArtifactsSummary: (runDir: string) =>
    invoke<RunArtifactsSummary>("get_run_artifacts_summary", { runDir }),
  readRunTextArtifact: (runDir: string, relativePath: string) =>
    invoke<string>("read_run_text_artifact", { runDir, relativePath }),
  /** Zips the run's final LaTeX sources (Overleaf upload); resolves to the written .zip path. */
  exportRunLatex: (runDir: string, destPath: string) =>
    invoke<string>("export_run_latex", { runDir, destPath }),
  readFileBase64: (filePath: string) =>
    invoke<string>("read_file_base64", { filePath }),

  // 「编辑并实时预览」: one run's export source folder (see LiveEditSession).
  // `file` is always relative to that folder.
  liveEditOpen: (runDir: string) => invoke<LiveEditSession>("live_edit_open", { runDir }),
  liveEditRead: (runDir: string, file: string) =>
    invoke<LiveEditFile>("live_edit_read", { runDir, file }),
  /**
   * Writes a .tex / .bib (the first write keeps the original in .edit_backup/).
   * With `expectedMtimeMs` it refuses when the file changed elsewhere since
   * (LIVE_EDIT_ERR_CONFLICT). Content equal to the file on disk writes nothing.
   * The reply's content is ""; its mtimeMs is the new one. Errors other than
   * LIVE_EDIT_ERR_CONFLICT / LIVE_EDIT_ERR_RUN_ACTIVE may pass: try again.
   */
  liveEditSave: (runDir: string, file: string, content: string, expectedMtimeMs?: number | null) =>
    invoke<LiveEditFile>("live_edit_save", {
      runDir,
      file,
      content,
      expectedMtimeMs: expectedMtimeMs ?? null,
    }),
  /** Copies the backup back over the file; resolves to its full content and new mtime. */
  liveEditRestore: (runDir: string, file: string) =>
    invoke<LiveEditFile>("live_edit_restore", { runDir, file }),
  /** latexmk into <run>/.live_preview/; one compile at a time app-wide (a second call waits). */
  liveEditCompile: (runDir: string, mainFile: string) =>
    invoke<LiveCompileResult>("live_edit_compile", { runDir, mainFile }),
  /**
   * SyncTeX code -> PDF; null when nothing matches. Rejects (with a message to
   * show as it is) when the last compile left no PDF or no .synctex.gz.
   */
  liveEditSyncView: (runDir: string, mainFile: string, file: string, line: number, column: number) =>
    invoke<SyncBox | null>("live_edit_sync_view", { runDir, mainFile, file, line, column }),
  /**
   * SyncTeX PDF -> code (page 1-based, x/y in PDF points from the top-left);
   * null outside the source folder. Rejects like liveEditSyncView.
   */
  liveEditSyncEdit: (runDir: string, mainFile: string, page: number, x: number, y: number) =>
    invoke<SyncSource | null>("live_edit_sync_edit", { runDir, mainFile, page, x, y }),

  openPathInSystem: (targetPath: string) =>
    invoke<void>("open_path_in_system", { targetPath }),
  revealPathInExplorer: (targetPath: string) =>
    invoke<void>("reveal_path_in_explorer", { targetPath }),
  resolvePickedPython: (picked: string, repoDir: string) =>
    invoke<string>("resolve_picked_python", { picked, repoDir }),
  /** 「导入填写单」: parses a PDF / .md / .txt / .docx form; nothing is saved. */
  parseIntakeForm: (path: string) => invoke<IntakeFormResult>("parse_intake_form", { path }),
  /** 「投稿工具」: one subcommand of tools/submission_tools.py; resolves to its JSON report. */
  runSubmissionTool: (args: string[]) => invoke<unknown>("run_submission_tool", { args }),

  // ---- ClawsGO Feature Suite ----
  runFigureStudioList: (dir = "") =>
    invoke<FigureStudioListResult>("run_figure_studio", { args: ["list", "--dir", dir] }),
  runFigureStudioEnsureBundle: (dir: string, imagePath: string, title = "") =>
    invoke<{ ok: boolean; bundle: FigureBundle }>("run_figure_studio", {
      args: ["ensure-bundle", "--dir", dir, "--image", imagePath, ...(title ? ["--title", title] : [])],
    }),
  runFigureStudioRevise: (opts: {
    bundleDir: string;
    instruction: string;
    bbox?: { x: number; y: number; w: number; h: number } | null;
    model?: string;
    baseDir?: string;
    jobId?: string;
  }) => {
    const args = ["revise", "--bundle", opts.bundleDir, "--instruction", opts.instruction];
    if (opts.bbox) {
      args.push("--bbox", `${opts.bbox.x},${opts.bbox.y},${opts.bbox.w},${opts.bbox.h}`);
    }
    if (opts.model) args.push("--model", opts.model);
    if (opts.baseDir) args.push("--base-dir", opts.baseDir);
    return invoke<{ ok: boolean; synced: string[]; bundle: FigureBundle; usageReport?: UsageReport | null }>(
      "run_figure_studio",
      { args, jobId: opts.jobId ?? null }
    );
  },
  runFigureStudioRestore: (bundleDir: string, version: string, baseDir = "") =>
    invoke<{ ok: boolean; synced: string[]; bundle: FigureBundle }>("run_figure_studio", {
      args: ["restore", "--bundle", bundleDir, "--version", version, ...(baseDir ? ["--base-dir", baseDir] : [])],
      jobId: null,
    }),
  runFigureStudioCreate: (opts: {
    outDir?: string;
    mode: "data_code" | "schematic_svg" | "concept_image";
    prompt: string;
    title?: string;
    dataFile?: string;
    refImage?: string;
    model?: string;
    jobId?: string;
  }) => {
    const args = ["create", "--out-dir", opts.outDir ?? "", "--mode", opts.mode, "--prompt", opts.prompt];
    if (opts.title) args.push("--title", opts.title);
    if (opts.dataFile) args.push("--data-file", opts.dataFile);
    if (opts.refImage) args.push("--ref-image", opts.refImage);
    if (opts.model) args.push("--model", opts.model);
    return invoke<{ ok: boolean; bundle: FigureBundle; usageReport?: UsageReport | null }>(
      "run_figure_studio",
      { args, jobId: opts.jobId ?? null }
    );
  },

  runDataProfile: (filePath: string) =>
    invoke<DataProfile>("run_data_profile", { filePath }),
  runDataAnalysis: (filePath: string, question: string, model?: string, jobId?: string) =>
    invoke<DataAnalysisResult>("run_data_analysis", {
      filePath,
      question,
      model: model ?? null,
      jobId: jobId ?? null,
    }),
  runSopWorkflow: (sop: SopKind, payload: Record<string, unknown>, model?: string, jobId?: string) =>
    invoke<SopWorkflowResult>("run_sop_workflow", {
      sop,
      payload,
      model: model ?? null,
      jobId: jobId ?? null,
    }),
  runCustomSkill: (skillId: string, prompt: string, attachedFiles: string[], model?: string, jobId?: string) =>
    invoke<{ ok: boolean; outDir: string; outputPath: string; outputMarkdown: string; usageReport?: UsageReport | null }>(
      "run_custom_skill",
      {
        skillId,
        prompt,
        attachedFiles,
        model: model ?? null,
        jobId: jobId ?? null,
      }
    ),
  cancelStudioJob: (jobId: string) => invoke<boolean>("cancel_studio_job", { jobId }),

  listLibraryItems: () => invoke<LibraryItem[]>("list_library_items"),
  saveLibraryItem: (input: SaveLibraryItemInput) => invoke<LibraryItem>("save_library_item", { input }),
  deleteLibraryItem: (id: string) => invoke<void>("delete_library_item", { id }),
  searchLibraryOnline: (query: string, limit = 15, expand = true) =>
    invoke<{ ok: boolean; query: string; expandedQueries: string[]; items: Array<Partial<LibraryItem>> }>(
      "run_library_tool",
      {
        args: ["search-online", "--query", query, "--limit", String(limit), ...(expand ? ["--expand"] : [])],
        itemsPayload: null,
      }
    ),
  importLibraryPdf: (pdfPath: string) =>
    invoke<{ ok: boolean; item: Partial<LibraryItem> }>("run_library_tool", {
      args: ["import-pdf", "--pdf", pdfPath],
      itemsPayload: null,
    }),
  importLibraryDoi: (doi: string) =>
    invoke<{ ok: boolean; item: Partial<LibraryItem> }>("run_library_tool", {
      args: ["import-doi", "--doi", doi],
      itemsPayload: null,
    }),
  buildLibraryGraph: (items: LibraryItem[]) =>
    invoke<{ ok: boolean; graph: KnowledgeGraph }>("run_library_tool", {
      args: ["build-graph"],
      itemsPayload: items,
    }),
  exportLibraryBib: (items: LibraryItem[], format: "bibtex" | "gb7714") =>
    invoke<{ ok: boolean; format: string; count: number; text: string }>("run_library_tool", {
      args: ["export-bib", "--format", format],
      itemsPayload: items,
    }),

  runLatexCopilotEdit: (opts: {
    file: string;
    instruction: string;
    selectionStart?: number;
    selectionEnd?: number;
    errorLog?: string;
    model?: string;
  }) => {
    const args = ["edit", "--file", opts.file, "--instruction", opts.instruction];
    if (opts.selectionStart && opts.selectionEnd) {
      args.push("--selection-start", String(opts.selectionStart), "--selection-end", String(opts.selectionEnd));
    }
    if (opts.errorLog) args.push("--error-log", opts.errorLog);
    if (opts.model) args.push("--model", opts.model);
    return invoke<LatexCopilotEditResult>("run_latex_copilot", { args });
  },
  runLatexCopilotTrace: (runDir: string, texFile?: string) =>
    invoke<{ ok: boolean; texFile: string; count: number; traces: CitationTraceItem[] }>("run_latex_copilot", {
      args: ["trace-citations", "--run-dir", runDir, ...(texFile ? ["--tex-file", texFile] : [])],
    }),

  listResearchMemories: () => invoke<ResearchMemory[]>("list_research_memories"),
  saveResearchMemory: (input: SaveResearchMemoryInput) =>
    invoke<ResearchMemory>("save_research_memory", { input }),
  deleteResearchMemory: (id: string) => invoke<void>("delete_research_memory", { id }),
  getResearchPreferences: () => invoke<ResearchPreferences>("get_research_preferences"),
  saveResearchPreferences: (prefs: ResearchPreferences) =>
    invoke<ResearchPreferences>("save_research_preferences", { prefs }),

  listSshHosts: () => invoke<SshHost[]>("list_ssh_hosts"),
  saveSshHost: (input: SaveSshHostInput) => invoke<SshHost>("save_ssh_host", { input }),
  deleteSshHost: (id: string) => invoke<void>("delete_ssh_host", { id }),
  testSshHost: (id: string) => invoke<SshTestResult>("test_ssh_host", { id }),
  execSshCommand: (id: string, command: string, workDir?: string) =>
    invoke<SshExecResult>("exec_ssh_command", { id, command, workDir: workDir ?? null }),
  scpTransfer: (id: string, direction: "upload" | "download", localPath: string, remotePath: string) =>
    invoke<string>("scp_transfer", { id, direction, localPath, remotePath }),

  listSkills: () => invoke<SkillEntry[]>("list_skills"),
  saveSkill: (input: SaveSkillInput) => invoke<SkillEntry>("save_skill", { input }),
  deleteSkill: (skillId: string) => invoke<void>("delete_skill", { skillId }),
  importSkillFromFile: (path: string) => invoke<SkillEntry>("import_skill_from_file", { path }),
  importSkillFromUrl: (url: string) => invoke<SkillEntry>("import_skill_from_url", { url }),

  getExportEnvironment: () => invoke<ExportEnvironmentInfo>("get_export_environment"),
  exportMarkdownDocument: (
    markdown: string,
    title: string,
    format: "docx" | "pdf" | "html",
    outputPath: string
  ) =>
    invoke<DocumentExportResult>("export_markdown_document", {
      markdown,
      title,
      format,
      outputPath,
    }),
  exportMarkdownToGoogleDrive: (
    markdown: string,
    title: string,
    format: "docx" | "pdf",
    driveDir?: string
  ) =>
    invoke<GoogleDriveExportResult>("export_markdown_to_google_drive", {
      markdown,
      title,
      format,
      driveDir: driveDir ?? null,
    }),

  pickDirectory: async (defaultPath?: string): Promise<string | null> => {
    const selected = await open({
      directory: true,
      multiple: false,
      defaultPath,
    });
    return typeof selected === "string" ? selected : null;
  },

  pickFile: async (
    filters?: Array<{ name: string; extensions: string[] }>,
    defaultPath?: string
  ): Promise<string | null> => {
    const selected = await open({
      directory: false,
      multiple: false,
      filters,
      defaultPath,
    });
    return typeof selected === "string" ? selected : null;
  },

  pickSaveFile: async (
    defaultPath?: string,
    filters?: Array<{ name: string; extensions: string[] }>
  ): Promise<string | null> => {
    const selected = await save({ defaultPath, filters });
    return typeof selected === "string" && selected ? selected : null;
  },

  onRunLog: (handler: (payload: RunLogEventPayload) => void): Promise<UnlistenFn> =>
    listen<RunLogEventPayload>("run-log", (e) => handler(e.payload)),

  onRunStatus: (
    handler: (payload: RunStatusEventPayload) => void
  ): Promise<UnlistenFn> =>
    listen<RunStatusEventPayload>("run-status", (e) => handler(e.payload)),

  onStudioEvent: (
    handler: (payload: StudioEventPayload) => void
  ): Promise<UnlistenFn> =>
    listen<StudioEventPayload>("studio-event", (e) => handler(e.payload)),
};

export function base64ToBytes(base64: string): Uint8Array<ArrayBuffer> {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

export function base64ToBlobUrl(base64: string, mimeType: string): string {
  const blob = new Blob([base64ToBytes(base64)], { type: mimeType });
  return URL.createObjectURL(blob);
}

export function guessImageMime(path: string): string {
  const lower = path.toLowerCase();
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".gif")) return "image/gif";
  return "image/png";
}

export function formatIsoShort(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}
