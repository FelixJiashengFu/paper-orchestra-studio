# PaperOrchestra Studio Plugins (Claude Code · Antigravity · Codex · Qoder · WorkBuddy)

Five agent platforms, one feature set. Every bundle is generated from `plugins/shared/` and ships the same **zero-dependency Python MCP server** (`shared/mcp_server/paper_orchestra_mcp.py`, v1.2.0, **24 model-visible tools + 1 app-only snapshot tool**, **6 resources** including 2 SEP-1865 MCP Apps, **5 prompts**), the same **Studio desktop bridge server** (71 tools, Node), and the same **12 workflow skills**.

## Five-Platform Parity

| Platform | Bundle | Manifest | MCP registration | Skills | Auditor subagent | Marketplace file (repo root) |
| --- | --- | --- | --- | --- | --- | --- |
| **Claude Code** (and Claude Desktop Cowork) | `plugins/claude-desktop/cowork-plugin/` | `.claude-plugin/plugin.json` | `.mcp.json` (`${CLAUDE_PLUGIN_ROOT}`) | 12 | agent | `.claude-plugin/marketplace.json` |
| **Google Antigravity** | `plugins/antigravity/` | `plugin.json` | `mcp_config.json` (`${PLUGIN_ROOT}`) | 12 | agent | (install by path) |
| **OpenAI Codex** | `plugins/codex/` | `.codex-plugin/plugin.json` | `.mcp.json` (relative `cwd`) | 12 | as skill `po-submission-auditor` | `.agents/plugins/marketplace.json` |
| **Qoder** | `integrations/qoder-plugin/` | `.qoder-plugin/plugin.json` | `.mcp.json` (`${QODER_PLUGIN_ROOT}`) | 12 | agent | (install by path or zip) |
| **WorkBuddy / CodeBuddy** | `plugins/workbuddy/` | `.codebuddy-plugin/plugin.json` | `.mcp.json` (`${CODEBUDDY_PLUGIN_ROOT}`) | 12 | agent | `.codebuddy-plugin/marketplace.json` |

**Claude Code sidebar pane (`/po-pane`)** lives in `plugins/claude-code-pane/`. It is a Claude Code mod that draws a live panel from the shared server's tools; other hosts have no pane extension point and use the same tools plus the MCP Apps workbench instead. The verifier checks that every tool the pane calls still exists.

**Coder (coder.com) workspaces** have no plugin format of their own: they run Claude Code or Codex remotely. `plugins/coder/` holds a workspace install script and a template snippet that load the same bundles there; the verifier checks that recipe too.

The guardrails that Antigravity loads from `rules/AGENTS.md` are also sent by the server in its MCP `initialize` instructions, so hosts whose plugin format has no always-on rules receive them too.

### Verify all five

```bash
python3 plugins/scripts/verify_platforms.py          # 44 checks: manifests, files, skills, agents, marketplaces, live MCP handshakes
python3 -m unittest discover -s plugins/tests -v     # same checks as unit tests (also run in CI: .github/workflows/plugin-parity.yml)
```

The check starts both MCP servers of every bundle the way the host would (placeholders resolved to the bundle folder) and compares the reported tools, resources, prompts and UI payloads with the shared source. It does not prove that a host application loads the bundle; after installing, ask the agent to call `po_check_environment` as a smoke test.

### Change something once, ship it five times

```bash
# edit plugins/shared/{mcp_server,skills,agents,ui_src} ...
python3 plugins/scripts/sync_platforms.py            # copy into all five bundles (never deletes)
python3 plugins/scripts/install.py --platform all    # build per-platform zips in plugins/dist/ and print install steps
```

### Workflow skills (identical on all five)

`po-paper-pipeline`, `po-research-studio`, `po-figure-studio`, `po-literature-graph`, `po-submission-latex`, `po-compute-memory`, `paper-orchestra-studio` (desktop bridge), and new in v1.2.0: `po-reviewer-response` (review triage, point-by-point response letter, latexdiff revision), `po-venue-preflight` (venue shortlist, desk-reject preflight, page-limit fitting, camera-ready, cover letter), `po-claim-evidence-audit` (claim ledger, number consistency, reproducibility checklist, data-availability and AI-use statements), `po-talk-poster` (slides, speaker script, Q&A rehearsal, poster, outreach text), `po-submission-auditor` (the auditor subagent as a skill).

---

<details><summary>Previous introduction (kept for reference)</summary>

