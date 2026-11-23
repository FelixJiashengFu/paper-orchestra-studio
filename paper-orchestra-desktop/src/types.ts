export interface AppSettings {
  paperOrchestraDir: string;
  pythonPath: string;
  outputRootDir: string;
  dbPath: string;
}

export interface UpdateAppSettingsInput {
  paperOrchestraDir: string;
  pythonPath: string;
  outputRootDir: string;
}

export interface EnvSecretsConfig {
  anthropicApiKey: string;
  anthropicBaseUrl: string;
  deepseekApiKey: string;
  deepseekBaseUrl: string;
  geminiApiKey: string;
  vertexAiProject: string;
  vertexAiLocation: string;
  openaiApiKey: string;
  openaiBaseUrl: string;
  semanticScholarApiKey: string;
  smtpEmail: string;
  smtpPassword: string;

  // ---- Scholarly search platforms (see paper-orchestra/utils/scholar_utils.py) ----
  /** S2_MIN_INTERVAL_SEC — min seconds between Semantic Scholar calls ("" = backend default 1.1) */
  s2MinIntervalSec: string;
  /** OPENALEX_EMAIL — polite pool */
  openalexEmail: string;
  /** OPENALEX_API_KEY — optional */
  openalexApiKey: string;
  /** CROSSREF_MAILTO — polite pool */
  crossrefMailto: string;
  /** CROSSREF_PLUS_TOKEN — optional */
  crossrefPlusToken: string;
  /** NCBI_API_KEY — PubMed, optional (3 -> 10 req/s) */
  ncbiApiKey: string;
  /** NCBI_EMAIL — PubMed, optional */
  ncbiEmail: string;
  /** CORE_API_KEY — required for CORE */
  coreApiKey: string;
  /** PO_SCHOLAR_SOURCES — comma list, order = priority; "" = all (backend default order) */
  scholarSources: string;

  // ---- Model routing (see paper-orchestra/utils/providers.py) ----
  /** PO_AUTO_MODEL — when set, every "auto" task uses this model ref, e.g. "qwen:qwen-max" */
  autoModel: string;
  /** PO_DEFAULT_PROVIDER — gateway for model names no rule recognises */
  defaultProvider: string;

  /**
   * Every other KEY=VALUE in .env that is not one of the fields above
   * (preset provider keys such as DASHSCOPE_API_KEY, MOONSHOT_API_KEY,
   * <NAME>_BASE_URL, <NAME>_DEFAULT_MODEL, PO_TASK_MODEL_<TASK>, custom vars...).
   * Saving writes exactly these back: a key removed from the map is removed from
   * .env, an existing key with "" stays as `KEY=`, a new key with "" is not written.
   */
  extraEnv: Record<string, string>;

  /** Raw text of providers.json in the repo root ("" if the file does not exist; saving "" deletes nothing). */
  providersJson: string;

  /**
   * Opaque fingerprint of the repo dir, .env and providers.json this config was
   * read from. save_env_config refuses to write when the files changed since
   * ("" skips the check). Send back the value from the latest get_env_config.
   */
  sourceFingerprint: string;
}

