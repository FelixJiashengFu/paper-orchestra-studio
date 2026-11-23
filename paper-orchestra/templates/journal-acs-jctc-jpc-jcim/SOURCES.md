# Third-party files in this template

The ACS journal class files below are copied **verbatim** from the TeX Live 2023 installation on the build machine. The Debian package was `texlive-latex-extra` 2023.20240207-1, which contains the CTAN package [`achemso`](https://ctan.org/pkg/achemso). They are copied so that `template.tex` compiles even where `achemso` is not installed. Nothing has been modified, and `cmp` against the originals reports no difference.

| File | Origin path | Version | License |
|---|---|---|---|
| `achemso.cls` | `/usr/share/texlive/texmf-dist/tex/latex/achemso/achemso.cls` | achemso 2024-01-30 v3.13g | LPPL 1.3c or later |
| `achemso.bst` | `/usr/share/texlive/texmf-dist/bibtex/bst/achemso/achemso.bst` | achemso 2024-01-30 v3.13g | LPPL 1.3c or later |
| `natmove.sty` | `/usr/share/texlive/texmf-dist/tex/latex/achemso/natmove.sty` | natmove 2010/01/15 v1.1a (part of the achemso bundle) | LPPL 1.3c or later |
| `achemso-jctcce.cfg` | `/usr/share/texlive/texmf-dist/tex/latex/achemso/achemso-jctcce.cfg` | 2024-01-30 v3.13g (J. Chem. Theory Comput.) | LPPL 1.3c or later |
| `achemso-jpcafh.cfg` | `/usr/share/texlive/texmf-dist/tex/latex/achemso/achemso-jpcafh.cfg` | 2024-01-30 v3.13g (J. Phys. Chem. A) | LPPL 1.3c or later |
| `achemso-jpcbfk.cfg` | `/usr/share/texlive/texmf-dist/tex/latex/achemso/achemso-jpcbfk.cfg` | 2024-01-30 v3.13g (J. Phys. Chem. B) | LPPL 1.3c or later |
| `achemso-jpccck.cfg` | `/usr/share/texlive/texmf-dist/tex/latex/achemso/achemso-jpccck.cfg` | 2024-01-30 v3.13g (J. Phys. Chem. C) | LPPL 1.3c or later |
| `achemso-jcisd8.cfg` | `/usr/share/texlive/texmf-dist/tex/latex/achemso/achemso-jcisd8.cfg` | 2024-01-30 v3.13g (J. Chem. Inf. Model.) | LPPL 1.3c or later |
| `achemso-jacsat.cfg` | `/usr/share/texlive/texmf-dist/tex/latex/achemso/achemso-jacsat.cfg` | 2024-01-30 v3.13g (J. Am. Chem. Soc.; the class falls back to it for an unknown `journal=` value) | LPPL 1.3c or later |

## Copyright and license
The achemso bundle was originally developed by Mats Dahlgren (Copyright (C) 1996–1998). It is Copyright (C) 2008–2022 by Joseph Wright, who maintains it. Part of the bundle is derived from `cite.sty`, Copyright (C) 1989–2009 by Donald Arseneau ("These macros may be freely transmitted, reproduced, or modified provided that this notice is left intact").

The files may be distributed and/or modified under the conditions of the LaTeX Project Public License (LPPL), version 1.3c or (at your option) any later version: http://www.latex-project.org/lppl.txt. The LPPL allows verbatim redistribution. Each file keeps its original header and license notice. The sources (`achemso.dtx`, `achemso.ins`) and the documentation are part of the same distribution, on CTAN at https://ctan.org/pkg/achemso.

## Not copied (standard LaTeX packages the class or template loads)
These packages come from the TeX Live base, recommended and science collections, so they are not copied:

* the class itself loads `xkeyval`, `geometry`, `caption`, `float`, `graphicx`, `setspace`, `url` and `natbib`;
* the class loads `mciteplus` only if it is installed;
* `template.tex` loads `amsmath`;
* `template.tex` loads `booktabs`, `mhchem` and `siunitx` only if they exist, with fallback definitions when they do not.

## Template files written for PaperOrchestra
`template.tex`, `guidelines.md`, `references.bib`, `venue.json` and this file were written for this repository; they are not third-party files.
