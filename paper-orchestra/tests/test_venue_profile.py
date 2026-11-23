"""Journal templates (templates/journal-*) adapt the agents' prompts; conference
templates (cvpr2025, iclr2025) must leave every prompt exactly as it was.

A journal template carries a venue.json next to guidelines.md. utils.venue_profile
turns it into a note appended to the outline, literature, writing, refinement and
review prompts. These tests pin both halves: the note reaches each agent for a
journal, and nothing changes without venue.json. They also check that every
journal template is complete and compiles (when pdflatex is installed).
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from utils.venue_profile import REQUIRED_KEYS, ROLES, load_venue_profile, venue_note  # noqa: E402

TEMPLATES = os.path.join(ROOT, "templates")
CONFERENCE_GUIDELINES = os.path.join(TEMPLATES, "cvpr2025", "guidelines.md")
JOURNAL_DIRS = sorted(d for d in glob.glob(os.path.join(TEMPLATES, "journal-*")) if os.path.isdir(d))

PROFILE = {
    "kind": "journal",
    "venue_name": "Journal of Chemical Theory and Computation",
    "short_name": "JCTC",
    "field": "computational chemistry",
    "review_model": "single-blind",
    "separate_related_work_section": False,
    "section_order": ["Introduction", "Computational Details", "Results and Discussion", "Conclusions"],
    "abstract_limit": "about 250 words, no citations",
    "writing_style": "Precise and quantitative.",
    "reviewer_focus": "Reproducibility of the computational protocol and benchmark quality.",
    "figure_style": "Single column 3.25 in wide.",
    "extras": ["TOC graphic in tocentry", "Supporting Information paragraph"],
}


def journal_dir(tmp, profile=PROFILE):
    os.makedirs(tmp, exist_ok=True)
    with open(os.path.join(tmp, "venue.json"), "w", encoding="utf-8") as fh:
        json.dump(profile, fh)
    with open(os.path.join(tmp, "guidelines.md"), "w", encoding="utf-8") as fh:
        fh.write("# Guidelines\n\nTarget about 8 journal pages; abstract ≤ 250 words.\n")
    with open(os.path.join(tmp, "template.tex"), "w", encoding="utf-8") as fh:
        fh.write("\\section{Introduction}\nIntroduction here.\n")
    return os.path.join(tmp, "guidelines.md")


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_conference_templates_and_bad_input_have_no_profile(self):
        for path in (None, "", CONFERENCE_GUIDELINES, os.path.join(TEMPLATES, "iclr2025"),
                     os.path.join(self.tmp, "missing", "guidelines.md")):
            self.assertIsNone(load_venue_profile(path), path)

    def test_journal_profile_loads_from_the_guidelines_path_or_the_folder(self):
        guidelines = journal_dir(self.tmp)
        self.assertEqual(load_venue_profile(guidelines)["short_name"], "JCTC")
        self.assertEqual(load_venue_profile(self.tmp)["short_name"], "JCTC")

    def test_unreadable_or_non_journal_profiles_are_ignored(self):
        with open(os.path.join(self.tmp, "venue.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertIsNone(load_venue_profile(self.tmp))
        for data in ([1, 2], {"kind": "conference", "venue_name": "X"}):
            with open(os.path.join(self.tmp, "venue.json"), "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            self.assertIsNone(load_venue_profile(self.tmp))


class NoteTests(unittest.TestCase):
    def test_no_profile_means_no_note_for_every_role(self):
        for role in ROLES + ("anything",):
            self.assertEqual(venue_note(None, role), "")

    def test_unknown_role_is_a_bug(self):
        with self.assertRaises(ValueError):
            venue_note(PROFILE, "plot")

    def test_notes_carry_the_journal_rules(self):
        for role in ROLES:
            note = venue_note(PROFILE, role)
            self.assertIn("Journal of Chemical Theory and Computation (JCTC)", note, role)
            self.assertTrue(note.startswith("\n###"), role)
        writing = venue_note(PROFILE, "writing")
        for part in ("Introduction; Computational Details; Results and Discussion; Conclusions",
                     "about 250 words, no citations", "TOC graphic in tocentry",
                     "To be completed by the authors.", "\\ce{", "Single column 3.25 in wide."):
            self.assertIn(part, writing)
        self.assertIn("no new \\section", venue_note(PROFILE, "literature"))
        self.assertIn("intro_related_work_plan", venue_note(PROFILE, "outline"))
        self.assertIn("plotting_plan", venue_note(PROFILE, "outline"))
        for role in ("refinement", "review", "meta_review"):
            self.assertIn("Reproducibility of the computational protocol", venue_note(PROFILE, role))
        self.assertIn("handling editor", venue_note(PROFILE, "meta_review"))
        self.assertIn("placeholders", venue_note(PROFILE, "review"))
        self.assertIn("Claim-driven architecture", venue_note(PROFILE, "outline"))
        self.assertIn("zero em-dashes", venue_note(PROFILE, "writing"))
        self.assertIn("Statistical reporting", venue_note(PROFILE, "writing"))
        self.assertIn("Polish prose", venue_note(PROFILE, "refinement"))
        self.assertIn("claim-to-evidence", venue_note(PROFILE, "review"))

    def test_extra_figure_style_guide_reaches_stylist(self):
        from utils import paper_banana_utils as PBU

        calls = []

        def fake_call(**kwargs):
            calls.append(kwargs["prompt"])
            return {"parsed_response": "styled"}

        with mock.patch.object(PBU, "call_llm_with_text_prompt", side_effect=fake_call), \
                mock.patch("builtins.print"):
            out = PBU.style_figure_content(
                "plot", "raw", "intent", "desc", model_name="m", extra_style_guide="NATURE FIG SPEC"
            )
        self.assertEqual(out, "styled")
        self.assertIn("NATURE FIG SPEC", calls[0])


class AgentPromptTests(unittest.TestCase):
    """The note reaches each agent for a journal; conference prompts stay byte-identical."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal = journal_dir(os.path.join(self.tmp, "journal"))
        self.files = {}
        for name, text in (("idea.md", "idea"), ("log.md", "log"), ("outline.json", "{}"),
                           ("citation_map.json", "{}"), ("figures.json", "[]")):
            path = os.path.join(self.tmp, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.files[name] = path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_outline_agent(self):
        import methods.agents.outline_agent as O

        seen = []

        def fake_call(prompt, model_name, generation_configs, **kw):
            seen.append(generation_configs["system_instruction"])
            return {"parsed_response": {}}

        base = O.outline_agent_system_prompt.format(cutoff_date="2024-10")
        with mock.patch.object(O, "call_llm_with_text_prompt", fake_call), \
                mock.patch.object(O, "resolve_task_model", lambda m, t: m):
            for guidelines in (CONFERENCE_GUIDELINES, self.journal):
                O.OutlineAgent(model_name="m").run(
                    self.files["idea.md"], self.files["log.md"],
                    os.path.join(TEMPLATES, "cvpr2025", "template.tex"), guidelines)
        self.assertEqual(seen[0], base)
        self.assertEqual(seen[1], base + venue_note(PROFILE, "outline"))

    def test_literature_writer(self):
        import methods.agents.literature_review_agent as L

        seen = []

        def fake_call(model_name, prompt, generation_configs, **kw):
            seen.append((generation_configs["system_instruction"], prompt))
            return {"parsed_response": "latex"}

        base = L.literature_review_agent_writter_prompt.format(
            paper_count=0, min_cite_paper_count=0, cutoff_date="2024-10")
        with mock.patch.object(L, "call_llm_with_text_prompt", fake_call), \
                mock.patch.object(L, "resolve_task_model", lambda m, t: m):
            for guidelines in (CONFERENCE_GUIDELINES, self.journal):
                agent = L.HybridLiteratureAgent(
                    self.files["idea.md"], self.files["log.md"],
                    os.path.join(TEMPLATES, "cvpr2025", "template.tex"), guidelines,
                    os.path.join(self.tmp, "lit"), model_name="m")
                agent._synthesize_content({}, [], "2024-10")
        self.assertEqual(seen[0][0], base)
        self.assertTrue(seen[0][1].startswith("Generate the LaTeX for Introduction and Related Work sections.\n\n"))
        self.assertEqual(seen[1][0], base + venue_note(PROFILE, "literature"))
        self.assertTrue(seen[1][1].startswith("Generate the LaTeX for the Introduction section"))

    def test_section_writing_and_refinement_agents(self):
        from methods.agents.content_refinement_agent import ContentRefinementAgent
        from methods.agents.section_writing_agent import SectionWritingAgent
        from methods.prompts.content_refinement_agent import content_refinement_agent_system_prompt
        from methods.prompts.section_writing_agent import section_writing_agent_prompt

        def writer(guidelines):
            return SectionWritingAgent(
                self.files["outline.json"], os.path.join(TEMPLATES, "cvpr2025", "template.tex"),
                self.files["idea.md"], self.files["log.md"], self.files["citation_map.json"],
                self.files["figures.json"], guidelines, model_name="m")

        def refiner(guidelines):
            with mock.patch("builtins.print"):
                return ContentRefinementAgent(
                    self.files["log.md"], self.files["citation_map.json"], guidelines,
                    model_name="m", work_dir=os.path.join(self.tmp, "work"))

        self.assertEqual(writer(CONFERENCE_GUIDELINES).system_prompt, section_writing_agent_prompt)
        self.assertEqual(writer(self.journal).system_prompt,
                         section_writing_agent_prompt + venue_note(PROFILE, "writing"))
        conference = refiner(CONFERENCE_GUIDELINES)
        self.assertEqual(conference.system_prompt, content_refinement_agent_system_prompt)
        self.assertIsNone(conference.venue_profile)
        journal = refiner(self.journal)
        self.assertEqual(journal.system_prompt,
                         content_refinement_agent_system_prompt + venue_note(PROFILE, "refinement"))
        self.assertEqual(journal.venue_profile["short_name"], "JCTC")

    def test_reviewer_and_meta_reviewer(self):
        import autoraters.agent_review as R

        def run(profile):
            seen = []

            def fake_call(prompt, model_name, generation_configs, **kw):
                seen.append(generation_configs["system_instruction"])
                return {"parsed_response": {"Overall": 6}}

            with mock.patch.object(R, "call_llm_with_text_prompt", fake_call), mock.patch("builtins.print"):
                R.perform_review_agentreview("paper", "m", num_review_ensemble=2, venue_profile=profile)
            return seen

        reviewer = R.get_agentreview_system_prompt(is_knowledgeable=True, is_responsible=True, is_benign=True)
        meta = R.meta_reviewer_system_prompt.format(reviewer_count=2)
        self.assertEqual(run(None), [reviewer, reviewer, meta])
        self.assertEqual(run(PROFILE), [reviewer + venue_note(PROFILE, "review")] * 2
                         + [meta + venue_note(PROFILE, "meta_review")])


SECTION = re.compile(r"^\s*\\section\*?\{([^}]*)\}", re.M)


class JournalTemplateTests(unittest.TestCase):
    """Every templates/journal-* folder is complete, consistent and compiles."""

    def test_there_are_journal_templates_and_they_sort_after_the_default(self):
        names = [os.path.basename(d) for d in JOURNAL_DIRS]
        self.assertGreaterEqual(len(names), 1)
        self.assertTrue(
            {"journal-nature", "journal-nature-methods", "journal-nature-biotech"}.issubset(set(names))
        )
        # The desktop app picks the first template alphabetically as the default.
        self.assertTrue(all(n > "cvpr2025" for n in names))

    def test_each_template_is_complete(self):
        for d in JOURNAL_DIRS:
            with self.subTest(template=os.path.basename(d)):
                for name in ("template.tex", "guidelines.md", "references.bib", "venue.json"):
                    self.assertTrue(os.path.isfile(os.path.join(d, name)), name)
                with open(os.path.join(d, "venue.json"), encoding="utf-8") as fh:
                    data = json.load(fh)
                self.assertEqual(set(data), set(REQUIRED_KEYS))
                self.assertEqual(data["kind"], "journal")
                self.assertIs(data["separate_related_work_section"], False)
                self.assertIsNotNone(load_venue_profile(os.path.join(d, "guidelines.md")))
                with open(os.path.join(d, "template.tex"), encoding="utf-8") as fh:
                    tex = fh.read()
                sections = [s.strip() for s in SECTION.findall(tex)]
                self.assertEqual(sections[: len(data["section_order"])], data["section_order"])
                self.assertNotIn("Related Work", sections)
                self.assertIn("A. Researcher", tex)
                with open(os.path.join(d, "guidelines.md"), encoding="utf-8") as fh:
                    guidelines = fh.read()
                self.assertIn("## Important Rules", guidelines)
                self.assertIn("figures/", guidelines)

    @unittest.skipUnless(shutil.which("pdflatex") and shutil.which("bibtex"), "pdflatex/bibtex not installed")
    def test_each_template_compiles_like_a_pipeline_run(self):
        env = dict(os.environ)
        env.pop("TEXINPUTS", None)
        for d in JOURNAL_DIRS:
            with self.subTest(template=os.path.basename(d)):
                work = tempfile.mkdtemp()
                try:
                    shutil.copytree(d, work, dirs_exist_ok=True)
                    os.makedirs(os.path.join(work, "figures"), exist_ok=True)
                    for cmd in (["pdflatex", "-interaction=nonstopmode", "template.tex"],
                                ["bibtex", "template"],
                                ["pdflatex", "-interaction=nonstopmode", "template.tex"],
                                ["pdflatex", "-interaction=nonstopmode", "template.tex"]):
                        subprocess.run(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
                    self.assertTrue(os.path.isfile(os.path.join(work, "template.pdf")))
                    with open(os.path.join(work, "template.log"), encoding="latin-1") as fh:
                        log = fh.read()
                    errors = [line for line in log.splitlines() if line.startswith("!")]
                    self.assertEqual(errors, [])
                finally:
                    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
