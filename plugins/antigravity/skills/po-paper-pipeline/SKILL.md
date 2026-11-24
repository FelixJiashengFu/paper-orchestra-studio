---
name: po-paper-pipeline
description: >-
  Run and manage the full PaperOrchestra Studio multi-agent academic paper
  writing pipeline across 19 conference, journal, Chinese GB/T 7714, and
  mathematical modeling LaTeX templates with 19-step LLM routing, intake form
  parsing, live job monitoring, and SQLite project management. Use when the
  user wants to initialize, configure, start, resume, monitor, or inspect an
  end-to-end PaperOrchestra paper generation run ("生成整篇论文", "启动
  PaperOrchestra 流水线", "检查模型路由与模板", "查看论文生成进度").
---

# PaperOrchestra Multi-Agent Paper Pipeline

Use the `paper-orchestra-studio_paper_orchestra` MCP server to manage projects, validate environments/models, and execute end-to-end paper generation runs.

## Workflow

1. **Preflight Environment, Bridge & Settings**:
   - Call `po_check_environment`, `po_settings_and_setup` (`get_settings`, `env_readiness`, `validate_providers_json`), and `po_check_providers` (`ping=false` first, or `ping=true` / `scholar=true` when requested).
   - Call `po_list_templates` to browse all 19 templates (conferences, Nature/ACS/AIP/RSC/Elsevier journals, Chinese GB/T 7714, MCM/CUMCM) and inspect `venue.json` style rules.
   - Use `po_studio_bridge` (`status`, `open_studio`, `launch_workbench`) to connect with a running Desktop Studio window or open `po_open_workbench` / `po_open_tool_tree` directly inside Antigravity.

2. **Prepare Project & Materials**:
   - If the user provides an intake form (`.pdf`, `.docx`, `.md`, `.txt`), parse it with `po_parse_intake_form`.
   - Use `po_projects` (`list`, `get`, `create`, `update` with `expected_updated_at` conflict guard, `import_cvpr_example`, `add_figure`, `update_figure_caption`, `delete_figure`, `list_runs`, `get_run_logs`, `resume_run`, `delete_run`) to manage projects & runs in SQLite (shared with the Tauri desktop app).

3. **Start, Monitor & Deliver Generation**:
   - Call `po_start_paper_run` with either `project_id` or `raw_materials_dir` + `template_name` (supports `step_model_overrides`). Or use `po_projects(action='resume_run', run_id=...)` to resume an interrupted run from checkpoint.
   - Monitor progress with `po_job_manage` (`status` / `logs` / `stop`), or inspect SQLite stage logs via `po_projects(action='get_run_logs')` or interactively via `po_open_workbench(tab='runs')`.
   - Inspect finished artifacts (`final_paper.pdf`, `outline.json`, `citation_audit.json`, `usage_report.json`, `peer_reviews/`) and copy `final_paper.pdf` safely without overwrite via `po_inspect_run(action='copy_pdf')`.
