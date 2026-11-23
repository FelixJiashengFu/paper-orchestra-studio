"""API spend estimate in Canadian dollars, from the token counts the platforms report.

Nothing here calls a model. The price of each call comes from:

1. BUILTIN_PRICES: list prices read from each platform's official pricing page
   (date in `as_of`), in the platform's own currency. Dated price changes are
   built in (e.g. Gemini 3.8 Flash doubles on 2027-01-01).
2. For models not in that table: the community LiteLLM price file and, for
   OpenRouter, OpenRouter's own model list (both free, no key), fetched at
   most once a day and cached in ~/.cache/paper-orchestra/pricing.json.

Exchange rates: the Bank of Canada's daily rates (Valet API, free, no key),
also refreshed once a day; the cache or a built-in fallback is used offline.

The result is an estimate at list price: free tiers, free search quotas,
discounts, credits and taxes are not taken into account. Every public
function swallows its own errors (it must never break a run).
"""

import datetime
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

PRICES_CHECKED = "2026-11-19"  # when BUILTIN_PRICES was last checked against the official pages


@dataclass(frozen=True)
class Price:
    """Per million tokens, in `currency`. None = not charged separately (falls
    back to the input / output price)."""

    currency: str
    input: float
    output: float
    cache_read: Optional[float] = None
    cache_write: Optional[float] = None  # 5-minute / default cache writes
    cache_write_1h: Optional[float] = None
    long_threshold: Optional[int] = None  # prompt tokens above which long_* apply to the whole call
    long_input: Optional[float] = None
    long_output: Optional[float] = None
    image_output: Optional[float] = None  # generated image tokens
    per_image: Optional[float] = None  # /images endpoints without token usage
    search_per_1k: Optional[float] = None  # web search tool, per 1000 queries
    source: str = ""  # URL of the official pricing page
    as_of: str = PRICES_CHECKED
    starts: str = ""  # "YYYY-MM-DD": the price applies from this date (dated changes)


def _claude(inp, out, cache_read, **kw) -> Price:
    # Anthropic: cache writes 1.25x input (5-minute TTL), 2x (1-hour TTL).
    return Price("USD", inp, out, cache_read=cache_read, cache_write=inp * 1.25, cache_write_1h=inp * 2,
                 source="https://platform.claude.com/docs/en/about-claude/pricing", **kw)


_GEMINI = "https://ai.google.dev/gemini-api/docs/pricing"
_OPENAI = "https://openai.com/api/pricing"
_DEEPSEEK = "https://api-docs.deepseek.com/quick_start/pricing"

