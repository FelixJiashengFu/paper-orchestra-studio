/**
 * Defensive readers for the JSON reports the Python pipeline writes into a run
 * folder (usage_report.json, citation_audit.json) and for the connection-check
 * report. Every field is optional on the way in: a missing or malformed value
 * falls back to a neutral default, so a report from a newer or older Python
 * version never breaks the view. A file that is not a JSON object yields null.
 */
import type {
  CitationAudit,
  CitationAuditEntry,
  CitationStatus,
  ConnectionCheckReport,
  ProviderCheckResult,
  ScholarCheckResult,
  UsageCost,
  UsageCounts,
  UsageModelRow,
  UsageReport,
  UsageRowCost,
} from "./types";
import { USAGE_DETAIL_KEYS } from "./types";

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);

function toNumber(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

const num = (v: unknown): number => Math.max(0, toNumber(v) ?? 0);
const str = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));
const strOrNull = (v: unknown): string | null => {
  const s = typeof v === "string" ? v.trim() : "";
  return s ? s : null;
};
const strList = (v: unknown): string[] =>
  Array.isArray(v) ? v.map(str).filter((s) => s.trim() !== "") : [];

function parseObject(text: string): Obj | null {
  try {
    const raw: unknown = JSON.parse(text);
    return isObj(raw) ? raw : null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// usage_report.json
// ---------------------------------------------------------------------------

function counts(o: unknown): UsageCounts {
  const r = isObj(o) ? o : {};
  return {
    calls: num(r.calls),
    errors: num(r.errors),
    input_tokens: num(r.input_tokens),
    output_tokens: num(r.output_tokens),
  };
}

function rowCost(o: unknown): UsageRowCost | undefined {
  if (!isObj(o)) return undefined;
  const cad = toNumber(o.cad);
  if (cad === null || cad < 0) return undefined;
  return {
    cad,
    amount: num(o.amount),
    currency: str(o.currency) || "USD",
    source: str(o.source),
    price_as_of: str(o.price_as_of),
  };
}

function numberMap(o: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (!isObj(o)) return out;
  for (const [k, v] of Object.entries(o)) {
    const n = toNumber(v);
    if (n !== null && n >= 0) out[k] = n;
  }
  return out;
}

function stringMap(o: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!isObj(o)) return out;
  for (const [k, v] of Object.entries(o)) if (typeof v === "string") out[k] = v;
  return out;
}

function reportCost(o: unknown): UsageCost | undefined {
  if (!isObj(o)) return undefined;
  const total = toNumber(o.total);
  if (total === null || total < 0) return undefined;
  const fx = isObj(o.fx) ? o.fx : {};
  const prices = isObj(o.prices) ? o.prices : {};
  return {
    currency: str(o.currency) || "CAD",
    total,
    by_currency: numberMap(o.by_currency),
    fx: { rates: numberMap(fx.rates), date: str(fx.date), source: str(fx.source) },
    prices: {
      builtin_checked: str(prices.builtin_checked),
      feeds_fetched: stringMap(prices.feeds_fetched),
      sources: strList(prices.sources),
    },
    unpriced_models: strList(o.unpriced_models),
    basis: str(o.basis),
  };
}

function modelRow(r: Obj): UsageModelRow {
  const row: UsageModelRow = { provider: str(r.provider), model: str(r.model), ...counts(r) };
  for (const k of USAGE_DETAIL_KEYS) {
    if (r[k] !== undefined) row[k] = num(r[k]);
  }
  const cost = rowCost(r.cost);
  if (cost) row.cost = cost;
  return row;
}

export function parseUsageReport(text: string): UsageReport | null {
  const raw = parseObject(text);
  if (!raw) return null;
  const byModel: UsageModelRow[] = Array.isArray(raw.by_model)
    ? raw.by_model.filter(isObj).map(modelRow)
    : [];
  const totals = isObj(raw.totals)
    ? counts(raw.totals)
    : byModel.reduce<UsageCounts>(
        (acc, r) => ({
          calls: acc.calls + r.calls,
          errors: acc.errors + r.errors,
          input_tokens: acc.input_tokens + r.input_tokens,
          output_tokens: acc.output_tokens + r.output_tokens,
        }),
        { calls: 0, errors: 0, input_tokens: 0, output_tokens: 0 }
      );
  const report: UsageReport = { generated_at: str(raw.generated_at), totals, by_model: byModel };
  const cost = reportCost(raw.cost);
  if (cost) report.cost = cost;
  return report;
}

export const totalTokens = (c: UsageCounts): number => c.input_tokens + c.output_tokens;

/** "C$1.23"; amounts under a cent keep two significant digits ("C$0.0042"). */
export function formatCad(v: number): string {
  return formatMoney(v, "CAD");
}

const CURRENCY_SIGN: Record<string, string> = { USD: "US$", CNY: "¥", EUR: "€", CAD: "C$" };

/** "US$1.20" / "¥3.40" / "C$0.0042" in the given currency. */
export function formatMoney(amount: number, currency: string): string {
  const sign = CURRENCY_SIGN[currency] ?? `${currency} `;
  if (!Number.isFinite(amount) || amount <= 0) return `${sign}0.00`;
  if (amount < 0.01) return `${sign}${Number(amount.toPrecision(2))}`;
  return `${sign}${amount.toLocaleString("en-CA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** Where a row's price came from, for a tooltip. */
export function priceSourceLabel(source: string): string {
  if (source === "official") return "官方标价（内置价目表）";
  if (source.startsWith("official:")) return `按 ${source.slice(9)} 官方标价估算（中转或自定义平台）`;
  if (source === "litellm") return "LiteLLM 公开价目表（每日更新）";
  if (source === "openrouter") return "OpenRouter 公开价目表（每日更新）";
  return source || "—";
}

// ---------------------------------------------------------------------------
// citation_audit.json
// ---------------------------------------------------------------------------

const STATUSES: CitationStatus[] = ["verified", "metadata_only", "unverified"];

function entry(r: Obj): CitationAuditEntry {
  const status = STATUSES.includes(r.status as CitationStatus)
    ? (r.status as CitationStatus)
    : "unverified";
  const year = toNumber(r.year);
  return {
    key: str(r.key),
    title: str(r.title),
    year: year === null ? null : Math.trunc(year),
    doi: strOrNull(r.doi),
    url: strOrNull(r.url),
    source: strOrNull(r.source),
    cited: r.cited === true,
    status,
  };
}

export function parseCitationAudit(text: string): CitationAudit | null {
  const raw = parseObject(text);
  if (!raw) return null;
  const entries = Array.isArray(raw.entries)
    ? raw.entries.filter(isObj).map(entry).filter((e) => e.key !== "")
    : [];
  const missing = strList(raw.missing_in_bib);
  const uncited = strList(raw.uncited);
  const s = isObj(raw.summary) ? raw.summary : {};
  const pick = (k: string, fallback: number) => (toNumber(s[k]) === null ? fallback : num(s[k]));
  return {
    generated_at: str(raw.generated_at),
    tex_file: str(raw.tex_file),
    bib_file: str(raw.bib_file),
    cited_bib_file: str(raw.cited_bib_file),
    summary: {
      cited: pick("cited", entries.filter((e) => e.cited).length + missing.length),
      bib_entries: pick("bib_entries", entries.length),
      // Older audits do not have it: the cited entries are exactly those.
      in_bibliography: pick("in_bibliography", entries.filter((e) => e.cited).length),
      missing_in_bib: pick("missing_in_bib", missing.length),
      uncited: pick("uncited", uncited.length),
      with_doi: pick("with_doi", entries.filter((e) => e.doi).length),
      verified: pick("verified", entries.filter((e) => e.status === "verified").length),
    },
    missing_in_bib: missing,
    uncited,
    entries,
  };
}

/**
 * The web address to open for an entry: its DOI resolver link, else its URL.
 * Only http(s) is returned, so a value from the file can never open a local
 * path or another URL scheme.
 */
export function entryLink(e: Pick<CitationAuditEntry, "doi" | "url">): string | null {
  if (e.doi) {
    const doi = e.doi
      .trim()
      .replace(/^https?:\/\/(dx\.)?doi\.org\//i, "")
      .replace(/^doi:\s*/i, "");
    if (/^10\.\d{4,9}\/\S+$/.test(doi)) return `https://doi.org/${encodeURI(doi)}`;
  }
  if (e.url && /^https?:\/\/[^\s]+$/i.test(e.url.trim())) return e.url.trim();
  return null;
}

