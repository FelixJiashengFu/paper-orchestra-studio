---
name: po-submission-latex
description: >-
  Run PaperOrchestra Studio's LaTeX workbench and complete 8-tool
  pre-submission verification suite via MCP: AI LaTeX copilot edits, inline
  claim-to-citation tracing, clean `.live_preview` latexmk compilation, forward/
  inverse SyncTeX lookup, Overleaf ZIP packaging with cited-only `.bib` swap,
  Nature manuscript audit, live Crossref/arXiv/S2 citation verification,
  arXiv submission ZIP, double-blind leak check, log diagnosis, `latexdiff`
  comparison PDF, and Lean 4 proof audit.
---

# PaperOrchestra LaTeX Workbench & Pre-Submission Audit

Use `po_latex_copilot`, `po_latex_workbench`, and `po_submission_audit` on `paper-orchestra-studio_paper_orchestra`.

## Workflows

1. **AI LaTeX Editing, Citation Traceability & Abstract Polish (`po_latex_copilot`)**:
   - `edit`: Modify `.tex` files (or exact `selection_start`..`selection_end` line ranges) with optional compiler `error_log` context.
   - `trace_citations`: Map prose claims in a run's `.tex` file back to `.bib` / `citation_audit.json` evidence.
   - `polish_abstract`: Run offline 7-field diagnosis + quantitative AI-rhythm metrics (`mode='diagnose'`) or generate an award-paper sentence change table & Limitations notes (`mode='rewrite'`) without fabricating numbers.

2. **Conflict-Safe Editing, CJK XeLaTeX Compile, Bidirectional SyncTeX & Overleaf Package (`po_latex_workbench`)**:
   - `open` / `read`: Inspect editable `.tex`/`.bib` files, backup presence, active-run lock, existing `previewPdfPath` / `hasSyncTeX`, and read UTF-8 source + `mtimeMs`.
   - `save` / `restore`: Always pass `expected_mtime_ms` from `read`; first edit atomically backs up original into `.edit_backup/` for one-click `restore` (edits refused while run is active).
   - `compile`: Runs `latexmk` (`-pdfxe` automatically when CJK Unicode / `ctex` detected and `xelatex` available, else `-pdf`) + `-synctex=1` into `<run_dir>/.live_preview/`, returning structured `issues` and `engine`.
   - `sync_view` / `sync_edit`: Resolve union PDF highlight box (`end_line`, `pdf_path`) from `.tex` line selection and click-to-jump `.tex` file/line (`relFile`, `line`) from PDF coordinates via `synctex`.
   - `export_overleaf`: Builds a clean Overleaf `.zip` (main `\documentclass` only, swaps `.bib` to `references_cited.bib`, refuses overwrite unless `allow_overwrite=true`).
   - Open `po_open_workbench(tab='latex')` for Overleaf-style side-by-side editing + paginated PDF SyncTeX preview.

3. **Pre-Submission Audits & Author Report (`po_submission_audit`)**:
   - `manuscript-audit`: Nature + award-paper comment-stripped word count, syntactic AI-rhythm metrics (participle tails, antithesis density, short runs, workflow leaks, front-matter disclaimers, number budget), figure takeaway/ref-order/raster/budget audit, draft `TODO`/`XXX` scan, statistics & disclosures audit.
   - `author-report`: Generate `report.md` and `author_report.json` summarizing narrative routing, claim–evidence table, figure plan, style metrics, `[TODO]` checklist, and Limitations notes.
   - `cite-online`: Live Crossref / arXiv / Semantic Scholar verification of DOIs, authors, and years.
   - `arxiv`: Build a clean arXiv upload archive using recorded `latexmk` `.fls` dependencies.
   - `blind`: Double-blind leak detector (author macros, `\thanks`, acknowledgments, personal repo URLs, PDF metadata & Type3/unembedded fonts).
   - `diagnose`: Parse LaTeX logs with Chinese remediation hints (and optional `--find-packages` via `tlmgr`).
   - `versions` / `diff`: Inspect intermediate draft versions or compile a visual `latexdiff` PDF.
   - `lean-audit`: Run formal Lean 4 project verification cleanly.
