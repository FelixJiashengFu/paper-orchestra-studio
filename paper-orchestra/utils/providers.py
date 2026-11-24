# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Provider registry: lets PaperOrchestra talk to (almost) any LLM API.

Every model reference is resolved to a (ProviderSpec, bare_model_name) pair.
A ProviderSpec says which wire protocol to speak ("openai", "anthropic" or
"gemini"), where the endpoint lives and which key to send.

Ways to pick a provider explicitly (recommended):

    qwen:qwen-max                 -> preset "qwen" (DashScope, OpenAI-compatible)
    openrouter:anthropic/claude-sonnet-4
    ollama:qwen2.5:14b            -> only the FIRST colon splits, so Ollama tags survive
    myproxy:gpt-4o                -> a provider you declared in providers.json

Without a prefix the old name-based rules still apply (claude-* -> Anthropic,
deepseek-* -> DeepSeek, gpt-*/o1/o3/o4 -> OpenAI, gemini-* -> Gemini), so every
existing project/database entry keeps working unchanged.

Custom providers live in ``providers.json`` at the repo root (or the file named
by PO_PROVIDERS_FILE). ``_spec_from_dict`` lists the accepted fields.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from utils.image_fit import ImageLimits

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

KINDS = ("openai", "anthropic", "gemini")
# Not a wire protocol: the model is an agent CLI the user is signed in to
# (Meta's Muse Code), run headless on the user's own quota. No key, no SDK.
CLI_KIND = "cli"
_HOST_AGENT_OFF = {"", "0", "off", "none", "no", "false"}


def host_agent() -> str:
    """Name of the host-agent provider every LLM call must use, or "".

    PO_HOST_AGENT=muse (set by the Muse plugin) sends every generation step to
    the signed-in Muse Code CLI and nothing to any other LLM API, whatever keys
    .env holds and whatever model a request names. PO_HOST_AGENT_STRICT=0 keeps
    Muse as the default for "auto" only and lets explicit model names through.
    """
    value = os.getenv("PO_HOST_AGENT", "").strip().lower()
    return "" if value in _HOST_AGENT_OFF else value


def host_agent_strict() -> bool:
    return bool(host_agent()) and os.getenv("PO_HOST_AGENT_STRICT", "").strip().lower() not in ("0", "off", "false", "no")


@dataclass
class ProviderSpec:
    name: str
    kind: str  # "openai" | "anthropic" | "gemini"
    base_url: str = ""
    api_key: str = ""
    api_key_env: List[str] = field(default_factory=list)
    base_url_env: List[str] = field(default_factory=list)
    # None = unknown: try images, fall back to text-only on a vision error.
    vision: Optional[bool] = None
    # Models that take images even though `vision` is False for the platform
    # (e.g. DeepSeek: deepseek-flash reads images, deepseek-v4-pro does not).
    vision_models: List[str] = field(default_factory=list)
    models: List[str] = field(default_factory=list)
    default_model: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    # Keys never sent to this endpoint (e.g. ["temperature", "top_p"]).
    drop_params: List[str] = field(default_factory=list)
    # True if the endpoint works without any API key (e.g. local Ollama / vLLM).
    key_optional: bool = False
    builtin: bool = True
    aliases: List[str] = field(default_factory=list)
    # Image limits of the endpoint (None = not known). Images over them are
    # scaled down on the copy that is sent instead of being dropped.
    image_max_pixels: Optional[int] = None  # width * height of one image
    image_max_edge: Optional[int] = None  # longest side of one image, px
    image_max_bytes: Optional[int] = None  # raw bytes of one image
    image_max_total_pixels: Optional[int] = None  # all images of one request together
    image_max_total_bytes: Optional[int] = None  # raw bytes of all images of one request (default 12 MB)

    def image_limits(self) -> ImageLimits:
        return ImageLimits(max_pixels=self.image_max_pixels, max_edge=self.image_max_edge,
                           max_bytes=self.image_max_bytes)

    # ---- resolved values -------------------------------------------------
    def resolved_api_key(self) -> str:
        # An inline key that references an unset variable ("${RELAY_TOKEN}")
        # counts as missing, so api_key_env / <NAME>_API_KEY still apply.
        if self.api_key:
            val = _expand_env(self.api_key).strip()
            if val and not _UNEXPANDED_VAR.search(val):
                return val
        for env_name in self.api_key_env:
            val = os.getenv(env_name, "").strip()
            if val:
                return val
        return ""

    def resolved_base_url(self) -> str:
        for env_name in self.base_url_env:
            val = os.getenv(env_name, "").strip()
            if val:
                return val
        return _expand_env(self.base_url).strip()

    def resolved_default_model(self) -> str:
        env_val = os.getenv(f"{_env_prefix(self.name)}_DEFAULT_MODEL", "").strip()
        if env_val:
            return env_val
        if self.default_model:
            return self.default_model
        return self.models[0] if self.models else ""

    def has_credentials(self) -> bool:
        if self.kind == CLI_KIND:
            # Opt-in: selected as the host agent, or its CLI path given explicitly.
            return host_agent() in ([self.name] + [a.lower() for a in self.aliases]) or bool(
                os.getenv(f"{_env_prefix(self.name)}_CLI", "").strip())
        if self.kind == "gemini" and self.name == "gemini":
            if os.getenv("VERTEX_AI_PROJECT") and os.getenv("VERTEX_AI_LOCATION"):
                return True
        if self.resolved_api_key():
            return True
        if self.key_optional:
            # Local endpoints count as configured only once their URL is set
            # explicitly (otherwise every machine would "have" Ollama).
            return any(os.getenv(e, "").strip() for e in self.base_url_env) or not self.builtin
        return False


