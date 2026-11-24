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

"""AI LaTeX Copilot & Inline Claim-to-Source Traceability CLI.

Subcommands (print one JSON object on stdout):
  python tools/latex_copilot.py edit            --file TEX_PATH --instruction TEXT [--selection-start L1] [--selection-end L2] [--error-log TEXT] [--model auto]
  python tools/latex_copilot.py trace-citations --run-dir RUN_DIR [--tex-file TEX_PATH]
"""

import argparse
import difflib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils import citation_audit as ca  # noqa: E402
from utils.providers import install_output_redaction  # noqa: E402


class CopilotError(Exception):
    pass


def emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _strip_tex_fences(raw: str) -> str:
    txt = (raw or "").strip()
    m = re.search(r"```(?:latex|tex)?\s*\n([\s\S]*?)\n```", txt, re.IGNORECASE)
    if m:
        return m.group(1)
    return txt


def cmd_edit(args: argparse.Namespace) -> Dict[str, Any]:
    from utils.llm_backend_utils import (
        call_llm_with_text_prompt,
        has_any_llm_credentials,
        resolve_task_model,
    )
    from utils.paper_banana_utils import identity_parse

    tex_path = os.path.abspath(args.file)
    if not os.path.isfile(tex_path):
        raise CopilotError(f"找不到 LaTeX 文件：{tex_path}")
    instruction = (args.instruction or "").strip()
    if not instruction:
        raise CopilotError("请输入修改指令。")
    if not has_any_llm_credentials():
        raise CopilotError("未检测到可用大模型密钥，请先在「环境与算力」配置 API 密钥。")

    with open(tex_path, "r", encoding="utf-8", errors="replace") as fh:
        original = fh.read()

    lines = original.splitlines(keepends=True)
    sel_start = args.selection_start
    sel_end = args.selection_end
    scoped = (
        sel_start is not None
        and sel_end is not None
        and 1 <= sel_start <= sel_end <= len(lines)
    )

    model = resolve_task_model(args.model, "content_refinement")
    err_hint = f"\nCompiler error log context:\n{args.error_log[:3000]}\n" if args.error_log else ""

    if scoped:
        target_slice = "".join(lines[sel_start - 1 : sel_end])
        ctx_before = "".join(lines[max(0, sel_start - 15) : sel_start - 1])
        ctx_after = "".join(lines[sel_end : min(len(lines), sel_end + 15)])
        prompt = (
            "You are a surgical LaTeX editor embedded inside PaperOrchestra Studio.\n"
            f"User instruction: {instruction}\n"
            f"{err_hint}\n"
            f"Context before (read-only):\n```latex\n{ctx_before}\n```\n"
            f"Selected lines {sel_start}-{sel_end} (RETURN REVISED VERSION OF THESE LINES ONLY):\n"
            f"```latex\n{target_slice}\n```\n"
            f"Context after (read-only):\n```latex\n{ctx_after}\n```\n"
            "Rules:\n"
            "1. Keep all existing \\cite{...} keys, verified numbers, labels, and unaffected prose intact.\n"
            "2. Return ONLY ```latex ... ``` containing the replacement for lines "
            f"{sel_start}-{sel_end}."
        )
        resp = call_llm_with_text_prompt(
            prompt=prompt,
            model_name=model,
            generation_configs={"system_instruction": "Precision LaTeX copilot. Never invent citations or numbers."},
            result_parsing_func=identity_parse,
        )
        replacement = _strip_tex_fences(resp.get("parsed_response", ""))
        if replacement and not replacement.endswith("\n") and target_slice.endswith("\n"):
            replacement += "\n"
        revised = "".join(lines[: sel_start - 1]) + replacement + "".join(lines[sel_end:])
        summary = f"已修改第 {sel_start}-{sel_end} 行：{instruction}"
    else:
        prompt = (
            "You are an expert LaTeX editor embedded inside PaperOrchestra Studio.\n"
            f"User instruction: {instruction}\n"
            f"{err_hint}\n"
            "Rules:\n"
            "1. Modify only what is needed to fulfill the instruction cleanly (e.g., centering tables, page breaks, fixing syntax errors, or editing requested sections).\n"
            "2. Preserve all verified \\cite{...} keys, numbers, labels, and \\bibliography commands.\n"
            "3. Return ONLY ```latex ... ``` containing the COMPLETE updated file.\n\n"
            f"Current file (`{os.path.basename(tex_path)}`):\n```latex\n{original}\n```"
        )
        resp = call_llm_with_text_prompt(
            prompt=prompt,
            model_name=model,
            generation_configs={"system_instruction": "Precision LaTeX copilot. Never invent citations or numbers."},
            result_parsing_func=identity_parse,
        )
        revised = _strip_tex_fences(resp.get("parsed_response", ""))
        if not revised.strip():
            raise CopilotError("模型未返回有效的 LaTeX 内容。")
        if original.endswith("\n") and not revised.endswith("\n"):
            revised += "\n"
        summary = f"已按指令生成修订稿：{instruction}"

    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(),
            revised.splitlines(),
            fromfile=os.path.basename(tex_path),
            tofile=f"{os.path.basename(tex_path)} (AI 修订)",
            lineterm="",
            n=2,
        )
    )
    return {
        "ok": True,
        "file": tex_path,
        "revisedContent": revised,
        "diff": "\n".join(diff_lines[:300]),
        "summary": summary,
        "changedLines": sum(1 for l in diff_lines if (l.startswith("+") or l.startswith("-")) and not l.startswith(("+++", "---"))),
    }


