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

"""Zotero-style Literature Library & Knowledge Graph CLI for PaperOrchestra Studio.

Subcommands (print one JSON object on stdout):
  python tools/library_tools.py search-online --query TEXT [--limit 15] [--expand] [--model auto]
  python tools/library_tools.py import-pdf    --pdf PATH [--model auto] [--no-llm]
  python tools/library_tools.py import-doi    --doi DOI
  python tools/library_tools.py build-graph   --input-json PATH [--model auto]
  python tools/library_tools.py export-bib    --input-json PATH --format bibtex|gb7714
"""

import argparse
from collections import Counter
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import json_repair
import requests

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.providers import install_output_redaction  # noqa: E402


class LibraryError(Exception):
    pass


def emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _norm_doi(raw: Optional[str]) -> str:
    s = (raw or "").strip()
    s = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    return s.rstrip(".,;)")


def _make_cite_key(authors_str: str, year: Any, title: str) -> str:
    first_author = (authors_str or "ref").split(",")[0].strip().split(";")[0].strip()
    surname = re.sub(r"[^a-zA-Z0-9]", "", (first_author.split()[-1] if first_author else "ref")).lower() or "ref"
    yr = str(year or "nd")
    words = [re.sub(r"[^a-zA-Z0-9]", "", w).lower() for w in (title or "").split() if len(re.sub(r"[^a-zA-Z0-9]", "", w)) > 2]
    kw = words[0] if words else "paper"
    return f"{surname}{yr}{kw}"


def _authors_to_str(authors: Any) -> str:
    if isinstance(authors, str):
        return authors.strip()
    if isinstance(authors, list):
        parts = []
        for a in authors:
            if isinstance(a, dict):
                name = a.get("name") or " ".join(p for p in (a.get("given"), a.get("family")) if p)
            else:
                name = str(a)
            if name and name.strip():
                parts.append(name.strip())
        return ", ".join(parts)
    return ""


def _escape_bibtex_text(text: Any) -> str:
    s = str(text or "")
    return re.sub(r"(?<!\\)((?:\\\\)*)([&%#])", r"\1\\\2", s)