export interface EnvironmentStatus {
  paperOrchestraExists: boolean;
  /** Non-empty when the repo's external disk is unplugged. */
  offlineHint: string;
  cliScriptExists: boolean;
  pythonExists: boolean;
  pythonVersion: string;
  pdflatexAvailable: boolean;
  pdflatexVersion: string;
  bibtexAvailable: boolean;
  templates: string[];
  hasAnthropicKey: boolean;
  hasDeepseekKey: boolean;
  hasGeminiKey: boolean;
  hasVertexAi: boolean;
  hasOpenaiKey: boolean;
  hasSemanticScholarKey: boolean;
  /** Names of LLM providers that currently have credentials (builtin presets + providers.json entries). */
  configuredProviders: string[];
  /** Names declared in providers.json (configured or not). */
  customProviders: string[];
  /** "" if providers.json is absent or valid; otherwise the parse/validation error (every run fails then, and configuredProviders is empty). */
  providersJsonError: string;
  /** Scholarly sources that will actually be queried, in order (CORE only when CORE_API_KEY is set). */
  scholarSources: string[];
  /** PO_AUTO_MODEL, else providers.json auto_model ("" if neither). */
  autoModel: string;
  /** Per-task models (lower-case task id -> model): PO_TASK_MODEL_<TASK>, else providers.json task_models. */
  taskModels: Record<string, string>;
  /**
   * "provider:model" that auto falls back to when no Anthropic/DeepSeek/OpenAI/Gemini
   * key is set (first configured platform with a default model), "" if none.
   */
  autoFallbackModel: string;
  dbPath: string;
  dbSizeBytes: number;
}

export interface ProjectFigure {
  id: string;
  projectId: string;
  fileName: string;
  sourcePath: string;
  caption: string;
  sortOrder: number;
  createdAt: string;
}

export interface Project {
  id: string;
  title: string;
  templateName: string;
  writerModel: string;
  reflectionModel: string;
  plottingModel: string;
  imageModel: string;
  researchCutoff: string;
  usePlotting: boolean;
  plottingMaxCriticRounds: number;
  ideaMarkdown: string;
  experimentalLogMarkdown: string;
  createdAt: string;
  updatedAt: string;
  figures: ProjectFigure[];
}

export interface ProjectSummary {
  id: string;
  title: string;
  templateName: string;
  writerModel: string;
  usePlotting: boolean;
  updatedAt: string;
  runCount: number;
  lastRunStatus: string | null;
}

export interface SaveProjectInput {
  id: string;
  title: string;
  templateName: string;
  writerModel: string;
  reflectionModel: string;
  plottingModel: string;
  imageModel: string;
  researchCutoff: string;
  usePlotting: boolean;
  plottingMaxCriticRounds: number;
  ideaMarkdown: string;
  experimentalLogMarkdown: string;
}

export interface RunRecord {
  id: string;
  projectId: string;
  status: "running" | "succeeded" | "failed" | "cancelled" | string;
  currentStage: string;
  templateName: string;
  writerModel: string;
  reflectionModel: string;
  usePlotting: boolean;
  runDir: string;
  pdfPath: string | null;
  startedAt: string;
  finishedAt: string | null;
  exitCode: number | null;
  errorMessage: string | null;
}

export interface RunLogEntry {
  id: number;
  runId: string;
  stage: string;
  line: string;
  createdAt: string;
}

export interface RunLogEventPayload {
  runId: string;
  projectId: string;
  stage: string;
  line: string;
  createdAt: string;
}

export interface RunStatusEventPayload {
  run: RunRecord;
}

export interface RunArtifactsSummary {
  runDir: string;
  finalPdfPath: string | null;
  hasOutline: boolean;
  hasCitationMap: boolean;
  hasPlottingResults: boolean;
  hasContentWorklog: boolean;
  hasFormatWorklog: boolean;
  hasTimeBreakdown: boolean;
  latexFiles: string[];
  generatedFigurePaths: string[];
  /** usage_report.json exists (LLM token usage; older runs do not have it). */
  hasUsageReport: boolean;
  /** citation_audit.json exists (written after the final PDF; older runs do not have it). */
  hasCitationAudit: boolean;
  /** Absolute path of citation_audit.md, null when absent. */
  citationAuditMdPath: string | null;
  /** literature_agent_output/references_meta.json exists. */
  hasReferencesMeta: boolean;
  /** Run-relative folder the Overleaf export zips ("content_refinement_workdir" | "latex_writeup"), null when the run has no LaTeX yet. */
  latexExportSource: string | null;
  /** Absolute path of references_cited.bib (the library with only the cited entries), null when absent. */
  citedBibPath: string | null;
  /** Set when the Overleaf export puts only the cited entries into the archive's .bib; null ships the library as it is. */
  latexExportBib: LatexExportBib | null;
}

