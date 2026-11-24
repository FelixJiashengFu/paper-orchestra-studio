"""Host-agent ("cli") providers: the LLM is an agent CLI the user is already signed in to.

The first one is Meta's Muse Code (`muse exec`), so every generation step runs
on the user's own Muse quota and no other LLM API (and no API key) is involved.

How a call is made
    The prompt is written to a file in a fresh, empty workspace and the CLI is
    run headless on it (`muse exec --json --prompt-file ...`). The answer is
    the `run.terminal.completed` event's text (plain stdout when --json is
    off), and `run.usage` gives the real token counts. Shell, file writes, web
    tools, reminders and the user's personal skills are switched off for the
    call, the sandbox and approval gate stay on, and the workspace is empty,
    so the agent has nothing to act on: it is used as a plain text model.

Environment
    PO_HOST_AGENT          "muse" turns host mode on (see utils.providers).
    MUSE_CLI               path of the `muse` binary (default: found on PATH,
                           then ~/.local/bin/muse).
    MUSE_DEFAULT_MODEL     model id; unset = whatever Muse Code is set to use.
    PO_MUSE_REASONING_EFFORT  none|minimal|low|medium|high|xhigh|max|ultra;
                           unset = Muse Code's own setting.
    PO_MUSE_TIMEOUT        seconds for one call (default 1800).
    PO_MUSE_MAX_STEPS      --max-model-steps (default 8).
    PO_MUSE_CONCURRENCY    calls running at once (default 2).
    PO_MUSE_JSON           0 = plain stdout instead of --json events.
    PO_MUSE_LEAN           0 = keep Muse Code's tools and personal skills on
                           (each call then carries the whole skills catalog).
    PO_MUSE_EXTRA_ARGS     extra arguments for `muse exec` (shell-split).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from utils import usage_tracker

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")

# Told to the agent before every prompt: answer as a text model, touch nothing.
TEXT_ONLY_PREAMBLE = (
    "You are being called as a plain text-generation model by an automated pipeline.\n"
    "Rules for this turn:\n"
    "- Do not use any tools: no shell commands, no file reads or writes, no web access, no sub-agents.\n"
    "- Do not ask questions and do not describe what you are doing.\n"
    "- Reply with the requested output only, exactly in the format the task asks for "
    "(for JSON: the JSON alone, no code fence, no commentary).\n"
)

_semaphore: Optional[threading.BoundedSemaphore] = None
_semaphore_lock = threading.Lock()


class AgentCliError(RuntimeError):
    """The CLI could not be run or reported a failure. `fatal` = retrying cannot help."""

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, "").strip() or default)))
    except ValueError:
        return default


def _slot() -> threading.BoundedSemaphore:
    global _semaphore
    with _semaphore_lock:
        if _semaphore is None:
            _semaphore = threading.BoundedSemaphore(_int_env("PO_MUSE_CONCURRENCY", 2, 1, 16))
        return _semaphore


def find_cli(name: str = "muse") -> str:
    """Path of the agent CLI, or "" when it is not installed."""
    explicit = os.getenv(f"{name.upper()}_CLI", "").strip()
    if explicit:
        path = os.path.expanduser(explicit)
        return path if os.path.isfile(path) and os.access(path, os.X_OK) else ""
    found = shutil.which(name)
    if found:
        return found
    for cand in (os.path.join(os.path.expanduser("~"), ".local", "bin", name),
                 f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        for path in (cand, cand + ".exe", cand + ".cmd"):
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return ""


def cli_version(name: str = "muse", timeout: float = 15.0) -> str:
    """`<cli> --version` output (first line); raises AgentCliError when it cannot run."""
    path = find_cli(name)
    if not path:
        raise AgentCliError(_not_installed(name), fatal=True)
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AgentCliError(f"`{name} --version` could not run: {e}", fatal=True)
    out = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0:
        raise AgentCliError(f"`{name} --version` exited with {proc.returncode}: {out[-300:]}", fatal=True)
    return out.splitlines()[0].strip() if out else ""


def _not_installed(name: str) -> str:
    return (
        f"找不到 `{name}` 命令行（Muse Code）。请先安装并登录："
        "curl -fsSL https://dev.meta.ai/install.sh | bash，然后运行一次 `muse` 完成登录；"
        f"或用 {name.upper()}_CLI 指定它的路径。"
    )


def _flag_on(name: str, default: bool = True) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value not in ("0", "off", "false", "no")


# Everything an agent could act with is switched off: the call is text in, text
# out. Measured on Muse Code 1.4.25 with a one-word prompt: 30k input tokens
# with these flags alone, 22k with `--preset empty` added (the rest is Muse
# Code's own system prompt and skill catalog, which no flag removes).
LEAN_FLAGS = (
    "--preset", "empty", "--disable-shell", "--disable-write", "--disable-web-tools", "--disable-reminders",
    "--disable-request-user-input", "--no-foreign-personal-context",
)


def build_command(cli_path: str, prompt_file: str, workspace: str, model: str = "") -> List[str]:
    cmd = [cli_path, "exec", "--prompt-file", prompt_file, "--workspace", workspace, "--no-session-log",
           "--max-model-steps", str(_int_env("PO_MUSE_MAX_STEPS", 8, 1, 64))]
    if _flag_on("PO_MUSE_JSON"):
        cmd.append("--json")
    if _flag_on("PO_MUSE_LEAN"):
        cmd += list(LEAN_FLAGS)
    if model:
        cmd += ["--model", model]
    effort = os.getenv("PO_MUSE_REASONING_EFFORT", "").strip().lower()
    if effort in EFFORTS:
        cmd += ["--reasoning-effort", effort]
    extra = os.getenv("PO_MUSE_EXTRA_ARGS", "").strip()
    if extra:
        cmd += shlex.split(extra)
    return cmd


def _child_env() -> Dict[str, str]:
    """The CLI signs in with the user's own Muse account: no key of ours is passed on.

    META_API_KEY is removed on purpose ("An API key always takes priority over a
    browser sign-in" in Muse Code), unless PO_MUSE_ALLOW_API_KEY=1, so a stray
    key can never move a run from the subscription to pay-as-you-go billing.
    """
    env = dict(os.environ)
    if os.getenv("PO_MUSE_ALLOW_API_KEY", "").strip() not in ("1", "true", "yes"):
        env.pop("META_API_KEY", None)
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")
    return env


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)")


def parse_output(stdout: str) -> Dict[str, Any]:
    """{"text", "usage", "error"} of one headless run.

    `--json` output is one event per line (Muse Code 1.4): the final answer is
    the `run.terminal.completed` event's payload.text, streamed pieces are
    `run.output.delta`, token counts are in `run.usage`. Anything that is not
    such an event stream (PO_MUSE_JSON=0) is the answer itself.
    """
    out = _ANSI.sub("", stdout or "").replace("\r\n", "\n").strip()
    events = _events(out)
    if events is None:
        return {"text": out, "usage": {}, "error": ""}
    text, error, deltas, usage = "", "", [], {}
    for ev in events:
        kind = str(ev.get("payload_type") or "")
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        if kind == "run.output.delta":
            deltas.append(str(payload.get("text") or ""))
        elif kind == "run.usage":
            usage = _usage(payload)
        elif kind.startswith("run.terminal."):
            if kind == "run.terminal.completed":
                text = str(payload.get("text") or "")
            else:
                error = f"{kind}: {payload.get('reason') or payload.get('text') or payload.get('terminal') or ''}".strip()
    return {"text": (text or "".join(deltas)).strip(), "usage": usage, "error": error}


def _events(out: str) -> Optional[List[Dict[str, Any]]]:
    """The JSONL events, or None when stdout is not an event stream.

    Every line must be an object with a string payload_type, so a model answer
    that is itself JSON is never mistaken for one.
    """
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return None
    events = []
    for ln in lines:
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict) or not isinstance(obj.get("payload_type"), str):
            return None
        events.append(obj)
    return events


def _usage(payload: Dict[str, Any]) -> Dict[str, int]:
    inp = out = cached = 0
    for m in payload.get("models") or []:
        if isinstance(m, dict):
            inp += int(m.get("input_tokens") or 0)
            out += int(m.get("output_tokens") or 0)
            cached += int(m.get("cached_tokens") or 0)
    return {"input_tokens": inp, "output_tokens": out, "cached_tokens": cached}


def clean_output(text: str) -> str:
    return parse_output(text)["text"]


def run_once(spec_name: str, model: str, prompt: str, system_prompt: Optional[str] = None,
             timeout: Optional[float] = None, usage_out: Optional[Dict[str, int]] = None) -> str:
    """One headless call; returns the answer text or raises AgentCliError.

    `usage_out`, when given, receives the run's token counts (empty when unknown).
    """
    cli_path = find_cli(spec_name)
    if not cli_path:
        raise AgentCliError(_not_installed(spec_name), fatal=True)
    limit = float(timeout or _int_env("PO_MUSE_TIMEOUT", 1800, 30, 6 * 3600))
    parts = [TEXT_ONLY_PREAMBLE]
    if system_prompt:
        parts.append(f"<system_instructions>\n{system_prompt}\n</system_instructions>")
    parts.append(prompt)
    workspace = tempfile.mkdtemp(prefix="po-muse-")
    try:
        prompt_file = os.path.join(workspace, "prompt.txt")
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write("\n\n".join(parts))
        cmd = build_command(cli_path, prompt_file, workspace, model)
        with _slot():
            try:
                proc = subprocess.run(cmd, cwd=workspace, env=_child_env(), stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      timeout=limit)
            except subprocess.TimeoutExpired:
                raise AgentCliError(f"`{spec_name} exec` gave no answer within {int(limit)} s "
                                    "(raise PO_MUSE_TIMEOUT or lower PO_MUSE_REASONING_EFFORT).")
            except OSError as e:
                raise AgentCliError(f"`{spec_name} exec` could not start: {e}", fatal=True)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    parsed = parse_output(proc.stdout)
    answer = parsed["text"]
    if usage_out is not None:
        usage_out.update(parsed["usage"])
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 or (parsed["error"] and not answer):
        tail = " ".join(x for x in (parsed["error"], err[-600:] or answer[-600:]) if x)
        low = tail.lower()
        fatal = proc.returncode == 2 or any(
            k in low for k in ("not logged in", "sign in", "login", "unauthorized", "malformed settings"))
        hint = ""
        if any(k in low for k in ("usage limit", "rate limit", "quota", "limit reached", "/upgrade")):
            hint = "（Muse 订阅额度已用完或被限速：等额度恢复后重试，或在 Muse 里 /upgrade。）"
        elif fatal and proc.returncode != 2:
            hint = "（请在终端运行一次 `muse` 完成登录。）"
        raise AgentCliError(f"`{spec_name} exec` exited with {proc.returncode}: {tail}{hint}", fatal=fatal)
    if not answer:
        raise AgentCliError(f"`{spec_name} exec` returned an empty answer. {err[-300:]}".strip())
    return answer


def call_agent_cli(
    prompt: str,
    model_name: str,
    spec_name: str = "muse",
    max_retries: int = 3,
    base_interval_sec: int = 5,
    result_parsing_func: Optional[Callable] = None,
    check_parsed_response_not_none: bool = True,
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Same contract as the SDK callers: {"raw_response", "parsed_response"}."""
    label = model_name or "default"
    raw_response = None
    parsed_response = None
    for attempt in range(max_retries):
        try:
            usage: Dict[str, int] = {}
            try:
                raw_response = run_once(spec_name, model_name, prompt, system_prompt, usage_out=usage)
            except Exception:
                usage_tracker.record_error(spec_name, label)
                raise
            # Token counts come from the run.usage event; without --json they
            # are estimated (~4 characters per token) so the call still counts.
            usage_tracker.record(spec_name, label,
                                 input_tokens=usage.get("input_tokens") or len(prompt) // 4,
                                 output_tokens=usage.get("output_tokens") or len(raw_response) // 4)
            parsed_response = result_parsing_func(raw_response) if result_parsing_func else raw_response
            if check_parsed_response_not_none:
                assert parsed_response is not None, (
                    f"Parsed response is None, Here is the raw response:\n{raw_response}")
            return {"raw_response": raw_response, "parsed_response": parsed_response}
        except Exception as e:
            print(f"Warning: {spec_name} CLI call (model={label}) failed on attempt "
                  f"{attempt + 1} of {max_retries}. Error: {e}")
            if isinstance(e, AgentCliError) and e.fatal:
                raise
            if attempt + 1 == max_retries:
                print(f"Error: All {max_retries} retry attempts failed.")
                raise
            time.sleep(base_interval_sec * (2 ** attempt))
    return {"raw_response": raw_response, "parsed_response": parsed_response}
