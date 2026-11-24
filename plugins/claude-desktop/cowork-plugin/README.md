# PaperOrchestra Studio — Claude Desktop / Cowork Plugin

Full-featured Claude Desktop & Claude Code plugin (v1.1.0) bundling all **22 MCP tools**, **6 resources**, **5 prompts**, **6 workflow skills**, and the **autonomous `paper-orchestra-auditor` subagent**.

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
