# Sources of the bundled TeX files

## What this template is
npj Computational Materials (Springer Nature) does not require its house format at first
submission: manuscripts "do not need to adhere to our formatting requirements at the point of
initial submission", text and figures are submitted as a single PDF or Word file, and LaTeX
source is accepted only at the acceptance stage (compiled PDFs before then); formatting
requirements are applied after peer review
(https://www.nature.com/npjcompumats/for-authors-and-referees/submission-guidelines,
https://www.nature.com/npjcompumats/for-authors-and-referees).

`template.tex` is therefore a **submission-format approximation**, not the journal's typeset
layout. Springer Nature's official LaTeX template (`sn-jnl.cls`) is not part of TeX Live and
could not be downloaded in the build environment, so it is not used. The template uses the
standard LaTeX `article` class (12 pt, A4, single column) with a small amount of layout code in
the preamble (unnumbered sans-serif headings, Nature-style title block, "Fig. 1" legends with a
bold title sentence) and the Nature bibliography style `naturemag.bst`.

CTAN's `nature.cls` (v1.0, 2004, by P. Czoschke) was tested and rejected: it redefines the
`figure` environment so that `\includegraphics` is discarded (no image reaches the PDF) and
legends are deferred to the end of the document, and `\ref` to a figure label prints nothing.
That breaks the pipeline's figures and cross-references, so the class is not bundled.

## Bundled file

| File | Copied from | Package / version | License |
|---|---|---|---|
| `naturemag.bst` | `/usr/share/texlive/texmf-dist/bibtex/bst/nature/naturemag.bst` | CTAN package `nature` v1.0 (P. Czoschke, 2004); generated with custom-bib from `merlin.mbs` 4.05 (2002/10/21), "Style for the journal Nature" | LPPL, version 1 or any later version (stated in the file) |

The copy is verbatim and unmodified (TeX Live 2023, Debian package `texlive-publishers`).
SHA-256 (identical to the TeX Live original):

```
fed4788763dedb10a3ba7f39612cf04e9e0aacd26265b006bb86371c563d2853  naturemag.bst
```

Upstream: https://ctan.org/pkg/nature

## Taken from the system TeX installation
`article.cls`, `amsmath`, `amssymb`, `graphicx` and `fontenc` (basic LaTeX) are loaded
unconditionally. `geometry`, `mathptmx`, `helvet`, `booktabs`, `url`, `cite` (superscript
citations), `mhchem` (version 4), `siunitx` and the optional `lineno` are loaded only if installed
(`\IfFileExists`); `template.tex` defines working fallbacks (plain superscript citations,
`\hline` rules, `\ce` printing the formula as typed, simple `\SI`/`\si`/`\num`/`\qty`/`\unit`)
so it still compiles without them. These are general-purpose packages, not journal files, and
are not bundled.
