#!/usr/bin/env python
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
Checks which LLM providers and scholarly search sources are usable.

Run from the repo root:

    python tools/check_providers.py                 # configuration only (table, Chinese)
    python tools/check_providers.py --ping          # + one tiny request per configured provider
    python tools/check_providers.py --scholar       # + one tiny query per enabled search source
    python tools/check_providers.py --ping --model qwen:qwen-max   # ping only this model
    python tools/check_providers.py --list-models   # the models each configured provider offers
    python tools/check_providers.py --json [...]    # one JSON object on stdout

JSON shape (--json):

    {"providers": [{"name", "label", "kind", "configured", "model", "ok", "latency_ms", "error"}],
     "scholar":   [{"name", "ok", "latency_ms", "hits", "error"}],
     "models":    [{"name", "label", "kind", "ok", "error", "models": [ref, ...]}]}   # --list-models only

Each "models" ref is what a model field accepts for that provider: the bare id
when it already resolves there (claude-opus-5-5, gemini-3.8-flash), else
"provider:id" (qwen:qwen-max). Listing a provider's models costs nothing.

ok / latency_ms (/ hits) are null for anything that was not tested. A ping
asks for at most 8 output tokens; each check times out after --timeout
seconds (default 30). Errors are short and have API keys masked. The exit
code is 0 whenever the check ran, whatever the results.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

DEFAULT_TIMEOUT_SEC = 30.0
PING_MAX_TOKENS = 8
SCHOLAR_QUERY = "deep learning"

# Same labels as the desktop app's providerCatalog.ts.
LABELS = {
    "anthropic": "Anthropic · Claude",
    "openai": "OpenAI",
    "gemini": "Google · Gemini / Vertex AI",
    "deepseek": "DeepSeek",
    "qwen": "通义千问 · DashScope",
    "moonshot": "Kimi · 月之暗面",
    "zhipu": "智谱 GLM · BigModel",
    "hunyuan": "腾讯混元 HY · TokenHub",
    "qianfan": "百度文心 ERNIE · 千帆",
    "antdigital": "蚂蚁数科 Ling · DTMaaS",
    "longcat": "美团 LongCat",
    "siliconflow": "硅基流动 · SiliconFlow",
    "openrouter": "OpenRouter",
    "xai": "xAI · Grok",
    "groq": "Groq",
    "mistral": "Mistral AI",
    "together": "Together AI",
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
    "vllm": "vLLM",
}

# Ping model for providers without a default_model. The four original backends: the
# models "auto" routes to (utils/llm_backend_utils.resolve_task_model), so the
# check covers what a run will actually call. <NAME>_DEFAULT_MODEL overrides.
FALLBACK_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-6.2-sol",
    "deepseek": "deepseek-v4-pro",
    "gemini": "gemini-4-argon",
    # Presets kept out of auto (no default_model): a connection test still needs a model.
    "antdigital": "ling-3.1-flash",
    "longcat": "LongCat-2.5",
}


# --------------------------------------------------------------------------
# Error text
# --------------------------------------------------------------------------

