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
import io
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

from anthropic import Anthropic
from dotenv import load_dotenv

from utils.gemini_utils import parse_gemini_json_results

dot_file = os.path.join(os.path.dirname(__file__), "../.env")
load_dotenv(dot_file)

from utils import usage_tracker  # noqa: E402
from utils.providers import (  # noqa: E402
    ProviderSpec,
    get_client,
    get_provider,
    mark_text_only,
    redact_secrets,
    resolve_model,
    vision_error_scope,
)


def get_anthropic_client() -> Anthropic:
    """Shared client for ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL (kept for backward compatibility)."""
    return get_client(get_provider("anthropic"))


def detect_image_media_type(img_bytes: bytes, fallback_path: str = "") -> str:
    """Detects exact image media type from file header bytes for Anthropic."""
    if img_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if img_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if img_bytes.startswith(b"RIFF") and img_bytes[8:12] == b"WEBP":
        return "image/webp"
    if img_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"

    lower = fallback_path.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/png"


# The Messages API rejects an image with a side over 8000 px (2000 px once a
# request carries more than 20 images) and one over ~5 MB, and the current
# models look at no more than 2576 px on the long edge anyway (larger images are
# scaled down on Anthropic's side). Matplotlib figures saved at 300 dpi with
# bbox_inches="tight" can exceed 8000 px, so oversized images are scaled down
# here, on the copy sent to Claude only; the figure files are not touched.
_CLAUDE_IMAGE_MAX_EDGE = 2576
_CLAUDE_API_MAX_EDGE = 8000
CLAUDE_MANY_IMAGES_MAX_EDGE = 2000
CLAUDE_MANY_IMAGES = 20
_CLAUDE_IMAGE_MAX_BYTES = 3_900_000  # raw bytes; base64 of this stays under the 5 MiB (5,242,880) limit


def _claude_image_max_edge() -> int:
    try:
        return max(200, int(os.getenv("PO_CLAUDE_IMAGE_MAX_EDGE", "") or _CLAUDE_IMAGE_MAX_EDGE))
    except ValueError:
        return _CLAUDE_IMAGE_MAX_EDGE


def _image_edge_limit(model_name: Optional[str], max_edge: Optional[int]) -> Optional[int]:
    """Long-edge limit for the copy sent to `model_name`, or None to send it as is.

    The current Claude models look at up to 2576 px, so larger images are
    scaled to that. Older Claude models are scaled on Anthropic's side to an
    even smaller size, so here they only get the hard API limits (8000 px, or
    2000 px with more than 20 images), which would otherwise be a 400. Other
    models behind an Anthropic-compatible endpoint get the image unchanged, as
    before.
    """
    hard = min(int(max_edge or _CLAUDE_API_MAX_EDGE), _CLAUDE_API_MAX_EDGE)
    if model_name is None or is_new_claude_model(model_name):
        return min(_claude_image_max_edge(), hard)
    if "claude" in (model_name or "").lower():
        return hard
    return None