# provider -> model id (lower case) -> one Price, or a list of dated Prices
# (oldest first; the last one whose `starts` has passed applies).
BUILTIN_PRICES: Dict[str, Dict[str, Any]] = {
    "anthropic": {
        "claude-fable-5-2": _claude(8, 40, 0.20),
        "claude-mythos-5-2": _claude(8, 40, 0.20),
        "claude-fable-5-1": _claude(10, 50, 0.25),
        "claude-mythos-5-1": _claude(10, 50, 0.25),
        "claude-fable-5": _claude(10, 50, 1.0),
        "claude-mythos-5": _claude(10, 50, 1.0),
        "claude-opus-5-5": _claude(4, 20, 0.20),
        "claude-opus-5": _claude(5, 25, 0.50),
        "claude-opus-4-8": _claude(5, 25, 0.50),
        "claude-opus-4-7": _claude(5, 25, 0.50),
        "claude-opus-4-6": _claude(5, 25, 0.50),
        "claude-opus-4-5": _claude(5, 25, 0.50),
        "claude-sonnet-5-5": _claude(2, 10, 0.10),
        "claude-sonnet-5": _claude(2, 10, 0.20),
        "claude-sonnet-4-6": _claude(3, 15, 0.30),
        "claude-sonnet-4-5": _claude(3, 15, 0.30),
        "claude-haiku-5-5": _claude(0.10, 0.50, 0.01, long_threshold=100_000, long_input=0.50, long_output=2.50),
        "claude-haiku-4-5": _claude(1, 5, 0.10),
    },
    "gemini": {
        # Search grounding: $14 per 1000 queries after 5000 free a month
        # (shared by Gemini 3 and newer); the free quota is not deducted here.
        "gemini-4-hydrogen": Price("USD", 1.50, 7.50, search_per_1k=14, source=_GEMINI),
        "gemini-4-argon": [
            Price("USD", 2.0, 10.0, cache_read=0.10, search_per_1k=14, source=_GEMINI),
            Price("USD", 4.0, 20.0, cache_read=0.20, search_per_1k=14, source=_GEMINI, starts="2026-12-01"),
        ],
        "gemini-3.8-flash": [
            Price("USD", 0.75, 3.75, cache_read=0.075, search_per_1k=14, source=_GEMINI),
            Price("USD", 1.50, 7.50, cache_read=0.15, search_per_1k=14, source=_GEMINI, starts="2027-01-01"),
        ],
        "gemini-3.1-pro-preview": Price("USD", 2.0, 12.0, cache_read=0.20, long_threshold=200_000,
                                        long_input=4.0, long_output=18.0, search_per_1k=14, source=_GEMINI),
        "gemini-3-flash-preview": Price("USD", 0.50, 3.0, cache_read=0.05, search_per_1k=14, source=_GEMINI),
        # Image models: generated images are output tokens at the image rate
        # (1120 tokens for a 1K/2K image on 3 Pro Image: about $0.134).
        "gemini-3-pro-image": Price("USD", 2.0, 12.0, image_output=120.0, per_image=0.134, source=_GEMINI),
        "gemini-3.1-flash-image": Price("USD", 0.50, 3.0, image_output=60.0, per_image=0.067, source=_GEMINI),
    },
    "deepseek": {
        # DeepSeek's web search / read_url tools bill only the tokens.
        "deepseek-flash": Price("USD", 0.30, 1.20, cache_read=0.006, search_per_1k=0, source=_DEEPSEEK),
        "deepseek-v4-flash-vision-exp": Price("USD", 0.30, 1.20, cache_read=0.006, source=_DEEPSEEK),
        "deepseek-v4-pro": Price("USD", 1.32, 3.96, cache_read=0.044, search_per_1k=0, source=_DEEPSEEK),
    },
    "openai": {
        "gpt-6.2-astra": Price("USD", 8, 40, cache_read=0.40, cache_write=10, long_threshold=272_000,
                               long_input=16, long_output=60, search_per_1k=10, source=_OPENAI),
        "gpt-6.2-sol": Price("USD", 2, 10, cache_read=0.10, cache_write=2.5, long_threshold=272_000,
                             long_input=4, long_output=15, search_per_1k=10, source=_OPENAI),
        "gpt-6.1-terra": Price("USD", 1, 5, cache_read=0.05, cache_write=1.25, long_threshold=272_000,
                               long_input=2, long_output=7.5, search_per_1k=10, source=_OPENAI),
        "gpt-6-luna": Price("USD", 0.10, 0.50, cache_read=0.01, cache_write=0.125, long_threshold=272_000,
                            long_input=0.20, long_output=0.75, search_per_1k=10, source=_OPENAI),
        "gpt-5.5": Price("USD", 5, 30, cache_read=0.50, long_threshold=272_000, long_input=10, long_output=45,
                         source=_OPENAI),
        "gpt-4.1": Price("USD", 2, 8, cache_read=0.50, source=_OPENAI),
        "gpt-4o": Price("USD", 2.5, 10, cache_read=1.25, source=_OPENAI),
        "gpt-image-1": Price("USD", 5, 40, cache_read=1.25, image_output=40, per_image=0.042, source=_OPENAI),
    },
    "xai": {
        "grok-4.7": Price("USD", 2, 6, cache_read=0.50, long_threshold=200_000, long_input=4, long_output=12,
                          search_per_1k=5, source="https://docs.x.ai/docs/models"),
        "grok-4.5": Price("USD", 2, 6, cache_read=0.30, long_threshold=200_000, long_input=4, long_output=12,
                          search_per_1k=5, source="https://docs.x.ai/docs/models"),
        "grok-4.3": Price("USD", 1.25, 2.5, cache_read=0.20, long_threshold=200_000, long_input=2.5,
                          long_output=5, search_per_1k=5, source="https://docs.x.ai/docs/models"),
    },
    "mistral": {
        "mistral-large-latest": Price("USD", 0.70, 2.10, cache_read=0.07, source="https://mistral.ai/pricing"),
    },
    # Mainland list prices in CNY (yuan per million tokens).
    "qwen": {
        "qwen4-max": Price("CNY", 10, 30, cache_read=0.5, cache_write=12.5,
                           source="https://help.aliyun.com/zh/model-studio/models"),
        "qwen3.8-max": Price("CNY", 12, 36, source="https://help.aliyun.com/zh/model-studio/models"),
        "qwen-max": Price("CNY", 2.4, 9.6, source="https://help.aliyun.com/zh/model-studio/models"),
        "qwen-plus": Price("CNY", 0.8, 2, long_threshold=128_000, long_input=2.4, long_output=20,
                           source="https://help.aliyun.com/zh/model-studio/models"),
        "qwen3.7-plus": Price("CNY", 2, 8, long_threshold=256_000, long_input=6, long_output=24,
                              source="https://help.aliyun.com/zh/model-studio/models"),
        "qwen3-vl-plus": Price("CNY", 1, 10, cache_read=0.2, cache_write=1.25, long_threshold=32_000,
                               long_input=1.5, long_output=15,
                               source="https://help.aliyun.com/zh/model-studio/models"),
    },
    "moonshot": {
        "kimi-k2.6": Price("CNY", 6.5, 27, cache_read=1.1, source="https://platform.moonshot.cn/docs/pricing/chat"),
    },
    "zhipu": {
        "glm-5": Price("CNY", 4, 18, cache_read=1, long_threshold=32_000, long_input=6, long_output=22,
                       search_per_1k=10, source="https://open.bigmodel.cn/pricing"),
        "glm-5.3": Price("CNY", 8, 28, cache_read=2, source="https://open.bigmodel.cn/pricing"),
        "glm-4-plus": Price("CNY", 5, 5, cache_read=2.5, source="https://open.bigmodel.cn/pricing"),
        "glm-5v-turbo": Price("CNY", 5, 22, cache_read=1.2, long_threshold=32_000, long_input=7, long_output=26,
                              source="https://open.bigmodel.cn/pricing"),
        "glm-4.6v": Price("CNY", 1, 3, cache_read=0.2, long_threshold=32_000, long_input=2, long_output=6,
                          source="https://open.bigmodel.cn/pricing"),
    },
    "hunyuan": {
        "hy4": Price("CNY", 6, 18, cache_read=0.3, search_per_1k=12,
                     source="https://cloud.tencent.com/document/product/1823"),
        "hy4-flash": Price("CNY", 0.5, 2, cache_read=0.02, source="https://cloud.tencent.com/document/product/1823"),
        "hy3": Price("CNY", 1, 4, cache_read=0.25, source="https://cloud.tencent.com/document/product/1823"),
    },
    "longcat": {
        "longcat-2.5": Price("CNY", 2, 8, cache_read=0.04, source="https://longcat.chat/platform/docs"),
        "longcat-2.0": Price("CNY", 5, 20, cache_read=0.1, source="https://longcat.chat/platform/docs"),
    },
    "qianfan": {
        "ernie-5.1": Price("CNY", 4, 18, long_threshold=32_000, long_input=6, long_output=22,
                           source="https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya"),
    },
    "antdigital": {
        "ling-3.1-flash": Price("CNY", 2, 6, cache_read=0.4, source="https://maas.antdigital.com"),
        "ling-3.0-flash": Price("CNY", 0.5, 1.5, cache_read=0.1, source="https://maas.antdigital.com"),
    },
    "siliconflow": {
        "deepseek-ai/deepseek-v3": Price("CNY", 2, 8, source="https://siliconflow.cn/pricing"),
        "deepseek-ai/deepseek-v3.2": Price("CNY", 4, 6, cache_read=0.4, source="https://siliconflow.cn/pricing"),
    },
}

