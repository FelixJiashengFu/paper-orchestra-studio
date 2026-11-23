# Third-party files in this template

The Elsevier class and bibliography style below are copied **verbatim** from the TeX Live 2023 installation on the build machine. The Debian package was `texlive-publishers` 2023.20240207-1, which contains the CTAN package [`elsarticle`](https://ctan.org/pkg/elsarticle). They are copied so that `template.tex` compiles even where `elsarticle` is not installed. Nothing has been modified, and `cmp` against the originals reports no difference.

| File | Origin path | Version | License | SHA-256 |
|---|---|---|---|---|
| `elsarticle.cls` | `/usr/share/texlive/texmf-dist/tex/latex/elsarticle/elsarticle.cls` | elsarticle 2020/11/20, v3.3 | LPPL 1.2 or later | `23a341acd0e35c20148c836fc6e9251549aa45b059fc1750356ca2f3f11f3cb0` |
| `elsarticle-num.bst` | `/usr/share/texlive/texmf-dist/bibtex/bst/elsarticle/elsarticle-num.bst` | elsarticle-num Version 2.1 (svn Id 194, 2020-11-23) | LPPL 1.2 or later | `aaf9bce5453a0c191aa5ff4d200dc3a9b4170a6067ca723ec4f1edcd7ca14edf` |

The class needs no helper files of its own. Everything else it loads is part of a basic LaTeX installation (see below).

## Copyright and license
Both files are "Copyright 2007-2020 Elsevier Ltd" and part of the "Elsarticle Bundle". Their headers say: "It may be distributed under the conditions of the LaTeX Project Public License, either version 1.2 of this license or (at your option) any later version." The license is at http://www.latex-project.org/lppl.txt. The LPPL allows verbatim redistribution, and each file keeps its original header and license notice. The full bundle (sources `elsarticle.dtx`/`elsarticle.ins`, documentation `elsdoc.pdf`, `manifest.txt`, the other bibliography styles and Elsevier's sample templates) is on CTAN at https://ctan.org/pkg/elsarticle.

CTAN lists a newer release, elsarticle v3.5 (2026-01-09). This template keeps the TeX Live 2023 copy (v3.3), which already provides the `highlights` and `graphicalabstract` environments and the key-value `\affiliation` macro used by `template.tex`.

## Not copied (standard LaTeX packages the class or template loads)
These come from the TeX Live base, recommended, science and font collections, so they are not copied:

* the class loads `article`, `graphicx` and `natbib`;
* the class loads `expl3`, `xparse` and `etoolbox` only if they are installed;
* the `5p`/`3p`/`1p` options load `geometry` and `fleqn.clo`;
* the `times` option (in the commented two-column line) loads `txfonts`, or `times` if `txfonts` is missing;
* `template.tex` loads `amsmath` and `amssymb`;
* `template.tex` loads `booktabs`, `url`, `mhchem` (version 4) and `siunitx` only if they exist, with fallback definitions when they do not.

## Template files written for PaperOrchestra
`template.tex`, `guidelines.md`, `references.bib`, `venue.json` and this file were written for this repository; they are not third-party files.