export interface LatexExportBib {
  /** Archive path of the .bib whose content is replaced, e.g. "references.bib". */
  file: string;
  /** Entries in the archive's .bib. */
  kept: number;
  /** Library entries left out (none of them is cited). */
  dropped: number;
}

// ---------------------------------------------------------------------------
// Python -> desktop report contracts. Keys are snake_case exactly as the
// Python side writes them (the desktop passes the JSON through untouched).
// ---------------------------------------------------------------------------

/** One LLM platform in `tools/check_providers.py --json`. */
export interface ProviderCheckResult {
  name: string;
  label: string;
  kind: "openai" | "anthropic" | "gemini" | string;
  configured: boolean;
  model: string | null;
  /** "test" when the model was chosen on the diagnostics page (测试模型), else "default". */
  model_source: string;
  /** null = not pinged (configuration only). */
  ok: boolean | null;
  /** Reachable but timed out or overloaded: shown as 暂时繁忙, not as a failure. */
  busy: boolean;
  latency_ms: number | null;
  error: string | null;
}

/** One scholarly source in `tools/check_providers.py --json --scholar`. */
export interface ScholarCheckResult {
  name: string;
  ok: boolean | null;
  /** A "come back later" answer (HTTP 202/408/429/5xx): 暂时繁忙, retried during runs. */
  busy: boolean;
  latency_ms: number | null;
  hits: number | null;
  error: string | null;
}

/** One provider's model list (`check_providers.py --list-models`). */
export interface ModelListResult {
  name: string;
  label: string;
  kind: string;
  ok: boolean | null;
  error: string | null;
  /** Refs a model field accepts: "claude-opus-5-5" or "qwen:qwen4-max". */
  models: string[];
}

/** Result of the `check_connections` command. */
export interface ConnectionCheckReport {
  providers: ProviderCheckResult[];
  scholar: ScholarCheckResult[];
  /** Only when called with listModels. */
  models?: ModelListResult[];
}

export interface UsageCounts {
  calls: number;
  errors: number;
  input_tokens: number;
  output_tokens: number;
}

/** List-price estimate of one by_model row (utils/pricing.py). */
export interface UsageRowCost {
  /** In Canadian dollars. */
  cad: number;
  /** In the platform's own currency. */
  amount: number;
  currency: string;
  /** "official" (built-in price table), "official:<maker>", "litellm" or "openrouter". */
  source: string;
  price_as_of: string;
}

/** Finer counters, present in a row only when not 0 (usage_tracker.DETAIL_KEYS). */
export const USAGE_DETAIL_KEYS = [
  "cache_read_tokens",
  "cache_write_tokens",
  "cache_write_1h_tokens",
  "image_output_tokens",
  "long_calls",
  "long_input_tokens",
  "long_output_tokens",
  "search_queries",
  "images",
] as const;
export type UsageDetailKey = (typeof USAGE_DETAIL_KEYS)[number];

export interface UsageModelRow extends UsageCounts, Partial<Record<UsageDetailKey, number>> {
  provider: string;
  model: string;
  cost?: UsageRowCost;
}

/** The report's "cost" summary: everything converted to CAD at list price. */
export interface UsageCost {
  currency: string;
  total: number;
  by_currency: Record<string, number>;
  /** rates: CAD per unit, e.g. {USD: 1.43}; source "bank_of_canada" or "builtin". */
  fx: { rates: Record<string, number>; date: string; source: string };
  prices: { builtin_checked: string; feeds_fetched: Record<string, string>; sources: string[] };
  unpriced_models: string[];
  basis: string;
}

/** <run dir>/usage_report.json */
export interface UsageReport {
  generated_at: string;
  totals: UsageCounts;
  by_model: UsageModelRow[];
  /** Absent in reports written before the cost estimate existed. */
  cost?: UsageCost;
}

