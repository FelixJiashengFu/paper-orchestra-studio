/**
 * Static catalog of the LLM providers and scholarly search sources that the
 * Python pipeline knows about. Mirrors:
 *   - paper-orchestra/utils/providers.py   (_builtin_providers, _NAME_RULES)
 *   - paper-orchestra/utils/scholar_utils.py (SOURCES, _DEFAULT_ORDER, _LIMITERS)
 *
 * Keep this file in sync with those modules when presets change. Example
 * model refs are illustrations only: real model names change often, so the UI
 * always labels them as examples.
 */
import type { EnvSecretsConfig } from "./types";

export type ProviderKind = "openai" | "anthropic" | "gemini";
export type ProviderRegion = "cn" | "global" | "local";

export const REGION_LABELS: Record<ProviderRegion, string> = {
  cn: "国内",
  global: "海外",
  local: "本地",
};

/** `_env_prefix` in providers.py: non-alphanumerics become "_", then upper-case. */
export function envPrefix(name: string): string {
  return name.replace(/[^A-Za-z0-9]/g, "_").toUpperCase();
}

// ---------------------------------------------------------------------------
// The four original backends (dedicated EnvSecretsConfig fields)
// ---------------------------------------------------------------------------

type EnvField = keyof Omit<EnvSecretsConfig, "extraEnv">;

export interface OfficialProvider {
  id: "anthropic" | "openai" | "gemini" | "deepseek";
  label: string;
  region: ProviderRegion;
  kind: ProviderKind;
  aliases: string[];
  /** EnvSecretsConfig field + .env name of the API key. */
  keyField: EnvField;
  keyEnv: string;
  /** Other env names the backend also accepts for the key (stored in extraEnv). */
  altKeyEnvs: string[];
  keyPlaceholder: string;
  /** Base URL override: a dedicated field, or (Gemini) an extraEnv key. */
  baseUrlField?: EnvField;
  baseUrlEnv: string;
  defaultBaseUrl: string;
  /** false = text-only (providers.py `vision=False`); null = detected at runtime. */
  vision: boolean | null;
  /** Models that take images although `vision` is false (providers.py `vision_models`). */
  visionModels?: string[];
  /** Illustrative model ref, already accepted without a prefix. */
  exampleRef: string;
  note?: string;
}

export const OFFICIAL_PROVIDERS: OfficialProvider[] = [
  {
    id: "anthropic",
    label: "Anthropic · Claude",
    region: "global",
    kind: "anthropic",
    aliases: ["claude"],
    keyField: "anthropicApiKey",
    keyEnv: "ANTHROPIC_API_KEY",
    altKeyEnvs: [],
    keyPlaceholder: "sk-ant-…",
    baseUrlField: "anthropicBaseUrl",
    baseUrlEnv: "ANTHROPIC_BASE_URL",
    defaultBaseUrl: "https://api.anthropic.com",
    vision: true,
    exampleRef: "claude-sonnet-5-5",
  },
  {
    id: "openai",
    label: "OpenAI",
    region: "global",
    kind: "openai",
    aliases: ["oai"],
    keyField: "openaiApiKey",
    keyEnv: "OPENAI_API_KEY",
    altKeyEnvs: [],
    keyPlaceholder: "sk-…",
    baseUrlField: "openaiBaseUrl",
    baseUrlEnv: "OPENAI_BASE_URL",
    defaultBaseUrl: "https://api.openai.com/v1",
    vision: null,
    exampleRef: "gpt-6.2-sol",
    note: "填写 Base URL 后也可作为 OpenAI 兼容中转使用。",
  },
  {
    id: "gemini",
    label: "Google · Gemini / Vertex AI",
    region: "global",
    kind: "gemini",
    aliases: ["google", "vertex"],
    keyField: "geminiApiKey",
    keyEnv: "GEMINI_API_KEY",
    altKeyEnvs: [],
    keyPlaceholder: "AIza…",
    baseUrlEnv: "GEMINI_BASE_URL",
    defaultBaseUrl: "",
    vision: true,
    exampleRef: "gemini-4-argon",
    note: "同时填写 Vertex 项目与区域时优先使用 Vertex AI。",
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    region: "cn",
    kind: "openai",
    aliases: [],
    keyField: "deepseekApiKey",
    keyEnv: "DEEPSEEK_API_KEY",
    altKeyEnvs: [],
    keyPlaceholder: "sk-…",
    baseUrlField: "deepseekBaseUrl",
    baseUrlEnv: "DEEPSEEK_BASE_URL",
    defaultBaseUrl: "https://api.deepseek.com",
    vision: false,
    visionModels: ["deepseek-flash", "deepseek-v4-flash-vision-exp"],
    exampleRef: "deepseek-flash",
    note: "deepseek-flash 能看图，也能联网搜索文献；其它 DeepSeek 模型按纯文本处理，运行时会省略图片输入。",
  },
];

