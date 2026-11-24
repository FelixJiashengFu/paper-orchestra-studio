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

import base64
import os
from typing import Any, Callable, Dict, List, Optional

from google.genai import types

from utils.anthropic_utils import (
    CLAUDE_MANY_IMAGES,
    CLAUDE_MANY_IMAGES_MAX_EDGE,
    call_claude_with_content,
    call_claude_with_pdf,
    make_claude_image_block,
)
from utils.gemini_utils import (
    call_gemini_with_contents,
    generate_image_with_gemini,
    parse_gemini_json_results,
)
from utils.openai_utils import (
    call_openai_models_with_content,
    get_openai_image_part_from_path,
    parse_openai_json_results,
)
from utils import usage_tracker
from utils.image_fit import fit_with_limits
from utils.pdf_utils import load_paper
from utils.agent_cli_utils import call_agent_cli, find_cli
from utils.providers import (
    CLI_KIND,
    ProviderSpec,
    host_agent,
    host_agent_strict,
    config_value,
    configured_providers,
    get_provider,
    model_supports_vision,
    redact_secrets,
    resolve_model,
    split_model_ref,
)


def detect_provider(model_name: str) -> str:
    """Returns the provider *name* a model reference resolves to.

    Kept for backward compatibility; legacy names ("anthropic", "deepseek",
    "openai", "gemini") are unchanged, new providers return their own name.
    """
    return resolve_model(model_name)[0].name


def supports_vision(model_name: str) -> bool:
    spec, bare = resolve_model(model_name)
    return model_supports_vision(spec, bare)


def has_anthropic_credentials() -> bool:
    return get_provider("anthropic").has_credentials()


def has_deepseek_credentials() -> bool:
    return get_provider("deepseek").has_credentials()


def has_openai_credentials() -> bool:
    return get_provider("openai").has_credentials()


def has_gemini_credentials() -> bool:
    return get_provider("gemini").has_credentials()


def has_any_llm_credentials() -> bool:
    return bool(configured_providers())


def _task_override(task: str) -> str:
    """PO_TASK_MODEL_<TASK>, else providers.json task_models; "auto" counts as unset."""
    env_val = os.getenv(f"PO_TASK_MODEL_{task.upper()}", "").strip()
    if env_val:
        return "" if env_val.lower() == "auto" else env_val
    task_models = config_value("task_models") or {}
    if isinstance(task_models, dict) and task_models.get(task):
        val = str(task_models[task]).strip()
        return "" if val.lower() == "auto" else val
    return ""


def _global_auto_model() -> str:
    val = (os.getenv("PO_AUTO_MODEL", "") or str(config_value("auto_model") or "")).strip()
    return "" if val.lower() == "auto" else val


def _non_legacy_default() -> str:
    """Default model of the first configured non-legacy provider (custom first)."""
    configured = configured_providers()
    configured.sort(key=lambda p: (p.builtin, p.name))  # custom providers first
    for spec in configured:
        if spec.name in ("anthropic", "deepseek", "openai", "gemini"):
            continue
        model = spec.resolved_default_model()
        if model:
            return f"{spec.name}:{model}"
    return ""


# Finer steps that can be pinned on their own (PO_TASK_MODEL_<STEP>). Unset,
# a step runs with whatever its parent task resolves to, exactly as before
# they were split out.
TASK_PARENTS: Dict[str, str] = {
    "relevance_screen": "literature_query",  # dropping off-topic search hits
    "plot_retrieval": "plot_code",  # picking few-shot reference figures
    "plot_style": "plot_code",  # polishing the figure description's style
    "diagram_code": "plot_code",  # Matplotlib diagram when no image model can draw it
    "plot_caption": "plot_critic",  # writing the figure caption
}