export type CitationStatus = "verified" | "metadata_only" | "unverified";

export interface CitationAuditEntry {
  key: string;
  title: string;
  year: number | null;
  doi: string | null;
  url: string | null;
  source: string | null;
  cited: boolean;
  status: CitationStatus;
}

export interface CitationAuditSummary {
  /** Distinct keys the text cites. */
  cited: number;
  /** Entries in the library (.bib): everything the literature search kept. */
  bib_entries: number;
  /** Library entries the text cites: what the paper's reference list prints. */
  in_bibliography: number;
  missing_in_bib: number;
  uncited: number;
  with_doi: number;
  verified: number;
}

/** <run dir>/citation_audit.json */
export interface CitationAudit {
  generated_at: string;
  /** Relative to the run dir. */
  tex_file: string;
  bib_file: string;
  /** "references_cited.bib" when the audit wrote the cited-only copy of the library, else "". */
  cited_bib_file: string;
  summary: CitationAuditSummary;
  missing_in_bib: string[];
  uncited: string[];
  entries: CitationAuditEntry[];
}

export type NavTab = "workspace" | "studio" | "library" | "runs" | "artifacts" | "settings";

export const PIPELINE_STAGES = [
  { id: "outline", label: "Outline 大纲" },
  { id: "literature", label: "Literature 文献" },
  { id: "plotting", label: "Plotting 绘图" },
  { id: "writing", label: "Writing 正文" },
  { id: "refinement", label: "Refinement 润色" },
] as const;

/**
 * Static suggestions, newest first per provider, as the providers' own model
 * lists showed them on 2026-11-18. "从各家 API 拉取最新模型" in Settings adds
 * whatever each configured provider lists now (modelCatalog.ts); any ref can
 * also be typed by hand.
 */
export const MODEL_PRESETS = [
  "auto",
  "claude-opus-5-5",
  "claude-sonnet-5-5",
  "claude-fable-5-2",
  "claude-haiku-5-5",
  "gemini-4-argon",
  "gemini-3.8-flash",
  "gemini-3.5-flash-lite",
  "gemini-3.1-pro-preview",
  "gemini-pro-latest",
  "gemini-flash-latest",
  "gpt-6.2-sol",
  "gpt-6.2-astra",
  "gpt-6.1-terra",
  "gpt-6-luna",
  "deepseek-flash",
  "deepseek-v4-pro",
  // Older models that still answer (kept so saved projects show familiar names).
  "claude-sonnet-4-6",
  "claude-opus-4-6",
  "gemini-3-flash-preview",
  "gpt-4.1",
  // "provider:model" examples (see utils/providers.py); any ref is accepted.
  "qwen:qwen4-max",
  "qwen:qwen-max",
  "zhipu:glm-5.3",
  "moonshot:kimi-k3.1-pro",
  "hunyuan:hy4",
  "hunyuan:hy3",
  "qianfan:ernie-5.1",
  "qianfan:ernie-5.5-flash-preview",
  "antdigital:ling-3.1-flash",
  "longcat:LongCat-2.5",
  "openrouter:anthropic/claude-sonnet-5.5",
  "ollama:qwen3.8",
  // Local Ollama model example (Google Gemma 4, 12B, 4-bit QAT); "从各家 API 拉取最新模型" lists every local tag too.
  "ollama:gemma4:12b-it-qat",
];

