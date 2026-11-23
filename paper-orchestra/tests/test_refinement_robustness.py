"""What run No.01 (Sonnet 5.5, D:\\论文项目\\...) exposed in the refinement stage.

1. Page screenshots were never written: cv2.imwrite returns False on Windows
   for a path with Chinese characters, so the folders stayed empty.
2. With no page images the format reviewer still answered, from guesswork.
3. The review of the v1 revision failed; the failure counted as score 0, so the
   revision was rejected as "score decreased" and the paper stayed the draft.

Each test pins the fix without a real model, LaTeX, or Windows.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymupdf  # noqa: E402

import methods.agents.content_refinement_agent as R  # noqa: E402
from utils import pdf_utils  # noqa: E402


def make_pdf(path, pages):
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page(width=200, height=260).insert_text((20, 40), f"page {i + 1}")
    doc.save(path)
    doc.close()


class PageGridTests(unittest.TestCase):
    def test_grids_are_written_where_cv2_imwrite_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "paper.pdf")
            make_pdf(pdf, 5)
            out = os.path.join(tmp, "论文项目", "pdf_screenshots", "v0")
            # What Windows does with that path: no exception, just False.
            with mock.patch.object(pdf_utils.cv2, "imwrite", return_value=False):
                paths = pdf_utils.pdf_to_grid_images(pdf, out)
            self.assertEqual([os.path.basename(p) for p in paths], ["grid_1.png", "grid_2.png"])
            for p in paths:
                with open(p, "rb") as f:
                    self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")

    def test_unencodable_grid_is_skipped_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "paper.pdf")
            make_pdf(pdf, 1)
            with mock.patch.object(pdf_utils.cv2, "imencode", return_value=(False, None)):
                paths = pdf_utils.pdf_to_grid_images(pdf, os.path.join(tmp, "out"))
            self.assertEqual(paths, [])


class RefinementTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "运行")
        os.makedirs(self.dir)
        for name in ("log.md", "cites.json", "guide.md"):
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
                f.write("x")
        self.tex = os.path.join(self.dir, "draft.tex")
        with open(self.tex, "w", encoding="utf-8") as f:
            f.write("DRAFT")

        def compile_latex(work_dir, pdf_path, name, *a, **k):
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4 fake")

        self.refine_calls = []

        def refine(**kw):
            self.refine_calls.append(kw)
            return {"parsed_response": json.dumps({"json": {"plan": "fix"}, "latex": "REVISED"})}

        self.vlm = mock.Mock(side_effect=AssertionError("no page images: the VLM must not be asked"))
        for target, value in (
            ("compile_latex", compile_latex),
            ("pdf_to_grid_images", lambda pdf, out, *a, **k: []),
            ("resolve_task_model", lambda m, t: "fake:model"),
            ("supports_vision", lambda m: True),
            ("call_llm_with_pdf", refine),
            ("call_llm_with_images", self.vlm),
        ):
            p = mock.patch.object(R, target, value)
            p.start()
            self.addCleanup(p.stop)

    def agent(self, rounds=1):
        return R.ContentRefinementAgent(
            experimental_log_path=os.path.join(self.dir, "log.md"),
            citation_map_path=os.path.join(self.dir, "cites.json"),
            guidelines_path=os.path.join(self.dir, "guide.md"),
            model_name="fake:model", max_reflections=rounds, work_dir=self.dir,
        )

    @staticmethod
    def review(overall):
        axes = ("Originality", "Quality", "Clarity", "Significance", "Soundness", "Presentation", "Contribution")
        return {**{a: 3 for a in axes}, "Overall": overall, "Confidence": 4}, float(overall)

    def run_with_reviews(self, reviews, rounds=1):
        with mock.patch.object(R.ContentRefinementAgent, "_get_peer_review", side_effect=reviews) as rv:
            self.agent(rounds).run(self.tex)
        with open(os.path.join(self.dir, "content_refinement_worklog.json"), encoding="utf-8") as f:
            worklog = json.load(f)
        with open(os.path.join(self.dir, "final_refined_paper.tex"), encoding="utf-8") as f:
            final = f.read()
        return worklog, final, rv.call_count

    def test_failed_review_of_the_revision_keeps_the_draft_and_says_so(self):
        failed = ({"Error": "declined (stop_reason=refusal)", "Overall": 0}, 0.0)
        worklog, final, reviews = self.run_with_reviews([self.review(5), failed, failed], rounds=3)
        self.assertEqual(reviews, 3)  # draft, v1, v1 once more
        self.assertEqual(len(self.refine_calls), 1)  # no further rounds on an unreviewed paper
        entry = worklog["v1"]
        self.assertEqual(entry["outcome"], "REVIEW_FAILED_KEPT_PREVIOUS")
        self.assertNotIn("scores_after", entry)  # not a row of zeros (and frontend/app.py skips it)
        self.assertEqual(entry["unreviewed_revision"], "refined_paper_v1.tex")
        self.assertIn("refusal", entry["review_error"])
        self.assertEqual(final, "DRAFT")
        with open(os.path.join(self.dir, "refined_paper_v1.tex"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "REVISED")  # kept for the user to look at

    def test_review_that_fails_once_is_asked_again_and_compared(self):
        failed = ({"Error": "overloaded", "Overall": 0}, 0.0)
        worklog, final, reviews = self.run_with_reviews([self.review(5), failed, self.review(6)])
        self.assertEqual(reviews, 3)
        self.assertEqual(worklog["v1"]["outcome"], "ACCEPTED_SCORE_INCREASE")
        self.assertEqual(final, "REVISED")

    def test_reviewer_that_always_fails_behaves_as_before(self):
        # Nothing was ever reviewed (e.g. the reviewer model is misconfigured):
        # the original rule (0 vs 0, neutral) keeps refining, so the stop must not fire.
        failed = ({"Error": "404 model not found", "Overall": 0}, 0.0)
        worklog, final, reviews = self.run_with_reviews([failed] * 8, rounds=2)
        self.assertEqual([e["outcome"] for e in worklog.values()],
                         ["ACCEPTED_NEUTRAL_IMPROVEMENT", "ACCEPTED_NEUTRAL_IMPROVEMENT"])
        self.assertEqual(final, "REVISED")
        self.assertEqual(reviews, 6)  # draft, v1, v2, each asked twice

    def test_lower_score_still_rejects_the_revision(self):
        # The original rule is unchanged when both reviews exist.
        worklog, final, reviews = self.run_with_reviews([self.review(5), self.review(4)])
        self.assertEqual(reviews, 2)
        self.assertEqual(worklog["v1"]["outcome"], "REJECTED_SCORE_DECREASE")
        self.assertEqual(final, "DRAFT")

    def test_format_review_is_skipped_without_page_images(self):
        agent = self.agent()
        for grids in ([], [os.path.join(self.dir, "missing", "grid_1.png")]):
            with self.subTest(grids=grids), \
                    mock.patch.object(R, "pdf_to_grid_images", lambda pdf, out, *a, **k: grids):
                out = agent._get_formatting_review(self.tex, "fmt_loop_1")
            self.assertEqual(out, {"figure_and_tables": {}, "other_issues": "None"})
        self.vlm.assert_not_called()

    def test_format_review_gets_the_images_that_exist(self):
        grid = os.path.join(self.dir, "grid_1.png")
        with open(grid, "wb") as f:
            f.write(b"png")
        self.vlm.side_effect = None
        self.vlm.return_value = {"parsed_response": {"figure_and_tables": {}, "other_issues": []}}
        with mock.patch.object(R, "pdf_to_grid_images",
                               lambda pdf, out, *a, **k: [grid, os.path.join(self.dir, "gone.png")]):
            self.agent()._get_formatting_review(self.tex, "fmt_loop_1")
        self.assertEqual(self.vlm.call_args.kwargs["images"], [grid])


if __name__ == "__main__":
    unittest.main()
