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
#
# Author delivery report workflow (`report.md`) inspired by
# gulucaptain/Best-Paper-Writing-Skill (CC BY 4.0).

"""Generate author delivery `report.md` and `author_report.json` for a run folder.

Everything here is offline, deterministic, and non-blocking: missing intermediate
artifacts degrade gracefully instead of failing the pipeline.
"""

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

REPORT_MD_NAME = "report.md"
REPORT_JSON_NAME = "author_report.json"

_TODO_LINE_RE = re.compile(r"\\(?:TODO|note|directive|draftfig)\s*\{[^}]*\}|\[\s*TODO[^\]]*\]|\b(?:TODO|FIXME|TBD)\b|To be completed by the authors", re.I)
_DISCLAIMER_RE = re.compile(
    r"\b(?:limitation|future work|remains to be (?:evaluated|tested|explored)|does not (?:establish|prove|guarantee)|fail(?:s|ed)? (?:when|under|on))\b",
    re.I,
)


def _load_submission_tools():
    tools_dir = os.path.join(REPO_ROOT, "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import submission_tools as st  # noqa: WPS433

    return st


def _atomic_write_text(path: str, content: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".report_", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _read_json_safe(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _find_outline_json(run_dir: str) -> Optional[Dict[str, Any]]:
    for rel in (
        "outline.json",
        os.path.join("outline_agent_output", "outline.json"),
        os.path.join("latex_writeup", "outline.json"),
        os.path.join("content_refinement_workdir", "outline.json"),
    ):
        data = _read_json_safe(os.path.join(run_dir, rel))
        if data:
            return data
    return None


def _extract_todos_and_limitations(src_dir: str, main_tex: str, st: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    tex_path = os.path.join(src_dir, main_tex)
    text = st.read_text(tex_path)
    merged, _ = st.merge_inputs(text, src_dir)
    lines = st.code_lines(merged)
    todos: List[Dict[str, Any]] = []
    lim_notes: List[str] = []

    in_lim = False
    for num, raw in lines:
        s = raw.strip()
        if not s:
            continue
        if _TODO_LINE_RE.search(s):
            todos.append({"line": num, "text": s[:180]})
        sec_m = re.match(r"\\(?:sub)*section\*?\{([^}]*)\}", s)
        if sec_m:
            title = sec_m.group(1).lower()
            in_lim = any(k in title for k in ("limitation", "discussion", "conclusion", "future"))
            continue
        if in_lim and len(s) >= 24 and (_DISCLAIMER_RE.search(s) or "limitation" in s.lower()):
            cleaned = st._clean_prose_block(s).strip()
            if cleaned:
                lim_notes.append(f"L{num}: {cleaned[:200]}")

    return todos[:40], lim_notes[:20]


def generate_author_report(base_dir: str) -> Dict[str, Any]:
    """Build `report.md` and `author_report.json` inside `base_dir`."""
    run_dir = os.path.abspath(base_dir)
    if not os.path.isdir(run_dir):
        raise RuntimeError(f"运行目录不存在：{run_dir}")

    st = _load_submission_tools()
    outline = _find_outline_json(run_dir) or {}
    citation_audit = _read_json_safe(os.path.join(run_dir, "citation_audit.json")) or {}

    intro_plan = outline.get("intro_related_work_plan") if isinstance(outline.get("intro_related_work_plan"), dict) else {}
    routing = intro_plan.get("narrative_routing") if isinstance(intro_plan.get("narrative_routing"), dict) else {}
    claim_map = intro_plan.get("claim_evidence_map") if isinstance(intro_plan.get("claim_evidence_map"), list) else []
    plotting_plan = outline.get("plotting_plan") if isinstance(outline.get("plotting_plan"), list) else []

    audit_report: Optional[Dict[str, Any]] = None
    audit_error = ""
    main_tex = ""
    todos: List[Dict[str, Any]] = []
    lim_notes: List[str] = []
    try:
        src_dir = st.source_dir(run_dir)
        main_tex = st.pick_main_tex(src_dir)
        audit_report = st.cmd_manuscript_audit(argparse.Namespace(run_dir=run_dir))
        todos, lim_notes = _extract_todos_and_limitations(src_dir, main_tex, st)
    except Exception as exc:  # noqa: BLE001
        audit_error = str(exc)

    checks = (audit_report or {}).get("checks") or []
    ai_check = next((c for c in checks if c.get("id") == "ai_tells"), {})
    ai_metrics = ai_check.get("metrics") or {}

    # Surface any front-matter disclaimers caught in Abstract/Intro as Limitations transfer hints
    for item in ai_check.get("items") or []:
        if "Limitations" in str(item) or "防御性免责" in str(item):
            lim_notes.append(f"[文风体检提示移出摘要/引言] {item}")

    cit_summary = citation_audit.get("summary") if isinstance(citation_audit.get("summary"), dict) else {}
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    md: List[str] = [
        f"# PaperOrchestra 作者交付报告 (`report.md`)",
        "",
        f"- **运行目录**：`{os.path.basename(run_dir)}`",
        f"- **生成时间**：{ts}",
        f"- **主文档**：`{main_tex or '尚未生成'}`",
    ]
    if routing:
        md.extend([
            f"- **论文类型 (Paper Type)**：`{routing.get('paper_type', '未标注')}`",
            f"- **动机与张力 (Motivation / Tension)**：`{routing.get('motivation_type', '未标注')}` / `{routing.get('tension_type', '未标注')}`",
        ])
        if routing.get("core_handle"):
            md.append(f"- **核心记忆点 (Handle)**：{routing.get('core_handle')}")
    if cit_summary:
        md.append(
            f"- **引文审计**：正文引用 {cit_summary.get('cited', 0)} 篇 · "
            f"缺键 {cit_summary.get('missing_in_bib', 0)} · 未引用 {cit_summary.get('uncited', 0)} · "
            f"库内核验 {cit_summary.get('verified', 0)}/{cit_summary.get('bib_entries', 0)}"
        )

    md.extend(["", "## 1. 核心主张 — 证据对照表 (Claim–Evidence Map)", ""])
    if claim_map:
        md.extend([
            "| # | 核心主张 (Claim) | 对应证据 / 实验日志 | 建议措辞强度 | 适用范围限定 |",
            "|---|---|---|---|---|",
        ])
        for i, row in enumerate(claim_map[:20], 1):
            if not isinstance(row, dict):
                continue
            md.append(
                f"| {i} | {str(row.get('claim', '')).replace('|', '/')} | "
                f"{str(row.get('evidence', '')).replace('|', '/')} | "
                f"{str(row.get('wording_rung', row.get('wording', ''))).replace('|', '/')} | "
                f"{str(row.get('scope', '')).replace('|', '/')} |"
            )
    else:
        md.append("_当前 `outline.json` 未包含显式 `claim_evidence_map`（后续使用新版大纲生成时将自动填充）。_")

    md.extend(["", "## 2. 配图规划与图表一致性", ""])
    if plotting_plan:
        md.extend([
            "| Figure ID | 标题 / 结论 | 类型 | Figure 1 样式 | 数据源 |",
            "|---|---|---|---|---|",
        ])
        for fig in plotting_plan[:15]:
            if not isinstance(fig, dict):
                continue
            md.append(
                f"| `{fig.get('figure_id', '')}` | {str(fig.get('title', fig.get('claim', ''))).replace('|', '/')} | "
                f"{fig.get('plot_type', '')} | {fig.get('figure1_type', '-')} | {fig.get('data_source', '')} |"
            )
    else:
        md.append("_暂无 `plotting_plan` 记录。_")

    md.extend(["", "## 3. 量化文风与投稿体检摘要", ""])
    if audit_report:
        md.append(f"**综合结论**：{audit_report.get('summary', '')}")
        md.append("")
        if ai_metrics:
            md.extend([
                f"- **词数 / 句数**：{ai_metrics.get('words', 0)} 词 / {ai_metrics.get('sentences', 0)} 句",
                f"- **句尾分词短语占比**（获奖论文基线 ~8%）：{ai_metrics.get('participleTailRatio', 0):.1%}",
                f"- **对比警句排比占比**：{ai_metrics.get('antithesisRatio', 0):.1%}",
                f"- **逻辑连接词密度**：{ai_metrics.get('connectorPer100w', 0)} / 百词",
                f"- **连续短句峰值 / 摘要数字 / 引言数字**：{ai_metrics.get('shortRunMax', 0)} 句 / {ai_metrics.get('abstractNumbers', 0)} 个 / {ai_metrics.get('introNumbers', 0)} 个",
                "",
            ])
        for chk in checks:
            icon = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}.get(chk.get("state"), "INFO")
            md.append(f"- **[{icon}] {chk.get('label')}**：{chk.get('detail')}")
            for item in (chk.get("items") or [])[:6]:
                md.append(f"  - {item}")
    else:
        md.append(f"_无法运行 LaTeX 手稿体检：{audit_error or '源码尚未生成'}_")

    md.extend(["", "## 4. `[TODO]` 与待作者核实/补充清单", ""])
    if todos:
        for td in todos:
            md.append(f"- 第 {td['line']} 行：`{td['text']}`")
    else:
        md.append("- 未检出残留 `TODO` / `FIXME` / `To be completed by the authors` 标记。")

    md.extend(["", "## 5. 留给 Limitations / Discussion 的要点", ""])
    if lim_notes:
        for note in lim_notes:
            md.append(f"- {note}")
    else:
        md.append("- 请在投稿前复核实验适用边界、假设条件与失败案例，确保它们集中体现在 Limitations/Discussion 而非削弱摘要核心主张。")

    report_md = "\n".join(md).strip() + "\n"
    md_path = os.path.join(run_dir, REPORT_MD_NAME)
    json_path = os.path.join(run_dir, REPORT_JSON_NAME)

    payload = {
        "ok": True,
        "kind": "author-report",
        "reportPath": md_path,
        "jsonPath": json_path,
        "mainTex": main_tex,
        "routing": routing,
        "claimCount": len(claim_map),
        "figurePlanCount": len(plotting_plan),
        "todoCount": len(todos),
        "limitationsCount": len(lim_notes),
        "metrics": ai_metrics,
        "auditSummary": (audit_report or {}).get("summary", audit_error),
        "citationSummary": cit_summary,
        "summary": (
            f"已生成 report.md（{len(claim_map)} 条主张 / {len(todos)} 处 TODO / "
            f"{len(lim_notes)} 条 Limitations 笔记）"
        ),
    }

    _atomic_write_text(md_path, report_md)
    _atomic_write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload
