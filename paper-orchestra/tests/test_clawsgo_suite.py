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
"""Unit tests for the ClawsGO Science parity suite in PaperOrchestra."""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TOOLS_DIR = os.path.join(ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

os.environ.setdefault("PO_PRICING_ONLINE", "0")

from utils import pipeline_state as ps  # noqa: E402
from utils import usage_tracker as ut  # noqa: E402
from tools import doc_exporter as de  # noqa: E402
from tools import figure_studio as fs  # noqa: E402
from tools import latex_copilot as lc  # noqa: E402
from tools import library_tools as lt  # noqa: E402
from tools import research_studio as rs  # noqa: E402
from tools import submission_tools as st  # noqa: E402


class PipelineStateAndResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="po-test-resume-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_effective_idea_path_preference_injection(self):
        materials = os.path.join(self.tmp, "raw_materials")
        os.makedirs(materials, exist_ok=True)
        idea_path = os.path.join(materials, "idea_dense.md")
        with open(idea_path, "w", encoding="utf-8") as fh:
            fh.write("# Original Idea\nExplore catalyst activity.\n")

        # No research_preferences.md -> exact path untouched
        self.assertEqual(ps.effective_idea_path(self.tmp, materials, idea_path), idea_path)

        # Whitespace-only research_preferences.md -> exact path untouched
        prefs_path = os.path.join(materials, "research_preferences.md")
        with open(prefs_path, "w", encoding="utf-8") as fh:
            fh.write("   \n  \n")
        self.assertEqual(ps.effective_idea_path(self.tmp, materials, idea_path), idea_path)

        # Non-empty research_preferences.md -> injects preferences section
        with open(prefs_path, "w", encoding="utf-8") as fh:
            fh.write("- Always report 95% confidence intervals.\n- Use okabe-ito palette.\n")
        combined = ps.effective_idea_path(self.tmp, materials, idea_path)
        self.assertNotEqual(combined, idea_path)
        self.assertTrue(os.path.isfile(combined))
        with open(combined, "r", encoding="utf-8") as fh:
            txt = fh.read()
        self.assertIn("# Original Idea", txt)
        self.assertIn("Persistent Research Preferences & Long-Term Memory", txt)
        self.assertIn("Always report 95% confidence intervals.", txt)

    def test_write_single_paper_resume_skips_completed_checkpoints_and_writes_state(self):
        from methods import paper_writer_with_plotting as pwp

        tpl_dir = os.path.join(self.tmp, "tpl")
        os.makedirs(tpl_dir, exist_ok=True)
        with open(os.path.join(tpl_dir, "template.tex"), "w", encoding="utf-8") as fh:
            fh.write("\\documentclass{article}\n\\begin{document}\nPlaceholder\n\\end{document}\n")
        with open(os.path.join(tpl_dir, "guidelines.md"), "w", encoding="utf-8") as fh:
            fh.write("# Guidelines\nFollow structure.\n")
        with open(os.path.join(tpl_dir, "references.bib"), "w", encoding="utf-8") as fh:
            fh.write("@misc{a, title={Ref A}, year={2024}}\n")

        run_dir = os.path.join(self.tmp, "run")
        materials = os.path.join(run_dir, "raw_materials")
        os.makedirs(materials, exist_ok=True)
        with open(os.path.join(materials, "idea_dense.md"), "w", encoding="utf-8") as fh:
            fh.write("# Idea\nTest checkpoint resumption.\n")
        with open(os.path.join(materials, "experimental_log.md"), "w", encoding="utf-8") as fh:
            fh.write("# Log\nn=10, accuracy=0.95\n")
        with open(os.path.join(materials, "research_preferences.md"), "w", encoding="utf-8") as fh:
            fh.write("- Prefer concise tables.\n")

        # Populate checkpoints
        with open(os.path.join(run_dir, "outline.json"), "w", encoding="utf-8") as fh:
            json.dump({"title": "Resumed Paper", "sections": ["Introduction"]}, fh)

        lit_dir = os.path.join(run_dir, "literature_agent_output")
        os.makedirs(lit_dir, exist_ok=True)
        with open(os.path.join(lit_dir, "citation_map.json"), "w", encoding="utf-8") as fh:
            json.dump({"Introduction": []}, fh)
        with open(os.path.join(lit_dir, "references.bib"), "w", encoding="utf-8") as fh:
            fh.write("@misc{a, title={Ref A}, year={2024}}\n")
        with open(os.path.join(lit_dir, "outline_v1.json"), "w", encoding="utf-8") as fh:
            json.dump({"title": "Resumed Paper"}, fh)
        with open(os.path.join(lit_dir, "updated_template.tex"), "w", encoding="utf-8") as fh:
            fh.write("\\documentclass{article}\n\\begin{document}\n\\cite{a}\\bibliography{references}\n\\end{document}\n")

        with open(os.path.join(run_dir, "plotting_results.json"), "w", encoding="utf-8") as fh:
            json.dump([], fh)

        lw_dir = os.path.join(run_dir, "latex_writeup")
        os.makedirs(lw_dir, exist_ok=True)
        with open(os.path.join(lw_dir, "raw_draft_paper.tex"), "w", encoding="utf-8") as fh:
            fh.write(
                "\\documentclass{article}\n\\begin{document}\n"
                + ("Resumed manuscript body text verified. " * 10)
                + "\\cite{a}\n\\bibliographystyle{plain}\n\\bibliography{references}\n\\end{document}\n"
            )

        class FakeRefinementAgent:
            def __init__(self, **kwargs):
                self.work_dir = kwargs["work_dir"]

            def run(self, texfile_path):
                out_tex = os.path.join(self.work_dir, "final_refined_paper.tex")
                out_pdf = os.path.join(self.work_dir, "final_refined_paper.pdf")
                with open(out_tex, "w", encoding="utf-8") as fh:
                    fh.write("\\documentclass{article}\n\\begin{document}\nFinal \\cite{a}\n\\bibliography{references}\n\\end{document}\n")
                with open(os.path.join(self.work_dir, "references.bib"), "w", encoding="utf-8") as fh:
                    fh.write("@misc{a, title={Ref A}, year={2024}}\n")
                with open(out_pdf, "wb") as fh:
                    fh.write(b"%PDF-1.4\n%Mock PDF\n")
                return out_pdf

        outline_mock = mock.MagicMock(side_effect=AssertionError("OutlineAgent should be skipped on resume"))
        lit_mock = mock.MagicMock(side_effect=AssertionError("HybridLiteratureAgent should be skipped on resume"))
        plot_mock = mock.MagicMock(side_effect=AssertionError("PlottingAgent should be skipped on resume"))
        write_mock = mock.MagicMock(side_effect=AssertionError("SectionWritingAgent should be skipped on resume"))

        with mock.patch.object(pwp, "OutlineAgent", outline_mock), \
                mock.patch.object(pwp, "HybridLiteratureAgent", lit_mock), \
                mock.patch.object(pwp, "PlottingAgent", plot_mock), \
                mock.patch.object(pwp, "SectionWritingAgent", write_mock), \
                mock.patch.object(pwp, "ContentRefinementAgent", FakeRefinementAgent), \
                mock.patch("builtins.print"):
            ok = pwp.write_single_paper(
                base_dir=run_dir,
                latex_template_dir=tpl_dir,
                resume=True,
            )

        self.assertIs(ok, True)
        outline_mock.assert_not_called()
        lit_mock.assert_not_called()
        plot_mock.assert_not_called()
        write_mock.assert_not_called()

        self.assertTrue(os.path.isfile(os.path.join(run_dir, "final_paper.pdf")))
        self.assertTrue(os.path.isfile(os.path.join(run_dir, ".prefs_idea_combined.md")))

        with open(os.path.join(run_dir, "pipeline_state.json"), "r", encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertIs(state["resumed"], True)
        by_id = {n["id"]: n["status"] for n in state["nodes"]}
        for nid in ("outline", "literature", "plotting", "writing", "refinement", "compile"):
            self.assertEqual(by_id[nid], "completed", nid)


class FigureStudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="po-test-fig-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ensure_bundle_list_revise_and_restore(self):
        figs_dir = os.path.join(self.tmp, "figures")
        os.makedirs(figs_dir, exist_ok=True)
        img_path = os.path.join(figs_dir, "chart_main.png")
        im = Image.new("RGB", (320, 200), color=(245, 248, 250))
        im.save(img_path)
        with open(img_path, "rb") as fh:
            v1_bytes = fh.read()

        code_path = os.path.join(self.tmp, "orig_plot.py")
        with open(code_path, "w", encoding="utf-8") as fh:
            fh.write(
                "import matplotlib.pyplot as plt\n"
                "fig, ax = plt.subplots(figsize=(4, 3))\n"
                "ax.plot([1, 2, 3], [2, 4, 6])\n"
            )

        ensured = fs.cmd_ensure_bundle(
            argparse.Namespace(
                dir=self.tmp,
                image=img_path,
                title="Main Result Curve",
                code_file=code_path,
                description="Initial curve",
            )
        )
        self.assertIs(ensured["ok"], True)
        bundle = ensured["bundle"]
        self.assertEqual(bundle["activeVersion"], "v1")
        bundle_dir = bundle["bundleDir"]
        self.assertTrue(os.path.isfile(os.path.join(bundle_dir, "v1", "plot.py")))

        listed = fs.cmd_list(argparse.Namespace(dir=self.tmp))
        self.assertIs(listed["ok"], True)
        self.assertEqual(len(listed["bundles"]), 1)
        self.assertEqual(listed["standalone"], [])

        captured_parts = []

        def fake_multimodal(parts, **kwargs):
            captured_parts.extend(parts)
            return {
                "parsed_response": (
                    "```python\n"
                    "import matplotlib.pyplot as plt\n"
                    "fig, ax = plt.subplots(figsize=(4, 3))\n"
                    "ax.bar(['A', 'B', 'C'], [3, 5, 8], color='#991b1b')\n"
                    "ax.set_title('Revised Red Bar Chart')\n"
                    "plt.tight_layout()\n"
                    "```"
                )
            }

        with mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=True), \
                mock.patch("utils.llm_backend_utils.resolve_task_model", return_value="gpt-4o"), \
                mock.patch("utils.llm_backend_utils.supports_vision", return_value=True), \
                mock.patch("utils.llm_backend_utils.call_llm_with_multimodal_parts", side_effect=fake_multimodal):
            revised = fs.cmd_revise(
                argparse.Namespace(
                    bundle=bundle_dir,
                    instruction="Switch to dark red bars",
                    bbox="0.1,0.1,0.5,0.5",
                    model="auto",
                    image_model="auto",
                    base_dir=self.tmp,
                )
            )

        self.assertIs(revised["ok"], True)
        self.assertEqual(revised["bundle"]["activeVersion"], "v2")
        self.assertEqual(len(revised["bundle"]["versions"]), 2)
        self.assertTrue(os.path.isfile(os.path.join(bundle_dir, "v2", "plot.py")))

        # Verify red bbox overlay was actually drawn and sent to the vision model
        img_parts = [p for p in captured_parts if p.get("type") == "image_bytes"]
        self.assertEqual(len(img_parts), 1)
        with Image.open(io.BytesIO(img_parts[0]["data"])) as overlay_im:
            # Top-left bbox corner around (32, 20) should contain red stroke (220, 38, 38)
            px = overlay_im.getpixel((32, 20))
            self.assertGreater(px[0], 180)
            self.assertLess(px[1], 80)

        # Target image should have been overwritten by v2 rendering
        with open(img_path, "rb") as fh:
            self.assertNotEqual(fh.read(), v1_bytes)

        # Restore back to v1
        restored = fs.cmd_restore(
            argparse.Namespace(bundle=bundle_dir, version="v1", base_dir=self.tmp)
        )
        self.assertIs(restored["ok"], True)
        self.assertEqual(restored["bundle"]["activeVersion"], "v1")
        with open(img_path, "rb") as fh:
            self.assertEqual(fh.read(), v1_bytes)

    def test_create_schematic_svg(self):
        svg_payload = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 120">'
            '<rect width="240" height="120" fill="#ffffff"/>'
            '<rect x="20" y="30" width="80" height="50" rx="8" fill="#e0f2fe" stroke="#0369a1"/>'
            '<text x="35" y="60" font-size="14">Encoder</text>'
            "</svg>"
        )
        with mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=True), \
                mock.patch("utils.llm_backend_utils.resolve_task_model", return_value="gpt-4o"), \
                mock.patch("utils.llm_backend_utils.supports_vision", return_value=False), \
                mock.patch(
                    "utils.llm_backend_utils.call_llm_with_multimodal_parts",
                    return_value={"parsed_response": svg_payload},
                ):
            res = fs.cmd_create(
                argparse.Namespace(
                    out_dir=self.tmp,
                    mode="schematic_svg",
                    prompt="Draw a clean encoder diagram",
                    title="Encoder Diagram",
                    data_file="",
                    ref_image="",
                    model="auto",
                    image_model="auto",
                )
            )
        self.assertIs(res["ok"], True)
        bundle = res["bundle"]
        self.assertTrue(bundle["activeSvgPath"].endswith(".svg"))
        self.assertTrue(os.path.isfile(bundle["activeSvgPath"]))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "figures", "encoder_diagram.svg")))


class ResearchStudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="po-test-rs-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_sample_csv(self) -> str:
        path = os.path.join(self.tmp, "dataset.csv")
        rows = ["id,group,dose,score"]
        for i in range(1, 21):
            missing = "" if i <= 7 else f"{i * 1.5:.1f}"
            val = 10000.0 if i == 20 else round(2.0 * i + (i % 3), 2)
            rows.append(f"{i},{'T' if i % 2 == 0 else 'C'},{missing},{val}")
        # Add 1 exact duplicate of row 1
        rows.append(rows[1])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(rows) + "\n")
        return path

    def test_data_profile_csv_stats_and_warnings(self):
        csv_path = self._write_sample_csv()
        prof = rs.cmd_data_profile(argparse.Namespace(file=csv_path))
        self.assertIs(prof["ok"], True)
        self.assertEqual(prof["rows"], 21)
        self.assertEqual(prof["cols"], 4)
        self.assertEqual(prof["duplicateRows"], 1)
        warns = "\n".join(prof["qualityWarnings"])
        self.assertIn("重复行", warns)
        self.assertIn("缺失率", warns)
        self.assertIn("离群点", warns)

    def test_run_data_analysis_self_fixing_loop_and_handoff(self):
        csv_path = self._write_sample_csv()
        out_dir = os.path.join(self.tmp, "analysis_out")

        bad_script = "```python\nx = 1 / 0\n```"
        good_script = """```python
import json, os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

df = pd.read_csv("raw_data_normalized.csv")
before = len(df)
df_clean = df.drop_duplicates()
cleaning_log = [{"step": "deduplicate", "rule": "exact row match", "rowsAffected": int(before - len(df_clean)), "reversible": True}]
with open("cleaning_log.json", "w") as f:
    json.dump(cleaning_log, f)

stats = {
    "n": int(len(df_clean)),
    "mean_score": round(float(df_clean["score"].mean()), 4),
    "ci95_low": round(float(df_clean["score"].mean() - 10.0), 4),
    "ci95_high": round(float(df_clean["score"].mean() + 10.0), 4),
}
with open("stats_results.json", "w") as f:
    json.dump(stats, f)

os.makedirs("figures", exist_ok=True)
fig, ax = plt.subplots(figsize=(4, 3), dpi=150)
ax.hist(df_clean["score"].clip(upper=100), bins=6, color="#0072B2")
ax.set_title("Score Distribution")
plt.tight_layout()
plt.savefig("figures/score_dist.png", dpi=150)
plt.close("all")
```"""
        report_md = "## 1. 数据概况与可逆清洗记录\n实测均值已写入 stats_results.json。\n"

        with mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=True), \
                mock.patch("utils.llm_backend_utils.resolve_task_model", return_value="gpt-4o-mini"), \
                mock.patch(
                    "utils.llm_backend_utils.call_llm_with_text_prompt",
                    side_effect=[
                        {"parsed_response": bad_script},
                        {"parsed_response": good_script},
                        {"parsed_response": report_md},
                    ],
                ):
            res = rs.cmd_run_data_analysis(
                argparse.Namespace(
                    file=csv_path,
                    question="Compare score distribution",
                    out_dir=out_dir,
                    model="auto",
                )
            )

        self.assertIs(res["ok"], True)
        self.assertEqual(res["attempts"], 2)
        self.assertEqual(res["statsResults"]["n"], 20)
        self.assertEqual(res["cleaningLog"][0]["rowsAffected"], 1)
        self.assertEqual(len(res["figurePaths"]), 1)
        self.assertEqual(len(res["bundles"]), 1)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "report.md")))
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "workspace_handoff.json")))

    def test_run_sop_paper_reading_lit_review_and_math_modeling(self):
        # 1. paper_reading SOP
        paper_md = os.path.join(self.tmp, "paper_notes.md")
        with open(paper_md, "w", encoding="utf-8") as fh:
            fh.write("# Attention Is All You Need\nWe propose the Transformer architecture.\n")
        pr_in = os.path.join(self.tmp, "pr_in.json")
        with open(pr_in, "w", encoding="utf-8") as fh:
            json.dump({"topic": "Transformer internals", "files": [paper_md]}, fh)

        captured_prompts = []

        def fake_llm(prompt, **kwargs):
            captured_prompts.append(prompt)
            return {"parsed_response": "## SOP Output\nStructured synthesis grounded in input."}

        with mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=True), \
                mock.patch("utils.llm_backend_utils.resolve_task_model", return_value="gpt-4o-mini"), \
                mock.patch("utils.llm_backend_utils.call_llm_with_text_prompt", side_effect=fake_llm):
            res_pr = rs.cmd_run_sop(
                argparse.Namespace(
                    sop="paper_reading",
                    input_json=pr_in,
                    out_dir=os.path.join(self.tmp, "sop_pr"),
                    model="auto",
                )
            )
        self.assertIs(res_pr["ok"], True)
        self.assertIn("6-Step Deep Paper Reading SOP", captured_prompts[-1])
        self.assertTrue(os.path.isfile(res_pr["reportPath"]))

        # 2. lit_review SOP
        lr_in = os.path.join(self.tmp, "lr_in.json")
        with open(lr_in, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "topic": "RLHF Alignment",
                    "queryOnline": True,
                    "libraryItems": [
                        {
                            "title": "Training language models to follow instructions",
                            "authors": "Long Ouyang et al.",
                            "year": 2022,
                            "venue": "NeurIPS",
                            "doi": "10.5555/instruct",
                            "abstractText": "InstructGPT aligns LLMs with human feedback.",
                            "readStatus": "full_read",
                            "bibtex": "@inproceedings{ouyang2022instruct,\n  title={InstructGPT},\n  year={2022}\n}",
                        }
                    ],
                    "extra": {"language": "zh", "targetCount": 5},
                },
                fh,
            )
        fake_online = [
            {
                "title": "Direct Preference Optimization",
                "authors": ["Rafael Rafailov"],
                "year": 2023,
                "venue": "NeurIPS",
                "doi": "10.5555/dpo",
                "abstract": "DPO optimizes policies without reward models.",
                "source": "openalex",
            }
        ]
        lr_out = os.path.join(self.tmp, "sop_lr")
        with mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=True), \
                mock.patch("utils.llm_backend_utils.resolve_task_model", return_value="gpt-4o-mini"), \
                mock.patch("utils.scholar_utils.multi_keyword_search", return_value=fake_online), \
                mock.patch("utils.llm_backend_utils.call_llm_with_text_prompt", side_effect=fake_llm):
            res_lr = rs.cmd_run_sop(
                argparse.Namespace(sop="lit_review", input_json=lr_in, out_dir=lr_out, model="auto")
            )
        self.assertIs(res_lr["ok"], True)
        self.assertEqual(res_lr["artifacts"]["fullReadCount"], 1)
        self.assertEqual(res_lr["artifacts"]["abstractOnlyCount"], 1)
        self.assertTrue(os.path.isfile(os.path.join(lr_out, "references.bib")))
        self.assertEqual(res_lr["workspaceHandoff"]["recommendedTemplate"], "journal-chinese-gb7714")

        # 3. math_modeling SOP (runs real pure-NumPy OLS/Ridge/KNN benchmark)
        mm_csv = os.path.join(self.tmp, "mm.csv")
        with open(mm_csv, "w", encoding="utf-8") as fh:
            fh.write("x1,x2,y\n")
            for i in range(1, 26):
                fh.write(f"{i},{i * 0.5},{round(3.0 * i + 0.8 * (i % 4), 2)}\n")
        mm_in = os.path.join(self.tmp, "mm_in.json")
        with open(mm_in, "w", encoding="utf-8") as fh:
            json.dump(
                {"topic": "Predict y from x1, x2", "files": [mm_csv], "extra": {"contest": "mcm"}},
                fh,
            )
        mm_out = os.path.join(self.tmp, "sop_mm")
        with mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=True), \
                mock.patch("utils.llm_backend_utils.resolve_task_model", return_value="gpt-4o-mini"), \
                mock.patch("utils.llm_backend_utils.call_llm_with_text_prompt", side_effect=fake_llm):
            res_mm = rs.cmd_run_sop(
                argparse.Namespace(sop="math_modeling", input_json=mm_in, out_dir=mm_out, model="auto")
            )
        self.assertIs(res_mm["ok"], True)
        bench = res_mm["artifacts"]["mathBenchmark"]
        self.assertEqual(len(bench["models"]), 3)
        self.assertTrue(os.path.isfile(os.path.join(mm_out, "math_benchmark.json")))
        self.assertTrue(os.path.isfile(os.path.join(mm_out, "figures", "model_benchmark.png")))
        self.assertEqual(res_mm["workspaceHandoff"]["recommendedTemplate"], "math-modeling-mcm")

    def test_run_skill_with_prefs_and_attachments(self):
        skill_file = os.path.join(self.tmp, "custom_skill.md")
        with open(skill_file, "w", encoding="utf-8") as fh:
            fh.write("# Rigorous Reviewer Skill\nCheck experimental baselines.\n")
        prefs_file = os.path.join(self.tmp, "prefs.md")
        with open(prefs_file, "w", encoding="utf-8") as fh:
            fh.write("- Flag missing seeds.\n")
        att_file = os.path.join(self.tmp, "draft.txt")
        with open(att_file, "w", encoding="utf-8") as fh:
            fh.write("We report accuracy 91.2%.\n")
        files_json = os.path.join(self.tmp, "files.json")
        with open(files_json, "w", encoding="utf-8") as fh:
            json.dump([att_file], fh)

        seen = []

        def fake_llm(prompt, **kwargs):
            seen.append(prompt)
            return {"parsed_response": "Skill execution complete."}

        out_dir = os.path.join(self.tmp, "skill_out")
        with mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=True), \
                mock.patch("utils.llm_backend_utils.resolve_task_model", return_value="gpt-4o-mini"), \
                mock.patch("utils.llm_backend_utils.call_llm_with_text_prompt", side_effect=fake_llm):
            res = rs.cmd_run_skill(
                argparse.Namespace(
                    skill_file=skill_file,
                    prompt="Review attached draft",
                    out_dir=out_dir,
                    files_json=files_json,
                    prefs_file=prefs_file,
                    model="auto",
                )
            )
        self.assertIs(res["ok"], True)
        self.assertTrue(os.path.isfile(res["outputPath"]))
        self.assertIn("Rigorous Reviewer Skill", seen[0])
        self.assertIn("Flag missing seeds.", seen[0])
        self.assertIn("91.2%", seen[0])


class LibraryToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="po-test-lib-")
        self.items_path = os.path.join(self.tmp, "items.json")
        self.items = [
            {
                "id": "p_recent",
                "title": "Recent Advances in RLHF Agents",
                "authors": "Alice Smith, Bob Lee",
                "year": 2025,
                "venue": "ICLR",
                "doi": "10.1000/recent",
                "tags": ["RLHF", "Agents"],
                "readStatus": "full_read",
                "aiNotes": "Introduces iterative feedback loops.",
            },
            {
                "id": "p_survey",
                "title": "A Survey on RLHF Alignment",
                "authors": "Carol Chen",
                "year": 2023,
                "venue": "CSUR",
                "doi": "10.1000/survey",
                "tags": ["RLHF", "Survey"],
                "readStatus": "abstract_only",
            },
            {
                "id": "p_seminal",
                "title": "Foundations of Reward Modeling",
                "authors": "David Kim, Eve Park, Frank Wu, Grace Liu",
                "year": 2021,
                "venue": "NeurIPS",
                "doi": "10.1000/seminal",
                "tags": ["RLHF"],
                "readStatus": "full_read",
            },
        ]
        with open(self.items_path, "w", encoding="utf-8") as fh:
            json.dump(self.items, fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_graph_offline_fallback(self):
        with mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=False):
            res = lt.cmd_build_graph(argparse.Namespace(input_json=self.items_path, model="auto"))
        self.assertIs(res["ok"], True)
        g = res["graph"]
        self.assertEqual(g["generatedBy"], "offline_structure")
        kinds = {n["kind"] for n in g["nodes"]}
        self.assertEqual(kinds, {"paper", "concept"})
        self.assertGreaterEqual(len(g["edges"]), 2)
        stages = [step["stage"] for step in g["readingOrder"]]
        self.assertEqual(stages, ["综述立框", "经典奠基", "近期前沿"])
        self.assertEqual(len(g["comparisonMatrix"]["rows"]), 3)

    def test_export_bib_bibtex_and_gb7714(self):
        bib_res = lt.cmd_export_bib(argparse.Namespace(input_json=self.items_path, format="bibtex"))
        self.assertIs(bib_res["ok"], True)
        self.assertEqual(bib_res["count"], 3)
        self.assertIn("@article{", bib_res["text"])
        self.assertIn("doi = {10.1000/recent}", bib_res["text"])

        gb_res = lt.cmd_export_bib(argparse.Namespace(input_json=self.items_path, format="gb7714"))
        self.assertIs(gb_res["ok"], True)
        self.assertEqual(gb_res["count"], 3)
        self.assertIn("[1] ", gb_res["text"])
        self.assertIn("et al.", gb_res["text"])
        self.assertIn("[J].", gb_res["text"])


class LatexCopilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="po-test-lc-")
        self.cw = os.path.join(self.tmp, "content_refinement_workdir")
        os.makedirs(self.cw, exist_ok=True)
        self.lit = os.path.join(self.tmp, "literature_agent_output")
        os.makedirs(self.lit, exist_ok=True)

        self.tex_path = os.path.join(self.cw, "final_refined_paper.tex")
        with open(self.tex_path, "w", encoding="utf-8") as fh:
            fh.write(
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Line three is calm.\n"
                "Our method matches prior work \\cite{smith2024, missing2025}.\n"
                "\\bibliography{references}\n"
                "\\end{document}\n"
            )
        with open(os.path.join(self.cw, "references.bib"), "w", encoding="utf-8") as fh:
            fh.write("@article{smith2024,\n  title={Verified Study},\n  author={Smith, J.},\n  year={2024},\n  journal={Nature},\n  doi={10.1038/s41586-024-00001}\n}\n")
        with open(os.path.join(self.lit, "citation_map.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {"Introduction": [{"key": "smith2024", "summary": "Verified experimental finding on catalysts."}]},
                fh,
            )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_trace_citations(self):
        res = lc.cmd_trace_citations(argparse.Namespace(run_dir=self.tmp, tex_file=""))
        self.assertIs(res["ok"], True)
        self.assertEqual(res["count"], 2)
        by_key = {t["key"]: t for t in res["traces"]}
        self.assertEqual(by_key["smith2024"]["status"], "verified")
        self.assertEqual(by_key["smith2024"]["doi"], "10.1038/s41586-024-00001")
        self.assertIn("Verified experimental finding", by_key["smith2024"]["sourceEvidence"])
        self.assertEqual(by_key["missing2025"]["status"], "unverified")
        self.assertEqual(by_key["smith2024"]["line"], 4)

    def test_edit_scoped_and_non_mutating(self):
        with open(self.tex_path, "r", encoding="utf-8") as fh:
            orig_content = fh.read()
        with mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=True), \
                mock.patch("utils.llm_backend_utils.resolve_task_model", return_value="gpt-4o-mini"), \
                mock.patch(
                    "utils.llm_backend_utils.call_llm_with_text_prompt",
                    return_value={"parsed_response": "```latex\n\\begin{document}\nLine three is sharper.\n```"},
                ):
            res = lc.cmd_edit(
                argparse.Namespace(
                    file=self.tex_path,
                    instruction="Sharpen line 3 wording",
                    selection_start=2,
                    selection_end=3,
                    error_log="",
                    model="auto",
                )
            )
        self.assertIs(res["ok"], True)
        self.assertIn("Line three is sharper.", res["revisedContent"])
        self.assertIn("--- final_refined_paper.tex", res["diff"])
        self.assertGreaterEqual(res["changedLines"], 2)
        # Disk file must NOT be mutated by edit preview
        with open(self.tex_path, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), orig_content)


class LeanAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="po-test-lean-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lean_audit_detects_real_holes_and_ignores_comments(self):
        dirty_dir = os.path.join(self.tmp, "dirty")
        os.makedirs(dirty_dir, exist_ok=True)
        with open(os.path.join(dirty_dir, "Main.lean"), "w", encoding="utf-8") as fh:
            fh.write(
                "-- sorry inside line comment should be ignored\n"
                "/- block comment:\n"
                "   axiom fake : False\n"
                "   sorry\n"
                "-/\n"
                "axiom unproved_ax : 1 = 2\n"
                "theorem bad : 2 + 2 = 4 := by\n"
                "  sorry\n"
                "theorem bad2 : True := by\n"
                "  admit\n"
            )
        res_dirty = st.cmd_lean_audit(argparse.Namespace(project_dir=dirty_dir))
        self.assertEqual(res_dirty["kind"], "lean-audit")
        self.assertIs(res_dirty["zeroHoles"], False)
        self.assertEqual(res_dirty["sorryCount"], 1)
        self.assertEqual(res_dirty["axiomCount"], 1)
        self.assertEqual(res_dirty["admitCount"], 1)
        self.assertEqual(res_dirty["unsafeCount"], 0)

        clean_dir = os.path.join(self.tmp, "clean")
        os.makedirs(clean_dir, exist_ok=True)
        with open(os.path.join(clean_dir, "Clean.lean"), "w", encoding="utf-8") as fh:
            fh.write(
                "-- Note: no sorry left\n"
                "/- unsafe block ignored in comment -/\n"
                "theorem good : 1 + 1 = 2 := rfl\n"
            )
        res_clean = st.cmd_lean_audit(argparse.Namespace(project_dir=clean_dir))
        self.assertIs(res_clean["zeroHoles"], True)
        self.assertEqual(res_clean["issues"], [])