_KEYLIKE = re.compile(r"\b(sk-ant-|sk-or-|sk-|sk_|AIza|xai-|gsk_|pplx-|ghp_|hf_)[A-Za-z0-9_\-.*]{4,}")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[^\s'\",}]+")
_HEADER_KEY = re.compile(r"(?i)((?:x-api-key|api-key|x-goog-api-key|authorization)['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+")


_POOL_ERR = re.compile(r"HTTPS?ConnectionPool\(host='([^']+)'[^)]*\):\s*(.*)$", re.DOTALL)
_HINTS = (
    ("proxy", "网络错误（代理）"),
    ("name or service not known", "网络错误（域名解析失败）"),
    ("nodename nor servname", "网络错误（域名解析失败）"),
    ("getaddrinfo failed", "网络错误（域名解析失败）"),
    ("name resolution", "网络错误（域名解析失败）"),
    ("timed out", "网络错误（超时）"),
    ("connection refused", "网络错误（连接被拒绝）"),
    ("certificate", "网络错误（SSL 证书）"),
    ("ssl", "网络错误（SSL）"),
)


def _compact_http_error(text: str) -> str:
    """'HTTPSConnectionPool(host=..): Max retries exceeded with url: /x?.. (Caused by X('a', Y('b')))'
    -> '网络错误（..）：host: X: a: b' (the URL, which may carry a query, is dropped)."""
    m = _POOL_ERR.search(text)
    if not m:
        return text
    host, rest = m.group(1), m.group(2)
    cause = re.search(r"\(Caused by (\w+)\((.*)\)\)\s*$", rest, re.DOTALL)
    if cause:
        # urllib3 1.x quotes the reason with '...', 2.x with "..." (with 'host' inside).
        quoted = re.findall(r"'([^']*)'|\"([^\"]*)\"", cause.group(2))
        parts = []
        for single, double in quoted:
            q = re.sub(r"<[^>]+>:\s*", "", double or single)
            q = re.sub(r"^\w*Connection\(host=[^)]*\):\s*", "", q).strip()
            if q and q != host:
                parts.append(q)
        detail = ": ".join([cause.group(1)] + parts)
    else:
        detail = re.sub(r"Max retries exceeded with url: \S+\s*", "", rest).strip()
    return f"{_network_hint(detail)}：{host}: {detail}"


def _network_hint(text: str) -> str:
    lower = text.lower()
    return next((h for k, h in _HINTS if k in lower), "网络错误")


def _root_cause(err: BaseException) -> BaseException:
    """The innermost exception of a __cause__ / __context__ chain (cycle-safe)."""
    seen = {id(err)}
    cur = err
    while True:
        nxt = cur.__cause__ or (None if cur.__suppress_context__ else cur.__context__)
        if nxt is None or id(nxt) in seen:
            return cur
        seen.add(id(nxt))
        cur = nxt


def short_error(err: Any, secrets: Tuple[str, ...] = (), limit: int = 240) -> str:
    """One-line, key-masked error text (from an exception or a string)."""
    try:
        if isinstance(err, BaseException):
            status = getattr(err, "status_code", None)
            msg = None
            body = getattr(err, "body", None)
            if isinstance(body, dict):
                inner = body.get("error", body)
                if isinstance(inner, dict):
                    msg = inner.get("message") or inner.get("msg")
                elif isinstance(inner, str):
                    msg = inner
            msg = msg or getattr(err, "message", None) or str(err) or type(err).__name__
            text = f"HTTP {status}: {msg}" if status else f"{type(err).__name__}: {msg}"
            if not status:
                # e.g. "APIConnectionError: Connection error." hides the reason
                # ("[Errno 111] Connection refused", "Name or service not known").
                root = _root_cause(err)
                root_msg = str(root).strip()
                if root is not err and root_msg and root_msg not in text:
                    text += f" ({type(root).__name__}: {root_msg})"
                if not _POOL_ERR.search(text):
                    hint = _network_hint(text)
                    if hint != "网络错误":
                        text = f"{hint}：{text}"
        else:
            text = str(err)
        text = _compact_http_error(text)
        for secret in secrets:
            if secret and len(secret) >= 6:
                text = text.replace(secret, "***")
        try:
            from utils.scholar_utils import _redact

            text = _redact(text)
        except Exception:
            pass
        text = _KEYLIKE.sub(r"\1***", text)
        text = _BEARER.sub(r"\1***", text)
        text = _HEADER_KEY.sub(r"\1***", text)
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"
    except Exception:
        return type(err).__name__ if isinstance(err, BaseException) else "error"


# --------------------------------------------------------------------------
# Running checks with a deadline
# --------------------------------------------------------------------------


class _Job:
    def __init__(self, fn: Callable[[], Any]):
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.elapsed_ms: Optional[int] = None
        self.thread = threading.Thread(target=self._run, args=(fn,), daemon=True)

    def _run(self, fn):
        t0 = time.time()
        try:
            self.result = fn()
        except BaseException as e:  # noqa: BLE001 - reported, never re-raised
            self.error = e
        finally:
            self.elapsed_ms = int((time.time() - t0) * 1000)


def run_parallel(fns: Dict[str, Callable[[], Any]], timeout: float) -> Dict[str, _Job]:
    """Runs every check in its own daemon thread; returns once all finished or `timeout` passed.

    A job still alive afterwards has thread.is_alive() True (reported as a timeout).
    """
    jobs = {name: _Job(fn) for name, fn in fns.items()}
    for job in jobs.values():
        job.thread.start()
    deadline = time.time() + timeout
    for job in jobs.values():
        job.thread.join(max(0.0, deadline - time.time()))
    return jobs


# --------------------------------------------------------------------------
# LLM providers
# --------------------------------------------------------------------------


def provider_label(spec) -> str:
    return LABELS.get(spec.name, spec.name) if getattr(spec, "builtin", False) else spec.name


def default_model(spec) -> str:
    return spec.resolved_default_model() or FALLBACK_MODELS.get(spec.name, "")


def ping_provider(spec, model: str, timeout: float, timing: Optional[Dict[str, int]] = None) -> int:
    """Sends one tiny request; returns its round-trip time in ms, raises on any failure.

    `timing["ms"]` is set to the request time (not the client set-up) even when it fails.
    """
    from utils.providers import get_client

    client = get_client(spec)
    messages = [{"role": "user", "content": "ping"}]
    if spec.kind == "openai":
        c = client.with_options(timeout=timeout, max_retries=0)

        def send():
            try:
                c.chat.completions.create(model=model, messages=messages, max_tokens=PING_MAX_TOKENS)
            except Exception as e:
                # Newer OpenAI reasoning models only accept max_completion_tokens.
                text = str(e).lower()
                if getattr(e, "status_code", None) == 400 and "max_completion_tokens" in text:
                    c.chat.completions.create(model=model, messages=messages,
                                              max_completion_tokens=PING_MAX_TOKENS)
                else:
                    raise
    elif spec.kind == "anthropic":
        c = client.with_options(timeout=timeout, max_retries=0)

        def send():
            c.messages.create(model=model, max_tokens=PING_MAX_TOKENS, messages=messages)
    else:
        from google.genai import types

        def config(thinking: bool, seconds: float):
            kwargs: Dict[str, Any] = dict(
                max_output_tokens=PING_MAX_TOKENS,
                http_options=types.HttpOptions(timeout=int(seconds * 1000)),
            )
            if thinking:
                # Thinking models otherwise think about "ping" for many seconds
                # (sometimes past the deadline). Low is accepted by flash and pro alike.
                try:
                    kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="LOW")
                except Exception:
                    pass  # older SDK without thinking_level
            return types.GenerateContentConfig(**kwargs)

        def send():
            t_start = time.time()
            thinking, busy_retried, last_err = True, False, None
            for _ in range(3):  # at most one thinking fallback plus one busy retry
                left = timeout - (time.time() - t_start)
                try:
                    client.models.generate_content(model=model, contents="ping",
                                                   config=config(thinking, max(left, 1.0)))
                    return
                except Exception as e:
                    last_err, text = e, str(e)
                    if thinking and _THINKING_REJECTED.search(text):
                        thinking = False  # model has no thinking levels: ask again without
                        continue
                    # Busy/overloaded answers often clear on a second try; only
                    # when enough of the time budget is left for it.
                    if not busy_retried and is_busy_error(e) and timeout - (time.time() - t_start) > 10:
                        busy_retried = True
                        time.sleep(1.0)
                        continue
                    raise
            raise last_err  # not reached: every path above returns or raises

    t0 = time.time()
    try:
        send()
    finally:
        ms = int((time.time() - t0) * 1000)
        if timing is not None:
            timing["ms"] = ms
    return ms