def _format_bibtex(title: str, authors: str, year: Any, venue: str, doi: str, url: str) -> str:
    key = _make_cite_key(authors, year, title)
    bib_authors = _escape_bibtex_text(
        " and ".join(a.strip() for a in authors.split(",") if a.strip()) or "Unknown"
    )
    bib_title = _escape_bibtex_text(title or "Untitled")
    entry_type = "article" if venue else "misc"
    lines = [f"@{entry_type}{{{key},"]
    lines.append(f"  title = {{{bib_title}}},")
    lines.append(f"  author = {{{bib_authors}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    if venue:
        lines.append(f"  journal = {{{_escape_bibtex_text(venue)}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
    if url:
        lines.append(f"  url = {{{url}}},")
    lines[-1] = lines[-1].rstrip(",")
    lines.append("}")
    return "\n".join(lines)


def _format_gb7714(title: str, authors: str, year: Any, venue: str, doi: str, url: str) -> str:
    author_list = [a.strip() for a in authors.split(",") if a.strip()]
    if len(author_list) > 3:
        au_txt = ", ".join(author_list[:3]) + ", et al"
    elif author_list:
        au_txt = ", ".join(author_list)
    else:
        au_txt = "Unknown"
    yr = str(year) if year else "n.d."
    venue_part = f"{venue}, " if venue else ""
    ref_type = "[J]" if venue else "[EB/OL]"
    tail = f" DOI:{doi}." if doi else (f" {url}." if url else "")
    return f"{au_txt}. {title or 'Untitled'}{ref_type}. {venue_part}{yr}.{tail}".strip()


def _paper_to_library_item(paper: Dict[str, Any], *, read_status: str = "abstract_only") -> Dict[str, Any]:
    title = (paper.get("title") or "").strip()
    authors = _authors_to_str(paper.get("authors"))
    year = paper.get("year")
    try:
        year = int(year) if year is not None and str(year).strip().isdigit() else None
    except (TypeError, ValueError):
        year = None
    venue = (paper.get("venue") or "").strip()
    doi = _norm_doi(paper.get("doi"))
    arxiv_id = ""
    ext_ids = paper.get("externalIds") or {}
    if isinstance(ext_ids, dict) and ext_ids.get("ArXiv"):
        arxiv_id = str(ext_ids["ArXiv"]).strip()
    url = (paper.get("url") or "").strip()
    if not url and doi:
        url = f"https://doi.org/{doi}"
    abstract = (paper.get("abstract") or "").strip()
    bibtex = _format_bibtex(title, authors, year, venue, doi, url)
    gb7714 = _format_gb7714(title, authors, year, venue, doi, url)
    return {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "arxivId": arxiv_id,
        "url": url,
        "pdfPath": "",
        "bibtex": bibtex,
        "gb7714": gb7714,
        "abstractText": abstract,
        "aiNotes": "",
        "keyClaims": [],
        "tags": [paper.get("source")] if paper.get("source") else [],
        "readStatus": read_status,
    }


def _fetch_crossref_doi(doi: str) -> Optional[Dict[str, Any]]:
    doi_clean = _norm_doi(doi)
    if not doi_clean:
        return None
    url = f"https://api.crossref.org/works/{quote(doi_clean, safe='')}"
    try:
        rsp = requests.get(
            url,
            headers={"User-Agent": "PaperOrchestra/1.0 (mailto:research@example.edu)"},
            timeout=15,
        )
        if rsp.status_code != 200:
            return None
        msg = (rsp.json() or {}).get("message") or {}
        title_list = msg.get("title") or []
        title = title_list[0] if title_list else ""
        if not title:
            return None
        authors = []
        for a in msg.get("author") or []:
            name = " ".join(p for p in (a.get("given"), a.get("family")) if p)
            if name:
                authors.append(name)
        date_parts = (
            (msg.get("published-print") or {}).get("date-parts")
            or (msg.get("published-online") or {}).get("date-parts")
            or (msg.get("created") or {}).get("date-parts")
            or [[]]
        )
        year = date_parts[0][0] if date_parts and date_parts[0] else None
        container = msg.get("container-title") or []
        venue = container[0] if container else (msg.get("publisher") or "")
        abstract = re.sub(r"<[^>]+>", " ", msg.get("abstract") or "").strip()
        return {
            "title": title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "doi": doi_clean,
            "url": msg.get("URL") or f"https://doi.org/{doi_clean}",
            "abstract": abstract,
            "source": "crossref",
        }
    except Exception:
        return None


def cmd_import_doi(args: argparse.Namespace) -> Dict[str, Any]:
    doi_clean = _norm_doi(args.doi)
    if not doi_clean:
        raise LibraryError("请提供有效的 DOI。")
    hit = _fetch_crossref_doi(doi_clean)
    if not hit:
        from utils.scholar_utils import title_lookup

        hit = title_lookup(doi_clean, require_abstract=False)
    if not hit:
        raise LibraryError(f"未能从 Crossref 或公开学术库查询到 DOI：{doi_clean}")
    item = _paper_to_library_item(hit, read_status="abstract_only")
    return {"ok": True, "item": item}


def cmd_search_online(args: argparse.Namespace) -> Dict[str, Any]:
    from utils.scholar_utils import multi_keyword_search

    query = (args.query or "").strip()
    if not query:
        raise LibraryError("请输入检索关键词。")

    queries = [query]
    if args.expand:
        try:
            from utils.llm_backend_utils import (
                call_llm_with_text_prompt,
                has_any_llm_credentials,
                resolve_task_model,
            )
            from utils.paper_banana_utils import identity_parse

            if has_any_llm_credentials():
                model = resolve_task_model(args.model, "literature_query")
                prompt = (
                    "Expand the following research search query into 3 concise English academic search queries "
                    "(covering core synonyms, method families, and classic seminal phrasing). "
                    "Return strict JSON: {\"queries\": [\"...\", \"...\", \"...\"]}.\n"
                    f"Input query: {query}"
                )
                resp = call_llm_with_text_prompt(
                    prompt=prompt,
                    model_name=model,
                    generation_configs={"system_instruction": "Academic search strategist."},
                    result_parsing_func=identity_parse,
                )
                parsed = json_repair.loads(resp.get("parsed_response", "{}"))
                if isinstance(parsed, dict) and isinstance(parsed.get("queries"), list):
                    for q in parsed["queries"]:
                        qs = str(q).strip()
                        if qs and qs not in queries:
                            queries.append(qs)
        except Exception:
            pass

    limit = max(1, min(50, int(args.limit)))
    per_q = max(5, limit // max(1, len(queries)) + 3)
    seen_titles = set()
    items: List[Dict[str, Any]] = []
    for q in queries[:4]:
        hits = multi_keyword_search(q, limit=per_q, per_source=5)
        for h in hits:
            norm = re.sub(r"[\W_]+", "", (h.get("title") or "").casefold())
            if not norm or norm in seen_titles:
                continue
            seen_titles.add(norm)
            items.append(_paper_to_library_item(h, read_status="abstract_only"))
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break

    return {
        "ok": True,
        "query": query,
        "expandedQueries": queries,
        "items": items,
    }


def cmd_import_pdf(args: argparse.Namespace) -> Dict[str, Any]:
    from utils.pdf_utils import load_paper

    pdf_path = os.path.abspath(args.pdf)
    if not os.path.isfile(pdf_path):
        raise LibraryError(f"找不到 PDF 文件：{pdf_path}")

    try:
        raw_text = load_paper(pdf_path, num_pages=12, min_size=20)
    except Exception:
        from pypdf import PdfReader

        reader = PdfReader(pdf_path)
        raw_text = "\n".join((p.extract_text() or "") for p in reader.pages[:12])

    snippet = (raw_text or "").strip()[:24000]
    doi_match = re.search(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", snippet[:6000])
    doi = _norm_doi(doi_match.group(1)) if doi_match else ""

    lines = [ln.strip() for ln in snippet.splitlines() if len(ln.strip()) > 6]
    fallback_title = os.path.splitext(os.path.basename(pdf_path))[0].replace("_", " ")
    for ln in lines[:12]:
        if not re.match(r"^(arxiv:|http|doi:|abstract\b|published\b|\d+$)", ln, re.IGNORECASE):
            fallback_title = ln[:200]
            break

    meta: Dict[str, Any] = {}
    if doi:
        meta = _fetch_crossref_doi(doi) or {}

    title = meta.get("title") or fallback_title
    authors = _authors_to_str(meta.get("authors"))
    year = meta.get("year")
    venue = meta.get("venue") or ""
    url = meta.get("url") or (f"https://doi.org/{doi}" if doi else "")
    abstract = meta.get("abstract") or ""
    ai_notes = ""
    key_claims: List[Dict[str, str]] = []
    tags: List[str] = ["PDF精读"]

    if not args.no_llm and snippet:
        try:
            from utils.llm_backend_utils import (
                call_llm_with_text_prompt,
                has_any_llm_credentials,
                resolve_task_model,
            )
            from utils.paper_banana_utils import identity_parse

            if has_any_llm_credentials():
                model = resolve_task_model(args.model, "literature_write")
                prompt = (
                    "Read the extracted paper text below carefully and return STRICT JSON (no markdown fence wrapper needed):\n"
                    "{\n"
                    '  "title": "exact paper title",\n'
                    '  "authors": "comma separated authors",\n'
                    '  "year": 2024,\n'
                    '  "venue": "journal or conference name if known",\n'
                    '  "abstract": "concise English or Chinese summary of abstract",\n'
                    '  "ai_notes": "structured Chinese reading note: 核心问题、方法亮点、主要实验结论、局限性",\n'
                    '  "tags": ["tag1", "tag2", "tag3"],\n'
                    '  "key_claims": [\n'
                    '    {"claim": "author finding (distinguish own contribution vs cited background)", "evidence": "exact table/figure/metric quote", "location": "Section/Page"}\n'
                    "  ]\n"
                    "}\n\n"
                    f"Extracted paper text:\n{snippet[:18000]}"
                )
                resp = call_llm_with_text_prompt(
                    prompt=prompt,
                    model_name=model,
                    generation_configs={"system_instruction": "Careful scientific reader. Never fabricate metadata or claims not in the text."},
                    result_parsing_func=identity_parse,
                )
                parsed = json_repair.loads(resp.get("parsed_response", "{}"))
                if isinstance(parsed, dict):
                    if not meta.get("title") and parsed.get("title"):
                        title = str(parsed["title"]).strip()
                    if not authors and parsed.get("authors"):
                        authors = _authors_to_str(parsed["authors"])
                    if not year and str(parsed.get("year") or "").isdigit():
                        year = int(parsed["year"])
                    if not venue and parsed.get("venue"):
                        venue = str(parsed["venue"]).strip()
                    if not abstract and parsed.get("abstract"):
                        abstract = str(parsed["abstract"]).strip()
                    ai_notes = str(parsed.get("ai_notes") or "").strip()
                    if isinstance(parsed.get("tags"), list):
                        tags = [str(t).strip() for t in parsed["tags"] if str(t).strip()][:8]
                    if isinstance(parsed.get("key_claims"), list):
                        for kc in parsed["key_claims"][:8]:
                            if isinstance(kc, dict) and kc.get("claim"):
                                key_claims.append(
                                    {
                                        "claim": str(kc.get("claim") or "").strip(),
                                        "evidence": str(kc.get("evidence") or "").strip(),
                                        "location": str(kc.get("location") or "").strip(),
                                    }
                                )
        except Exception as exc:
            ai_notes = f"已提取原文前 {len(snippet)} 字符；AI 精读解析失败：{exc}"

    item = _paper_to_library_item(
        {
            "title": title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "doi": doi,
            "url": url,
            "abstract": abstract,
            "source": "pdf",
        },
        read_status="full_read",
    )
    item["pdfPath"] = pdf_path
    item["aiNotes"] = ai_notes or f"已导入全文文本（{len(snippet)} 字符），可直接在科研工坊「论文精读 SOP」中深入分析。"
    item["keyClaims"] = key_claims
    item["tags"] = tags
    return {"ok": True, "item": item}


def _offline_graph_from_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic fallback builder for Knowledge Graph + Reading Order + Comparison Matrix."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    tag_papers: Dict[str, List[str]] = {}
    tag_counts: Counter = Counter()

    def _role_for(it: Dict[str, Any], idx: int) -> str:
        t = (it.get("title") or "").lower()
        if any(w in t for w in ("survey", "review", "overview", "综述")):
            return "survey"
        yr = it.get("year")
        if isinstance(yr, int):
            return "seminal" if yr <= 2022 else "recent"
        return "recent" if idx % 3 != 0 else "seminal"

    for idx, it in enumerate(items):
        pid = str(it.get("id") or f"paper_{idx+1}")
        label = (it.get("title") or f"Paper {idx+1}").strip()
        short_label = label if len(label) <= 44 else label[:41] + "..."
        role = _role_for(it, idx)
        nodes.append(
            {
                "id": pid,
                "label": short_label,
                "fullTitle": label,
                "kind": "paper",
                "year": it.get("year"),
                "role": role,
                "itemId": it.get("id") or "",
            }
        )
        raw_tags = list(it.get("tags") or [])
        if not raw_tags:
            words = [w for w in re.findall(r"[A-Za-z]{4,}|[\u4e00-\u9fff]{2,6}", label) if w.lower() not in ("with", "from", "using", "based", "study", "analysis")]
            raw_tags = words[:3]
        for tg in raw_tags[:4]:
            t_clean = str(tg).strip()
            if not t_clean:
                continue
            tag_counts[t_clean] += 1
            tag_papers.setdefault(t_clean, []).append(pid)

    top_tags = [tg for tg, _ in tag_counts.most_common(8)]
    for idx, tg in enumerate(top_tags):
        cid = f"concept_{idx+1}"
        nodes.append(
            {
                "id": cid,
                "label": tg,
                "fullTitle": tg,
                "kind": "concept",
                "year": None,
                "role": "concept",
                "itemId": "",
            }
        )
        for pid in tag_papers.get(tg, []):
            edges.append({"source": pid, "target": cid, "relation": "涉及主题"})

    # Add chronological sequence edges between papers sharing a top concept
    node_year = {n["id"]: (n.get("year") or 9999) for n in nodes if n["kind"] == "paper"}
    for tg in top_tags:
        pids = sorted(set(tag_papers.get(tg, [])), key=lambda p: node_year.get(p, 9999))
        for a, b in zip(pids, pids[1:]):
            edges.append({"source": a, "target": b, "relation": "演进/延伸"})

    role_rank = {"survey": 0, "seminal": 1, "recent": 2}
    ordered_papers = sorted(
        [n for n in nodes if n["kind"] == "paper"],
        key=lambda n: (role_rank.get(n.get("role", "recent"), 2), n.get("year") or 9999),
    )
    role_zh = {"survey": "综述立框", "seminal": "经典奠基", "recent": "近期前沿"}
    reading_order = [
        {
            "step": i + 1,
            "itemId": n.get("itemId") or n["id"],
            "title": n["fullTitle"],
            "year": n.get("year"),
            "stage": role_zh.get(n.get("role", "recent"), "延伸阅读"),
            "purpose": "先建立全局地图" if n.get("role") == "survey" else ("掌握核心问题定义与基线方法" if n.get("role") == "seminal" else "跟踪最新改进与现存局限"),
        }
        for i, n in enumerate(ordered_papers)
    ]

    matrix_rows = []
    for it in items:
        claims = it.get("keyClaims") or []
        claim_txt = claims[0].get("claim", "") if claims and isinstance(claims[0], dict) else ""
        matrix_rows.append(
            {
                "title": it.get("title") or "",
                "year": str(it.get("year") or "—"),
                "venue": it.get("venue") or "—",
                "readStatus": "全文精读" if it.get("readStatus") == "full_read" else ("仅摘要" if it.get("readStatus") == "abstract_only" else "待读"),
                "coreFocus": (it.get("aiNotes") or claim_txt or (it.get("abstractText") or "")[:160] or "—")[:180],
                "tags": ", ".join(str(t) for t in (it.get("tags") or [])[:4]) or "—",
            }
        )

    return {
        "nodes": nodes,
        "edges": edges,
        "readingOrder": reading_order,
        "comparisonMatrix": {
            "columns": ["文献标题", "年份", "发表刊物", "阅读深度", "核心问题/结论", "主题标签"],
            "rows": matrix_rows,
        },
        "generatedBy": "offline_structure",
    }


def cmd_build_graph(args: argparse.Namespace) -> Dict[str, Any]:
    input_path = os.path.abspath(args.input_json)
    if not os.path.isfile(input_path):
        raise LibraryError(f"找不到输入文件：{input_path}")
    with open(input_path, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    if not isinstance(items, list) or not items:
        raise LibraryError("文献列表为空，请先入库至少 1 篇文献。")

    fallback = _offline_graph_from_items(items)
    try:
        from utils.llm_backend_utils import (
            call_llm_with_text_prompt,
            has_any_llm_credentials,
            resolve_task_model,
        )
        from utils.paper_banana_utils import identity_parse

        if has_any_llm_credentials() and len(items) >= 2:
            model = resolve_task_model(args.model, "outline")
            brief = [
                {
                    "id": str(it.get("id") or f"paper_{i+1}"),
                    "title": it.get("title"),
                    "year": it.get("year"),
                    "venue": it.get("venue"),
                    "tags": it.get("tags") or [],
                    "abstract": (it.get("abstractText") or "")[:400],
                    "notes": (it.get("aiNotes") or "")[:300],
                    "readStatus": it.get("readStatus"),
                }
                for i, it in enumerate(items[:25])
            ]
            prompt = (
                "Build a structured research Knowledge Graph, Priority Reading Order (综述 -> 经典奠基 -> 近期前沿), and Comparison Matrix from ONLY the provided literature items.\n"
                "Return strict JSON matching:\n"
                "{\n"
                '  "nodes": [{"id": "...", "label": "short <=40 chars", "fullTitle": "...", "kind": "paper|concept|method", "year": 2024, "role": "survey|seminal|recent|concept", "itemId": "..."}],\n'
                '  "edges": [{"source": "node_id", "target": "node_id", "relation": "中文简短关系词"}],\n'
                '  "readingOrder": [{"step": 1, "itemId": "...", "title": "...", "year": 2024, "stage": "综述立框|经典奠基|近期前沿", "purpose": "阅读目的"}],\n'
                '  "comparisonMatrix": {\n'
                '    "columns": ["文献标题", "年份", "研究问题", "核心方法", "评估/局限", "阅读深度"],\n'
                '    "rows": [{"title": "...", "year": "2024", "question": "...", "method": "...", "limitation": "...", "readStatus": "全文精读|仅摘要"}]\n'
                "  }\n"
                "}\n\n"
                f"Items:\n{json.dumps(brief, ensure_ascii=False, indent=2)}"
            )
            resp = call_llm_with_text_prompt(
                prompt=prompt,
                model_name=model,
                generation_configs={"system_instruction": "Research knowledge-graph architect. Ground every node/edge in the provided items only."},
                result_parsing_func=identity_parse,
            )
            parsed = json_repair.loads(resp.get("parsed_response", "{}"))
            if isinstance(parsed, dict) and isinstance(parsed.get("nodes"), list) and len(parsed["nodes"]) >= len(brief):
                parsed["generatedBy"] = "llm"
                return {"ok": True, "graph": parsed}
    except Exception:
        pass

    return {"ok": True, "graph": fallback}


def cmd_export_bib(args: argparse.Namespace) -> Dict[str, Any]:
    input_path = os.path.abspath(args.input_json)
    if not os.path.isfile(input_path):
        raise LibraryError(f"找不到输入文件：{input_path}")
    with open(input_path, "r", encoding="utf-8") as fh:
        items = json.load(fh)
    if not isinstance(items, list):
        raise LibraryError("文献列表格式无效。")

    if args.format == "bibtex":
        chunks = []
        for it in items:
            bib = (it.get("bibtex") or "").strip()
            if not bib:
                bib = _format_bibtex(
                    it.get("title") or "",
                    it.get("authors") or "",
                    it.get("year"),
                    it.get("venue") or "",
                    it.get("doi") or "",
                    it.get("url") or "",
                )
            chunks.append(bib)
        text = "\n\n".join(chunks)
    else:
        lines = []
        for idx, it in enumerate(items, start=1):
            gb = (it.get("gb7714") or "").strip()
            if not gb:
                gb = _format_gb7714(
                    it.get("title") or "",
                    it.get("authors") or "",
                    it.get("year"),
                    it.get("venue") or "",
                    it.get("doi") or "",
                    it.get("url") or "",
                )
            lines.append(f"[{idx}] {gb}")
        text = "\n".join(lines)
    return {"ok": True, "format": args.format, "count": len(items), "text": text}


def main(argv: Optional[List[str]] = None) -> int:
    install_output_redaction()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search-online")
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--expand", action="store_true")
    p.add_argument("--model", default="auto")
    p.set_defaults(fn=cmd_search_online)

    p = sub.add_parser("import-pdf")
    p.add_argument("--pdf", required=True)
    p.add_argument("--model", default="auto")
    p.add_argument("--no-llm", action="store_true")
    p.set_defaults(fn=cmd_import_pdf)

    p = sub.add_parser("import-doi")
    p.add_argument("--doi", required=True)
    p.set_defaults(fn=cmd_import_doi)

    p = sub.add_parser("build-graph")
    p.add_argument("--input-json", required=True)
    p.add_argument("--model", default="auto")
    p.set_defaults(fn=cmd_build_graph)

    p = sub.add_parser("export-bib")
    p.add_argument("--input-json", required=True)
    p.add_argument("--format", choices=["bibtex", "gb7714"], default="bibtex")
    p.set_defaults(fn=cmd_export_bib)

    args = ap.parse_args(argv)
    try:
        emit(args.fn(args))
    except LibraryError as exc:
        emit({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        emit({"error": f"{type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
