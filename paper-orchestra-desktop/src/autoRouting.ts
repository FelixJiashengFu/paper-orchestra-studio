// Mirror of paper-orchestra/utils/llm_backend_utils.py `resolve_task_model()`,
// `resolve_image_model()` and `literature_search_backend()`: which model each
// pipeline step really runs with.
//
// Precedence for a step (the same as the backend):
//   1. the workbench model of the agent that runs it, unless it is "auto" or empty
//      (older versions saved these; the workbench no longer edits them);
//   2. PO_TASK_MODEL_<TASK> (or providers.json task_models);
//   3. a finer step without its own model: whatever its parent task uses;
//   4. PO_AUTO_MODEL (or providers.json auto_model);
//   5. the built-in routing by which API keys are set.

export type RouteRole = "writer" | "reflection" | "plotting" | "image" | "search";

/**
 * Where a step's model came from. "inherit" = same as its parent task;
 * "pending" = nothing usable is configured; "fallback" = the value cannot be
 * used for this step, so the backend does something else (shown in `model`).
 */
export type RouteSource = "workbench" | "task" | "inherit" | "unified" | "auto" | "pending" | "fallback";

export interface RouteStep {
  task: string;
  label: string;
  role: RouteRole;
  /** The step sends images (figures or rendered PDF pages) to the model. */
  needsVision: boolean;
  model: string;
  source: RouteSource;
  /** For source "inherit": the parent task id. */
  parent?: string;
}

export interface RoutingInput {
  writerModel: string;
  reflectionModel: string;
  plottingModel: string;
  imageModel: string;
  usePlotting: boolean;
  /** PO_AUTO_MODEL (or providers.json auto_model), "" if unset. */
  autoModel: string;
  /** Lower-case task id -> model, from PO_TASK_MODEL_<TASK> / providers.json. */
  taskModels: Record<string, string>;
  keys: { claude: boolean; deepseek: boolean; openai: boolean; gemini: boolean };
  /** `_non_legacy_default()`: first configured platform with a default model, "" if none. */
  autoFallback: string;
  /** Some platform has credentials (decides the wording of a pending step). */
  anyProviderConfigured: boolean;
}

export const DEFAULT_IMAGE_MODEL = "gemini-3-pro-image";
/** Shown for the image step when diagrams will be drawn with Matplotlib code. */
export const MATPLOTLIB_IMAGE = "Matplotlib 代码画图";

type Key = keyof RoutingInput["keys"];

type AgentRole = "writer" | "reflection" | "plotting";

interface StepDef {
  task: string;
  label: string;
  role: AgentRole;
  needsVision: boolean;
  /** Built-in routing when no override is set: first entry whose key is configured. */
  order: Array<[Key, string]>;
}

/** Finer steps: they run with their parent's model until pinned (TASK_PARENTS). */
interface SubStepDef {
  task: string;
  label: string;
  role: AgentRole;
  needsVision: boolean;
  parent: string;
}

/** Shown for literature_search when papers are found by keyword search. */
export const SCHOLAR_SEARCH = "不联网（学术库关键词检索）";
export const GEMINI_SEARCH_MODEL = "gemini-3-flash-preview";
const SEARCH_OFF = new Set(["off", "none", "no", "false", "0", "关", "关闭", "不用"]);

