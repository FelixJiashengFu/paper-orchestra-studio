---
name: po-research-studio
description: >-
  Execute PaperOrchestra Research Studio backend workflows via MCP: 5 structured
  scientific SOPs (`paper_reading` 6-step deep read, `lit_review`
  anti-fabrication synthesis, `topic_ideation` 0-to-1 proposal, `reproduction`
  7-step code/paper plan, `math_modeling` MCM/CUMCM), 7-step reproducible data
  analysis & profiling (`po_data_analysis`), and custom GitHub/local Markdown
  skills (`po_custom_skills`). Use when running PaperOrchestra backend SOPs,
  automated Python statistical analysis pipelines, or remote SKILL.md workflows
  through the Studio backend.
---

# PaperOrchestra Research Studio

Drive Research Studio's automated LLM + sandboxed Python execution backend via `paper-orchestra-studio_paper_orchestra`.

## Capabilities & Tool Mapping

1. **Structured Scientific SOPs (`po_run_sop`)**:
   - `paper_reading`: 6-step deep read with SHA-256 manifest and provenance tags (`【原文】` / `【审读推导】` / `【不可核实】`).
   - `lit_review`: Anti-fabrication thematic literature review (`Q -> E -> T -> A`).
   - `topic_ideation`: From-zero research question & novelty scoping.
   - `reproduction`: 7-step paper reproduction blueprint & hyperparameter verification.
   - `math_modeling`: Complete MCM/CUMCM mathematical modeling & sensitivity workflow.
   - Automatically injects enabled SQLite Research Memories & Preferences when `include_memories=true` (default). Use `async_mode=true` for large inputs and poll `po_job_manage`.

2. **Reproducible Tabular Data Analysis (`po_data_analysis`)**:
   - First run `action='profile'` on `.csv` / `.tsv` / `.xlsx` / `.parquet` / `.json` for fast offline schema, missingness, and outlier checks.
   - Then run `action='analyze'` (with `question` and `out_dir`) to execute the 7-step reversible cleaning & self-fixing Python statistical/plotting loop (`async_mode=true` by default; track with `po_job_manage`).

3. **Built-in & Custom Markdown Skills (`po_custom_skills`)**:
   - `list` / `get`: Inspect all **5 built-in Studio skills** (`builtin-paper-deep-read`, `builtin-lit-review-gbt7714`, `builtin-data-analysis-7step`, `builtin-paper-reproduction`, `builtin-nature-de-ai-polish`) alongside shared/custom `.md` skills.
   - `save` / `import_file` / `delete` (built-ins are protected from overwrite/deletion).
   - `fetch_url` to import any GitHub or HTTPS `SKILL.md` file.
   - `run` to execute any built-in or custom skill via Research Studio with optional attached `files` and long-term memories.
