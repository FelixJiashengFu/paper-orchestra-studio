"""Relevance screen of the literature agent (non-Gemini discovery only).

Keyword search returns homonyms ("PET" imaging for the PET potential); the
writer must cite ~90% of the library, so the screen drops clear off-topic
papers. It must be conservative: drop only what the model names, keep a batch
that is flagged entirely, keep everything on any error, and never run on the
Gemini path or when PO_RELEVANCE_SCREEN=0.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import methods.agents.literature_review_agent as L  # noqa: E402
from methods.agents.literature_review_agent import HybridLiteratureAgent, PaperData  # noqa: E402


def paper(i: int, title: str) -> PaperData:
    return PaperData(
        citation_key=f"key{i}", title=title, authors=["A. Author"], venue="arXiv", year=2024,
        abstract=f"Abstract of {title}.", citation_count=1, found_in_section="Related Work",
        reason="keyword match",
    )


def bare_agent() -> HybridLiteratureAgent:
    agent = object.__new__(HybridLiteratureAgent)
    agent.model_name = "auto"
    agent.context_files = {"idea.md": "Rotational symmetry error of MLIPs on HEA SQS."}
    agent.registry_lock = threading.Lock()
    agent.print_lock = threading.Lock()
    return agent


OUTLINE = {"intro_related_work_plan": {"introduction_strategy": {"problem_gap_hypothesis": "P1 cells hide errors"}}}


class RelevanceScreenTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        patcher = mock.patch.object(L, "resolve_task_model", lambda m, t: "fake:model")
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_llm(self, answers):
        """answers: list of parsed responses (or exceptions), one per call."""
        it = iter(answers)

        def call(prompt, model_name, **kw):
            self.calls.append({"prompt": prompt, "model": model_name, **kw})
            ans = next(it)
            if isinstance(ans, Exception):
                raise ans
            return {"raw_response": json.dumps(ans), "parsed_response": ans}

        return mock.patch.object(L, "call_llm_with_text_prompt", call)

    def registry(self, titles):
        return {f"t{i}": paper(i, t) for i, t in enumerate(titles)}

    def test_drops_only_the_named_papers(self):
        reg = self.registry(["Orb-v3", "PET imaging segmentation", "MACE", "Calabi-Yau compactification"])
        with self.fake_llm([{"irrelevant": [{"id": 1, "reason": "medical imaging"}, {"id": "3", "reason": "string theory"}]}]):
            bare_agent()._screen_relevance(OUTLINE, reg)
        self.assertEqual([p.title for p in reg.values()], ["Orb-v3", "MACE"])
        self.assertEqual(len(self.calls), 1)
        prompt = self.calls[0]["prompt"]
        self.assertIn("PET imaging segmentation", prompt)
        self.assertIn("Rotational symmetry error", prompt)  # the idea is given
        self.assertIn("P1 cells hide errors", prompt)  # and the outline's problem statement
        self.assertEqual(self.calls[0]["model"], "fake:model")

    def test_empty_verdict_keeps_everything(self):
        reg = self.registry(["A", "B", "C"])
        with self.fake_llm([{"irrelevant": []}]):
            bare_agent()._screen_relevance(OUTLINE, reg)
        self.assertEqual(len(reg), 3)

    def test_whole_batch_flagged_is_kept(self):
        reg = self.registry(["A", "B"])
        with self.fake_llm([{"irrelevant": [{"id": 0}, {"id": 1}]}]):
            bare_agent()._screen_relevance(OUTLINE, reg)
        self.assertEqual(len(reg), 2)

    def test_errors_and_unreadable_answers_keep_everything(self):
        for answer in (RuntimeError("API down"), None, {"keep": [0]}, {"irrelevant": "all"}, ["x"]):
            with self.subTest(answer=answer):
                reg = self.registry(["A", "B", "C"])
                with self.fake_llm([answer]):
                    bare_agent()._screen_relevance(OUTLINE, reg)
                self.assertEqual(len(reg), 3)

    def test_out_of_range_and_malformed_ids_are_ignored(self):
        reg = self.registry(["A", "B", "C"])
        with self.fake_llm([{"irrelevant": [{"id": 7}, {"id": -1}, {"id": None}, "1", {"id": 2}]}]):
            bare_agent()._screen_relevance(OUTLINE, reg)
        self.assertEqual([p.title for p in reg.values()], ["A", "B"])

    def test_batches_use_local_ids(self):
        n = L._RELEVANCE_BATCH + 5  # two equal batches: 18 + 17
        titles = [f"Paper {i}" for i in range(n)]
        reg = self.registry(titles)
        second = n - (n + 1) // 2
        with self.fake_llm([{"irrelevant": [{"id": 0}]}, {"irrelevant": [{"id": second - 1}]}]):
            bare_agent()._screen_relevance(OUTLINE, reg)
        self.assertEqual(len(self.calls), 2)
        remaining = {p.title for p in reg.values()}
        self.assertNotIn("Paper 0", remaining)
        self.assertNotIn(f"Paper {n - 1}", remaining)
        self.assertEqual(len(remaining), len(titles) - 2)

    def test_no_tiny_trailing_batch(self):
        n = L._RELEVANCE_BATCH + 1  # 31 papers: 16 + 15, not 30 + 1
        reg = self.registry([f"Paper {i}" for i in range(n)])
        with self.fake_llm([{"irrelevant": []}, {"irrelevant": [{"id": 14}]}]):
            bare_agent()._screen_relevance(OUTLINE, reg)
        in_template = L._RELEVANCE_PROMPT.count('"found_by"')
        sizes = [p["prompt"].count('"found_by"') - in_template for p in self.calls]
        self.assertEqual(sizes, [16, 15])
        self.assertNotIn(f"Paper {n - 1}", {p.title for p in reg.values()})

    def test_papers_requested_by_title_are_not_screened(self):
        reg = self.registry(["Dice coefficient", "Orb-v3", "PET imaging", "MACE"])
        reg["t0"].reason = "Targeted Semantic Scholar match for the Dice overlap metric"
        with self.fake_llm([{"irrelevant": [{"id": 1, "reason": "medical imaging"}]}]), \
                mock.patch.object(HybridLiteratureAgent, "_safe_print", create=True) as log:
            bare_agent()._screen_relevance(OUTLINE, reg)
        prompt = self.calls[0]["prompt"]
        self.assertNotIn("Dice coefficient", prompt)  # never shown, so it cannot be dropped
        self.assertIn('"found_by": "keyword match"', prompt)  # the others carry their query
        self.assertEqual([p.title for p in reg.values()], ["Dice coefficient", "Orb-v3", "MACE"])
        printed = " ".join(str(c.args[0]) for c in log.call_args_list)
        self.assertIn("kept 3 of 4 papers", printed)

    def test_only_title_requested_papers_left_means_no_call(self):
        reg = self.registry(["A", "B", "C"])
        for p in list(reg.values())[:2]:
            p.reason = "Targeted OpenAlex match for a baseline"
        with self.fake_llm([]):
            bare_agent()._screen_relevance(OUTLINE, reg)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(reg), 3)

    def test_echoed_format_example_drops_nothing(self):
        for echo in ({"irrelevant": [{"id": 0, "reason": "short reason"}]},
                     {"irrelevant": [{"id": 0, "reason": "<why it is off-topic>"}]}):
            with self.subTest(echo=echo):
                reg = self.registry(["A", "B", "C"])
                with self.fake_llm([echo]):
                    bare_agent()._screen_relevance(OUTLINE, reg)
                self.assertEqual(len(reg), 3)

    def test_prompt_example_is_not_a_parseable_verdict(self):
        # The only literal JSON in the prompt is the empty answer; the non-empty
        # form uses placeholders, so a model that copies it cannot drop paper 0.
        self.assertIn('{"irrelevant": []}', L._RELEVANCE_PROMPT.format(idea="", problem="", candidates=""))
        self.assertNotIn('"id": 0', L._RELEVANCE_PROMPT.format(idea="", problem="", candidates=""))

    def test_single_paper_is_not_screened(self):
        reg = self.registry(["Only one"])
        with self.fake_llm([]):
            bare_agent()._screen_relevance(OUTLINE, reg)
        self.assertEqual(len(reg), 1)
        self.assertEqual(self.calls, [])


class RunGatingTests(unittest.TestCase):
    """run() screens only on the non-Gemini path and only when enabled."""

    def run_agent(self, gemini: bool, env: dict):
        agent = bare_agent()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        agent.output_dir = tmp.name
        agent.max_workers = 1
        outline_path = os.path.join(tmp.name, "outline.json")
        with open(outline_path, "w", encoding="utf-8") as f:
            json.dump(OUTLINE, f)

        def fill(tasks, outline, registry, processed, cutoff):
            registry.update({"a": paper(0, "Orb-v3"), "b": paper(1, "PET imaging")})

        seen = {}
        with mock.patch.object(L, "literature_search_backend",
                                  lambda: ("gemini", "gemini-3-flash-preview") if gemini else ("", "")), \
                mock.patch.dict(os.environ, env), \
                mock.patch.object(HybridLiteratureAgent, "_collect_search_tasks", lambda self, o: []), \
                mock.patch.object(HybridLiteratureAgent, "_execute_tasks_in_parallel", lambda self, *a: fill(*a)), \
                mock.patch.object(HybridLiteratureAgent, "_screen_relevance",
                                  lambda self, o, reg: seen.setdefault("screened", sorted(reg))), \
                mock.patch.object(HybridLiteratureAgent, "_synthesize_content", lambda self, *a: "latex"), \
                mock.patch.object(HybridLiteratureAgent, "_save_outputs", lambda self, *a, **k: None):
            agent.run(outline_path, cutoff_date="2026-11")
        return seen.get("screened")

    def test_non_gemini_path_screens(self):
        self.assertEqual(self.run_agent(gemini=False, env={"PO_RELEVANCE_SCREEN": "1"}), ["a", "b"])

    def test_gemini_path_is_unchanged(self):
        self.assertIsNone(self.run_agent(gemini=True, env={"PO_RELEVANCE_SCREEN": "1"}))

    def test_switch_off(self):
        for off in ("0", "false", "off", "no"):
            with self.subTest(off=off):
                self.assertIsNone(self.run_agent(gemini=False, env={"PO_RELEVANCE_SCREEN": off}))



class DeepSeekDiscoveryTests(unittest.TestCase):
    """PO_TASK_MODEL_LITERATURE_SEARCH=deepseek-flash: DeepSeek web search, scholarly APIs as fallback."""

    TASK = {"section": "Related Work", "focus": "f", "context": "c", "search_type": "exploration"}

    def agent(self):
        agent = bare_agent()
        agent.search_backend, agent.search_model = "deepseek", "deepseek-flash"
        return agent

    def test_uses_web_search_results(self):
        agent = self.agent()
        hit = {"parsed_response": {"candidates": [{"title": "Orb-v3", "year": 2025}]}}
        with mock.patch.object(L, "call_deepseek_with_web_search", return_value=hit) as ds, \
                mock.patch.object(HybridLiteratureAgent, "_discover_candidates_via_scholar_apis") as apis:
            self.assertEqual(agent._discover_candidates(self.TASK, OUTLINE, "2026-11"), [{"title": "Orb-v3", "year": 2025}])
        apis.assert_not_called()
        self.assertNotIn("Google Search", ds.call_args[0][0])

    def test_targeted_hits_are_marked_so_the_screen_keeps_them(self):
        agent = self.agent()
        task = dict(self.TASK, search_type="targeted", focus="paper introducing dataset X")
        hit = {"parsed_response": {"candidates": [{"title": "X", "year": 2024, "reason": "Introduces X"}]}}
        with mock.patch.object(L, "call_deepseek_with_web_search", return_value=hit):
            found = agent._discover_candidates(task, OUTLINE, "2026-11")
        self.assertTrue(found[0]["reason"].startswith(L._RELEVANCE_SKIP_REASON_PREFIX))

    def test_falls_back_and_stops_asking_after_a_refused_request(self):
        agent = self.agent()
        err = RuntimeError("unknown tool type")
        err.status_code = 400
        with mock.patch.object(L, "call_deepseek_with_web_search", side_effect=err) as ds, \
                mock.patch.object(HybridLiteratureAgent, "_discover_candidates_via_scholar_apis",
                                  return_value=[{"title": "S2 hit"}]), \
                mock.patch("builtins.print"):
            self.assertEqual(agent._discover_candidates(self.TASK, OUTLINE, "2026-11"), [{"title": "S2 hit"}])
            self.assertEqual(agent._discover_candidates(self.TASK, OUTLINE, "2026-11"), [{"title": "S2 hit"}])
        self.assertEqual(ds.call_count, 1)

    def test_empty_answer_falls_back(self):
        agent = self.agent()
        with mock.patch.object(L, "call_deepseek_with_web_search", return_value={"parsed_response": None}), \
                mock.patch.object(HybridLiteratureAgent, "_discover_candidates_via_scholar_apis",
                                  return_value=[{"title": "S2 hit"}]), \
                mock.patch("builtins.print"):
            self.assertEqual(agent._discover_candidates(self.TASK, OUTLINE, "2026-11"), [{"title": "S2 hit"}])


if __name__ == "__main__":
    unittest.main()