def _env_prefix(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


# What os.path.expandvars leaves behind for an undefined variable.
_UNEXPANDED_VAR = re.compile(r"\$(\w+|\{[^}]*\})")


def _expand_env(value: str) -> str:
    """Allows "${MY_KEY}" / "$MY_KEY" inside providers.json values."""
    if not isinstance(value, str):
        return ""
    return os.path.expandvars(value)


def _preset(name, base_url, key_envs, aliases=(), vision=None, key_optional=False, default_model="",
            **image_limits):
    prefix = _env_prefix(name)
    return ProviderSpec(**image_limits,
        name=name,
        kind="openai",
        base_url=base_url,
        api_key_env=list(key_envs),
        base_url_env=[f"{prefix}_BASE_URL"],
        vision=vision,
        aliases=list(aliases),
        key_optional=key_optional,
        default_model=default_model,
    )


def _builtin_providers() -> List[ProviderSpec]:
    return [
        # ---- the four original backends (behaviour unchanged) ----
        # The official base URLs are spelled out because the stock .env ships
        # empty "OPENAI_BASE_URL=" / "ANTHROPIC_BASE_URL=" lines: python-dotenv
        # exports them as "", and both SDKs treat an empty env var as a set (and
        # scheme-less) URL when base_url is not passed, so every request failed
        # with "Request URL is missing an 'http://' or 'https://' protocol".
        ProviderSpec(
            name="openai", kind="openai", base_url="https://api.openai.com/v1",
            api_key_env=["OPENAI_API_KEY"],
            base_url_env=["OPENAI_BASE_URL"], vision=None, aliases=["oai"],
        ),
        ProviderSpec(
            name="anthropic", kind="anthropic", base_url="https://api.anthropic.com",
            api_key_env=["ANTHROPIC_API_KEY"],
            base_url_env=["ANTHROPIC_BASE_URL"], vision=True, aliases=["claude"],
        ),
        ProviderSpec(
            name="deepseek", kind="openai", base_url="https://api.deepseek.com",
            api_key_env=["DEEPSEEK_API_KEY"], base_url_env=["DEEPSEEK_BASE_URL"],
            # deepseek-flash takes images on /chat/completions (DeepSeek docs,
            # guides/vision, checked 2026-11-19; the retired
            # deepseek-v4-flash-vision-exp name is routed to it). Other DeepSeek
            # models are not documented as vision models: text-only.
            vision=False, vision_models=["deepseek-flash", "deepseek-v4-flash-vision-exp"],
            # guides/vision: 8192 px per side (4096 with 15+ images), 32 MiB per image.
            image_max_edge=4096, image_max_bytes=24_000_000,
        ),
        ProviderSpec(
            # GEMINI_API_KEY only, like the original code: a GOOGLE_API_KEY set for
            # other tools must not switch OpenAI-only setups to the Gemini paths.
            name="gemini", kind="gemini", api_key_env=["GEMINI_API_KEY"],
            base_url_env=["GEMINI_BASE_URL"], vision=True, aliases=["google", "vertex"],
        ),
        # ---- OpenAI-compatible presets: set <NAME>_API_KEY and go ----
        # default_model is what "auto" falls back to when no legacy key is set
        # (override with <NAME>_DEFAULT_MODEL). Platforms whose model ids are
        # account-specific (local servers, some gateways) have none. Doubao (Ark)
        # and MiniMax were taken off the list on 2026-11-19; add them back
        # through providers.json if needed (their .env keys are left alone).
        _preset("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                ["DASHSCOPE_API_KEY", "QWEN_API_KEY"], aliases=["dashscope", "bailian", "aliyun"],
                default_model="qwen-max",
                image_max_bytes=7_700_000),
        _preset("moonshot", "https://api.moonshot.cn/v1",
                ["MOONSHOT_API_KEY", "KIMI_API_KEY"], aliases=["kimi"], default_model="kimi-k3.1-pro"),
        # Image limits below: each platform's vision docs, read 2026-11-19. Byte
        # limits counted on the base64 text (or not said) are given as raw bytes
        # (about 3/4). Hunyuan scales everything to ~2048 px itself ("超过
        # 2048×2048 的部分，模型无法识别"), so larger uploads only cost time.
        _preset("zhipu", "https://open.bigmodel.cn/api/paas/v4",
                ["ZHIPU_API_KEY", "ZHIPUAI_API_KEY", "GLM_API_KEY"], aliases=["glm", "bigmodel"],
                default_model="glm-4-plus",
                image_max_edge=6000, image_max_pixels=36_000_000, image_max_bytes=3_700_000),
        # Tencent HY (Hunyuan): the old api.hunyuan.cloud.tencent.com platform
        # was retired on 2026-09-30; TokenHub serves the hy* models now.
        _preset("hunyuan", "https://tokenhub.tencentmaas.com/v1",
                ["HUNYUAN_API_KEY", "TOKENHUB_API_KEY"], aliases=["hy", "tencent", "tokenhub"],
                default_model="hy4",
                image_max_edge=2048, image_max_bytes=10_000_000),
        # Ant Digital (蚂蚁数科) DTMaaS: its own platform (keys from maas.antdigital.com,
        # sign-in through cloud.alipay.com). Model ids are lower-case ("ling-3.1-flash").
        # Ant Ling (api.ant-ling.com) and Bailian also serve Ling; ANTDIGITAL_BASE_URL
        # switches. Meituan LongCat: api.longcat.chat (the SDK appends /v1).
        # Neither has a default_model, so "auto" never falls back to them.
        _preset("antdigital", "https://maas-api.antdigital.com/v1",
                ["ANTDIGITAL_API_KEY", "DTMAAS_API_KEY"], aliases=["ant", "ling", "dtmaas"],
                image_max_pixels=12_845_056, image_max_bytes=24_000_000),
        # LongCat docs (image-video-understanding): W*H <= 5,242,880 px and each
        # side <= 5120 px per image, 10 MB per image; larger images are a 400.
        _preset("longcat", "https://api.longcat.chat/openai",
                ["LONGCAT_API_KEY"], aliases=["meituan"],
                image_max_pixels=5_242_880, image_max_edge=5120, image_max_bytes=7_700_000),
        # Baidu ERNIE via Qianfan's OpenAI-compatible v2 API (bce-v3 API Key).
        _preset("qianfan", "https://qianfan.baidubce.com/v2",
                ["QIANFAN_API_KEY", "ERNIE_API_KEY"], aliases=["ernie", "baidu", "wenxin"],
                default_model="ernie-5.1", image_max_bytes=10_000_000),
        _preset("siliconflow", "https://api.siliconflow.cn/v1", ["SILICONFLOW_API_KEY"], aliases=["sf"],
                default_model="deepseek-ai/DeepSeek-V3"),
        _preset("openrouter", "https://openrouter.ai/api/v1", ["OPENROUTER_API_KEY"],
                default_model="anthropic/claude-sonnet-5.5"),
        _preset("xai", "https://api.x.ai/v1", ["XAI_API_KEY", "GROK_API_KEY"], aliases=["grok"],
                image_max_bytes=15_500_000),
        _preset("groq", "https://api.groq.com/openai/v1", ["GROQ_API_KEY"],
                default_model="llama-3.3-70b-versatile", image_max_bytes=2_900_000),
        _preset("mistral", "https://api.mistral.ai/v1", ["MISTRAL_API_KEY"],
                default_model="mistral-large-latest", image_max_edge=10_000, image_max_bytes=7_400_000),
        _preset("together", "https://api.together.xyz/v1", ["TOGETHER_API_KEY"]),
        # Meta Muse Code, run headless (`muse exec`) on the user's own Muse
        # sign-in and quota: utils/agent_cli_utils.py. Text-only here. No
        # default_model: Muse Code's own configured model is used unless
        # MUSE_DEFAULT_MODEL (or a "muse:<model>" ref) names one.
        ProviderSpec(name="muse", kind=CLI_KIND, vision=False, aliases=["meta", "muse-code", "musecode"]),
        _preset("ollama", "http://localhost:11434/v1", ["OLLAMA_API_KEY"], key_optional=True),
        _preset("lmstudio", "http://localhost:1234/v1", ["LMSTUDIO_API_KEY"], key_optional=True),
        _preset("vllm", "http://localhost:8000/v1", ["VLLM_API_KEY"], key_optional=True),
    ]


# --------------------------------------------------------------------------
# providers.json
# --------------------------------------------------------------------------

def providers_file_path() -> str:
    custom = os.getenv("PO_PROVIDERS_FILE", "").strip()
    if custom:
        return custom if os.path.isabs(custom) else os.path.join(_REPO_ROOT, custom)
    return os.path.join(_REPO_ROOT, "providers.json")


_registry_lock = threading.Lock()
_registry_cache: Optional[Tuple[float, Dict[str, ProviderSpec], List[str], Dict[str, Any]]] = None


def _load_config_file() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    path = providers_file_path()
    if not os.path.exists(path):
        return [], {}
    try:
        # utf-8-sig: files saved by Windows editors may start with a BOM.
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
        if not text.strip():
            return [], {}  # an empty file declares nothing
        data = json.loads(text)
    except Exception as e:  # malformed JSON should not kill the whole run silently
        raise ValueError(f"providers.json 解析失败 ({path}): {e}") from e
    if isinstance(data, list):
        return data, {}
    if not isinstance(data, dict):
        raise ValueError(f"providers.json 顶层必须是对象或数组 ({path})")
    providers = data.get("providers")
    if providers is None:
        return [], data
    if not isinstance(providers, list):
        raise ValueError(f'providers.json 中的 "providers" 必须是数组 ({path})')
    return providers, data


def _opt_str(raw: Dict[str, Any], key: str, where: str) -> str:
    val = raw.get(key)
    if val is None:
        return ""
    if not isinstance(val, str):
        raise ValueError(f"{where} 的 {key} 必须是字符串")
    return val


def _str_list(raw: Dict[str, Any], key: str, where: str) -> List[str]:
    """A list-of-strings field; a single string counts as a one-item list."""
    val = raw.get(key)
    if val is None:
        return []
    if isinstance(val, str):
        return [val] if val.strip() else []
    if isinstance(val, list) and all(isinstance(v, str) for v in val):
        return list(val)
    raise ValueError(f"{where} 的 {key} 必须是字符串或字符串数组")


def _opt_pos_int(raw: Dict[str, Any], key: str, where: str) -> Optional[int]:
    """A positive whole number, else None. Lenient on purpose (a bad value is
    ignored with a note, never an error), so the desktop app's own check of
    providers.json needs no matching rule."""
    val = raw.get(key)
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)) or val <= 0 or int(val) != val:
        print(f" >> {where} 的 {key} 应为正整数，已忽略。")
        return None
    return int(val)