# Rates used when neither the Bank of Canada nor the cache is reachable
# (Bank of Canada daily rates of 2026-11-18): CAD per unit.
BUILTIN_FX = {"USD": 1.4326, "CNY": 0.2149, "EUR": 1.5717, "CAD": 1.0}
BUILTIN_FX_DATE = "2026-11-18"

_BOC_URL = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD,FXCNYCAD,FXEURCAD/json?recent=1"
_LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
_USER_AGENT = "PaperOrchestra/1.0 (price list refresh)"
_REFRESH_EVERY_SEC = 20 * 3600
_TIMEOUT = 8

# LiteLLM's provider names for ours (only providers whose LiteLLM entries are
# direct list prices; mainland Chinese platforms keep the built-in CNY table).
_LITELLM_PROVIDERS = {
    "openai": ("openai",), "anthropic": ("anthropic",), "gemini": ("gemini",), "deepseek": ("deepseek",),
    "xai": ("xai",), "mistral": ("mistral",), "groq": ("groq",), "together": ("together_ai",),
}

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_refresh_thread: Optional[threading.Thread] = None


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def _cache_path() -> str:
    base = os.getenv("PO_PRICING_CACHE_DIR", "").strip() or os.path.join(
        os.path.expanduser("~"), ".cache", "paper-orchestra")
    return os.path.join(base, "pricing.json")