export const LEGACY_PROVIDER_IDS = OFFICIAL_PROVIDERS.map((p) => p.id) as string[];

// ---------------------------------------------------------------------------
// OpenAI-compatible presets (everything lives in extraEnv)
// ---------------------------------------------------------------------------

export interface PresetProvider {
  id: string;
  label: string;
  region: ProviderRegion;
  /** One short line on what the platform is. */
  blurb: string;
  aliases: string[];
  /** The key env the card writes, plus the alternates the backend also reads. */
  keyEnv: string;
  altKeyEnvs: string[];
  /** `<NAME>_BASE_URL` override and the built-in default endpoint. */
  baseUrlEnv: string;
  defaultBaseUrl: string;
  /** `<NAME>_DEFAULT_MODEL`: used when `auto` falls back to this platform. */
  defaultModelEnv: string;
  /** Built-in default model (`default_model` in providers.py), "" when model ids are account-specific. */
  defaultModel: string;
  /** Local endpoints work without a key; they count as configured once the URL is set. */
  keyOptional: boolean;
  /**
   * Example ref for hints and the model datalist. When `examplePlaceholder`
   * is true the model part is account-specific and only the prefix is offered.
   */
  exampleRef: string;
  examplePlaceholder?: boolean;
  /** Mirrors `vision=False` in providers.py (text-only models). */
  vision?: false;
}

function preset(
  id: string,
  label: string,
  region: ProviderRegion,
  blurb: string,
  keyEnvs: string[],
  defaultBaseUrl: string,
  exampleRef: string,
  opts: {
    aliases?: string[];
    keyOptional?: boolean;
    examplePlaceholder?: boolean;
    /** Mirrors `default_model` in providers.py `_builtin_providers()`. */
    defaultModel?: string;
    vision?: false;
  } = {}
): PresetProvider {
  const prefix = envPrefix(id);
  return {
    id,
    label,
    region,
    blurb,
    aliases: opts.aliases ?? [],
    keyEnv: keyEnvs[0],
    altKeyEnvs: keyEnvs.slice(1),
    baseUrlEnv: `${prefix}_BASE_URL`,
    defaultBaseUrl,
    defaultModelEnv: `${prefix}_DEFAULT_MODEL`,
    defaultModel: opts.defaultModel ?? "",
    keyOptional: Boolean(opts.keyOptional),
    exampleRef,
    examplePlaceholder: opts.examplePlaceholder,
    vision: opts.vision,
  };
}