def _opt_bool(raw: Dict[str, Any], key: str, where: str) -> Optional[bool]:
    val = raw.get(key)
    if val is None or isinstance(val, bool):
        return val
    raise ValueError(f"{where} 的 {key} 必须是 true 或 false")


def _spec_from_dict(raw: Any, index: Optional[int] = None) -> ProviderSpec:
    """One providers.json entry. Accepted fields (all optional except name):

    name, type ("openai" | "anthropic" | "gemini", plus the aliases below; "kind"
    is accepted too), base_url, api_key (may use ${VAR}), api_key_env (string or
    list), vision (bool), models (list), default_model, headers (object of
    strings), drop_params (list), key_optional (bool), aliases (list),
    image_max_pixels / image_max_edge / image_max_bytes / image_max_total_pixels
    (positive integers; images over them are scaled down before sending).
    The desktop app validates the file with the same rules (providers.rs).
    """
    where = f"providers.json 第 {index + 1} 个 provider" if index is not None else "providers.json 中的 provider"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} 必须是对象")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{where} 缺少 name（需为非空字符串）")
    name = name.strip()
    where = f"provider '{name}'"
    kind_raw = raw["type"] if "type" in raw else raw.get("kind", "openai")
    if not isinstance(kind_raw, str):
        raise ValueError(f"{where} 的 type 必须是字符串")
    kind = kind_raw.strip().lower()
    if kind in ("openai-compatible", "openai_compatible", "oai"):
        kind = "openai"
    if kind == "claude":
        kind = "anthropic"
    if kind == "google":
        kind = "gemini"
    if kind not in KINDS:
        raise ValueError(f"{where} 的 type 必须是 {KINDS} 之一，收到 '{kind}'")
    headers = raw.get("headers")
    if headers is None:
        headers = {}
    if not isinstance(headers, dict) or not all(isinstance(v, str) for v in headers.values()):
        raise ValueError(f"{where} 的 headers 必须是值为字符串的对象")
    prefix = _env_prefix(name)
    return ProviderSpec(
        name=name,
        kind=kind,
        base_url=_opt_str(raw, "base_url", where),
        api_key=_opt_str(raw, "api_key", where),
        api_key_env=_str_list(raw, "api_key_env", where) + [f"{prefix}_API_KEY"],
        base_url_env=[f"{prefix}_BASE_URL"],
        vision=_opt_bool(raw, "vision", where),
        models=_str_list(raw, "models", where),
        default_model=_opt_str(raw, "default_model", where),
        headers={str(k): v for k, v in headers.items()},
        drop_params=_str_list(raw, "drop_params", where),
        key_optional=bool(_opt_bool(raw, "key_optional", where)),
        builtin=False,
        aliases=_str_list(raw, "aliases", where),
        image_max_pixels=_opt_pos_int(raw, "image_max_pixels", where),
        image_max_edge=_opt_pos_int(raw, "image_max_edge", where),
        image_max_bytes=_opt_pos_int(raw, "image_max_bytes", where),
        image_max_total_pixels=_opt_pos_int(raw, "image_max_total_pixels", where),
        image_max_total_bytes=_opt_pos_int(raw, "image_max_total_bytes", where),
    )


