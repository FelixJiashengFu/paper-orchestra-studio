---
name: po-literature-graph
description: >-
  Search 8 rate-limited scholarly databases (Semantic Scholar, arXiv, OpenAlex,
  Crossref, dblp, Europe PMC, PubMed, CORE), import DOIs or PDFs into the
  Zotero-style SQLite literature library, build citation knowledge graphs with
  recommended reading roadmaps and comparison matrices, and export BibTeX or
  Chinese GB/T 7714 bibliographies via MCP. Use for multi-source academic search,
  library curation, reading order planning, or citation formatting.
---

# PaperOrchestra Literature Library & Knowledge Graph

Use `po_literature_library` on `paper-orchestra-studio_paper_orchestra` to search, curate, synthesize, and export literature.

## Operations

1. **8-Source Online Search (`action='search_online'`)**:
   - Queries rate-limited scholarly APIs safely without 429 bursts. Enable `expand=true` for LLM query expansion.
2. **Import & Paginated SQLite Curation (`import_doi`, `import_pdf`, `list`, `save`, `delete`)**:
   - Resolve DOIs cleanly via Crossref/OpenAlex (`import_doi`) or extract title/authors/claims/BibTeX from local PDFs (`import_pdf`; pass `no_llm=true` for fast metadata-only extraction).
   - Browse with `action='list'` using optional `folder`, `offset` (`0`), and `limit` (`1..100`, default `25`) returning `total` and `nextOffset`.
   - Entries persist in the shared `library_items` SQLite table when `save_to_library=true`.
3. **Knowledge Graph & Reading Order (`action='build_graph'`)**:
   - Analyzes selected `item_ids` (or all library items) to classify seminal foundational works, surveys, frontier papers, and concept nodes, producing an evolutionary graph, recommended reading path, and comparative matrix.
4. **Bibliography Export (`action='export_bib'`)**:
   - Exports selected or all library items as clean `bibtex` or Chinese `gb7714` formatted references.
- **Zotero & Overleaf sync (`po_reference_sync`)**: check `status` first. `zotero_search` / `zotero_import` bring Zotero items (by `query`, `tag`, `collection_key` or `item_keys`) into the Studio library; `zotero_bibtex` merges only new citation keys into a run's `.bib` (backup in `.edit_backup/`); `library_to_ris` exports the library for Zotero import. `overleaf_link` / `overleaf_pull` fetch an Overleaf project through its git integration; `overleaf_push` is a dry run until the user agrees and you pass `confirm_push=true`. Never ask the user to paste API keys or tokens into the chat; they belong in `paper-orchestra/.env`.