export const PRESET_PROVIDERS: PresetProvider[] = [
  preset(
    "qwen",
    "通义千问 · DashScope",
    "cn",
    "阿里云百炼，OpenAI 兼容模式",
    ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "qwen:qwen-max",
    { aliases: ["dashscope", "bailian", "aliyun"], defaultModel: "qwen-max" }
  ),
  preset(
    "moonshot",
    "Kimi · 月之暗面",
    "cn",
    "Moonshot 开放平台",
    ["MOONSHOT_API_KEY", "KIMI_API_KEY"],
    "https://api.moonshot.cn/v1",
    "moonshot:kimi-k3.1-pro",
    { aliases: ["kimi"], defaultModel: "kimi-k3.1-pro" }
  ),
  preset(
    "zhipu",
    "智谱 GLM · BigModel",
    "cn",
    "智谱开放平台",
    ["ZHIPU_API_KEY", "ZHIPUAI_API_KEY", "GLM_API_KEY"],
    "https://open.bigmodel.cn/api/paas/v4",
    "zhipu:glm-4-plus",
    { aliases: ["glm", "bigmodel"], defaultModel: "glm-4-plus" }
  ),
  preset(
    "hunyuan",
    "腾讯混元 HY · TokenHub",
    "cn",
    "腾讯云 TokenHub（原混元平台），Key 在 TokenHub 控制台创建",
    ["HUNYUAN_API_KEY", "TOKENHUB_API_KEY"],
    "https://tokenhub.tencentmaas.com/v1",
    "hunyuan:hy4",
    { aliases: ["hy", "tencent", "tokenhub"], defaultModel: "hy4" }
  ),
  preset(
    "antdigital",
    "蚂蚁数科 Ling · DTMaaS",
    "cn",
    "蚂蚁数科自己的模型服务平台（maas.antdigital.com，支付宝账号登录）。百灵平台的 Key 把接口地址改成 https://api.ant-ling.com/v1",
    ["ANTDIGITAL_API_KEY", "DTMAAS_API_KEY"],
    "https://maas-api.antdigital.com/v1",
    "antdigital:ling-3.1-flash",
    { aliases: ["ant", "ling", "dtmaas"] }
  ),
  preset(
    "longcat",
    "美团 LongCat",
    "cn",
    "美团 LongCat 开放平台（longcat.chat），Key 在「API Keys」页面创建",
    ["LONGCAT_API_KEY"],
    "https://api.longcat.chat/openai",
    "longcat:LongCat-2.5",
    { aliases: ["meituan"] }
  ),
  preset(
    "qianfan",
    "百度文心 ERNIE · 千帆",
    "cn",
    "百度智能云千帆，OpenAI 兼容 v2 接口（bce-v3 开头的 API Key）",
    ["QIANFAN_API_KEY", "ERNIE_API_KEY"],
    "https://qianfan.baidubce.com/v2",
    "qianfan:ernie-5.1",
    { aliases: ["ernie", "baidu", "wenxin"], defaultModel: "ernie-5.1" }
  ),
  preset(
    "siliconflow",
    "硅基流动 · SiliconFlow",
    "cn",
    "聚合多家开源模型",
    ["SILICONFLOW_API_KEY"],
    "https://api.siliconflow.cn/v1",
    "siliconflow:deepseek-ai/DeepSeek-V3",
    { aliases: ["sf"], defaultModel: "deepseek-ai/DeepSeek-V3" }
  ),
  preset(
    "openrouter",
    "OpenRouter",
    "global",
    "聚合平台，一个 Key 调用多家模型",
    ["OPENROUTER_API_KEY"],
    "https://openrouter.ai/api/v1",
    "openrouter:anthropic/claude-sonnet-5.5",
    { defaultModel: "anthropic/claude-sonnet-5.5" }
  ),
  preset(
    "xai",
    "xAI · Grok",
    "global",
    "xAI 官方接口",
    ["XAI_API_KEY", "GROK_API_KEY"],
    "https://api.x.ai/v1",
    "xai:<模型名>",
    { aliases: ["grok"], examplePlaceholder: true }
  ),
  preset(
    "groq",
    "Groq",
    "global",
    "低延迟推理服务",
    ["GROQ_API_KEY"],
    "https://api.groq.com/openai/v1",
    "groq:llama-3.3-70b-versatile",
    { defaultModel: "llama-3.3-70b-versatile" }
  ),
  preset(
    "mistral",
    "Mistral AI",
    "global",
    "Mistral 官方接口",
    ["MISTRAL_API_KEY"],
    "https://api.mistral.ai/v1",
    "mistral:mistral-large-latest",
    { defaultModel: "mistral-large-latest" }
  ),
  preset(
    "together",
    "Together AI",
    "global",
    "开源模型托管平台",
    ["TOGETHER_API_KEY"],
    "https://api.together.xyz/v1",
    "together:<组织/模型名>",
    { examplePlaceholder: true }
  ),
  preset(
    "ollama",
    "Ollama",
    "local",
    "本机运行的开源模型",
    ["OLLAMA_API_KEY"],
    "http://localhost:11434/v1",
    "ollama:qwen2.5:14b",
    { keyOptional: true }
  ),
  preset(
    "lmstudio",
    "LM Studio",
    "local",
    "本机 LM Studio 服务",
    ["LMSTUDIO_API_KEY"],
    "http://localhost:1234/v1",
    "lmstudio:<已加载的模型>",
    { keyOptional: true, examplePlaceholder: true }
  ),
  preset(
    "vllm",
    "vLLM",
    "local",
    "自建 vLLM 推理服务",
    ["VLLM_API_KEY"],
    "http://localhost:8000/v1",
    "vllm:<served-model-name>",
    { keyOptional: true, examplePlaceholder: true }
  ),
];

