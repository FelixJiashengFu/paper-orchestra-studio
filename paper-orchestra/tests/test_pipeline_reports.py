"""The real write_single_paper (both variants) with stand-in agents: the new reports appear
next to the unchanged outputs on a successful run.

The literature agent is the real one (search, LLM and metadata lookups are patched), so its
references.bib / references_meta.json flow through the pipeline to the citation audit.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import methods.agents.literature_review_agent as L  # noqa: E402
os.environ.setdefault("PO_PRICING_ONLINE", "0")  # no price-list downloads in tests
from utils import usage_tracker as U  # noqa: E402

RECORDS = {
    "Attention Is All You Need": {
        "title": "Attention Is All You Need", "authors": [{"name": "Ashish Vaswani"}], "year": 2017,
        "venue": "NeurIPS", "abstract": "Transformers.", "doi": "10.48550/arXiv.1706.03762",
        "url": "https://www.semanticscholar.org/paper/abc", "source": "semantic_scholar",
    },
    "Some Workshop Paper": {
        "title": "Some Workshop Paper", "authors": [{"name": "Jane Smith"}], "year": 2020, "venue": "WS",
        "abstract": "Text.", "source": "dblp",
    },
}
OUTLINE = {"intro_related_work_plan": {"introduction_strategy": {"search_directions": ["attention"]},
                                       "related_work_strategy": {"subsections": []}},
           "section_plan": []}


class FakeOutlineAgent:
    def __init__(self, model_name, cutoff_date):
        pass

    def run(self, idea_file, experimental_log_file, latex_template_file, conference_guidelines_file,
            output_filepath):
        U.record("fake", "outline-model", 10, 5)
        with open(output_filepath, "w") as f:
            json.dump(OUTLINE, f)


class FakePlottingAgent:
    def __init__(self, **kw):
        pass

    def run(self, outline_json_path, raw_materials_dir, output_filepath):
        return []


class FakeSectionWritingAgent:
    def __init__(self, **kw):
        pass

    def run(self, output_path):
        with open(output_path, "w") as f:
            f.write("draft")


class FakeContentRefinementAgent:
    def __init__(self, work_dir, **kw):
        self.work_dir = work_dir

    def run(self, texfile_path):
        U.record("fake", "refine-model", 100, 50)
        with open(os.path.join(self.work_dir, "final_refined_paper.tex"), "w") as f:
            f.write("\\begin{document}\\citep{Vaswani2017AttentionAll,Smith2020SomeWorkshop}"
                    "\\cite{Ghost2024Missing}\\bibliography{references}\\end{document}")
        pdf = os.path.join(self.work_dir, "final_refined_paper.pdf")
        with open(pdf, "wb") as f:
            f.write(b"%PDF-1.4 fake")
        return pdf


def fake_llm(prompt, model_name, **kw):
    U.record("fake", "lit-writer", 7, 3)
    if prompt.startswith("You are screening a reference library"):  # relevance screen (non-Gemini path)
        return {"raw_response": '{"irrelevant": []}', "parsed_response": {"irrelevant": []}}
    return {"raw_response": "LATEX", "parsed_response": "\\section{Introduction} \\cite{Vaswani2017AttentionAll}"}


class PipelineReportsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = os.path.join(self.tmp.name, "run")
        materials = os.path.join(self.run_dir, "raw_materials")
        os.makedirs(os.path.join(materials, "figures"))
        for name in ("idea_sparse.md", "experimental_log.md"):
            with open(os.path.join(materials, name), "w") as f:
                f.write("# text")
        self.template = os.path.join(REPO_ROOT, "templates", "iclr2025")

    def tearDown(self):
        U.end_run()
        U.reset()
        self.tmp.cleanup()

    def run_writer(self, module):
        candidates = [{"title": t, "year": r["year"], "reason": "why"} for t, r in RECORDS.items()]
        patches = [
            mock.patch.object(module, "OutlineAgent", FakeOutlineAgent),
            mock.patch.object(module, "SectionWritingAgent", FakeSectionWritingAgent),
            mock.patch.object(module, "ContentRefinementAgent", FakeContentRefinementAgent),
            mock.patch.object(L.HybridLiteratureAgent, "_discover_candidates", return_value=candidates),
            mock.patch.object(L, "enrich_lookup", side_effect=lambda t, *a, **k: dict(RECORDS[t])),
            mock.patch.object(L, "call_llm_with_text_prompt", side_effect=fake_llm),
            mock.patch.object(L, "literature_search_backend", return_value=("", "")),
            mock.patch("builtins.print"),
        ]
        if hasattr(module, "PlottingAgent"):
            patches.append(mock.patch.object(module, "PlottingAgent", FakePlottingAgent))
        for p in patches:
            p.start()
        try:
            return module.write_single_paper(
                base_dir=self.run_dir, latex_template_dir=self.template, idea_filename="idea_sparse.md",
                experimental_log_filename="experimental_log.md", writer_model_name="fake:writer",
                reflection_model_name="fake:reflect", research_cutoff="2025-01",
            )
        finally:
            for p in reversed(patches):
                p.stop()

    def check_run(self):
        rd = self.run_dir
        # The original outputs, where they always were.
        for rel in ("final_paper.pdf", "outline.json", "latex_writeup/references.bib",
                    "literature_agent_output/references.bib", "literature_agent_output/citation_map.json",
                    "content_refinement_workdir/final_refined_paper.tex"):
            self.assertTrue(os.path.isfile(os.path.join(rd, rel)), rel)
        with open(os.path.join(rd, "latex_writeup", "references.bib"), encoding="utf-8") as f:
            self.assertNotIn("doi", f.read().lower())

        with open(os.path.join(rd, "usage_report.json"), encoding="utf-8") as f:
            usage = json.load(f)
        # outline + relevance screen + literature write-up + refinement
        self.assertEqual(usage["totals"], {"calls": 4, "errors": 0, "input_tokens": 124, "output_tokens": 61})

        with open(os.path.join(rd, "literature_agent_output", "references_meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(sorted(meta), ["Smith2020SomeWorkshop", "Vaswani2017AttentionAll"])

        with open(os.path.join(rd, "citation_audit.json"), encoding="utf-8") as f:
            audit = json.load(f)
        self.assertEqual(audit["tex_file"], "content_refinement_workdir/final_refined_paper.tex")
        self.assertEqual(audit["bib_file"], "content_refinement_workdir/references.bib")
        self.assertEqual(audit["missing_in_bib"], ["Ghost2024Missing"])
        status = {e["key"]: e["status"] for e in audit["entries"]}
        self.assertEqual(status, {"Vaswani2017AttentionAll": "verified", "Smith2020SomeWorkshop": "metadata_only"})
        self.assertTrue(os.path.isfile(os.path.join(rd, "citation_audit.md")))

    def test_paper_writer(self):
        from methods import paper_writer

        self.assertIs(self.run_writer(paper_writer), True)
        self.check_run()

    def test_paper_writer_with_plotting(self):
        from methods import paper_writer_with_plotting

        self.assertIs(self.run_writer(paper_writer_with_plotting), True)
        self.check_run()
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "time_breakdown.json")))

    def test_rerun_failure_removes_old_audit(self):
        from methods import paper_writer

        self.assertIs(self.run_writer(paper_writer), True)
        shutil.rmtree(os.path.join(self.run_dir, "raw_materials"))  # the next run fails early
        with mock.patch("builtins.print"):
            self.assertIs(paper_writer.write_single_paper(base_dir=self.run_dir,
                                                          latex_template_dir=self.template), False)
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "citation_audit.json")))
        with open(os.path.join(self.run_dir, "usage_report.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["totals"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()
