# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Unit tests for the offline parts of tools/submission_tools.py."""

import argparse
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import submission_tools as st  # noqa: E402


class StripCommentsTest(unittest.TestCase):
    def test_whole_line_trailing_and_escaped(self):
        src = "% note\n\\foo{%\nbar % TODO fix\n50\\% done\n\\url{a%b} x\n"
        out, comments = st.strip_comments(src)
        self.assertEqual(out, "\\foo{%\nbar %\n50\\% done\n\\url{a%b} x\n")
        self.assertEqual([c["text"] for c in comments], ["note", "TODO fix"])
        self.assertTrue(comments[1]["todo"])

    def test_verbatim_and_comment_environment(self):
        src = "\\begin{verbatim}\n% kept\n\\end{verbatim}\n\\begin{comment}\nsecret\n\\end{comment}\nx\n"
        out, comments = st.strip_comments(src)
        self.assertIn("% kept", out)
        self.assertNotIn("secret", out)
        self.assertTrue(out.endswith("x\n"))


class VenueTest(unittest.TestCase):
    def test_review_and_camera_ready(self):
        self.assertTrue(st.detect_venue("\\usepackage{neurips_2026}")[1])
        self.assertFalse(st.detect_venue("\\usepackage[final]{neurips_2026}")[1])
        self.assertTrue(st.detect_venue("\\usepackage[review]{acl}")[1])
        self.assertTrue(st.detect_venue("\\documentclass[sigconf,review,anonymous]{acmart}")[1])
        self.assertEqual(st.detect_venue("\\documentclass{article}")[0], None)


class LogTest(unittest.TestCase):
    def test_errors_and_undefined(self):
        log = ("./main.tex:12: Undefined control sequence.\n"
               "LaTeX Warning: Citation `foo' on page 1 undefined on input line 3.\n"
               "! LaTeX Error: File `nicefrac.sty' not found.\n")
        p = st.parse_log(log, "main.tex")
        self.assertEqual(p["errors"][0]["line"], 12)
        self.assertIn("没定义", p["errors"][0]["hint"])
        self.assertEqual(p["undefinedCitations"], ["foo"])
        self.assertEqual(p["missingFiles"], ["nicefrac.sty"])


class LatexEngineTest(unittest.TestCase):
    def _build_mode(self, source: str, xelatex_available: bool) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "main.tex"), "w", encoding="utf-8") as f:
                f.write(source)

            def fake_which(tool):
                if tool == "latexmk":
                    return "/bin/latexmk"
                return "/bin/xelatex" if tool == "xelatex" and xelatex_available else None

            with patch.object(st, "which", side_effect=fake_which), patch.object(
                st, "run", return_value=(0, "", False)
            ) as run:
                st.latexmk_build(tmp, "main.tex")
            return run.call_args.args[0][1]

    def test_chinese_source_uses_xelatex_when_available(self):
        source = "\\documentclass{article}\\begin{document}中文\\end{document}"
        self.assertEqual(self._build_mode(source, True), "-pdfxe")
        self.assertEqual(self._build_mode(source, False), "-pdf")

    def test_english_source_keeps_pdflatex(self):
        source = "\\documentclass{article}\\begin{document}English\\end{document}"
        self.assertEqual(self._build_mode(source, True), "-pdf")


class SurnameTest(unittest.TestCase):
    def test_fuzzy_surname(self):
        self.assertTrue(st._surname_matches("Barroso-Luque", ["Luis Barros-Luque"]))
        self.assertFalse(st._surname_matches("Smith", ["Ada Lovelace", "Alan Turing"]))