/** All builtin provider ids, in the backend's listing order. */
export const BUILTIN_PROVIDER_IDS: string[] = [
  "openai",
  "anthropic",
  "deepseek",
  "gemini",
  ...PRESET_PROVIDERS.map((p) => p.id),
];

const ALIAS_TO_ID: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const p of [...OFFICIAL_PROVIDERS, ...PRESET_PROVIDERS]) {
    map[p.id] = p.id;
    for (const a of p.aliases) map[a] = p.id;
  }
  return map;
})();

/** Resolves a builtin provider id or alias ("kimi" -> "moonshot"); undefined if unknown. */
export function canonicalProviderId(name: string): string | undefined {
  return ALIAS_TO_ID[name.trim().toLowerCase()];
}

export function providerLabel(id: string): string {
  const canon = canonicalProviderId(id);
  const hit =
    OFFICIAL_PROVIDERS.find((p) => p.id === canon) ?? PRESET_PROVIDERS.find((p) => p.id === canon);
  return hit ? hit.label : id;
}

/**
 * The prefix of a "provider:model" ref, mirroring `split_model_ref`: only the
 * first colon splits, and only when the prefix is a known provider or alias
 * (builtin, or one of `customNames`). Returns undefined for bare names.
 */
export function splitModelRef(
  ref: string,
  customNames: string[] = []
): { provider: string; model: string } | undefined {
  const trimmed = ref.trim();
  const idx = trimmed.indexOf(":");
  if (idx <= 0) return undefined;
  const prefix = trimmed.slice(0, idx).trim().toLowerCase();
  const model = trimmed.slice(idx + 1).trim();
  if (!model) return undefined;
  const custom = customNames.find((n) => n.toLowerCase() === prefix);
  if (custom) return { provider: custom.toLowerCase(), model };
  const builtin = canonicalProviderId(prefix);
  return builtin ? { provider: builtin, model } : undefined;
}

/** Builtin providers declared `vision=False` in providers.py. */
export const TEXT_ONLY_PROVIDER_IDS: string[] = [...OFFICIAL_PROVIDERS, ...PRESET_PROVIDERS]
  .filter((p) => p.vision === false)
  .map((p) => p.id);

/**
 * True when a model ref is known to be text-only from catalog data alone:
 * an explicit prefix of a provider marked `vision: false`, or a bare name the
 * backend's name rules send to such a provider (e.g. "deepseek-*").
 * Custom providers are not judged here: their vision flag is not reported.
 */
export function isKnownTextOnlyRef(ref: string, customNames: string[] = []): boolean {
  const cleaned = ref.trim();
  if (!cleaned || cleaned.toLowerCase() === "auto") return false;
  const split = splitModelRef(cleaned, customNames);
  if (split) {
    if (customNames.some((n) => n.toLowerCase() === split.provider)) return false;
    return TEXT_ONLY_PROVIDER_IDS.includes(split.provider) && !isVisionModel(split.provider, split.model);
  }
  return /deepseek/i.test(cleaned) && !isVisionModel("deepseek", cleaned);
}