def _preload_sdks(kinds) -> None:
    """Imports the SDKs once on this thread (parallel first imports serialise on the import lock)."""
    for kind in set(kinds):
        try:
            if kind == "openai":
                import openai  # noqa: F401
            elif kind == "anthropic":
                import anthropic  # noqa: F401
            else:
                from google import genai  # noqa: F401
                from google.genai import types  # noqa: F401
        except Exception:
            pass


def _all_providers() -> List[Any]:
    """Every provider a model ref can reach, once each (a providers.json entry that
    overrides a builtin name replaces it, even though the builtin's aliases still exist)."""
    import utils.providers as P

    try:
        specs = P.list_providers()
    except Exception as e:  # malformed providers.json: still report the builtins
        print(f"Warning: {short_error(e)}", file=sys.stderr)
        return P._builtin_providers()
    return [s for s in specs if P.get_provider(s.name) is s]


def parse_ping_models(items: Optional[List[str]], names: List[str]) -> Dict[str, str]:
    """"NAME=MODEL" items (--ping-model) -> {provider name: model}. The model is the
    platform's own name for it; a leading "NAME:" is dropped. Bad items are skipped
    with a warning, so one typo never blocks the other tests."""
    out: Dict[str, str] = {}
    lower = {n.lower(): n for n in names}
    for item in items or []:
        name, sep, model = str(item).partition("=")
        name, model = name.strip(), model.strip()
        real = name if name in names else lower.get(name.lower())
        if not sep or not real or not model:
            print(f"Warning: --ping-model {item!r} ignored (expected NAME=MODEL for a known provider).",
                  file=sys.stderr)
            continue
        if model.lower().startswith(real.lower() + ":"):
            model = model[len(real) + 1:].strip()
        if not model or model.lower() == "auto":
            print(f"Warning: --ping-model {item!r} ignored (needs a concrete model name).", file=sys.stderr)
            continue
        out[real] = model
    return out


