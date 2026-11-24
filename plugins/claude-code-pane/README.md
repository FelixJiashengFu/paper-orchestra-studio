# PaperOrchestra pane for Claude Code (`/po-pane`)

A Claude Code mod that keeps a PaperOrchestra panel beside the chat: Studio connection status, projects, runs (cost, tokens, stage timings, citation audit), background jobs, the literature library with DOI import, and environment / provider checks. It refreshes every 60 seconds and shows a toast when a run changes state.

The pane has no logic of its own. Every number comes from the `paper-orchestra-studio` MCP server's tools (`po_studio_bridge`, `po_projects`, `po_job_manage`, `po_literature_library`, `po_inspect_run`, `po_check_environment`, `po_check_providers`), so the main plugin must be installed first.

## Install

```
/plugin install paper-orchestra-studio --marketplace FelixJiashengFu/paper-orchestra-studio
/plugin install paper-orchestra-pane --marketplace FelixJiashengFu/paper-orchestra-studio
```

Then run `/po-pane`. While developing, `claude --plugin-dir plugins/claude-code-pane` loads it from the checkout and `claude plugin validate plugins/claude-code-pane` checks it.

## Other platforms

Panes beside the chat are a Claude Code feature; Antigravity, Codex, Qoder and WorkBuddy have no equivalent extension point. On those hosts the same information is reached through the same tools, and `po_open_workbench` / `po_open_tool_tree` open the bundled MCP Apps UI where the host renders it.

## Notes

- "Open PDF" and "Show in folder" work on macOS (`open`), Windows (`explorer.exe`) and Linux / WSL (`xdg-open`, then `wslview`, then `gio open`). Linux has no "select this file" verb, so revealing a run folder opens the folder.
- `python3 plugins/scripts/verify_platforms.py` checks that the pane targets the server key the Claude Code plugin registers and that every tool it calls still exists.