/** A model listed in a text-only platform's `visionModels` (e.g. deepseek-flash). */
function isVisionModel(provider: string, model: string): boolean {
  const entry = OFFICIAL_PROVIDERS.find((p) => p.id === provider);
  const m = model.trim().toLowerCase();
  return !!entry?.visionModels?.some((v) => v.toLowerCase() === m);
}

// ---------------------------------------------------------------------------
// Pipeline tasks (PO_TASK_MODEL_<TASK>)
// ---------------------------------------------------------------------------

/**
 * Every step that calls a model, in pipeline order (PO_TASK_MODEL_<ID>).
 * `parent`: a finer step that runs with its parent's model until pinned
 * (TASK_PARENTS in llm_backend_utils.py). literature_search and image are
 * special: a web-search model and an image model.
 */
export const PIPELINE_TASKS: Array<{ id: string; label: string; hint: string; parent?: string }> = [
  { id: "outline", label: "大纲规划", hint: "根据研究思路和实验记录排出全文大纲、要找的文献和要画的图" },
  {
    id: "literature_search",
    label: "文献联网检索",
    hint: "联网找候选论文：填 Gemini 模型（Google 搜索）或 deepseek-flash（DeepSeek 联网搜索）；填 off 不联网，只用学术库关键词检索",
  },
  { id: "literature_query", label: "文献检索词", hint: "不联网时，生成去学术库（Semantic Scholar、arXiv 等）检索的关键词" },
  { id: "relevance_screen", label: "文献相关性筛选", hint: "剔除关键词检索带进来的离题论文", parent: "literature_query" },
  { id: "literature_write", label: "文献综述", hint: "写引言和相关工作，并给其余章节分配引用" },
  { id: "plot_retrieval", label: "参考图检索", hint: "从示例图库里挑相似的图当参考", parent: "plot_code" },
  { id: "plot_plan", label: "绘图规划", hint: "把要画的图写成详细的绘图说明" },
  { id: "plot_style", label: "图表风格优化", hint: "按学术图表风格润色绘图说明", parent: "plot_code" },
  { id: "plot_code", label: "绘图代码", hint: "写 Matplotlib 代码画数据图" },
  { id: "image", label: "示意图生成", hint: "图像模型画方法示意图；留空时有 Gemini Key 用 gemini-3-pro-image，否则用代码画" },
  { id: "diagram_code", label: "示意图代码兜底", hint: "图像模型画不了时，用 Matplotlib 代码画示意图", parent: "plot_code" },
  { id: "plot_critic", label: "图表视觉评审", hint: "看着画出来的图挑毛病并改说明（需要能看图的模型）" },
  { id: "plot_caption", label: "图注撰写", hint: "为每张图写图注", parent: "plot_critic" },
  { id: "section_writing", label: "正文写作", hint: "写其余章节，并看图写对图的描述（需要能看图的模型）" },
  { id: "peer_review", label: "同行评审", hint: "模拟审稿人给全文打分、提意见" },
  { id: "content_refinement", label: "内容润色", hint: "按审稿意见修改正文" },
  { id: "format_review", label: "版式质检", hint: "看 PDF 页面截图检查版式（需要能看图的模型）" },
  { id: "format_fix", label: "版式修复", hint: "按版式问题改 LaTeX" },
  { id: "citation_parse", label: "引用解析", hint: "写论文用不到，仅评测脚本使用" },
];

export function taskModelEnv(taskId: string): string {
  return `PO_TASK_MODEL_${taskId.toUpperCase()}`;
}

// ---------------------------------------------------------------------------
// Scholarly search sources
// ---------------------------------------------------------------------------

export type ScholarSourceId =
  | "semantic_scholar"
  | "openalex"
  | "crossref"
  | "arxiv"
  | "dblp"
  | "europepmc"
  | "pubmed"
  | "core";

export interface ScholarField {
  field: EnvField;
  env: string;
  label: string;
  secret: boolean;
  placeholder?: string;
  inputMode?: "text" | "email" | "decimal";
}