/** Display names of the bundled LaTeX templates (folder names under templates/). */
const TEMPLATE_LABELS: Record<string, string> = {
  cvpr2025: "CVPR 2025",
  iclr2025: "ICLR 2025",
  neurips2026: "NeurIPS 2026",
  icml2026: "ICML 2026",
  aaai2027: "AAAI 2027",
  acl: "ACL / ARR",
  "ieee-conference": "IEEE 会议",
  "acm-sigconf": "ACM 会议 (sigconf)",
  "journal-acs-jctc-jpc-jcim": "ACS · JCTC / JPC / JCIM",
  "journal-aip-jcp": "AIP · J. Chem. Phys.",
  "journal-rsc-pccp": "RSC · PCCP",
  "journal-elsevier-commatsci": "Elsevier · Comput. Mater. Sci.",
  "journal-npj-compmater": "npj Computational Materials",
  "journal-nature": "Nature · Flagship Article",
  "journal-nature-methods": "Nature Methods",
  "journal-nature-biotech": "Nature Biotechnology",
  "journal-chinese-gb7714": "中文期刊 · GB/T 7714 (计算机学报风格)",
  "math-modeling-mcm": "数学建模 · 美赛 MCM/ICM",
  "math-modeling-cumcm": "数学建模 · 国赛 CUMCM / 电工杯",
};

/** Journal templates carry a venue.json and live in folders named journal-*. */
export function isJournalTemplate(name: string): boolean {
  return name.startsWith("journal-");
}

export function templateLabel(name: string): string {
  return TEMPLATE_LABELS[name] ?? name;
}

/** Image models for figures (Gemini), newest first. */
export const IMAGE_MODEL_PRESETS = [
  "gemini-3-pro-image",
  "gemini-nano-banana-2.1",
  "gemini-3.1-flash-image",
];

/** tools/parse_intake_form.py result (「导入填写单」). Only the fields found are present. */
export interface IntakeFormFields {
  title?: string;
  template?: string;
  researchCutoff?: string;
  usePlotting?: boolean;
  plottingMaxCriticRounds?: number;
  ideaMarkdown?: string;
  experimentalLogMarkdown?: string;
}

export interface IntakeFormResult {
  fields: IntakeFormFields;
  layout: "markers" | "pdf-form";
  warnings: string[];
}

/**
 * The template id a form names: an id, a display name, or (looser) the one
 * template whose name shares a distinctive word with it ("JCTC", "CVPR").
 */
export function matchTemplate(raw: string, templates: string[]): string | null {
  const norm = (s: string) => s.toLowerCase().replace(/[\s·.,:;/()（）_-]+/g, " ").trim();
  const want = norm(raw);
  if (!want) return null;
  for (const t of templates) if (norm(t) === want || norm(templateLabel(t)) === want) return t;
  // Looser: every distinctive word of the form (not years, not generic words)
  // must appear in exactly one template's id or name ("JCTC", "CVPR 2025 template").
  const generic = new Set(["template", "journal", "conference", "latex", "模板", "期刊", "会议"]);
  const words = want.split(" ").filter((w) => w.length >= 2 && !/^\d+$/.test(w) && !generic.has(w));
  if (words.length === 0) return null;
  const hits = templates.filter((t) => {
    const tw = new Set(`${norm(t)} ${norm(templateLabel(t))}`.split(" "));
    return words.every((w) => tw.has(w));
  });
  return hits.length === 1 ? hits[0] : null;
}

// ---------------------------------------------------------------------------
// 「编辑并实时预览」(live_edit_* commands). Every `file` is relative to the
// run's export source folder (sourceDir) and uses "/" separators.
// ---------------------------------------------------------------------------

/** live_edit_open: the run's LaTeX source folder as the editor sees it. */
export interface LiveEditSession {
  runDir: string;
  /** Absolute path of the export source folder. */
  sourceDir: string;
  /** Run-relative name of that folder, e.g. "content_refinement_workdir". */
  sourceRel: string;
  /** The document latexmk builds (also files[0]). */
  mainFile: string;
  /** Every .tex and .bib under sourceDir: mainFile first, then sorted. */
  files: string[];
  /** Files that already have a backup copy (edited here before): 恢复原稿 works for them. */
  backedUp: string[];
  /** The run is still running or queued: the editor must stay read-only. */
  runActive: boolean;
}

