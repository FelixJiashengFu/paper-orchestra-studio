---
name: po-paper-pipeline
description: >
  This skill should be used when the user asks to "start a PaperOrchestra run",
  "generate a full LaTeX paper", "check PaperOrchestra models or templates",
  "monitor or resume a paper pipeline run", or "parse an intake form"
  (生成整篇论文、启动 PaperOrchestra 流水线、检查 19 套期刊会议模板与模型路由).
---

# PaperOrchestra Multi-Agent Paper Pipeline

Execute and monitor end-to-end academic paper generation across 19 conference, journal, Chinese GB/T 7714, and mathematical modeling templates using the `paper-orchestra-studio` MCP tools.

## Instructions

1. **Validate Environment, Bridge, Settings & Templates**:
   - Call `po_check_environment`, `po_settings_and_setup` (`get_settings`, `env_readiness`, `validate_providers_json`, `setup_info`), and `po_check_providers` (`ping=false` first, or `ping=true` / `scholar=true`).
   - Call `po_list_templates` to browse all 19 templates and `venue.json` editorial rules.
   - Use `po_studio_bridge` (`status`, `open_studio`, `launch_workbench`) to link with a running PaperOrchestra Studio Desktop window when desired.

2. **Manage Projects & Intake Forms**:
   - Parse `.pdf`, `.docx`, `.md`, or `.txt` intake forms with `po_parse_intake_form`.
   - Manage SQLite project & run records with `po_projects` (`list`, `get`, `create`, `update` with `expected_updated_at`, `import_cvpr_example`, `delete`, `add_figure`, `update_figure_caption`, `delete_figure`, `list_runs`, `get_run_logs`, `resume_run`, `delete_run`).

3. **Launch, Monitor & Inspect Runs**:
   - Start or resume generation asynchronously with `po_start_paper_run` (or `po_projects(action='resume_run', run_id=...)`), supplying `project_id` or `raw_materials_dir` + `template_name` and optional `step_model_overrides`.
   - Poll progress, token usage, and redacted log tails via `po_job_manage` (`list`, `status`, `logs`, `stop`) or `po_projects(action='get_run_logs')`.
   - Summarize finished outputs (`final_paper.pdf`, `citation_audit.json`, `usage_report.json`, `peer_reviews/`), read relative artifacts, or copy `final_paper.pdf` without overwrite via `po_inspect_run(action='copy_pdf')`.