def resolve_task_model(requested_model: str, task: str) -> str:
    """
    Resolves 'auto' to the optimal model per pipeline subtask based on configured API keys.
    Explicit model names are returned unchanged.
    """
    cleaned = (requested_model or "").strip()
    if cleaned and cleaned.lower() != "auto":
        return cleaned

    # Host-agent mode (PO_HOST_AGENT=muse): every step runs on the host agent's
    # own quota. Strict (the default) puts it before any per-task pin; with
    # PO_HOST_AGENT_STRICT=0 the pins below still win.
    host = host_agent()
    if host and host_agent_strict():
        return f"{host}:default"

    parent = TASK_PARENTS.get(task)
    if parent:
        return _task_override(task) or resolve_task_model("auto", parent)

    override = _task_override(task) or _global_auto_model()
    if override:
        return override
    if host:
        return f"{host}:default"

    has_claude = has_anthropic_credentials()
    has_ds = has_deepseek_credentials()
    has_oai = has_openai_credentials()
    has_gem = has_gemini_credentials()

    def pick(*candidates: tuple[bool, str]) -> str:
        for available, model in candidates:
            if available:
                return model
        return _non_legacy_default() or "gemini-3.1-pro-preview"

    # DeepSeek (V4 Pro, the old deepseek-reasoner's successor) excels at structured planning,
    # peer review & code/query generation; Claude excels at strict citation synthesis, visual
    # figure/PDF inspection & LaTeX refinement. Model ids as the providers' model lists showed
    # them on 2026-11-18 (deepseek-chat/-reasoner and o3 are retired or retiring; Claude stays
    # at Sonnet 5.5 or newer). Any task can be pinned with PO_TASK_MODEL_<TASK> or PO_AUTO_MODEL.
    routing = {
        "outline": pick(
            (has_ds, "deepseek-v4-pro"),
            (has_claude, "claude-sonnet-5-5"),
            (has_oai, "gpt-6.2-sol"),
            (has_gem, "gemini-4-argon"),
        ),
        "literature_query": pick(
            (has_ds, "deepseek-flash"),
            (has_claude, "claude-sonnet-5-5"),
            (has_oai, "gpt-6.1-terra"),
            (has_gem, "gemini-3.8-flash"),
        ),
        "literature_write": pick(
            (has_claude, "claude-sonnet-5-5"),
            (has_ds, "deepseek-flash"),
            (has_oai, "gpt-6.2-sol"),
            (has_gem, "gemini-4-argon"),
        ),
        "plot_plan": pick(
            (has_claude, "claude-sonnet-5-5"),
            (has_oai, "gpt-6.2-sol"),
            (has_gem, "gemini-4-argon"),
            (has_ds, "deepseek-flash"),
        ),
        "plot_code": pick(
            (has_ds, "deepseek-flash"),
            (has_claude, "claude-sonnet-5-5"),
            (has_oai, "gpt-6.1-terra"),
            (has_gem, "gemini-4-argon"),
        ),
        "plot_critic": pick(
            (has_claude, "claude-sonnet-5-5"),
            (has_oai, "gpt-6.2-sol"),
            (has_gem, "gemini-4-argon"),
            (has_ds, "deepseek-flash"),
        ),
        "section_writing": pick(
            (has_claude, "claude-sonnet-5-5"),
            (has_oai, "gpt-6.2-sol"),
            (has_gem, "gemini-4-argon"),
            (has_ds, "deepseek-v4-pro"),
        ),
        "peer_review": pick(
            (has_ds, "deepseek-v4-pro"),
            (has_claude, "claude-sonnet-5-5"),
            (has_oai, "gpt-6.2-sol"),
            (has_gem, "gemini-4-argon"),
        ),
        "content_refinement": pick(
            (has_claude, "claude-sonnet-5-5"),
            (has_oai, "gpt-6.2-sol"),
            (has_gem, "gemini-4-argon"),
            (has_ds, "deepseek-v4-pro"),
        ),
        "format_review": pick(
            (has_claude, "claude-sonnet-5-5"),
            (has_oai, "gpt-6.2-sol"),
            (has_gem, "gemini-4-argon"),
            (has_ds, "deepseek-flash"),
        ),
        "format_fix": pick(
            (has_claude, "claude-sonnet-5-5"),
            (has_ds, "deepseek-flash"),
            (has_oai, "gpt-6.2-sol"),
            (has_gem, "gemini-4-argon"),
        ),
        "citation_parse": pick(
            (has_ds, "deepseek-flash"),
            (has_claude, "claude-sonnet-5-5"),
            (has_oai, "gpt-6.1-terra"),
            (has_gem, "gemini-3.8-flash"),
        ),
    }
    return routing.get(task, pick(
        (has_claude, "claude-sonnet-5-5"),
        (has_ds, "deepseek-flash"),
        (has_oai, "gpt-6.2-sol"),
        (has_gem, "gemini-4-argon"),
    ))