class ManuscriptAuditTest(unittest.TestCase):
    def _write_run(self, tex_body: str, venue: dict | None = None) -> tempfile.TemporaryDirectory:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = os.path.join(tmp.name, "content_refinement_workdir")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "final_refined_paper.tex"), "w", encoding="utf-8") as f:
            f.write(tex_body)
        if venue is not None:
            with open(os.path.join(src, "venue.json"), "w", encoding="utf-8") as f:
                json.dump(venue, f)
        return tmp

    def test_flags_ai_tells_placeholders_figures_and_stats(self):
        tex = r"""\documentclass{article}
\begin{document}
\begin{abstract}
We delve into an intricate realm --- and show improvements; however, it works.
\end{abstract}
\section*{Introduction}
It is worth noting that we underscore a seamless paradigm --- with two dashes --- in one paragraph; also two; semicolons.
\textbf{This long bold sentence in body text should be flagged by the audit.}
See Fig.~\ref{fig:missing} and Fig. 3.
\section*{Results}
Results here.
Our method achieved p < 0.05 on the benchmark.
\begin{figure}[htbp]
\caption{Plain caption without bold opening.}
\label{fig:unused}
\end{figure}
\section*{Methods}
TODO complete methods.
\section*{Data availability}
To be completed by the authors.
\bibliographystyle{naturemag}
\end{document}
"""
        tmp = self._write_run(tex, {"venue_name": "Nature", "short_name": "Nature", "abstract_limit": "200 words"})
        report = st.cmd_manuscript_audit(argparse.Namespace(run_dir=tmp.name))
        self.assertEqual(report["kind"], "manuscript-audit")
        by_id = {c["id"]: c for c in report["checks"]}
        self.assertEqual(by_id["ai_tells"]["state"], "warn")
        joined_ai = "\n".join(by_id["ai_tells"]["items"])
        self.assertIn("delve", joined_ai)
        self.assertIn("摘要含", joined_ai)
        self.assertIn("正文长粗体句", joined_ai)
        self.assertEqual(by_id["figure_refs"]["state"], "warn")
        joined_fig = "\n".join(by_id["figure_refs"]["items"])
        self.assertIn("fig:unused", joined_fig)
        self.assertIn("fig:missing", joined_fig)
        self.assertIn("Fig. 3", joined_fig)
        self.assertIn("未以加粗一句话总标题", joined_fig)
        self.assertEqual(by_id["draft_markers"]["state"], "fail")
        self.assertEqual(by_id["stats_reporting"]["state"], "warn")
        self.assertEqual(by_id["back_matter"]["state"], "warn")

    def test_clean_journal_manuscript_passes(self):
        tex = r"""\documentclass{article}
\begin{document}
\begin{abstract}
We measure catalytic rates across twelve oxide surfaces and reduce error by 38 percent relative to baseline models.
\end{abstract}
\section*{Introduction}
Catalytic activity depends on surface oxygen binding energy.
\section*{Results}
As shown in Fig.~\ref{fig:rates}a, measured turnover increases linearly (n = 12 samples, mean +/- 0.04 s.d., p = 0.002).
\begin{figure}[htbp]
\caption{\textbf{Surface binding controls catalytic rate.} Panel a shows measured rates.}
\label{fig:rates}
\end{figure}
\section*{Discussion}
These findings establish a quantitative descriptor.
\section*{Methods}
Calculations used VASP 6.4.2 with PBE functional.
\section*{Data availability}
All data are included in the supplementary tables.
\section*{Code availability}
Analysis scripts are deposited at https://github.com/example/repo.
\section*{Author contributions}
A.R. designed and executed the study.
\section*{Competing interests}
The author declares no competing interests.
\bibliographystyle{naturemag}
\end{document}
"""
        tmp = self._write_run(tex, {"venue_name": "Nature Methods", "short_name": "Nat. Methods", "abstract_limit": "150 words"})
        report = st.cmd_manuscript_audit(argparse.Namespace(run_dir=tmp.name))
        for check in report["checks"]:
            self.assertEqual(check["state"], "ok", f"{check['id']}: {check}")

    def test_structural_ai_rhythm_and_conference_figure_audit(self):
        tex = r"""\documentclass{article}
\begin{document}
\begin{abstract}
We tackle motion estimation across scenes, reaching 91.2, 88.4, 79.1, 64.0, 52.3, 41.2, and 33.8 on seven benchmarks, making inference fast.
These results remain to be evaluated on larger suites.
\end{abstract}
\section{Introduction}
First sentence is short. Second is short. Third is short. Fourth is short.
We achieve strong scores across benchmarks, making training efficient.
We propose a method, enabling fast convergence.
It improves accuracy, reducing latency cleanly.
This design scales well, preserving fine details.
Our method outperforms baselines, achieving superior results.
X is not Y in practice. Rather than approximating gradients, we optimize directly.
Our results are not limited to one domain, making transfer simple.
However, prior pipelines fail on the provided results under memory limits.
\section{Method}
We present the core architecture cleanly.
\begin{figure}[htbp]
\includegraphics[width=12cm]{figures/ablation_curve.png}
\caption{This unnecessarily long opening caption sentence intentionally contains well over twenty-five plain English words across multiple clauses so that the award-paper takeaway checker definitely flags the verbose opening claim directly.}
\label{fig:overview}
\end{figure}
\section{Experiments}
See Fig.~\ref{fig:overview} and Fig.~\ref{fig:late} here.
\begin{figure}[htbp]
\includegraphics[width=\linewidth]{figures/qualitative.png}
\caption{Short summary.}
\label{fig:late}
\end{figure}
\end{document}
"""
        tmp = self._write_run(tex, venue=None)
        report = st.cmd_manuscript_audit(argparse.Namespace(run_dir=tmp.name))
        by_id = {c["id"]: c for c in report["checks"]}
        self.assertEqual(by_id["ai_tells"]["state"], "warn")
        ai_items = "\n".join(by_id["ai_tells"]["items"])
        self.assertIn("句尾分词短语", ai_items)
        self.assertIn("工作流泄露", ai_items)
        self.assertIn("短句", ai_items)
        self.assertIn("摘要含", ai_items)
        self.assertIn("future-work", ai_items)
        self.assertIn("metrics", by_id["ai_tells"])

        self.assertEqual(by_id["figure_refs"]["state"], "warn")
        fig_items = "\n".join(by_id["figure_refs"]["items"])
        self.assertIn("图注首句共", fig_items)
        self.assertIn("绝对尺寸参数", fig_items)
        self.assertIn("ablation_curve.png", fig_items)
        self.assertIn("更靠后的章节", fig_items)

        # Chinese CJK prose should skip English-specific participle/caption-word checks
        cjk_tex = r"""\documentclass{article}
\begin{document}
\begin{abstract}
本文提出一种面向分子动力学非梯度力场的能量守恒修正方法，在多尺度基准上将误差降低 38\%。
\end{abstract}
\section{引言}
我们在热力学采样与动力学稳定性两方面给出理论分析与数值验证。详见图 \ref{fig:cn}。
\begin{figure}[htbp]
\includegraphics[width=\linewidth]{figures/main_diagram.pdf}
\caption{本文提出框架在多尺度非梯度力场上的整体流程图与对比效果示意说明。}
\label{fig:cn}
\end{figure}
\end{document}
"""
        cjk_tmp = self._write_run(cjk_tex, venue=None)
        cjk_rep = st.cmd_manuscript_audit(argparse.Namespace(run_dir=cjk_tmp.name))
        cjk_ai = "\n".join(next(c["items"] for c in cjk_rep["checks"] if c["id"] == "ai_tells"))
        self.assertNotIn("句尾分词短语", cjk_ai)

    def test_polish_abstract_offline_diagnose_and_author_report(self):
        import latex_copilot as lc

        tex = r"""\documentclass{article}
\begin{document}
\begin{abstract}
Existing neural solvers suffer from drift because explicit integrators accumulate truncation errors. We propose SymplecticNet, improving accuracy by 42% on molecular benchmarks.
\end{abstract}
\section*{Introduction}
Symplectic integrators preserve phase space volume \cite{verlet1967}.
\section*{Limitations}
Our current formulation remains to be evaluated on reactive systems with bond breaking.
\end{document}
"""
        tmp = self._write_run(tex, venue=None)
        diag = lc.cmd_polish_abstract(
            argparse.Namespace(text="", intro_text="", file="", run_dir=tmp.name, mode="diagnose", model="auto")
        )
        self.assertTrue(diag["ok"])
        self.assertEqual(diag["mode"], "diagnose")
        self.assertEqual(len(diag["fieldDiagnosis"]), 7)
        self.assertIn("metrics", diag["beforeAudit"])

        rep = st.cmd_author_report(argparse.Namespace(run_dir=tmp.name))
        self.assertTrue(rep["ok"])
        self.assertTrue(os.path.isfile(os.path.join(tmp.name, "report.md")))
        self.assertTrue(os.path.isfile(os.path.join(tmp.name, "author_report.json")))
        with open(os.path.join(tmp.name, "report.md"), encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("核心主张 — 证据对照表", md)
        self.assertIn("留给 Limitations", md)


if __name__ == "__main__":
    unittest.main()
