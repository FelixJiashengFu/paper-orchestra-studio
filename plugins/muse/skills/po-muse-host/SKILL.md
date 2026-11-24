---
name: po-muse-host
description: Explain and check PaperOrchestra's Muse-only mode, where every backend LLM step runs on the user's own Muse Code sign-in and no other LLM API is called. Use when the user asks which model or quota PaperOrchestra uses in Muse, asks about API keys, cost or allowance (额度, 用量, API key), or when a po_* run fails with a `muse exec` error.
---

# PaperOrchestra in Muse: your own Muse allowance, no other LLM API

In this plugin the PaperOrchestra backend is started with `PO_HOST_AGENT=muse`. Every step that needs a language model (paper pipeline, Research Studio SOPs, data analysis, figure code, LaTeX copilot, abstract polishing) is sent to the signed-in Muse Code CLI as `muse exec`, one call per step. Keys for OpenAI, Anthropic, Gemini, DeepSeek and the other providers in `paper-orchestra/.env` are ignored, and model names of other providers are served by Muse instead.

## Check it

1. Call `po_check_environment` and read `hostAgent`: `enabled` must be true, `name` is `muse`, `cliFound` must be true.
2. `po_check_providers` lists `muse` as configured. Its connection test only runs `muse --version`; it sends no prompt, because each prompt counts against the allowance.
3. To see the allowance, ask the user to run `muse usage` in a terminal. Do not read `~/.config/muse/auth.json`.

## Rules while this mode is on

- Never ask for, print or configure another provider's API key. Leave every model field on `auto`. A specific Muse model can be requested as `muse:<model-id>`, for example `muse:muse-spark-1.2`.
- Before a full paper run (`po_start_paper_run`), tell the user that it makes many model calls on their Muse allowance, and wait for their go-ahead. For short pieces (one paragraph, an abstract polish, a reply to one reviewer point) write the text yourself in this conversation instead of starting a backend job.
- Web search by another model is off. Literature comes from the scholarly sources (Semantic Scholar, OpenAlex, arXiv, Crossref and the rest), which are not LLM APIs.
- Image generation by another model is off. Diagrams are drawn as Matplotlib or SVG code. Figures are described to the model as text, because the headless call is text-only.

## If a run fails

- `muse exec exited ... usage limit / quota`: the allowance is used up. Wait for it to reset or run `/upgrade` in Muse, then resume the run with `po_projects(resume_run)`. Finished stages are kept.
- `not logged in`: ask the user to run `muse` once in a terminal and sign in.
- `找不到 muse 命令行`: Muse Code is not installed or not on PATH. Set `MUSE_CLI` to its path, or install it.
- Timeouts on long sections: lower the effort with `PO_MUSE_REASONING_EFFORT=medium`, or raise `PO_MUSE_TIMEOUT` (seconds, default 1800).

## Settings (environment variables of the MCP server)

| Variable | Meaning |
| --- | --- |
| `MUSE_DEFAULT_MODEL` | Model for every step. Unset means the model Muse Code itself is set to use. |
| `PO_MUSE_REASONING_EFFORT` | `none` to `ultra`. Unset means Muse Code's own setting. |
| `PO_MUSE_CONCURRENCY` | Calls running at once (default 2). |
| `PO_MUSE_TIMEOUT` | Seconds for one call (default 1800). |
| `PO_HOST_AGENT_STRICT=0` | Muse stays the default, but explicit model names of other providers reach their own APIs again. |
| `PO_HOST_AGENT=off` | Turn the mode off for this server. |