export interface LiveEditFile {
  file: string;
  /** "" in the reply to live_edit_save (keeps the IPC small). */
  content: string;
  mtimeMs: number;
  /** <run>/.edit_backup/<sourceRel>/<file> exists after the call: 恢复原稿 works for it. */
  hasBackup: boolean;
}

// Save errors that trying again cannot fix (exactly ERR_CONFLICT / ERR_RUN_ACTIVE
// in live_edit.rs). Any other save error is treated as passing.
export const LIVE_EDIT_ERR_CONFLICT =
  "这个文件在别处被改过（比如用别的编辑器）。请关掉编辑页重新打开，免得互相覆盖。";
export const LIVE_EDIT_ERR_RUN_ACTIVE = "论文还在生成中，现在只能看，不能改。等运行结束后再编辑。";

export interface LiveCompileIssue {
  file: string | null;
  line: number | null;
  message: string;
  /** Errors come first; warnings carry no file/line and start with 「警告」. */
  severity?: "error" | "warning";
}

/** Issues that are real errors (not warnings). */
export function compileErrors(issues: LiveCompileIssue[]): LiveCompileIssue[] {
  return issues.filter((i) => i.severity !== "warning");
}

export interface LiveCompileResult {
  /** A PDF newer than the start of this compile exists. */
  ok: boolean;
  /** Absolute path of the preview PDF, if any PDF exists (it may be an older one when ok is false). */
  pdfPath: string | null;
  /** Errors first, at most 50. */
  issues: LiveCompileIssue[];
  warnings: number;
  durationMs: number;
  /** Last ~40 lines of the LaTeX log. */
  logTail: string;
}

/** SyncTeX code -> PDF: PDF points from the page's top-left; v is the baseline (the box spans v-height..v). */
export interface SyncBox {
  /** 1-based. */
  page: number;
  h: number;
  v: number;
  width: number;
  height: number;
}

/** SyncTeX PDF -> code. */
export interface SyncSource {
  file: string;
  line: number;
}

// ---------------------------------------------------------------------------
// ClawsGO Feature Suite: Figure Bundles, Research SOPs, Library & Graph,
// Pipeline State DAG, LaTeX Copilot, Lean 4 Audit, Memories, SSH/GPU, Skills
// ---------------------------------------------------------------------------

export interface FigureBbox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FigureVersion {
  version: string;
  created_at: string;
  instruction: string;
  bbox: FigureBbox | null;
  image_rel: string;
  svg_rel?: string | null;
  code_rel?: string | null;
  description_rel?: string | null;
  abs_image_path: string;
  abs_svg_path?: string;
  code: string;
  description: string;
}

export interface FigureBundle {
  figureId: string;
  title: string;
  taskName: string;
  caption: string;
  aspectRatio: string;
  activeVersion: string;
  targetRelPath: string;
  targetImagePath: string;
  activeImagePath: string;
  activeSvgPath?: string;
  bundleDir: string;
  versions: FigureVersion[];
}

export interface FigureStudioListResult {
  ok: boolean;
  dir: string;
  bundles: FigureBundle[];
  standalone: Array<{ name: string; path: string; relDir: string }>;
}

export interface DataColumnProfile {
  name: string;
  dtype: string;
  missing: number;
  missingPct: number;
  unique: number;
  mean: number | null;
  std: number | null;
  min: number | null;
  max: number | null;
  sampleValues: string[];
}

export interface DataProfile {
  ok: boolean;
  file: string;
  fileName: string;
  rows: number;
  cols: number;
  duplicateRows: number;
  columns: DataColumnProfile[];
  previewRows: Array<Record<string, unknown>>;
  qualityWarnings: string[];
}

export interface WorkspaceHandoff {
  title: string;
  recommendedTemplate: string;
  ideaMarkdown: string;
  experimentalLogMarkdown: string;
  figurePaths: string[];
}

