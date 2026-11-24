---
name: po-claim-evidence-audit
description: >
  This skill should be used when the user asks to "verify the claims in my
  paper", "check claims against evidence", "audit reported numbers",
  "check for overclaiming", "write a data availability statement",
  "write an AI-use disclosure", or "run a reproducibility checklist"
  (论断与证据核对、数字一致性核查、过度表述检查、数据可用性声明、AI 使用声明、可复现性清单).
---

# PaperOrchestra Claim-Evidence & Research Integrity Audit

Check that every claim in a manuscript is supported by a citation, a figure or table, or the user's own data, and prepare the integrity statements journals ask for, using `paper-orchestra-studio` MCP tools.

## Instructions

1. **Extract the claim ledger**:
   - Read the source with `po_latex_workbench(action='open')` and `read`.
   - List each empirical or comparative claim in the abstract, introduction contributions, results and conclusion: `ID`, verbatim sentence, file and line, claim type (quantitative result / comparison / causal / novelty / generality).

2. **Attach evidence to each claim**:
   - Citations: `po_latex_copilot(action='trace_citations')`; confirm the cited works exist with `po_submission_audit(action='cite-online')` and read them through `po_literature_library` when the claim depends on what the source says.
   - Figures and tables: locate the float the sentence refers to; compare the number in the text with the number in the table or plot data.
   - Raw data: when the user provides result files, recompute with `po_data_analysis(action='profile'|'analyze')` and compare with the manuscript.

3. **Grade each claim**:
   - `Supported`【原文】: evidence found and consistent.
   - `Weakened`【审读推导】: evidence exists but the wording is stronger than it allows (for example "significantly" without a test, "state of the art" against a partial baseline set, causal verbs for correlational results). Propose the minimal rewording.
   - `Unsupported`【不可核实】: no evidence located. Never fill the gap with invented numbers or citations.
   - Flag number mismatches between abstract, body, tables and captions, and statistics reported without n, dispersion, or test name.

4. **Reproducibility checklist**: run `po_revision_toolkit(action='reporting_checklist', guideline='ml_reproducibility'|'consort'|'prisma'|'strobe'|'arrive', run_dir=...)` for line-level evidence and missing topics, and `po_revision_toolkit(action='retraction_check')` for retracted references. Then review by hand: code and data location, random seeds, hardware, hyperparameters, dataset splits and licences, number of runs, compute budget. Mark each as stated (with line), missing, or not applicable. For formal proofs run `po_submission_audit(action='lean-audit')`.

5. **Draft the statements**, only from facts in the manuscript or supplied by the user:
   - Data and code availability (repository, licence, accession numbers left as explicit `TODO` when unknown).
   - AI-use disclosure: which tools were used for what (drafting, editing, figures, analysis), and that the authors reviewed and take responsibility. Use `usage_report.json` from `po_inspect_run` for the models actually used in a PaperOrchestra run.
   - Competing interests, ethics and funding: templates with `TODO` fields; never guess.

6. **Deliver**: a ledger table (claim, grade, evidence, fix) plus the statements; apply accepted fixes with `po_latex_copilot(action='edit')` and export the report with `po_export_document`.
