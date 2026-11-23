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

"""
Offline citation audit of a finished run (no network).

audit_run(run_dir) compares the keys cited in the final LaTeX source with the
BibTeX database it was compiled against and with the scholar metadata in
literature_agent_output/references_meta.json, then writes

    <run dir>/citation_audit.json   (machine-readable, see audit_run)
    <run dir>/citation_audit.md     (human-readable, Chinese)
    <run dir>/references_cited.bib  (the library reduced to the entries the paper
                                     cites; the compiled bibliography is the same,
                                     since BibTeX prints only cited entries)

The .bib files of the run are never changed: references.bib stays the whole
library the literature stage collected, uncited candidates included.

Entry status:
    verified       has a DOI (sidecar or .bib) or a resolvable source record
                   (sidecar entry with a URL)
    metadata_only  in references_meta.json, but with neither DOI nor URL
    unverified     not in references_meta.json (e.g. typed in by hand or
                   shipped with the template)
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from utils.report_io import atomic_write_json, atomic_write_text, now_iso

JSON_NAME = "citation_audit.json"
MD_NAME = "citation_audit.md"
CITED_BIB_NAME = "references_cited.bib"
META_RELPATH = os.path.join("literature_agent_output", "references_meta.json")

# Final source first: ContentRefinementAgent writes the LaTeX behind
# final_refined_paper.pdf (= final_paper.pdf) to final_refined_paper.tex.
TEX_CANDIDATES = [
    os.path.join("content_refinement_workdir", "final_refined_paper.tex"),
    os.path.join("latex_writeup", "raw_draft_paper.tex"),
    os.path.join("latex_writeup", "template.tex"),
]
BIB_FALLBACKS = [
    os.path.join("latex_writeup", "references.bib"),
    os.path.join("literature_agent_output", "references.bib"),
]

# natbib, plain LaTeX and biblatex citation commands whose mandatory argument is
# a comma list of keys (\citetext / \citestyle / \citeindextrue are not).
_CITE_CMDS = (
    "cite", "citep", "citet", "citealp", "citealt", "citeauthor", "citefullauthor",
    "citepalias", "citetalias",
    "citeyear", "citeyearpar", "citenum", "citeurl", "citetitle", "citedate",
    "Cite", "Citep", "Citet", "Citealp", "Citealt", "Citeauthor",
    "nocite", "parencite", "Parencite", "textcite", "Textcite", "autocite", "Autocite",
    "footcite", "footcitetext", "smartcite", "Smartcite", "supercite", "fullcite",
    # REVTeX (AIP/APS): "Ref.~\onlinecite{k}" prints the number inline.
    "onlinecite",
)
# An optional [..] argument; {..} groups inside may hold brackets: \citep[{see [1]}]{k}.
_OPT_ARG = r"\[(?:[^\]{}]|\{[^{}]*\})*\]"
_CITE_RE = re.compile(
    r"\\(?:" + "|".join(sorted(_CITE_CMDS, key=len, reverse=True)) + r")(?![A-Za-z@])\*?"
    r"\s*(?:" + _OPT_ARG + r"\s*){0,2}\{([^{}]*)\}"
)
# biblatex multicite commands: \cites(pre)(post)[pre][post]{a}[..]{b}...
_MULTI_CITE_CMDS = (
    "cites", "Cites", "parencites", "Parencites", "textcites", "Textcites", "autocites", "Autocites",
    "footcites", "footcitetexts", "smartcites", "Smartcites", "supercites",
)
_MULTI_CITE_RE = re.compile(
    r"\\(?:" + "|".join(sorted(_MULTI_CITE_CMDS, key=len, reverse=True)) + r")(?![A-Za-z@])\*?"
    r"\s*(?:\([^()]*\)\s*){0,2}"
)
_MULTI_CITE_ARG_RE = re.compile(r"\s*(?:" + _OPT_ARG + r"\s*){0,2}\{([^{}]*)\}")
# \url{..} / \path{..} / \href{url}: a "%" inside is part of the URL, not a comment.
_URL_ARG_RE = re.compile(r"(\\(?:url|path|href)\s*)\{[^{}\n]*\}")
_COMMENT_RE = re.compile(r"(?m)(^|[^\\])((?:\\\\)*)%.*$")
_BIBLIO_RE = re.compile(r"\\(?:bibliography|addbibresource|addglobalbib)\s*(?:\[[^\]]*\]\s*)?\{([^{}]*)\}")


# --------------------------------------------------------------------------
# LaTeX
# --------------------------------------------------------------------------


def strip_comments(tex: str) -> str:
    """Drops %-comments; an escaped \\% stays (a % after an even run of backslashes starts a comment)."""
    return _COMMENT_RE.sub(r"\1\2", tex or "")


def _code(tex: str) -> str:
    """LaTeX without comments; URL arguments are emptied first so their "%" stays harmless."""
    return strip_comments(_URL_ARG_RE.sub(r"\1{}", tex or ""))


def _key_lists(code: str):
    """Yields (position, comma list) for every citation command's key argument."""
    for m in _CITE_RE.finditer(code):
        yield m.start(), m.group(1)
    for m in _MULTI_CITE_RE.finditer(code):
        pos = m.end()
        while True:
            arg = _MULTI_CITE_ARG_RE.match(code, pos)
            if not arg:
                break
            yield m.start(), arg.group(1)
            pos = arg.end()