def describe_auto_routing() -> Dict[str, str]:
    tasks = [
        "outline",
        "literature_query",
        "relevance_screen",
        "literature_write",
        "plot_retrieval",
        "plot_plan",
        "plot_style",
        "plot_code",
        "diagram_code",
        "plot_critic",
        "plot_caption",
        "section_writing",
        "peer_review",
        "content_refinement",
        "format_review",
        "format_fix",
    ]
    return {t: resolve_task_model("auto", t) for t in tasks}


# DeepSeek's server-side web tools (api-docs.deepseek.com/guides/web_tools,
# 2026-11-13 release): deepseek-flash searches and reads pages itself and
# answers with the result. Only model tokens are billed for them per the docs.
DEEPSEEK_WEB_TOOLS = [{"type": "deepseek_web_search"}, {"type": "deepseek_read_url"}]


def call_deepseek_with_web_search(prompt: str, model_name: str = "deepseek-flash",
                                  max_retries: int = 2) -> Dict[str, Any]:
    """One chat completion on DeepSeek with its web search / read-URL tools on.

    Returns {"raw_response", "parsed_response"} like the other callers (JSON
    parsed from the answer). Raises on API errors so the caller can fall back.
    """
    spec = get_provider("deepseek")
    return call_openai_models_with_content(
        content=prompt,
        model_name=model_name,
        max_retries=max_retries,
        generation_configs={"tools": DEEPSEEK_WEB_TOOLS},
        check_parsed_response_not_none=False,
        provider=spec,
    )


GEMINI_SEARCH_MODEL = "gemini-3-flash-preview"
_SEARCH_OFF = {"off", "none", "no", "false", "0", "关", "关闭", "不用"}


def literature_search_backend() -> tuple[str, str]:
    """Which web search finds candidate papers: ("gemini" | "deepseek", model) or ("", "").

    PO_TASK_MODEL_LITERATURE_SEARCH (or providers.json task_models) picks it:
      unset / "auto" -> Gemini + Google Search (gemini-3-flash-preview) when a
                        Gemini key is set, else no web search (as before);
      a Gemini model -> that model with Google Search;
      a DeepSeek model (deepseek-flash) -> DeepSeek's deepseek_web_search tool;
      "off"          -> no web search: keyword search in the scholarly APIs.
    Without web search, papers are found through Semantic Scholar / arXiv etc.
    with search queries from the literature_query model. Other platforms have
    no web search here; they fall back to that path too (with a warning).
    """
    if host_agent_strict():
        return "", ""  # no other LLM API: papers come from the scholarly APIs
    value = _task_override("literature_search")
    if value.lower() in _SEARCH_OFF:
        return "", ""
    if not value or value.lower() == "auto":
        return ("gemini", GEMINI_SEARCH_MODEL) if has_gemini_credentials() else ("", "")
    try:
        spec, bare = resolve_model(value)
    except Exception as e:  # unknown platform prefix etc.
        print(f"Warning: literature search model '{value}' cannot be used ({redact_secrets(e)}); "
              "searching the scholarly APIs instead.")
        return "", ""
    # The built-in platforms, or a providers.json entry that overrides one of them
    # (e.g. a relay base URL) with the same protocol; a relay without the web
    # tools fails the request and that search falls back to the scholarly APIs.
    if (spec.name, spec.kind) in (("gemini", "gemini"), ("deepseek", "openai")):
        if spec.has_credentials():
            return spec.name, bare
        print(f"Warning: literature search model '{value}' has no {spec.name} API key; "
              "searching the scholarly APIs instead.")
        return "", ""
    print(f"Warning: '{value}' ({spec.name}) has no web search here; only Gemini or DeepSeek models "
          "can search the web for papers. Searching the scholarly APIs instead.")
    return "", ""


def _usable(spec: ProviderSpec) -> bool:
    if spec.kind == CLI_KIND:
        return bool(find_cli(spec.name))
    # Key-optional endpoints (Ollama, LM Studio, vLLM) work on their default
    # localhost URL even when <NAME>_BASE_URL is not set.
    return spec.has_credentials() or spec.key_optional