def _load_cache() -> Dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
    data: Dict[str, Any] = {}
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            data = raw
    except Exception:
        data = {}
    with _lock:
        if _cache is None:
            _cache = data
        return _cache


def _save_cache(data: Dict[str, Any]) -> None:
    global _cache
    with _lock:
        _cache = data
    tmp = None
    try:
        path = _cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # A temp file of its own: two runs refreshing at once never share one.
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="pricing.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        tmp = None
    except Exception:
        pass
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Online refresh (once a day, in the background)
# --------------------------------------------------------------------------


def _online_allowed() -> bool:
    return os.getenv("PO_PRICING_ONLINE", "1").strip().lower() not in ("0", "false", "no", "off")


def _get_json(url: str) -> Any:
    import requests

    rsp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT)
    rsp.raise_for_status()
    return rsp.json()


def _fetch_fx() -> Optional[Dict[str, Any]]:
    data = _get_json(_BOC_URL)
    obs = (data or {}).get("observations") or []
    if not obs:
        return None
    last = obs[-1]
    rates = {"CAD": 1.0}
    for code in ("USD", "CNY", "EUR"):
        try:
            v = float(((last.get(f"FX{code}CAD") or {}).get("v")))
            if v > 0:
                rates[code] = v
        except (TypeError, ValueError):
            continue
    if "USD" not in rates:
        return None
    return {"rates": rates, "date": str(last.get("d") or "")}


def _per_m(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x * 1_000_000, 6) if x >= 0 else None


def _fetch_litellm() -> Dict[str, Dict[str, Any]]:
    """{"provider/model": price fields per million tokens, USD} for our providers."""
    data = _get_json(_LITELLM_URL)
    wanted = {lp: ours for ours, lps in _LITELLM_PROVIDERS.items() for lp in lps}
    out: Dict[str, Dict[str, Any]] = {}
    for key, v in (data or {}).items():
        if not isinstance(v, dict) or key == "sample_spec":
            continue
        ours = wanted.get(str(v.get("litellm_provider") or ""))
        if not ours or v.get("mode") not in (None, "chat", "completion", "responses", "image_generation"):
            continue
        inp, out_ = _per_m(v.get("input_cost_per_token")), _per_m(v.get("output_cost_per_token"))
        if inp is None and out_ is None and v.get("output_cost_per_image") is None:
            continue
        model = key.split("/", 1)[1] if key.startswith(f"{v.get('litellm_provider')}/") else key
        entry = {
            "input": inp or 0.0, "output": out_ or 0.0,
            "cache_read": _per_m(v.get("cache_read_input_token_cost")),
            "cache_write": _per_m(v.get("cache_creation_input_token_cost")),
            "cache_write_1h": _per_m(v.get("cache_creation_input_token_cost_above_1hr")),
            "image_output": _per_m(v.get("output_cost_per_image_token")),
            "per_image": v.get("output_cost_per_image") if isinstance(v.get("output_cost_per_image"), (int, float)) else None,
        }
        for k_in, k_out, threshold in (("input_cost_per_token_above_200k_tokens",
                                        "output_cost_per_token_above_200k_tokens", 200_000),
                                       ("input_cost_per_token_above_272k_tokens",
                                        "output_cost_per_token_above_272k_tokens", 272_000)):
            if v.get(k_in) is not None:
                entry.update(long_threshold=threshold, long_input=_per_m(v.get(k_in)),
                             long_output=_per_m(v.get(k_out)))
                break
        out[f"{ours}/{model.lower()}"] = {k: val for k, val in entry.items() if val is not None}
    return out