def _get_registry() -> Tuple[Dict[str, ProviderSpec], List[str], Dict[str, Any]]:
    """Returns (name/alias -> spec, ordered custom names, raw config). Hot-reloads on file change."""
    global _registry_cache
    path = providers_file_path()
    mtime = os.path.getmtime(path) if os.path.exists(path) else -1.0
    with _registry_lock:
        if _registry_cache is not None and _registry_cache[0] == mtime:
            return _registry_cache[1], _registry_cache[2], _registry_cache[3]
        lookup: Dict[str, ProviderSpec] = {}
        for spec in _builtin_providers():
            lookup[spec.name.lower()] = spec
            for a in spec.aliases:
                lookup.setdefault(a.lower(), spec)
        raw_providers, raw_cfg = _load_config_file()
        custom_names: List[str] = []
        for i, raw in enumerate(raw_providers):
            spec = _spec_from_dict(raw, i)
            # A custom entry may override a builtin of the same name.
            lookup[spec.name.lower()] = spec
            for a in spec.aliases:
                lookup[a.lower()] = spec
            custom_names.append(spec.name.lower())
        _registry_cache = (mtime, lookup, custom_names, raw_cfg)
        return lookup, custom_names, raw_cfg


def get_provider(name: str) -> Optional[ProviderSpec]:
    lookup, _, _ = _get_registry()
    return lookup.get((name or "").strip().lower())


