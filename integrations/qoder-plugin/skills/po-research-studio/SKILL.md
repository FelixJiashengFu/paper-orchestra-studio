---
name: po-research-studio
description: >
  This skill should be used when the user asks to "deep read a paper",
  "run a Research Studio SOP", "profile or analyze an experiment dataset",
  "run literature review or topic ideation", "plan paper reproduction",
  "solve math modeling", or "fetch and run a GitHub SKILL.md"
  (运行六步精读、防编造综述、从零选题、文献复现、数学建模、可逆数据清洗与统计分析).
---

# PaperOrchestra Research Studio

Drive automated scientific SOPs, tabular data profiling/analysis, and custom research skills through `paper-orchestra-studio` MCP tools.

## Instructions

1. **Run Any of the 5 Scientific SOPs (`po_run_sop`)**:
   - Invoke `po_run_sop` with `sop` in `paper_reading`, `lit_review`, `topic_ideation`, `reproduction`, or `math_modeling`.
   - Pass attached `files` (PDF, HTML, LaTeX, Markdown), `topic`/`question`, optional `library_items`, and keep `include_memories=true` to apply saved Research Memories & Preferences automatically.
   - For strict deep reading, maintain `【原文】` (exact source citations), `【审读推导】` (independent verification), and `【不可核实】` (missing evidence) markers without fabricating claims.

2. **Profile & Analyze Tabular Datasets (`po_data_analysis`)**:
   - Start with `action='profile'` on `.csv`, `.tsv`, `.xlsx`, `.parquet`, or `.json` files for offline schema, missing-value, and outlier diagnostics.
   - Run `action='analyze'` to execute the 7-step reproducible self-fixing Python analysis and plotting pipeline (`async_mode=true` by default; track with `po_job_manage`).

3. **Built-in & Custom Markdown Skills (`po_custom_skills`)**:
   - Inspect the **5 built-in Studio SOP skills** (`builtin-paper-deep-read`, `builtin-lit-review-gbt7714`, `builtin-data-analysis-7step`, `builtin-paper-reproduction`, `builtin-nature-de-ai-polish`) and shared custom skills (`list`, `get`).
   - Store/import/remove custom Markdown skills (`save`, `import_file`, `delete` with built-in protection), fetch remote GitHub `SKILL.md` URLs (`fetch_url`), or run any built-in/custom skill with attached files (`run`).