Antigravity and Claude Desktop use distinct native plugin manifest formats, so this repository ships **two dedicated native plugin bundles** powered by **one shared zero-dependency Python MCP server** (`shared/mcp_server/paper_orchestra_mcp.py`, v1.1.0, exposing **24 model-visible tools + 1 app-only snapshot tool**, **6 resources** including 2 SEP-1865 MCP Apps, and **5 prompts** across 100% of PaperOrchestra Studio's capabilities).

</details>

## Bundle Formats (incl. Claude Desktop Extension and Chat config)

| Platform | Bundle Format | Location | Included Components |
| --- | --- | --- | --- |
| **Antigravity** | `plugin.json` + `mcp_config.json` + MCP Apps | `plugins/antigravity/` | 24 MCP Tools + `ui://` Workbench & Tool Tree (`aux_pane` / `global`), 6 Resources, 5 Prompts, 6 `po-*` Skills, Always-on `AGENTS.md` Rules, SVG Logo |
| **Claude Desktop Cowork / Code** | `.claude-plugin/plugin.json` + `.mcp.json` (`.plugin` zip) | `plugins/claude-desktop/cowork-plugin/` & `dist/paper-orchestra-studio.plugin` | 24 MCP Tools, 6 Resources, 5 Prompts, 6 `po-*` Skills, `paper-orchestra-auditor` Subagent |
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
14. `po_latex_copilot` — AI LaTeX editing copilot (`edit` with line ranges), inline claim-to-citation traceability (`trace_citations`), and award-paper abstract diagnosis + sentence change-table polishing (`polish_abstract`)
15. `po_latex_workbench` — Conflict-safe `open`/`read`/`save` (`expected_mtime_ms` + `.edit_backup/`), `restore`, auto-CJK XeLaTeX (`-pdfxe`) / pdfLaTeX `compile` with structured issues, bidirectional SyncTeX (`sync_view` multi-line highlight box & `sync_edit` click-to-jump), clean `export_overleaf` ZIP
16. `po_submission_audit` — Submission verification & author report tools: Nature + award-paper structural AI-rhythm/figure `manuscript-audit`, `author-report` (`report.md`), live `cite-online` DOI/arXiv/S2 verification, `arxiv`, `blind`, `diagnose`, `versions`, `diff` (`latexdiff`), `lean-audit`
17. `po_export_document` — Claude Artifacts-style offline interactive HTML (TOC, Paper/Light/Dark themes, provenance pills, MathML, SVG Mermaid), Word `.docx`, PDF, Google Drive
18. `po_compute_and_memory` — Persistent Research Memories & Preferences + SSH/GPU hosts (`nvidia-smi` probe, remote `exec`, `scp`)
19. `po_studio_bridge` — Loopback TCP v1 bridge to running Studio Desktop app (`status`, auto-launch & `open_studio`, bridge jobs) & `integrations/` launcher
20. `po_settings_and_setup` — SQLite `app_settings` get/update, `providers.json` static validator (`providers.rs` parity), boolean 20-platform readiness, `scripts/setup-backend.*` discovery
21. `po_open_workbench` — Interactive 9-tab MCP App UI (Overview + Canvas Collaboration Console, Projects, Runs, Paginated Library, Interactive Drag-Box Figure Bench, Overleaf-style side-by-side LaTeX Co-Editor with paginated PDF preview & bidirectional SyncTeX highlight/jump, Audits & Exports, Skills/Env, Safe Tools Console)
22. `po_open_tool_tree` — Interactive 10-group PaperOrchestra Tool Tree MCP App UI (`aux_pane`)
23. `po_revision_toolkit` — Deterministic revision & submission checks without an LLM call: `review_matrix` (atomic reviewer comments with IDs, type, severity, Markdown response matrix), `preflight` (page/word/abstract limits, placeholders, undefined references, double-blind leaks, required statements), `retraction_check` (Crossref retraction / concern / correction notices per DOI), `reporting_checklist` (CONSORT, PRISMA, STROBE, ARRIVE, ML reproducibility)
24. `po_reference_sync` — Zotero (local Zotero 7 API or web API): `zotero_search`, `zotero_import` into the SQLite library (keeps notes and reading status on re-import), `zotero_bibtex` merge into a run's `.bib` with backup, `library_to_ris` export; Overleaf git integration: `overleaf_link`, `overleaf_pull` (never overwrites differing files without `allow_overwrite`), `overleaf_push` (dry run unless `confirm_push=true`, fast-forward only, never deletes); `status`. Credentials only from `ZOTERO_API_KEY` / `ZOTERO_USER_ID` / `OVERLEAF_GIT_TOKEN`
(+ internal app-only `po_ui_snapshot` powering live UI refreshes, local figure previews, PyMuPDF `render_pdf_page` with page point geometry, and OS viewer fallback `open_file_external`)

## Rebuild Archives

```bash
python3 plugins/claude-desktop/scripts/build-packages.py
```
