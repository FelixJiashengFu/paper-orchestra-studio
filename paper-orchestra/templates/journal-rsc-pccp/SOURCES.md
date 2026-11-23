# Sources of the files in this template

## This template approximates the RSC layout
The Royal Society of Chemistry (RSC) does not publish its LaTeX article template on CTAN. The official template is on Overleaf ("Royal Society of Chemistry article template", with a PCCP version that links to the journal's submission system). This folder does **not** contain any RSC file. Instead, `pccp-approx.sty` was written for PaperOrchestra. It approximates the two-column A4 PCCP article layout on the standard `article` class.

What RSC says about submission formats: the PCCP author guidelines (checked 18 November 2026) say article text may be submitted as "a Word, PDF or LaTeX file (if you use LaTeX, please also include a PDF version)". They also say that use of the RSC templates is optional. RSC retypesets every accepted article in its house style. A manuscript built from this approximation is therefore an acceptable initial submission under the current guidelines, provided the authors upload the native LaTeX files together with the compiled PDF. Check the current author guidelines before submitting, because these rules can change.

Differences from the official Overleaf template:

* **Page geometry.** That template's source sets `left=1.5cm, right=1.5cm, top=1.785cm, bottom=2.0cm`. This template uses a 17.1 cm text width with two 8.3 cm columns, because those are RSC's stated single- and double-column figure widths.
* **Sans-serif font.** That template loads the Droid Sans fonts (`droidsans`), which are not in this TeX installation. This template uses Helvetica (`helvet`) for headings and captions instead.
* **Shared choices.** Both templates use Charter text (`bch`), Times math (`mathptmx`), `natbib` with `super,sort&compress,comma`, `rsc.bst` and the "Notes and references" heading.
* **Supplementary information footnote.** That template writes "Supplementary Information available: ...". This template prints the older RSC wording, "Electronic supplementary information (ESI) available: ...". RSC sets the final wording when it typesets the article.

## Third-party file copied verbatim

| File | Origin path | Version | License |
|---|---|---|---|
| `rsc.bst` | `/usr/share/texlive/texmf-dist/bibtex/bst/rsc/rsc.bst` | rsc bundle 2016/08/22 v3.1f (prints `rsc 2016/08/22 v3.1f` when BibTeX runs) | LPPL 1.3c or later |

The file was copied from the TeX Live 2023 installation on the build machine, from the Debian package `texlive-bibtex-extra` 2023.20240207-1. That package contains the CTAN package [`rsc`](https://ctan.org/pkg/rsc) by Joseph Wright, which provides BibTeX styles for RSC and Wiley journals. The file is copied so that the template compiles where the `rsc` package is not installed. It is unmodified, and `cmp` against the original reports no difference. Its header reads "Copyright (C) 2006-2009,2011,2013,2016 by Joseph Wright" and "Released under the LaTeX Project Public License v1.3c or later" (http://www.latex-project.org/lppl.txt). The LPPL allows verbatim redistribution. The file keeps its original header and license notice. The source `rsc.dtx` and the documentation are on CTAN.

`rsc.sty` from the same bundle is not needed and is not copied. The template uses natbib directly.

## Standard packages used but not copied
These packages are part of a normal TeX Live installation (base, latex-recommended, latex-extra, fonts-recommended and science collections), so they are not copied:

* `pccp-approx.sty` loads `geometry`, `graphicx`, `xcolor` (or `color`), `natbib` and `hyperref`, plus `expl3` where it is available, which is in every LaTeX since 2020.
* If the font files are installed, `pccp-approx.sty` also loads `fontenc` (T1), `mathptmx` and `helvet`, and selects Charter (`bch`). Without them it falls back to Computer Modern.
* `template.tex` loads `amsmath`, `graphicx` and `natbib`.
* `template.tex` loads `booktabs`, `mhchem` (version 4) and `siunitx` only when they exist (`\IfFileExists`). When they do not, `pccp-approx.sty` provides fallbacks for `\ce`, `\SI`, `\si`, `\num`, `\qty`, `\unit`, `\SIrange` and the booktabs rules, and `template.tex` adds last-resort `\providecommand` definitions.

## Files written for PaperOrchestra
`pccp-approx.sty`, `template.tex`, `guidelines.md`, `references.bib`, `venue.json` and this file were written for this repository. They contain no RSC code. `pccp-approx.sty` may be redistributed and modified freely with the rest of the repository.

## URLs
* PCCP author guidelines: https://www.rsc.org/publishing/publish-with-us/publish-a-journal-article/pccp
* RSC official LaTeX templates on Overleaf: https://www.overleaf.com/latex/templates/tagged/rsc-official
* PCCP Overleaf template: https://www.overleaf.com/latex/templates/royal-society-of-chemistry-template-for-preparing-your-submission-to-pccp-physical-chemistry-chemical-physics-using-overleaf/ysqhjvszpxzk
* CTAN `rsc` package: https://ctan.org/pkg/rsc
