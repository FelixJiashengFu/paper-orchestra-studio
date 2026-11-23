"""Offline citation audit (utils/citation_audit.py): parsing edge cases and statuses."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.citation_audit import (  # noqa: E402
    audit_run,
    bibliography_names,
    build_audit,
    parse_bib,
    parse_citations,
    strip_comments,
)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class CitationParsingTests(unittest.TestCase):
    def test_commands_and_arguments(self):
        tex = r"""
        As shown by \cite{a} and \citep[see][p.~3]{b, c}, and \citet*{d}.
        \citeauthor{e} (\citeyear{f}) \nocite{g} \Citep{h} \citealp{i} \citeyearpar{j}
        \citep{k,
               l} \cite [p. 2] {m} \citep[]{n} \parencite{o} \textcite[12]{p} \autocite{q}
        \citep{a, b} \cite{} \cite{ , r ,}
        \citepalias{s} \citetalias{t}
        """
        keys, star = parse_citations(tex)
        self.assertEqual(keys, list("abcdefghijklmnopqrst"))  # first-seen order, no duplicates
        self.assertFalse(star)

    def test_revtex_onlinecite_counts_as_a_citation(self):
        # The AIP (JCP) template's guidelines recommend "Ref.~\onlinecite{key}".
        keys, _ = parse_citations(r"as in Ref.~\onlinecite{grimme2010} and Refs.~\onlinecite{a,b}")
        self.assertEqual(keys, ["grimme2010", "a", "b"])

    def test_not_citations(self):
        tex = r"""
        \citestyle{nature} \citetext{see also} \bibliographystyle{plain}
        \citeindextrue \mycite{zz} \excite{zz}
        """
        self.assertEqual(parse_citations(tex), ([], False))

    def test_comments(self):
        tex = "% \\cite{hidden}\n50\\% of \\cite{kept} % \\cite{hidden2}\nline\\\\% \\cite{hidden3}\n"
        self.assertEqual(parse_citations(tex)[0], ["kept"])
        self.assertIn("50\\% of", strip_comments(tex))

    def test_urls_macros_and_multicite(self):
        tex = (r"\url{http://x.org/~a%20b} \cite{r} \href{http://h.org/a%2Fb}{link} \citep{s}" "\n"
               r"\newcommand{\mycite}[1]{\citep{#1}} \cites{j}{k} \parencites(pre)(post)[p.~1]{l}[x]{m}" "\n"
               r"\citep[{see [1]}]{e} % \cite{hidden}")
        self.assertEqual(parse_citations(tex), (["r", "s", "j", "k", "l", "m", "e"], False))

    def test_nocite_star(self):
        keys, star = parse_citations(r"\nocite{*} \cite{x}")
        self.assertEqual(keys, ["x"])
        self.assertTrue(star)

    def test_bibliography_names(self):
        tex = "% \\bibliography{old}\n\\bibliography{references, extra}\n\\addbibresource{more.bib}"
        self.assertEqual(bibliography_names(tex), ["references", "extra", "more.bib"])

    def test_parse_bib(self):
        bib = r"""
        @string{nips = "NeurIPS"}
        @comment{ @article{fake, title={no}} }
        @article{Key:One-2020,
          title = {A {Nested} Title with \emph{Emph}},
          author={A and B},
          year = 2020,
          doi = {10.1000/x_y},
        }
        @inproceedings( paren2019 , title = "Quoted {Braces} Title", year = "2019" )
        @misc{concat, title = "Part " # {Two}, url = {https://x.org/a_b}}
        @book{nokey}
        """
        entries = parse_bib(bib)
        self.assertEqual(list(entries), ["Key:One-2020", "paren2019", "concat"])
        self.assertEqual(entries["Key:One-2020"]["title"], r"A {Nested} Title with \emph{Emph}")
        self.assertEqual(entries["Key:One-2020"]["year"], "2020")
        self.assertEqual(entries["Key:One-2020"]["doi"], "10.1000/x_y")
        self.assertEqual(entries["paren2019"]["title"], "Quoted {Braces} Title")
        self.assertEqual(entries["concat"]["title"], "Part Two")
        self.assertEqual(entries["concat"]["ENTRYTYPE"], "misc")

    def test_generated_bib_round_trip(self):
        bib = ("@article{He2016DeepResidual,\n  title={Deep Residual Learning},\n  author={Kaiming He},\n"
               "  journal={Proc. CVPR},\n  year={2016},\n  volume={1},\n  pages={770--778}\n}\n\n"
               "@inproceedings{Vaswani2017AttentionAll,\n  title={Attention Is All You Need},\n"
               "  author={Ashish Vaswani and Noam Shazeer},\n  booktitle={NeurIPS},\n  year={2017}\n}")
        entries = parse_bib(bib)
        self.assertEqual(list(entries), ["He2016DeepResidual", "Vaswani2017AttentionAll"])
        self.assertEqual(entries["He2016DeepResidual"]["pages"], "770--778")


class AuditStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def make_run(self, tex_rel="content_refinement_workdir/final_refined_paper.tex"):
        tex = r"""\documentclass{article}
        \begin{document}
        Prior work \citep{withDoi, urlOnly} and \citet{bare}. Missing \cite{ghost}.
        Typed by hand \cite{handDoi}. % \cite{commented}
        \bibliography{references}
        \end{document}"""
        write(os.path.join(self.run, tex_rel), tex)
        bib = "\n\n".join([
            "@article{withDoi,\n  title={With DOI},\n  author={A},\n  journal={J},\n  year={2021}\n}",
            "@inproceedings{urlOnly,\n  title={URL Only},\n  author={B},\n  booktitle={C},\n  year={2020}\n}",
            "@inproceedings{bare,\n  title={Bare},\n  author={C},\n  booktitle={C},\n  year={2019}\n}",
            "@inproceedings{uncitedOne,\n  title={Never Cited},\n  author={D},\n  booktitle={C},\n  year={2018}\n}",
            "@article{handDoi,\n  title={Hand {Made}},\n  author={E},\n  journal={J},\n  year={2017},\n"
            "  doi={https://doi.org/10.9/hand}\n}",
        ])
        write(os.path.join(self.run, os.path.dirname(tex_rel), "references.bib"), bib)
        write(os.path.join(self.run, "literature_agent_output", "references_meta.json"), json.dumps({
            "withDoi": {"title": "With DOI (scholar title)", "year": 2021, "doi": "10.1/with_doi",
                        "url": None, "source": "crossref"},
            "urlOnly": {"title": "URL Only", "year": 2020, "doi": None, "url": "https://arxiv.org/abs/1",
                        "source": "arxiv"},
            "bare": {"title": "Bare", "year": None, "doi": None, "url": None, "source": "dblp"},
            "uncitedOne": {"title": "Never Cited", "year": 2018, "doi": None, "url": None, "source": None},
        }))

    def test_statuses_and_summary(self):
        self.make_run()
        audit = audit_run(self.run)
        self.assertEqual(audit["tex_file"], "content_refinement_workdir/final_refined_paper.tex")
        self.assertEqual(audit["bib_file"], "content_refinement_workdir/references.bib")
        self.assertEqual(audit["missing_in_bib"], ["ghost"])
        self.assertEqual(audit["uncited"], ["uncitedOne"])
        self.assertEqual(audit["summary"], {"cited": 5, "bib_entries": 5, "in_bibliography": 4,
                                            "missing_in_bib": 1, "uncited": 1, "with_doi": 2, "verified": 3})
        self.assertEqual(audit["cited_bib_file"], "references_cited.bib")
        by_key = {e["key"]: e for e in audit["entries"]}
        self.assertEqual([e["key"] for e in audit["entries"]],
                         ["withDoi", "urlOnly", "bare", "uncitedOne", "handDoi"])
        self.assertEqual(by_key["withDoi"], {"key": "withDoi", "title": "With DOI (scholar title)", "year": 2021,
                                             "doi": "10.1/with_doi", "url": None, "source": "crossref",
                                             "cited": True, "status": "verified"})
        self.assertEqual(by_key["urlOnly"]["status"], "verified")  # resolvable source record
        self.assertEqual(by_key["bare"]["status"], "metadata_only")
        self.assertEqual(by_key["bare"]["year"], 2019)  # sidecar year is null: the .bib year is used
        self.assertEqual(by_key["uncitedOne"]["status"], "metadata_only")
        self.assertFalse(by_key["uncitedOne"]["cited"])
        self.assertEqual(by_key["handDoi"]["status"], "verified")  # DOI in the .bib, not from the search
        self.assertEqual(by_key["handDoi"]["doi"], "10.9/hand")
        self.assertEqual(by_key["handDoi"]["title"], "Hand Made")
        self.assertIsNone(by_key["handDoi"]["source"])

        with open(os.path.join(self.run, "citation_audit.json"), encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["summary"], audit["summary"])
        self.assertEqual(set(on_disk), {"generated_at", "tex_file", "bib_file", "cited_bib_file", "summary",
                                        "missing_in_bib", "uncited", "entries"})
        with open(os.path.join(self.run, "citation_audit.md"), encoding="utf-8") as f:
            md = f.read()
        self.assertIn("# 引用核查报告", md)
        self.assertIn("`ghost`", md)
        self.assertIn("https://doi.org/10.1/with_doi", md)
        self.assertIn("| 仅元数据 | `bare` |", md)

    def test_unverified_without_sidecar(self):
        self.make_run()
        os.remove(os.path.join(self.run, "literature_agent_output", "references_meta.json"))
        audit = build_audit(self.run)
        status = {e["key"]: e["status"] for e in audit["entries"]}
        self.assertEqual(status, {"withDoi": "unverified", "urlOnly": "unverified", "bare": "unverified",
                                  "uncitedOne": "unverified", "handDoi": "verified"})

    def test_falls_back_to_template(self):
        self.make_run(tex_rel="latex_writeup/template.tex")
        audit = build_audit(self.run)
        self.assertEqual(audit["tex_file"], "latex_writeup/template.tex")
        self.assertEqual(audit["bib_file"], "latex_writeup/references.bib")
        self.assertEqual(audit["summary"]["bib_entries"], 5)

    def test_final_tex_preferred_over_drafts(self):
        self.make_run()
        write(os.path.join(self.run, "latex_writeup", "template.tex"), r"\cite{onlyInTemplate}")
        self.assertEqual(build_audit(self.run)["tex_file"], "content_refinement_workdir/final_refined_paper.tex")

    def test_nocite_star_counts_every_entry(self):
        write(os.path.join(self.run, "latex_writeup", "template.tex"), r"\nocite{*}\cite{x}\bibliography{refs}")
        write(os.path.join(self.run, "latex_writeup", "refs.bib"), "@misc{x, title={X}}\n@misc{y, title={Y}}")
        audit = build_audit(self.run)
        self.assertEqual(audit["uncited"], [])
        self.assertEqual(audit["summary"]["cited"], 2)
        self.assertEqual(audit["bib_file"], "latex_writeup/refs.bib")

    def test_empty_run_folder(self):
        audit = audit_run(self.run)
        self.assertEqual(audit["tex_file"], "")
        self.assertEqual(audit["bib_file"], "")
        self.assertEqual(audit["summary"], {"cited": 0, "bib_entries": 0, "in_bibliography": 0,
                                            "missing_in_bib": 0, "uncited": 0, "with_doi": 0, "verified": 0})
        self.assertTrue(os.path.isfile(os.path.join(self.run, "citation_audit.md")))
        self.assertEqual(audit["cited_bib_file"], "")  # no .bib: nothing to reduce
        self.assertFalse(os.path.exists(os.path.join(self.run, "references_cited.bib")))

    def test_cited_bib_keeps_only_cited_entries_verbatim(self):
        self.make_run()
        bib_path = os.path.join(self.run, "content_refinement_workdir", "references.bib")
        with open(bib_path, encoding="utf-8") as f:
            original = f.read()
        with open(bib_path, "w", encoding="utf-8") as f:
            f.write("@string{jn = {Journal of Tests}}\n% a comment between entries\n" + original)
        with open(bib_path, encoding="utf-8") as f:
            library = f.read()
        audit_run(self.run)
        with open(os.path.join(self.run, "references_cited.bib"), encoding="utf-8") as f:
            cited = f.read()
        with open(bib_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), library)  # the library itself is never changed
        self.assertTrue(cited.startswith("% references_cited.bib"))
        self.assertIn("@string{jn = {Journal of Tests}}", cited)  # macros the entries may use
        self.assertNotIn("uncitedOne", cited)
        self.assertNotIn("a comment between entries", cited)
        body = cited.split("\n\n", 1)[1]
        from utils.citation_audit import parse_bib
        self.assertEqual(list(parse_bib(body)), ["withDoi", "urlOnly", "bare", "handDoi"])  # file order
        self.assertIn("@article{handDoi,\n  title={Hand {Made}},", cited)  # verbatim, braces and all

    def test_cited_bib_with_nocite_star_is_the_whole_library(self):
        write(os.path.join(self.run, "latex_writeup", "template.tex"), r"\nocite{*}\bibliography{refs}")
        write(os.path.join(self.run, "latex_writeup", "refs.bib"), "@misc{x, title={X}}\n@misc{y, title={Y}}")
        audit = audit_run(self.run)
        self.assertEqual(audit["summary"]["in_bibliography"], 2)
        with open(os.path.join(self.run, "references_cited.bib"), encoding="utf-8") as f:
            self.assertEqual(sorted(parse_bib_keys(f.read())), ["x", "y"])

    def test_cited_bib_keeps_crossref_parents(self):
        from utils.citation_audit import cited_bib_text
        bib = ("@inproceedings{child, title={C}, crossref={Proc24}}\n"
               "@proceedings{proc24, title={Proceedings}}\n@misc{other, title={O}}")
        text, n = cited_bib_text([("refs.bib", bib)], {"child"})
        self.assertEqual(parse_bib_keys(text), ["child", "proc24"])
        self.assertEqual(n, 2)

    def test_keys_match_case_insensitively_like_bibtex(self):
        # BibTeX resolves \cite{Smith2020} to @article{smith2020,...}: such an
        # entry is cited, in the paper's reference list, and in the cited copy.
        write(os.path.join(self.run, "content_refinement_workdir", "final_refined_paper.tex"),
              "\\documentclass{article}\\begin{document}\\cite{Smith2020} \\citep{other2021,SMITH2020}"
              "\\bibliography{references}\\end{document}")
        write(os.path.join(self.run, "content_refinement_workdir", "references.bib"),
              "@article{smith2020, title={S}}\n@article{Other2021, title={O}}\n@misc{unused, title={U}}\n")
        audit = audit_run(self.run)
        self.assertEqual({e["key"]: e["cited"] for e in audit["entries"]},
                         {"smith2020": True, "Other2021": True, "unused": False})
        self.assertEqual(audit["missing_in_bib"], [])
        self.assertEqual(audit["summary"]["cited"], 2)  # Smith2020 and SMITH2020 are one entry
        self.assertEqual(audit["summary"]["in_bibliography"], 2)
        with open(os.path.join(self.run, "references_cited.bib"), encoding="utf-8") as f:
            self.assertEqual(parse_bib_keys(f.read()), ["smith2020", "Other2021"])

    def test_cited_bib_matches_keys_and_parents_case_insensitively(self):
        from utils.citation_audit import cited_bib_text
        bib = "@inproceedings{Child, crossref={PROC}}\n@proceedings{proc, title={P}}\n@misc{x, title={X}}"
        text, n = cited_bib_text([("refs.bib", bib)], {"child"})
        self.assertEqual(parse_bib_keys(text), ["Child", "proc"])
        self.assertEqual(n, 2)

    def test_markdown_lists_cited_entries_first_and_names_the_library(self):
        self.make_run()
        audit_run(self.run)
        with open(os.path.join(self.run, "citation_audit.md"), encoding="utf-8") as f:
            md = f.read()
        table = md.split("## 文献库全部条目", 1)[1]
        self.assertLess(table.index("`handDoi`"), table.index("`uncitedOne`"))  # handDoi comes last in the file
        self.assertIn("不等于论文末尾的参考文献列表", md)
        self.assertIn("| 进入参考文献列表 | 4 |", md)
        self.assertIn("`references_cited.bib`（4 条）", md)

    def test_stale_cited_bib_is_removed_when_the_bib_is_gone(self):
        write(os.path.join(self.run, "references_cited.bib"), "@misc{old, title={Old}}")
        audit_run(self.run)
        self.assertFalse(os.path.exists(os.path.join(self.run, "references_cited.bib")))

    def test_bad_sidecar_is_ignored(self):
        self.make_run()
        write(os.path.join(self.run, "literature_agent_output", "references_meta.json"), "{not json")
        audit = build_audit(self.run)
        self.assertEqual(audit["summary"]["verified"], 1)  # only the hand-made DOI entry


def parse_bib_keys(text):
    from utils.citation_audit import parse_bib
    return list(parse_bib(text))


if __name__ == "__main__":
    unittest.main()
