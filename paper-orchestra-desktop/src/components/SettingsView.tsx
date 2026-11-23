import {
  ArrowDown,
  ArrowUp,
  BookOpenCheck,
  Braces,
  Check,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleX,
  Eye,
  EyeOff,
  FolderSearch,
  Loader2,
  LockKeyhole,
  PlugZap,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Trash2,
  Undo2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, formatBytes } from "../api";
import {
  OFFICIAL_PROVIDERS,
  PIPELINE_TASKS,
  PRESET_PROVIDERS,
  PROVIDERS_JSON_EXAMPLE,
  PROVIDERS_JSON_FIELDS,
  PROVIDERS_JSON_TOP_LEVEL,
  REGION_LABELS,
  SCHOLAR_DEFAULT_ORDER,
  SCHOLAR_SOURCES,
  parseScholarSources,
  providerLabel,
  scholarSourceLabel,
  serializeScholarSources,
  taskModelEnv,
  type OfficialProvider,
  type PresetProvider,
  type ProviderRegion,
  type ScholarSource,
  type ScholarSourceId,
} from "../providerCatalog";
import { formatInt, formatLatency, normalizeConnectionReport } from "../runReports";
import { fetchedModelRefs, saveFetchedModels, useFetchedModels, type FetchedModels } from "../modelCatalog";
import { bareModel, loadPingModels, savePingModels, withPingModel, type PingModels } from "../pingModels";
import { maskProvidersJsonSecrets } from "../secretMask";
import {
  MODEL_PRESETS,
  type AppSettings,
  type EnvSecretsConfig,
  type EnvironmentStatus,
  type ProviderCheckResult,
  type ScholarCheckResult,
} from "../types";
import "./settings.css";

interface SettingsViewProps {
  onEnvUpdated: () => Promise<void>;
  onToast: (msg: string, isError?: boolean) => void;
}

// ---------------------------------------------------------------------------
// Static tables
// ---------------------------------------------------------------------------

const EMPTY_ENV: EnvSecretsConfig = {
  anthropicApiKey: "",
  anthropicBaseUrl: "",
  deepseekApiKey: "",
  deepseekBaseUrl: "",
  geminiApiKey: "",
  vertexAiProject: "",
  vertexAiLocation: "",
  openaiApiKey: "",
  openaiBaseUrl: "",
  semanticScholarApiKey: "",
  smtpEmail: "",
  smtpPassword: "",
  s2MinIntervalSec: "",
  openalexEmail: "",
  openalexApiKey: "",
  crossrefMailto: "",
  crossrefPlusToken: "",
  ncbiApiKey: "",
  ncbiEmail: "",
  coreApiKey: "",
  scholarSources: "",
  autoModel: "",
  defaultProvider: "",
  extraEnv: {},
  providersJson: "",
  sourceFingerprint: "",
};

type ScalarField = Exclude<keyof EnvSecretsConfig, "extraEnv" | "sourceFingerprint">;

const SCALAR_FIELDS = (Object.keys(EMPTY_ENV) as Array<keyof EnvSecretsConfig>).filter(
  (k): k is ScalarField => k !== "extraEnv" && k !== "sourceFingerprint"
);

const SETTINGS_FIELDS = ["paperOrchestraDir", "pythonPath", "outputRootDir"] as const;

/** .env keys with a dedicated input (mirrors MANAGED_KEYS in env_config.rs). */
const MANAGED_ENV: Record<string, { field: ScalarField; home: string }> = (() => {
  const map: Record<string, { field: ScalarField; home: string }> = {};
  for (const p of OFFICIAL_PROVIDERS) {
    map[p.keyEnv] = { field: p.keyField as ScalarField, home: `模型服务 · ${p.label}` };
    if (p.baseUrlField) {
      map[p.baseUrlEnv] = { field: p.baseUrlField as ScalarField, home: `模型服务 · ${p.label}` };
    }
  }
  map.VERTEX_AI_PROJECT = { field: "vertexAiProject", home: "模型服务 · Gemini" };
  map.VERTEX_AI_LOCATION = { field: "vertexAiLocation", home: "模型服务 · Gemini" };
  for (const s of SCHOLAR_SOURCES) {
    for (const f of s.fields) map[f.env] = { field: f.field as ScalarField, home: "文献检索" };
  }
  map.PO_SCHOLAR_SOURCES = { field: "scholarSources", home: "文献检索" };
  map.PO_AUTO_MODEL = { field: "autoModel", home: "模型服务 · 路由" };
  map.PO_DEFAULT_PROVIDER = { field: "defaultProvider", home: "模型服务 · 路由" };
  map.SMTP_EMAIL = { field: "smtpEmail", home: "邮件通知" };
  map.SMTP_PASSWORD = { field: "smtpPassword", home: "邮件通知" };
  return map;
})();

/** extraEnv keys that a card on this page edits, and the card's name. */
const GEMINI_BASE_URL_ENV = OFFICIAL_PROVIDERS.find((p) => p.id === "gemini")?.baseUrlEnv ?? "GEMINI_BASE_URL";

const STATIC_CLAIMS: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const p of PRESET_PROVIDERS) {
    map[p.keyEnv] = p.label;
    map[p.baseUrlEnv] = p.label;
    map[p.defaultModelEnv] = p.label;
  }
  map[GEMINI_BASE_URL_ENV] = "Google · Gemini";
  for (const t of PIPELINE_TASKS) map[taskModelEnv(t.id)] = "路由 · 按任务指定";
  return map;
})();

const ALT_KEY_OWNER: Record<string, PresetProvider> = (() => {
  const map: Record<string, PresetProvider> = {};
  for (const p of PRESET_PROVIDERS) for (const alt of p.altKeyEnvs) map[alt] = p;
  return map;
})();

const CARD_KEYS = new Set([...Object.keys(STATIC_CLAIMS), ...Object.keys(ALT_KEY_OWNER)]);

const SCHOLAR_FIELD_SET = new Set<string>([
  "scholarSources",
  ...SCHOLAR_SOURCES.flatMap((s) => s.fields.map((f) => f.field as string)),
]);

const ENV_KEY_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
const SECRETISH_RE = /(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS)$/i;
const ACCEPTED_PROVIDER_TYPES = [
  "openai",
  "openai-compatible",
  "openai_compatible",
  "oai",
  "anthropic",
  "claude",
  "gemini",
  "google",
];

type SectionId = "local" | "models" | "scholar" | "extra" | "smtp" | "diagnostics";

interface NavItem {
  id: string;
  label: string;
  section: SectionId;
  child?: boolean;
}

const NAV: NavItem[] = [
  { id: "local", label: "本地环境", section: "local" },
  { id: "models", label: "模型服务", section: "models" },
  { id: "models-official", label: "官方接口", section: "models", child: true },
  { id: "models-presets", label: "预设平台", section: "models", child: true },
  { id: "models-custom", label: "providers.json", section: "models", child: true },
  { id: "models-routing", label: "路由", section: "models", child: true },
  { id: "scholar", label: "文献检索", section: "scholar" },
  { id: "extra", label: "其他环境变量", section: "extra" },
  { id: "smtp", label: "邮件通知", section: "smtp" },
  { id: "diagnostics", label: "诊断", section: "diagnostics" },
];

const SECTION_LABELS: Record<SectionId, string> = {
  local: "本地环境",
  models: "模型服务",
  scholar: "文献检索",
  extra: "其他环境变量",
  smtp: "邮件通知",
  diagnostics: "诊断",
};

const domId = (id: string) => `settings-${id}`;

type ConnTestKind = "providers" | "scholar";

/** Outcome of one "测试连接" / "测试文献源" click. */
interface ConnTestResult<T> {
  rows: T[];
  /** "" on success, else the command's (Chinese) error message. */
  error: string;
  finishedAt: Date;
  elapsedMs: number;
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

interface EnvRow {
  uid: number;
  key: string;
  value: string;
}

let rowSeq = 0;
const newRow = (key = "", value = ""): EnvRow => ({ uid: ++rowSeq, key, value });

const lower = (s: string) => s.trim().toLowerCase();

function activeKeyEnv(p: PresetProvider, extra: Record<string, string>): string {
  return [p.keyEnv, ...p.altKeyEnvs].find((k) => (extra[k] ?? "") !== "") ?? p.keyEnv;
}

function normalizeEnv(raw: Partial<EnvSecretsConfig> | null | undefined): EnvSecretsConfig {
  const out: EnvSecretsConfig = { ...EMPTY_ENV, extraEnv: {} };
  if (!raw) return out;
  for (const f of SCALAR_FIELDS) {
    const v = raw[f];
    out[f] = typeof v === "string" ? v : "";
  }
  out.extraEnv = { ...(raw.extraEnv ?? {}) };
  out.sourceFingerprint = typeof raw.sourceFingerprint === "string" ? raw.sourceFingerprint : "";
  return out;
}

/** Splits extraEnv into the keys a card edits and the free-form rows. */
function splitExtra(extra: Record<string, string>): {
  claimed: Record<string, string>;
  rows: EnvRow[];
} {
  const claimed: Record<string, string> = {};
  const rows: EnvRow[] = [];
  for (const [k, v] of Object.entries(extra)) {
    if (k in STATIC_CLAIMS) {
      claimed[k] = v; // "" too: an existing `KEY=` placeholder stays in .env
      continue;
    }
    const owner = ALT_KEY_OWNER[k];
    if (owner && v !== "" && activeKeyEnv(owner, extra) === k) {
      claimed[k] = v;
      continue;
    }
    rows.push(newRow(k, v));
  }
  return { claimed, rows };
}

/**
 * The full config to save. An empty value is kept for keys the file already
 * has (they stay as `KEY=`); new keys without a value are left out.
 */
function composeEnv(
  cfg: EnvSecretsConfig,
  rows: EnvRow[],
  savedExtra: Record<string, string>
): EnvSecretsConfig {
  const keep = (k: string, v: string) => v !== "" || k in savedExtra;
  const extra: Record<string, string> = {};
  for (const [k, v] of Object.entries(cfg.extraEnv)) if (keep(k, v)) extra[k] = v;
  for (const r of rows) {
    const k = r.key.trim();
    if (k && !(k in extra) && keep(k, r.value)) extra[k] = r.value;
  }
  return { ...cfg, extraEnv: extra };
}

function flattenEnv(cfg: EnvSecretsConfig): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of SCALAR_FIELDS) out[f] = cfg[f] ?? "";
  for (const [k, v] of Object.entries(cfg.extraEnv)) out[`env:${k}`] = v;
  return out;
}

/** Keys whose value differs; a key present on one side only counts as a change. */
function diffFlat(a: Record<string, string>, b: Record<string, string>): string[] {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].filter((k) => a[k] !== b[k]);
}

/**
 * Re-applies the edits made on this page (`edited` vs `base`, the config the
 * page loaded) onto `fresh`, a new read of the files. Keys the page did not
 * touch take the fresh value, so a variable added to .env in a text editor
 * while the page was open, or the .env of a newly chosen repo dir, survives.
 */