export interface DataAnalysisResult {
  ok: boolean;
  outDir: string;
  dataDictionary: DataProfile;
  cleaningLog: Array<Record<string, unknown>>;
  statsResults: Record<string, unknown>;
  figurePaths: string[];
  bundles: FigureBundle[];
  reportMarkdown: string;
  reportPath: string;
  scriptPath: string;
  attempts: number;
  workspaceHandoff: WorkspaceHandoff;
  usageReport?: UsageReport | null;
}

export type SopKind =
  | "paper_reading"
  | "lit_review"
  | "topic_ideation"
  | "reproduction"
  | "math_modeling";

export interface SopWorkflowResult {
  ok: boolean;
  sop: SopKind;
  outDir: string;
  reportMarkdown: string;
  reportPath: string;
  figurePaths: string[];
  bundles: FigureBundle[];
  artifacts: Record<string, unknown>;
  workspaceHandoff: WorkspaceHandoff;
  usageReport?: UsageReport | null;
}

export interface LibraryClaim {
  claim: string;
  evidence: string;
  location: string;
}

export interface LibraryItem {
  id: string;
  projectId: string | null;
  folder: string;
  title: string;
  authors: string;
  year: number | null;
  venue: string;
  doi: string;
  arxivId: string;
  url: string;
  pdfPath: string;
  bibtex: string;
  gb7714: string;
  abstractText: string;
  aiNotes: string;
  keyClaims: LibraryClaim[];
  tags: string[];
  readStatus: "full_read" | "abstract_only" | "unread" | string;
  createdAt: string;
  updatedAt: string;
}

export interface SaveLibraryItemInput {
  id?: string | null;
  projectId?: string | null;
  folder: string;
  title: string;
  authors: string;
  year?: number | null;
  venue: string;
  doi: string;
  arxivId: string;
  url: string;
  pdfPath: string;
  bibtex: string;
  gb7714: string;
  abstractText: string;
  aiNotes: string;
  keyClaims: LibraryClaim[];
  tags: string[];
  readStatus: string;
}

export interface KnowledgeGraphNode {
  id: string;
  label: string;
  fullTitle?: string;
  kind: "paper" | "concept" | "method" | string;
  year?: number | null;
  role?: "survey" | "seminal" | "recent" | "concept" | string;
  itemId?: string;
}

export interface KnowledgeGraphEdge {
  source: string;
  target: string;
  relation: string;
}

export interface KnowledgeGraph {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  readingOrder: Array<{
    step: number;
    itemId: string;
    title: string;
    year: number | null;
    stage: string;
    purpose: string;
  }>;
  comparisonMatrix: {
    columns: string[];
    rows: Array<Record<string, string>>;
  };
  generatedBy?: string;
}

export interface ResearchMemory {
  id: string;
  category: string;
  title: string;
  content: string;
  enabled: boolean;
  updatedAt: string;
}

export interface SaveResearchMemoryInput {
  id?: string | null;
  category: string;
  title: string;
  content: string;
  enabled: boolean;
}

export interface ResearchPreferences {
  outputLanguage: string;
  citationStyle: string;
  deliveryFormat: string;
  customRules: string;
}

export interface SshHost {
  id: string;
  alias: string;
  host: string;
  port: number;
  username: string;
  authType: string;
  keyPath: string;
  remoteWorkDir: string;
  notes: string;
  lastStatus: string;
  lastGpuInfo: string;
  createdAt: string;
  updatedAt: string;
}

export interface SaveSshHostInput {
  id?: string | null;
  alias: string;
  host: string;
  port: number;
  username: string;
  authType: string;
  keyPath: string;
  privateKeyPem?: string | null;
  remoteWorkDir: string;
  notes: string;
}

export interface SshGpuInfo {
  name: string;
  utilizationPct: number;
  memUsedMb: number;
  memTotalMb: number;
}

