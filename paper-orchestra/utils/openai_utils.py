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

from typing import Any, Optional
import re
import json
import time
import os
import sys
import cv2  # type: ignore
import base64

from openai import OpenAI  # type: ignore
from dotenv import load_dotenv  # type: ignore

dot_file = os.path.join(os.path.dirname(__file__), "../.env")
load_dotenv(dot_file)

from utils import usage_tracker  # noqa: E402
from utils.image_fit import (  # noqa: E402
    ImageLimits,
    data_url_image_sizes,
    fit_data_url_images,
    learned_limits,
    remember_limits,
    request_image_budget,
    shrink_more,
    size_error_limits,
)
from utils.providers import (  # noqa: E402
    ProviderSpec,
    get_client,
    get_provider,
    mark_text_only,
    model_supports_vision,
    redact_secrets,
    resolve_model,
    vision_error_scope,
)


def is_deepseek_model(model_name: str) -> bool:
    return "deepseek" in (model_name or "").lower()


def get_openai_client() -> OpenAI:
    """Shared client for the official/OPENAI_BASE_URL OpenAI endpoint (kept for backward compatibility)."""
    return get_client(get_provider("openai"))


def get_deepseek_client() -> OpenAI:
    """Shared DeepSeek client (kept for backward compatibility)."""
    return get_client(get_provider("deepseek"))


