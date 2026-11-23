# Third-party notices

## Google PaperOrchestra (Upstream Base)

`paper-orchestra/` is forked from Google Research's **PaperOrchestra**
(`https://github.com/google-research/paper-orchestra`, commit `d9bae99`),
Copyright 2026 Google LLC, licensed under the Apache License, Version 2.0 (see `LICENSE`).

Major additions on top of upstream include multi-provider LLM routing (`utils/providers.py`),
rate-limited 8-source scholarly search (`utils/scholar_utils.py`), offline and online citation
verification (`utils/citation_audit.py`, `tools/submission_tools.py`), token & CAD cost accounting
(`utils/usage_tracker.py`, `utils/pricing.py`), journal template adaptation (`utils/venue_profile.py`),
Nature/ACS/AIP/RSC/Elsevier journal templates, and the `paper-orchestra-desktop/` Tauri desktop app.

---

## Lattice

Parts of `paper-orchestra/tools/submission_tools.py` and the templates `neurips2026`, `icml2026`,
`aaai2027`, `acl`, `ieee-conference` and `acm-sigconf` are adapted from Lattice
(`https://github.com/leo1oel/lattice`, commit `c605832`).

    Lattice
    Copyright 2026 Leonardo and the Lattice contributors

    Lattice is licensed under the Apache License, Version 2.0 (see LICENSE).

What was adapted and how it changed is detailed in `paper-orchestra/THIRD_PARTY_NOTICES.md`.
Lattice's `gpui/` (GPL-3.0) and Trellis whiteboard (non-commercial license) were **not** used.

---

## Conference and Journal Style Files

Bundled official conference style files (`neurips2026`, `icml2026`, `aaai2027`, `acl`,
`ieee-conference`, `acm-sigconf`) and journal styles (`journal-*`) include provenance, checksums,
and license notes in each template's `UPSTREAM.md` or `SOURCES.md` (including LPPL 1.2 / 1.3+).

---

## Nature-Paper-Skills

Selected scientific writing rules, Nature Portfolio venue guidance, and offline
pre-submission audit checks are adapted from `Boom5426/Nature-Paper-Skills`
(`https://github.com/Boom5426/Nature-Paper-Skills`, MIT and Apache-2.0):

| Here | From Nature-Paper-Skills | Purpose |
|---|---|---|
| `paper-orchestra/templates/journal-nature{,-methods,-biotech}/` | `nature-portfolio-playbook`, `figure-style` | *Nature*, *Nature Methods*, and *Nature Biotechnology* venue profiles and guidelines |
| `paper-orchestra/utils/venue_profile.py` (`venue_note`) | `manuscript-optimizer`, `scientific-prose-style`, `anti-defensive-writing`, `stats-reporting-audit` | Claim-driven planning, anti-AI prose restraint, and statistical completeness for journal runs |
| `paper-orchestra/tools/submission_tools.py` (`cmd_manuscript_audit`) | `scientific-prose-style/scripts/ai_tells.py`, `submission-audit/scripts/check_figure_refs.py`, `draft-marker-discipline/scripts/prose_wordcount.py`, `stats-reporting-audit`, `data-availability` | Offline `manuscript-audit` check panel in 「投稿工具 → 论文体检 (Nature)」 |
