# PaperOrchestra Studio — Claude Desktop / Cowork Plugin

Full-featured Claude Desktop & Claude Code plugin (v1.2.0) with **100% feature parity** with Antigravity: all **22 MCP tools (+ app-only snapshot)**, **6 resources** (including 2 SEP-1865 MCP Apps `workbench.html` & `tool-tree.html` featuring Overleaf-style side-by-side LaTeX/PDF SyncTeX, abstract polish change-table, and `report.md` delivery), **5 prompts**, **6 workflow skills**, Best-Paper L1–L8 routing + structural AI/figure audit, and the **autonomous `paper-orchestra-auditor` subagent**.

## Installation Options

### 1. Claude Desktop Cowork (Recommended `.plugin` Archive)
1. Build or locate `../dist/paper-orchestra-studio.plugin`.
2. Open **Claude Desktop → Customize → Plugins → Upload plugin** and select `paper-orchestra-studio.plugin`.

### 2. Claude Desktop Chat (`claude_desktop_config.json`)
Run `../scripts/install-claude-desktop.sh` (which installs the zero-dependency server into `~/.local/share/paper-orchestra-studio-plugin/` and safely merges `mcpServers["paper-orchestra-studio"]` with backup), then restart Claude Desktop.

### 3. Claude Code CLI
```bash
claude plugin validate ./cowork-plugin
```
Add or install the directory as a local plugin/marketplace entry to expose all skills, the auditor agent, and MCP tools directly in Claude Code sessions.