def list_providers() -> List[ProviderSpec]:
    lookup, _, _ = _get_registry()
    seen, out = set(), []
    for spec in lookup.values():
        if id(spec) not in seen:
            seen.add(id(spec))
            out.append(spec)
    return out


def custom_providers() -> List[ProviderSpec]:
    lookup, custom_names, _ = _get_registry()
    return [lookup[n] for n in custom_names]


def configured_providers() -> List[ProviderSpec]:
    return [p for p in list_providers() if p.has_credentials()]


def config_value(key: str) -> Any:
    _, _, raw_cfg = _get_registry()
    return raw_cfg.get(key)


# --------------------------------------------------------------------------
# Model reference resolution
# --------------------------------------------------------------------------

_NAME_RULES: List[Tuple[str, str]] = [
    # (regex on lower-cased model name, provider name)
    (r"claude", "anthropic"),
    (r"deepseek", "deepseek"),
    (r"^(ft:|gpt|chatgpt|o\d|dall-e|text-embedding)", "openai"),  # ft: = OpenAI fine-tunes
    (r"^(gemini|imagen|gemma)", "gemini"),
    (r"^(qwen|qwq|qvq)", "qwen"),
    (r"^(kimi|moonshot)", "moonshot"),
    (r"^(glm|cogview|chatglm)", "zhipu"),
    (r"^(hunyuan|hy\d|hy-)", "hunyuan"),
    (r"^ernie", "qianfan"),
    (r"^(ling|lingdt|ring)-", "antdigital"),
    (r"^longcat", "longcat"),
    (r"^grok", "xai"),
    (r"^muse-", "muse"),
    (r"^(mistral|codestral|magistral|pixtral|ministral)", "mistral"),
]

# Gateways that only accept "vendor/model" slugs (they reject bare names such
# as "claude-sonnet-4-6" or "deepseek-chat").
_SLUG_GATEWAYS = ("openrouter", "siliconflow")

# An Ollama-style size/variant tag after the last colon: "qwen:7b", "llama3:8b",
# "mistral:7b-instruct-q4_K_M", "mixtral:8x7b", "gemma:latest".
_LOCAL_TAG = re.compile(r"^(\d+(\.\d+)?[bm]|\d+x\d+b|latest)([-_.].*)?$", re.IGNORECASE)
_LOCAL_PROVIDERS = ("ollama", "lmstudio", "vllm")


def _configured_local_provider() -> Optional[ProviderSpec]:
    """The first local endpoint whose URL (or key) is configured explicitly, else None."""
    for name in _LOCAL_PROVIDERS:
        spec = get_provider(name)
        if spec is not None and spec.has_credentials():
            return spec
    return None


def _local_provider() -> Optional[ProviderSpec]:
    """The first local endpoint whose URL is configured, else Ollama."""
    return _configured_local_provider() or get_provider("ollama")


def _legacy_provider_name(lower: str) -> str:
    """The backend the original detect_provider() picked for a bare name (substring rules)."""
    if "claude" in lower:
        return "anthropic"
    if "deepseek" in lower:
        return "deepseek"
    if any(tok in lower for tok in ("gpt", "o1", "o3", "o4")):
        return "openai"
    return "gemini"


# Names the Gemini SDK itself serves (the original sent every other name to
# Gemini too, but those calls could only fail there).
_GEMINI_NATIVE = re.compile(
    r"^((models|tunedmodels)/|publishers/google/|projects/[^/]+/locations/)"
    r"|^(gemini|gemma|imagen|learnlm|aqa|veo)"
)


