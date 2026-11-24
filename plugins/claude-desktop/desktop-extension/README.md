# PaperOrchestra Studio — Claude Desktop Extension (`.mcpb`)

Standalone MCPB (`manifest_version: "0.3"`, v1.1.0) desktop extension package for one-click installation in Claude Desktop on macOS, Windows, and Linux with **100% feature parity with Antigravity**: exposing all **22+1 `po_*` tools** and **both SEP-1865 MCP Apps (`workbench.html`, `tool-tree.html`)** including Overleaf-style side-by-side XeLaTeX/SyncTeX editor, Best-Paper structural AI-rhythm & figure audit, `polish_abstract` sentence change-table, `author-report` (`report.md`), Desktop TCP bridge, and `providers.json` validator.

## GUI Configuration Fields (`user_config`)
- **PaperOrchestra Backend Directory** (optional): Override path to `paper-orchestra/` when not auto-detected via Desktop App SQLite settings or `~/paper-orchestra`.
- **Backend Python Executable** (optional): Explicit `.venv` Python interpreter containing scientific packages.
- **Default PDF Output Directory** (optional): Default directory for exported PDFs when no `--out` path is specified.
- **Default HTML Output Directory** (optional): Default directory for exported interactive Artifacts-style HTML reports.

Drag or open `../dist/paper-orchestra-studio.mcpb` with Claude Desktop to install.