// ---------------------------------------------------------------------------
// check_connections
// ---------------------------------------------------------------------------

export function normalizeConnectionReport(raw: unknown): ConnectionCheckReport {
  const r = isObj(raw) ? raw : {};
  const ok = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
  const ms = (v: unknown): number | null => {
    const n = toNumber(v);
    return n === null || n < 0 ? null : Math.round(n);
  };
  const providers: ProviderCheckResult[] = Array.isArray(r.providers)
    ? r.providers.filter(isObj).map((p) => ({
        name: str(p.name),
        label: str(p.label),
        kind: str(p.kind),
        configured: p.configured === true,
        model: strOrNull(p.model),
        model_source: str(p.model_source) || "default",
        ok: ok(p.ok),
        busy: p.busy === true,
        latency_ms: ms(p.latency_ms),
        error: strOrNull(p.error),
      }))
    : [];
  const scholar: ScholarCheckResult[] = Array.isArray(r.scholar)
    ? r.scholar.filter(isObj).map((p) => {
        const hits = toNumber(p.hits);
        return {
          name: str(p.name),
          ok: ok(p.ok),
          busy: p.busy === true,
          latency_ms: ms(p.latency_ms),
          hits: hits === null ? null : Math.max(0, Math.trunc(hits)),
          error: strOrNull(p.error),
        };
      })
    : [];
  return { providers, scholar };
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

const INT = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });

export const formatInt = (n: number): string => INT.format(n);

/** 1234 -> "1,234 ms", 15400 -> "15.4 s". */
export function formatLatency(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 10_000) return `${INT.format(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}