function rebaseEnv(
  edited: EnvSecretsConfig,
  base: EnvSecretsConfig,
  fresh: EnvSecretsConfig
): EnvSecretsConfig {
  const out = normalizeEnv(fresh);
  for (const f of SCALAR_FIELDS) if (edited[f] !== base[f]) out[f] = edited[f];
  const keys = new Set([...Object.keys(edited.extraEnv), ...Object.keys(base.extraEnv)]);
  for (const k of keys) {
    const now = edited.extraEnv[k];
    if (now === base.extraEnv[k]) continue;
    if (now === undefined) delete out.extraEnv[k];
    else out.extraEnv[k] = now;
  }
  return out;
}

function orderFor(enabled: ScholarSourceId[], prev?: ScholarSourceId[]): ScholarSourceId[] {
  const rest = (prev ?? SCHOLAR_DEFAULT_ORDER).filter((id) => !enabled.includes(id));
  const missing = SCHOLAR_DEFAULT_ORDER.filter((id) => !enabled.includes(id) && !rest.includes(id));
  return [...enabled, ...rest, ...missing];
}

const isStringList = (v: unknown) =>
  v === undefined || v === null || typeof v === "string" || (Array.isArray(v) && v.every((x) => typeof x === "string"));

/** Field types `_spec_from_dict` accepts (mirrors providers::check_field_types). */
function fieldTypeError(obj: Record<string, unknown>): string {
  for (const key of ["base_url", "api_key", "default_model"]) {
    const v = obj[key];
    if (v !== undefined && v !== null && typeof v !== "string") return `的 ${key} 必须是字符串`;
  }
  for (const key of ["api_key_env", "models", "drop_params", "aliases"]) {
    if (!isStringList(obj[key])) return `的 ${key} 必须是字符串或字符串数组`;
  }
  for (const key of ["vision", "key_optional"]) {
    const v = obj[key];
    if (v !== undefined && v !== null && typeof v !== "boolean") return `的 ${key} 必须是 true 或 false`;
  }
  const h = obj.headers;
  if (
    h !== undefined &&
    h !== null &&
    (typeof h !== "object" || Array.isArray(h) || !Object.values(h).every((v) => typeof v === "string"))
  ) {
    return "的 headers 必须是值为字符串的对象";
  }
  return "";
}

/** A literal `api_key` (not just a `${VAR}` reference) written into the file. */
const hasInlineKey = (obj: Record<string, unknown>) =>
  typeof obj.api_key === "string" && obj.api_key.replace(/\$\{[^}]*\}|\$\w+/g, "").trim() !== "";

/** Lightweight client-side mirror of providers::parse_providers_json. */
function checkProvidersJson(text: string): { error: string; names: string[]; inlineKeys: string[] } {
  const t = text.trim();
  const inlineKeys: string[] = [];
  if (!t) return { error: "", names: [], inlineKeys };
  let data: unknown;
  try {
    data = JSON.parse(t);
  } catch (e) {
    return { error: `JSON 语法错误：${e instanceof Error ? e.message : String(e)}`, names: [], inlineKeys };
  }
  let entries: unknown[];
  if (Array.isArray(data)) {
    entries = data;
  } else if (data !== null && typeof data === "object") {
    const p = (data as Record<string, unknown>).providers;
    if (p === undefined || p === null) entries = [];
    else if (Array.isArray(p)) entries = p;
    else return { error: "\"providers\" 必须是数组", names: [], inlineKeys };
  } else {
    return { error: "顶层必须是 provider 数组，或带 \"providers\" 数组的对象", names: [], inlineKeys };
  }
  const names: string[] = [];
  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    if (e === null || typeof e !== "object" || Array.isArray(e)) {
      return { error: `第 ${i + 1} 个 provider 必须是对象`, names, inlineKeys };
    }
    const obj = e as Record<string, unknown>;
    const name = obj.name;
    if (typeof name !== "string" || !name.trim()) {
      return { error: `第 ${i + 1} 个 provider 缺少 name`, names, inlineKeys };
    }
    const type = obj.type !== undefined ? obj.type : obj.kind;
    if (type !== undefined) {
      if (typeof type !== "string" || !ACCEPTED_PROVIDER_TYPES.includes(lower(type))) {
        return {
          error: `provider "${name.trim()}" 的 type 无效，可选 openai / anthropic / gemini`,
          names,
          inlineKeys,
        };
      }
    }
    const typeError = fieldTypeError(obj);
    if (typeError) return { error: `provider "${name.trim()}" ${typeError}`, names, inlineKeys };
    if (hasInlineKey(obj)) inlineKeys.push(name.trim());
    if (!names.some((n) => lower(n) === lower(name))) names.push(name.trim());
  }
  return { error: "", names, inlineKeys };
}

function officialConfigured(id: OfficialProvider["id"], st: EnvironmentStatus | null): boolean {
  if (!st) return false;
  switch (id) {
    case "anthropic":
      return st.hasAnthropicKey;
    case "openai":
      return st.hasOpenaiKey;
    case "deepseek":
      return st.hasDeepseekKey;
    case "gemini":
      return st.hasGeminiKey || st.hasVertexAi;
  }
}

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

