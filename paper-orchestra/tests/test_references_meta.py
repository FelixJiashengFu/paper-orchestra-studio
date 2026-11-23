"""references_meta.json sidecar of the literature agent, and references.bib byte-identity.

The reference BibTeX generator below is a verbatim copy of
HybridLiteratureAgent._generate_bibtex from the original code (commit c68cd16):
the .bib written today must be byte-identical to what it produced.
"""

import json
import os
import sys
import tempfile
import unittest
from typing import List
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import methods.agents.literature_review_agent as L  # noqa: E402
from methods.agents.literature_review_agent import HybridLiteratureAgent, PaperData  # noqa: E402

ORIGINAL_DUMP_KEYS = {
    "citation_key", "title", "authors", "venue", "year", "abstract", "citation_count",
    "found_in_section", "reason", "journal", "volume", "pages", "publication_date",
}


def original_generate_bibtex(papers: List[PaperData]) -> str:
    """Verbatim from c68cd16 (methods/agents/literature_review_agent.py), minus `self`."""
    entries = []
    seen_keys = set()
    for p in papers:
        base_key = p.citation_key
        unique_key = base_key
        suffix = "a"
        while unique_key in seen_keys:
            unique_key = f"{base_key}{suffix}"
            suffix = chr(ord(suffix) + 1)
        p.citation_key = unique_key
        seen_keys.add(unique_key)

        author_str = " and ".join(p.authors) if p.authors else "Unknown"
        entry_type = "article" if p.journal else "inproceedings"

        entry = f"@{entry_type}{{{p.citation_key},\n  title={{{p.title}}},\n  author={{{author_str}}},\n"
        if p.journal:
            entry += f"  journal={{{p.journal}}},\n"
        else:
            entry += f"  booktitle={{{p.venue}}},\n"
        entry += f"  year={{{p.year}}}"
        if p.volume:
            entry += f",\n  volume={{{p.volume}}}"
        if p.pages:
            entry += f",\n  pages={{{p.pages}}}"
        entry += "\n}"
        entries.append(entry)
    return "\n\n".join(entries)


def original_paper(agent, s2_data, section="Introduction", reason="r") -> PaperData:
    """PaperData exactly as the original _enrich_and_register_paper built it (no provenance fields)."""
    j_info = s2_data.get("journal") or {}
    abstract_text = s2_data.get("abstract") or ""
    if len(abstract_text) > 1500:
        abstract_text = abstract_text[:1500] + "... [Truncated]"
    return PaperData(
        citation_key=agent._generate_key(s2_data.get("authors", []), s2_data.get("year"), s2_data["title"]),
        title=s2_data["title"],
        authors=[a["name"] for a in s2_data.get("authors", [])],
        venue=s2_data.get("venue") or "arXiv",
        year=s2_data.get("year") or 2024,
        abstract=abstract_text,
        citation_count=s2_data.get("citationCount") or 0,
        found_in_section=section,
        reason=reason,
        journal=j_info.get("name"),
        volume=j_info.get("volume"),
        pages=j_info.get("pages"),
        publication_date=s2_data.get("publicationDate"),
    )


RECORDS = {
    "Attention Is All You Need": {
        "title": "Attention Is All You Need", "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
        "year": 2017, "venue": "NeurIPS", "abstract": "The dominant sequence transduction models ...",
        "citationCount": 100000, "publicationDate": "2017-06-12", "journal": None,
        "doi": "10.48550/arXiv.1706.03762", "url": "https://www.semanticscholar.org/paper/abc",
        "source": "semantic_scholar",
    },
    "Deep Residual Learning for Image Recognition": {
        "title": "Deep Residual Learning for Image Recognition", "authors": [{"name": "Kaiming He"}],
        "year": 2016, "venue": "CVPR", "abstract": "Deeper neural networks are more difficult to train." * 60,
        "citationCount": 5, "publicationDate": "2016-06-01",
        "journal": {"name": "Proc. CVPR", "volume": "1", "pages": "770--778"},
        "doi": "https://doi.org/10.1109/CVPR_2016.90", "url": None, "source": "openalex",
    },
    "Deep Learning Methods A": {
        "title": "Deep Learning Methods A", "authors": [{"name": "Jane Smith"}], "year": 2020,
        "venue": "", "abstract": "First.", "doi": None, "url": "https://example.org/a", "source": "crossref",
    },
    "Deep Learning Methods B": {  # same first author / year / title words -> key gets suffix "a"
        "title": "Deep Learning Methods B", "authors": [{"name": "Jane Smith"}], "year": 2020,
        "venue": "ICML", "abstract": "Second.", "source": "dblp+europepmc",
    },
    "No Abstract Paper": {
        "title": "No Abstract Paper", "authors": [{"name": "X Y"}], "year": 2019, "abstract": "",
        "doi": "10.1/none", "source": "dblp",
    },
}


def make_agent(out_dir):
    missing = os.path.join(out_dir, "missing.md")
    return HybridLiteratureAgent(
        idea_path=missing, experimental_log_path=missing, latex_template_path=missing,
        conference_guidelines_path=missing, output_dir=out_dir, model_name="fake-model", max_workers=1,
    )


class ReferencesMetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "literature_agent_output")

    def tearDown(self):
        self.tmp.cleanup()

    def test_paper_data_construction_and_dump_unchanged(self):
        p = PaperData(citation_key="k", title="t", authors=[], venue="v", year=2020, abstract="a",
                      citation_count=0, found_in_section="s", reason="r")
        self.assertIsNone(p.doi)
        self.assertIsNone(p.source)
        self.assertEqual(set(p.model_dump()), ORIGINAL_DUMP_KEYS)
        q = PaperData(**p.model_dump(), doi="10.1/x", url="https://x", source="crossref")
        self.assertEqual(q.model_dump(), p.model_dump())  # provenance never reaches the writer prompt

    def test_bibtex_byte_identical_with_provenance(self):
        agent = make_agent(self.out)
        with_meta, without = [], []
        for rec in RECORDS.values():
            if not rec.get("abstract"):
                continue
            base = original_paper(agent, rec)
            without.append(base)
            with_meta.append(PaperData(**base.model_dump(), **L._source_meta(rec)))
        self.assertEqual(agent._generate_bibtex(with_meta), original_generate_bibtex(without))
        for p in with_meta:
            self.assertNotIn("doi", agent._generate_bibtex([p]).lower())

    def test_run_writes_sidecar_and_identical_bib(self):
        os.makedirs(self.out, exist_ok=True)
        outline_path = os.path.join(self.tmp.name, "outline.json")
        with open(outline_path, "w", encoding="utf-8") as f:
            json.dump({"intro_related_work_plan": {
                "introduction_strategy": {"search_directions": ["transformers"]},
                "related_work_strategy": {"subsections": []}}, "section_plan": []}, f)
        agent = make_agent(self.out)
        candidates = [{"title": t, "year": r.get("year"), "reason": "why"} for t, r in RECORDS.items()]
        prompts = []

        def fake_llm(prompt, model_name, **kw):
            prompts.append(prompt)
            return {"raw_response": "LATEX", "parsed_response": "LATEX"}

        with mock.patch.object(agent, "_discover_candidates", return_value=candidates), \
                mock.patch.object(L, "enrich_lookup", side_effect=lambda t, *a, **k: dict(RECORDS[t])), \
                mock.patch.object(L, "call_llm_with_text_prompt", side_effect=fake_llm), \
                mock.patch.object(L, "_relevance_screen_enabled", return_value=False):  # covered in test_relevance_screen
            agent.run(outline_path=outline_path, cutoff_date="2025-01")

        expected = original_generate_bibtex(
            [original_paper(agent, RECORDS[t], reason="why") for t in RECORDS if RECORDS[t]["abstract"]])
        # Byte-identical to the original apart from the platform newline: both the
        # original and the new code write the .bib in text mode, so Windows gets
        # CRLF from either one.
        with open(os.path.join(self.out, "references.bib"), "rb") as f:
            self.assertEqual(f.read(), expected.replace("\n", os.linesep).encode("utf-8"))

        with open(os.path.join(self.out, "references_meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(list(meta), ["Vaswani2017AttentionAll", "He2016DeepResidual",
                                      "Smith2020DeepLearning", "Smith2020DeepLearninga"])
        self.assertEqual(meta["Vaswani2017AttentionAll"], {
            "title": "Attention Is All You Need", "year": 2017, "doi": "10.48550/arXiv.1706.03762",
            "url": "https://www.semanticscholar.org/paper/abc", "source": "semantic_scholar"})
        self.assertEqual(meta["He2016DeepResidual"]["doi"], "10.1109/CVPR_2016.90")  # doi.org prefix dropped
        self.assertEqual(meta["Smith2020DeepLearninga"], {
            "title": "Deep Learning Methods B", "year": 2020, "doi": None, "url": None,
            "source": "dblp+europepmc"})

        # The writer prompt carries exactly the original paper fields.
        payload = json.loads(prompts[-1].split("\n\n", 1)[1])
        self.assertEqual(len(payload["collected_papers"]), 4)
        for p in payload["collected_papers"]:
            self.assertEqual(set(p), ORIGINAL_DUMP_KEYS)
        # The other outputs are still written.
        for name in ("updated_template.tex", "citation_map.json", "outline_v1.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, name)), name)

    def test_sidecar_failure_does_not_break_saving(self):
        agent = make_agent(self.out)
        outline_path = os.path.join(self.out, "outline_v1.json")
        with open(os.path.join(self.out, "references_meta.json"), "w") as f:
            f.write('{"Stale2019Key": {}}')  # left by an earlier run in the same folder
        agent._save_outputs("L", "B", {}, {}, outline_path, references_meta={"k": {"bad": object()}})
        with open(os.path.join(self.out, "references.bib"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "B")
        self.assertTrue(os.path.isfile(outline_path))
        self.assertFalse(os.path.exists(os.path.join(self.out, "references_meta.json")))
        # The original positional call still works and writes no sidecar.
        agent._save_outputs("L", "B2", {}, {}, outline_path)
        self.assertFalse(os.path.exists(os.path.join(self.out, "references_meta.json")))

    def test_source_meta_never_raises(self):
        self.assertEqual(L._source_meta(None), {"doi": None, "url": None, "source": None})
        self.assertEqual(L._source_meta({"doi": "doi: 10.5/X", "url": "javascript:alert(1)", "source": 3}),
                         {"doi": "10.5/X", "url": None, "source": "3"})


if __name__ == "__main__":
    unittest.main()
