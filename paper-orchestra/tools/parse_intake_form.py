#!/usr/bin/env python
"""Reads a PaperOrchestra intake form (填写单) and prints its fields as JSON.

Used by the desktop app's 「导入填写单」 button: the fields are shown for
review and only written to the project after the user confirms.

Accepted files: .pdf, .md / .markdown / .txt, .docx.

Two layouts are understood:

1. Marker lines (any file type; the stable format for generated forms). Each
   field starts at a line holding only a marker and runs to the next marker:

       <!-- PO:title -->         also [[PO:title]] or 【PO:title】
       When Forces Are Not Gradients: ...
       <!-- PO:template -->
       ACS · JCTC / JPC / JCIM   (a template name or id)
       <!-- PO:cutoff -->         (old forms only: recognised and skipped, since
       2026-11                    every run now uses the month it starts in)
       <!-- PO:plotting -->
       启用 · 3                  (on/off and the critic rounds, both optional)
       <!-- PO:idea -->
       ## Working Title ...      (Markdown, copied verbatim)
       <!-- PO:log -->
       ## 0. STATUS ...
       <!-- PO:end -->

   HTML comments vanish when Markdown is rendered or printed, so a PDF made
   from HTML should print the [[PO:...]] form as visible text.

2. The numbered PDF form (badge number, a heading such as
   「新建项目 → 项目标题」, grey hint lines, then the value in dark text). The
   value is told apart from the hint by its text colour.

Output: {"fields": {...}, "layout": "markers" | "pdf-form", "warnings": [...]}
with camelCase keys title, template, usePlotting,
plottingMaxCriticRounds, ideaMarkdown, experimentalLogMarkdown (only those
found), or {"error": "..."} with exit code 1.
"""

import json
import os
import re
import sys
import zipfile
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

MAX_BYTES = 30 * 1024 * 1024

_MARKER = re.compile(
    r"^\s*(?:<!--\s*)?(?:\[\[|【|\[)?\s*PO\s*[:：]\s*([A-Za-z_一-鿿]+)\s*(?:\]\]|】|\])?\s*(?:-->)?\s*$",
    re.IGNORECASE,
)

_FIELD_NAMES = {
    "title": "title", "标题": "title", "项目标题": "title",
    "template": "template", "模板": "template",
    # The literature cutoff is no longer a project setting (each run uses the
    # month it starts in). Old forms still carry it, so it is skipped quietly.
    "cutoff": "", "research_cutoff": "", "截止": "", "截止日期": "", "文献截止日期": "",
    "plotting": "plotting", "绘图": "plotting",
    "idea": "ideaMarkdown", "研究思路": "ideaMarkdown",
    "log": "experimentalLogMarkdown", "experimental_log": "experimentalLogMarkdown",
    "实验记录": "experimentalLogMarkdown",
    "end": "", "结束": "",
}

_MULTILINE = {"ideaMarkdown", "experimentalLogMarkdown"}


# ---------------------------------------------------------------------------
# Field values
# ---------------------------------------------------------------------------