def split_model_ref(model_ref: str) -> Tuple[Optional[str], str]:
    """'qwen:qwen-max' -> ('qwen', 'qwen-max'); unknown prefixes are left intact.

    A cloud preset followed by a local size tag ("qwen:7b", "mistral:7b") is an
    Ollama tag, not a cloud model called "7b", so it is not split.
    """
    ref = (model_ref or "").strip()
    if ":" in ref:
        prefix, rest = ref.split(":", 1)
        spec = get_provider(prefix) if rest else None
        if spec is not None:
            if spec.builtin and not spec.key_optional and _LOCAL_TAG.match(rest.strip()):
                return None, ref
            return prefix.strip().lower(), rest.strip()
    return None, ref


def resolve_model(model_ref: str) -> Tuple[ProviderSpec, str]:
    """Maps a model reference to (provider spec, model name to send on the wire)."""
    prefix, bare = split_model_ref(model_ref)
    host = get_provider(host_agent()) if host_agent() else None
    if host is not None and host.kind == CLI_KIND:
        # Host-agent mode: a ref of the host itself ("muse:muse-spark-1.2",
        # "muse:default", a bare "muse-..." id) keeps its model; in strict mode
        # every other ref is served by the host's default model, not its own API.
        if prefix is None and bare.lower().startswith(host.name + "-"):
            return host, bare
        if host_agent_strict() and (prefix is None or get_provider(prefix) is not host):
            return host, host.resolved_default_model()
    if prefix is not None:
        spec = get_provider(prefix)
        if spec is not None and spec.kind == CLI_KIND and bare.lower() in ("default", "auto"):
            bare = spec.resolved_default_model()  # "" = the CLI's own configured model
        return spec, bare  # type: ignore[return-value]

    lower = bare.lower()
    local_tag = ":" in bare and bool(_LOCAL_TAG.match(bare.rsplit(":", 1)[1]))

    # 1) A custom provider that lists this exact model wins.
    for spec in custom_providers():
        if bare in spec.models:
            return spec, bare

    # 2) Bare Ollama tag ("llama3:8b") with a local server configured explicitly
    #    (OLLAMA_BASE_URL / LMSTUDIO_BASE_URL / VLLM_BASE_URL).
    if local_tag:
        local = _configured_local_provider()
        if local is not None:
            return local, bare

    # 3) The original routing, whenever it would work: a name the original code
    #    sent to Anthropic / DeepSeek / OpenAI (substring rules) goes there when
    #    that backend has a key, whatever its shape ("gpt-oss:20b" via
    #    OPENAI_BASE_URL, "azure-gpt-4o", "openai/gpt-4o"); a Gemini SDK name
    #    ("models/gemini-2.5-pro", "learnlm-...") goes to Gemini when it has one.
    legacy = get_provider(_legacy_provider_name(lower))
    if legacy is not None and legacy.has_credentials():
        if legacy.name != "gemini" or (not local_tag and _GEMINI_NATIVE.search(lower)):
            return legacy, bare

    # 4) Bare Ollama tag without a configured local server: the default Ollama URL.
    if local_tag:
        local = _local_provider()
        if local is not None:
            return local, bare

    # 5) A "vendor/model" slug is meant for a gateway, whatever vendor it names.
    if "/" in bare:
        gateway = _any_gateway(slug=True)
        if gateway is not None:
            return gateway, bare

    # 6) Name rules: the family's own provider when it has a key. Without one, a
    #    gateway that takes bare names (PO_DEFAULT_PROVIDER, a custom proxy, or
    #    OpenAI / OPENAI_BASE_URL) may serve it; otherwise the family's provider
    #    is returned so the missing-key error names the right variable.
    for pattern, provider_name in _NAME_RULES:
        if re.search(pattern, lower):
            spec = get_provider(provider_name)
            if spec is None:
                continue
            if spec.has_credentials():
                return spec, bare
            return (_any_gateway(slug=False) or spec), bare

    # 7) Unrecognised name: a gateway configured explicitly (PO_DEFAULT_PROVIDER,
    #    providers.json); else Gemini, the original default, when it has a key;
    #    else any other gateway; else Gemini (its missing-key error).
    gemini = get_provider("gemini")
    gateway = _any_gateway(slug=True, builtin=not (gemini is not None and gemini.has_credentials()))
    if gateway is not None:
        return gateway, bare
    return gemini, bare  # type: ignore[return-value]


def _any_gateway(slug: bool = True, builtin: bool = True) -> Optional[ProviderSpec]:
    """A provider that can plausibly serve arbitrary model names.

    slug=False leaves out the gateways that only understand "vendor/model";
    builtin=False only considers explicit configuration (PO_DEFAULT_PROVIDER /
    default_provider and providers.json entries).
    """
    explicit = (os.getenv("PO_DEFAULT_PROVIDER", "") or str(config_value("default_provider") or "")).strip()
    if explicit:
        spec = get_provider(explicit)
        if spec is not None:
            return spec
    for spec in custom_providers():
        if spec.has_credentials():
            return spec
    if not builtin:
        return None
    names = _SLUG_GATEWAYS + ("openai",) if slug else ("openai",)
    for name in names:
        spec = get_provider(name)
        if spec is not None and spec.has_credentials():
            return spec
    return None


