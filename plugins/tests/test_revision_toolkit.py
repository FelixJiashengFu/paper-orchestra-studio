"""Offline tests for the deterministic po_revision_toolkit tool.

    python3 -m unittest discover -s plugins/tests -v
"""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SERVER = Path(__file__).resolve().parents[1] / "shared" / "mcp_server" / "paper_orchestra_mcp.py"

TEX = r"""\documentclass{article}
\title{A Study}
\author{Jane Doe \\ Example University}
\begin{document}
\maketitle
\begin{abstract}
We study a thing and show it works well on two benchmarks.
\end{abstract}
\section{Introduction}
We propose a method. % TODO hidden in a comment must not count
Results are in \ref{tab:main} and follow \cite{}.
TODO: add the ablation here.
\input{sections/method}
\section*{Acknowledgements}
We thank our lab. Code: https://github.com/janedoe/thing
\section{Limitations}
The limitation is the small sample size.
\bibliography{refs}
\end{document}
"""
METHOD = "\\section{Method}\nWe use a learning rate of 0.1 and report standard deviation over 5 runs on one GPU.\n"
LOG = "LaTeX Warning: Reference `tab:main' on page 1 undefined on input line 9.\nOutput written on main.pdf (11 pages, 12345 bytes).\n"
BIB = "@article{a1,\n title={X},\n doi={10.1000/retracted.1}\n}\n@article{a2,\n title={Y}\n}\n"

REVIEWS = """Reviewer 1
Summary: The paper proposes a method for the task and evaluates it on two datasets.
Weaknesses:
1. The paper lacks an ablation study and a comparison with the strongest baseline, which is a major concern.
2. There is a typo in Figure 3 caption and the notation in Section 2 is unclear.

Reviewer 2
- No error bars or statistical significance tests are reported for the main table.
- Please cite the related work on contrastive pretraining.
"""


def load_server(data_dir: str):
    with mock.patch.dict(os.environ, {"PAPER_ORCHESTRA_PLUGIN_DATA": data_dir, "PAPER_ORCHESTRA_DB": os.path.join(data_dir, "t.db")}):
        spec = importlib.util.spec_from_file_location("po_mcp_under_test", SERVER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, mod.RuntimeContext()


class RevisionToolkitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(cls.tmp.name)
        cls.mod, cls.ctx = load_server(str(root / "data"))
        cls.run_dir = root / "run"
        (cls.run_dir / "sections").mkdir(parents=True)
        (cls.run_dir / "main.tex").write_text(TEX, encoding="utf-8")
        (cls.run_dir / "sections" / "method.tex").write_text(METHOD, encoding="utf-8")
        (cls.run_dir / "main.log").write_text(LOG, encoding="utf-8")
        (cls.run_dir / "refs.bib").write_text(BIB, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def call(self, **args):
        return self.mod.tool_revision_toolkit(self.ctx, args)

    def test_tool_is_registered(self):
        self.assertIn("po_revision_toolkit", self.mod.TOOL_MAP)

    def test_review_matrix_splits_and_classifies(self):
        out = self.call(action="review_matrix", reviews_text=REVIEWS)
        ids = [r["id"] for r in out["rows"]]
        self.assertEqual(ids, ["R1.1", "R1.2", "R1.3", "R2.1", "R2.2"])
        by_id = {r["id"]: r for r in out["rows"]}
        self.assertEqual(by_id["R1.2"]["type"], "missing experiment")
        self.assertEqual(by_id["R1.2"]["severity"], "blocking")
        self.assertEqual(by_id["R1.3"]["severity"], "minor")
        self.assertEqual(by_id["R2.1"]["type"], "statistics")
        self.assertEqual(by_id["R2.2"]["type"], "missing citation")
        self.assertIn("R1.2", out["needsAuthorInput"])
        self.assertIn("| R2.2 |", out["markdown"])

    def test_review_matrix_refuses_to_overwrite(self):
        target = self.run_dir / "matrix.md"
        self.call(action="review_matrix", reviews_text=REVIEWS, out_path=str(target))
        self.assertTrue(target.is_file())
        with self.assertRaises(RuntimeError):
            self.call(action="review_matrix", reviews_text=REVIEWS, out_path=str(target))

    def test_preflight_finds_blockers(self):
        out = self.call(action="preflight", run_dir=str(self.run_dir), max_pages=9, anonymous=True,
                        required_statements=["limitations", "data_availability"])
        self.assertFalse(out["ready"])
        self.assertEqual(out["pages"], 11)
        self.assertEqual(out["filesScanned"], 2)
        checks = [b["check"] for b in out["blocking"]]
        self.assertIn("page limit", checks)
        self.assertIn("placeholder: TODO / FIXME marker", checks)
        self.assertIn("placeholder: empty citation", checks)
        self.assertIn("undefined references or citations", checks)
        self.assertIn("anonymity: acknowledgements present", checks)
        self.assertIn("anonymity: personal or lab repository URL", checks)
        self.assertIn("anonymity: \\author is not anonymised", checks)
        self.assertIn("required statement missing: data_availability", checks)
        self.assertIn("statement present: limitations", out["passed"])
        todo = next(b for b in out["blocking"] if b["check"].startswith("placeholder: TODO"))
        self.assertEqual([h["line"] for h in todo["hits"]], [12], "a TODO inside a LaTeX comment must be ignored")

    def test_preflight_passes_when_limits_are_met(self):
        out = self.call(action="preflight", run_dir=str(self.run_dir), max_pages=12, max_words=5000)
        self.assertIn("page limit: 11 <= 12", out["passed"])
        self.assertTrue(0 < out["words"] < 200)

    def test_retraction_check_flags_and_never_reports_failures_as_clean(self):
        def fake(doi, timeout):
            if doi == "10.1000/retracted.1":
                return [{"kind": "retraction", "status": "retracted", "noticeDoi": "10.1000/n", "noticeTitle": "Retraction"}]
            if doi == "10.1000/down":
                raise OSError("network unreachable")
            return []
        with mock.patch.object(self.mod, "_crossref_updates", side_effect=fake):
            out = self.call(action="retraction_check", run_dir=str(self.run_dir), dois=["https://doi.org/10.1000/ok", "10.1000/down"])
        status = {r["doi"]: r["status"] for r in out["results"]}
        self.assertEqual(status, {"10.1000/ok": "no_notice_found", "10.1000/down": "unverified", "10.1000/retracted.1": "retracted"})
        self.assertEqual([r["bibKey"] for r in out["flagged"]], ["a1"])

    def test_reporting_checklist(self):
        listing = self.call(action="reporting_checklist", guideline="list")
        self.assertEqual({g["id"] for g in listing["guidelines"]}, {"consort", "prisma", "strobe", "arrive", "ml_reproducibility"})
        out = self.call(action="reporting_checklist", guideline="ml_reproducibility", run_dir=str(self.run_dir))
        self.assertTrue(out["scanned"])
        by_topic = {r["topic"]: r["status"] for r in out["items"]}
        self.assertEqual(by_topic["Limitations discussed"], "evidence_found")
        self.assertEqual(by_topic["Licences for existing assets"], "not_found")
        with self.assertRaises(RuntimeError):
            self.call(action="reporting_checklist", guideline="nope")


if __name__ == "__main__":
    unittest.main()
