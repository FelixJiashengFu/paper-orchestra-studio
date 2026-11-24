# PaperOrchestra Studio — OpenAI Codex Plugin

Codex plugin (v1.2.0) with the same feature set as the Claude Code, Antigravity, Qoder and WorkBuddy bundles: the shared zero-dependency Python MCP server (**24 model tools + 1 app-only snapshot / 6 resources / 5 prompts**), the Studio desktop bridge server (71 tools, Node), and **12 workflow skills**.

## Layout

```text
plugins/codex/
├── .codex-plugin/plugin.json   # manifest (skills + mcpServers + interface)
├── .mcp.json                   # two stdio servers, paths relative to the plugin root
├── AGENTS.md                   # optional always-on rules (the server also sends them at MCP initialize)
├── server/paper_orchestra_mcp.py
├── mcp/{server,studio,tools}.mjs
├── assets/ui/{workbench,tool-tree}.html
└── skills/<12 skills>/SKILL.md
```

## Install

The repository root carries a Codex marketplace file at `.agents/plugins/marketplace.json`.

```bash
codex plugin marketplace add FelixJiashengFu/paper-orchestra-studio   # or a local clone path
```

Then enable **PaperOrchestra Studio** in the Plugins view of the Codex / ChatGPT desktop app, or add to `.codex/config.toml`:

```toml
[plugins."paper-orchestra-studio@paper-orchestra-studio"]
enabled = true
```

Requirements: `python3` (3.9+) on `PATH`; `node` 20+ for the desktop bridge server. On Windows, change `"command": "python3"` to `"python"` in `.mcp.json` if `python3` is not available.

## Platform notes

- Codex plugins do not bundle subagents, so the `paper-orchestra-auditor` agent is shipped as the `po-submission-auditor` skill.
- `po_open_workbench` / `po_open_tool_tree` return a text summary on hosts that do not render MCP Apps.
- Optional overrides (`PAPER_ORCHESTRA_REPO`, `PAPER_ORCHESTRA_PYTHON`, `PO_DEFAULT_PDF_DIR`, `PO_DEFAULT_HTML_DIR`) are passed through from your environment.

Check the bundle: `python3 plugins/scripts/verify_platforms.py`.