_QUOTA_ERR = re.compile(r"quota|RESOURCE_EXHAUSTED|insufficient_quota|billing", re.IGNORECASE)
# The provider was reachable but did not finish in time or is overloaded: try later.
_BUSY_STATUS = {500, 502, 503, 504, 529}
_BUSY_GOOGLE_STATUS = {"DEADLINE_EXCEEDED", "UNAVAILABLE"}
_DEADLINE_TEXT = re.compile(r"DEADLINE_EXCEEDED|Deadline expired")


def is_busy_error(err: BaseException) -> bool:
    """Judged from the HTTP status the SDK reports (openai/anthropic: status_code,
    google-genai: code/status), never from words such as "unavailable" in a
    message: a 404 "model unavailable" or a proxy's "503 Service Unavailable"
    is not something waiting fixes."""
    for attr in ("status_code", "code"):
        status = getattr(err, attr, None)
        if isinstance(status, int) and not isinstance(status, bool):
            return status in _BUSY_STATUS
    if str(getattr(err, "status", "") or "").upper() in _BUSY_GOOGLE_STATUS:
        return True
    return bool(_DEADLINE_TEXT.search(str(err)))
# A 400 about the thinking settings (a model without thinking levels).
_THINKING_REJECTED = re.compile(r"(?=.*thinking)(?=.*(?:\b400\b|INVALID_ARGUMENT))", re.IGNORECASE | re.DOTALL)