def credential_problems(model_refs: List[str]) -> List[str]:
    """Human-readable problems for models that cannot run with the current keys.

    Explicit refs are checked directly; "auto" is expanded to every task's
    resolved model. An empty list means every requested model has a usable
    provider.
    """
    problems: List[str] = []
    refs = [str(r or "").strip() or "auto" for r in model_refs]
    for ref in dict.fromkeys(r for r in refs if r.lower() != "auto"):
        spec, _ = resolve_model(ref)
        if spec.kind == CLI_KIND and not _usable(spec):
            problems.append(f"model '{ref}' -> '{spec.name}' CLI is not installed "
                            f"(install Muse Code and sign in, or set {spec.name.upper()}_CLI).")
            continue
        if not _usable(spec):
            envs = " / ".join(spec.api_key_env) or f"{spec.name.upper()}_API_KEY"
            problems.append(f"model '{ref}' -> provider '{spec.name}' has no API key (set {envs}).")
    if any(r.lower() == "auto" for r in refs):
        for task, model in describe_auto_routing().items():
            spec, _ = resolve_model(model)
            if spec.kind == CLI_KIND:
                if not _usable(spec):
                    problems.append(f"auto: every task runs on the '{spec.name}' CLI, which is not installed "
                                    f"(install Muse Code and sign in, or set {spec.name.upper()}_CLI).")
                    break
                continue
            if not _usable(spec):
                problems.append(
                    f"auto: task '{task}' resolved to '{model}', but provider '{spec.name}' has no API key. "
                    "Set PO_AUTO_MODEL (e.g. qwen:qwen-max), a <NAME>_DEFAULT_MODEL for a configured "
                    "platform, or one of ANTHROPIC/DEEPSEEK/OPENAI/GEMINI_API_KEY."
                )
                break
    return problems


def pick_available_lightweight_model() -> str:
    return resolve_task_model("auto", "citation_parse")


def identity_parse(response: str):
    return response


def get_llm_parser(model_name: str, return_json: bool = True) -> Callable:
    if not return_json:
        return identity_parse

    if resolve_model(model_name)[0].kind == "openai":
        return parse_openai_json_results
    return parse_gemini_json_results


def _split_openai_configs(generation_configs: Optional[Dict[str, Any]]) -> tuple[Optional[str], Dict[str, Any]]:
    cfg = dict(generation_configs) if generation_configs else {}
    system_prompt = cfg.pop("system_instruction", None)
    cfg.pop("temperature", None)
    # Gemini-only options must never reach an OpenAI-protocol endpoint.
    for k in ("tools", "response_modalities", "image_config", "thinking_config", "safety_settings"):
        cfg.pop(k, None)
    return system_prompt, cfg


def _image_path_to_bytes(path: str) -> Optional[bytes]:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def _fit_for_spec(raw: bytes, spec: ProviderSpec, n_images: int) -> bytes:
    """Scales one image down to the platform's known limits (ProviderSpec.image_*)."""
    limits = spec.image_limits()
    if not (limits.any() or spec.image_max_total_pixels):
        return raw
    return fit_with_limits(raw, limits, label=spec.name, n_images=n_images,
                           total_pixels=spec.image_max_total_pixels)