// Pipeline order. `order` must match the `routing` table in resolve_task_model().
const STEPS: StepDef[] = [
  {
    task: "outline",
    label: "大纲规划",
    role: "writer",
    needsVision: false,
    order: [["deepseek", "deepseek-v4-pro"], ["claude", "claude-sonnet-5-5"], ["openai", "gpt-6.2-sol"], ["gemini", "gemini-4-argon"]],
  },
  {
    task: "literature_query",
    label: "文献检索词",
    role: "writer",
    needsVision: false,
    order: [["deepseek", "deepseek-flash"], ["claude", "claude-sonnet-5-5"], ["openai", "gpt-6.1-terra"], ["gemini", "gemini-3.8-flash"]],
  },
  {
    task: "literature_write",
    label: "文献综述",
    role: "writer",
    needsVision: false,
    order: [["claude", "claude-sonnet-5-5"], ["deepseek", "deepseek-flash"], ["openai", "gpt-6.2-sol"], ["gemini", "gemini-4-argon"]],
  },
  {
    task: "plot_plan",
    label: "绘图规划",
    role: "plotting",
    needsVision: false,
    order: [["claude", "claude-sonnet-5-5"], ["openai", "gpt-6.2-sol"], ["gemini", "gemini-4-argon"], ["deepseek", "deepseek-flash"]],
  },
  {
    task: "plot_code",
    label: "绘图代码",
    role: "plotting",
    needsVision: false,
    order: [["deepseek", "deepseek-flash"], ["claude", "claude-sonnet-5-5"], ["openai", "gpt-6.1-terra"], ["gemini", "gemini-4-argon"]],
  },
  {
    task: "plot_critic",
    label: "图表视觉评审",
    role: "plotting",
    needsVision: true,
    order: [["claude", "claude-sonnet-5-5"], ["openai", "gpt-6.2-sol"], ["gemini", "gemini-4-argon"], ["deepseek", "deepseek-flash"]],
  },
  {
    task: "section_writing",
    label: "正文写作",
    role: "writer",
    needsVision: true,
    order: [["claude", "claude-sonnet-5-5"], ["openai", "gpt-6.2-sol"], ["gemini", "gemini-4-argon"], ["deepseek", "deepseek-v4-pro"]],
  },
  {
    task: "peer_review",
    label: "同行评审",
    role: "reflection",
    needsVision: false,
    order: [["deepseek", "deepseek-v4-pro"], ["claude", "claude-sonnet-5-5"], ["openai", "gpt-6.2-sol"], ["gemini", "gemini-4-argon"]],
  },
  {
    task: "content_refinement",
    label: "内容润色",
    role: "reflection",
    needsVision: false,
    order: [["claude", "claude-sonnet-5-5"], ["openai", "gpt-6.2-sol"], ["gemini", "gemini-4-argon"], ["deepseek", "deepseek-v4-pro"]],
  },
  {
    task: "format_review",
    label: "版式截图质检",
    role: "reflection",
    needsVision: true,
    order: [["claude", "claude-sonnet-5-5"], ["openai", "gpt-6.2-sol"], ["gemini", "gemini-4-argon"], ["deepseek", "deepseek-flash"]],
  },
  {
    task: "format_fix",
    label: "版式修复",
    role: "reflection",
    needsVision: false,
    order: [["claude", "claude-sonnet-5-5"], ["deepseek", "deepseek-flash"], ["openai", "gpt-6.2-sol"], ["gemini", "gemini-4-argon"]],
  },
];

const SUB_STEPS: SubStepDef[] = [
  { task: "relevance_screen", label: "文献相关性筛选", role: "writer", needsVision: false, parent: "literature_query" },
  { task: "plot_retrieval", label: "参考图检索", role: "plotting", needsVision: false, parent: "plot_code" },
  { task: "plot_style", label: "图表风格优化", role: "plotting", needsVision: false, parent: "plot_code" },
  { task: "diagram_code", label: "示意图代码兜底", role: "plotting", needsVision: false, parent: "plot_code" },
  { task: "plot_caption", label: "图注撰写", role: "plotting", needsVision: false, parent: "plot_critic" },
];

/** Pipeline order of every step (STEPS, SUB_STEPS and the two special ones). */
const ORDER = [
  "outline", "literature_search", "literature_query", "relevance_screen", "literature_write",
  "plot_retrieval", "plot_plan", "plot_style", "plot_code", "image", "diagram_code", "plot_critic",
  "plot_caption", "section_writing", "peer_review", "content_refinement", "format_review", "format_fix",
];

