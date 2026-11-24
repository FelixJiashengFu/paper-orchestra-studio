# PaperOrchestra Studio — Antigravity Plugin

Native Antigravity plugin (v1.2.0) bundling PaperOrchestra Studio's full local AI scientific workstation over one shared zero-dependency MCP server (`paper_orchestra_mcp.py`, **23 model tools + 1 app-only snapshot / 6 resources / 5 prompts**) with **2 native interactive Antigravity MCP Apps** (`workbench.html` + `tool-tree.html` for `aux_pane` & `global` sidebar views), **6 workflow skills**, and **always-on provenance & security rules**.

- **Side-by-Side LaTeX/PDF Split View & Bidirectional SyncTeX**: `LaTeX 协同` features an Overleaf-style side-by-side split view (toggleable to stacked), built-in paginated PDF rendering (`render_pdf_page` via PyMuPDF with exact point dimensions), forward `tex -> PDF` multi-line highlight (`sync_view`, including optional auto-follow on cursor/selection), inverse `PDF -> tex` click-to-jump (`sync_edit`), OS default viewer fallback (`open_file_external`), plus award-paper `polish_abstract` diagnosis/change-table and `author-report` (`report.md`) generation.

## Directory Layout

```text
paper-orchestra-studio/
├── plugin.json                # Antigravity plugin manifest
├── mcp_config.json            # MCP server registration (${PLUGIN_ROOT} / ${PLUGIN_DATA})
├── assets/
│   ├── logo.svg               # Studio icon
│   └── ui/                    # Bundled single-file SEP-1865 MCP App HTMLs (workbench & tool-tree)
├── rules/AGENTS.md            # Provenance tags, secret protection & conflict-safe edit guardrails
├── server/paper_orchestra_mcp.py
└── skills/
    ├── po-paper-pipeline/SKILL.md
    ├── po-research-studio/SKILL.md
    ├── po-figure-studio/SKILL.md
    ├── po-literature-graph/SKILL.md
    ├── po-submission-latex/SKILL.md
    └── po-compute-memory/SKILL.md
```

## Installation in Antigravity

Copy this directory into `~/.gemini/config/plugins/paper-orchestra-studio/`:

```bash
mkdir -p ~/.gemini/config/plugins
cp -R plugins/antigravity ~/.gemini/config/plugins/paper-orchestra-studio
chmod +x ~/.gemini/config/plugins/paper-orchestra-studio/server/paper_orchestra_mcp.py
```

Antigravity discovers plugins under `~/.gemini/config/plugins/` automatically without restarting. Manage or toggle the bundle from Antigravity's **Customizations** view.

### Optional Environment Overrides (in `mcp_config.json`)

- `PAPER_ORCHESTRA_REPO`: Path to `<repo>/paper-orchestra` if not auto-detected via `~/paper-orchestra` or desktop app SQLite settings.
- `PAPER_ORCHESTRA_PYTHON`: Python interpreter for backend scripts (defaults to `.venv/bin/python` or `sys.executable`).
- `PO_DEFAULT_PDF_DIR` / `PO_DEFAULT_HTML_DIR`: Default export folders for standalone PDF and HTML reports.
