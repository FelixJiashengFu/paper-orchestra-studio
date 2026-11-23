"""tools/parse_intake_form.py: the desktop's 「导入填写单」 reads these fields."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import parse_intake_form as P  # noqa: E402

MARKED = """# 填写单（说明文字不会被导入）

<!-- PO:title -->
When Forces Are Not Gradients

<!-- PO:template -->
ACS · JCTC / JPC / JCIM
<!-- PO:cutoff -->
2026年11月
<!-- PO:plotting -->
启用 Plotting Agent · auto · 3
<!-- PO:idea -->
## Working Title

    indented code stays

- item
<!-- PO:log -->
## 0. STATUS: NO EXPERIMENTS HAVE BEEN RUN YET
[[PO:end]]
trailing notes are ignored
"""


class MarkerTests(unittest.TestCase):
    def test_markers(self):
        fields, warnings = P.parse_markers(MARKED)
        self.assertEqual(warnings, [])
        self.assertEqual(fields["title"], "When Forces Are Not Gradients")
        self.assertEqual(fields["template"], "ACS · JCTC / JPC / JCIM")
        self.assertNotIn("researchCutoff", fields)
        self.assertEqual((fields["usePlotting"], fields["plottingMaxCriticRounds"]), (True, 3))
        self.assertEqual(fields["ideaMarkdown"], "## Working Title\n\n    indented code stays\n\n- item")
        self.assertEqual(fields["experimentalLogMarkdown"], "## 0. STATUS: NO EXPERIMENTS HAVE BEEN RUN YET")

    def test_other_marker_spellings_and_unknown_ones(self):
        fields, warnings = P.parse_markers("【PO:研究思路】\nidea\n[[po:LOG]]\nlog\n<!-- PO:colour -->\nx\n")
        self.assertEqual(fields, {"ideaMarkdown": "idea", "experimentalLogMarkdown": "log"})
        self.assertEqual(len(warnings), 1)

    def test_no_markers(self):
        self.assertIsNone(P.parse_markers("## Working Title\ntext"))

    def test_plotting_values(self):
        for text, expected in (
            ("Plotting: yes, rounds: 3, no limit", {"usePlotting": True, "plottingMaxCriticRounds": 3}),
            ("启用，自修 3 轮（1-10）", {"usePlotting": True, "plottingMaxCriticRounds": 3}),
            ("off · 2", {"usePlotting": False, "plottingMaxCriticRounds": 2}),
        ):
            self.assertEqual(P._plotting(text), expected, text)

    def test_plotting_off_and_cutoff_is_skipped_quietly(self):
        fields, warnings = P.parse_markers("<!-- PO:plotting -->\n关闭\n<!-- PO:cutoff -->\nsoon\n")
        self.assertEqual(fields, {"usePlotting": False})
        self.assertEqual(warnings, [])

    def test_form_with_only_a_cutoff_has_no_fields(self):
        self.assertEqual(P.parse_markers("<!-- PO:cutoff -->\n2026-11\n"), ({}, []))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def run_cli(self, path):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "parse_intake_form.py"), path],
                             capture_output=True, text=True, encoding="utf-8")
        return out.returncode, json.loads(out.stdout)

    def test_markdown_file(self):
        with open(self.path("form.md"), "w", encoding="utf-8") as f:
            f.write(MARKED)
        code, result = self.run_cli(self.path("form.md"))
        self.assertEqual(code, 0)
        self.assertEqual(result["layout"], "markers")
        self.assertNotIn("researchCutoff", result["fields"])

    def test_markdown_without_markers_is_an_error(self):
        with open(self.path("idea.md"), "w", encoding="utf-8") as f:
            f.write("## Working Title\n")
        code, result = self.run_cli(self.path("idea.md"))
        self.assertEqual(code, 1)
        self.assertIn("PO", result["error"])

    def test_docx(self):
        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        paras = [("", "[[PO:idea]]"), ("Heading2", "Working Title"), ("", "Body text"), ("", "[[PO:cutoff]]"),
                 ("", "2026-03")]
        body = "".join(
            "<w:p>" + (f'<w:pPr><w:pStyle w:val="{s}"/></w:pPr>' if s else "") + f"<w:r><w:t>{t}</w:t></w:r></w:p>"
            for s, t in paras
        )
        with zipfile.ZipFile(self.path("form.docx"), "w") as z:
            z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
        code, result = self.run_cli(self.path("form.docx"))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["fields"], {"ideaMarkdown": "## Working Title\nBody text"})

    def test_unsupported_and_missing(self):
        with open(self.path("a.rtf"), "w") as f:
            f.write("x")
        self.assertEqual(self.run_cli(self.path("a.rtf"))[0], 1)
        self.assertEqual(self.run_cli(self.path("nope.pdf"))[0], 1)

    def test_pdf_with_markers_keeps_marker_lines_apart(self):
        # A line ending in a space (a soft wrap, or a Markdown hard break) must not
        # swallow the marker that follows it.
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page()
        for k, line in enumerate(["[[PO:idea]]", "text ending with space ", "[[PO:log]]", "log body"]):
            page.insert_text((60, 60 + 12 * k), line, fontsize=10, fontname="cour")
        doc.save(self.path("marked.pdf"))
        code, result = self.run_cli(self.path("marked.pdf"))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["layout"], "markers")
        self.assertEqual(result["fields"], {"ideaMarkdown": "text ending with space", "experimentalLogMarkdown": "log body"})

    def test_numbered_pdf_form(self):
        """The layout of the forms made so far: white badge, heading, grey hint, dark value."""
        import pymupdf

        dark, grey, white = (0.11, 0.14, 0.2), (0.36, 0.39, 0.46), (1, 1, 1)
        doc = pymupdf.open()
        page = doc.new_page()
        y = 60

        def put(text, color, x=60, size=10, gap=16, font="helv"):
            nonlocal y
            page.insert_text((x, y), text, fontsize=size, color=color, fontname=font)
            y += gap

        def block(n, heading, hint, values, size=10):
            put(str(n), white)
            put(heading, dark, x=80, size=11)
            if hint:
                put(hint, grey, size=9)
            for v in values:
                put(v, dark, size=size, font="cour", gap=size + 2)
            put("", dark, gap=14)

        cjk = "china-s"
        put("PaperOrchestra form", dark, size=16)
        put("1", white)
        page.insert_text((80, y), "新建项目 → 项目标题", fontsize=11, color=dark, fontname=cjk)
        y += 16
        page.insert_text((60, y), "点左栏新建，再把标题换成下面这行。", fontsize=9, color=grey, fontname=cjk)
        y += 16
        put("When Forces Are Not Gradients: Auditing Machine-", dark, font="cour")
        put("Learning Potentials", dark, font="cour")
        y += 14
        put("3", white)
        page.insert_text((80, y), "流水线参数 → 文献截止日期（YYYY-MM）", fontsize=11, color=dark, fontname=cjk)
        y += 16
        put("covers Bigi 2025", grey, size=9)
        put("2026-11", dark, font="cour")
        y += 14
        put("7", white)
        page.insert_text((80, y), "研究思路（idea.md）", fontsize=11, color=dark, fontname=cjk)
        y += 16
        for line, gap in (("## Working Title", 11), ("A first paragraph that is long and ", 11),
                          ("wraps here.", 22), ("## Problem", 11), ("Second.", 11)):
            put(line, dark, size=8, font="cour", gap=gap)
        doc.save(self.path("form.pdf"))

        code, result = self.run_cli(self.path("form.pdf"))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["layout"], "pdf-form")
        fields = result["fields"]
        self.assertEqual(fields["title"], "When Forces Are Not Gradients: Auditing Machine-Learning Potentials")
        self.assertNotIn("researchCutoff", fields)
        self.assertEqual(fields["ideaMarkdown"],
                         "## Working Title\nA first paragraph that is long and wraps here.\n\n## Problem\nSecond.")


if __name__ == "__main__":
    unittest.main()
