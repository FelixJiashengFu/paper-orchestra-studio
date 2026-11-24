# PaperOrchestra Studio Plugins (Antigravity & Claude Desktop)

Antigravity and Claude Desktop use distinct native plugin manifest formats, so this repository ships **two dedicated native plugin bundles** powered by **one shared zero-dependency Python MCP server** (`shared/mcp_server/paper_orchestra_mcp.py`, v1.1.0, exposing **22 model-visible tools + 1 app-only snapshot tool**, **6 resources** including 2 SEP-1865 MCP Apps, and **5 prompts** across 100% of PaperOrchestra Studio's capabilities).

## Platform Matrix

| Platform | Bundle Format | Location | Included Components |
| --- | --- | --- | --- |
| **Antigravity** | `plugin.json` + `mcp_config.json` + MCP Apps | `plugins/antigravity/` | 22 MCP Tools + `ui://` Workbench & Tool Tree (`aux_pane` / `global`), 6 Resources, 5 Prompts, 6 `po-*` Skills, Always-on `AGENTS.md` Rules, SVG Logo |
| **Claude Desktop Cowork / Code** | `.claude-plugin/plugin.json` + `.mcp.json` (`.plugin` zip) | `plugins/claude-desktop/cowork-plugin/` & `dist/paper-orchestra-studio.plugin` | 22 MCP Tools, 6 Resources, 5 Prompts, 6 `po-*` Skills, `paper-orchestra-auditor` Subagent |
| **Claude Desktop Extension** | MCPB `manifest_version: "0.3"` (`.mcpb` zip) | `plugins/claude-desktop/desktop-extension/` & `dist/paper-orchestra-studio.mcpb` | One-click GUI-configurable MCP extension (`user_config` overrides) |
| **Claude Desktop Chat** | `claude_desktop_config.json` | `plugins/claude-desktop/scripts/install-claude-desktop.sh` | Safe atomic registration on internal disk (`~/.local/share/paper-orchestra-studio-plugin/`) |
| **Qoder Desktop** *(companion)* | `.qoder-plugin/plugin.json` + `.mcp.json` | `integrations/qoder-plugin/` | Bridge-powered Qoder Desktop plugin (`mcp/server.mjs`) & `paper-orchestra-studio` skill |

## Shared MCP Tools (Full Feature Parity with Repo `main` + Interactive UIs)

1. `po_check_environment` — Repo, Python, `.env` presence, TeX Live/MacTeX, Pandoc, Chrome, Lean 4, SSH/SCP, export dirs, SQLite counts
2. `po_check_providers` — 20+ LLM platforms + custom `providers.json` + 8 scholarly search sources (`--ping`, `--scholar`, `--list-models`)
3. `po_list_templates` — All 19 conference, journal, Chinese GB/T 7714, and MCM/CUMCM templates (`venue.json`)
4. `po_parse_intake_form` — `.pdf` / `.docx` / `.md` / `.txt` intake form parser
5. `po_projects` — SQLite project, figure & run CRUD (`list`, `get`, `create`, `update` with `expected_updated_at`, `delete`, `import_cvpr_example`, `add_figure`, `update_figure_caption`, `delete_figure`, `list_runs`, `get_run_logs`, `resume_run`, `delete_run`)
6. `po_start_paper_run` — Async multi-agent paper generation (`--resume`, 19-step `PO_TASK_MODEL_*` routing)
7. `po_job_manage` — Async job list, live stage & `usage_report.json` telemetry, redacted logs, clean stop
8. `po_inspect_run` — Run artifacts summary (`final_paper.pdf`, `citation_audit.json`, `peer_reviews/`), traversal-safe `read_artifact`, exclusive non-overwriting `copy_pdf`
9. `po_data_analysis` — Offline dataset profiling & 7-step reproducible self-fixing Python analysis pipeline
10. `po_run_sop` — All 5 Research Studio SOPs (`paper_reading`, `lit_review`, `topic_ideation`, `reproduction`, `math_modeling`) with memory injection
11. `po_custom_skills` — 5 built-in Studio SOP skills + shared custom `.md` CRUD (`list`, `get`, `save`, `import_file`, `delete`), remote `SKILL.md` fetcher & execution
12. `po_figure_studio` — 3-route plotting (`data_code`, `schematic_svg`, `concept_image`), version bundles, normalized `bbox` (`'x,y,w,h'` or `{x,y,w,h}`) local revision, restore
13. `po_literature_library` — 8-source search, DOI/PDF import into SQLite, paginated `list` (`folder`/`offset`/`limit`), citation knowledge graph, BibTeX & GB/T 7714 export
14. `po_latex_copilot` — AI LaTeX editing copilot & inline claim-to-citation traceability
15. `po_latex_workbench` — Conflict-safe `open`/`read`/`save` (`expected_mtime_ms` + `.edit_backup/`), `restore`, auto-CJK XeLaTeX (`-pdfxe`) / pdfLaTeX `compile` with structured issues, SyncTeX lookup, clean `export_overleaf` ZIP
16. `po_submission_audit` — All 8 checks: `manuscript-audit` (Nature), `cite-online`, `arxiv`, `blind`, `diagnose`, `versions`, `diff` (`latexdiff`), `lean-audit`
17. `po_export_document` — Claude Artifacts-style offline interactive HTML (TOC, Paper/Light/Dark themes, provenance pills, MathML, SVG Mermaid), Word `.docx`, PDF, Google Drive
18. `po_compute_and_memory` — Persistent Research Memories & Preferences + SSH/GPU hosts (`nvidia-smi` probe, remote `exec`, `scp`)
19. `po_studio_bridge` — Loopback TCP v1 bridge to running Studio Desktop app (`status`, auto-launch & `open_studio`, bridge jobs) & `integrations/` launcher
20. `po_settings_and_setup` — SQLite `app_settings` get/update, `providers.json` static validator (`providers.rs` parity), boolean 20-platform readiness, `scripts/setup-backend.*` discovery
21. `po_open_workbench` — Interactive 9-tab MCP App UI (Overview + Canvas Collaboration Console, Projects, Runs, Paginated Library, Interactive Drag-Box Figure Bench, LaTeX Co-Editor with built-in paginated PDF preview & `refined_paper_v*.tex` selection, Audits & Exports, Skills/Env, Safe Tools Console)
22. `po_open_tool_tree` — Interactive 10-group PaperOrchestra Tool Tree MCP App UI (`aux_pane`)
(+ internal app-only `po_ui_snapshot` powering live UI refreshes, local figure previews, PyMuPDF `render_pdf_page`, and OS viewer fallback `open_file_external`)

## Rebuild Archives

```bash
python3 plugins/claude-desktop/scripts/build-packages.py
```