def _fetch_openrouter() -> Dict[str, Dict[str, Any]]:
    data = _get_json(_OPENROUTER_URL)
    out: Dict[str, Dict[str, Any]] = {}
    for m in (data or {}).get("data") or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        p = m.get("pricing") or {}
        inp, out_ = _per_m(p.get("prompt")), _per_m(p.get("completion"))
        if inp is None and out_ is None:
            continue
        entry = {"input": inp or 0.0, "output": out_ or 0.0,
                 "cache_read": _per_m(p.get("input_cache_read")),
                 "cache_write": _per_m(p.get("input_cache_write"))}
        ws = p.get("web_search")
        try:
            if ws is not None and float(ws) >= 0:
                entry["search_per_1k"] = round(float(ws) * 1000, 6)
        except (TypeError, ValueError):
            pass
        for o in p.get("overrides") or []:
            if isinstance(o, dict) and o.get("min_prompt_tokens"):
                entry.update(long_threshold=int(o["min_prompt_tokens"]), long_input=_per_m(o.get("prompt")),
                             long_output=_per_m(o.get("completion")))
                break
        out[str(m["id"]).lower()] = {k: v for k, v in entry.items() if v is not None}
    return out


def _fetched(entry: Any) -> float:
    """When a cache entry was fetched (0 = never / damaged entry)."""
    try:
        return float(entry.get("fetched") or 0) if isinstance(entry, dict) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _feed(cache: Dict[str, Any], key: str) -> Dict[str, Any]:
    entry = cache.get(key)
    value = entry.get("value") if isinstance(entry, dict) else None
    return value if isinstance(value, dict) else {}


def refresh(force: bool = False) -> Dict[str, Any]:
    """Fetches exchange rates and price feeds when the cache is older than a
    day (or `force`); keeps the old values of any source that fails."""
    data = dict(_load_cache())
    now = time.time()
    if not _online_allowed():
        return data
    changed = False
    for key, fetch in (("fx", _fetch_fx), ("litellm", _fetch_litellm), ("openrouter", _fetch_openrouter)):
        if not force and now - _fetched(data.get(key)) < _REFRESH_EVERY_SEC:
            continue
        try:
            value = fetch()
        except Exception:
            value = None
        if value:
            data[key] = {"fetched": now, "value": value}
            changed = True
    if changed:
        _save_cache(data)
    return data


def refresh_in_background() -> None:
    """Starts refresh() in a daemon thread (once per process at a time)."""
    global _refresh_thread
    try:
        with _lock:
            if _refresh_thread is not None and _refresh_thread.is_alive():
                return
            _refresh_thread = threading.Thread(target=_safe_refresh, name="po-pricing-refresh", daemon=True)
            _refresh_thread.start()
    except Exception:
        pass


def _safe_refresh() -> None:
    try:
        refresh()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Lookup
# --------------------------------------------------------------------------

_DATE_SUFFIX = re.compile(r"-(?:20\d{6}|\d{4}-\d{2}-\d{2}|latest)$")


def _today() -> str:
    return datetime.date.today().isoformat()


def _dated(entry: Any, today: Optional[str] = None) -> Optional[Price]:
    if isinstance(entry, Price):
        return entry
    if isinstance(entry, list) and entry:
        today = today or _today()
        current = entry[0]
        for p in entry:
            if not p.starts or p.starts <= today:
                current = p
        return current
    return None


def _from_feed(fields: Dict[str, Any], source: str) -> Optional[Price]:
    try:
        return Price(
            "USD", float(fields.get("input") or 0), float(fields.get("output") or 0),
            cache_read=fields.get("cache_read"), cache_write=fields.get("cache_write"),
            cache_write_1h=fields.get("cache_write_1h"), long_threshold=fields.get("long_threshold"),
            long_input=fields.get("long_input"), long_output=fields.get("long_output"),
            image_output=fields.get("image_output"), per_image=fields.get("per_image"),
            search_per_1k=fields.get("search_per_1k"), source=source, as_of="",
        )
    except Exception:
        return None


