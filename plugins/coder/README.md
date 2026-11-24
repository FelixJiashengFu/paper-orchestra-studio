# PaperOrchestra Studio — Coder (coder.com) workspaces

Coder is not an agent with its own plugin format: it provisions remote workspaces in which Claude Code or Codex run. This folder is therefore a **deployment recipe**, not a sixth copy of the bundle. Inside the workspace the agents load the same Claude Code and Codex bundles as everywhere else, so the feature set is identical (24 `po_*` tools, 71 bridge tools, 12 skills).

| File | Use |
| --- | --- |
| `install-in-workspace.sh` | Run inside any workspace (or as a startup script). Clones or updates this repository, self-checks the MCP server, installs the plugin into Claude Code and adds the marketplace to Codex when those CLIs exist. Never deletes anything. |
| `paper-orchestra.tf` | Template snippet for the registry `claude-code` module: runs the script before the agent starts and registers both MCP servers through the module's `mcp` input. |

```bash
# inside a workspace
curl -fsSL https://raw.githubusercontent.com/FelixJiashengFu/paper-orchestra-studio/main/plugins/coder/install-in-workspace.sh | bash
```

Notes:

- The workspace image needs `git` and `python3`; `node` 20+ for the desktop bridge server. LaTeX, Pandoc and the Python backend (`scripts/setup-backend.sh`) are needed for compiling and for the paper pipeline, as on a local machine.
- The Studio desktop app does not run in a headless workspace, so bridge tools report that Studio is not running; the `po_*` tools work without it.
- `paper-orchestra.tf` was written against the module README (inputs `workdir`, `mcp`, `pre_install_script`) and has not been applied to a live Coder deployment. Coder Tasks is being replaced by Coder Agents in newer releases; the shell script does not depend on either.
- `python3 plugins/scripts/verify_platforms.py` also checks this recipe (script syntax, and that the servers named in the template exist and answer).
