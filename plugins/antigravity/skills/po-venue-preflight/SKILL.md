---
name: po-venue-preflight
description: >
  This skill should be used when the user asks to "choose a venue",
  "check submission requirements", "run a desk-reject preflight",
  "fit the page limit", "retarget the paper to another venue",
  "prepare the camera-ready version", or "write a cover letter"
  (选刊选会、投稿要求核对、Desk-Reject 预检、页数压缩、换模板改投、Camera-Ready、投稿信).
---

# PaperOrchestra Venue Selection, Preflight & Camera-Ready

Shortlist venues, check a manuscript against the target venue's hard requirements before submission, fit the page budget, and assemble the final deliverables, using `paper-orchestra-studio` MCP tools.

## Instructions

1. **Shortlist venues**:
   - Call `po_list_templates` to see the 19 bundled templates and each `venue.json` (structure rules, style rules, deadline month).
   - Ask the user for the official call for papers or author guidelines (URL or file) when requirements are not in `venue.json`; treat page limits, deadlines and policies you cannot read from a source as `【不可核实】`.
   - Rank at most five candidates by scope fit, contribution type, and evidence strength. State the reason for each rank in one line.

2. **Build a requirements card** for the chosen venue: page or word limit (and what is excluded from it), anonymity policy, reference style, required statements (data and code availability, ethics, competing interests, AI-use disclosure), supplementary rules, file format.

3. **Run the deterministic preflight** on the run directory:
   - `po_revision_toolkit(action='preflight', run_dir=..., max_pages=..., max_words=..., max_abstract_words=..., anonymous=true|false, required_statements=[...])` with the limits from the requirements card: page, word and abstract limits, draft placeholders, undefined references, double-blind leaks, required statements.
   - `po_revision_toolkit(action='retraction_check', run_dir=...)` to make sure no cited DOI carries a retraction or expression-of-concern notice.
   - `po_submission_audit(action='manuscript-audit')` for word counts, placeholders, figure and caption checks, required statements.
   - `po_submission_audit(action='blind')` when the venue is double-blind (author leaks, acknowledgements, font embedding).
   - `po_submission_audit(action='cite-online')` for reference existence and metadata.
   - `po_latex_workbench(action='compile')` then `po_submission_audit(action='diagnose')` for a clean log.
   - `po_submission_audit(action='author-report')` to produce `report.md`.

4. **Fit the page limit** without changing claims:
   - Compare the compiled page count with the card. Cut in this order: redundant restatements, over-long related work, figure whitespace and oversized floats, appendix-eligible derivations. Never use negative `\vspace` hacks or font or margin changes that violate the style file.
   - Edit with `po_latex_copilot(action='edit')` on explicit line ranges, recompile, and re-count after every round.

5. **Retarget to another venue**: create or update the project with the new `template_name` through `po_projects` (pass `expected_updated_at`), then start a new run or port the source, and repeat step 3. Keep the previous run untouched.

6. **Camera-ready and delivery**:
   - De-anonymize, add acknowledgements and funding only from user-supplied text, and re-run `manuscript-audit`.
   - Package with `po_submission_audit(action='arxiv')` and `po_latex_workbench(action='export_overleaf')`; copy the PDF with `po_inspect_run(action='copy_pdf')`.
   - Cover letter: one page stating the contribution, fit to the venue, and required declarations, written only from the manuscript and user-supplied facts; export via `po_export_document`.

## Output

Report as **Blocking (desk-reject risk)**, **Warnings**, and **Ready**, each item with file, line, and the requirement it was checked against.
