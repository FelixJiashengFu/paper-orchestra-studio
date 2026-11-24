"""Run reports around write_single_paper (utils/run_reports.py): additive and failure-safe."""

import inspect
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("PO_PRICING_ONLINE", "0")  # no price-list downloads in tests
from utils import usage_tracker as U  # noqa: E402
from utils.run_reports import with_run_reports  # noqa: E402


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class DecoratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = self.tmp.name

    def tearDown(self):
        U.end_run()
        U.reset()
        self.tmp.cleanup()

    def test_success_writes_usage_and_audit(self):
        @with_run_reports
        def fake(base_dir, latex_template_dir, writer_model_name="auto"):
            U.record("qwen", "qwen-max", 100, 20)
            tex = os.path.join(base_dir, "content_refinement_workdir", "final_refined_paper.tex")
            os.makedirs(os.path.dirname(tex))
            with open(tex, "w") as f:
                f.write(r"\cite{a}\bibliography{references}")
            with open(os.path.join(os.path.dirname(tex), "references.bib"), "w") as f:
                f.write("@misc{a, title={A}}")
            return True

        self.assertIs(fake(self.run, "tpl"), True)
        usage = read_json(os.path.join(self.run, "usage_report.json"))
        self.assertEqual(usage["totals"], {"calls": 1, "errors": 0, "input_tokens": 100, "output_tokens": 20})
        audit = read_json(os.path.join(self.run, "citation_audit.json"))
        self.assertEqual(audit["summary"]["cited"], 1)
        self.assertTrue(os.path.isfile(os.path.join(self.run, "citation_audit.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.run, "report.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.run, "author_report.json")))

    def test_failure_still_writes_usage_and_skips_audit(self):
        for name in ("citation_audit.json", "citation_audit.md", "report.md", "author_report.json"):  # stale files from an earlier run
            with open(os.path.join(self.run, name), "w") as f:
                f.write("old")

        @with_run_reports
        def fake(base_dir):
            U.record_error("openai", "gpt-4.1")
            return False

        self.assertIs(fake(base_dir=self.run), False)
        self.assertEqual(read_json(os.path.join(self.run, "usage_report.json"))["totals"]["errors"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.run, "citation_audit.json")))
        self.assertFalse(os.path.exists(os.path.join(self.run, "citation_audit.md")))
        self.assertFalse(os.path.exists(os.path.join(self.run, "report.md")))
        self.assertFalse(os.path.exists(os.path.join(self.run, "author_report.json")))

    def test_exception_propagates_unchanged(self):
        class Boom(Exception):
            pass

        @with_run_reports
        def fake(base_dir):
            U.record("p", "m", 1, 1)
            raise Boom("x")

        with self.assertRaises(Boom):
            fake(self.run)
        self.assertEqual(read_json(os.path.join(self.run, "usage_report.json"))["totals"]["calls"], 1)

    def test_counters_reset_per_run(self):
        @with_run_reports
        def fake(base_dir):
            U.record("p", "m", 1, 1)
            return False

        fake(self.run)
        fake(self.run)
        self.assertEqual(read_json(os.path.join(self.run, "usage_report.json"))["totals"]["calls"], 1)

    def test_report_failures_never_break_the_run(self):
        @with_run_reports
        def fake(base_dir):
            return True

        with mock.patch("utils.citation_audit.audit_run", side_effect=RuntimeError("audit broke")), \
                mock.patch("utils.usage_tracker.atomic_write_json", side_effect=OSError("disk full")):
            self.assertIs(fake(self.run), True)

    def test_missing_base_dir(self):
        @with_run_reports
        def fake(base_dir=None):
            return "unchanged"

        self.assertEqual(fake(), "unchanged")
        self.assertEqual(fake(os.path.join(self.run, "does", "not", "exist")), "unchanged")


class WriteSinglePaperTests(unittest.TestCase):
    """The real pipelines keep their signature and still write usage_report.json when they fail."""

    def test_both_writers(self):
        from methods import paper_writer, paper_writer_with_plotting

        for module in (paper_writer, paper_writer_with_plotting):
            fn = module.write_single_paper
            self.assertTrue(hasattr(fn, "__wrapped__"))
            self.assertEqual(inspect.signature(fn), inspect.signature(fn.__wrapped__))
            self.assertEqual(list(inspect.signature(fn).parameters)[:2], ["base_dir", "latex_template_dir"])
            with tempfile.TemporaryDirectory() as run:
                # No raw_materials/: every try fails at its first assert, as before.
                with mock.patch("builtins.print"):
                    result = fn(base_dir=run, latex_template_dir=os.path.join(run, "no_template"))
                self.assertIs(result, False)
                usage = read_json(os.path.join(run, "usage_report.json"))
                self.assertEqual(usage["totals"]["calls"], 0)
                self.assertFalse(os.path.exists(os.path.join(run, "citation_audit.json")))
                self.assertFalse(os.path.exists(os.path.join(run, "final_paper.pdf")))


if __name__ == "__main__":
    unittest.main()
