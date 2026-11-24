# PaperOrchestra Studio — Claude Desktop Extension (`.mcpb`)

Standalone MCPB (`manifest_version: "0.3"`, v1.1.0) desktop extension package for one-click installation in Claude Desktop on macOS, Windows, and Linux, exposing all 22 `po_*` tools including Desktop TCP bridge, `providers.json` validator, and conflict-safe LaTeX XeLaTeX compilation.

## GUI Configuration Fields (`user_config`)
- **PaperOrchestra Backend Directory** (optional): Override path to `paper-orchestra/` when not auto-detected via Desktop App SQLite settings or `~/paper-orchestra`.
- **Backend Python Executable** (optional): Explicit `.venv` Python interpreter containing scientific packages.
- **Default PDF Output Directory** (optional): Default directory for exported PDFs when no `--out` path is specified.
- **Default HTML Output Directory** (optional): Default directory for exported interactive Artifacts-style HTML reports.

Drag or open `../dist/paper-orchestra-studio.mcpb` with Claude Desktop to install.