export interface ScholarSource {
  id: ScholarSourceId;
  label: string;
  discipline: string;
  /** Default request pacing applied by scholar_utils' limiter. */
  rateNote: string;
  /** True when the source is skipped unless its key is set (CORE). */
  keyRequired: boolean;
  fields: ScholarField[];
}

/** `_DEFAULT_ORDER` in scholar_utils.py. */
export const SCHOLAR_DEFAULT_ORDER: ScholarSourceId[] = [
  "semantic_scholar",
  "openalex",
  "crossref",
  "arxiv",
  "dblp",
  "europepmc",
  "pubmed",
  "core",
];

export const SCHOLAR_ALIASES: Record<string, ScholarSourceId> = {
  s2: "semantic_scholar",
  semanticscholar: "semantic_scholar",
  epmc: "europepmc",
};

export const SCHOLAR_SOURCES: ScholarSource[] = [
  {
    id: "semantic_scholar",
    label: "Semantic Scholar",
    discipline: "综合 · 引用图谱",
    rateNote: "每秒最多 1 次请求，程序已自动限速",
    keyRequired: false,
    fields: [
      {
        field: "semanticScholarApiKey",
        env: "SEMANTIC_SCHOLAR_API_KEY",
        label: "API Key（可选）",
        secret: true,
      },
      {
        field: "s2MinIntervalSec",
        env: "S2_MIN_INTERVAL_SEC",
        label: "最小请求间隔（秒）",
        secret: false,
        placeholder: "默认 1.1",
        inputMode: "decimal",
      },
    ],
  },
  {
    id: "openalex",
    label: "OpenAlex",
    discipline: "全学科",
    rateNote: "约 8 次/秒；填写邮箱进入 polite pool",
    keyRequired: false,
    fields: [
      {
        field: "openalexEmail",
        env: "OPENALEX_EMAIL",
        label: "联系邮箱",
        secret: false,
        placeholder: "you@example.org",
        inputMode: "email",
      },
      { field: "openalexApiKey", env: "OPENALEX_API_KEY", label: "API Key（可选）", secret: true },
    ],
  },
  {
    id: "crossref",
    label: "Crossref",
    discipline: "全学科 · DOI 元数据",
    rateNote: "约 5 次/秒；填写邮箱进入 polite pool",
    keyRequired: false,
    fields: [
      {
        field: "crossrefMailto",
        env: "CROSSREF_MAILTO",
        label: "联系邮箱",
        secret: false,
        placeholder: "you@example.org",
        inputMode: "email",
      },
      {
        field: "crossrefPlusToken",
        env: "CROSSREF_PLUS_TOKEN",
        label: "Plus Token（可选）",
        secret: true,
      },
    ],
  },
  {
    id: "arxiv",
    label: "arXiv",
    discipline: "物理 · 数学 · 计算机预印本",
    rateNote: "每 3 秒 1 次（arXiv API 条款），无需 Key",
    keyRequired: false,
    fields: [],
  },
  {
    id: "dblp",
    label: "dblp",
    discipline: "计算机科学",
    rateNote: "每秒 1 次，无需 Key",
    keyRequired: false,
    fields: [],
  },
  {
    id: "europepmc",
    label: "Europe PMC",
    discipline: "生命科学 · 化学",
    rateNote: "约 8 次/秒，无需 Key",
    keyRequired: false,
    fields: [],
  },
  {
    id: "pubmed",
    label: "PubMed",
    discipline: "生物医学",
    rateNote: "无 Key 3 次/秒，有 Key 10 次/秒",
    keyRequired: false,
    fields: [
      { field: "ncbiApiKey", env: "NCBI_API_KEY", label: "NCBI API Key（可选）", secret: true },
      {
        field: "ncbiEmail",
        env: "NCBI_EMAIL",
        label: "联系邮箱（可选）",
        secret: false,
        placeholder: "you@example.org",
        inputMode: "email",
      },
    ],
  },
  {
    id: "core",
    label: "CORE",
    discipline: "开放获取全文",
    rateNote: "每秒 1 次；必须填写 Key 才会启用",
    keyRequired: true,
    fields: [{ field: "coreApiKey", env: "CORE_API_KEY", label: "API Key（必填）", secret: true }],
  },
];