def _extract_sentence_excerpt(text: str, pos: int) -> str:
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    if len(line) > 220:
        rel = max(0, pos - line_start)
        s = max(0, rel - 90)
        e = min(len(line), rel + 110)
        return ("…" if s > 0 else "") + line[s:e] + ("…" if e < len(line) else "")
    return line


def _pick_tex_in_run(run_dir: str, explicit_tex: str) -> str:
    if explicit_tex and os.path.isfile(explicit_tex):
        return os.path.abspath(explicit_tex)
    for rel in ca.TEX_CANDIDATES:
        p = os.path.join(run_dir, rel)
        if os.path.isfile(p):
            return os.path.abspath(p)
    raise CopilotError("未在该运行目录中找到可溯源的 .tex 主文件。")


def _load_submission_tools():
    tools_dir = os.path.join(REPO_ROOT, "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import submission_tools as st  # noqa: WPS433

    return st


def _strip_json_fences(raw: str) -> str:
    txt = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", txt, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return txt


def _diagnose_abstract_fields(abstract_text: str) -> List[Dict[str, str]]:
    """Classify 7 award-paper intake fields inside an abstract without any LLM call."""
    txt = " ".join((abstract_text or "").split())
    low = txt.lower()
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", txt) if s.strip()]

    checks = [
        (
            "task",
            "研究任务 / 问题域",
            bool(re.search(r"\b(task|problem|predict|estimat|reconstruct|generat|optimiz|simulat|detect|classify|benchmark|model|study)\b", low))
            or len(sents) >= 1,
            sents[0][:110] if sents else "",
            "首句清晰点明具体任务场景，避免泛泛背景开篇。",
        ),
        (
            "prior_approach",
            "现有主流方法 / 前人工作",
            bool(re.search(r"\b(existing|prior|previous|conventional|traditional|current|recent|rely on|standard)\b", low)),
            "",
            "点明具体主流方法类别（而非笼统写 'existing work'）。",
        ),
        (
            "concrete_gap_and_cause",
            "具体缺口及其原因",
            bool(re.search(r"\b(however|but|fail|suffer|limit|bottleneck|costly|slow|struggle|cannot|inaccurate|unable)\b", low))
            and bool(re.search(r"\b(because|since|due to|when|under|as |since)\b", low)),
            "",
            "补充现有方法在什么条件下失效、以及背后的机制原因。",
        ),
        (
            "key_insight",
            "核心洞察 (Key Insight)",
            bool(re.search(r"\b(observ|insight|key idea|leverag|exploit|posit|show that|reveal|crucial)\b", low)),
            "",
            "用一句话直白说出从问题本质推导出的核心洞察。",
        ),
        (
            "method_mechanism",
            "方法核心机制",
            bool(re.search(r"\b(we (?:propose|present|introduce|develop)|our (?:method|framework|approach|model)|consists of|architecture|algorithm|via )\b", low)),
            "",
            "写清组件之间如何因果衔接（而非并排罗列模块名）。",
        ),
        (
            "headline_numbers",
            "头条定量结果",
            bool(re.search(r"\d+(?:\.\d+)?\s*(?:%|x|×|dB|FPS|ms|s\b|fold)|\b(?:outperform|reduc|improv|achiev)\w*[^.]{0,40}\d", low)),
            "",
            "保留 1–2 个最核心的头条数字支撑主张，其余留给正文表格。",
        ),
        (
            "closest_work",
            "对比基线 / 最近工作定位",
            bool(re.search(r"\b(compar|outperform|surpass|over |baseline|state-of-the-art|sota|strongest)\b", low)),
            "",
            "点明与最相近强基线的一个可核查差异。",
        ),
    ]

    out: List[Dict[str, str]] = []
    for key, label, ok_flag, ev, sug in checks:
        status = "present" if ok_flag else "missing"
        if key == "concrete_gap_and_cause" and not ok_flag and re.search(r"\b(however|but|fail|limit|costly|slow)\b", low):
            status = "vague"
        if key == "headline_numbers" and not ok_flag and re.search(r"\b\d+(?:\.\d+)?\b", txt):
            status = "vague"
        evidence = ev
        if not evidence and ok_flag:
            for s in sents:
                if key == "headline_numbers" and re.search(r"\d", s):
                    evidence = s[:110]
                    break
                if key == "concrete_gap_and_cause" and re.search(r"\b(however|but|fail|limit)\b", s.lower()):
                    evidence = s[:110]
                    break
                if key == "method_mechanism" and re.search(r"\b(propose|present|introduce|our )\b", s.lower()):
                    evidence = s[:110]
                    break
        out.append({
            "field": key,
            "label": label,
            "status": status,
            "evidence": evidence,
            "suggestion": "" if status == "present" else sug,
        })
    return out


def cmd_polish_abstract(args: argparse.Namespace) -> Dict[str, Any]:
    st = _load_submission_tools()
    mode = str(args.mode or "diagnose").strip().lower()
    if mode not in ("diagnose", "rewrite"):
        raise CopilotError(f"Unsupported polish mode: {mode}")

    abs_raw = str(args.text or "").strip()
    intro_raw = str(args.intro_text or "").strip()
    source_label = "inline-text"

    if not abs_raw:
        tex_path = ""
        if args.file:
            tex_path = os.path.abspath(os.path.expanduser(args.file))
            if not os.path.isfile(tex_path):
                raise CopilotError(f"找不到 LaTeX 文件：{tex_path}")
        elif args.run_dir:
            tex_path = _pick_tex_in_run(os.path.abspath(os.path.expanduser(args.run_dir)), "")
        else:
            raise CopilotError("请提供 `text`、`file_path` 或 `run_dir` 以诊断摘要。")

        with open(tex_path, "r", encoding="utf-8", errors="replace") as fh:
            tex_content = fh.read()
        source_label = tex_path
        ext_abs = st._extract_abstract_block(tex_content)
        abs_raw = st._clean_prose_block(ext_abs).strip() if ext_abs else ""
        if not intro_raw:
            ext_intro = st._extract_intro_block(tex_content)
            intro_raw = st._clean_prose_block(ext_intro).strip() if ext_intro else ""

    if not abs_raw:
        raise CopilotError("未能从目标文本或源码中提取到有效摘要（\\begin{abstract}）。")

    field_diag = _diagnose_abstract_fields(abs_raw)
    missing = [d["label"] for d in field_diag if d["status"] != "present"]
    before_audit = st.audit_text_draft(abs_raw, section="abstract")
    intro_audit = st.audit_text_draft(intro_raw, section="introduction") if intro_raw else None

    if mode == "diagnose":
        return {
            "ok": True,
            "mode": "diagnose",
            "source": source_label,
            "abstract": abs_raw,
            "fieldDiagnosis": field_diag,
            "missingFields": missing,
            "beforeAudit": before_audit,
            "introAudit": intro_audit,
            "summary": (
                f"摘要字段完备度 {len(field_diag) - len(missing)}/{len(field_diag)} · "
                f"文风体检问题 {len(before_audit.get('issues') or [])} 条"
            ),
        }

    from utils.llm_backend_utils import (
        call_llm_with_text_prompt,
        has_any_llm_credentials,
        resolve_task_model,
    )
    from utils.paper_banana_utils import identity_parse

    if not has_any_llm_credentials():
        raise CopilotError("AI 逐句改写需要可用的大模型密钥，请先配置 API 密钥（也可先使用「摘要诊断 (离线)」）。")

    model = resolve_task_model(args.model, "content_refinement")
    prompt = (
        "You are a senior AI paper editor trained on award-winning CVPR/ICCV/NeurIPS/Nature writing patterns.\n"
        "Revise ONLY the Abstract below into crisp, persuasive, claim-driven prose while producing a transparent sentence-by-sentence change table.\n\n"
        f"Original Abstract:\n{abs_raw}\n\n"
        + (f"Current Introduction Context (read-only):\n{intro_raw[:2500]}\n\n" if intro_raw else "")
        + f"Pre-flight Audit Findings:\n{json.dumps(before_audit, ensure_ascii=False)}\n\n"
        "STRICT RULES:\n"
        "1. NEVER fabricate numbers, baselines, datasets, or citations. Preserve every original metric verbatim; mark missing evidence as [TODO: ...].\n"
        "2. Zero em-dashes (---), zero semicolons, and zero Limitations/future-work disclaimers in the Abstract.\n"
        "3. Eliminate generic openings ('has attracted increasing attention'), trailing ', making/enabling ...' participles, and workflow leaks.\n"
        "4. Keep the Abstract under 200 words and carry only 1-2 headline numbers.\n"
        "5. Return ONLY a single valid JSON object inside ```json ... ``` with keys:\n"
        "   - paperType (one of L1..L8)\n"
        "   - motivationType (failure|cost|capability_gap|insight|trend)\n"
        "   - missingQuestions (array of concise questions for missing fields)\n"
        "   - revisedAbstract (string)\n"
        "   - changeTable (array of {original, revised, reason, pattern})\n"
        "   - limitationsNotes (array of caveats kept out of Abstract for Limitations section)\n"
    )
    resp = call_llm_with_text_prompt(
        prompt=prompt,
        model_name=model,
        generation_configs={"system_instruction": "Precision scientific abstract editor. Output valid JSON only; zero fabricated facts."},
        result_parsing_func=identity_parse,
    )
    raw_json = _strip_json_fences(resp.get("parsed_response", ""))
    try:
        parsed = json.loads(raw_json)
    except Exception as exc:
        raise CopilotError(f"模型返回的改写 JSON 无法解析：{exc}") from exc

    revised_abs = str(parsed.get("revisedAbstract") or "").strip()
    if not revised_abs:
        raise CopilotError("模型未能返回有效的 revisedAbstract。")
    after_audit = st.audit_text_draft(revised_abs, section="abstract")

    return {
        "ok": True,
        "mode": "rewrite",
        "source": source_label,
        "abstract": abs_raw,
        "revisedAbstract": revised_abs,
        "paperType": parsed.get("paperType", ""),
        "motivationType": parsed.get("motivationType", ""),
        "fieldDiagnosis": field_diag,
        "missingFields": missing,
        "missingQuestions": parsed.get("missingQuestions") or [],
        "changeTable": parsed.get("changeTable") or [],
        "limitationsNotes": parsed.get("limitationsNotes") or [],
        "beforeAudit": before_audit,
        "afterAudit": after_audit,
        "summary": (
            f"已完成逐句改写（{len(parsed.get('changeTable') or [])} 条对照）· "
            f"问题数 {len(before_audit.get('issues') or [])} → {len(after_audit.get('issues') or [])}"
        ),
    }


def cmd_trace_citations(args: argparse.Namespace) -> Dict[str, Any]:
    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        raise CopilotError(f"运行目录不存在：{run_dir}")
    tex_path = _pick_tex_in_run(run_dir, args.tex_file)
    with open(tex_path, "r", encoding="utf-8", errors="replace") as fh:
        raw_tex = fh.read()

    # Load .bib entries
    bib_entries: Dict[str, Dict[str, str]] = {}
    for rel_bib in ca.BIB_FALLBACKS + [
        os.path.join("content_refinement_workdir", "references.bib"),
        os.path.join("content_refinement_workdir", "bibtex.bib"),
    ]:
        bp = os.path.join(run_dir, rel_bib)
        if os.path.isfile(bp):
            with open(bp, "r", encoding="utf-8", errors="replace") as bfh:
                for k, fields in ca.parse_bib(bfh.read()).items():
                    bib_entries.setdefault(k.lower(), {**fields, "_orig_key": k})

    # Load references_meta.json & citation_map.json & citation_audit.json if present
    meta_by_key: Dict[str, Dict[str, Any]] = {}
    meta_path = os.path.join(run_dir, ca.META_RELPATH)
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                raw_meta = json.load(fh)
            if isinstance(raw_meta, dict):
                for k, v in raw_meta.items():
                    if isinstance(v, dict):
                        meta_by_key[k.lower()] = v
        except Exception:
            pass

    cmap_by_key: Dict[str, Dict[str, Any]] = {}
    cmap_path = os.path.join(run_dir, "literature_agent_output", "citation_map.json")
    if os.path.isfile(cmap_path):
        try:
            with open(cmap_path, "r", encoding="utf-8") as fh:
                raw_cmap = json.load(fh)
            if isinstance(raw_cmap, dict):
                for section, entries in raw_cmap.items():
                    if isinstance(entries, list):
                        for e in entries:
                            if isinstance(e, dict) and e.get("key"):
                                cmap_by_key[str(e["key"]).strip().lower()] = {**e, "_section": section}
                    elif isinstance(entries, dict):
                        for k, v in entries.items():
                            if isinstance(v, dict):
                                cmap_by_key[str(k).strip().lower()] = {**v, "_section": section}
        except Exception:
            pass

    audit_status: Dict[str, str] = {}
    audit_path = os.path.join(run_dir, ca.JSON_NAME)
    if os.path.isfile(audit_path):
        try:
            with open(audit_path, "r", encoding="utf-8") as fh:
                aud = json.load(fh)
            for row in aud.get("entries") or []:
                if isinstance(row, dict) and row.get("key"):
                    audit_status[str(row["key"]).strip().lower()] = str(row.get("status") or "")
        except Exception:
            pass

    stripped = ca._code(raw_tex)
    traces: List[Dict[str, Any]] = []
    for pos, group in sorted(ca._key_lists(stripped), key=lambda x: x[0]):
        line_no = stripped.count("\n", 0, pos) + 1
        excerpt = _extract_sentence_excerpt(raw_tex, pos)
        for raw_key in group.split(","):
            key = raw_key.strip()
            if not key or key == "*" or "#" in key:
                continue
            kl = key.lower()
            bib = bib_entries.get(kl, {})
            meta = meta_by_key.get(kl, {})
            cmap = cmap_by_key.get(kl, {})
            doi = (meta.get("doi") or bib.get("doi") or cmap.get("doi") or "").strip()
            url = (meta.get("url") or bib.get("url") or cmap.get("url") or "").strip()
            if not url and doi:
                url = f"https://doi.org/{doi}"
            status = audit_status.get(kl) or ("verified" if (doi or url) else ("metadata_only" if (bib or meta) else "unverified"))
            evidence = (
                cmap.get("summary")
                or cmap.get("justification")
                or cmap.get("relevance")
                or meta.get("abstract")
                or bib.get("note")
                or ""
            )
            traces.append(
                {
                    "key": key,
                    "line": line_no,
                    "claimExcerpt": excerpt,
                    "title": (meta.get("title") or bib.get("title") or cmap.get("title") or "").strip(),
                    "authors": (meta.get("authors") or bib.get("author") or cmap.get("authors") or ""),
                    "year": meta.get("year") or bib.get("year") or cmap.get("year") or "",
                    "venue": (meta.get("venue") or bib.get("journal") or bib.get("booktitle") or "").strip(),
                    "doi": doi,
                    "url": url,
                    "status": status,
                    "sourceEvidence": str(evidence).strip()[:450],
                }
            )

    return {
        "ok": True,
        "texFile": tex_path,
        "count": len(traces),
        "traces": traces,
    }


def main(argv: Optional[List[str]] = None) -> int:
    install_output_redaction()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("edit")
    p.add_argument("--file", required=True)
    p.add_argument("--instruction", required=True)
    p.add_argument("--selection-start", type=int, default=None)
    p.add_argument("--selection-end", type=int, default=None)
    p.add_argument("--error-log", default="")
    p.add_argument("--model", default="auto")
    p.set_defaults(fn=cmd_edit)

    p = sub.add_parser("trace-citations")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--tex-file", default="")
    p.set_defaults(fn=cmd_trace_citations)

    p = sub.add_parser("polish-abstract")
    p.add_argument("--text", default="")
    p.add_argument("--intro-text", default="")
    p.add_argument("--file", default="")
    p.add_argument("--run-dir", default="")
    p.add_argument("--mode", default="diagnose", choices=["diagnose", "rewrite"])
    p.add_argument("--model", default="auto")
    p.set_defaults(fn=cmd_polish_abstract)

    args = ap.parse_args(argv)
    try:
        emit(args.fn(args))
    except CopilotError as exc:
        emit({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        emit({"error": f"{type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
