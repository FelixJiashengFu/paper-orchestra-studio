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

1. **AI LaTeX Editing & Traceability (`po_latex_copilot`)**:
   - `edit`: Modify `.tex` files (or exact `selection_start`..`selection_end` line ranges) with optional compiler `error_log` context.
   - `trace_citations`: Map prose claims in a run's `.tex` file back to `.bib` / `citation_audit.json` evidence.

2. **Conflict-Safe Editing, CJK XeLaTeX Compile, SyncTeX & Overleaf Package (`po_latex_workbench`)**:
   - `open` / `read`: Inspect editable `.tex`/`.bib` files, backup presence, active-run lock, and read UTF-8 source + `mtimeMs`.
   - `save` / `restore`: Always pass `expected_mtime_ms` from `read`; first edit atomically backs up original into `.edit_backup/` for one-click `restore` (edits refused while run is active).
   - `compile`: Runs `latexmk` (`-pdfxe` automatically when CJK Unicode / `ctex` detected and `xelatex` available, else `-pdf`) + `-synctex=1` into `<run_dir>/.live_preview/`, returning structured `issues` and `engine`.
   - `sync_view` / `sync_edit`: Resolve exact PDF page/coordinates from `.tex` line/column and vice versa via `synctex`.
   - `export_overleaf`: Builds a clean Overleaf `.zip` (main `\documentclass` only, swaps `.bib` to `references_cited.bib`, refuses overwrite unless `allow_overwrite=true`).
   - Optionally open `po_open_workbench(tab='latex')` for interactive editing in Antigravity.

3. **8 Pre-Submission Checks (`po_submission_audit`)**:
   - `manuscript-audit`: Nature-style comment-stripped word count, AI tell & punctuation rhythm check, figure-ref/caption check, draft `TODO`/`XXX` marker scan, statistics & disclosures audit.
   - `cite-online`: Live Crossref / arXiv / Semantic Scholar verification of DOIs, authors, and years.
   - `arxiv`: Build a clean arXiv upload archive using recorded `latexmk` `.fls` dependencies.
   - `blind`: Double-blind leak detector (author macros, `\thanks`, acknowledgments, personal repo URLs, PDF metadata & Type3/unembedded fonts).
   - `diagnose`: Parse LaTeX logs with Chinese remediation hints (and optional `--find-packages` via `tlmgr`).
   - `versions` / `diff`: Inspect intermediate draft versions or compile a visual `latexdiff` PDF.
   - `lean-audit`: Run formal Lean 4 project verification cleanly.
