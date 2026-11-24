---
name: po-reviewer-response
description: >
  This skill should be used when the user asks to "write a rebuttal",
  "respond to reviewers", "triage review comments", "draft a response letter",
  "plan a revision", or "prepare a revised manuscript with tracked changes"
  (审稿意见分拣、逐条回复审稿人、Rebuttal、修回信、修订稿与改动对比).
---

# PaperOrchestra Reviewer Response & Revision

Turn raw reviews (real ones pasted by the user, or the simulated ones in a run's `peer_reviews/`) into a response plan, a point-by-point response letter, and a revised manuscript with a `latexdiff` PDF, using `paper-orchestra-studio` MCP tools.

## Instructions

1. **Collect the reviews and the manuscript**:
   - Real reviews: take the text or file the user provides verbatim. Simulated reviews: `po_inspect_run(action='summary')`, then `po_inspect_run(action='read_artifact', relative_path='peer_reviews/...')`.
   - Open the source with `po_latex_workbench(action='open')` and `read`; keep the returned `mtimeMs` for later saves.

2. **Triage into a response matrix** (one row per atomic comment, never merged). Start from `po_revision_toolkit(action='review_matrix', reviews_text=...|reviews_file=...|run_dir=...)`, which splits the reviews deterministically into IDs with a first-pass type and severity, then correct and complete each row:
   - Columns: `ID` (R1.1, R1.2, ...), `Reviewer quote` (verbatim, short), `Type` (factual error / missing experiment / missing citation / clarity / scope / presentation), `Severity` (blocking / major / minor), `Action` (accept and change / partially accept / respectfully decline), `Evidence needed`, `Manuscript location`.
   - Mark every row whose answer needs a result the user has not supplied as `【不可核实】 needs author input`. Stop and ask for those before drafting the letter.

3. **Ground each answer before writing it**:
   - Claims about the literature: `po_latex_copilot(action='trace_citations')` and `po_literature_library` (`search_online`, `import_doi`, `import_pdf`); verify new references with `po_submission_audit(action='cite-online')`.
   - Claims about the data: `po_data_analysis` on the user's own files. New numbers enter the letter only when they come from a tool result or from the user.

4. **Revise the manuscript conflict-safely**:
   - Record the pre-revision source path first (the `.edit_backup/` copy that `save` preserves is the "old" version).
   - Apply edits with `po_latex_copilot(action='edit')` or `po_latex_workbench(action='save', expected_mtime_ms=...)`, then `compile` and resolve reported issues.
   - Produce the tracked-changes PDF with `po_submission_audit(action='diff', old_tex=..., new_tex=..., only_changed_pages=true)`.

5. **Write the response letter** (Markdown, then export):
   - Opening paragraph: thanks, and a three-to-five line summary of the main changes.
   - Per comment: the reviewer quote, **Response** (direct answer first), **Changes** (section, page, line of the revision, quoting the new text). Declines state the reason and what was clarified instead.
   - Respect venue limits when the user names them (character or page caps for conference rebuttals).
   - Export with `po_export_document(action='export', format='docx'|'pdf'|'html')`.

## Non-negotiable rules

- Never invent experiments, numbers, citations, or reviewer statements. Unknowns stay tagged `【不可核实】`.
- Every "we changed X" sentence must point at text that exists in the revised source after compile.
- Keep the tone factual and courteous; do not argue with a reviewer's motives.