def check_providers(ping: bool, model_refs: List[str], timeout: float,
                    progress: Optional[Dict[str, Any]] = None,
                    ping_models: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """`progress` (optional) receives "rows" and "targets" before any request is sent,
    so a caller whose deadline passes can still report the configuration.
    `ping_models` ("NAME=MODEL") changes the model a provider is tested with."""
    specs = _all_providers()
    rows: Dict[str, Dict[str, Any]] = {}
    by_name = {}
    for spec in specs:
        try:
            configured = bool(spec.has_credentials())
        except Exception:
            configured = False
        rows[spec.name] = {
            "name": spec.name,
            "label": provider_label(spec),
            "kind": spec.kind,
            "configured": configured,
            "model": default_model(spec) or None,
            "model_source": "default",  # "test" when --ping-model chose it
            "ok": None,
            "busy": False,  # reachable but timed out / overloaded: 暂时繁忙, not a failure
            "latency_ms": None,
            "error": None,
        }
        by_name[spec.name] = spec

    for name, model in parse_ping_models(ping_models, list(rows)).items():
        rows[name]["model"] = model
        rows[name]["model_source"] = "test"  # chosen for this test, not the platform default

    # Which (provider -> model) to ping: --model refs only, else every configured provider.
    targets: Dict[str, str] = {}
    explicit = [r.strip() for r in model_refs or [] if r and r.strip()]
    if explicit:
        from utils.providers import resolve_model

        for ref in explicit:
            if ref.lower() == "auto":
                print("Warning: --model auto is not a concrete model; ignored.", file=sys.stderr)
                continue
            try:
                spec, bare = resolve_model(ref)
            except Exception as e:
                print(f"Warning: cannot resolve --model {ref}: {short_error(e)}", file=sys.stderr)
                continue
            if spec.name not in rows:  # e.g. builtins-only fallback after a providers.json error
                continue
            rows[spec.name]["model"] = bare
            targets[spec.name] = bare
    elif ping:
        for name, row in rows.items():
            if row["configured"]:
                if row["model"]:
                    targets[name] = row["model"]
                else:
                    row["error"] = (
                        f"未设置默认模型，未测试：请为该平台填写默认模型"
                        f"（{re.sub(r'[^A-Za-z0-9]', '_', name).upper()}_DEFAULT_MODEL，"
                        f"或 providers.json 的 default_model）；命令行也可用 --model {name}:<模型名>"
                    )

    if progress is not None:
        progress["rows"], progress["targets"] = rows, (dict(targets) if ping else {})
    if ping and targets:
        _preload_sdks(by_name[n].kind for n in targets)
        timings: Dict[str, Dict[str, int]] = {n: {} for n in targets}

        def make(name, model):
            return lambda: ping_provider(by_name[name], model, timeout, timings[name])

        jobs = run_parallel({n: make(n, m) for n, m in targets.items()}, timeout + 5)
        for name, job in jobs.items():
            row = rows[name]
            try:
                secrets = (by_name[name].resolved_api_key(),)
            except Exception:
                secrets = ()
            if job.thread.is_alive():
                row["ok"], row["error"] = False, f"超时（超过 {timeout:.0f} 秒无响应）"
                continue
            # latency_ms: request round-trip (until the reply or the error)
            row["latency_ms"] = timings[name].get("ms", job.elapsed_ms)
            if job.error is not None:
                row["ok"], row["error"] = False, short_error(job.error, secrets)
                if _QUOTA_ERR.search(row["error"]):
                    row["error"] += "（这个模型在你的账号或套餐下没有额度；可以把测试模型换成别的，例如 flash 版）"
                elif is_busy_error(job.error):
                    row["busy"] = True
                    row["error"] += "（对方服务这次没能及时处理完，常见于新模型或高峰期；稍后再测即可，写论文时会自动重试）"
            else:
                row["ok"], row["error"] = True, None
    return list(rows.values())


# --------------------------------------------------------------------------
# Model lists (what a model field can offer)
# --------------------------------------------------------------------------

MAX_LISTED_MODELS = 600
# Local servers (Ollama, LM Studio, vLLM) are listed even when their URL is not
# set, if they answer at the default address within this many seconds.
LOCAL_PROBE_TIMEOUT = 3.0

# Ids that are not text-generation models: embeddings, speech, moderation,
# legacy completions, video, music, live audio.
_NOT_A_WRITER = re.compile(
    r"embed|(^|[-_/.])(tts|whisper|moderation|davinci|babbage|aqa|veo|lyria|sora)($|[-_/.\d])"
    r"|transcri|realtime|native-audio|(^|[-_/.])live($|[-_/.])|(^|[-_/.])audio($|[-_/.])"
    r"|robotics|computer-use|deep-research|antigravity",
    re.IGNORECASE,
)


def _natural_key(text: str):
    return [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", text)]


def list_provider_models(spec, timeout: float) -> List[str]:
    """Model ids the provider's API lists, newest first where the API says so.

    Raises on any failure (no key, no /models endpoint, network...).
    """
    from utils.providers import get_client

    client = get_client(spec)
    if spec.kind == "openai":
        c = client.with_options(timeout=timeout, max_retries=0)
        items = list(c.models.list())
        # OpenAI-compatible servers report "created" (Unix time) or nothing.
        if any(getattr(m, "created", None) for m in items):
            items.sort(key=lambda m: getattr(m, "created", None) or 0, reverse=True)
        ids = [getattr(m, "id", "") for m in items]
    elif spec.kind == "anthropic":
        c = client.with_options(timeout=timeout, max_retries=0)
        ids = [m.id for m in c.models.list(limit=1000)]  # newest first; pages followed
    else:
        from google.genai import types

        config = types.ListModelsConfig(
            page_size=1000, http_options=types.HttpOptions(timeout=int(timeout * 1000)))
        ids = []
        for m in client.models.list(config=config):
            actions = getattr(m, "supported_actions", None) or []
            if actions and not ({"generateContent", "predict"} & set(actions)):
                continue
            ids.append((getattr(m, "name", "") or "").rsplit("/", 1)[-1])
        ids.sort(key=_natural_key, reverse=True)  # gemini-3.8-flash before gemini-3.6-flash
        ids.sort(key=lambda i: not i.startswith("gemini"))  # then Gemma, Imagen, ...
    seen, out = set(), []
    for model_id in ids:
        model_id = (model_id or "").strip()
        if not model_id or model_id in seen or _NOT_A_WRITER.search(model_id):
            continue
        if spec.kind != "gemini" and re.search(r"(^|[-_/.])image($|[-_/.])|dall-e|gpt-image", model_id, re.I):
            continue  # only Gemini image models are used (for figures); others cannot write
        seen.add(model_id)
        out.append(model_id)
    return out


def _is_local(spec) -> bool:
    return bool(getattr(spec, "builtin", False) and getattr(spec, "key_optional", False))


def model_ref(spec, model_id: str) -> str:
    """The shortest ref that reaches `model_id` on `spec`'s provider.

    Local servers always get their prefix ("ollama:gemma4:12b-it-qat"): a bare
    Ollama tag would go to whichever local server is configured first.
    """
    from utils.providers import resolve_model

    if _is_local(spec):
        return f"{spec.name}:{model_id}"
    try:
        resolved, bare = resolve_model(model_id)
        if resolved.name == spec.name and bare == model_id:
            return model_id
    except Exception:
        pass
    return f"{spec.name}:{model_id}"


def list_models(timeout: float) -> List[Dict[str, Any]]:
    """One row per configured provider with the models its API offers (as refs),
    plus any local server (Ollama, LM Studio, vLLM) running at its default address."""
    all_specs = _all_providers()
    specs = [s for s in all_specs if _configured(s)]
    probes = {s.name for s in all_specs if not _configured(s) and _is_local(s)}
    specs += [s for s in all_specs if s.name in probes]
    rows: Dict[str, Dict[str, Any]] = {
        s.name: {"name": s.name, "label": provider_label(s), "kind": s.kind,
                 "ok": None, "error": None, "models": []}
        for s in specs
    }
    if not specs:
        return []
    _preload_sdks(s.kind for s in specs)
    by_name = {s.name: s for s in specs}

    def make(spec):
        limit = min(timeout, LOCAL_PROBE_TIMEOUT) if spec.name in probes else timeout
        return lambda: list_provider_models(spec, limit)

    jobs = run_parallel({s.name: make(s) for s in specs}, timeout + 5)
    for name, job in jobs.items():
        row, spec = rows[name], by_name[name]
        if name in probes and (job.thread.is_alive() or job.error is not None):
            del rows[name]  # not set up and not running here: nothing to report
            continue
        try:
            secrets = (spec.resolved_api_key(),)
        except Exception:
            secrets = ()
        if job.thread.is_alive():
            row["ok"], row["error"] = False, f"超时（超过 {timeout:.0f} 秒无响应）"
        elif job.error is not None:
            row["ok"], row["error"] = False, short_error(job.error, secrets)
            status = getattr(job.error, "status_code", None) or getattr(job.error, "code", None)
            if status in (404, 405):
                row["error"] += "（该平台不提供模型列表接口，请手动输入模型名）"
        else:
            ids = job.result[:MAX_LISTED_MODELS]
            row["ok"], row["models"] = True, [model_ref(spec, m) for m in ids]
    return list(rows.values())


def _configured(spec) -> bool:
    try:
        return bool(spec.has_credentials())
    except Exception:
        return False


# --------------------------------------------------------------------------
# Scholarly sources
# --------------------------------------------------------------------------


def probe_source(name: str) -> Dict[str, Any]:
    """One tiny query through the normal search function and rate limiter (no retries)."""
    import utils.scholar_utils as S

    with S.probe_mode():
        t0 = time.time()
        hits, err = None, None
        try:
            hits = S.SOURCES[name](SCHOLAR_QUERY, 1)
        except Exception as e:
            err = e
        wall_ms = int((time.time() - t0) * 1000)
        diag = S.last_http_result(name)
    row: Dict[str, Any] = {"name": name, "ok": None, "latency_ms": None, "hits": None, "error": None,
                           "busy": False}
    if diag and diag.get("elapsed_ms") is not None:
        row["latency_ms"] = int(diag["elapsed_ms"])
    if err is not None:
        row.update(ok=False, latency_ms=row["latency_ms"] or wall_ms, error=short_error(err))
    elif diag is None:
        row.update(error="未发出请求")
    elif diag.get("status") == 200:
        row.update(ok=True, hits=len(hits) if isinstance(hits, list) else 0)
    else:
        status = diag.get("status")
        msg = short_error(diag.get("error") or (f"HTTP {status}" if status else "请求失败"))
        busy = S.is_transient_status(status) or "after repeated HTTP 429" in msg
        if status == 202:
            msg += "（对方正在排队处理，不是错误；正式检索时会等一会儿再取）"
        elif status == 429 or "after repeated HTTP 429" in msg:
            msg += "（请求过于频繁，暂时被限流；正式检索时会自动等待后重试）"
        elif busy:
            msg += "（服务暂时繁忙；正式检索时会自动重试）"
        elif status in (401, 403):
            msg += "（API Key 无效或无权限）"
        # busy: a "come back later" answer (202/408/429/5xx), shown as 暂时繁忙, not as a failure
        row.update(ok=False, error=msg, busy=bool(busy))
    return row


def check_scholar(run_probes: bool, timeout: float) -> List[Dict[str, Any]]:
    import utils.scholar_utils as S

    names = S.enabled_sources()
    if not run_probes:
        return [{"name": n, "ok": None, "latency_ms": None, "hits": None, "error": None, "busy": False}
                for n in names]

    def make(n):
        return lambda: probe_source(n)

    jobs = run_parallel({n: make(n) for n in names}, timeout + 5)
    rows = []
    for n in names:
        job = jobs[n]
        if job.thread.is_alive():
            rows.append({"name": n, "ok": False, "latency_ms": None, "hits": None,
                         "error": f"超时（超过 {timeout:.0f} 秒无响应）", "busy": False})
        elif job.error is not None:
            rows.append({"name": n, "ok": False, "latency_ms": None, "hits": None,
                         "error": short_error(job.error), "busy": False})
        else:
            rows.append(job.result)
    return rows


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def collect(ping: bool = False, scholar: bool = False, models: Optional[List[str]] = None,
            timeout: float = DEFAULT_TIMEOUT_SEC, list_models_too: bool = False,
            ping_models: Optional[List[str]] = None) -> Dict[str, Any]:
    """The parts run side by side; each check inside them has its own deadline."""
    report: Dict[str, Any] = {"providers": [], "scholar": []}
    if list_models_too:
        report["models"] = []
    import utils.providers  # noqa: F401  (first imports on this thread, not inside the jobs)
    import utils.scholar_utils  # noqa: F401

    if ping or list_models_too:
        # SDK imports can take many seconds on a cold start: do them before the
        # deadlines below start counting.
        _preload_sdks(_configured_kinds())

    progress: Dict[str, Any] = {}
    parts = {
        "providers": lambda: check_providers(ping, models or [], timeout, progress, ping_models),
        "scholar": lambda: check_scholar(scholar, timeout),
    }
    if list_models_too:
        parts["models"] = lambda: list_models(timeout)
    jobs = run_parallel(parts, timeout + 10)
    for key, job in jobs.items():
        if job.thread.is_alive():
            print(f"Warning: {key} check did not finish in time.", file=sys.stderr)
            if key == "providers":
                report[key] = _timed_out_provider_rows(progress, models or [], timeout, ping_models)
        elif job.error is not None:
            print(f"Warning: {key} check failed: {short_error(job.error)}", file=sys.stderr)
        else:
            report[key] = job.result
    return report


def _configured_kinds() -> List[str]:
    """Wire protocols of the configured providers (quietly; check_providers reports errors)."""
    try:
        import utils.providers as P

        try:
            specs = P.list_providers()
        except Exception:
            specs = P._builtin_providers()
        kinds = []
        for spec in specs:
            try:
                if spec.has_credentials():
                    kinds.append(spec.kind)
            except Exception:
                pass
        return kinds
    except Exception:
        return []


def _timed_out_provider_rows(progress: Dict[str, Any], models: List[str],
                             timeout: float, ping_models: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """The configuration rows when the ping phase overran its deadline: every
    provider still waiting for its answer is reported as timed out, not dropped."""
    rows = progress.get("rows")
    targets = progress.get("targets") or {}
    if rows is None:
        try:
            rows = check_providers(False, models, timeout, None, ping_models)
        except Exception as e:
            print(f"Warning: providers check failed: {short_error(e)}", file=sys.stderr)
            return []
    rows = [dict(r) for r in rows.values()] if isinstance(rows, dict) else [dict(r) for r in rows]
    for row in rows:
        if row["name"] in targets and row.get("ok") is None:
            row["ok"], row["error"] = False, f"超时（超过 {timeout:.0f} 秒无响应）"
    return rows


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _status(row: Dict[str, Any]) -> str:
    if row.get("ok") is True:
        extra = f"{row['latency_ms']} ms" if row.get("latency_ms") is not None else ""
        if row.get("hits") is not None:
            extra = f"{row['hits']} 条结果  {extra}".strip()
        return f"可用  {extra}".rstrip()
    if row.get("ok") is False and row.get("busy"):
        return f"暂时繁忙  {row.get('error') or ''}".rstrip()
    if row.get("ok") is False:
        return f"失败  {row.get('error') or ''}".rstrip()
    if row.get("error"):
        return f"未测试  {row['error']}"
    return "未测试"


def _table(headers: List[str], rows: List[List[str]]) -> List[str]:
    widths = [max(_width(h), *(_width(r[i]) for r in rows)) if rows else _width(h)
              for i, h in enumerate(headers)]
    out = ["  " + "  ".join(_pad(h, widths[i]) for i, h in enumerate(headers)).rstrip()]
    for r in rows:
        out.append("  " + "  ".join(_pad(c, widths[i]) for i, c in enumerate(r)).rstrip())
    return out


def render_text(report: Dict[str, Any], ping: bool, scholar: bool) -> str:
    providers = report.get("providers", [])
    shown = [p for p in providers if p["configured"] or p["ok"] is not None]
    hidden = [p["name"] for p in providers if p not in shown]
    lines = ["PaperOrchestra 服务检查", ""]
    lines.append(f"LLM 服务商（已配置 {sum(1 for p in providers if p['configured'])} / 共 {len(providers)}）")
    if shown:
        lines += _table(
            ["服务商", "前缀", "协议", "模型", "状态"],
            [[p["label"], p["name"], p["kind"], p["model"] or "—", _status(p)] for p in shown],
        )
    else:
        lines.append("  （没有已配置的服务商：请在 .env 或 providers.json 中填写 API Key）")
    if hidden:
        lines.append(f"  未配置：{', '.join(hidden)}")
    lines.append("")
    sources = report.get("scholar", [])
    lines.append(f"学术检索源（已启用 {len(sources)} 个）")
    if sources:
        lines += _table(["检索源", "状态"], [[s["name"], _status(s)] for s in sources])
    else:
        lines.append("  （未启用任何检索源）")
    if "models" in report:
        lines.append("")
        lines.append("各平台可用模型（来自其 API）")
        if not report["models"]:
            lines.append("  （没有已配置的服务商）")
        for row in report["models"]:
            if row["ok"]:
                shown_models = row["models"][:12]
                more = len(row["models"]) - len(shown_models)
                tail = f" …另有 {more} 个" if more > 0 else ""
                lines.append(f"  {row['label']}（{len(row['models'])} 个）：{', '.join(shown_models)}{tail}")
            else:
                lines.append(f"  {row['label']}：失败  {row['error'] or ''}".rstrip())
    tips = []
    if not ping:
        tips.append(f"加 --ping 向已配置的服务商各发一次极小的测试请求（最多 {PING_MAX_TOKENS} 个输出 token）")
    if not scholar:
        tips.append("加 --scholar 测试各检索源的连通性")
    if tips:
        lines += ["", "提示：" + "；".join(tips) + "。"]
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查 LLM 服务商与学术检索源是否可用。")
    parser.add_argument("--json", action="store_true", help="在标准输出打印一个 JSON 对象")
    parser.add_argument("--ping", action="store_true",
                        help=f"向每个已配置的服务商发送一次极小的请求（最多 {PING_MAX_TOKENS} 个输出 token）")
    parser.add_argument("--scholar", action="store_true", help="对每个已启用的检索源做一次极小的查询")
    parser.add_argument("--model", action="append", default=[], metavar="REF",
                        help="只测试这个模型（如 qwen:qwen-max）；可重复")
    parser.add_argument("--ping-model", action="append", default=[], metavar="NAME=MODEL",
                        help="测试时这个平台改用这个模型（如 gemini=gemini-3.8-flash）；其余平台照常测试；可重复")
    parser.add_argument("--list-models", action="store_true",
                        help="列出每个已配置服务商的 API 提供的模型（不产生费用）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SEC,
                        help=f"每项检查的超时秒数（默认 {DEFAULT_TIMEOUT_SEC:.0f}）")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    real_stdout = sys.stdout
    # Library modules print status lines (e.g. on import): keep stdout for the result only.
    sys.stdout = sys.stderr
    try:
        report = collect(args.ping, args.scholar, args.model, max(1.0, args.timeout), args.list_models,
                         args.ping_model)
    finally:
        sys.stdout = real_stdout
    if args.json:
        real_stdout.write(json.dumps(report, ensure_ascii=True) + "\n")
    else:
        try:
            real_stdout.reconfigure(errors="replace")  # e.g. a GBK console
        except Exception:
            pass
        real_stdout.write(render_text(report, args.ping, args.scholar))
    real_stdout.flush()
    return 0


if __name__ == "__main__":
    exit_code = main()
    # A check that outlived its deadline may still be running in a daemon thread:
    # leave now, so nothing it prints can follow the result on stdout.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