/**
 * Which of the four built-in vendors the backend counts as configured. Python's
 * has_anthropic_credentials() etc. ask the provider registry, which includes a
 * providers.json entry that overrides a built-in (e.g. {"name": "anthropic", ...});
 * configuredProviders mirrors that registry, the raw .env flags do not.
 */
export function providerKeys(
  env: { configuredProviders?: string[]; hasVertexAi?: boolean } | null | undefined,
): RoutingInput["keys"] {
  const names = (env?.configuredProviders ?? []).map((n) => n.toLowerCase());
  return {
    claude: names.includes("anthropic"),
    deepseek: names.includes("deepseek"),
    openai: names.includes("openai"),
    gemini: names.includes("gemini") || !!env?.hasVertexAi,
  };
}

/** Which workbench model runs a task ("writer" / "reflection" / "plotting"), undefined for others. */
export function taskRole(task: string): AgentRole | undefined {
  return STEPS.find((s) => s.task === task)?.role ?? SUB_STEPS.find((s) => s.task === task)?.role;
}

export const ROLE_LABEL: Record<AgentRole, string> = {
  writer: "Writer",
  reflection: "Reflection",
  plotting: "Plotting",
};

export const isAutoModelRef = (ref: string) => {
  const r = (ref ?? "").trim().toLowerCase();
  return r === "" || r === "auto";
};

/** "gemini" / "deepseek" for a model ref the backend sends to that built-in platform. */
export function searchPlatform(ref: string): "gemini" | "deepseek" | "" {
  const r = ref.trim().toLowerCase();
  const colon = r.indexOf(":");
  const prefix = colon > 0 ? r.slice(0, colon) : "";
  if (["gemini", "google", "vertex"].includes(prefix)) return "gemini";
  if (prefix === "deepseek") return "deepseek";
  if (prefix) return "";
  if (r.startsWith("gemini")) return "gemini";
  if (r.includes("deepseek")) return "deepseek";
  return "";
}

/**
 * Why `can_generate_images()` would refuse an image model ("" = it can draw).
 * A bare name must be a Gemini image model with a Gemini key; "openai:..." and
 * "gemini:..." need that key. Other platforms' keys are not known here.
 */
export function imageModelProblem(ref: string, keys: RoutingInput["keys"]): string {
  const r = ref.trim().toLowerCase();
  const colon = r.indexOf(":");
  const prefix = colon > 0 ? r.slice(0, colon) : "";
  if (!prefix) {
    if (!r.includes("gemini")) return "不带平台前缀时只认 Gemini 图像模型，其它平台请写成 平台:模型名";
    return keys.gemini ? "" : "没有 Gemini Key";
  }
  if (["gemini", "google", "vertex"].includes(prefix)) return keys.gemini ? "" : "没有 Gemini Key";
  if (["openai", "oai"].includes(prefix)) return keys.openai ? "" : "没有 OpenAI Key";
  return "";
}

