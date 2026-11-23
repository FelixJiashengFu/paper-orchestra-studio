# Sources of the bundled TeX files

These files are verbatim, unmodified copies from the TeX Live 2023 installation
(Debian packages `texlive-publishers` and `texlive-latex-recommended`) under
`/usr/share/texlive/texmf-dist`. They are bundled so that `template.tex` compiles with
`pdflatex` + `bibtex` even on a TeX installation without REVTeX. All of them may be
redistributed unchanged under the LaTeX Project Public License (LPPL).

| File | Copied from | Package / version | License |
|---|---|---|---|
| `revtex4-2.cls` | `/usr/share/texlive/texmf-dist/tex/latex/revtex/revtex4-2.cls` | REVTeX 4.2f, 2022/06/05 (American Physical Society) | LPPL 1.3c |
| `aip4-2.rtx` | `/usr/share/texlive/texmf-dist/tex/latex/revtex/aip4-2.rtx` | REVTeX 4.2f AIP substyle, 2022/06/05 (American Institute of Physics) | LPPL 1.3c or later |
| `aps10pt4-2.rtx` | `/usr/share/texlive/texmf-dist/tex/latex/revtex/aps10pt4-2.rtx` | REVTeX 4.2f, 2022/06/05; read by `aip4-2.rtx` for the 10pt `reprint` layout | LPPL 1.3c (REVTeX 4.2) |
| `aps12pt4-2.rtx` | `/usr/share/texlive/texmf-dist/tex/latex/revtex/aps12pt4-2.rtx` | REVTeX 4.2f, 2022/06/05; read by `aip4-2.rtx` for the 12pt `preprint` layout | LPPL 1.3c (REVTeX 4.2) |
| `revsymb4-2.sty` | `/usr/share/texlive/texmf-dist/tex/latex/revtex/revsymb4-2.sty` | REVTeX 4.2f, 2022/06/05; loaded by the class | LPPL 1.3c (REVTeX 4.2) |
| `aipnum4-2.bst` | `/usr/share/texlive/texmf-dist/bibtex/bst/revtex/aipnum4-2.bst` | aipnum4-2.bst 2019-01-14 (numbered AIP references); the class selects it for `aip,jcp` | LPPL version 1 or any later version |
| `textcase.sty` | `/usr/share/texlive/texmf-dist/tex/latex/textcase/textcase.sty` | textcase v1.04, 2023/07/27 (D. Carlisle); required by `revtex4-2.cls` (`\RequirePackage[overload]{textcase}`) and not part of a basic LaTeX install | LPPL |

License notes: `revtex4-2.cls` states "Licensed under the LPPL" and its embedded
`revtex4-2.dtx` metadata names LPPL 1.3c; the `aps*`/`revsymb` files carry the same
REVTeX 4.2 copyright notice (American Physical Society, 2019--2022). `aip4-2.rtx`
names LPPL 1.3c or later; `aipnum4-2.bst` names LPPL version 1 or later;
`textcase.sty` says "may be distributed under the terms of the LPPL".

Not copied, because the class does not load them: `ltxutil.sty`, `ltxfront.sty`,
`ltxgrid.sty` (REVTeX 4.2 compiles these into `revtex4-2.cls` itself, so the separate
files are never read) and `ltxdocext.sty` (documentation helper only). Verified
with `pdflatex -recorder` for both the `reprint` and the `preprint` option.

Taken from the system TeX installation (basic LaTeX or guarded): `natbib.sty`,
`url.sty`, `amsmath`, `amssymb`, `graphicx`, `dcolumn`, `bm` (LaTeX base / tools /
amsmath / graphics). `booktabs`, `mhchem` and `siunitx` are loaded only if installed
(`\IfFileExists`); `template.tex` defines simple fallbacks otherwise.

SHA-256 checksums of the copies (identical to the TeX Live originals):

```
58727044fa47fff2663615774f8f24baab978944bbc55f84f8dc08ef91f67358  revtex4-2.cls
c9be497e89fcdc0a27174351d8ee5c94a2ed9a6057acfd5e62795e6a544b00de  aip4-2.rtx
0cc5d7fab6ffd59ba5779f9ddfad990cb8a71438ea390277bafff1824bbcb8e4  aps10pt4-2.rtx
ed800427c862eee9fec9c705ca7fb1d82dc1f6abee5598257653e89d875e94bd  aps12pt4-2.rtx
7614d7446d929f49917e54c8590d7ff7408572a9d2d293d209ea718540947d3e  revsymb4-2.sty
e69b7e6656784ef660ea243d9417fce5fc72742b084dc9775943c9dcfa714598  aipnum4-2.bst
3c5b15cec4eab4bec15f1881c708650c9a8e3be5f171b1691d6253093db90494  textcase.sty
```

Upstream: REVTeX 4.2 <https://journals.aps.org/revtex/> (CTAN package `revtex`);
textcase <https://ctan.org/pkg/textcase>.