def _guess_mime(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return "image/jpeg"


def _dispatch_parts(
    spec: ProviderSpec,
    model: str,
    parts: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    parser: Callable,
    check_parsed_response_not_none: bool,
) -> Dict[str, Any]:
    """Sends normalised parts to the right wire protocol.

    parts: {"type": "text", "text"} | {"type": "image_bytes", "data", "mime_type"} | {"type": "image_path", "path"}
    cfg may contain "system_instruction" and sampling params.
    """
    vision = model_supports_vision(spec, model)
    n_images = sum(1 for p in parts if p["type"] in ("image_bytes", "image_path"))
    if n_images and not vision:
        print(
            f" >> [{spec.name}] Model '{model}' is text-only; proceeding without {n_images} image attachment(s)."
        )
        parts = [p for p in parts if p["type"] == "text"] + [{
            "type": "text",
            "text": f"[Note: {n_images} image attachment(s) omitted because the selected model is text-only.]",
        }]

    if spec.kind == CLI_KIND:
        # An agent CLI on the user's own sign-in (Muse Code): text in, text out.
        return call_agent_cli(
            prompt="\n".join(p["text"] for p in parts if p["type"] == "text" and p.get("text")),
            model_name=model,
            spec_name=spec.name,
            result_parsing_func=parser,
            check_parsed_response_not_none=check_parsed_response_not_none,
            system_prompt=cfg.get("system_instruction"),
        )

    if spec.kind == "anthropic":
        cfg = dict(cfg)
        sys_p = cfg.pop("system_instruction", None)
        blocks: List[Dict[str, Any]] = []
        max_edge = CLAUDE_MANY_IMAGES_MAX_EDGE if n_images > CLAUDE_MANY_IMAGES else None
        for p in parts:
            if p["type"] == "text":
                if p.get("text"):
                    blocks.append({"type": "text", "text": p["text"]})
            elif p["type"] == "image_bytes":
                blocks.append(make_claude_image_block(_fit_for_spec(p["data"], spec, n_images),
                                                      max_edge=max_edge, model_name=model))
            elif p["type"] == "image_path":
                raw = _image_path_to_bytes(p["path"])
                if raw:
                    blocks.append(make_claude_image_block(_fit_for_spec(raw, spec, n_images), p["path"],
                                                          max_edge=max_edge, model_name=model))
        return call_claude_with_content(
            content=blocks,
            model_name=model,
            generation_configs=cfg,
            result_parsing_func=parser,
            check_parsed_response_not_none=check_parsed_response_not_none,
            system_prompt=sys_p,
            provider=spec,
        )

    if spec.kind == "openai":
        sys_p, openai_cfg = _split_openai_configs(cfg)
        content: List[Dict[str, Any]] = []
        for p in parts:
            if p["type"] == "text" and p.get("text"):
                content.append({"type": "text", "text": p["text"]})
            elif p["type"] == "image_bytes":
                mime = p.get("mime_type", "image/jpeg")
                b64 = base64.b64encode(p["data"]).decode("utf-8")
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            elif p["type"] == "image_path" and os.path.exists(p["path"]):
                content.extend(get_openai_image_part_from_path(p["path"]))
        return call_openai_models_with_content(
            content=content,
            model_name=model,
            generation_configs=openai_cfg,
            result_parsing_func=parser,
            check_parsed_response_not_none=check_parsed_response_not_none,
            system_prompt=sys_p,
            provider=spec,
        )

    # gemini protocol
    gemini_parts: List[types.Part] = []
    for p in parts:
        if p["type"] == "text" and p.get("text"):
            gemini_parts.append(types.Part.from_text(text=p["text"]))
        elif p["type"] == "image_bytes":
            data = _fit_for_spec(p["data"], spec, n_images)
            mime = p.get("mime_type", "image/jpeg") if data is p["data"] else _guess_mime(data)
            gemini_parts.append(types.Part.from_bytes(data=data, mime_type=mime))
        elif p["type"] == "image_path":
            raw = _image_path_to_bytes(p["path"])
            if raw:
                raw = _fit_for_spec(raw, spec, n_images)
                gemini_parts.append(types.Part.from_bytes(data=raw, mime_type=_guess_mime(raw)))
    return call_gemini_with_contents(
        contents=gemini_parts,
        model_name=model,
        generation_configs=cfg,
        result_parsing_func=parser,
        check_parsed_response_not_none=check_parsed_response_not_none,
        provider=spec,
    )


def call_llm_with_text_prompt(
    prompt: str,
    model_name: str,
    generation_configs: Optional[Dict[str, Any]] = None,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Unified dispatcher for any provider (OpenAI-, Anthropic- or Gemini-protocol)."""
    parser = result_parsing_func if result_parsing_func is not None else get_llm_parser(model_name, return_json)
    spec, model = resolve_model(model_name)
    cfg = dict(generation_configs) if generation_configs else {}
    return _dispatch_parts(spec, model, [{"type": "text", "text": prompt}], cfg, parser,
                           check_parsed_response_not_none)


def call_llm_with_images(
    prompt: str,
    images: list[str],
    model_name: str,
    generation_configs: Optional[Dict[str, Any]] = None,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Unified image+prompt dispatcher; degrades to text-only on text-only models."""
    parser = result_parsing_func if result_parsing_func is not None else get_llm_parser(model_name, return_json)
    spec, model = resolve_model(model_name)
    cfg = dict(generation_configs) if generation_configs else {}
    parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    parts += [{"type": "image_path", "path": p} for p in images or []]
    return _dispatch_parts(spec, model, parts, cfg, parser, check_parsed_response_not_none)


def call_llm_with_multimodal_parts(
    parts: List[Dict[str, Any]],
    model_name: str,
    generation_configs: Optional[Dict[str, Any]] = None,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func: Optional[Callable] = None,
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes interleaved text/image parts on any provider.
    Each part dict uses:
      - {"type": "text", "text": "..."}
      - {"type": "image_bytes", "data": b"...", "mime_type": "image/jpeg"}
      - {"type": "image_path", "path": "..."}
    """
    parser = result_parsing_func if result_parsing_func is not None else get_llm_parser(model_name, return_json)
    cfg = dict(generation_configs) if generation_configs else {}
    if system_prompt is not None and "system_instruction" not in cfg:
        cfg["system_instruction"] = system_prompt
    spec, model = resolve_model(model_name)
    if not model_supports_vision(spec, model):
        # Same text as the original DeepSeek branch: the text parts joined with
        # "\n", images left out without a note.
        merged = "\n".join(p["text"] for p in parts if p.get("type") == "text" and p.get("text"))
        parts = [{"type": "text", "text": merged}]
    return _dispatch_parts(spec, model, parts, cfg, parser, check_parsed_response_not_none)


def call_llm_with_pdf(
    pdf_path: str,
    prompt: str,
    model_name: str,
    system_instruction: Optional[str] = None,
    temperature: float = 0.7,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Unified function to evaluate a PDF.
    Native PDF for Gemini- and Anthropic-protocol providers (text fallback if the
    endpoint refuses documents); extracted text for OpenAI-protocol providers.
    """
    parser = result_parsing_func if result_parsing_func is not None else get_llm_parser(model_name, return_json)
    spec, model = resolve_model(model_name)

    def text_fallback() -> Dict[str, Any]:
        paper_text = load_paper(pdf_path, min_size=100)
        full_prompt = f"Paper Content:\n{paper_text}\n\nTask:\n{prompt}"
        cfg: Dict[str, Any] = {"temperature": temperature}
        if system_instruction is not None:
            cfg["system_instruction"] = system_instruction
        return _dispatch_parts(spec, model, [{"type": "text", "text": full_prompt}], cfg, parser,
                               check_parsed_response_not_none)

    if not model_supports_vision(spec, model):
        return text_fallback()  # text-only endpoints take no document blocks either

    if spec.kind == "anthropic":
        try:
            return call_claude_with_pdf(
                pdf_path=pdf_path,
                prompt=prompt,
                model_name=model,
                system_prompt=system_instruction,
                temperature=temperature,
                check_parsed_response_not_none=check_parsed_response_not_none,
                result_parsing_func=parser,
                provider=spec,
            )
        except Exception as e:
            print(f"Warning: native PDF call on '{spec.name}' failed ({redact_secrets(e)}); falling back to extracted PDF text.")
            return text_fallback()

    if spec.kind == "openai":
        return text_fallback()

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    contents = [
        types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
        types.Part.from_text(text=prompt),
    ]
    generation_configs: Dict[str, Any] = {"temperature": temperature}
    if system_instruction is not None:
        generation_configs["system_instruction"] = system_instruction
    try:
        return call_gemini_with_contents(
            contents=contents,
            model_name=model,
            generation_configs=generation_configs,
            result_parsing_func=parser,
            check_parsed_response_not_none=check_parsed_response_not_none,
            provider=spec,
        )
    except Exception as e:
        if spec.name == "gemini":
            raise
        print(f"Warning: native PDF call on '{spec.name}' failed ({redact_secrets(e)}); falling back to extracted PDF text.")
        return text_fallback()


# --------------------------------------------------------------------------
# Image generation (diagrams)
# --------------------------------------------------------------------------


DEFAULT_IMAGE_MODEL = "gemini-3-pro-image"


def resolve_image_model(image_model_name: str) -> str:
    """'auto' (or an empty field) picks the image model the desktop shows as its default.

    It is the Gemini image model when a Gemini key (or Vertex AI) is set, and ""
    otherwise, which means diagrams are drawn with Matplotlib code. Before this,
    "auto" reached can_generate_images() as a literal model name and every
    diagram silently fell back to Matplotlib.
    """
    if host_agent_strict():
        return ""  # no other API: diagrams are drawn with Matplotlib code
    cleaned = (image_model_name or "").strip()
    if cleaned and cleaned.lower() != "auto":
        return cleaned
    # PO_TASK_MODEL_IMAGE (or providers.json task_models.image), set from the
    # desktop's per-step model panel, comes before the Gemini default.
    override = _task_override("image")
    if override:
        return override
    return DEFAULT_IMAGE_MODEL if has_gemini_credentials() else ""


def can_generate_images(image_model_name: str) -> bool:
    """True only for an endpoint that plausibly serves this image model.

    A bare name keeps the original rule: a Gemini image model ("gemini" in the
    name) on a Gemini provider with credentials; anything else (gpt-image-1,
    dall-e-3, imagen-*) gets the Matplotlib diagram, as before. An explicit
    "provider:model" ref opts in to that provider's image endpoint (Gemini-
    or OpenAI-protocol).
    """
    if not (image_model_name or "").strip():
        return False
    prefix, _ = split_model_ref(image_model_name)
    spec, model = resolve_model(image_model_name)
    if not spec.has_credentials():
        return False
    if prefix is None:
        return spec.kind == "gemini" and "gemini" in model.lower()
    return spec.kind in ("gemini", "openai")


# Gemini image models Google has shut down, and the GA model that replaced each
# (https://ai.google.dev/gemini-api/docs/deprecations).
RETIRED_GEMINI_IMAGE_MODELS = {"gemini-3-pro-image-preview": "gemini-3-pro-image"}


def generate_image(
    image_model_name: str,
    prompt: str,
    aspect_ratio: str = "16:9",
    system_instruction: Optional[str] = None,
) -> Optional[str]:
    """Returns a base64 PNG/JPEG, or None. Gemini image models and OpenAI-style /images endpoints."""
    spec, model = resolve_model(image_model_name)
    if spec.kind == "gemini" and model in RETIRED_GEMINI_IMAGE_MODELS:
        # Projects saved before the shutdown still name the preview; its GA
        # successor is the same model, so their figures keep working.
        replacement = RETIRED_GEMINI_IMAGE_MODELS[model]
        print(f" >> [Image] {model} has been shut down by Google; using {replacement}.")
        model = replacement
    if spec.kind == "gemini":
        cfg = {"system_instruction": system_instruction} if system_instruction else {}
        return generate_image_with_gemini(
            model_name=model, prompt=prompt, aspect_ratio=aspect_ratio,
            generation_configs=cfg, provider=spec,
        )
    if spec.kind == "openai":
        from utils.providers import get_client

        size = {"16:9": "1536x1024", "4:3": "1536x1024", "1:1": "1024x1024",
                "9:16": "1024x1536", "3:4": "1024x1536"}.get(aspect_ratio, "1536x1024")
        full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
        client = get_client(spec)
        for attempt in range(3):
            try:
                kwargs: Dict[str, Any] = {"model": model, "prompt": full_prompt, "size": size, "n": 1}
                if not model.startswith("gpt-image"):
                    kwargs["response_format"] = "b64_json"
                try:
                    rsp = client.images.generate(**kwargs)
                except Exception:
                    usage_tracker.record_error(spec.name, model)
                    raise
                usage_tracker.record_response(spec.name, model, rsp)
                item = rsp.data[0]
                if getattr(item, "b64_json", None):
                    return item.b64_json
                if getattr(item, "url", None):
                    import requests

                    return base64.b64encode(requests.get(item.url, timeout=60).content).decode("utf-8")
            except Exception as e:
                msg = str(e).lower()
                print(f"Warning: image generation on '{spec.name}' failed (attempt {attempt + 1}/3): {redact_secrets(e)}")
                if "size" in msg and size != "1024x1024":
                    size = "1024x1024"
                    continue
                if "response_format" in msg and "response_format" in kwargs:
                    model = model  # retry without the param
                    kwargs.pop("response_format", None)
                    try:
                        try:
                            rsp = client.images.generate(**kwargs)
                        except Exception:
                            usage_tracker.record_error(spec.name, model)
                            raise
                        usage_tracker.record_response(spec.name, model, rsp)
                        item = rsp.data[0]
                        if getattr(item, "b64_json", None):
                            return item.b64_json
                    except Exception:
                        pass
        return None
    print(f"Warning: provider '{spec.name}' ({spec.kind}) has no image generation endpoint.")
    return None