def _strip_images(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    texts = [
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    n_images = sum(
        1 for part in content if isinstance(part, dict) and part.get("type") == "image_url"
    )
    merged = "\n\n".join(t for t in texts if t)
    if n_images:
        merged += f"\n\n[Note: {n_images} image attachment(s) omitted because the selected model is text-only.]"
    return merged


_REASONING_NO_SAMPLING = re.compile(r"(^o\d|reasoner|-r1\b|^r1|thinking)", re.IGNORECASE)


def parse_openai_json_results(response: str):
    if not response or not isinstance(response, str):
        return None

    json_pattern = r"```json(.*?)```"
    matches = re.findall(json_pattern, response, re.DOTALL)

    if not matches:
        json_pattern = r"(\{.*\}|\[.*\])"
        matches = re.findall(json_pattern, response, re.DOTALL)

    for json_string in matches:
        json_string = json_string.strip()
        try:
            parsed_json = json.loads(json_string)
            return parsed_json
        except json.JSONDecodeError:
            try:
                json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
                json_string_clean = re.sub(r",\s*([\]}])", r"\1", json_string_clean)
                json_string_clean = re.sub(
                    r'\\(?![\\"/bfnrtu])', r"\\\\", json_string_clean
                )

                parsed_json = json.loads(json_string_clean)
                return parsed_json
            except json.JSONDecodeError:
                continue

    try:
        start_idx = response.find("{")
        end_idx = response.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_string = response[start_idx : end_idx + 1]
            json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
            json_string_clean = re.sub(r",\s*([\]}])", r"\1", json_string_clean)
            json_string_clean = re.sub(
                r'\\(?![\\"/bfnrtu])', r"\\\\", json_string_clean
            )
            return json.loads(json_string_clean)
    except Exception:
        pass

    try:
        start_idx = response.find("[")
        end_idx = response.rfind("]")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_string = response[start_idx : end_idx + 1]
            json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
            json_string_clean = re.sub(r",\s*([\]}])", r"\1", json_string_clean)
            json_string_clean = re.sub(
                r'\\(?![\\"/bfnrtu])', r"\\\\", json_string_clean
            )
            return json.loads(json_string_clean)
    except Exception:
        pass

    return None  # No valid JSON found


def call_openai_models_with_content(
    content: list[Any],
    model_name: str,
    max_retries: int = 5,
    base_interval_sec: int = 5,
    result_parsing_func: callable = parse_openai_json_results,
    generation_configs: dict = {},
    check_parsed_response_not_none: bool = True,
    system_prompt: str = None,
    provider: ProviderSpec = None,
):
    raw_response = None
    parsed_response = None
    if provider is None:
        provider, model_name = resolve_model(model_name)
    openai_client = get_client(provider)

    configs = dict(generation_configs) if generation_configs else {}
    for p in provider.drop_params:
        configs.pop(p, None)
    if _REASONING_NO_SAMPLING.search(model_name or ""):
        configs.pop("temperature", None)
        configs.pop("top_p", None)

    if model_supports_vision(provider, model_name):
        normalized_content: Any = content
    else:
        # Text-only endpoint (e.g. DeepSeek official API): plain string content.
        normalized_content = _strip_images(content)

    provider_label = provider.name
    normalized_content = fit_images_for_provider(normalized_content, provider, model_name)
    size_retries = 0
    pending_limits = None  # image limits of a resend after a size error, kept once it goes through
    attempt = 0
    while attempt < max_retries:
        try:
            messages = []
            if system_prompt:
                role = (
                    "developer"
                    if (provider.name == "openai" and model_name.startswith(("o1", "o3", "o4")))
                    else "system"
                )
                messages.append({"role": role, "content": system_prompt})

            messages.append({"role": "user", "content": normalized_content})

            try:
                response = openai_client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    **configs,
                )
            except Exception:
                usage_tracker.record_error(provider_label, model_name)
                raise
            usage_tracker.record_response(provider_label, model_name, response)
            if pending_limits is not None:
                remember_limits(provider.name, model_name, pending_limits)
                pending_limits = None
            raw_response = response.choices[0].message.content
            parsed_response = result_parsing_func(raw_response)

            if check_parsed_response_not_none:
                assert (
                    parsed_response is not None
                ), f"Parsed response is None, Here is the raw response:\n{raw_response}"

            return {
                "raw_response": raw_response,
                "parsed_response": parsed_response,
            }

        except Exception as e:
            print(
                f"Warning: {provider_label} API call (model={model_name}) failed on attempt {attempt + 1} of {max_retries}. Error: {redact_secrets(e)}",
            )

            # Only API errors are inspected below: any other exception (e.g. the
            # parse assertion above) carries the model's own text, not the API's.
            status = getattr(e, "status_code", None)
            err_lower = str(e).lower() if status is not None else ""
            if status in (401, 403) or any(
                k in err_lower for k in ["authentication", "invalid_api_key", "incorrect api key"]
            ):
                raise e

            # Some endpoints reject sampling params for reasoning models.
            if any(k in configs for k in ("temperature", "top_p")) and (
                "temperature" in err_lower or "top_p" in err_lower
            ):
                configs.pop("temperature", None)
                configs.pop("top_p", None)
                continue

            # The request was refused because of its images: retry immediately as
            # text-only, and remember the model only when it plainly takes no images.
            has_images = isinstance(normalized_content, list) and any(
                isinstance(p, dict) and p.get("type") == "image_url" for p in normalized_content
            )
            # Too large for the endpoint: scale the images down and resend (at
            # most twice) before giving up on them.
            if has_images and size_retries < 2:
                shrunk = _shrink_after_size_error(e, normalized_content, provider, model_name)
                if shrunk is None and _upload_stalled(e):
                    shrunk = _shrink_after_stalled_upload(normalized_content, provider, model_name)
                if shrunk is not None:
                    size_retries += 1
                    normalized_content, pending_limits = shrunk
                    continue
            scope = vision_error_scope(e) if has_images else ""
            if scope:
                if scope == "model":
                    print(f" >> [{provider_label}] '{model_name}' seems text-only; retrying without images.")
                    mark_text_only(provider, model_name)
                else:
                    print(f" >> [{provider_label}] request with images was rejected; retrying it without images.")
                normalized_content = _strip_images(normalized_content)
                pending_limits = None  # the images went, not a smaller copy of them
                continue

            if attempt + 1 == max_retries:
                print(f"Error: All {max_retries} retry attempts failed.")
                raise e

            delay = base_interval_sec * (2 ** (attempt - 1)) if attempt > 0 else 0
            print(f"Retrying in {delay} seconds...")
            if delay > 0:
                time.sleep(delay)
            attempt += 1

    print("Error: Exited retry loop unexpectedly.")
    return {
        "raw_response": raw_response,
        "parsed_response": parsed_response,
    }


def _known_limits(provider: ProviderSpec, model_name: str) -> ImageLimits:
    limits = provider.image_limits()
    learned = learned_limits(provider.name, model_name)
    return limits.tighter(learned) if learned else limits


def fit_images_for_provider(content: Any, provider: ProviderSpec, model_name: str,
                            limits: Optional[ImageLimits] = None) -> Any:
    """Scales base64 images in `content` down to the platform's known limits
    (ProviderSpec.image_*, plus any that worked after an earlier size error),
    or to `limits` when given."""
    return fit_data_url_images(content, limits or _known_limits(provider, model_name), label=provider.name,
                               total_pixels=provider.image_max_total_pixels,
                               total_bytes=request_image_budget(provider.image_max_total_bytes))


def _shrink_after_size_error(err: Exception, content: Any, provider: ProviderSpec, model_name: str):
    """(smaller copy of `content`, its limits) when `err` says its images are
    too large, else None. The caller remembers the limits for the model only
    once the smaller resend goes through, so a misread error never keeps
    shrinking later images."""
    sizes = data_url_image_sizes(content)
    found = size_error_limits(err, sizes)
    if found is None:
        return None
    limits = _known_limits(provider, model_name).tighter(found[0])
    smaller = fit_images_for_provider(content, provider, model_name, limits)
    if data_url_image_sizes(smaller) == sizes:
        # Already within what the error names (e.g. the limit counts all
        # images together): aim at half the pixels instead.
        limits = shrink_more(limits, sizes)
        smaller = fit_images_for_provider(content, provider, model_name, limits)
        if data_url_image_sizes(smaller) == sizes:
            return None
    print(f" >> [{provider.name}] images too large for '{model_name}'; "
          f"scaled down to {limits.describe()} and resending.")
    return smaller, limits


def _upload_stalled(err: BaseException) -> bool:
    """True when the request failed because sending it stopped making
    progress (httpx.WriteTimeout somewhere in the exception chain)."""
    seen = 0
    while err is not None and seen < 6:
        if type(err).__name__ == "WriteTimeout":
            return True
        err = err.__cause__ or err.__context__
        seen += 1
    return False


def _shrink_after_stalled_upload(content: Any, provider: ProviderSpec, model_name: str):
    """(content with its images at about half the pixels, their limits), or
    None when they cannot get smaller."""
    sizes = data_url_image_sizes(content)
    if not sizes:
        return None
    limits = shrink_more(_known_limits(provider, model_name), sizes)
    smaller = fit_images_for_provider(content, provider, model_name, limits)
    if data_url_image_sizes(smaller) == sizes:
        return None
    print(f" >> [{provider.name}] uploading the images stalled; resending them smaller ({limits.describe()}).")
    return smaller, limits


def call_openai_models_with_text_prompt(
    prompt: str,
    model_name: str,
    result_parsing_func: callable = parse_openai_json_results,
    generation_configs: dict = {},
    check_parsed_response_not_none: bool = True,
    max_retries: int = 5,
    base_interval_sec: int = 5,
    system_prompt: str = None,
    provider: ProviderSpec = None,
):
    return call_openai_models_with_content(
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


def get_openai_image_part_from_path(image_path: str):
    with open(image_path, "rb") as image_file:
        raw_bytes = image_file.read()
    base64_image = base64.b64encode(raw_bytes).decode("utf-8")
    mime_type = "image/png" if raw_bytes.startswith(b"\x89PNG") else "image/jpeg"

    image_content = [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{base64_image}",
            },
        }
    ]
    return image_content
