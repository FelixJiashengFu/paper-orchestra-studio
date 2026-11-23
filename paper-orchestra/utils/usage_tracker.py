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
In-process LLM token accounting for one paper run.

The OpenAI-, Anthropic- and Gemini-protocol call paths report every request
here; write_single_paper() writes the totals to <run dir>/usage_report.json:

    {"generated_at": ISO8601,
     "totals":   {"calls", "errors", "input_tokens", "output_tokens"},
     "by_model": [{"provider", "model", "calls", "errors", "input_tokens", "output_tokens",
                   optional finer counters (DETAIL_KEYS), optional "cost"}],
     "cost":     optional list-price estimate in CAD, see utils/pricing.price_report}

"calls" counts every request sent, failed ones included; "errors" counts the
requests that raised (HTTP error, timeout, refused connection...). A response
without usage data counts as a call with 0 tokens.

Nothing in this module may break a run: every public function swallows its
own errors. While a run is bound (begin_run), the report file is refreshed
after each request, so even a run that is killed leaves a recent report.

Each bound run folder has its own counters, so a second run started in the
same process (e.g. two Streamlit sessions) neither resets nor takes over the
first one's report. Calls cannot be told apart by run, though: while two runs
overlap, every call is counted in both.
"""

import os
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

from utils.report_io import atomic_write_json, now_iso

REPORT_FILENAME = "usage_report.json"

_lock = threading.Lock()  # guards _stats, _runs, _ended
_write_lock = threading.Lock()  # serialises report writes (taken before _lock)
_Stats = Dict[Tuple[str, str], Dict[str, int]]
_stats: _Stats = {}  # everything since the last reset()
_runs: Dict[str, _Stats] = {}  # bound run folder (absolute) -> its own counters, in begin order
_ended: Dict[str, _Stats] = {}  # counters of recently ended runs, for report(run_dir)
_MAX_ENDED = 32
_run_dir: Optional[str] = None  # the most recently bound run folder
_listeners: List[Any] = []
_warned = False


def add_listener(fn: Any) -> None:
    """Register a live usage snapshot callback invoked on every record(). Never raises."""
    try:
        with _lock:
            if fn not in _listeners:
                _listeners.append(fn)
    except Exception:
        pass


def remove_listener(fn: Any) -> None:
    """Unregister a previously registered usage listener. Never raises."""
    try:
        with _lock:
            if fn in _listeners:
                _listeners.remove(fn)
    except Exception:
        pass


def _warn(what: str, err: Exception) -> None:
    global _warned
    if _warned:
        return
    _warned = True
    try:
        print(f"Warning: token usage tracking: {what} failed ({type(err).__name__}: {err}); continuing.",
              file=sys.stderr)
    except Exception:
        pass


def _int(value: Any) -> int:
    try:
        n = int(value or 0)
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return 0


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def extract_tokens(response: Any) -> Tuple[int, int]:
    """(input_tokens, output_tokens) from an OpenAI, Anthropic or Gemini response.

    Unknown shapes and missing usage give (0, 0).
    """
    # Gemini: usage_metadata. Thinking tokens and the Google Search tool's
    # prompt tokens are billed too, so they are included.
    meta = _field(response, "usage_metadata")
    if meta is not None:
        inp = _int(_field(meta, "prompt_token_count")) + _int(_field(meta, "tool_use_prompt_token_count"))
        out = _int(_field(meta, "candidates_token_count")) + _int(_field(meta, "thoughts_token_count"))
        return inp, out

    usage = _field(response, "usage")
    if usage is None:
        return 0, 0
    # OpenAI chat completions (completion_tokens already include reasoning tokens).
    if _field(usage, "prompt_tokens") is not None or _field(usage, "completion_tokens") is not None:
        return _int(_field(usage, "prompt_tokens")), _int(_field(usage, "completion_tokens"))
    # Anthropic messages (input_tokens excludes cached prompt tokens) and the
    # OpenAI images endpoint (input_tokens / output_tokens).
    inp = (
        _int(_field(usage, "input_tokens"))
        + _int(_field(usage, "cache_creation_input_tokens"))
        + _int(_field(usage, "cache_read_input_tokens"))
    )
    return inp, _int(_field(usage, "output_tokens"))


# Finer counters for pricing (utils/pricing.py), all subsets of the totals:
#   cache_read_tokens / cache_write_tokens / cache_write_1h_tokens: input tokens
#     read from / written to a prompt cache (billed at their own rates);
#   image_output_tokens: output tokens that are generated images (Gemini);
#   long_calls / long_input_tokens / long_output_tokens: calls whose prompt was
#     over the model's long-context threshold (a higher price tier);
#   search_queries: web searches run by a search tool (Google Search grounding);
#   images: images returned by an /images endpoint.
# They appear in a by_model row only when not 0.
DETAIL_KEYS = ("cache_read_tokens", "cache_write_tokens", "cache_write_1h_tokens", "image_output_tokens",
               "long_calls", "long_input_tokens", "long_output_tokens", "search_queries", "images")


def extract_details(response: Any) -> Dict[str, int]:
    """The finer counters (DETAIL_KEYS) of one response; unknown shapes give {}."""
    out: Dict[str, int] = {}
    meta = _field(response, "usage_metadata")
    if meta is not None:  # Gemini
        out["cache_read_tokens"] = _int(_field(meta, "cached_content_token_count"))
        for d in _field(meta, "candidates_tokens_details") or []:
            modality = str(_field(d, "modality") or "").upper()
            if modality.endswith("IMAGE"):
                out["image_output_tokens"] = out.get("image_output_tokens", 0) + _int(_field(d, "token_count"))
        queries = 0
        for cand in _field(response, "candidates") or []:
            gm = _field(cand, "grounding_metadata")
            queries += len(_field(gm, "web_search_queries") or []) if gm is not None else 0
        out["search_queries"] = queries
    else:
        usage = _field(response, "usage")
        if usage is not None:
            # OpenAI: prompt_tokens_details.cached_tokens; DeepSeek: prompt_cache_hit_tokens
            details = _field(usage, "prompt_tokens_details")
            cached = _int(_field(details, "cached_tokens")) if details is not None else 0
            out["cache_read_tokens"] = cached or _int(_field(usage, "prompt_cache_hit_tokens")) \
                or _int(_field(usage, "cache_read_input_tokens"))  # Anthropic
            write = _int(_field(usage, "cache_creation_input_tokens"))
            out["cache_write_tokens"] = write
            cc = _field(usage, "cache_creation")
            if cc is not None:
                out["cache_write_1h_tokens"] = min(write, _int(_field(cc, "ephemeral_1h_input_tokens")))
        data = _field(response, "data")  # /images endpoint: one item per image
        if isinstance(data, list):
            out["images"] = sum(1 for d in data
                                if _field(d, "b64_json") is not None or _field(d, "url") is not None)
            if usage is not None:
                # Its output tokens are the generated images (gpt-image-1:
                # output_tokens_details.image_tokens, else all of them).
                total_out = _int(_field(usage, "output_tokens"))
                od = _field(usage, "output_tokens_details")
                image_tokens = _int(_field(od, "image_tokens")) if od is not None else 0
                out["image_output_tokens"] = min(total_out, image_tokens) if image_tokens else total_out
    return {k: v for k, v in out.items() if v}


def record(provider: Optional[str], model: Optional[str], input_tokens: int = 0,
           output_tokens: int = 0, error: bool = False, details: Optional[Dict[str, int]] = None) -> None:
    """Counts one request. Never raises."""
    try:
        key = (str(provider or "unknown"), str(model or "unknown"))
        inp, out = _int(input_tokens), _int(output_tokens)
        extra = {k: _int(v) for k, v in (details or {}).items() if k in DETAIL_KEYS and _int(v)}
        call = {"input_tokens": inp, "output_tokens": out, **extra}
        billable = bool(inp or out or extra.get("images") or extra.get("search_queries"))
        priced = None
        if billable:
            found = _price_lookup(key[0], key[1])
            threshold = found[0].long_threshold if found else None
            if threshold and inp > threshold:
                extra["long_calls"] = 1
                extra["long_input_tokens"] = inp
                extra["long_output_tokens"] = out
                call.update(extra)
            priced = _price_call(found, call)
        with _lock:
            for stats in [_stats, *_runs.values()]:
                st = stats.get(key)
                if st is None:
                    st = stats[key] = {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0}
                st["calls"] += 1
                if error:
                    st["errors"] += 1
                st["input_tokens"] += inp
                st["output_tokens"] += out
                for k, v in extra.items():
                    st[k] = st.get(k, 0) + v
                if billable:
                    _add_cost(st, priced)
        _flush()
    except Exception as e:  # pragma: no cover - defensive
        _warn("record", e)


def _price_lookup(provider: str, model: str):
    """utils.pricing.lookup() at the time of the call, or None."""
    try:
        from utils import pricing

        return pricing.lookup(provider, model)
    except Exception:
        return None


def _price_call(found, call: Dict[str, int]) -> Optional[Dict[str, Any]]:
    try:
        from utils import pricing

        return pricing.price_call(found, call)
    except Exception:
        return None


def _add_cost(st: Dict[str, Any], priced: Optional[Dict[str, Any]]) -> None:
    """Adds one call's cost to the row's internal "_cost" (kept out of the
    report rows). A row with any call that could not be priced, or priced in
    another currency, is priced from its counters at report time instead."""
    acc = st.get("_cost")
    if acc is False:
        return
    if not priced or (acc and acc["currency"] != priced["currency"]):
        st["_cost"] = False
        return
    if not acc:
        st["_cost"] = dict(priced)
        return
    acc["amount"] += priced["amount"]
    acc["source"], acc["price_as_of"] = priced["source"], priced["price_as_of"]


def record_response(provider: Optional[str], model: Optional[str], response: Any) -> None:
    """Counts one successful request and its token usage. Never raises."""
    try:
        inp, out = extract_tokens(response)
    except Exception:
        inp, out = 0, 0
    try:
        details = extract_details(response)
    except Exception:
        details = {}
    record(provider, model, inp, out, details=details)


def record_error(provider: Optional[str], model: Optional[str]) -> None:
    """Counts one request that raised. Never raises."""
    record(provider, model, error=True)


def _key(run_dir: str) -> str:
    return os.path.abspath(run_dir)


def report(run_dir: Optional[str] = None) -> Dict[str, Any]:
    """The usage_report.json payload: for `run_dir` (a bound or recently ended run)
    when given, else for everything counted since the last reset."""
    with _lock:
        stats = _stats
        if run_dir:
            key = _key(run_dir)
            stats = _runs.get(key, _ended.get(key, {}))
        items = sorted(stats.items(), key=lambda kv: (kv[0][0], kv[0][1]))
        by_model: List[Dict[str, Any]] = [
            {"provider": p, "model": m, **{k: v for k, v in st.items() if not k.startswith("_")}}
            for (p, m), st in items
        ]
        priced = [dict(st["_cost"]) if st.get("_cost") else None for _, st in items]
    totals = {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0}
    for row in by_model:
        for k in totals:
            totals[k] += row[k]
    out: Dict[str, Any] = {"generated_at": now_iso(), "totals": totals, "by_model": by_model}
    cost = _cost(by_model, priced)
    if cost is not None:
        out["cost"] = cost
    return out


def _cost(by_model: List[Dict[str, Any]], priced: List[Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """The list-price estimate in CAD (utils/pricing.py); adds "cost" to the
    priced rows. `priced`: each row's calls priced one by one (or None).
    None (and rows untouched) if pricing fails."""
    if not by_model:
        return None
    try:
        from utils import pricing

        rows = [dict(r, _priced=p) if p else dict(r) for r, p in zip(by_model, priced)]
        summary = pricing.price_report(rows)
        for row, priced in zip(by_model, rows):
            if "cost" in priced:
                row["cost"] = priced["cost"]
        return summary
    except Exception as e:
        _warn("estimating the cost", e)
        return None


def reset() -> None:
    try:
        with _lock:
            _stats.clear()
    except Exception as e:  # pragma: no cover - defensive
        _warn("reset", e)


def write_report(run_dir: str) -> Optional[str]:
    """Writes <run_dir>/usage_report.json; returns its path, or None. Never raises.

    The counters are the run's own when it is bound (or recently ended), else
    everything since the last reset.
    """
    try:
        if not run_dir or not os.path.isdir(run_dir):
            return None
        path = os.path.join(run_dir, REPORT_FILENAME)
        with _lock:
            known = _key(run_dir) in _runs or _key(run_dir) in _ended
        with _write_lock:
            atomic_write_json(path, report(run_dir if known else None))
        return path
    except Exception as e:
        _warn("writing usage_report.json", e)
        return None


def _flush() -> None:
    with _lock:
        bound = list(_runs)
        listeners = list(_listeners)
    for run_dir in bound:
        write_report(run_dir)
    if listeners:
        try:
            snap = report(bound[-1] if bound else None)
        except Exception:
            snap = None
        if snap is not None:
            for fn in listeners:
                try:
                    fn(snap)
                except Exception:
                    pass


def begin_run(run_dir: Optional[str]) -> None:
    """Resets the counters and binds a run directory to its own (report refreshed live).

    Other runs bound in this process keep their counters.
    """
    global _run_dir
    try:
        reset()
        if not run_dir:
            return
        key = _key(run_dir)
        with _lock:
            _runs.pop(key, None)
            _runs[key] = {}
            _ended.pop(key, None)
        _run_dir = key
        write_report(key)  # replaces any stale report from an earlier run in the same folder
        try:  # today's prices and exchange rates, at most once a day, off the call path
            from utils import pricing

            pricing.refresh_in_background()
        except Exception:
            pass
    except Exception as e:
        _warn("begin_run", e)


def end_run(run_dir: Optional[str] = None) -> Optional[str]:
    """Writes the final report and unbinds the run directory (all bound runs when
    none is given); returns the report path. Never raises."""
    global _run_dir
    try:
        if run_dir:
            targets = [_key(run_dir)]
        else:
            with _lock:
                targets = list(_runs)
            if _run_dir in targets:  # the most recent one last: its path is returned
                targets.remove(_run_dir)
                targets.append(_run_dir)
        path = None
        for target in targets:
            path = write_report(target)
            with _lock:
                stats = _runs.pop(target, None)
                if stats is not None:
                    _ended[target] = stats
                    while len(_ended) > _MAX_ENDED:
                        _ended.pop(next(iter(_ended)))
            if _run_dir == target:
                _run_dir = None
        return path
    except Exception as e:
        _warn("end_run", e)
        return None
