---
name: po-submission-latex
description: >
  This skill should be used when the user asks to "edit a LaTeX file",
  "compile paper with SyncTeX", "export an Overleaf zip package",
  "run Nature manuscript audit", "verify citations online", "prepare an arXiv
  submission package", "check double-blind anonymity", "diagnose LaTeX log
  errors", "generate a latexdiff PDF", or "run Lean 4 proof audit"
  (LaTeX 协同编辑、SyncTeX 编译定位、Overleaf 导出、Nature 论文体检、双盲检查、Lean 4 核查).
---

# PaperOrchestra LaTeX Workbench & Pre-Submission Audit

Operate PaperOrchestra's LaTeX editing, compilation, SyncTeX, Overleaf export, and 8-tool submission audit suite using `po_latex_copilot`, `po_latex_workbench`, and `po_submission_audit`.

## Instructions

- **LaTeX Copilot (`po_latex_copilot`)**: Perform targeted edits (`action='edit'`, supporting `selection_start`/`selection_end` and compiler `error_log`), map claims to citations (`action='trace_citations'`), and run award-paper abstract diagnosis or sentence change-table polishing (`action='polish_abstract'`, `mode='diagnose'|'rewrite'`).
- **Workbench (`po_latex_workbench`)**: Open (`open` returning `previewPdfPath`/`hasSyncTeX`), read source + `mtimeMs` (`read`), apply conflict-safe edits with `.edit_backup/` preservation (`save` with `expected_mtime_ms`) or roll back (`restore`), run clean `compile` into `.live_preview/` (auto-selecting `-pdfxe` for CJK/`ctex` sources when `xelatex` is present, else `-pdf`), lookup via bidirectional SyncTeX (`sync_view` with `end_line`/`pdf_path` highlight boxes and `sync_edit` click-to-jump), and package non-overwriting Overleaf archives (`export_overleaf`). Open `po_open_workbench(tab='latex')` for Overleaf-style side-by-side split view + interactive SyncTeX PDF preview.
- **Pre-Submission Checks & Author Report (`po_submission_audit`)**: Run `manuscript-audit` (Nature + award-paper syntactic AI-rhythm metrics, figure takeaway/ref-order/raster/budget audit), `author-report` (generate `report.md` & `author_report.json`), `cite-online` (Crossref/arXiv/S2), `arxiv` (fls-based zip), `blind` (leak + font embedding check), `diagnose` (log analysis + `tlmgr` lookup), `versions`, `diff` (`latexdiff` PDF), and `lean-audit` (`lean`/`lake` formal verification).
- **Deterministic Revision Toolkit (`po_revision_toolkit`)**: `review_matrix` (reviewer comments to an ID'd response matrix), `preflight` (limits, placeholders, undefined references, anonymity leaks, required statements), `retraction_check` (Crossref retraction notices per DOI), `reporting_checklist` (CONSORT, PRISMA, STROBE, ARRIVE, ML reproducibility). No LLM call; failed lookups are reported as `unverified`.
- **Zotero & Overleaf sync (`po_reference_sync`)**: check `status` first. `zotero_search` / `zotero_import` bring Zotero items (by `query`, `tag`, `collection_key` or `item_keys`) into the Studio library; `zotero_bibtex` merges only new citation keys into a run's `.bib` (backup in `.edit_backup/`); `library_to_ris` exports the library for Zotero import. `overleaf_link` / `overleaf_pull` fetch an Overleaf project through its git integration; `overleaf_push` is a dry run until the user agrees and you pass `confirm_push=true`. Never ask the user to paste API keys or tokens into the chat; they belong in `paper-orchestra/.env`.