const IS_MAC =
  typeof navigator !== "undefined" && /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SettingsView({ onEnvUpdated, onToast }: SettingsViewProps) {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [savedSettings, setSavedSettings] = useState<AppSettings | null>(null);
  /** Managed fields + card-edited extraEnv keys only. */
  const [envCfg, setEnvCfg] = useState<EnvSecretsConfig>(() => ({ ...EMPTY_ENV, extraEnv: {} }));
  /** Free-form extraEnv rows (keys no card claims). */
  const [rows, setRows] = useState<EnvRow[]>([]);
  /** Full config as last read from disk. */
  const [savedEnv, setSavedEnv] = useState<EnvSecretsConfig>(() => ({ ...EMPTY_ENV, extraEnv: {} }));
  const [status, setStatus] = useState<EnvironmentStatus | null>(null);
  const [scholarOrder, setScholarOrder] = useState<ScholarSourceId[]>([...SCHOLAR_DEFAULT_ORDER]);
  const [showSecrets, setShowSecrets] = useState(false);
  /** One-off reveal of the providers.json editor while 显示密钥 is off. */
  const [jsonReveal, setJsonReveal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [activeNav, setActiveNav] = useState("local");
  const [presetFilter, setPresetFilter] = useState<"all" | ProviderRegion | "configured">("all");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [jsonUndo, setJsonUndo] = useState<string | null>(null);
  const [serverCheck, setServerCheck] = useState<{ text: string; error: string } | null>(null);
  const [validating, setValidating] = useState(false);
  const [connBusy, setConnBusy] = useState<ConnTestKind | null>(null);
  const fetchedModels = useFetchedModels();
  const [modelsBusy, setModelsBusy] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [providerTest, setProviderTest] = useState<ConnTestResult<ProviderCheckResult> | null>(null);
  const [scholarTest, setScholarTest] = useState<ConnTestResult<ScholarCheckResult> | null>(null);
  /** 测试模型: per-provider model for 「测试连接」 (localStorage). */
  const [pingModels, setPingModels] = useState<PingModels>(() => loadPingModels());

  function changePingModel(name: string, model: string) {
    setPingModels((prev) => {
      const next = withPingModel(prev, name, model);
      savePingModels(next);
      return next;
    });
  }

  const rootRef = useRef<HTMLDivElement>(null);

  function applyLoadedEnv(raw: EnvSecretsConfig, keepOrderFrom?: ScholarSourceId[]) {
    const full = normalizeEnv(raw);
    const { claimed, rows: freeRows } = splitExtra(full.extraEnv);
    setSavedEnv(full);
    setEnvCfg({ ...full, extraEnv: claimed });
    setRows(freeRows);
    const enabled = parseScholarSources(full.scholarSources);
    setScholarOrder((prev) => {
      const base = keepOrderFrom ?? prev;
      const kept = base.filter((id) => enabled.includes(id));
      const sameOrder = kept.length === enabled.length && kept.every((id, i) => id === enabled[i]);
      return keepOrderFrom && sameOrder ? base : orderFor(enabled);
    });
  }

  async function loadAll() {
    setLoadError("");
    try {
      const [s, e] = await Promise.all([api.getSettings(), api.getEnvConfig()]);
      setSettings(s);
      setSavedSettings(s);
      applyLoadedEnv(e);
    } catch (err) {
      setLoadError(String(err));
      onToast(`加载配置失败：${err}`, true);
      return;
    }
    try {
      setStatus(await api.checkEnvironment());
    } catch (err) {
      onToast(`环境检测失败：${err}`, true);
    }
  }

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- derived state ------------------------------------------------------

  const composed = useMemo(
    () => composeEnv(envCfg, rows, savedEnv.extraEnv),
    [envCfg, rows, savedEnv]
  );

  const changes = useMemo(() => {
    const out = diffFlat(flattenEnv(composed), flattenEnv(savedEnv));
    if (settings && savedSettings) {
      for (const f of SETTINGS_FIELDS) {
        if (settings[f] !== savedSettings[f]) out.push(`settings:${f}`);
      }
    }
    return out;
  }, [composed, savedEnv, settings, savedSettings]);

  const changeSet = useMemo(() => new Set(changes), [changes]);

  const rowKeySet = useMemo(() => new Set(rows.map((r) => r.key.trim())), [rows]);

  const dirtySections = useMemo(() => {
    const out = new Set<SectionId>();
    for (const key of changes) {
      if (key.startsWith("settings:")) out.add("local");
      else if (key.startsWith("env:")) {
        const k = key.slice(4);
        out.add(CARD_KEYS.has(k) && !rowKeySet.has(k) ? "models" : "extra");
      } else if (SCHOLAR_FIELD_SET.has(key)) out.add("scholar");
      else if (key === "smtpEmail" || key === "smtpPassword") out.add("smtp");
      else out.add("models");
    }
    return out;
  }, [changes, rowKeySet]);

  const jsonCheck = useMemo(() => checkProvidersJson(envCfg.providersJson), [envCfg.providersJson]);
  const jsonMasked = useMemo(() => maskProvidersJsonSecrets(envCfg.providersJson), [envCfg.providersJson]);

  const rowErrors = useMemo(() => {
    const errs: Record<number, { msg: string; move?: () => void }> = {};
    const seen = new Set<string>();
    for (const r of rows) {
      const key = r.key.trim();
      if (!key) {
        if (r.value !== "") errs[r.uid] = { msg: "请填写变量名" };
        continue;
      }
      if (!ENV_KEY_RE.test(key)) {
        errs[r.uid] = { msg: "变量名只能包含英文字母、数字和下划线，且不能以数字开头" };
        continue;
      }
      const managed = MANAGED_ENV[key];
      if (managed) {
        const empty = (envCfg[managed.field] ?? "") === "";
        errs[r.uid] = {
          msg: `该变量有专门的设置项（${managed.home}）`,
          move: empty
            ? () => {
                setField(managed.field, r.value);
                if (managed.field === "scholarSources") {
                  setScholarOrder(orderFor(parseScholarSources(r.value)));
                }
                removeRow(r.uid);
              }
            : undefined,
        };
        continue;
      }
      const claimOwner =
        STATIC_CLAIMS[key] ??
        (ALT_KEY_OWNER[key] && activeKeyEnv(ALT_KEY_OWNER[key], envCfg.extraEnv) === key
          ? ALT_KEY_OWNER[key].label
          : undefined);
      if (claimOwner) {
        const empty = (envCfg.extraEnv[key] ?? "") === "";
        errs[r.uid] = {
          msg: `该变量由「${claimOwner}」管理`,
          move: empty
            ? () => {
                setExtra(key, r.value);
                removeRow(r.uid);
              }
            : undefined,
        };
        continue;
      }
      if (seen.has(key)) {
        errs[r.uid] = { msg: "变量名重复" };
        continue;
      }
      seen.add(key);
    }
    return errs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, envCfg]);

  const rowErrorCount = Object.keys(rowErrors).length;
  const problemCount = rowErrorCount + (jsonCheck.error ? 1 : 0);

  const configuredSet = useMemo(
    () => new Set((status?.configuredProviders ?? []).map(lower)),
    [status]
  );

  const customNames = useMemo(() => {
    const out: string[] = [];
    for (const n of [...(status?.customProviders ?? []), ...jsonCheck.names]) {
      if (!out.some((x) => lower(x) === lower(n))) out.push(n);
    }
    return out;
  }, [status, jsonCheck.names]);

  const modelRefOptions = useMemo(() => {
    const out: string[] = [];
    const add = (v: string) => {
      if (v && !out.includes(v)) out.push(v);
    };
    MODEL_PRESETS.filter((m) => m !== "auto").forEach(add);
    PRESET_PROVIDERS.forEach((p) => add(p.examplePlaceholder ? `${p.id}:` : p.exampleRef));
    customNames.forEach((n) => add(`${n}:`));
    // What the providers' APIs listed in the last fetch (newest first per provider).
    fetchedModelRefs(fetchedModels).forEach(add);
    return out;
  }, [customNames, fetchedModels]);

  const enabledSources = useMemo(
    () => parseScholarSources(envCfg.scholarSources),
    [envCfg.scholarSources]
  );

  // ---- mutations ------------------------------------------------------------

  function setField(field: ScalarField, value: string) {
    setEnvCfg((prev) => ({ ...prev, [field]: value }));
  }

  function setExtra(key: string, value: string) {
    setEnvCfg((prev) => {
      const extra = { ...prev.extraEnv };
      if (value.trim() !== "") extra[key] = value;
      else if (key in savedEnv.extraEnv) extra[key] = ""; // stays in .env as `KEY=`
      else delete extra[key];
      return { ...prev, extraEnv: extra };
    });
  }

  function updateRow(uid: number, patch: Partial<EnvRow>) {
    setRows((prev) => prev.map((r) => (r.uid === uid ? { ...r, ...patch } : r)));
  }

  function removeRow(uid: number) {
    setRows((prev) => prev.filter((r) => r.uid !== uid));
  }

  function setScholar(order: ScholarSourceId[], enabled: ScholarSourceId[]) {
    setScholarOrder(order);
    setField("scholarSources", serializeScholarSources(order.filter((id) => enabled.includes(id))));
  }

  function toggleSource(id: ScholarSourceId) {
    const on = enabledSources.includes(id);
    if (on && enabledSources.length <= 1) {
      onToast("至少保留一个文献检索来源", true);
      return;
    }
    const next = on ? enabledSources.filter((x) => x !== id) : [...enabledSources, id];
    setScholar(scholarOrder, next);
  }

  function moveSource(id: ScholarSourceId, delta: -1 | 1) {
    const idx = scholarOrder.indexOf(id);
    const target = idx + delta;
    if (idx < 0 || target < 0 || target >= scholarOrder.length) return;
    const order = [...scholarOrder];
    [order[idx], order[target]] = [order[target], order[idx]];
    setScholar(order, enabledSources);
  }

  function resetSources() {
    setScholarOrder([...SCHOLAR_DEFAULT_ORDER]);
    setField("scholarSources", "");
  }

  function handleDiscard() {
    if (savedSettings) setSettings(savedSettings);
    applyLoadedEnv(savedEnv);
    setJsonUndo(null);
    onToast("已放弃未保存的更改");
  }

  function scrollToNav(id: string) {
    const el = document.getElementById(domId(id));
    if (!el) return;
    el.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
    setActiveNav(id);
  }

  async function handleSaveAll() {
    if (!settings || saving) return;
    if (rowErrorCount > 0) {
      onToast("「其他环境变量」中有条目需要修正", true);
      scrollToNav("extra");
      return;
    }
    if (jsonCheck.error) {
      onToast(`providers.json 有误：${jsonCheck.error}`, true);
      scrollToNav("models-custom");
      return;
    }
    setSaving(true);
    try {
      const dirChanged = savedSettings !== null && settings.paperOrchestraDir !== savedSettings.paperOrchestraDir;
      const savedApp = await api.updateSettings({
        paperOrchestraDir: settings.paperOrchestraDir,
        pythonPath: settings.pythonPath,
        outputRootDir: settings.outputRootDir,
      });
      const appNow = savedApp ?? settings;
      setSettings(appNow);
      setSavedSettings(appNow);

      // Re-read the files (from the new repo dir, if it changed) and apply only
      // the edits made on this page, so nothing the page never showed is lost.
      const fresh = normalizeEnv(await api.getEnvConfig());
      const next = rebaseEnv(composed, savedEnv, fresh);
      const envEdited = diffFlat(flattenEnv(next), flattenEnv(fresh)).length > 0;
      if (envEdited) await api.saveEnvConfig(next);

      const nextStatus = await api.checkEnvironment();
      setStatus(nextStatus);
      await onEnvUpdated();
      let reread: EnvSecretsConfig = next;
      try {
        reread = await api.getEnvConfig();
      } catch {
        // keep what we sent
      }
      applyLoadedEnv(reread, scholarOrder);
      setJsonUndo(null);

      if (nextStatus.providersJsonError) {
        onToast(`已保存，但 providers.json 有误：${nextStatus.providersJsonError}`, true);
      } else if (dirChanged) {
        onToast(envEdited ? "已切换仓库目录，并把本页的修改写入新目录的 .env" : "已切换仓库目录，已读取该目录的 .env");
      } else {
        onToast(envEdited ? "设置已保存，.env 已同步" : "设置已保存");
      }
    } catch (err) {
      onToast(`保存失败：${err}`, true);
    } finally {
      setSaving(false);
    }
  }

  async function handleRecheck() {
    setChecking(true);
    try {
      const st = await api.checkEnvironment();
      setStatus(st);
      await onEnvUpdated();
    } catch (err) {
      onToast(`环境检测失败：${err}`, true);
    } finally {
      setChecking(false);
    }
  }

  /** Runs tools/check_providers.py (--ping or --scholar) against the saved configuration. */
  async function handleConnectionTest(kind: ConnTestKind) {
    if (connBusy) return;
    setConnBusy(kind);
    const started = performance.now();
    let rows: { providers: ProviderCheckResult[]; scholar: ScholarCheckResult[] } = {
      providers: [],
      scholar: [],
    };
    let error = "";
    try {
      rows = normalizeConnectionReport(
        await api.checkConnections(
          kind === "providers",
          kind === "scholar",
          kind === "providers" ? pingModels : undefined
        )
      );
    } catch (err) {
      error = String(err);
    }
    const meta = { error, finishedAt: new Date(), elapsedMs: performance.now() - started };
    if (kind === "providers") setProviderTest({ rows: rows.providers, ...meta });
    else setScholarTest({ rows: rows.scholar, ...meta });
    setConnBusy(null);
  }

  /** Asks every configured provider's API for its model list (free) and keeps it for all model fields. */
  async function handleFetchModels() {
    if (modelsBusy) return;
    setModelsBusy(true);
    setModelsError("");
    try {
      const report = await api.listModels();
      const rows = Array.isArray(report.models) ? report.models : [];
      if (rows.length === 0) {
        setModelsError("还没有已配置的平台：先在上面填写 API Key 并保存，再来拉取。");
      } else {
        saveFetchedModels(rows);
      }
    } catch (err) {
      setModelsError(String(err));
    } finally {
      setModelsBusy(false);
    }
  }

  async function handleValidateJson() {
    const text = envCfg.providersJson;
    setValidating(true);
    try {
      const error = await api.validateProvidersJson(text);
      setServerCheck({ text, error });
    } catch (err) {
      setServerCheck({ text, error: String(err) });
    } finally {
      setValidating(false);
    }
  }

  function handleInsertExample() {
    const current = envCfg.providersJson;
    if (current.trim()) setJsonUndo(current);
    setField("providersJson", PROVIDERS_JSON_EXAMPLE);
  }

  function handleFormatJson() {
    try {
      const data = JSON.parse(envCfg.providersJson);
      setJsonUndo(envCfg.providersJson);
      setField("providersJson", `${JSON.stringify(data, null, 2)}\n`);
    } catch {
      // button is disabled while the JSON does not parse
    }
  }

  async function handleBrowseDir(field: "paperOrchestraDir" | "outputRootDir") {
    if (!settings) return;
    const picked = await api.pickDirectory(settings[field]);
    if (picked) setSettings({ ...settings, [field]: picked });
  }

  async function handleBrowsePython() {
    if (!settings) return;
    const isWindows = navigator.userAgent.toLowerCase().includes("windows");
    const repo = settings.paperOrchestraDir.trim().replace(/[\\/]+$/, "");
    // macOS / Linux: start in the repo's venv, because ".venv" is hidden in the
    // file dialog. (Not on Windows: a missing folder there turns into a typed
    // file name.)
    const startIn = repo && !isWindows ? `${repo}/.venv/bin` : undefined;
    const picked = await api.pickFile(
      isWindows ? [{ name: "Executable", extensions: ["exe"] }] : undefined,
      startIn
    );
    if (!picked) return;
    // The macOS dialog returns the file a venv's python links to; map it back.
    const python = await api.resolvePickedPython(picked, repo).catch(() => picked);
    setSettings((current) => (current ? { ...current, pythonPath: python } : current));
    if (python !== picked) {
      onToast(
        `已填入仓库 .venv 里的 Python（选中的 ${picked} 是它链接到的原始解释器，缺少 venv 里装的包）。` +
          `确实要用原始解释器的话，直接在输入框里填它的路径即可。`
      );
    }
  }

  // ---- keyboard shortcut & section tracking -------------------------------

  const saveRef = useRef<() => void>(() => {});
  saveRef.current = () => {
    void handleSaveAll();
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const ready = settings !== null;

  useEffect(() => {
    if (!ready) return;
    const root = rootRef.current;
    const scroller = root?.closest(".view-scroll") as HTMLElement | null;
    if (!scroller) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const top = scroller.getBoundingClientRect().top;
      let current = NAV[0].id;
      for (const item of NAV) {
        const el = document.getElementById(domId(item.id));
        if (el && el.getBoundingClientRect().top - top <= 120) current = item.id;
      }
      if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) {
        current = NAV[NAV.length - 1].id;
      }
      setActiveNav(current);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    update();
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [ready]);

  // ---- render ---------------------------------------------------------------

  if (!settings) {
    return (
      <div className="st-loading card">
        {loadError ? (
          <>
            <p className="hint">配置加载失败：{loadError}</p>
            <button type="button" className="btn btn-sm" onClick={loadAll}>
              <RefreshCw size={13} /> 重试
            </button>
          </>
        ) : (
          <p className="hint">正在加载配置…</p>
        )}
      </div>
    );
  }

  const show = showSecrets;
  /** providers.json holds a literal key and the user hasn't asked to see it: show the masked, read-only view. */
  const jsonLocked = !show && !jsonReveal && jsonMasked !== envCfg.providersJson;
  const activeSection = NAV.find((n) => n.id === activeNav)?.section ?? "local";
  const dirty = changes.length > 0;

  const presetsShown = PRESET_PROVIDERS.filter((p) => {
    if (presetFilter === "all") return true;
    if (presetFilter === "configured") return configuredSet.has(p.id);
    return p.region === presetFilter;
  });

  const presetKeys = (p: PresetProvider) =>
    [p.keyEnv, ...p.altKeyEnvs, p.baseUrlEnv, p.defaultModelEnv].map((k) => `env:${k}`);

  const presetEdited = (p: PresetProvider) =>
    presetKeys(p).some((k) => changeSet.has(k) && !rowKeySet.has(k.slice(4)));

  const presetOpen = (p: PresetProvider) => {
    const flag = expanded[p.id];
    if (flag !== undefined) return flag;
    const extra = envCfg.extraEnv;
    return p.keyOptional
      ? Boolean(extra[activeKeyEnv(p, extra)] || extra[p.defaultModelEnv])
      : Boolean(extra[p.baseUrlEnv] || extra[p.defaultModelEnv]);
  };

  const renderPreset = (p: PresetProvider) => (
    <PresetCard
      key={p.id}
      p={p}
      extra={envCfg.extraEnv}
      setExtra={setExtra}
      show={show}
      configured={configuredSet.has(p.id)}
      edited={presetEdited(p)}
      open={presetOpen(p)}
      onToggle={() => setExpanded((prev) => ({ ...prev, [p.id]: !presetOpen(p) }))}
    />
  );

  const taskOverrideCount = PIPELINE_TASKS.filter((t) => envCfg.extraEnv[taskModelEnv(t.id)]).length;

  const defaultProviderKnown =
    !envCfg.defaultProvider ||
    OFFICIAL_PROVIDERS.some((p) => p.id === lower(envCfg.defaultProvider)) ||
    PRESET_PROVIDERS.some((p) => p.id === lower(envCfg.defaultProvider)) ||
    customNames.some((n) => n === envCfg.defaultProvider);

  const jsonDirty = changeSet.has("providersJson");
  const serverResult =
    serverCheck && serverCheck.text === envCfg.providersJson ? serverCheck : null;

  const llmOk =
    !status?.providersJsonError &&
    ((status?.configuredProviders?.length ?? 0) > 0 ||
      Boolean(
        status &&
          (status.hasAnthropicKey ||
            status.hasDeepseekKey ||
            status.hasGeminiKey ||
            status.hasVertexAi ||
            status.hasOpenaiKey)
      ));

  const configuredNames = status?.configuredProviders ?? [];
  const legacyNames = status
    ? [
        status.hasAnthropicKey ? "Claude" : "",
        status.hasDeepseekKey ? "DeepSeek" : "",
        status.hasGeminiKey ? "Gemini" : "",
        status.hasVertexAi ? "Vertex AI" : "",
        status.hasOpenaiKey ? "OpenAI" : "",
      ].filter(Boolean)
    : [];
  const llmDetail = status?.providersJsonError
    ? "providers.json 有误，运行会失败"
    : configuredNames.length > 0
    ? `${configuredNames.length} 个：${configuredNames.slice(0, 3).map(providerLabel).join("、")}${
        configuredNames.length > 3 ? " 等" : ""
      }`
    : legacyNames.join(" / ") || "未填写";

  const coreMissingKey = enabledSources.includes("core") && !envCfg.coreApiKey.trim();

  const s2Interval = envCfg.s2MinIntervalSec.trim();
  const s2IntervalNum = Number(s2Interval);
  const s2IntervalWarn = !s2Interval
    ? ""
    : !Number.isFinite(s2IntervalNum) || s2IntervalNum < 0
    ? "需为非负数字，否则将使用默认值 1.1"
    : s2IntervalNum < 1
    ? "低于 1 秒可能触发 Semantic Scholar 限流"
    : "";

  return (
    <div className="st-root" ref={rootRef}>
      <datalist id="st-model-refs">
        {modelRefOptions.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>

      <div className="st-layout">
        {/* ---------------- in-page navigation ---------------- */}
        <nav className="st-nav" aria-label="设置分区">
          <div className="st-nav-eyebrow">设置</div>
          <ul className="st-nav-list">
            {NAV.map((item) => {
              const isActive = item.child ? activeNav === item.id : activeSection === item.section;
              return (
                <li key={item.id} className={item.child ? "is-child" : undefined}>
                  <button
                    type="button"
                    className={`st-nav-link${isActive ? " active" : ""}`}
                    aria-current={isActive && !item.child ? "true" : undefined}
                    onClick={() => scrollToNav(item.id)}
                  >
                    <span>{item.label}</span>
                    {!item.child && dirtySections.has(item.section) && (
                      <span className="st-nav-dirty" title="有未保存的更改" />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="st-nav-foot">
            <button
              type="button"
              className="btn btn-sm st-secret-toggle"
              aria-pressed={showSecrets}
              onClick={() => {
                setShowSecrets((v) => !v);
                setJsonReveal(false);
              }}
            >
              {showSecrets ? <EyeOff size={13} /> : <Eye size={13} />}
              {showSecrets ? "隐藏密钥" : "显示密钥"}
            </button>
          </div>
        </nav>

        <div className="st-main">
          <p className="st-lede">
            这里的配置写入 PaperOrchestra 仓库根目录的 <span className="st-code">.env</span> 与{" "}
            <span className="st-code">providers.json</span>，只保存在本机。
          </p>

          {/* ---------------- a) local environment ---------------- */}
          <Section
            id="local"
            num="01"
            title="本地环境"
            description="仓库位置、Python 解释器与论文输出目录，保存在本地 SQLite。"
          >
            <div className="card st-card">
              <div className="stack">
                <div className="field">
                  <label htmlFor="st-repo" className="st-label">
                    <span>PaperOrchestra 仓库目录</span>
                    {status && (
                      <StatusNote
                        ok={status.paperOrchestraExists && status.cliScriptExists}
                        text={
                          status.offlineHint
                            ? "外接盘没连接"
                            : !status.paperOrchestraExists
                            ? "目录不存在"
                            : status.cliScriptExists
                            ? "已找到 paper_writing_cli.py"
                            : "未找到 paper_writing_cli.py"
                        }
                      />
                    )}
                  </label>
                  <div className="st-inline">
                    <TextInput
                      id="st-repo"
                      value={settings.paperOrchestraDir}
                      onChange={(v) => setSettings({ ...settings, paperOrchestraDir: v })}
                      mono
                    />
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => handleBrowseDir("paperOrchestraDir")}
                    >
                      <FolderSearch size={14} /> 选择
                    </button>
                  </div>
                </div>

                <div className="field">
                  <label htmlFor="st-python" className="st-label">
                    <span>Python 解释器</span>
                    {status && (
                      <StatusNote
                        ok={status.pythonExists}
                        text={status.pythonExists ? status.pythonVersion || "可用" : "不可用"}
                      />
                    )}
                  </label>
                  <div className="st-inline">
                    <TextInput
                      id="st-python"
                      value={settings.pythonPath}
                      onChange={(v) => setSettings({ ...settings, pythonPath: v })}
                      mono
                    />
                    <button type="button" className="btn btn-sm" onClick={handleBrowsePython}>
                      <FolderSearch size={14} /> 选择
                    </button>
                  </div>
                  <p className="hint">
                    Windows 通常为 <span className="st-code">.venv\Scripts\python.exe</span>，macOS /
                    Linux 为 <span className="st-code">.venv/bin/python</span>
                  </p>
                </div>

                <div className="field">
                  <label htmlFor="st-output" className="st-label">
                    <span>论文输出根目录</span>
                  </label>
                  <div className="st-inline">
                    <TextInput
                      id="st-output"
                      value={settings.outputRootDir}
                      onChange={(v) => setSettings({ ...settings, outputRootDir: v })}
                      mono
                    />
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => handleBrowseDir("outputRootDir")}
                    >
                      <FolderSearch size={14} /> 选择
                    </button>
                  </div>
                </div>
              </div>

              <div className="st-meta-line">
                <span>SQLite 数据库</span>
                <span className="st-code st-path" title={settings.dbPath}>
                  {settings.dbPath}
                </span>
                {status && <span className="st-tnum">{formatBytes(status.dbSizeBytes)}</span>}
              </div>
            </div>
          </Section>

          {/* ---------------- b) model services ---------------- */}
          <Section
            id="models"
            num="02"
            title="模型服务"
            description="填写任意一个平台的密钥即可开始；可以同时配置多个，并在工作台里用「平台:模型名」指定。"
          >
            <SubSection id="models-official" title="官方接口" hint="原生支持的四家，独立字段保存。">
              <div className="st-ogrid">
                {OFFICIAL_PROVIDERS.map((p) => (
                  <OfficialCard
                    key={p.id}
                    p={p}
                    env={envCfg}
                    altKeyInUse={
                      envCfg[p.keyField as ScalarField]
                        ? undefined
                        : p.altKeyEnvs.find((k) => (composed.extraEnv[k] ?? "") !== "")
                    }
                    setField={setField}
                    setExtra={setExtra}
                    show={show}
                    status={status}
                    edited={officialChangeKeys(p).some((k) => changeSet.has(k))}
                  />
                ))}
              </div>
            </SubSection>

            <SubSection
              id="models-presets"
              title="预设平台"
              hint="OpenAI 兼容接口，填入密钥即可使用；高级选项可覆盖接口地址和默认模型。"
              actions={
                <div className="st-filter" role="group" aria-label="筛选平台">
                  {(
                    [
                      ["all", "全部"],
                      ["cn", REGION_LABELS.cn],
                      ["global", REGION_LABELS.global],
                      ["local", REGION_LABELS.local],
                      ["configured", "已配置"],
                    ] as Array<[typeof presetFilter, string]>
                  ).map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      className={`chip${presetFilter === key ? " on" : ""}`}
                      aria-pressed={presetFilter === key}
                      onClick={() => setPresetFilter(key)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              }
            >
              {presetFilter === "all" ? (
                (["cn", "global", "local"] as ProviderRegion[]).map((region) => (
                  <div key={region} className="st-group">
                    <div className="st-group-label">
                      {REGION_LABELS[region]}
                      {region === "local" && (
                        <span className="hint">无需密钥，填写服务地址后即视为启用</span>
                      )}
                    </div>
                    <div className="st-pgrid">
                      {PRESET_PROVIDERS.filter((p) => p.region === region).map(renderPreset)}
                    </div>
                  </div>
                ))
              ) : presetsShown.length > 0 ? (
                <div className="st-pgrid">{presetsShown.map(renderPreset)}</div>
              ) : (
                <p className="st-empty hint">
                  {presetFilter === "configured" ? "还没有已配置的预设平台（以上次保存为准）。" : "没有匹配的平台。"}
                </p>
              )}
            </SubSection>

            <SubSection
              id="models-custom"
              title="自定义平台 · providers.json"
              hint="中转站、自建网关或其他接口可在这里声明，保存后写入仓库根目录。"
            >
              <div className="card st-card st-json">
                <div className="st-json-toolbar">
                  <div className="row">
                    <button type="button" className="btn btn-sm" onClick={handleInsertExample}>
                      <Braces size={13} />
                      {envCfg.providersJson.trim() ? "替换为示例" : "插入示例"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={handleFormatJson}
                      disabled={!envCfg.providersJson.trim() || Boolean(jsonCheck.error)}
                    >
                      格式化
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={handleValidateJson}
                      disabled={!envCfg.providersJson.trim() || validating}
                    >
                      <Check size={13} /> {validating ? "校验中…" : "校验"}
                    </button>
                    {jsonUndo !== null && (
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => {
                          setField("providersJson", jsonUndo);
                          setJsonUndo(null);
                        }}
                      >
                        <Undo2 size={13} /> 撤销
                      </button>
                    )}
                  </div>
                  <span className="hint">
                    {!envCfg.providersJson.trim()
                      ? "未创建（留空不会删除已有文件）"
                      : jsonCheck.error
                      ? "格式有误"
                      : `${jsonCheck.names.length} 个平台`}
                  </span>
                </div>

                {jsonLocked ? (
                  <div className="st-json-locked">
                    <textarea
                      className="textarea-code st-json-editor"
                      value={jsonMasked}
                      readOnly
                      spellCheck={false}
                      aria-label="providers.json 内容（密钥已隐藏，只读）"
                      onFocus={(e) => e.currentTarget.blur()}
                    />
                    <div className="st-json-lockbar">
                      <span className="hint">
                        <LockKeyhole size={13} /> 文件里的密钥已隐藏，此处只读，防止截图或共享屏幕时露出。
                      </span>
                      <button type="button" className="btn btn-sm" onClick={() => setJsonReveal(true)}>
                        <Eye size={13} /> 显示原文并编辑
                      </button>
                    </div>
                  </div>
                ) : (
                  <textarea
                    className="textarea-code st-json-editor"
                    value={envCfg.providersJson}
                    spellCheck={false}
                    aria-label="providers.json 内容"
                    aria-invalid={Boolean(jsonCheck.error)}
                    placeholder={'{\n  "providers": []\n}'}
                    onChange={(e) => {
                      setJsonUndo(null);
                      setField("providersJson", e.target.value);
                    }}
                  />
                )}

                {jsonCheck.error ? (
                  <Notice tone="danger" icon={<CircleX size={14} />}>
                    {jsonCheck.error}
                  </Notice>
                ) : serverResult ? (
                  serverResult.error ? (
                    <Notice tone="danger" icon={<CircleX size={14} />}>
                      {serverResult.error}
                    </Notice>
                  ) : (
                    <Notice tone="success" icon={<CircleCheck size={14} />}>
                      校验通过
                    </Notice>
                  )
                ) : !jsonDirty && status?.providersJsonError ? (
                  <Notice tone="danger" icon={<CircleX size={14} />}>
                    当前文件有误，所有运行都会失败：{status.providersJsonError}
                  </Notice>
                ) : null}

                {jsonCheck.inlineKeys.length > 0 && (
                  <Notice tone="warn" icon={<CircleAlert size={14} />}>
                    {jsonCheck.inlineKeys.join("、")} 的 api_key 以明文写在这里，截图或共享屏幕时会露出。建议移到「其他环境变量」，再用
                    api_key_env 引用。
                  </Notice>
                )}

                {(status?.customProviders?.length ?? 0) > 0 && (
                  <div className="st-inline-list">
                    <span className="hint">已声明</span>
                    {status!.customProviders.map((n) => (
                      <span
                        key={n}
                        className={`badge ${configuredSet.has(lower(n)) ? "badge-succeeded" : "badge-neutral"}`}
                        title={configuredSet.has(lower(n)) ? "已配置密钥" : "尚未找到密钥"}
                      >
                        {n}
                      </span>
                    ))}
                  </div>
                )}

                <details className="st-details">
                  <summary>
                    <ChevronRight size={13} className="st-details-caret" /> 字段说明
                  </summary>
                  <dl className="st-ref">
                    {PROVIDERS_JSON_FIELDS.map((f) => (
                      <div key={f.key} className="st-ref-row">
                        <dt className="st-code">{f.key}</dt>
                        <dd>{f.desc}</dd>
                      </div>
                    ))}
                  </dl>
                  <div className="st-ref-caption hint">顶层可选字段</div>
                  <dl className="st-ref">
                    {PROVIDERS_JSON_TOP_LEVEL.map((f) => (
                      <div key={f.key} className="st-ref-row">
                        <dt className="st-code">{f.key}</dt>
                        <dd>{f.desc}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="hint">
                    密钥请放在「其他环境变量」里，再用 <span className="st-code">api_key_env</span>{" "}
                    引用变量名，不要直接写进 providers.json。
                  </p>
                </details>
              </div>
            </SubSection>

            <SubSection id="models-routing" title="路由" hint="决定 auto 与未带前缀的模型名发往哪里。">
              <div className="card st-card">
                <div className="st-grid-2">
                  <div className="field">
                    <Label htmlFor="st-auto-model" text="统一 auto 模型" env="PO_AUTO_MODEL" />
                    <TextInput
                      id="st-auto-model"
                      value={envCfg.autoModel}
                      onChange={(v) => setField("autoModel", v)}
                      placeholder="留空：按已配置的密钥智能分工"
                      list="st-model-refs"
                      mono
                    />
                    <p className="hint">设置后，下方没有单独指定模型的 auto 任务都用这个模型。</p>
                  </div>
                  <div className="field">
                    <Label htmlFor="st-default-provider" text="默认平台" env="PO_DEFAULT_PROVIDER" />
                    <select
                      id="st-default-provider"
                      className="select"
                      value={envCfg.defaultProvider}
                      onChange={(e) => setField("defaultProvider", e.target.value)}
                    >
                      <option value="">自动选择</option>
                      {!defaultProviderKnown && (
                        <option value={envCfg.defaultProvider}>{envCfg.defaultProvider}（未知）</option>
                      )}
                      <optgroup label="官方接口">
                        {OFFICIAL_PROVIDERS.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.id} · {p.label}
                          </option>
                        ))}
                      </optgroup>
                      <optgroup label="预设平台">
                        {PRESET_PROVIDERS.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.id} · {p.label}
                          </option>
                        ))}
                      </optgroup>
                      {customNames.length > 0 && (
                        <optgroup label="自定义平台">
                          {customNames.map((n) => (
                            <option key={n} value={n}>
                              {n}
                            </option>
                          ))}
                        </optgroup>
                      )}
                    </select>
                    <p className="hint">
                      无法从名称识别平台的模型发往这里；自动选择时依次尝试自定义平台、OpenRouter、硅基流动、OpenAI。
                    </p>
                  </div>
                </div>

                <div className="st-syntax">
                  <span className="st-syntax-label">写法</span>
                  <span>
                    <span className="st-code">平台:模型名</span>，如{" "}
                    <span className="st-code">qwen:qwen4-max</span>、
                    <span className="st-code">ollama:qwen3.8</span>
                    ，只按第一个冒号拆分；不带前缀时按名称识别（claude-* → Anthropic，gpt-* / o3 →
                    OpenAI，deepseek-* → DeepSeek，gemini-* → Gemini）。
                  </span>
                </div>

                <div className="st-model-fetch">
                  <div className="st-model-fetch-head">
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={handleFetchModels}
                      disabled={modelsBusy}
                    >
                      {modelsBusy ? (
                        <Loader2 size={13} className="spin" />
                      ) : (
                        <RefreshCw size={13} />
                      )}
                      {modelsBusy ? "正在向各家 API 拉取…" : "从各家 API 拉取最新模型"}
                    </button>
                    <span className="hint">
                      {fetchedModels
                        ? `上次拉取：${new Date(fetchedModels.fetchedAt).toLocaleString()}，共 ${
                            fetchedModelRefs(fetchedModels).length
                          } 个模型，已加入所有模型输入框的候选。`
                        : "用已保存的 API Key 读取各平台当前提供的模型（不发生成请求，不花钱），加入所有模型输入框的候选。"}
                    </span>
                  </div>
                  {modelsError && <p className="st-model-fetch-error">{modelsError}</p>}
                  {fetchedModels && fetchedModels.rows.length > 0 && (
                    <ul className="st-model-fetch-list">
                      {fetchedModels.rows.map((row) => (
                        <li key={row.name}>
                          <span className="st-model-fetch-label">{row.label}</span>
                          {row.ok ? (
                            <span className="st-tnum">{row.models.length} 个</span>
                          ) : (
                            <span className="st-model-fetch-error">
                              {row.error || "失败"}
                              {row.models.length > 0 ? `（沿用上次的 ${row.models.length} 个）` : ""}
                            </span>
                          )}
                          {row.ok && row.models.length > 0 && (
                            <span className="st-model-fetch-sample st-code">
                              {row.models.slice(0, 4).join("  ")}
                              {row.models.length > 4 ? "  …" : ""}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <details className="st-details">
                  <summary>
                    <ChevronRight size={13} className="st-details-caret" /> 按任务指定模型
                    {taskOverrideCount > 0 && (
                      <span className="badge badge-neutral st-tnum">{taskOverrideCount} 项</span>
                    )}
                  </summary>
                  <p className="hint">
                    和工作台的「按任务指定模型」是同一份设置，优先于上面的统一模型；留空则用统一模型，统一模型也没填才按 Key 自动分工。
                  </p>
                  <div className="st-task-grid">
                    {PIPELINE_TASKS.map((t) => {
                      const env = taskModelEnv(t.id);
                      return (
                        <div key={t.id} className="field" title={t.hint}>
                          <Label htmlFor={`st-task-${t.id}`} text={t.label} env={env} />
                          <TextInput
                            id={`st-task-${t.id}`}
                            value={envCfg.extraEnv[env] ?? ""}
                            onChange={(v) => setExtra(env, v)}
                            placeholder={t.parent ? `跟随「${PIPELINE_TASKS.find((p) => p.id === t.parent)?.label}」` : "auto"}
                            list="st-model-refs"
                            mono
                          />
                        </div>
                      );
                    })}
                  </div>
                </details>
              </div>
            </SubSection>
          </Section>

          {/* ---------------- c) scholarly search ---------------- */}
          <Section
            id="scholar"
            num="03"
            title="文献检索"
            description="按从上到下的顺序依次查询，点击名称启用或停用；不需要的来源可以关掉以加快检索。"
            actions={
              <button
                type="button"
                className="btn btn-sm"
                onClick={resetSources}
                disabled={!envCfg.scholarSources && scholarOrder.every((id, i) => id === SCHOLAR_DEFAULT_ORDER[i])}
              >
                <RotateCcw size={13} /> 恢复默认
              </button>
            }
          >
            <div className="card st-card st-sources-card">
              <ol className="st-sources">
                {scholarOrder.map((id, idx) => {
                  const src = SCHOLAR_SOURCES.find((s) => s.id === id);
                  if (!src) return null;
                  const on = enabledSources.includes(id);
                  const rank = on ? enabledSources.indexOf(id) + 1 : null;
                  return (
                    <SourceRow
                      key={id}
                      src={src}
                      on={on}
                      rank={rank}
                      isFirst={idx === 0}
                      isLast={idx === scholarOrder.length - 1}
                      onToggle={() => toggleSource(id)}
                      onMove={(d) => moveSource(id, d)}
                      env={envCfg}
                      setField={setField}
                      show={show}
                      warn={
                        id === "core" && coreMissingKey
                          ? "缺少 Key，将被跳过"
                          : id === "semantic_scholar"
                          ? s2IntervalWarn
                          : ""
                      }
                    />
                  );
                })}
              </ol>
              <div className="st-meta-line">
                <span>检索顺序</span>
                <span className="st-chain">
                  {enabledSources.map((id) => scholarSourceLabel(id)).join(" → ") || "无"}
                </span>
              </div>
              {status && !dirtySections.has("scholar") && (
                <p className="hint st-applied">
                  当前生效：
                  {(status.scholarSources ?? []).map((id) => scholarSourceLabel(id)).join(" → ") || "无"}
                </p>
              )}
            </div>
          </Section>

          {/* ---------------- d) other env vars ---------------- */}
          <Section
            id="extra"
            num="04"
            title="其他环境变量"
            description="写入 .env 的其余变量，例如自定义平台的密钥（供 api_key_env 引用）或 PO_PROVIDERS_FILE。"
            actions={
              <button type="button" className="btn btn-sm" onClick={() => setRows((prev) => [...prev, newRow()])}>
                <Plus size={13} /> 添加变量
              </button>
            }
          >
            <div className="card st-card">
              {rows.length === 0 ? (
                <div className="st-empty">
                  <p className="hint">
                    暂无其他变量。预设平台的密钥请直接在「模型服务」的卡片中填写。
                  </p>
                </div>
              ) : (
                <div className="st-kv">
                  <div className="st-kv-head" aria-hidden="true">
                    <span>变量名</span>
                    <span>值</span>
                    <span />
                  </div>
                  {rows.map((r) => {
                    const err = rowErrors[r.uid];
                    const secret = SECRETISH_RE.test(r.key.trim());
                    return (
                      <div key={r.uid} className={`st-kv-row${err ? " has-error" : ""}`}>
                        <input
                          className="input st-mono"
                          value={r.key}
                          placeholder="VARIABLE_NAME"
                          aria-label="变量名"
                          aria-invalid={Boolean(err)}
                          spellCheck={false}
                          autoComplete="off"
                          onChange={(e) => updateRow(r.uid, { key: e.target.value })}
                        />
                        <input
                          className="input st-mono"
                          type={secret && !show ? "password" : "text"}
                          value={r.value}
                          placeholder="值"
                          aria-label={`${r.key || "变量"} 的值`}
                          spellCheck={false}
                          autoComplete="off"
                          onChange={(e) => updateRow(r.uid, { value: e.target.value })}
                        />
                        <button
                          type="button"
                          className="btn btn-sm st-icon-btn"
                          aria-label={`删除 ${r.key || "该变量"}`}
                          title="删除"
                          onClick={() => removeRow(r.uid)}
                        >
                          <Trash2 size={13} />
                        </button>
                        {(err || (r.key.trim() && r.value === "")) && (
                          <div className={`st-kv-note${err ? " is-error" : ""}`}>
                            {err ? (
                              <>
                                {err.msg}
                                {err.move && (
                                  <button type="button" className="st-link" onClick={err.move}>
                                    移到对应设置项
                                  </button>
                                )}
                              </>
                            ) : r.key.trim() in savedEnv.extraEnv ? (
                              `值为空，.env 中保留为 ${r.key.trim()}=`
                            ) : (
                              "值为空，保存时不会写入"
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </Section>

          {/* ---------------- e) SMTP ---------------- */}
          <Section
            id="smtp"
            num="05"
            title="邮件通知"
            description="供 PaperOrchestra 的完成通知使用，通过 Gmail SMTP 发送，密码请使用应用专用密码。可留空。"
          >
            <div className="card st-card">
              <div className="st-grid-2">
                <div className="field">
                  <Label htmlFor="st-smtp-email" text="发件邮箱" env="SMTP_EMAIL" />
                  <TextInput
                    id="st-smtp-email"
                    value={envCfg.smtpEmail}
                    onChange={(v) => setField("smtpEmail", v)}
                    placeholder="you@gmail.com"
                    inputMode="email"
                  />
                </div>
                <div className="field">
                  <Label htmlFor="st-smtp-pass" text="应用专用密码" env="SMTP_PASSWORD" />
                  <SecretInput
                    id="st-smtp-pass"
                    value={envCfg.smtpPassword}
                    onChange={(v) => setField("smtpPassword", v)}
                    show={show}
                  />
                </div>
              </div>
            </div>
          </Section>

          {/* ---------------- f) diagnostics ---------------- */}
          <Section
            id="diagnostics"
            num="06"
            title="诊断"
            description="以上次保存的配置为准。"
            actions={
              <button type="button" className="btn btn-sm" onClick={handleRecheck} disabled={checking}>
                <RefreshCw size={13} className={checking ? "spin" : undefined} /> 重新检测
              </button>
            }
          >
            <div className="card st-card">
              {status ? (
                <>
                  <div className="st-checks">
                    <CheckRow
                      ok={status.cliScriptExists}
                      label="paper_writing_cli.py"
                      detail={status.cliScriptExists ? "已找到" : "未找到"}
                    />
                    <CheckRow
                      ok={status.pythonExists}
                      label="Python 运行时"
                      detail={status.pythonVersion || "不可用"}
                    />
                    <CheckRow
                      ok={status.pdflatexAvailable}
                      label="pdflatex"
                      detail={status.pdflatexVersion || "未检测到"}
                    />
                    <CheckRow
                      ok={status.bibtexAvailable}
                      label="bibtex"
                      detail={status.bibtexAvailable ? "可用" : "未检测到"}
                    />
                    <CheckRow
                      ok={llmOk}
                      label="LLM 凭证"
                      detail={llmDetail}
                    />
                    <CheckRow
                      ok={status.templates.length > 0}
                      label="LaTeX 会议模板"
                      detail={status.templates.length > 0 ? `${status.templates.length} 个` : "无"}
                    />
                  </div>

                  <dl className="st-facts">
                    <div className="st-fact">
                      <dt>已配置的模型平台</dt>
                      <dd>
                        {(status.configuredProviders ?? []).length > 0 ? (
                          <span className="st-inline-list">
                            {status.configuredProviders.map((n) => (
                              <span key={n} className="badge badge-neutral" title={n}>
                                {providerLabel(n)}
                              </span>
                            ))}
                          </span>
                        ) : (
                          <span className="st-muted">无</span>
                        )}
                      </dd>
                    </div>
                    <div className="st-fact">
                      <dt>统一 auto 模型</dt>
                      <dd>
                        {status.autoModel ? (
                          <span className="st-code">{status.autoModel}</span>
                        ) : (
                          <span className="st-muted">未设置，按任务智能分工</span>
                        )}
                      </dd>
                    </div>
                    <div className="st-fact">
                      <dt>providers.json</dt>
                      <dd>
                        {status.providersJsonError ? (
                          <span className="st-text-danger">{status.providersJsonError}</span>
                        ) : (status.customProviders ?? []).length > 0 ? (
                          `${status.customProviders.length} 个自定义平台`
                        ) : (
                          <span className="st-muted">未使用</span>
                        )}
                      </dd>
                    </div>
                    <div className="st-fact">
                      <dt>文献检索来源</dt>
                      <dd>
                        {(status.scholarSources ?? []).length > 0 ? (
                          <span className="st-chain">
                            {status.scholarSources.map((id) => scholarSourceLabel(id)).join(" → ")}
                          </span>
                        ) : (
                          <span className="st-muted">无</span>
                        )}
                      </dd>
                    </div>
                    <div className="st-fact">
                      <dt>LaTeX 模板</dt>
                      <dd>{status.templates.join("、") || <span className="st-muted">无</span>}</dd>
                    </div>
                  </dl>
                </>
              ) : (
                <p className="hint">尚未完成环境检测。</p>
              )}
            </div>

            <div className="card st-card" aria-busy={connBusy !== null}>
              <div className="st-sub-head">
                <div>
                  <h3 className="st-subheading">连接测试</h3>
                  <p className="hint st-conn-hint">
                    「测试连接」向每个已配置的模型平台发送一次极小的请求（最多约 8 个输出
                    token，会产生极少量费用）；「测试文献源」在每个已启用的文献源检索一次，并遵守各自的限速。
                  </p>
                </div>
                <div className="row">
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => handleConnectionTest("providers")}
                    disabled={connBusy !== null}
                  >
                    {connBusy === "providers" ? (
                      <Loader2 size={13} className="spin" aria-hidden="true" />
                    ) : (
                      <PlugZap size={13} aria-hidden="true" />
                    )}
                    {connBusy === "providers" ? "测试中…" : "测试连接"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => handleConnectionTest("scholar")}
                    disabled={connBusy !== null}
                  >
                    {connBusy === "scholar" ? (
                      <Loader2 size={13} className="spin" aria-hidden="true" />
                    ) : (
                      <BookOpenCheck size={13} aria-hidden="true" />
                    )}
                    {connBusy === "scholar" ? "检索中…" : "测试文献源"}
                  </button>
                </div>
              </div>

              {dirty && (
                <Notice tone="warn" icon={<CircleAlert size={14} />}>
                  有尚未保存的更改；测试使用的是上次保存的配置。
                </Notice>
              )}

              <div className="st-conn-results" aria-live="polite">
                {connBusy && (
                  <p className="hint st-conn-running">
                    <Loader2 size={13} className="spin" aria-hidden="true" />
                    {connBusy === "providers"
                      ? "正在同时联系各已配置的模型平台，通常需要几秒到几十秒…"
                      : "正在同时检索各文献源（每个来源各发一次极小的查询）…"}
                  </p>
                )}
                {providerTest && (
                  <ConnTestBlock
                    title="模型平台"
                    result={providerTest}
                    okCount={providerTest.rows.filter((r) => r.configured && r.ok === true).length}
                    testedCount={providerTest.rows.filter((r) => r.configured && r.ok !== null).length}
                    busyCount={providerTest.rows.filter((r) => r.configured && r.ok === false && r.busy).length}
                  >
                    <ProviderCheckTable
                      rows={providerTest.rows}
                      pingModels={pingModels}
                      onPingModel={changePingModel}
                      fetched={fetchedModels}
                      busy={connBusy !== null}
                    />
                  </ConnTestBlock>
                )}
                {scholarTest && (
                  <ConnTestBlock
                    title="文献源"
                    result={scholarTest}
                    okCount={scholarTest.rows.filter((r) => r.ok === true).length}
                    testedCount={scholarTest.rows.filter((r) => r.ok !== null).length}
                    busyCount={scholarTest.rows.filter((r) => r.ok === false && r.busy).length}
                  >
                    <ScholarCheckTable rows={scholarTest.rows} />
                  </ConnTestBlock>
                )}
                {!providerTest && !scholarTest && !connBusy && (
                  <p className="hint">尚未测试。结果只显示在这里，不会写入任何文件。</p>
                )}
              </div>
            </div>
          </Section>

          {/* ---------------- save bar ---------------- */}
          <div
            className={`st-savebar${dirty ? " is-dirty" : ""}`}
            role="region"
            aria-label="保存"
          >
            <div className="st-savebar-status" aria-live="polite">
              {problemCount > 0 ? (
                <>
                  <CircleAlert size={15} className="st-text-danger" />
                  <span>
                    有 <span className="st-tnum">{problemCount}</span> 处需要修正
                  </span>
                  <button
                    type="button"
                    className="st-link"
                    onClick={() => scrollToNav(rowErrorCount > 0 ? "extra" : "models-custom")}
                  >
                    查看
                  </button>
                </>
              ) : dirty ? (
                <>
                  <span className="dot dot-warn" />
                  <span>
                    <span className="st-tnum">{changes.length}</span> 项更改尚未保存
                  </span>
                  <span className="hint">
                    {[...dirtySections].map((s) => SECTION_LABELS[s]).join(" · ")}
                  </span>
                </>
              ) : (
                <>
                  <CircleCheck size={15} className="st-text-success" />
                  <span className="st-muted">所有更改均已保存</span>
                </>
              )}
            </div>
            <div className="row">
              {dirty && (
                <button type="button" className="btn btn-sm" onClick={handleDiscard} disabled={saving}>
                  放弃更改
                </button>
              )}
              <button
                type="button"
                className={`btn${dirty ? " btn-primary" : ""}`}
                disabled={saving}
                onClick={handleSaveAll}
                title={`保存（${IS_MAC ? "⌘" : "Ctrl"} + S）`}
              >
                <Save size={14} />
                {saving ? "保存中…" : "保存"}
                <span className="kbd st-kbd">{IS_MAC ? "⌘S" : "Ctrl S"}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Building blocks
// ---------------------------------------------------------------------------

function officialChangeKeys(p: OfficialProvider): string[] {
  const keys: string[] = [p.keyField];
  if (p.baseUrlField) keys.push(p.baseUrlField);
  else keys.push(`env:${p.baseUrlEnv}`);
  if (p.id === "gemini") keys.push("vertexAiProject", "vertexAiLocation");
  return keys;
}

function Section({
  id,
  num,
  title,
  description,
  actions,
  children,
}: {
  id: string;
  num: string;
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={domId(id)} className="st-section" aria-labelledby={`${domId(id)}-title`}>
      <header className="st-section-head">
        <div className="st-section-titles">
          <h2 id={`${domId(id)}-title`} className="section-heading">
            <span className="st-num" aria-hidden="true">
              {num}
            </span>
            {title}
          </h2>
          {description && <p className="hint">{description}</p>}
        </div>
        {actions && <div className="st-section-actions">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

function SubSection({
  id,
  title,
  hint,
  actions,
  children,
}: {
  id: string;
  title: string;
  hint?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div id={domId(id)} className="st-sub">
      <div className="st-sub-head">
        <div>
          <h3 className="st-subheading">{title}</h3>
          {hint && <p className="hint">{hint}</p>}
        </div>
        {actions}
      </div>
      {children}
    </div>
  );
}

function Label({ htmlFor, text, env }: { htmlFor: string; text: string; env?: string }) {
  return (
    <label htmlFor={htmlFor} className="st-label">
      <span>{text}</span>
      {env && (
        <span className="st-env" title={env}>
          {env}
        </span>
      )}
    </label>
  );
}

interface InputProps {
  id: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mono?: boolean;
  list?: string;
  inputMode?: "text" | "email" | "decimal" | "url";
}

function TextInput({ id, value, onChange, placeholder, mono, list, inputMode }: InputProps) {
  return (
    <input
      id={id}
      className={`input${mono ? " st-mono" : ""}`}
      value={value}
      placeholder={placeholder}
      list={list}
      inputMode={inputMode}
      spellCheck={false}
      autoComplete="off"
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function SecretInput({
  id,
  value,
  onChange,
  placeholder,
  show,
}: Omit<InputProps, "mono" | "list" | "inputMode"> & { show: boolean }) {
  return (
    <input
      id={id}
      className="input st-mono"
      type={show ? "text" : "password"}
      value={value}
      placeholder={placeholder}
      spellCheck={false}
      autoComplete="off"
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function StatusBadge({ configured, edited }: { configured: boolean; edited: boolean }) {
  if (edited) return <span className="badge badge-warn">待保存</span>;
  return configured ? (
    <span className="badge badge-succeeded">
      <Check size={11} /> 已配置
    </span>
  ) : (
    <span className="badge badge-neutral">未配置</span>
  );
}

function StatusNote({ ok, text }: { ok: boolean; text: string }) {
  return (
    <span className={`st-status-note${ok ? " is-ok" : " is-bad"}`}>
      <span className={`dot ${ok ? "dot-ok" : "dot-err"}`} />
      {text}
    </span>
  );
}

function Notice({
  tone,
  icon,
  children,
}: {
  tone: "danger" | "success" | "warn";
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={`st-notice st-notice-${tone}`} role={tone === "danger" ? "alert" : "status"}>
      {icon}
      <span>{children}</span>
    </div>
  );
}

function OfficialCard({
  p,
  env,
  altKeyInUse,
  setField,
  setExtra,
  show,
  status,
  edited,
}: {
  p: OfficialProvider;
  env: EnvSecretsConfig;
  /** An alternate key variable (e.g. KIMI_API_KEY) that currently supplies the key. */
  altKeyInUse?: string;
  setField: (f: ScalarField, v: string) => void;
  setExtra: (k: string, v: string) => void;
  show: boolean;
  status: EnvironmentStatus | null;
  edited: boolean;
}) {
  const keyField = p.keyField as ScalarField;
  const baseField = p.baseUrlField as ScalarField | undefined;
  const baseValue = baseField ? env[baseField] : env.extraEnv[p.baseUrlEnv] ?? "";
  const setBase = (v: string) => (baseField ? setField(baseField, v) : setExtra(p.baseUrlEnv, v));
  return (
    <article className="st-ocard">
      <header className="st-pcard-head">
        <div className="st-pcard-title">
          <span className="st-pcard-name">{p.label}</span>
          <span className="st-pcard-id">{p.id}</span>
        </div>
        <div className="row">
          {p.id === "gemini" && status?.hasVertexAi && !edited && (
            <span className="badge badge-neutral">Vertex AI</span>
          )}
          <StatusBadge configured={officialConfigured(p.id, status)} edited={edited} />
        </div>
      </header>

      <div className="st-ocard-fields">
        <div className="field">
          <Label htmlFor={`st-${p.id}-key`} text="API Key" env={p.keyEnv} />
          <SecretInput
            id={`st-${p.id}-key`}
            value={env[keyField]}
            onChange={(v) => setField(keyField, v)}
            placeholder={p.keyPlaceholder}
            show={show}
          />
          {altKeyInUse && (
            <p className="hint">
              当前使用「其他环境变量」中的 <span className="st-code">{altKeyInUse}</span>
            </p>
          )}
        </div>
        <div className="field">
          <Label htmlFor={`st-${p.id}-base`} text="Base URL（可选）" env={p.baseUrlEnv} />
          <TextInput
            id={`st-${p.id}-base`}
            value={baseValue}
            onChange={setBase}
            placeholder={p.defaultBaseUrl ? `默认 ${p.defaultBaseUrl}` : "留空使用官方地址"}
            inputMode="url"
            mono
          />
        </div>
        {p.id === "gemini" && (
          <>
            <div className="field">
              <Label htmlFor="st-vertex-project" text="Vertex 项目" env="VERTEX_AI_PROJECT" />
              <TextInput
                id="st-vertex-project"
                value={env.vertexAiProject}
                onChange={(v) => setField("vertexAiProject", v)}
                placeholder="可选"
                mono
              />
            </div>
            <div className="field">
              <Label htmlFor="st-vertex-location" text="Vertex 区域" env="VERTEX_AI_LOCATION" />
              <TextInput
                id="st-vertex-location"
                value={env.vertexAiLocation}
                onChange={(v) => setField("vertexAiLocation", v)}
                placeholder="例如 us-central1"
                mono
              />
            </div>
          </>
        )}
      </div>

      <footer className="st-pcard-foot">
        <span>
          示例 <span className="st-code">{p.exampleRef}</span>
        </span>
        {p.note && <span>{p.note}</span>}
      </footer>
    </article>
  );
}

function PresetCard({
  p,
  extra,
  setExtra,
  show,
  configured,
  edited,
  open,
  onToggle,
}: {
  p: PresetProvider;
  extra: Record<string, string>;
  setExtra: (k: string, v: string) => void;
  show: boolean;
  configured: boolean;
  edited: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const keyEnv = activeKeyEnv(p, extra);
  const otherKeyEnvs = [p.keyEnv, ...p.altKeyEnvs].filter((k) => k !== keyEnv);
  const exampleModel = p.examplePlaceholder ? "" : p.exampleRef.slice(p.exampleRef.indexOf(":") + 1);
  const baseValue = extra[p.baseUrlEnv] ?? "";
  const moreId = `st-${p.id}-more`;

  const keyField = (
    <div className="field">
      <Label
        htmlFor={`st-${p.id}-key`}
        text={p.keyOptional ? "API Key（可选）" : "API Key"}
        env={keyEnv}
      />
      <SecretInput
        id={`st-${p.id}-key`}
        value={extra[keyEnv] ?? ""}
        onChange={(v) => setExtra(keyEnv, v)}
        show={show}
      />
    </div>
  );

  const baseField = (
    <div className="field">
      <Label
        htmlFor={`st-${p.id}-base`}
        text={p.keyOptional ? "服务地址" : "Base URL（可选）"}
        env={p.baseUrlEnv}
      />
      <div className="st-inline">
        <TextInput
          id={`st-${p.id}-base`}
          value={baseValue}
          onChange={(v) => setExtra(p.baseUrlEnv, v)}
          placeholder={`默认 ${p.defaultBaseUrl}`}
          inputMode="url"
          mono
        />
        {p.keyOptional && !baseValue && (
          <button
            type="button"
            className="btn btn-sm"
            title={`填入 ${p.defaultBaseUrl}`}
            onClick={() => setExtra(p.baseUrlEnv, p.defaultBaseUrl)}
          >
            用默认
          </button>
        )}
      </div>
    </div>
  );

  return (
    <article className={`st-pcard${configured ? " is-configured" : ""}`}>
      <header className="st-pcard-head">
        <div className="st-pcard-title">
          <span className="st-pcard-name">{p.label}</span>
          <span className="st-pcard-id">{p.id}</span>
        </div>
        <StatusBadge configured={configured} edited={edited} />
      </header>
      <p className="hint">{p.blurb}</p>

      {p.keyOptional ? baseField : keyField}

      <button
        type="button"
        className="st-disclosure"
        aria-expanded={open}
        aria-controls={moreId}
        onClick={onToggle}
      >
        <ChevronRight size={13} className="st-details-caret" />
        {p.keyOptional ? "密钥与默认模型" : "Base URL 与默认模型"}
      </button>

      {open && (
        <div id={moreId} className="st-pcard-more">
          {p.keyOptional ? keyField : baseField}
          {otherKeyEnvs.length > 0 && (
            <p className="hint st-alt">密钥也可写在 {otherKeyEnvs.join(" / ")}</p>
          )}
          <div className="field">
            <Label htmlFor={`st-${p.id}-model`} text="默认模型" env={p.defaultModelEnv} />
            <TextInput
              id={`st-${p.id}-model`}
              value={extra[p.defaultModelEnv] ?? ""}
              onChange={(v) => setExtra(p.defaultModelEnv, v)}
              placeholder={
                p.defaultModel ? `默认 ${p.defaultModel}` : exampleModel ? `例如 ${exampleModel}` : "平台上的模型名"
              }
              mono
            />
            <p className="hint">
              {p.defaultModel ? "没有官方接口密钥时，auto 会回退到这个模型。" : "auto 回退到该平台前需要先填写。"}
            </p>
          </div>
        </div>
      )}

      <footer className="st-pcard-foot">
        <span>
          示例 <span className="st-code">{p.exampleRef}</span>
        </span>
      </footer>
    </article>
  );
}

function SourceRow({
  src,
  on,
  rank,
  isFirst,
  isLast,
  onToggle,
  onMove,
  env,
  setField,
  show,
  warn,
}: {
  src: ScholarSource;
  on: boolean;
  rank: number | null;
  isFirst: boolean;
  isLast: boolean;
  onToggle: () => void;
  onMove: (delta: -1 | 1) => void;
  env: EnvSecretsConfig;
  setField: (f: ScalarField, v: string) => void;
  show: boolean;
  warn: string;
}) {
  return (
    <li className={`st-source${on ? "" : " is-off"}`}>
      <div className="st-source-head">
        <span className="st-source-rank st-tnum" aria-hidden="true">
          {rank ?? "–"}
        </span>
        <button
          type="button"
          className={`chip${on ? " on" : ""}`}
          aria-pressed={on}
          onClick={onToggle}
          title={on ? "点击停用" : "点击启用"}
        >
          {on && <Check size={12} />}
          {src.label}
        </button>
        <span className="st-source-meta">
          <span className="st-source-disc">{src.discipline}</span>
          <span className="st-source-rate">{src.rateNote}</span>
          {warn && <span className="badge badge-warn">{warn}</span>}
        </span>
        <span className="st-source-move">
          <button
            type="button"
            className="btn btn-sm st-icon-btn"
            aria-label={`上移 ${src.label}`}
            disabled={isFirst}
            onClick={() => onMove(-1)}
          >
            <ArrowUp size={13} />
          </button>
          <button
            type="button"
            className="btn btn-sm st-icon-btn"
            aria-label={`下移 ${src.label}`}
            disabled={isLast}
            onClick={() => onMove(1)}
          >
            <ArrowDown size={13} />
          </button>
        </span>
      </div>
      {src.fields.length > 0 && (
        <div className="st-source-fields">
          {src.fields.map((f) => {
            const id = `st-src-${f.env.toLowerCase()}`;
            const field = f.field as ScalarField;
            return (
              <div key={f.env} className="field">
                <Label htmlFor={id} text={f.label} env={f.env} />
                {f.secret ? (
                  <SecretInput
                    id={id}
                    value={env[field]}
                    onChange={(v) => setField(field, v)}
                    placeholder={f.placeholder}
                    show={show}
                  />
                ) : (
                  <TextInput
                    id={id}
                    value={env[field]}
                    onChange={(v) => setField(field, v)}
                    placeholder={f.placeholder}
                    inputMode={f.inputMode}
                    mono
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </li>
  );
}

function CheckPill({
  ok,
  configured = true,
  busy = false,
}: {
  ok: boolean | null;
  configured?: boolean;
  busy?: boolean;
}) {
  if (ok === false && busy)
    return (
      <span className="badge badge-warn" title="对方暂时限流或繁忙，不是配置错误；稍后再测即可">
        <CircleAlert size={11} aria-hidden="true" /> 暂时繁忙
      </span>
    );
  if (ok === true)
    return (
      <span className="badge badge-succeeded">
        <Check size={11} aria-hidden="true" /> 正常
      </span>
    );
  if (ok === false)
    return (
      <span className="badge badge-failed">
        <CircleX size={11} aria-hidden="true" /> 失败
      </span>
    );
  return <span className="badge badge-neutral">{configured ? "未测试" : "未配置"}</span>;
}

function ConnTestBlock<T>({
  title,
  result,
  okCount,
  testedCount,
  busyCount = 0,
  children,
}: {
  title: string;
  result: ConnTestResult<T>;
  okCount: number;
  testedCount: number;
  /** Rows that answered "try later" (shown apart from real failures). */
  busyCount?: number;
  children: ReactNode;
}) {
  const time = result.finishedAt.toLocaleTimeString("zh-CN", { hour12: false });
  return (
    <section className="st-conn-block" aria-label={`${title}测试结果`}>
      <div className="st-conn-head">
        <h4 className="st-conn-title">{title}</h4>
        <span className="st-conn-meta">
          {!result.error && testedCount > 0 && (
            <>
              <span className={okCount === testedCount ? "st-text-success" : undefined}>
                {okCount}/{testedCount} 正常
              </span>
              {busyCount > 0 && (
                <>
                  <span aria-hidden="true"> · </span>
                  <span className="st-text-warn">{busyCount} 个暂时繁忙</span>
                </>
              )}
              <span aria-hidden="true"> · </span>
            </>
          )}
          测试于 {time} · 用时 {(result.elapsedMs / 1000).toFixed(1)} 秒
        </span>
      </div>
      {result.error ? (
        <Notice tone="danger" icon={<CircleX size={14} />}>
          {result.error}
        </Notice>
      ) : (
        children
      )}
    </section>
  );
}

function ProviderCheckTable({
  rows,
  pingModels,
  onPingModel,
  fetched,
  busy,
}: {
  rows: ProviderCheckResult[];
  pingModels: PingModels;
  onPingModel: (name: string, model: string) => void;
  fetched: FetchedModels | null;
  busy: boolean;
}) {
  const configured = rows.filter((r) => r.configured);
  // The platform default seen in this result (rows tested with a 测试模型 do not show it).
  const defaults = useRef<Record<string, string>>({});
  for (const r of configured) {
    if (r.model_source !== "test" && r.model) defaults.current[r.name] = r.model;
  }
  const choices = (name: string): string[] => {
    const row = fetched?.rows.find((f) => f.name === name && f.ok);
    return (row?.models ?? []).map((ref) => bareModel(name, ref)).slice(0, 200);
  };
  const others = rows.length - configured.length;
  if (configured.length === 0) {
    return (
      <p className="hint">
        没有已配置的模型平台。请先在「模型服务」中填写密钥并保存。
        {others > 0 && `（检测脚本识别了 ${others} 个未配置的平台。）`}
      </p>
    );
  }
  return (
    <>
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">平台</th>
              <th scope="col">测试模型</th>
              <th scope="col">状态</th>
              <th scope="col" className="is-num">延迟</th>
              <th scope="col">说明</th>
            </tr>
          </thead>
          <tbody>
            {configured.map((r) => {
              const label = providerLabel(r.name);
              const name = label !== r.name ? label : r.label || r.name;
              // A 测试模型 was typed but this result used the platform default
              // (typed after the test, or not a usable model name).
              const typed = pingModels[r.name];
              const ignored = !!typed && r.ok !== null && r.model_source !== "test";
              return (
                <tr key={r.name}>
                  <td className="is-nowrap" title={r.name}>
                    {name}
                  </td>
                  <td className="is-mono st-ping-model">
                    <input
                      className="input st-mono st-ping-input"
                      aria-label={`${name} 的测试模型`}
                      value={pingModels[r.name] ?? ""}
                      placeholder={defaults.current[r.name] ?? r.model ?? "平台默认模型"}
                      title="只改「测试连接」用的模型；清空则恢复平台默认。写论文用的模型不受影响。"
                      list={`st-ping-${r.name}`}
                      spellCheck={false}
                      autoComplete="off"
                      disabled={busy}
                      aria-invalid={ignored || undefined}
                      onChange={(e) => onPingModel(r.name, e.target.value)}
                    />
                    <datalist id={`st-ping-${r.name}`}>
                      {choices(r.name).map((m) => (
                        <option key={m} value={m} />
                      ))}
                    </datalist>
                  </td>
                  <td className="is-nowrap">
                    <CheckPill ok={r.ok} configured={r.configured} busy={r.busy} />
                  </td>
                  <td className="is-num">{formatLatency(r.latency_ms)}</td>
                  <td
                    className={
                      ignored ? "is-warn" : r.ok === false ? (r.busy ? "is-warn" : "is-danger") : "is-muted"
                    }
                  >
                    <span className="st-conn-error" title={r.error ?? undefined}>
                      {ignored
                        ? `这次是用平台默认的 ${r.model ?? "模型"} 测的，不是「${typed}」：改完请再点一次「测试连接」；` +
                          `模型名不能含空格、不能以 - 开头，也不能是 auto。` +
                          (r.ok === true ? " 默认模型连接正常。" : r.error ? ` 默认模型：${r.error}` : "")
                        : r.error ?? (r.ok === true ? "连接正常" : "—")}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="hint">
        每个平台用「测试模型」发一次极小的请求（最多 8 个输出 token）。想换一个模型测（例如免费档没有额度时换成 flash 版），
        直接改那一格再点「测试连接」；清空即恢复平台默认。这里只影响测试，项目里选用的模型不变。
        {others > 0 && ` 另有 ${others} 个平台未配置，未参与测试。`}
      </p>
    </>
  );
}

function ScholarCheckTable({ rows }: { rows: ScholarCheckResult[] }) {
  if (rows.length === 0) {
    return <p className="hint">没有启用的文献源。请在「文献检索」中至少启用一个来源并保存。</p>;
  }
  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">文献源</th>
            <th scope="col">状态</th>
            <th scope="col" className="is-num">延迟</th>
            <th scope="col" className="is-num">结果</th>
            <th scope="col">说明</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <td className="is-nowrap" title={r.name}>
                {scholarSourceLabel(r.name)}
              </td>
              <td className="is-nowrap">
                <CheckPill ok={r.ok} busy={r.busy} />
              </td>
              <td className="is-num">{formatLatency(r.latency_ms)}</td>
              <td className="is-num">{r.hits === null ? "—" : `${formatInt(r.hits)} 条`}</td>
              <td className={r.ok === false ? (r.busy ? "is-warn" : "is-danger") : "is-muted"}>
                <span className="st-conn-error" title={r.error ?? undefined}>
                  {r.error ?? (r.ok === true ? "检索正常" : "—")}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CheckRow({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <div className={`st-check${ok ? " is-ok" : " is-bad"}`}>
      <span className="st-check-label">
        {ok ? <CircleCheck size={15} /> : <CircleX size={15} />}
        {label}
      </span>
      <span className="st-check-detail" title={detail}>
        {detail}
      </span>
    </div>
  );
}