/** Display name of a source id; merged ids from title_lookup ("dblp+openalex") name each part. */
export function scholarSourceLabel(id: string): string {
  const one = (part: string) => SCHOLAR_SOURCES.find((s) => s.id === part)?.label ?? part;
  if (!id.includes("+")) return one(id);
  return id
    .split("+")
    .filter(Boolean)
    .map(one)
    .join(" + ");
}

/** Normalises a PO_SCHOLAR_SOURCES value like `enabled_sources()` (minus the CORE key check). */
export function parseScholarSources(raw: string): ScholarSourceId[] {
  if (!raw.trim()) return [...SCHOLAR_DEFAULT_ORDER];
  const out: ScholarSourceId[] = [];
  for (const part of raw.split(",")) {
    const name = part.trim().toLowerCase();
    if (!name) continue;
    const id = (SCHOLAR_ALIASES[name] ?? name) as ScholarSourceId;
    if (SCHOLAR_DEFAULT_ORDER.includes(id) && !out.includes(id)) out.push(id);
  }
  return out;
}

/** Enabled ids in priority order -> PO_SCHOLAR_SOURCES ("" when it equals the default). */
export function serializeScholarSources(enabled: ScholarSourceId[]): string {
  const isDefault =
    enabled.length === SCHOLAR_DEFAULT_ORDER.length &&
    enabled.every((id, i) => id === SCHOLAR_DEFAULT_ORDER[i]);
  return isDefault ? "" : enabled.join(",");
}

// ---------------------------------------------------------------------------
// providers.json example and field reference
// ---------------------------------------------------------------------------

export const PROVIDERS_JSON_EXAMPLE = `{
  "providers": [
    {
      "name": "relay",
      "type": "openai",
      "base_url": "https://relay.example.com/v1",
      "api_key_env": "RELAY_API_TOKEN",
      "models": ["llama-3.3-70b-instruct"],
      "default_model": "llama-3.3-70b-instruct"
    },
    {
      "name": "claude-relay",
      "type": "anthropic",
      "base_url": "https://claude-relay.example.com",
      "api_key_env": "CLAUDE_RELAY_TOKEN",
      "default_model": "claude-sonnet-5-5",
      "vision": true,
      "drop_params": ["top_p"]
    }
  ]
}
`;

export const PROVIDERS_JSON_FIELDS: Array<{ key: string; desc: string }> = [
  { key: "name", desc: "必填。调用时作为前缀，如 relay:llama-3.3-70b-instruct" },
  { key: "type", desc: "openai（默认，含兼容接口）/ anthropic / gemini" },
  { key: "base_url", desc: "接口地址；可用 <NAME>_BASE_URL 环境变量覆盖" },
  { key: "api_key_env", desc: "读取密钥的环境变量名（字符串或数组），也会读取 <NAME>_API_KEY" },
  { key: "api_key", desc: "直接写入密钥，支持 ${VAR}；建议改用 api_key_env" },
  { key: "models", desc: "该平台提供的模型；不带前缀的同名模型会优先路由到这里" },
  { key: "default_model", desc: "auto 回退到该平台时使用的模型" },
  { key: "vision", desc: "是否支持图片输入；省略则运行时自动探测" },
  { key: "headers", desc: "附加请求头（对象）" },
  { key: "drop_params", desc: "不发送给该接口的参数，如 [\"temperature\"]" },
  { key: "key_optional", desc: "无需密钥（本地服务）" },
  { key: "aliases", desc: "其他可用的前缀名" },
];

export const PROVIDERS_JSON_TOP_LEVEL: Array<{ key: string; desc: string }> = [
  { key: "auto_model", desc: "等同 PO_AUTO_MODEL，环境变量优先" },
  { key: "task_models", desc: "按任务指定模型，如 {\"outline\": \"qwen:qwen-max\"}" },
  { key: "default_provider", desc: "等同 PO_DEFAULT_PROVIDER" },
];