/** Every step the next run executes, in pipeline order, with its real model. */
export function resolveRouting(input: RoutingInput): RouteStep[] {
  const workbench: Record<AgentRole, string> = {
    writer: input.writerModel,
    reflection: input.reflectionModel,
    plotting: input.plottingModel,
  };
  // "auto" written as a model means unset (as in the backend).
  const unified = isAutoModelRef(input.autoModel) ? "" : input.autoModel.trim();
  const byTask: Record<string, RouteStep> = {};

  for (const step of STEPS) {
    const base = { task: step.task, label: step.label, role: step.role, needsVision: step.needsVision };
    const explicit = (workbench[step.role] ?? "").trim();
    const perTask = (input.taskModels[step.task] ?? "").trim();
    if (!isAutoModelRef(explicit)) byTask[step.task] = { ...base, model: explicit, source: "workbench" };
    else if (!isAutoModelRef(perTask)) byTask[step.task] = { ...base, model: perTask, source: "task" };
    else if (unified) byTask[step.task] = { ...base, model: unified, source: "unified" };
    else {
      const hit = step.order.find(([key]) => input.keys[key])?.[1];
      if (hit) byTask[step.task] = { ...base, model: hit, source: "auto" };
      else if (input.autoFallback) byTask[step.task] = { ...base, model: input.autoFallback, source: "auto" };
      else
        byTask[step.task] = {
          ...base,
          model: input.anyProviderConfigured ? "需为平台设置默认模型" : "待配置 API Key",
          source: "pending",
        };
    }
  }

  for (const step of SUB_STEPS) {
    const base = { task: step.task, label: step.label, role: step.role, needsVision: step.needsVision };
    const explicit = (workbench[step.role] ?? "").trim();
    const perTask = (input.taskModels[step.task] ?? "").trim();
    const parent = byTask[step.parent];
    if (!isAutoModelRef(explicit)) byTask[step.task] = { ...base, model: explicit, source: "workbench" };
    else if (!isAutoModelRef(perTask)) byTask[step.task] = { ...base, model: perTask, source: "task" };
    else if (parent.source === "pending") byTask[step.task] = { ...base, model: parent.model, source: "pending" };
    else byTask[step.task] = { ...base, model: parent.model, source: "inherit", parent: step.parent };
  }

  // Literature web search (literature_search_backend). The workbench Writer model never applied here.
  {
    const base = { task: "literature_search", label: "文献联网检索", role: "search" as const, needsVision: false };
    const value = (input.taskModels["literature_search"] ?? "").trim();
    if (SEARCH_OFF.has(value.toLowerCase())) {
      byTask[base.task] = { ...base, model: SCHOLAR_SEARCH, source: "task" };
    } else if (isAutoModelRef(value)) {
      byTask[base.task] = input.keys.gemini
        ? { ...base, model: GEMINI_SEARCH_MODEL, source: "auto" }
        : { ...base, model: SCHOLAR_SEARCH, source: "auto" };
    } else {
      const platform = searchPlatform(value);
      if (platform && input.keys[platform]) byTask[base.task] = { ...base, model: value, source: "task" };
      else
        byTask[base.task] = {
          ...base,
          model: platform ? `${SCHOLAR_SEARCH}：没有 ${platform === "gemini" ? "Gemini" : "DeepSeek"} Key` : `${SCHOLAR_SEARCH}：这个模型不能联网`,
          source: "fallback",
        };
    }
  }

  // Diagrams (not data plots) go to the image model; plots are drawn by the plot code.
  {
    const base = { task: "image", label: "示意图生成", role: "image" as const, needsVision: false };
    const image = (input.imageModel ?? "").trim();
    const perTask = (input.taskModels["image"] ?? "").trim();
    const chosen = !isAutoModelRef(image) ? image : !isAutoModelRef(perTask) ? perTask : "";
    if (chosen) {
      const problem = imageModelProblem(chosen, input.keys);
      byTask.image = problem
        ? { ...base, model: `${MATPLOTLIB_IMAGE}：${problem}`, source: "fallback" }
        : { ...base, model: chosen, source: !isAutoModelRef(image) ? "workbench" : "task" };
    } else if (input.keys.gemini) byTask.image = { ...base, model: DEFAULT_IMAGE_MODEL, source: "auto" };
    else byTask.image = { ...base, model: MATPLOTLIB_IMAGE, source: "auto" };
  }

  return ORDER.map((task) => byTask[task]).filter(
    (s) => input.usePlotting || (s.role !== "plotting" && s.role !== "image"),
  );
}

export const ROUTE_SOURCE_LABEL: Record<RouteSource, string> = {
  workbench: "工作台",
  task: "按任务",
  inherit: "跟随",
  unified: "统一",
  auto: "自动",
  pending: "未配置",
  fallback: "用不了",
};