def _candidates(model: str) -> List[str]:
    m = (model or "").strip().lower()
    if m.startswith("models/"):
        m = m[len("models/"):]
    out = [m]
    for short in (_DATE_SUFFIX.sub("", m), re.sub(r"-preview(?:-\d{2}-\d{2})?$", "", m)):
        if short not in out:
            out.append(short)
    return out


# Platforms whose built-in table lists their own models (not relays).
_MAKERS = ("anthropic", "openai", "gemini", "deepseek", "xai", "mistral", "qwen", "moonshot", "zhipu",
           "hunyuan", "longcat", "qianfan", "antdigital")


def lookup(provider: str, model: str, today: Optional[str] = None) -> Optional[Tuple[Price, str]]:
    """(price, kind) for a call on `today` (default: today), kind = "official"
    (built-in table), "official:<maker>" (a relay serving a maker's model),
    "litellm" or "openrouter"; None when the model has no known price."""
    try:
        provider = (provider or "").strip().lower()
        names = _candidates(model)
        table = BUILTIN_PRICES.get(provider, {})
        cache = _load_cache()
        openrouter = _feed(cache, "openrouter") if provider == "openrouter" else {}
        litellm = _feed(cache, "litellm") if provider != "openrouter" else {}
        # The exact id first in every source, then the shortened ones (a dated
        # snapshot the feed prices on its own wins over the built-in alias).
        for n in names:
            # 1. Built-in official prices, for this platform.
            p = _dated(table.get(n), today)
            if p:
                return p, "official"
            # 2. OpenRouter's own prices for OpenRouter models.
            if n in openrouter:
                p = _from_feed(openrouter[n], "openrouter.ai/api/v1/models")
                if p:
                    return p, "openrouter"
            # 3. The LiteLLM community file for this platform.
            if f"{provider}/{n}" in litellm:
                p = _from_feed(litellm[f"{provider}/{n}"], "LiteLLM model_prices_and_context_window.json")
                if p:
                    return p, "litellm"
        # 4. A relay or custom platform (or OpenRouter offline) serving a
        # well-known model under its maker's name: the maker's list price.
        # Only the makers' own tables: another relay's price is no guide.
        tails = names + [n.rsplit("/", 1)[1] for n in names if "/" in n]
        for maker in _MAKERS:
            models = BUILTIN_PRICES.get(maker, {})
            for n in tails:
                p = _dated(models.get(n), today)
                if p:
                    return p, f"official:{maker}"
        return None
    except Exception:
        return None


def long_context_threshold(provider: str, model: str) -> Optional[int]:
    found = lookup(provider, model)
    return found[0].long_threshold if found else None


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------


def _n(row: Dict[str, Any], key: str) -> int:
    try:
        v = int(row.get(key) or 0)
        return v if v > 0 else 0
    except (TypeError, ValueError):
        return 0


def cost_of(row: Dict[str, Any], price: Price) -> float:
    """Cost of a usage_report by_model row in the price's currency."""
    inp, out = _n(row, "input_tokens"), _n(row, "output_tokens")
    cr, cw, cw1h = _n(row, "cache_read_tokens"), _n(row, "cache_write_tokens"), _n(row, "cache_write_1h_tokens")
    img_out = min(_n(row, "image_output_tokens"), out)
    long_in, long_out = min(_n(row, "long_input_tokens"), inp), min(_n(row, "long_output_tokens"), out)
    cw1h = min(cw1h, cw)
    cached = min(cr + cw, inp)
    plain_in = inp - cached
    long_share = (long_in / inp) if inp and price.long_threshold else 0.0
    li, lo = price.long_input or price.input, price.long_output or price.output

    # Uncached input, split into the normal and long-context tiers.
    plain_long = plain_in * long_share
    total = (plain_in - plain_long) * price.input + plain_long * li
    # Cache reads / writes (long-context calls scale their cache prices too).
    scale = (1 + long_share * (li / price.input - 1)) if price.input else 1.0
    total += cr * (price.cache_read if price.cache_read is not None else price.input) * scale
    total += (cw - cw1h) * (price.cache_write if price.cache_write is not None else price.input) * scale
    total += cw1h * (price.cache_write_1h if price.cache_write_1h is not None else
                     (price.cache_write if price.cache_write is not None else price.input)) * scale
    # Output: generated images at the image rate, text in its tier.
    text_out = out - img_out
    text_long = min(long_out, text_out) if price.long_threshold else 0
    total += (text_out - text_long) * price.output + text_long * lo
    total += img_out * (price.image_output if price.image_output is not None else price.output)
    total /= 1_000_000
    if price.search_per_1k:
        total += _n(row, "search_queries") * price.search_per_1k / 1000
    if price.per_image and not img_out and not out:
        total += _n(row, "images") * price.per_image
    return total


