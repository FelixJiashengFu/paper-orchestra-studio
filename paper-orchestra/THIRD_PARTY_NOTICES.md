# Third-party notices

## Lattice

Parts of `tools/submission_tools.py` and the templates `neurips2026`, `icml2026`,
`aaai2027`, `acl`, `ieee-conference` and `acm-sigconf` are adapted from Lattice
(https://github.com/leo1oel/lattice, commit c605832).

    Lattice
    Copyright 2026 Leonardo and the Lattice contributors

    Lattice is licensed under the Apache License, Version 2.0 (see LICENSE).

Lattice's Apache-2.0 licence is the same licence text as this repository's
`LICENSE`. What was taken, and how it was changed:

| Here | From Lattice | Change |
|---|---|---|
| `tools/submission_tools.py` `cmd_arxiv`, `strip_comments`, `merge_inputs` | `src-tauri/lattice-core/src/latex/arxiv.rs`, `latex/arxiv/source.rs` | Ported from Rust to Python; works on a PaperOrchestra run folder; Chinese report |
| `tools/submission_tools.py` `cmd_blind` | `latex/submission_review.rs` | Ported; venue detection reduced to the preamble; fonts checked with PyMuPDF |
| `tools/submission_tools.py` `parse_log`, `cmd_diagnose` | `latex/build_log.rs` | Ported; Chinese hints for common errors |
| `tools/submission_tools.py` `cmd_diff` | `latex/changes.rs` (latexdiff options and retry with simpler markup) | Ported; compares any two `.tex` of PaperOrchestra runs |
| `tools/submission_tools.py` `cmd_cite_online` | idea of `citation_audit.rs` (check each entry against publication databases) | New implementation (Crossref, arXiv, Semantic Scholar) |
| `templates/{neurips2026,icml2026,aaai2027,acl,ieee-conference,acm-sigconf}/template.tex` | `src-tauri/templates/*/main.tex` | Rewritten into PaperOrchestra's section-marker format, anonymous by default |

Lattice's `gpui/` (GPL-3.0) and its Trellis whiteboard (non-commercial licence)
were **not** used.

## Conference style files

The style files in the templates above are copied unchanged from the official
author kits, exactly as Lattice ships them; each template's `UPSTREAM.md` records
the source, the checksum and the licence or credits in the file headers
(IEEEtran and acmart: LaTeX Project Public License 1.3). Do not edit them in place.

## Nature-Paper-Skills

Selected scientific writing rules, Nature Portfolio venue guidance, and offline
pre-submission audit checks are adapted from Boom5426/Nature-Paper-Skills
(https://github.com/Boom5426/Nature-Paper-Skills, MIT and Apache-2.0):

| Here | From Nature-Paper-Skills | Purpose |
|---|---|---|
| `templates/journal-nature{,-methods,-biotech}/` | `nature-portfolio-playbook`, `figure-style` | Nature, Nature Methods, Nature Biotechnology venue profiles and guidelines |
| `utils/venue_profile.py` (`venue_note`) | `manuscript-optimizer`, `scientific-prose-style`, `anti-defensive-writing`, `stats-reporting-audit` | Claim-driven planning, anti-AI prose restraint, statistical completeness for journal runs |
| `tools/submission_tools.py` (`cmd_manuscript_audit`) | `scientific-prose-style/scripts/ai_tells.py`, `submission-audit/scripts/check_figure_refs.py`, `draft-marker-discipline/scripts/prose_wordcount.py`, `stats-reporting-audit`, `data-availability` | Offline `manuscript-audit` check panel in 「投稿工具 → 论文体检 (Nature)」 |

## Best-Paper-Writing-Skill

Selected narrative routing rules, quantitative structural AI-rhythm metrics,
award-paper figure audit rules, Matplotlib print stylesheet, and author delivery
report workflow are adapted from `gulucaptain/Best-Paper-Writing-Skill`
(https://github.com/gulucaptain/Best-Paper-Writing-Skill, CC BY 4.0):

| Here | From Best-Paper-Writing-Skill | Purpose |
|---|---|---|
| `tools/submission_tools.py` (`audit_text_draft`, `_audit_ai_tells`, `_audit_figure_refs`) | `scripts/style_metrics.py`, `scripts/check_draft.py`, `scripts/check_figures.py` | Syntactic AI-rhythm checks (participle tails, antithesis density, short runs, number budgets) and conference figure checks |
| `methods/prompts/{outline,literature_review,section_writing,content_refinement}_agent.py` | `references/routing.md`, `references/claim-calibration.md`, `references/figures.md` | L1–L8 paper-type × motivation routing, claim-strength ladder, Figure 1 type selection |
| `utils/bestpaper_style.mplstyle`, `tools/figure_studio.py` | `assets/figures/bestpaper.mplstyle` | Print-ready Matplotlib rcParams preset |
| `tools/latex_copilot.py` (`polish-abstract`), `utils/author_report.py` | `SKILL.md` (Mode B change table & Step 8 `report.md`) | Field diagnosis + sentence change-table abstract polishing and `report.md` delivery summary |

Copyrighted paper excerpts in Best-Paper-Writing-Skill's `references/exemplars/` were **not** copied or included.
