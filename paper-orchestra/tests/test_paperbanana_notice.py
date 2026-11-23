"""Missing optional PaperBanana files are noted once per run, not once per figure."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils import paper_banana_utils as U  # noqa: E402


class MissingPaperBananaFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patches = [
            mock.patch.object(U, "PB_DIR", self.tmp.name),
            mock.patch.object(U, "_missing_notices", set()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_reference_set_noted_once_per_task(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for _ in range(3):
                self.assertEqual(U.retrieve_few_shot_examples("plot", "data", "intent"), [])
                self.assertEqual(U.retrieve_few_shot_examples("diagram", "method", "caption"), [])
        lines = [l for l in out.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(all(l.startswith("Note: PaperBanana reference figures") for l in lines))
        self.assertNotIn("Warn:", out.getvalue())

    def test_style_guide_noted_once_and_styling_still_runs(self):
        calls = []

        def fake_llm(**kwargs):
            calls.append(kwargs["prompt"])
            return {"parsed_response": " styled "}

        out = io.StringIO()
        with mock.patch.object(U, "call_llm_with_text_prompt", side_effect=fake_llm), \
                contextlib.redirect_stdout(out):
            for _ in range(2):
                self.assertEqual(U.style_figure_content("plot", "data", "intent", "desc", model_name="x"), "styled")
        self.assertEqual(len(calls), 2)
        self.assertEqual(out.getvalue().count("style guide for 'plot' is not installed"), 1)

    def test_installed_style_guide_is_used_without_notice(self):
        os.makedirs(os.path.join(self.tmp.name, "style_guides"))
        with open(os.path.join(self.tmp.name, "style_guides", "neurips2025_plot_style_guide.md"), "w", encoding="utf-8") as f:
            f.write("USE SERIF")
        calls = []
        out = io.StringIO()
        with mock.patch.object(U, "call_llm_with_text_prompt",
                               side_effect=lambda **k: calls.append(k["prompt"]) or {"parsed_response": "ok"}), \
                contextlib.redirect_stdout(out):
            U.style_figure_content("plot", "data", "intent", "desc", model_name="x")
        self.assertIn("USE SERIF", calls[0])
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