def provider_of(model_ref: str) -> ProviderSpec:
    return resolve_model(model_ref)[0]


# --------------------------------------------------------------------------
# Client factories (one cached client per provider)
# --------------------------------------------------------------------------

_clients: Dict[Tuple[str, str, str], Any] = {}
_clients_lock = threading.Lock()


def _http_timeout():
    """10 minutes to read a reply (long generations), but 15 s to connect and
    2 minutes for any single upload write to make progress."""
    try:
        import httpx  # installed with the openai / anthropic SDKs
    except ImportError:
        return None
    return httpx.Timeout(600.0, connect=15.0, write=120.0)


def _missing_key_error(spec: ProviderSpec) -> ValueError:
    envs = " / ".join(spec.api_key_env) or f"{_env_prefix(spec.name)}_API_KEY"
    return ValueError(
        f"Provider '{spec.name}' 没有可用的 API Key：请在 .env 设置 {envs}，"
        f"或在 providers.json 里给它填 api_key / api_key_env。"
    )


def gemini_api_version(vertex: bool) -> Optional[str]:
    """The Gemini Developer API version to call, or None for the SDK's choice.

    google-genai 3.x calls v1 by default, and v1 serves only GA models: the preview
    models this project defaults to (gemini-3.1-pro-preview,
    gemini-3-pro-image-preview, gemini-3-flash-preview) answer "not found for API
    version v1" there. v1beta serves GA and preview models alike and was the SDK's
    default before 3.0, so API-key clients pin it. Vertex AI keeps the SDK default
    (v1beta1), and GOOGLE_GENAI_API_VERSION, when set, wins as the SDK intends.
    """
    if vertex or os.getenv("GOOGLE_GENAI_API_VERSION"):
        return None
    return "v1beta"


def get_client(spec: ProviderSpec):
    if spec.kind == CLI_KIND:
        raise ValueError(f"Provider '{spec.name}' is an agent CLI and has no SDK client; "
                         "its calls go through utils.agent_cli_utils.")
    key = spec.resolved_api_key()
    base_url = spec.resolved_base_url()
    cache_key = (spec.name, key, base_url)
    with _clients_lock:
        if cache_key in _clients:
            return _clients[cache_key]

        if spec.kind == "openai":
            from openai import OpenAI  # type: ignore

            if not key:
                if not spec.key_optional:
                    raise _missing_key_error(spec)
                key = "not-needed"
            kwargs: Dict[str, Any] = {"api_key": key}
            if base_url:
                kwargs["base_url"] = base_url
            if spec.headers:
                kwargs["default_headers"] = spec.headers
            # A stalled upload (no bytes accepted for 2 minutes) fails instead
            # of hanging for the SDK's 10-minute default on every write.
            if _http_timeout() is not None:
                kwargs["timeout"] = _http_timeout()
            client = OpenAI(**kwargs)

        elif spec.kind == "anthropic":
            from anthropic import Anthropic

            if not key and not spec.key_optional:
                raise _missing_key_error(spec)
            kwargs = {"api_key": key or "not-needed"}
            if base_url:
                kwargs["base_url"] = base_url
            if spec.headers:
                kwargs["default_headers"] = spec.headers
            if _http_timeout() is not None:
                kwargs["timeout"] = _http_timeout()
            client = Anthropic(**kwargs)

        else:  # gemini
            from google import genai
            from google.genai import types

            vertex_project = os.getenv("VERTEX_AI_PROJECT")
            vertex_location = os.getenv("VERTEX_AI_LOCATION")
            vertex = bool(spec.name == "gemini" and vertex_project and vertex_location)

            http_kwargs: Dict[str, Any] = {}
            if base_url:
                http_kwargs["base_url"] = base_url
            if spec.headers:
                http_kwargs["headers"] = spec.headers
            api_version = gemini_api_version(vertex)
            if api_version:
                http_kwargs["api_version"] = api_version
            http_options = types.HttpOptions(**http_kwargs) if http_kwargs else None

            if vertex:
                print(f"Using Vertex AI configuration. Project: {vertex_project}, Location: {vertex_location}")
                client = genai.Client(
                    vertexai=True, project=vertex_project, location=vertex_location,
                    http_options=http_options,
                )
            elif key:
                client = genai.Client(api_key=key, http_options=http_options)
            else:
                raise ValueError(
                    "Either VERTEX_AI_PROJECT/VERTEX_AI_LOCATION or GEMINI_API_KEY"
                    " must be set to use Gemini models."
                    if spec.name == "gemini"
                    else str(_missing_key_error(spec))
                )

        _clients[cache_key] = client
        return client


# --------------------------------------------------------------------------
# Runtime vision capability memory
# --------------------------------------------------------------------------

