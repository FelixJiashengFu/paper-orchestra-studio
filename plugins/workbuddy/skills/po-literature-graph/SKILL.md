---
name: po-literature-graph
description: >
  This skill should be used when the user asks to "search academic papers across
  multiple databases", "import a DOI or paper PDF into the library", "build a
  citation knowledge graph and reading roadmap", or "export BibTeX or GB/T 7714
  references" (8源学术检索、导入文献库、生成知识图谱与阅读顺序、导出引文).
---

# PaperOrchestra Literature Library & Knowledge Graph

Search, curate, synthesize, and format scholarly references using `po_literature_library`.

## Instructions

1. **Multi-Source Search (`action='search_online'`)**: Query Semantic Scholar, arXiv, OpenAlex, Crossref, dblp, Europe PMC, PubMed, and CORE under shared rate-limit protection (`expand=true` for LLM query expansion).
2. **Import & Paginated Curation (`import_doi`, `import_pdf`, `list`, `save`, `delete`)**: Resolve metadata from DOIs or local PDFs, store records in SQLite `library_items` (`save_to_library=true`), and browse paginated slices via `list` (`folder`, `offset`, `limit` `1..100`, returning `total` & `nextOffset`).
3. **Knowledge Graph (`action='build_graph'`)**: Categorize foundational classics, survey papers, frontier advances, and key concepts, emitting a recommended reading path and comparison matrix.
4. **Bibliography Export (`action='export_bib'`)**: Export entries as clean `bibtex` or Chinese `gb7714`.
- **Zotero & Overleaf sync (`po_reference_sync`)**: check `status` first. `zotero_search` / `zotero_import` bring Zotero items (by `query`, `tag`, `collection_key` or `item_keys`) into the Studio library; `zotero_bibtex` merges only new citation keys into a run's `.bib` (backup in `.edit_backup/`); `library_to_ris` exports the library for Zotero import. `overleaf_link` / `overleaf_pull` fetch an Overleaf project through its git integration; `overleaf_push` is a dry run until the user agrees and you pass `confirm_push=true`. Never ask the user to paste API keys or tokens into the chat; they belong in `paper-orchestra/.env`.