export interface SshTestResult {
  ok: boolean;
  systemInfo: string;
  gpus: SshGpuInfo[];
  rawOutput: string;
  error: string | null;
}

export interface SshExecResult {
  ok: boolean;
  exitCode: number | null;
  stdout: string;
  stderr: string;
  durationMs: number;
}

export interface SkillEntry {
  id: string;
  name: string;
  description: string;
  category: string;
  content: string;
  builtin: boolean;
  updatedAt: string;
}

export interface SaveSkillInput {
  id?: string | null;
  name: string;
  description: string;
  category: string;
  content: string;
}

export interface PipelineStateNode {
  id: string;
  label: string;
  depends_on: string[];
  status: "pending" | "running" | "completed" | "skipped" | "failed" | string;
  model: string;
  started_at: string | null;
  finished_at: string | null;
  artifact: string;
  error: string | null;
  note: string | null;
}

export interface PipelineIssue {
  timestamp: string;
  stage: string;
  severity: string;
  message: string;
}

export interface PipelineState {
  version: number;
  resumed: boolean;
  updated_at: string;
  nodes: PipelineStateNode[];
  issues: PipelineIssue[];
}

export interface CitationTraceItem {
  key: string;
  line: number;
  claimExcerpt: string;
  title: string;
  authors: string;
  year: string | number;
  venue: string;
  doi: string;
  url: string;
  status: string;
  sourceEvidence: string;
}

export interface LatexCopilotEditResult {
  ok: boolean;
  file: string;
  revisedContent: string;
  diff: string;
  summary: string;
  changedLines: number;
}

export interface LeanAuditReport {
  kind: "lean-audit";
  projectDir: string;
  lakeAvailable: boolean;
  hasLakefile: boolean;
  buildRan: boolean;
  buildOk: boolean | null;
  zeroHoles: boolean;
  leanFiles: number;
  totalLines: number;
  sorryCount: number;
  admitCount: number;
  axiomCount: number;
  unsafeCount: number;
  issues: Array<{ kind: string; file: string; line: number; excerpt: string }>;
  buildTail: string;
}

export interface StudioEventPayload {
  jobId: string;
  kind: "started" | "progress" | "usage" | "log" | "done" | "error" | "cancelled" | string;
  stage?: string | null;
  status?: string | null;
  message?: string | null;
  model?: string | null;
  detail?: string | null;
  estInputTokens?: number | null;
  outDir?: string | null;
  usage?: Record<string, unknown> | null;
  line?: string | null;
  level?: string | null;
  timestamp: string;
}

export interface StudioStepItem {
  stage: string;
  status: "running" | "completed" | "error" | string;
  message: string;
  model?: string;
  detail?: string;
  timestamp: string;
}

export interface StudioLogItem {
  id: number;
  time: string;
  level: string;
  text: string;
}

export interface StudioJobTelemetry {
  jobId: string;
  title: string;
  status: "running" | "completed" | "error" | "cancelled";
  startedAtMs: number;
  finishedAtMs?: number;
  outDir?: string;
  activeModel?: string;
  estInputTokens: number;
  currentMessage: string;
  errorMessage?: string;
  steps: StudioStepItem[];
  logs: StudioLogItem[];
  usage: UsageReport | null;
}

export interface ExportEnvironmentInfo {
  ok: boolean;
  available: boolean;
  driveRoot: string;
  exportDir: string;
  defaultPdfDir: string;
  pandocAvailable: boolean;
  chromeAvailable: boolean;
}

export interface DocumentExportResult {
  ok: boolean;
  path: string;
  format: "docx" | "pdf" | "html" | string;
  engine: string;
  bytes: number;
}

export interface GoogleDriveExportResult {
  ok: boolean;
  path: string;
  driveRoot: string;
  fileName: string;
  format: "docx" | "pdf" | string;
  engine: string;
  bytes: number;
  searchUrl: string;
}