def parse_citations(tex: str) -> Tuple[List[str], bool]:
    """(cited keys in first-seen order, whether \\nocite{*} is present)."""
    keys: List[str] = []
    seen = set()
    star = False
    for _, group in sorted(_key_lists(_code(tex)), key=lambda item: item[0]):
        for raw in group.split(","):
            key = raw.strip()
            if not key or "#" in key:  # "#1": a macro parameter in a \newcommand body
                continue
            if key == "*":
                star = True
                continue
            if key.lower() not in seen:  # one entry for BibTeX, whatever the case
                seen.add(key.lower())
                keys.append(key)
    return keys, star


def bibliography_names(tex: str) -> List[str]:
    names: List[str] = []
    for m in _BIBLIO_RE.finditer(_code(tex)):
        for raw in m.group(1).split(","):
            name = raw.strip()
            if name and name not in names:
                names.append(name)
    return names


# --------------------------------------------------------------------------
# BibTeX
# --------------------------------------------------------------------------

_ENTRY_START = re.compile(r"@\s*([A-Za-z]+)\s*([{(])")
_SKIP_TYPES = {"comment", "string", "preamble"}


def _matching(text: str, start: int, open_ch: str) -> int:
    """Index just past the delimiter closing the one at `start` (or len(text))."""
    close_ch = "}" if open_ch == "{" else ")"
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{" or (open_ch == "(" and ch == "("):
            depth += 1
        elif ch == "}" or (open_ch == "(" and ch == ")"):
            depth -= 1
            if depth == 0 and ch == close_ch:
                return i + 1
        i += 1
    return len(text)


def _parse_fields(body: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    i, n = 0, len(body)
    name_re = re.compile(r"\s*,?\s*([A-Za-z][\w\-:.]*)\s*=\s*")
    while i < n:
        m = name_re.match(body, i)
        if not m:
            nxt = body.find(",", i + 1)
            if nxt == -1:
                break
            i = nxt
            continue
        name = m.group(1).lower()
        j = m.end()
        parts: List[str] = []
        while j < n:  # value: {..} | ".." | bare token, joined with #
            ch = body[j]
            if ch == "{":
                end = _matching(body, j, "{")
                parts.append(body[j + 1:end - 1])
                j = end
            elif ch == '"':
                k = j + 1
                depth = 0
                while k < n and not (body[k] == '"' and depth == 0):
                    if body[k] == "{":
                        depth += 1
                    elif body[k] == "}":
                        depth -= 1
                    elif body[k] == "\\":
                        k += 1
                    k += 1
                parts.append(body[j + 1:k])
                j = k + 1
            else:
                m2 = re.match(r"[^\s,#}]+", body[j:])
                if m2:
                    parts.append(m2.group(0))
                    j += m2.end()
            m3 = re.match(r"\s*#\s*", body[j:])
            if m3:
                j += m3.end()
                continue
            break
        fields.setdefault(name, "".join(parts))
        i = j
    return fields


def parse_bib(text: str) -> Dict[str, Dict[str, str]]:
    """{key: {field: raw value}} in file order; @string/@comment/@preamble are skipped."""
    entries: Dict[str, Dict[str, str]] = {}
    pos = 0
    text = text or ""
    while True:
        m = _ENTRY_START.search(text, pos)
        if not m:
            break
        etype, open_ch = m.group(1).lower(), m.group(2)
        end = _matching(text, m.end() - 1, open_ch)
        pos = max(end, m.end())
        if etype in _SKIP_TYPES:
            continue
        inner = text[m.end():end - 1]
        key, sep, body = inner.partition(",")
        key = key.strip()
        if not key or not sep:
            continue
        fields = _parse_fields(body)
        fields["ENTRYTYPE"] = etype
        entries.setdefault(key, fields)
    return entries


def split_bib(text: str) -> List[Tuple[Optional[str], str, str]]:
    """[(key or None, entry type, raw text)] for every @-block, in file order.

    @string / @preamble / @comment blocks carry key None."""
    blocks: List[Tuple[Optional[str], str, str]] = []
    pos = 0
    text = text or ""
    while True:
        m = _ENTRY_START.search(text, pos)
        if not m:
            break
        etype, open_ch = m.group(1).lower(), m.group(2)
        end = _matching(text, m.end() - 1, open_ch)
        pos = max(end, m.end())
        raw = text[m.start():end]
        if etype in _SKIP_TYPES:
            blocks.append((None, etype, raw))
            continue
        key, sep, _ = text[m.end():end - 1].partition(",")
        blocks.append((key.strip() if sep else None, etype, raw))
    return blocks


def cited_bib_text(bib_texts: List[Tuple[str, str]], cited: set) -> Tuple[str, int]:
    """The entries of the given .bib files that are in `cited`, verbatim and in
    file order (first file wins for a duplicated key), with the @string and
    @preamble blocks they may rely on. Returns (text, number of entries).

    Keys match case-insensitively, as BibTeX matches \\cite keys."""
    fields_by_key: Dict[str, Dict[str, str]] = {}
    for _, text in bib_texts:
        for key, fields in parse_bib(text).items():
            fields_by_key.setdefault(key.lower(), fields)
    # An entry pulled in through crossref / xdata must stay too (BibTeX
    # includes the parent of a cited child).
    keep = {k.lower() for k in cited}
    todo = list(keep)
    while todo:
        child = todo.pop()
        for ref_field in ("crossref", "xdata"):
            for parent in (fields_by_key.get(child, {}).get(ref_field) or "").split(","):
                parent = parent.strip().lower()
                if parent in fields_by_key and parent not in keep:
                    keep.add(parent)
                    todo.append(parent)
    out: List[str] = []
    seen = set()
    for name, text in bib_texts:
        for key, etype, raw in split_bib(text):
            if key is None:
                if etype in ("string", "preamble"):
                    out.append(raw)
                continue
            low = key.lower()
            if low in keep and low not in seen:
                seen.add(low)
                out.append(raw)
    return "\n\n".join(out) + ("\n" if out else ""), len(seen)


def clean_latex_text(value: Optional[str]) -> str:
    if not value:
        return ""
    value = re.sub(r"\\[A-Za-z]+\s*", " ", value)  # \emph, \textit ... (keep their argument)
    value = value.replace("{", "").replace("}", "").replace("~", " ")
    return " ".join(value.split())


def _bib_year(value: Optional[str]) -> Optional[int]:
    m = re.search(r"\d{4}", value or "")
    return int(m.group(0)) if m else None


def _clean_doi(value: Optional[str]) -> Optional[str]:
    doi = clean_latex_text(value)
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.IGNORECASE).strip()
    return doi or None


# --------------------------------------------------------------------------
# Run folder discovery
# --------------------------------------------------------------------------


def find_final_tex(run_dir: str) -> Optional[str]:
    for rel in TEX_CANDIDATES:
        path = os.path.join(run_dir, rel)
        if os.path.isfile(path):
            return path
    return None


def find_bib_files(run_dir: str, tex_path: Optional[str], tex_text: str) -> List[str]:
    found: List[str] = []
    if tex_path:
        tex_dir = os.path.dirname(tex_path)
        for name in bibliography_names(tex_text):
            path = os.path.join(tex_dir, name if name.lower().endswith(".bib") else name + ".bib")
            if os.path.isfile(path) and path not in found:
                found.append(path)
        if not found:
            path = os.path.join(tex_dir, "references.bib")
            if os.path.isfile(path):
                found.append(path)
    if not found:
        for rel in BIB_FALLBACKS:
            path = os.path.join(run_dir, rel)
            if os.path.isfile(path):
                found.append(path)
                break
    return found


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _rel(run_dir: str, path: Optional[str]) -> str:
    if not path:
        return ""
    try:
        return os.path.relpath(path, run_dir).replace(os.sep, "/")
    except ValueError:  # another drive on Windows
        return path


def load_meta(run_dir: str) -> Dict[str, Dict[str, Any]]:
    path = os.path.join(run_dir, META_RELPATH)
    if not os.path.isfile(path):
        return {}
    try:
        data = json.loads(_read(path))
    except Exception:
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _opt_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return _bib_year(str(value))


def build_audit(run_dir: str) -> Dict[str, Any]:
    """The citation_audit.json payload (nothing is written)."""
    run_dir = os.path.abspath(run_dir)
    tex_path = find_final_tex(run_dir)
    tex_text = _read(tex_path) if tex_path else ""
    cited_list, star = parse_citations(tex_text)

    bib_paths = find_bib_files(run_dir, tex_path, tex_text)
    bib: Dict[str, Dict[str, str]] = {}
    for path in bib_paths:
        for key, fields in parse_bib(_read(path)).items():
            bib.setdefault(key, fields)

    meta = load_meta(run_dir)
    # BibTeX matches \cite keys to entries case-insensitively.
    cited = {k.lower() for k in cited_list}
    if star:
        for key in bib:
            if key.lower() not in cited:
                cited.add(key.lower())
                cited_list.append(key)
    in_bib = {k.lower() for k in bib}

    entries: List[Dict[str, Any]] = []
    for key, fields in bib.items():
        m = meta.get(key)
        doi = _opt_str((m or {}).get("doi")) or _clean_doi(fields.get("doi"))
        url = _opt_str((m or {}).get("url")) or _opt_str(clean_latex_text(fields.get("url")))
        if doi or (m is not None and url):
            status = "verified"
        elif m is not None:
            status = "metadata_only"
        else:
            status = "unverified"
        title = _opt_str((m or {}).get("title")) or clean_latex_text(fields.get("title"))
        year = _opt_int((m or {}).get("year")) if m and m.get("year") is not None else _bib_year(fields.get("year"))
        entries.append({
            "key": key,
            "title": title or "",
            "year": year,
            "doi": doi,
            "url": url,
            "source": _opt_str((m or {}).get("source")),
            "cited": key.lower() in cited,
            "status": status,
        })

    missing = [k for k in cited_list if k.lower() not in in_bib]
    uncited = [e["key"] for e in entries if not e["cited"]]
    return {
        "generated_at": now_iso(),
        "tex_file": _rel(run_dir, tex_path),
        "bib_file": _rel(run_dir, bib_paths[0]) if bib_paths else "",
        # Set by audit_run once references_cited.bib is written.
        "cited_bib_file": "",
        "summary": {
            "cited": len(cited_list),
            "bib_entries": len(entries),
            # entries that make it into the paper's reference list
            "in_bibliography": sum(1 for e in entries if e["cited"]),
            "missing_in_bib": len(missing),
            "uncited": len(uncited),
            "with_doi": sum(1 for e in entries if e["doi"]),
            "verified": sum(1 for e in entries if e["status"] == "verified"),
        },
        "missing_in_bib": missing,
        "uncited": uncited,
        "entries": entries,
        "_bib_paths": bib_paths,  # internal, removed before writing
    }


_STATUS_ZH = {"verified": "已核验", "metadata_only": "仅元数据", "unverified": "未核验"}


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.split()).replace("|", "\\|").replace("`", "'") or "—"


def render_markdown(audit: Dict[str, Any]) -> str:
    s = audit["summary"]
    in_bib = s.get("in_bibliography", s["bib_entries"] - s["uncited"])
    lines = [
        "# 引用核查报告",
        "",
        f"- 生成时间：{audit['generated_at']}",
        f"- 正文源文件：`{audit['tex_file'] or '（未找到）'}`",
        f"- 文献库：`{audit['bib_file'] or '（未找到）'}`（文献检索阶段收集的全部候选文献，"
        "不等于论文末尾的参考文献列表）",
    ]
    if audit.get("cited_bib_file"):
        lines.append(f"- 只含被引用条目的文献库：`{audit['cited_bib_file']}`（{in_bib} 条）")
    lines += [
        "- 说明：离线核查，未联网访问任何数据库；元数据来自文献检索阶段。",
        "",
        "| 指标 | 数量 |",
        "| --- | ---: |",
        f"| 正文引用的文献 | {s['cited']} |",
        f"| 进入参考文献列表 | {in_bib} |",
        f"| 文献库条目 | {s['bib_entries']} |",
        f"| 正文引用但库中缺失 | {s['missing_in_bib']} |",
        f"| 库中未被正文引用 | {s['uncited']} |",
        f"| 带 DOI | {s['with_doi']} |",
        f"| 已核验 | {s['verified']} |",
        "",
    ]
    if audit["missing_in_bib"]:
        lines += ["## 正文引用但库中缺失", "",
                  "这些引用键在 PDF 中会显示为 [?]，请补充对应条目或从正文删除：", ""]
        lines += [f"- `{_cell(k)}`" for k in audit["missing_in_bib"]] + [""]
    if audit["uncited"]:
        lines += ["## 库中未被正文引用", "",
                  "BibTeX 只输出正文引用过的条目，这些条目不会出现在论文的参考文献列表中：", ""]
        lines += [f"- `{_cell(k)}`" for k in audit["uncited"]] + [""]
    lines += [
        "## 文献库全部条目",
        "",
        "正文引用的条目在前，未引用的排在最后。",
        "",
        "状态：**已核验** = 有 DOI 或可访问的来源记录；**仅元数据** = 检索到元数据但没有 DOI/链接；"
        "**未核验** = 不是由文献检索获得（如手工添加或模板自带），请人工确认。",
        "",
        "| 状态 | 引用键 | 标题 | 年份 | DOI / 链接 | 来源 | 正文引用 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in sorted(audit["entries"], key=lambda e: not e["cited"]):  # stable: file order within each group
        link = f"https://doi.org/{e['doi']}" if e["doi"] else (e["url"] or "")
        lines.append(
            f"| {_STATUS_ZH.get(e['status'], e['status'])} | `{_cell(e['key'])}` | {_cell(e['title'])} | "
            f"{_cell(e['year'])} | {_cell(link)} | {_cell(e['source'])} | {'是' if e['cited'] else '否'} |"
        )
    if not audit["entries"]:
        lines.append("| — | — | 未找到参考文献条目 | — | — | — | — |")
    return "\n".join(lines) + "\n"


def audit_run(run_dir: str) -> Dict[str, Any]:
    """Audits a run folder and writes citation_audit.json / citation_audit.md into it.

    Returns the JSON payload:
      {"generated_at", "tex_file", "bib_file", "cited_bib_file",
       "summary": {"cited", "bib_entries", "in_bibliography", "missing_in_bib", "uncited",
                   "with_doi", "verified"},
       "missing_in_bib": [key], "uncited": [key],
       "entries": [{"key", "title", "year", "doi", "url", "source", "cited", "status"}]}
    Paths are relative to run_dir with "/" separators ("" when not found).
    Also writes references_cited.bib (cited_bib_file) when the run has a .bib.
    """
    audit = build_audit(run_dir)
    bib_paths = audit.pop("_bib_paths", [])
    cited_path = os.path.join(run_dir, CITED_BIB_NAME)
    if bib_paths:
        cited = {e["key"] for e in audit["entries"] if e["cited"]}
        text, _ = cited_bib_text([(p, _read(p)) for p in bib_paths], cited)
        header = (
            f"% {CITED_BIB_NAME}: the entries of {audit['bib_file']} that {audit['tex_file'] or 'the paper'} cites\n"
            f"% ({len(cited)} of {len(audit['entries'])}), written by the PaperOrchestra citation audit.\n"
            "% The paper's bibliography is the same with either file; the full library stays in references.bib.\n\n"
        )
        atomic_write_text(cited_path, header + text)
        audit["cited_bib_file"] = CITED_BIB_NAME
    elif os.path.isfile(cited_path):
        os.remove(cited_path)  # stale, from an earlier audit of this folder
    atomic_write_json(os.path.join(run_dir, JSON_NAME), audit)
    atomic_write_text(os.path.join(run_dir, MD_NAME), render_markdown(audit))
    return audit