_text_only_models: set = set()

# Error texts that say the model itself does not take images: remembered for
# the rest of the run.
_TEXT_ONLY_HINTS = (
    "image_url", "image input", "image_input", "images are not supported", "image is not supported",
    "does not support image", "doesn't support image", "not support image", "vision", "multimodal",
    "multi-modal", "must be a string", "expected a string",
    "不支持图片", "不支持图像", "不支持多模态", "不支持视觉",
)
# Errors about one particular image (size, format): the model is fine.
_BAD_IMAGE_HINTS = (
    "unsupported image", "invalid image", "image too large", "image size", "image_parse",
    "could not process image", "decode image", "图片格式", "图片过大", "图片大小",
)
# Vague 400s that may be caused by the images: retry this one call without them.
_MAYBE_IMAGE_HINTS = ("image", "图片", "图像", "参数有误")


def vision_error_scope(err: Exception) -> str:
    """How a failed request with images attached should be retried.

    "model": the endpoint rejects images; drop them and remember the model.
    "call":  drop the images for this request only.
    "":      not image-related.
    Only HTTP 4xx API errors are inspected: other exceptions (e.g. a parse
    failure) carry the model's own text, which proves nothing.
    """
    if getattr(err, "status_code", None) not in (400, 413, 415, 422):
        return ""
    msg = str(err).lower()
    if any(h in msg for h in _BAD_IMAGE_HINTS):
        return "call"
    if any(h in msg for h in _TEXT_ONLY_HINTS):
        return "model"
    if any(h in msg for h in _MAYBE_IMAGE_HINTS):
        return "call"
    return ""


def looks_like_vision_error(err: Exception) -> bool:
    """Kept for backward compatibility: True when the model rejects images."""
    return vision_error_scope(err) == "model"


def mark_text_only(spec: ProviderSpec, model: str) -> None:
    _text_only_models.add((spec.name, model))


def model_supports_vision(spec: ProviderSpec, model: str) -> bool:
    if (spec.name, model) in _text_only_models:
        return False
    if spec.vision is False:
        return (model or "").strip().lower() in {m.lower() for m in spec.vision_models}
    return True


# --------------------------------------------------------------------------
# Log hygiene
# --------------------------------------------------------------------------

_KEYLIKE_RE = re.compile(r"\b(sk-ant-|sk-or-|sk-proj-|sk-|sk_|AIza|xai-|gsk_|pplx-|ghp_|hf_)[A-Za-z0-9_\-]{8,}")
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[^\s'\",}]+")
_KEY_PARAM_RE = re.compile(r"(?i)\b((?:api_?key|key|access_token|token)=)[^&\s'\"<>)]+")


def _configured_secrets() -> List[str]:
    """API key values of every provider (longest first). Never raises."""
    keys = set()
    try:
        for spec in list_providers():
            key = spec.resolved_api_key()
            if key and len(key) >= 6:
                keys.add(key)
    except Exception:
        pass
    return sorted(keys, key=len, reverse=True)


def _redact_with(out: str, keys: List[str]) -> str:
    try:
        for key in keys:
            out = out.replace(key, "***")
    except Exception:
        pass
    try:
        out = _KEYLIKE_RE.sub(r"\1***", out)
        out = _BEARER_RE.sub(r"\1***", out)
        out = _KEY_PARAM_RE.sub(r"\1***", out)
    except Exception:
        pass
    return out


def redact_secrets(text: Any) -> str:
    """Text safe for the run log: configured API key values and key-shaped tokens masked.

    Used for the retry warnings that print an API error (a gateway may echo
    the key back). Never raises.
    """
    try:
        out = str(text)
    except Exception:
        return "<error text unavailable>"
    return _redact_with(out, _configured_secrets())


class _RedactingStream:
    """Wraps sys.stdout / sys.stderr so every write goes through redact_secrets.

    Catches what the targeted redaction cannot reach, e.g. a traceback printed
    by the unchanged pipeline code after an authentication error whose text
    echoes the key. Everything else is delegated to the wrapped stream.
    """

    _TTL_SEC = 5.0

    def __init__(self, stream):
        self._stream = stream
        self._keys: List[str] = []
        self._keys_at = float("-inf")

    def write(self, text):
        try:
            if isinstance(text, str) and text:
                import time

                now = time.monotonic()
                if now - self._keys_at > self._TTL_SEC:
                    self._keys, self._keys_at = _configured_secrets(), now
                redacted = _redact_with(text, self._keys)
                self._stream.write(redacted)
                return len(text)
        except Exception:
            pass
        return self._stream.write(text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_output_redaction() -> None:
    """Masks API keys in everything this process prints to stdout / stderr. Never raises."""
    import sys

    for name in ("stdout", "stderr"):
        try:
            stream = getattr(sys, name)
            if stream is not None and not isinstance(stream, _RedactingStream):
                setattr(sys, name, _RedactingStream(stream))
        except Exception:
            pass