def fit_image_for_claude(
    img_bytes: bytes, max_edge: Optional[int] = None, model_name: Optional[str] = None
) -> bytes:
    """Returns the image unchanged when it is within Claude's limits, else a
    scaled-down copy (same format where possible, JPEG if still too large).
    Any failure (no Pillow, unreadable image) returns the original bytes."""
    limit = _image_edge_limit(model_name, max_edge)
    if limit is None:
        return img_bytes
    try:
        from PIL import Image, ImageOps  # matplotlib depends on Pillow
    except ImportError:
        return img_bytes
    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            w, h = im.size
            if max(w, h) <= limit and len(img_bytes) <= _CLAUDE_IMAGE_MAX_BYTES:
                return img_bytes
            fmt = (im.format or "PNG").upper()
            is_jpeg = fmt in ("JPEG", "JPG", "MPO")  # MPO: a JPEG with extra frames (phone photos)
            if getattr(im, "is_animated", False) and fmt != "MPO":
                return img_bytes
            target = min(limit, max(w, h))  # long edge of the copy
            if is_jpeg and target * 2 <= max(w, h):
                im.draft("RGB", (max(1, w * target // max(w, h)), max(1, h * target // max(w, h))))
            has_alpha = "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info)
            # Re-encoding drops EXIF, so apply its rotation to the pixels first.
            src = ImageOps.exif_transpose(im) if im.getexif().get(0x0112, 1) != 1 else im
            if src.mode.startswith("I;16") or src.mode in ("I", "F"):
                # 16/32-bit grayscale: convert("RGB") would clip values to 0..255
                # (an almost white picture); rescale to 8 bit first.
                gray = src.convert("F")
                lo, hi = gray.getextrema()
                if src.mode.startswith("I;16"):
                    lo, hi = 0.0, 65535.0
                span = (hi - lo) or 1.0
                img = gray.point(lambda v: (v - lo) * 255.0 / span).convert("L").convert("RGB")
            else:
                mode = "RGBA" if has_alpha else "RGB"
                img = src if src.mode == mode else src.convert(mode)
            resample = getattr(Image, "Resampling", Image).LANCZOS

            def shrink_to(edge: int):
                sw, sh = img.size
                if max(sw, sh) <= edge:
                    return img
                k = edge / float(max(sw, sh))
                return img.resize((max(1, round(sw * k)), max(1, round(sh * k))), resample, reducing_gap=3.0)

            def encode(pic, as_jpeg: bool, quality: int = 90) -> bytes:
                out = io.BytesIO()
                if as_jpeg:
                    flat = pic
                    if pic.mode == "RGBA":
                        flat = Image.new("RGB", pic.size, (255, 255, 255))
                        flat.paste(pic, mask=pic.getchannel("A"))
                    elif pic.mode != "RGB":
                        flat = pic.convert("RGB")
                    flat.save(out, format="JPEG", quality=quality)
                elif fmt == "WEBP":
                    pic.save(out, format="WEBP", quality=quality)
                else:
                    pic.save(out, format="PNG")
                return out.getvalue()

            pic = shrink_to(target)
            data = encode(pic, is_jpeg)
            if len(data) > _CLAUDE_IMAGE_MAX_BYTES and not is_jpeg:
                data = encode(pic, True)
            # Still too large (noisy heatmaps, photos): smaller and lower quality until it fits.
            edge, quality = max(pic.size), 85
            for _ in range(8):
                if len(data) <= _CLAUDE_IMAGE_MAX_BYTES:
                    break
                edge = max(200, int(edge * 0.8))
                pic = shrink_to(edge)
                data = encode(pic, True, quality)
                quality = max(60, quality - 10)
            if len(data) > _CLAUDE_IMAGE_MAX_BYTES:
                return img_bytes  # let the API's own answer (and the text-only retry) decide
            if pic.size == (w, h):
                print(f" >> [anthropic] Image {w}x{h} re-encoded for Claude "
                      f"({len(img_bytes) // 1000} KB -> {len(data) // 1000} KB).")
            else:
                why = f"limit {limit} px per side" if max(pic.size) >= target else "to fit the 5 MB limit"
                print(f" >> [anthropic] Image {w}x{h} scaled to {pic.size[0]}x{pic.size[1]} for Claude ({why}).")
            return data
    except Image.DecompressionBombError:
        print(" >> [anthropic] Image is too large to scale down here (Pillow's pixel limit); sent as is.")
        return img_bytes
    except Exception:
        return img_bytes


def make_claude_image_block(
    img_bytes: bytes, fallback_path: str = "", max_edge: Optional[int] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    img_bytes = fit_image_for_claude(img_bytes, max_edge, model_name)
    media_type = detect_image_media_type(img_bytes, fallback_path)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(img_bytes).decode("utf-8"),
        },
    }


# Claude Opus 4.7+, the 5-series, Fable / Mythos and Haiku 5.5 reject sampling
# parameters (temperature / top_p / top_k -> HTTP 400) and most of them think
# adaptively by default. Thinking tokens count against max_tokens, so these
# models get a larger budget, streamed (the SDK refuses long non-streaming
# requests). Older models keep the original request unchanged.
_NEW_CLAUDE_MODEL = re.compile(
    r"claude-(?:(?:opus|sonnet|haiku)-(?:[5-9]|4-[7-9])|fable|mythos)", re.IGNORECASE
)
_SAMPLING_PARAMS = ("temperature", "top_p", "top_k")


_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
# Models whose API default effort is "medium" (the rest default to "high").
_MEDIUM_DEFAULT_EFFORT = re.compile(r"claude-(?:opus|haiku)-5[-.]5", re.IGNORECASE)


class IncompleteStream(RuntimeError):
    """A streamed reply that ended without stop_reason (a proxy cut the stream)."""


class EmptyStream(IncompleteStream):
    """A streamed request that produced no stream events at all."""


# (provider, model) pairs whose endpoint streams replies without a stop_reason
# at all: after two such replies in a row they are accepted as they are.
_streams_without_stop_reason: set = set()
_notes_printed: set = set()


def _note_once(msg: str) -> None:
    if msg not in _notes_printed:
        _notes_printed.add(msg)
        print(msg)


def _sdk_api_version(client: Any) -> str:
    try:
        return str((getattr(client, "default_headers", None) or {}).get("anthropic-version", "") or "")
    except Exception:
        return ""


class ClaudeRefusal(RuntimeError):
    """stop_reason == "refusal" twice for the same request: not retried further."""


def is_new_claude_model(model_name: str) -> bool:
    return bool(_NEW_CLAUDE_MODEL.search(model_name or ""))


def _lower_effort(effort: Optional[str], model_name: str) -> Optional[str]:
    """The next effort level down from `effort` (None = the model's default), or None at "low"."""
    current = effort or ("medium" if _MEDIUM_DEFAULT_EFFORT.search(model_name or "") else "high")
    idx = _EFFORT_LEVELS.index(current) if current in _EFFORT_LEVELS else 2
    return _EFFORT_LEVELS[idx - 1] if idx > 0 else None


# Output cap (max_tokens) of the models above when the Models API cannot be
# asked: 128K for every Claude 4.7+ / 5-series / Fable / Mythos model.
_NEW_CLAUDE_OUTPUT_CAP = 128000
_output_caps: Dict[Any, Optional[int]] = {}


def _model_output_cap(client: Any, provider_name: str, model_name: str) -> int:
    """The model's maximum max_tokens, read once from the Models API
    (GET /v1/models/{id} -> max_tokens) and cached; falls back to the 128K of
    the current models when the endpoint (or a proxy in front of it) does not
    say."""
    key = (provider_name, model_name)
    if key not in _output_caps:
        cap = None
        try:
            info = client.with_options(timeout=15.0, max_retries=0).models.retrieve(model_name)
            cap = getattr(info, "max_tokens", None)
            if cap is None:
                cap = (getattr(info, "model_extra", None) or {}).get("max_tokens")
            cap = int(cap) if cap else None
        except Exception:
            cap = None
        _output_caps[key] = cap if cap and cap >= 1024 else None
    return _output_caps[key] or _NEW_CLAUDE_OUTPUT_CAP


def _new_claude_max_tokens(cap: int = _NEW_CLAUDE_OUTPUT_CAP) -> int:
    """PO_CLAUDE_MAX_TOKENS if set (never above the model's cap), else the cap itself."""
    try:
        wanted = int(os.getenv("PO_CLAUDE_MAX_TOKENS", "") or cap)
    except ValueError:
        wanted = cap
    return max(1024, min(wanted, cap))


def _allowed_max_tokens(api_msg: str) -> Optional[int]:
    """The limit named in a 400 such as 'max_tokens: 200000 > 128000, which is the maximum ...'."""
    m = re.search(r"max_tokens:\s*\d+\s*>\s*(\d+)", api_msg or "")
    return int(m.group(1)) if m else None


def _create_message(client: Any, kwargs: Dict[str, Any], stream: bool) -> Any:
    if not stream:
        return client.messages.create(**kwargs)
    with client.messages.stream(**kwargs) as s:
        try:
            return s.get_final_message()
        except AssertionError:  # the SDK's bare assert when no stream event arrived
            raise EmptyStream(
                "the stream returned no events (the endpoint may not support streaming)"
            ) from None


def _extract_claude_text(response: Any) -> str:
    parts: List[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text" and getattr(block, "text", None):
            parts.append(block.text)
    return "\n".join(parts)


def call_claude_with_content(
    content: List[Dict[str, Any]],
    model_name: str,
    max_retries: int = 5,
    base_interval_sec: int = 5,
    result_parsing_func: Callable = parse_gemini_json_results,
    generation_configs: Optional[Dict[str, Any]] = None,
    check_parsed_response_not_none: bool = True,
    system_prompt: Optional[str] = None,
    provider: Optional[ProviderSpec] = None,
) -> Dict[str, Any]:
    raw_response = None
    parsed_response = None
    cut_off_result = None  # a parsed reply that max_tokens cut off, kept in case the retries fail
    if provider is None:
        provider, model_name = resolve_model(model_name)
        if provider.kind != "anthropic":
            provider = get_provider("anthropic")
    client = get_client(provider)

    cfg = dict(generation_configs) if generation_configs else {}
    for p in provider.drop_params:
        cfg.pop(p, None)
    if system_prompt is None and "system_instruction" in cfg:
        system_prompt = cfg.pop("system_instruction")

    new_model = is_new_claude_model(model_name)
    if "max_tokens" in cfg:
        max_tokens = int(cfg.pop("max_tokens"))
    elif new_model:
        # Thinking and the answer share max_tokens: give them the model's full output cap.
        max_tokens = _new_claude_max_tokens(_model_output_cap(client, provider.name, model_name))
    else:
        max_tokens = 16384  # the original default for older models
    temperature = cfg.pop("temperature", None)
    if new_model:
        temperature = None  # rejected by these models
    effort = (os.getenv("PO_CLAUDE_EFFORT", "") or "").strip().lower()
    if effort not in _EFFORT_LEVELS:
        effort = None  # the model's default
    temperature_in_body = False  # SDK 1.x: no temperature argument, but its API version takes one
    streams_without_stop = 0
    empty_streams = 0
    use_stream = new_model
    effort_ok = True  # False once the endpoint rejected output_config (strict gateways)
    # True while the effort sent is the one PO_CLAUDE_EFFORT asked for; a
    # step-down after max_tokens is not dropped on a 400 (see below).
    effort_from_env = effort is not None
    refusals = 0

    for attempt in range(max_retries):
        try:
            kwargs: Dict[str, Any] = {
                "model": model_name,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": content}],
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            extra_body: Dict[str, Any] = {}
            if temperature is not None:
                if temperature_in_body:
                    extra_body["temperature"] = temperature
                else:
                    kwargs["temperature"] = temperature
            if new_model and effort:
                # extra_body: older SDKs have no output_config argument.
                extra_body["output_config"] = {"effort": effort}
            if extra_body:
                kwargs["extra_body"] = extra_body

            try:
                try:
                    response = _create_message(client, kwargs, stream=use_stream)
                except TypeError as te:
                    if "temperature" not in kwargs or "temperature" not in str(te):
                        raise
                    t = kwargs.pop("temperature")
                    api_version = _sdk_api_version(client)
                    if api_version and api_version < "2026-10-21":
                        # anthropic 1.x dropped the argument, but its API version
                        # (2023-06-01) still takes temperature for older models.
                        temperature_in_body = True
                        kwargs["extra_body"] = {**(kwargs.get("extra_body") or {}), "temperature": t}
                        _note_once(f" >> [{provider.name}] this anthropic SDK has no temperature argument; "
                                   f"sending temperature in the request body (API {api_version}).")
                    else:
                        # SDK releases for API version 2026-10-21 take no sampling
                        # arguments at all: send the request without it.
                        temperature = None
                        _note_once(f" >> [{provider.name}] this anthropic SDK takes no temperature "
                                   f"(API {api_version or 'unknown'}); sending requests without it.")
                    response = _create_message(client, kwargs, stream=use_stream)
            except Exception:
                usage_tracker.record_error(provider.name, model_name)
                raise
            usage_tracker.record_response(provider.name, model_name, response)
            stop_reason = getattr(response, "stop_reason", None)
            raw_response = _extract_claude_text(response)
            stream_key = (provider.name, model_name)
            if use_stream and stop_reason is None and stream_key not in _streams_without_stop_reason:
                # A complete stream always ends with a stop_reason; without one
                # a proxy or the network cut the reply short (often mid-LaTeX).
                streams_without_stop += 1
                if streams_without_stop >= 2 or attempt + 1 >= max_retries:
                    # Twice in a row: this endpoint never sends one. Accept it.
                    _streams_without_stop_reason.add(stream_key)
                    print(f" >> [{provider.name}] '{model_name}': the stream gave no stop_reason again; "
                          f"accepting replies from this endpoint as they are.")
                else:
                    raise IncompleteStream("the stream ended before the reply was complete (no stop_reason)")
            if stop_reason == "max_tokens":
                # Adaptive thinking counts against max_tokens: a model that
                # thinks until the cap leaves no (or a cut-off) answer. Asking
                # again with the same settings tends to end the same way, so the
                # next attempt thinks one effort level less.
                lower = _lower_effort(effort, model_name) if new_model and effort_ok else None
                if lower and attempt + 1 < max_retries:
                    if raw_response and cut_off_result is None:
                        try:
                            cut_parsed = result_parsing_func(raw_response)
                            if cut_parsed is not None or not check_parsed_response_not_none:
                                cut_off_result = {"raw_response": raw_response, "parsed_response": cut_parsed}
                        except Exception:
                            pass
                    what = "the answer was cut off" if raw_response else "no answer text was left"
                    print(
                        f" >> [{provider.name}] '{model_name}' used all max_tokens={max_tokens} and {what}; "
                        f"asking again with effort={lower}."
                    )
                    effort = lower
                    effort_from_env = False
                    continue
                hint = "."
                if new_model:
                    at_cap = max_tokens >= _model_output_cap(client, provider.name, model_name)
                    hint = (" (this is the model's maximum; PO_CLAUDE_EFFORT=medium makes it think less)."
                            if at_cap else " (raise PO_CLAUDE_MAX_TOKENS if this repeats).")
                print(f" >> [{provider.name}] '{model_name}' hit max_tokens={max_tokens}; the reply may be cut off{hint}")
            elif stop_reason == "refusal":
                # A refusal can be a one-off false positive (a paper review was
                # once refused and the whole revision round was lost), so ask
                # once more; a second refusal is final.
                refusals += 1
                if refusals >= 2 or attempt + 1 >= max_retries:
                    raise ClaudeRefusal(f"{model_name} declined this request (stop_reason=refusal)")
                raise RuntimeError(f"{model_name} declined this request (stop_reason=refusal); asking once more")
            if not raw_response:
                raise ValueError("Empty response text from Claude API")

            parsed_response = result_parsing_func(raw_response)

            if check_parsed_response_not_none:
                assert (
                    parsed_response is not None
                ), f"Parsed response is None, Here is the raw response:\n{raw_response}"

            return {
                "raw_response": raw_response,
                "parsed_response": parsed_response,
            }

        except ClaudeRefusal:
            raise
        except Exception as e:
            err_msg = str(e)
            print(
                f"Warning: {provider.name} (Anthropic API) call (model={model_name}) failed on attempt {attempt + 1} of {max_retries}. Error: {redact_secrets(err_msg)}"
            )

            if isinstance(e, EmptyStream) and use_stream:
                empty_streams += 1
                if empty_streams >= 2:
                    # Twice no stream events: an endpoint that ignores stream=true.
                    # Ask without streaming, within the SDK's non-streaming budget.
                    use_stream = False
                    max_tokens = min(max_tokens, 16384)
                    print(f" >> [{provider.name}] '{model_name}': no streamed reply; "
                          f"retrying without streaming (max_tokens={max_tokens}).")
                    continue

            # Only API errors are inspected: other exceptions (e.g. the parse
            # assertion above) carry the model's own text.
            status = getattr(e, "status_code", None)
            api_msg = err_msg.lower() if status is not None else ""

            # A gateway with a strict request schema that refuses output_config:
            # ask again without it (the model's default effort). Only for the
            # configured effort: after a max_tokens step-down the default effort
            # is the one that just ran out, so the 4xx handling below applies as
            # before (the cut-off reply, else the caller's fallback).
            if (status == 400 and effort_from_env and attempt + 1 < max_retries
                    and "output_config" in (kwargs.get("extra_body") or {})
                    and any(p in api_msg for p in ("output_config", "effort"))):
                print(f" >> [{provider.name}] '{model_name}': the endpoint does not take an effort setting; "
                      f"retrying without it.")
                effort_ok, effort = False, None
                continue

            # A model that rejects sampling parameters: retry without them.
            if temperature is not None and status == 400 and any(p in api_msg for p in _SAMPLING_PARAMS):
                print(f" >> [{provider.name}] '{model_name}' rejects sampling parameters; retrying without temperature.")
                temperature = None
                continue

            # Retry immediately with the cap the API names (else 8192) if a model has a lower output cap.
            if "max_tokens" in api_msg and max_tokens > 8192:
                allowed = _allowed_max_tokens(api_msg)
                if allowed and 1024 <= allowed < max_tokens:
                    max_tokens = allowed
                else:  # the original 16384, then 8192
                    max_tokens = 16384 if max_tokens > 16384 else 8192
                continue

            if status in (401, 403) or any(
                code in api_msg
                for code in ["authentication_error", "permission_error", "invalid x-api-key"]
            ):
                raise e

            # Images refused: retry as text-only (remembering clearly text-only models).
            n_images = sum(1 for b in content if isinstance(b, dict) and b.get("type") == "image")
            scope = vision_error_scope(e) if n_images else ""
            if scope:
                if scope == "model":
                    print(f" >> [{provider.name}] '{model_name}' seems text-only; retrying without images.")
                    mark_text_only(provider, model_name)
                content = [b for b in content if not (isinstance(b, dict) and b.get("type") == "image")]
                content.append({
                    "type": "text",
                    "text": f"[Note: {n_images} image attachment(s) omitted because the selected model is text-only.]",
                })
                continue

            # Other 4xx (invalid request, unsupported document block, unknown
            # model...) will fail the same way again: let the caller fall back.
            if status in (400, 404, 413, 415, 422):
                if cut_off_result is not None:
                    return cut_off_result
                raise e

            if attempt + 1 == max_retries:
                if cut_off_result is not None:
                    print(" >> Returning the earlier reply that max_tokens cut off.")
                    return cut_off_result
                print(f"Error: All {max_retries} retry attempts failed.")
                raise e

            delay = base_interval_sec * (2 ** (attempt - 1)) if attempt > 0 else 0
            print(f"Retrying in {delay} seconds...")
            if delay > 0:
                time.sleep(delay)

    return {
        "raw_response": raw_response,
        "parsed_response": parsed_response,
    }


def call_claude_with_text_prompt(
    prompt: str,
    model_name: str,
    result_parsing_func: Callable = parse_gemini_json_results,
    generation_configs: Optional[Dict[str, Any]] = None,
    check_parsed_response_not_none: bool = True,
    max_retries: int = 5,
    base_interval_sec: int = 5,
    system_prompt: Optional[str] = None,
    provider: Optional[ProviderSpec] = None,
) -> Dict[str, Any]:
    return call_claude_with_content(
        content=[{"type": "text", "text": prompt}],
        model_name=model_name,
        result_parsing_func=result_parsing_func,
        generation_configs=generation_configs,
        check_parsed_response_not_none=check_parsed_response_not_none,
        max_retries=max_retries,
        base_interval_sec=base_interval_sec,
        system_prompt=system_prompt,
        provider=provider,
    )


def call_claude_with_images(
    prompt: str,
    images: List[str],
    model_name: str,
    result_parsing_func: Callable = parse_gemini_json_results,
    generation_configs: Optional[Dict[str, Any]] = None,
    check_parsed_response_not_none: bool = True,
    max_retries: int = 5,
    base_interval_sec: int = 5,
    system_prompt: Optional[str] = None,
    provider: Optional[ProviderSpec] = None,
) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    max_edge = CLAUDE_MANY_IMAGES_MAX_EDGE if len(images) > CLAUDE_MANY_IMAGES else None
    for image_path in images:
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        content.append(make_claude_image_block(img_bytes, image_path, max_edge=max_edge,
                                               model_name=model_name))

    return call_claude_with_content(
        content=content,
        model_name=model_name,
        result_parsing_func=result_parsing_func,
        generation_configs=generation_configs,
        check_parsed_response_not_none=check_parsed_response_not_none,
        max_retries=max_retries,
        base_interval_sec=base_interval_sec,
        system_prompt=system_prompt,
        provider=provider,
    )


def call_claude_with_pdf(
    pdf_path: str,
    prompt: str,
    model_name: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    check_parsed_response_not_none: bool = True,
    result_parsing_func: Callable = parse_gemini_json_results,
    provider: Optional[ProviderSpec] = None,
) -> Dict[str, Any]:
    with open(pdf_path, "rb") as f:
        pdf_b64 = base64.b64encode(f.read()).decode("utf-8")

    content = [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": pdf_b64,
            },
        },
        {"type": "text", "text": prompt},
    ]
    return call_claude_with_content(
        content=content,
        model_name=model_name,
        result_parsing_func=result_parsing_func,
        generation_configs={"temperature": temperature},
        check_parsed_response_not_none=check_parsed_response_not_none,
        system_prompt=system_prompt,
        provider=provider,
    )