class MathModelingTemplatesTests(unittest.TestCase):
    TEMPLATE_NAMES = ("math-modeling-mcm", "math-modeling-cumcm")

    def test_math_modeling_templates_are_complete(self):
        for name in self.TEMPLATE_NAMES:
            d = os.path.join(ROOT, "templates", name)
            with self.subTest(template=name):
                for fn in ("template.tex", "guidelines.md", "references.bib"):
                    self.assertTrue(os.path.isfile(os.path.join(d, fn)), fn)

    @unittest.skipUnless(shutil.which("pdflatex") and shutil.which("bibtex"), "pdflatex/bibtex not installed")
    def test_math_modeling_templates_compile(self):
        env = dict(os.environ)
        env.pop("TEXINPUTS", None)
        for name in self.TEMPLATE_NAMES:
            d = os.path.join(ROOT, "templates", name)
            with self.subTest(template=name):
                work = tempfile.mkdtemp(prefix="po-test-mmtex-")
                try:
                    shutil.copytree(d, work, dirs_exist_ok=True)
                    os.makedirs(os.path.join(work, "figures"), exist_ok=True)
                    for cmd in (
                        ["pdflatex", "-interaction=nonstopmode", "template.tex"],
                        ["bibtex", "template"],
                        ["pdflatex", "-interaction=nonstopmode", "template.tex"],
                        ["pdflatex", "-interaction=nonstopmode", "template.tex"],
                    ):
                        subprocess.run(
                            cmd,
                            cwd=work,
                            env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=180,
                        )
                    self.assertTrue(os.path.isfile(os.path.join(work, "template.pdf")))
                    with open(os.path.join(work, "template.log"), encoding="latin-1") as fh:
                        log = fh.read()
                    errors = [line for line in log.splitlines() if line.startswith("!")]
                    self.assertEqual(errors, [])
                finally:
                    shutil.rmtree(work, ignore_errors=True)


class StudioLiveTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="po-test-telemetry-")

    def tearDown(self):
        ut.end_run()
        ut.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_usage_tracker_listeners_fire_live_and_detach(self):
        snapshots = []

        def on_snap(rep):
            snapshots.append(rep)

        ut.begin_run(self.tmp)
        ut.add_listener(on_snap)
        try:
            ut.record("openai", "gpt-4.1", input_tokens=120, output_tokens=45)
            ut.record("gemini", "gemini-3.1-pro-preview", input_tokens=200, output_tokens=80)
        finally:
            ut.remove_listener(on_snap)
        ut.record("openai", "gpt-4.1", input_tokens=10, output_tokens=5)
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0]["totals"]["input_tokens"], 120)
        self.assertEqual(snapshots[1]["totals"]["input_tokens"], 320)
        self.assertEqual(snapshots[1]["totals"]["output_tokens"], 125)

    def _parse_events(self, stderr_text: str):
        events = []
        for ln in stderr_text.splitlines():
            if ln.startswith(rs._EVENT_PREFIX):
                events.append(json.loads(ln[len(rs._EVENT_PREFIX) :]))
        return events

    def test_research_studio_emits_live_events_and_usage_report(self):
        skill_file = os.path.join(self.tmp, "skill.md")
        with open(skill_file, "w", encoding="utf-8") as fh:
            fh.write("# Skill\nCheck units.\n")
        skill_out = os.path.join(self.tmp, "skill_out")

        def fake_skill_llm(**kwargs):
            ut.record("openai", "gpt-4.1", input_tokens=310, output_tokens=90)
            return {"parsed_response": "Skill check done."}

        buf_err = io.StringIO()
        with mock.patch("sys.stderr", buf_err), \
                mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=True), \
                mock.patch("utils.llm_backend_utils.resolve_task_model", return_value="gpt-4.1"), \
                mock.patch("utils.llm_backend_utils.call_llm_with_text_prompt", side_effect=fake_skill_llm):
            res_skill = rs.cmd_run_skill(
                argparse.Namespace(
                    skill_file=skill_file,
                    prompt="Run telemetry check",
                    out_dir=skill_out,
                    files_json="",
                    prefs_file="",
                    model="auto",
                )
            )

        self.assertEqual(res_skill["usageReport"]["totals"]["input_tokens"], 310)
        self.assertEqual(res_skill["usageReport"]["totals"]["output_tokens"], 90)
        self.assertTrue(os.path.isfile(os.path.join(skill_out, "usage_report.json")))
        evs = self._parse_events(buf_err.getvalue())
        self.assertTrue(any(e.get("kind") == "progress" and e.get("stage") == "synthesize" for e in evs))
        usage_evs = [e for e in evs if e.get("kind") == "usage"]
        self.assertGreaterEqual(len(usage_evs), 2)
        self.assertEqual(usage_evs[-1]["usage"]["totals"]["input_tokens"], 310)

        # SOP flow also persists usageReport in result.json
        md_file = os.path.join(self.tmp, "paper.md")
        with open(md_file, "w", encoding="utf-8") as fh:
            fh.write("# Paper\nMethod section.\n")
        sop_in = os.path.join(self.tmp, "sop_in.json")
        with open(sop_in, "w", encoding="utf-8") as fh:
            json.dump({"topic": "Telemetry SOP", "files": [md_file]}, fh)
        sop_out = os.path.join(self.tmp, "sop_out")
        buf_err2 = io.StringIO()

        def fake_sop_llm(**kwargs):
            ut.record("gemini", "gemini-3.1-pro-preview", input_tokens=450, output_tokens=150)
            return {"parsed_response": "## Overview\nGrounded synthesis."}

        with mock.patch("sys.stderr", buf_err2), \
                mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=True), \
                mock.patch("utils.llm_backend_utils.resolve_task_model", return_value="gemini-3.1-pro-preview"), \
                mock.patch("utils.llm_backend_utils.call_llm_with_text_prompt", side_effect=fake_sop_llm):
            res_sop = rs.cmd_run_sop(
                argparse.Namespace(sop="paper_reading", input_json=sop_in, out_dir=sop_out, model="auto")
            )
        self.assertEqual(res_sop["usageReport"]["totals"]["input_tokens"], 450)
        self.assertEqual(res_sop["usageReport"]["totals"]["output_tokens"], 150)
        with open(os.path.join(sop_out, "result.json"), "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["usageReport"]["totals"]["calls"], 1)

    def test_figure_studio_revise_emits_live_events_and_usage_report(self):
        figs_dir = os.path.join(self.tmp, "figures")
        os.makedirs(figs_dir, exist_ok=True)
        img_path = os.path.join(figs_dir, "panel.png")
        Image.new("RGB", (200, 150), color=(255, 255, 255)).save(img_path)
        code_path = os.path.join(self.tmp, "p.py")
        with open(code_path, "w", encoding="utf-8") as fh:
            fh.write("import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([1, 2], [3, 4])\n")

        ensured = fs.cmd_ensure_bundle(
            argparse.Namespace(dir=self.tmp, image=img_path, title="Panel", code_file=code_path, description="init")
        )
        bundle_dir = ensured["bundle"]["bundleDir"]

        def fake_mm(parts, **kwargs):
            ut.record("openai", "gpt-4o", input_tokens=500, output_tokens=110)
            return {
                "parsed_response": (
                    "```python\nimport matplotlib.pyplot as plt\nfig, ax = plt.subplots()\n"
                    "ax.bar(['X', 'Y'], [2, 5], color='#0072B2')\n```"
                )
            }

        buf_err = io.StringIO()
        with mock.patch("sys.stderr", buf_err), \
                mock.patch("utils.llm_backend_utils.has_any_llm_credentials", return_value=True), \
                mock.patch("utils.llm_backend_utils.resolve_task_model", return_value="gpt-4o"), \
                mock.patch("utils.llm_backend_utils.supports_vision", return_value=True), \
                mock.patch("utils.llm_backend_utils.call_llm_with_multimodal_parts", side_effect=fake_mm):
            rev = fs.cmd_revise(
                argparse.Namespace(
                    bundle=bundle_dir,
                    instruction="Use bars",
                    bbox="",
                    model="auto",
                    image_model="auto",
                    base_dir=self.tmp,
                )
            )
        self.assertEqual(rev["usageReport"]["totals"]["input_tokens"], 500)
        self.assertEqual(rev["usageReport"]["totals"]["output_tokens"], 110)
        self.assertTrue(os.path.isfile(os.path.join(bundle_dir, "usage_report.json")))
        evs = self._parse_events(buf_err.getvalue())
        self.assertTrue(any(e.get("kind") == "progress" and e.get("stage") == "render" for e in evs))
        self.assertTrue(any(e.get("kind") == "usage" and e["usage"]["totals"]["calls"] == 1 for e in evs))


class DocExporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="po-test-export-")
        self.md_path = os.path.join(self.tmp, "report.md")
        with open(self.md_path, "w", encoding="utf-8") as fh:
            fh.write(
                "# 《Onset of subdiffusion》深度精读报告\n\n"
                "> **溯源约定**：本报告中标注为 **【原文】** 的内容直接来自论文。\n\n"
                "## Step 1 | 材料完整性检查\n\n"
                "| 组成部分 | 状态 | 说明 |\n"
                "| --- | --- | --- |\n"
                "| 摘要 | ✅ 完整 | p.1 |\n"
                "| 模型 (Eq. 1-5) | ⚠️ 部分 | 能量密度 $E = mc^2$ 与哈密顿量 |\n\n"
                "$$\nH = \\sum_{i=1}^{N} n_i\n$$\n\n"
                "```python\nprint('ok')\n```\n"
            )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_docx_pdf_html_and_google_drive(self):
        # 1. Word (.docx)
        docx_out = os.path.join(self.tmp, "out.docx")
        res_docx = de.cmd_export(
            argparse.Namespace(input_md=self.md_path, format="docx", out=docx_out, title="测试报告")
        )
        self.assertIs(res_docx["ok"], True)
        self.assertTrue(os.path.isfile(docx_out))
        self.assertGreater(os.path.getsize(docx_out), 1500)
        with zipfile.ZipFile(docx_out, "r") as zf:
            names = set(zf.namelist())
            self.assertIn("word/document.xml", names)
            doc_xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
            self.assertIn("tblBorders", doc_xml)

        # 2. PDF (.pdf)
        pdf_out = os.path.join(self.tmp, "out.pdf")
        res_pdf = de.cmd_export(
            argparse.Namespace(input_md=self.md_path, format="pdf", out=pdf_out, title="测试报告")
        )
        self.assertIs(res_pdf["ok"], True)
        self.assertTrue(os.path.isfile(pdf_out))
        self.assertGreater(os.path.getsize(pdf_out), 2000)
        with open(pdf_out, "rb") as fh:
            self.assertTrue(fh.read(5).startswith(b"%PDF-"))

        # 3. HTML (.html)
        html_out = os.path.join(self.tmp, "out.html")
        res_html = de.cmd_export(
            argparse.Namespace(input_md=self.md_path, format="html", out=html_out, title="测试报告")
        )
        self.assertIs(res_html["ok"], True)
        with open(html_out, "r", encoding="utf-8") as fh:
            h = fh.read()
        self.assertIn("<table", h)
        self.assertIn("测试报告", h)

        # 4. Google Drive export
        drive_dir = os.path.join(self.tmp, "FakeGoogleDrive")
        os.makedirs(drive_dir, exist_ok=True)
        res_gd = de.cmd_export_google_drive(
            argparse.Namespace(
                input_md=self.md_path,
                format="docx",
                title="精读测试",
                drive_dir=drive_dir,
            )
        )
        self.assertIs(res_gd["ok"], True)
        self.assertTrue(os.path.isfile(res_gd["path"]))
        self.assertIn("PaperOrchestra Exports", res_gd["path"])
        self.assertTrue(res_gd["searchUrl"].startswith("https://drive.google.com/drive/search?q="))


if __name__ == "__main__":
    unittest.main()
