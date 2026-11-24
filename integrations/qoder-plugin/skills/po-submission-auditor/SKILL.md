---
name: po-submission-auditor
description: >
  This skill should be used when the user asks for "a full pre-submission
  audit", "an autonomous submission check", "a reproducibility and
  statistical rigor audit", or "audit this run directory end to end"
  (投稿前全流程自动审计、可复现性与统计严谨性审计、运行目录端到端核查). It is the
  skill form of the `paper-orchestra-auditor` subagent for platforms without
  plugin subagents.
---

# PaperOrchestra End-to-End Submission Auditor

Run the complete pre-submission and reproducibility audit of a manuscript, a PaperOrchestra run directory, or a Lean 4 proof repository in one pass. On platforms that support plugin subagents, delegating to `paper-orchestra-auditor` is equivalent.

## Instructions

1. **Inventory**: `po_inspect_run(action='summary')` to list `.tex`, `.bib`, `final_paper.pdf`, `citation_audit.json`, `usage_report.json`, `peer_reviews/`.
2. **Deterministic checks** with `po_submission_audit` on the run directory: `manuscript-audit`, `cite-online`, `blind` (if double-blind), `diagnose`, `versions`, and `lean-audit` for formal proofs (`project_dir`). Add `po_revision_toolkit` `preflight` (limits, placeholders, anonymity, statements) and `retraction_check` (retracted references).
3. **Claim grounding**: `po_latex_copilot(action='trace_citations')`, and `polish_abstract` with `mode='diagnose'` for abstract structure.
4. **Source fixes** (only when the user asked for fixes): `po_latex_workbench` `open` and `read`, `save` with `expected_mtime_ms`, then `compile` and confirm a clean log.
5. **Delivery**: `po_submission_audit(action='author-report')` for `report.md`, `po_latex_workbench(action='export_overleaf')` for the upload archive, `po_inspect_run(action='copy_pdf')` for a non-overwriting PDF copy.

## Output format

Group findings into **Pass / Ready**, **Warnings (Recommended Fixes)**, and **Blocking Issues (Must Fix Before Submission)** with exact file paths, line numbers, and remediation steps. Tag evidence with `【原文】`, `【审读推导】`, or `【不可核实】`. Never invent DOIs, grant disclosures, sample sizes, or metrics, and never expose raw API secrets.