def _clean_block(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(line.rstrip() for line in lines)


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()




_PLOT_ON = re.compile(r"启用|开启|打开|\b(?:on|yes|enabled?|true)\b", re.I)
_PLOT_OFF = re.compile(r"关闭|不启用|停用|不用|\b(?:off|no|disabled?|false)\b", re.I)


def _plotting(text: str) -> Dict[str, object]:
    """「启用 Plotting Agent · auto · auto · 3」 → on, 3 rounds.

    On/off is whichever word comes first; the rounds are the number next to
    轮 / rounds, else the last stand-alone number from 1 to 10 outside brackets.
    """
    out: Dict[str, object] = {}
    on, off = _PLOT_ON.search(text), _PLOT_OFF.search(text)
    if on or off:
        out["usePlotting"] = bool(on) and (not off or on.start() < off.start())
    m = re.search(r"(\d{1,2})\s*(?:轮|次|rounds?\b)|(?:轮次|轮数|rounds?)\s*[:：=]?\s*(\d{1,2})", text, re.I)
    if m:
        n = int(m.group(1) or m.group(2))
    else:
        bare = re.sub(r"[（(][^）)]*[）)]", " ", text)
        nums = [int(x) for x in re.findall(r"(?<![\d.\-])(\d{1,2})(?![\d.\-])", bare)]
        n = nums[-1] if nums else 0
    if 1 <= n <= 10:
        out["plottingMaxCriticRounds"] = n
    return out


def _store(fields: Dict[str, object], warnings: List[str], key: str, raw: str) -> None:
    if key in _MULTILINE:
        value = _clean_block(raw)
        if value:
            fields[key] = value
    elif key == "title":
        value = _one_line(raw)
        if value:
            fields["title"] = value
    elif key == "template":
        value = _one_line(raw)
        if value:
            fields["template"] = value
    elif key == "plotting":
        fields.update(_plotting(raw))


# ---------------------------------------------------------------------------
# Marker layout
# ---------------------------------------------------------------------------


def parse_markers(text: str) -> Optional[Tuple[Dict[str, object], List[str]]]:
    """Fields between marker lines, or None when the text has no markers."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: List[Tuple[str, List[str]]] = []
    current: Optional[Tuple[str, List[str]]] = None
    warnings: List[str] = []
    found = False
    for line in lines:
        m = _MARKER.match(line)
        if m:
            name = m.group(1).strip().lower()
            key = _FIELD_NAMES.get(name, _FIELD_NAMES.get(m.group(1).strip()))
            if key is None:
                warnings.append(f"不认识的标记 PO:{m.group(1)}，这一段已跳过。")
                current = None
                continue
            found = True
            current = (key, []) if key else None
            if current:
                sections.append(current)
            continue
        if current is not None:
            current[1].append(line)
    if not found:
        return None
    fields: Dict[str, object] = {}
    for key, body in sections:
        _store(fields, warnings, key, "\n".join(body))
    return fields, warnings


# ---------------------------------------------------------------------------
# File readers
# ---------------------------------------------------------------------------


def read_text_file(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_docx(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    out: List[str] = []
    for p in root.iter(f"{_W}p"):
        style = p.find(f"{_W}pPr/{_W}pStyle")
        level = 0
        if style is not None:
            m = re.match(r"(?i)heading\s*(\d)|标题\s*(\d)", style.get(f"{_W}val", ""))
            if m:
                level = int(m.group(1) or m.group(2))
        parts: List[str] = []
        for node in p.iter():
            if node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        text = "".join(parts)
        out.append(("#" * level + " " + text) if level and text.strip() else text)
    return "\n".join(out)


def _pdf_lines(path: str) -> List[dict]:
    import pymupdf  # PyMuPDF, already a dependency (utils/pdf_utils.py)

    lines: List[dict] = []
    with pymupdf.open(path) as doc:
        for page_no, page in enumerate(doc):
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    spans = [s for s in line.get("spans", []) if s.get("text")]
                    if not spans:
                        continue
                    text = "".join(s["text"] for s in spans)
                    if not text.strip():
                        continue
                    main = max(spans, key=lambda s: len(s["text"].strip()))
                    lines.append({
                        "text": text,
                        "color": main.get("color", 0),
                        "size": round(main.get("size", 0), 1),
                        "x0": line["bbox"][0],
                        "x1": line["bbox"][2],
                        "y0": line["bbox"][1],
                        "y1": line["bbox"][3],
                        "page": page_no,
                    })
    return lines


def _join_wrapped(lines: List[dict]) -> str:
    """Joins lines a PDF wrapped (pre-wrap leaves a trailing space at a soft break)
    and puts back the blank lines between paragraphs (a taller gap between lines)."""
    if not lines:
        return ""
    right = max(l["x1"] for l in lines)
    heights = sorted(l["y1"] - l["y0"] for l in lines)
    height = heights[len(heights) // 2] or 1.0
    out: List[str] = []
    for i, line in enumerate(lines):
        text = line["text"]
        if i == 0:
            out.append(text)
            continue
        prev = lines[i - 1]
        if _MARKER.match(text) or _MARKER.match(prev["text"]):
            out.append(text)  # a marker line always stands alone
            continue
        if line["page"] == prev["page"] and line["y0"] - prev["y1"] > 0.8 * height:
            out.append("")
            out.append(text)
            continue
        prev_text = out[-1]
        if prev_text.endswith(" ") and prev_text.strip():
            out[-1] = prev_text + text.lstrip()
        elif prev_text.endswith("-") and prev["x1"] >= right - 3 * prev["size"] and text[:1].isalnum():
            out[-1] = prev_text + text.lstrip()
        else:
            out.append(text)
    return "\n".join(l.rstrip() for l in out)


def _is_light(color: int) -> bool:
    r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
    return r > 235 and g > 235 and b > 235


_HEADING_KEYS = [
    ("ideaMarkdown", re.compile(r"研究思路|idea\.md", re.I)),
    ("experimentalLogMarkdown", re.compile(r"实验记录|experimental_log", re.I)),
    ("title", re.compile(r"项目标题|论文标题|标题")),
    ("template", re.compile(r"模板|template", re.I)),
    ("", re.compile(r"截止")),  # old forms: skipped, see _FIELD_NAMES
    ("plotting", re.compile(r"绘图|plotting", re.I)),
]


def parse_pdf_form(lines: List[dict]) -> Tuple[Dict[str, object], List[str]]:
    """The numbered PDF form: badge, heading, grey hints, dark value lines."""
    fields: Dict[str, object] = {}
    warnings: List[str] = []
    i = 0
    n = len(lines)
    blocks: List[Tuple[dict, List[dict]]] = []
    while i < n:
        line = lines[i]
        badge = line["text"].strip()
        if (re.fullmatch(r"\d{1,2}|[✓✔]", badge) and _is_light(line["color"])) and i + 1 < n:
            heading = lines[i + 1]
            body: List[dict] = []
            j = i + 2
            while j < n:
                t = lines[j]["text"].strip()
                if re.fullmatch(r"\d{1,2}|[✓✔]", t) and _is_light(lines[j]["color"]):
                    break
                body.append(lines[j])
                j += 1
            blocks.append((heading, body))
            i = j
        else:
            i += 1
    for heading, body in blocks:
        key = next((k for k, rx in _HEADING_KEYS if rx.search(heading["text"])), None)
        if not key:
            continue
        values = [l for l in body if l["color"] == heading["color"]]
        if not values:
            continue
        if key in fields or (key == "plotting" and "usePlotting" in fields):
            warnings.append(f"「{_one_line(heading['text'])[:30]}」出现了不止一次，用的是第一处。")
            continue
        _store(fields, warnings, key, _join_wrapped(values))
    return fields, warnings


# ---------------------------------------------------------------------------


def parse_file(path: str) -> dict:
    if not os.path.isfile(path):
        raise ValueError("找不到这个文件。")
    if os.path.getsize(path) > MAX_BYTES:
        raise ValueError("文件超过 30 MB，不像是填写单。")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        lines = _pdf_lines(path)
        if not lines:
            raise ValueError("这个 PDF 里读不到文字（可能是扫描件或图片）。")
        marked = parse_markers(_join_wrapped(lines))
        if marked is not None:
            fields, warnings = marked
            layout = "markers"
        else:
            fields, warnings = parse_pdf_form(lines)
            layout = "pdf-form"
    elif ext in (".md", ".markdown", ".txt"):
        marked = parse_markers(read_text_file(path))
        if marked is None:
            raise ValueError("没找到 PO 标记行（如 <!-- PO:idea -->），认不出每段该填到哪一栏。")
        fields, warnings = marked
        layout = "markers"
    elif ext == ".docx":
        marked = parse_markers(read_docx(path))
        if marked is None:
            raise ValueError("没找到 PO 标记行（如 [[PO:idea]]），认不出每段该填到哪一栏。")
        fields, warnings = marked
        layout = "markers"
    else:
        raise ValueError("只支持 PDF、Markdown（.md）、纯文本（.txt）和 Word（.docx）。")
    if not fields:
        raise ValueError("文件里没认出任何栏位。")
    return {"fields": fields, "layout": layout, "warnings": warnings}


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"error": "用法：parse_intake_form.py <文件>"}, ensure_ascii=False))
        return 1
    try:
        result = parse_file(argv[1])
    except zipfile.BadZipFile:
        result = {"error": "这个 .docx 文件打不开（可能已损坏）。"}
    except ValueError as e:
        result = {"error": str(e)}
    except Exception as e:  # never a traceback in the UI
        result = {"error": f"读取失败：{type(e).__name__}: {e}"}
    print(json.dumps(result, ensure_ascii=False))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