def fx_rates() -> Dict[str, Any]:
    """{"rates": {code: CAD per unit}, "date", "source": bank_of_canada|builtin, "fetched_at"}."""
    cache = _load_cache()
    fx = cache.get("fx") if isinstance(cache.get("fx"), dict) else {}
    value = fx.get("value") if isinstance(fx.get("value"), dict) else None
    if value and isinstance(value.get("rates"), dict) and value["rates"].get("USD"):
        rates = dict(BUILTIN_FX)
        rates.update({k: float(v) for k, v in value["rates"].items() if isinstance(v, (int, float)) and v > 0})
        return {"rates": rates, "date": value.get("date") or "", "source": "bank_of_canada",
                "fetched_at": _iso(fx.get("fetched"))}
    return {"rates": dict(BUILTIN_FX), "date": BUILTIN_FX_DATE, "source": "builtin", "fetched_at": ""}


def _iso(ts: Any) -> str:
    try:
        return datetime.datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return ""


def billable(row: Dict[str, Any]) -> bool:
    return bool(_n(row, "input_tokens") or _n(row, "output_tokens") or _n(row, "images")
                or _n(row, "search_queries"))


def price_call(found: Optional[Tuple[Price, str]], counts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The cost of ONE call (counts as in a by_model row) at the price
    `found` by lookup() when the call was made: {"amount", "currency",
    "source", "price_as_of"}, or None when it has no price. Pricing each call
    when it happens puts every call in its own long-context tier and at the
    price of its own day. Never raises."""
    try:
        if not found:
            return None
        price, kind = found
        return {"amount": cost_of(counts, price), "currency": price.currency, "source": kind,
                "price_as_of": price.as_of}
    except Exception:
        return None


def price_report(by_model: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Adds a "cost" entry to each priced by_model row (in place) and returns
    the report's top-level "cost" summary, in CAD.

    A row may carry "_priced": the sum of its calls priced one by one
    (price_call; the key is removed). Rows without it are priced from their
    counters at today's price (long-context and cache tiers then shared out
    in proportion)."""
    fx = fx_rates()
    rates = fx["rates"]
    total = 0.0
    by_currency: Dict[str, float] = {}
    unpriced: List[str] = []
    kinds = set()
    for row in by_model:
        pre = row.pop("_priced", None)
        if not billable(row):
            continue
        if isinstance(pre, dict) and pre.get("currency") in rates:
            amount, currency = float(pre.get("amount") or 0), pre["currency"]
            kind, as_of = str(pre.get("source") or ""), str(pre.get("price_as_of") or "")
        else:
            found = lookup(str(row.get("provider") or ""), str(row.get("model") or ""))
            if not found or found[0].currency not in rates:
                unpriced.append(f"{row.get('provider')}:{row.get('model')}")
                continue
            price, kind = found
            amount, currency, as_of = cost_of(row, price), price.currency, price.as_of
        cad = amount * rates[currency]
        row["cost"] = {"cad": round(cad, 6), "amount": round(amount, 6), "currency": currency,
                       "source": kind, "price_as_of": as_of}
        total += cad
        by_currency[currency] = by_currency.get(currency, 0.0) + amount
        kinds.add(kind.split(":", 1)[0])
    cache = _load_cache()
    feeds = {k: _iso(_fetched(cache.get(k))) for k in ("litellm", "openrouter") if _fetched(cache.get(k))}
    return {
        "currency": "CAD",
        "total": round(total, 6),
        "by_currency": {k: round(v, 6) for k, v in by_currency.items()},
        "fx": {"rates": {k: v for k, v in rates.items() if k in by_currency or k == "USD"},
               "date": fx["date"], "source": fx["source"]},
        "prices": {"builtin_checked": PRICES_CHECKED, "feeds_fetched": feeds, "sources": sorted(kinds)},
        "unpriced_models": unpriced,
        "basis": "list_price_estimate",
    }
