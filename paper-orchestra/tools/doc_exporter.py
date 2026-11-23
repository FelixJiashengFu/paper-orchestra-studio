#!/usr/bin/env python
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

"""Publication-Grade Document Exporter (Word .docx / PDF / HTML / Google Drive).

Outputs ONE JSON object on stdout:
  python tools/doc_exporter.py google-drive-status
  python tools/doc_exporter.py export --input-md PATH --format docx|pdf|html --out PATH [--title TEXT]
  python tools/doc_exporter.py export-google-drive --input-md PATH --format docx|pdf [--title TEXT] [--drive-dir PATH]
"""

import argparse
from datetime import datetime
import glob
import html
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.providers import install_output_redaction  # noqa: E402

_REAL_STDOUT = sys.stdout
DEFAULT_PDF_OUTPUT_DIR = os.environ.get("PO_DEFAULT_PDF_DIR", "").strip()
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class ExportError(Exception):
    pass


def emit(payload: Dict[str, Any]) -> None:
    target = sys.stdout if sys.stdout is not sys.stderr else _REAL_STDOUT
    print(json.dumps(payload, ensure_ascii=False), file=target, flush=True)


def _find_pandoc() -> Optional[str]:
    for cand in (
        shutil.which("pandoc"),
        "/usr/local/bin/pandoc",
        "/opt/homebrew/bin/pandoc",
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _find_chrome() -> Optional[str]:
    for cand in (
        os.environ.get("PO_CHROME_BIN"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _find_google_drive(explicit_dir: str = "") -> Tuple[str, str]:
    """Returns (drive_root, export_dir) or ("", "") if Google Drive Desktop is not found."""
    if explicit_dir and explicit_dir.strip():
        root = os.path.abspath(os.path.expanduser(explicit_dir.strip()))
        if os.path.isdir(root):
            return root, os.path.join(root, "PaperOrchestra Exports")
        raise ExportError(f"指定的 Google Drive 目录不存在：{root}")

    env_dir = (os.environ.get("PO_GOOGLE_DRIVE_DIR") or "").strip()
    if env_dir:
        root = os.path.abspath(os.path.expanduser(env_dir))
        if os.path.isdir(root):
            return root, os.path.join(root, "PaperOrchestra Exports")

    candidates: List[str] = []
    for base in sorted(glob.glob(os.path.expanduser("~/Library/CloudStorage/GoogleDrive-*"))):
        for sub in ("我的云端硬盘", "My Drive"):
            candidates.append(os.path.join(base, sub))
        candidates.append(base)
    gd_link = os.path.expanduser("~/Google Drive")
    for sub in ("我的云端硬盘", "My Drive"):
        candidates.append(os.path.join(gd_link, sub))
    candidates.append(gd_link)

    for c in candidates:
        if os.path.isdir(c):
            return c, os.path.join(c, "PaperOrchestra Exports")
    return "", ""


def _infer_title(md_text: str, explicit_title: str = "") -> str:
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()
    for line in md_text.splitlines():
        m = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if m:
            return re.sub(r"[`*_]", "", m.group(1)).strip()[:120]
    return "PaperOrchestra 研究报告"


def _sanitize_filename(title: str, max_len: int = 72) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(". ")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].strip(". ")
    return cleaned or "PaperOrchestra_Document"


def _fallback_md_to_html(md_text: str) -> str:
    """Clean stdlib Markdown->HTML fallback when pandoc is unavailable."""
    lines = md_text.splitlines()
    out: List[str] = []
    i = 0

    def _inline(txt: str) -> str:
        esc = html.escape(txt)
        esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
        esc = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", esc)
        esc = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", esc)
        return esc

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            lang = html.escape(stripped[3:].strip())
            code_lines: List[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(code_lines))}</code></pre>")
            continue
        hm = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if hm:
            lvl = len(hm.group(1))
            out.append(f"<h{lvl}>{_inline(hm.group(2))}</h{lvl}>")
            i += 1
            continue
        if re.match(r"^[-*_]{3,}$", stripped):
            out.append("<hr />")
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            headers = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows: List[List[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            ths = "".join(f"<th>{_inline(h)}</th>" for h in headers)
            trs = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r[: len(headers)]) + "</tr>"
                for r in rows
            )
            out.append(f"<table><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>")
            continue
        if stripped.startswith(">"):
            bq: List[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                bq.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f"<blockquote><p>{' '.join(_inline(x) for x in bq)}</p></blockquote>")
            continue
        if re.match(r"^[-*+]\s+", stripped):
            items: List[str] = []
            while i < len(lines) and re.match(r"^\s*[-*+]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*+]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            continue
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ol>")
            continue
        para: List[str] = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|```|\||>|[-*+]\s|\d+\.\s|[-*_]{3,}$)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{' '.join(_inline(x) for x in para)}</p>")
    return "\n".join(out)


def _markdown_to_styled_html(md_text: str, title: str) -> Tuple[str, str]:
    """Converts Markdown to a standalone styled A4 HTML5 document. Returns (html_str, engine)."""
    pandoc_bin = _find_pandoc()
    body_html = ""
    engine = "stdlib"
    if pandoc_bin:
        try:
            proc = subprocess.run(
                [
                    pandoc_bin,
                    "-f",
                    "markdown+tex_math_dollars+pipe_tables+strikeout+task_lists+fenced_code_blocks+backtick_code_blocks+auto_identifiers",
                    "-t",
                    "html5",
                    "--mathml",
                ],
                input=md_text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                body_html = proc.stdout.decode("utf-8", errors="replace")
                engine = "pandoc+html5"
        except Exception:
            body_html = ""

    if not body_html:
        body_html = _fallback_md_to_html(md_text)

    doc_title = html.escape(title or "PaperOrchestra Report")
    styled = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{doc_title}</title>
  <style>
    @page {{
      size: A4;
      margin: 18mm 16mm;
    }}
    * {{
      box-sizing: border-box;
    }}
    html, body {{
      margin: 0;
      padding: 0;
      background: #ffffff;
      color: #1e293b;
      font-family: "PingFang SC", "Hiragino Sans GB", "Songti SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif, "Apple Color Emoji";
      font-size: 10.5pt;
      line-height: 1.72;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }}
    .doc-shell {{
      max-width: 780px;
      margin: 0 auto;
      padding: 20px 26px 36px;
    }}
    @media print {{
      .doc-shell {{
        max-width: none;
        padding: 0;
      }}
    }}
    h1, h2, h3, h4 {{
      color: #0f172a;
      font-weight: 700;
      line-height: 1.35;
      margin-top: 1.35em;
      margin-bottom: 0.55em;
      break-after: avoid;
    }}
    h1 {{
      font-size: 18pt;
      margin-top: 0;
      padding-bottom: 8px;
      border-bottom: 2px solid #e2e8f0;
    }}
    h2 {{
      font-size: 13.5pt;
      padding-bottom: 4px;
      border-bottom: 1px solid #e2e8f0;
    }}
    h3 {{ font-size: 11.5pt; }}
    h4 {{ font-size: 10.5pt; color: #334155; }}
    p {{
      margin: 0.55em 0;
    }}
    ul, ol {{
      margin: 0.5em 0 0.7em;
      padding-left: 1.6em;
    }}
    li {{
      margin: 0.25em 0;
    }}
    blockquote {{
      margin: 0.85em 0;
      padding: 9px 14px;
      border-left: 3.5px solid #2563eb;
      background: #f8fafc;
      border-radius: 0 6px 6px 0;
      color: #334155;
    }}
    blockquote p:first-child {{ margin-top: 0; }}
    blockquote p:last-child {{ margin-bottom: 0; }}
    hr {{
      border: 0;
      border-top: 1px solid #cbd5e1;
      margin: 1.2em 0;
    }}
    code {{
      font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
      font-size: 0.9em;
      background: #f1f5f9;
      color: #0f172a;
      padding: 1.5px 5px;
      border-radius: 4px;
    }}
    pre {{
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 6px;
      padding: 10px 12px;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 9pt;
      line-height: 1.5;
      break-inside: avoid;
    }}
    pre code {{
      background: transparent;
      padding: 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 0.9em 0;
      font-size: 9.2pt;
      line-height: 1.5;
    }}
    thead {{
      display: table-header-group;
    }}
    tr {{
      break-inside: avoid;
    }}
    th, td {{
      border: 1px solid #cbd5e1;
      padding: 6px 9px;
      text-align: left;
      vertical-align: top;
      word-break: break-word;
    }}
    th {{
      background: #f1f5f9;
      color: #0f172a;
      font-weight: 600;
    }}
    tbody tr:nth-child(even) td {{
      background: #f8fafc;
    }}
    math {{
      font-size: 1.02em;
    }}
    a {{
      color: #2563eb;
      text-decoration: none;
    }}
  </style>
</head>
<body>
  <main class="doc-shell">
    {body_html}
  </main>
</body>
</html>
"""
    return styled, engine


def _register_xml_namespaces(raw_xml: bytes) -> None:
    try:
        for _event, (prefix, uri) in ET.iterparse(io.BytesIO(raw_xml), events=["start-ns"]):
            ET.register_namespace(prefix, uri)
    except Exception:
        pass
    ET.register_namespace("w", _W_NS)


def _wtag(local: str) -> str:
    return f"{{{_W_NS}}}{local}"


def _polish_docx_xml(docx_path: str) -> None:
    """Adds crisp grid borders, header shading, and CJK fonts to a pandoc-generated .docx."""
    try:
        with zipfile.ZipFile(docx_path, "r") as zin:
            entries = {name: zin.read(name) for name in zin.namelist()}
    except Exception:
        return

    changed = False
    if "word/document.xml" in entries:
        raw_doc = entries["word/document.xml"]
        _register_xml_namespaces(raw_doc)
        try:
            root = ET.fromstring(raw_doc)
            for tbl in root.iter(_wtag("tbl")):
                tbl_pr = tbl.find(_wtag("tblPr"))
                if tbl_pr is None:
                    tbl_pr = ET.Element(_wtag("tblPr"))
                    tbl.insert(0, tbl_pr)
                if tbl_pr.find(_wtag("tblW")) is None:
                    tbl_w = ET.SubElement(tbl_pr, _wtag("tblW"))
                    tbl_w.set(_wtag("w"), "5000")
                    tbl_w.set(_wtag("type"), "pct")
                existing_borders = tbl_pr.find(_wtag("tblBorders"))
                if existing_borders is not None:
                    tbl_pr.remove(existing_borders)
                borders = ET.SubElement(tbl_pr, _wtag("tblBorders"))
                for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                    el = ET.SubElement(borders, _wtag(side))
                    el.set(_wtag("val"), "single")
                    el.set(_wtag("sz"), "4")
                    el.set(_wtag("space"), "0")
                    el.set(_wtag("color"), "CBD5E1")
                if tbl_pr.find(_wtag("tblCellMar")) is None:
                    mar = ET.SubElement(tbl_pr, _wtag("tblCellMar"))
                    for side, val in (("top", "80"), ("bottom", "80"), ("left", "120"), ("right", "120")):
                        m_el = ET.SubElement(mar, _wtag(side))
                        m_el.set(_wtag("w"), val)
                        m_el.set(_wtag("type"), "dxa")

                trs = tbl.findall(_wtag("tr"))
                if trs:
                    for tc in trs[0].findall(_wtag("tc")):
                        tc_pr = tc.find(_wtag("tcPr"))
                        if tc_pr is None:
                            tc_pr = ET.Element(_wtag("tcPr"))
                            tc.insert(0, tc_pr)
                        if tc_pr.find(_wtag("shd")) is None:
                            shd = ET.SubElement(tc_pr, _wtag("shd"))
                            shd.set(_wtag("val"), "clear")
                            shd.set(_wtag("color"), "auto")
                            shd.set(_wtag("fill"), "F1F5F9")
            entries["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            changed = True
        except Exception:
            pass

    if "word/styles.xml" in entries:
        raw_styles = entries["word/styles.xml"]
        _register_xml_namespaces(raw_styles)
        try:
            sroot = ET.fromstring(raw_styles)
            doc_def = sroot.find(_wtag("docDefaults"))
            if doc_def is not None:
                rpr_def = doc_def.find(_wtag("rPrDefault"))
                if rpr_def is not None:
                    rpr = rpr_def.find(_wtag("rPr"))
                    if rpr is not None:
                        rfonts = rpr.find(_wtag("rFonts"))
                        if rfonts is None:
                            rfonts = ET.SubElement(rpr, _wtag("rFonts"))
                        rfonts.set(_wtag("eastAsia"), "PingFang SC")
                        if not rfonts.get(_wtag("ascii")):
                            rfonts.set(_wtag("ascii"), "Calibri")
                        if not rfonts.get(_wtag("hAnsi")):
                            rfonts.set(_wtag("hAnsi"), "Calibri")
            entries["word/styles.xml"] = ET.tostring(sroot, encoding="utf-8", xml_declaration=True)
            changed = True
        except Exception:
            pass

    if not changed:
        return
    tmp_zip = docx_path + ".polish.tmp"
    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)
    os.replace(tmp_zip, docx_path)


def _export_docx(md_text: str, out_path: str, title: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    pandoc_bin = _find_pandoc()
    with tempfile.TemporaryDirectory(prefix="po-docx-") as tmpdir:
        tmp_md = os.path.join(tmpdir, "input.md")
        tmp_docx = os.path.join(tmpdir, "out.docx")
        with open(tmp_md, "w", encoding="utf-8") as fh:
            fh.write(md_text)

        if pandoc_bin:
            cmd = [
                pandoc_bin,
                tmp_md,
                "-f",
                "markdown+tex_math_dollars+pipe_tables+strikeout+task_lists+fenced_code_blocks+backtick_code_blocks",
                "-t",
                "docx",
                "--standalone",
                "-o",
                tmp_docx,
            ]
            if title:
                cmd.extend(["--metadata", f"title={title}"])
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            if proc.returncode == 0 and os.path.isfile(tmp_docx) and os.path.getsize(tmp_docx) > 0:
                _polish_docx_xml(tmp_docx)
                shutil.copy2(tmp_docx, out_path)
                return "pandoc+ooxml"

        textutil_bin = shutil.which("textutil")
        if textutil_bin:
            styled_html, _ = _markdown_to_styled_html(md_text, title)
            tmp_html = os.path.join(tmpdir, "input.html")
            with open(tmp_html, "w", encoding="utf-8") as fh:
                fh.write(styled_html)
            proc = subprocess.run(
                [textutil_bin, "-convert", "docx", "-output", tmp_docx, tmp_html],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            if proc.returncode == 0 and os.path.isfile(tmp_docx) and os.path.getsize(tmp_docx) > 0:
                _polish_docx_xml(tmp_docx)
                shutil.copy2(tmp_docx, out_path)
                return "textutil"

    raise ExportError("未能找到 pandoc 或 textutil 工具来生成 Word (.docx) 文档。")


def _export_pdf(md_text: str, out_path: str, title: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    styled_html, _ = _markdown_to_styled_html(md_text, title)

    with tempfile.TemporaryDirectory(prefix="po-pdf-") as tmpdir:
        tmp_html = os.path.join(tmpdir, "doc.html")
        tmp_pdf = os.path.join(tmpdir, "doc.pdf")
        with open(tmp_html, "w", encoding="utf-8") as fh:
            fh.write(styled_html)

        chrome_bin = _find_chrome()
        if chrome_bin:
            try:
                cmd = [
                    chrome_bin,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--no-pdf-header-footer",
                    f"--print-to-pdf={tmp_pdf}",
                    "--virtual-time-budget=3000",
                    pathlib.Path(tmp_html).as_uri(),
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                if proc.returncode == 0 and os.path.isfile(tmp_pdf) and os.path.getsize(tmp_pdf) > 0:
                    shutil.copy2(tmp_pdf, out_path)
                    return "chrome-headless-pdf"
            except Exception:
                pass

        # Fallback 2: PyMuPDF HTML Story pagination
        try:
            import pymupdf  # type: ignore

            story = pymupdf.Story(html=styled_html)
            mediabox = pymupdf.paper_rect("a4")
            where = mediabox + (36, 36, -36, -36)
            writer = pymupdf.DocumentWriter(tmp_pdf)
            more = 1
            while more:
                dev = writer.begin_page(mediabox)
                more, _ = story.place(where)
                story.draw(dev, None)
                writer.end_page()
            writer.close()
            if os.path.isfile(tmp_pdf) and os.path.getsize(tmp_pdf) > 0:
                shutil.copy2(tmp_pdf, out_path)
                return "pymupdf-story-pdf"
        except Exception:
            pass

        # Fallback 3: Pandoc + XeLaTeX
        pandoc_bin = _find_pandoc()
        xelatex_bin = shutil.which("xelatex") or ("/Library/TeX/texbin/xelatex" if os.path.isfile("/Library/TeX/texbin/xelatex") else None)
        if pandoc_bin and xelatex_bin:
            tmp_md = os.path.join(tmpdir, "input.md")
            with open(tmp_md, "w", encoding="utf-8") as fh:
                fh.write(md_text)
            proc = subprocess.run(
                [pandoc_bin, tmp_md, "--pdf-engine", xelatex_bin, "-V", "CJKmainfont=Hiragino Sans GB", "-o", tmp_pdf],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=90,
            )
            if proc.returncode == 0 and os.path.isfile(tmp_pdf) and os.path.getsize(tmp_pdf) > 0:
                shutil.copy2(tmp_pdf, out_path)
                return "pandoc+xelatex"

    raise ExportError("未检测到 Chrome、PyMuPDF 或 XeLaTeX，无法生成 PDF。")


def _export_to_path(md_text: str, fmt: str, out_path: str, title: str) -> Tuple[str, int]:
    fmt_low = fmt.lower()
    if fmt_low == "docx":
        engine = _export_docx(md_text, out_path, title)
    elif fmt_low == "pdf":
        engine = _export_pdf(md_text, out_path, title)
    elif fmt_low == "html":
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        styled_html, engine = _markdown_to_styled_html(md_text, title)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(styled_html)
    else:
        raise ExportError(f"不支持的导出格式：{fmt}")
    size = os.path.getsize(out_path) if os.path.isfile(out_path) else 0
    return engine, size


def cmd_google_drive_status(_args: argparse.Namespace) -> Dict[str, Any]:
    drive_root, export_dir = _find_google_drive()
    default_pdf = DEFAULT_PDF_OUTPUT_DIR if (DEFAULT_PDF_OUTPUT_DIR and os.path.isdir(DEFAULT_PDF_OUTPUT_DIR)) else ""
    return {
        "ok": True,
        "available": bool(drive_root),
        "driveRoot": drive_root,
        "exportDir": export_dir,
        "defaultPdfDir": default_pdf,
        "pandocAvailable": bool(_find_pandoc()),
        "chromeAvailable": bool(_find_chrome()),
    }


def cmd_export(args: argparse.Namespace) -> Dict[str, Any]:
    in_path = os.path.abspath(args.input_md)
    if not os.path.isfile(in_path):
        raise ExportError(f"找不到输入 Markdown 文件：{in_path}")
    with open(in_path, "r", encoding="utf-8", errors="replace") as fh:
        md_text = fh.read()
    title = _infer_title(md_text, args.title)
    out_path = os.path.abspath(args.out)
    engine, size = _export_to_path(md_text, args.format, out_path, title)
    return {
        "ok": True,
        "path": out_path,
        "format": args.format,
        "engine": engine,
        "bytes": size,
    }


def cmd_export_google_drive(args: argparse.Namespace) -> Dict[str, Any]:
    in_path = os.path.abspath(args.input_md)
    if not os.path.isfile(in_path):
        raise ExportError(f"找不到输入 Markdown 文件：{in_path}")
    with open(in_path, "r", encoding="utf-8", errors="replace") as fh:
        md_text = fh.read()

    drive_root, export_dir = _find_google_drive(args.drive_dir)
    if not drive_root:
        raise ExportError("未在本机检测到 Google Drive 同步目录（~/Library/CloudStorage/GoogleDrive-* 或 ~/Google Drive）。")

    os.makedirs(export_dir, exist_ok=True)
    title = _infer_title(md_text, args.title)
    stem = _sanitize_filename(title)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fmt = args.format.lower()
    file_name = f"{stem}_{stamp}.{fmt}"
    dest_path = os.path.join(export_dir, file_name)

    engine, size = _export_to_path(md_text, fmt, dest_path, title)
    return {
        "ok": True,
        "path": dest_path,
        "driveRoot": drive_root,
        "fileName": file_name,
        "format": fmt,
        "engine": engine,
        "bytes": size,
        "searchUrl": f"https://drive.google.com/drive/search?q={quote(file_name)}",
    }


def main(argv: Optional[List[str]] = None) -> int:
    global _REAL_STDOUT
    install_output_redaction()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("google-drive-status")
    p.set_defaults(fn=cmd_google_drive_status)

    p = sub.add_parser("export")
    p.add_argument("--input-md", required=True)
    p.add_argument("--format", choices=["docx", "pdf", "html"], required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--title", default="")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("export-google-drive")
    p.add_argument("--input-md", required=True)
    p.add_argument("--format", choices=["docx", "pdf"], default="docx")
    p.add_argument("--title", default="")
    p.add_argument("--drive-dir", default="")
    p.set_defaults(fn=cmd_export_google_drive)

    args = ap.parse_args(argv)
    orig_stdout = sys.stdout
    _REAL_STDOUT = orig_stdout
    try:
        sys.stdout = sys.stderr
        res = args.fn(args)
    except ExportError as exc:
        res = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        res = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        sys.stdout = orig_stdout
    emit(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
