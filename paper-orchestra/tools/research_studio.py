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

"""Research Studio Backend CLI for PaperOrchestra Studio:
  - Offline Dataset Profiling (`data-profile`)
  - 7-Step Reproducible Data Analysis Sandbox with Self-Fixing Loop (`run-data-analysis`)
  - 5 Structured Scientific SOP Workflows (`run-sop`: paper_reading, lit_review, topic_ideation, reproduction, math_modeling)
  - Custom Skill Execution & HTTPS Markdown Skill Fetcher (`run-skill`, `fetch-skill-url`)
"""

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import json_repair
import numpy as np
import pandas as pd
import requests

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils import usage_tracker  # noqa: E402
from utils.providers import install_output_redaction  # noqa: E402


_REAL_STDOUT = sys.stdout
_EVENT_PREFIX = "@@PO_STUDIO_EVENT "


class ResearchStudioError(Exception):
    pass


def emit(payload: Dict[str, Any]) -> None:
    target = sys.stdout if sys.stdout is not sys.stderr else _REAL_STDOUT
    print(json.dumps(payload, ensure_ascii=False), file=target, flush=True)


def _est_tokens(text: str) -> int:
    return max(1, int(len(text) / 2.8)) if text else 0


def _emit_event(kind: str, **fields: Any) -> None:
    try:
        payload: Dict[str, Any] = {
            "kind": kind,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        print(
            _EVENT_PREFIX + json.dumps(payload, ensure_ascii=False),
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        pass


def _emit_progress(
    stage: str,
    status: str,
    message: str,
    *,
    model: str = "",
    detail: str = "",
    est_input_tokens: int = 0,
) -> None:
    fields: Dict[str, Any] = {
        "stage": stage,
        "status": status,
        "message": message,
    }
    if model:
        fields["model"] = model
    if detail:
        fields["detail"] = detail
    if est_input_tokens > 0:
        fields["estInputTokens"] = int(est_input_tokens)
    _emit_event("progress", **fields)


def _emit_log(message: str, level: str = "info") -> None:
    txt = (message or "").strip()
    if txt:
        _emit_event("log", line=txt[:1200], level=level)


@contextlib.contextmanager
def _track_studio_usage(out_dir: str):
    """Binds usage_tracker to out_dir and streams live token & cost updates over stderr."""
    os.makedirs(out_dir, exist_ok=True)
    usage_tracker.begin_run(out_dir)

    def _on_usage(rep: Dict[str, Any]) -> None:
        _emit_event("usage", usage=rep)

    usage_tracker.add_listener(_on_usage)
    holder: Dict[str, Any] = {"final": None}
    try:
        _emit_event("usage", usage=usage_tracker.report(out_dir))
        yield holder
    finally:
        usage_tracker.remove_listener(_on_usage)
        final_rep = usage_tracker.report(out_dir)
        usage_tracker.end_run(out_dir)
        holder["final"] = final_rep
        _emit_event("usage", usage=final_rep)


def _load_xlsx_stdlib(path: str) -> pd.DataFrame:
    """Reads first worksheet of a standard .xlsx workbook without requiring openpyxl."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path, "r") as zf:
        shared: List[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{ns}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
        sheet_names = sorted(n for n in zf.namelist() if re.match(r"^xl/worksheets/sheet\d+\.xml$", n))
        if not sheet_names:
            raise ResearchStudioError("Excel 文件中未找到工作表。")
        sroot = ET.fromstring(zf.read(sheet_names[0]))

        def _col_idx(ref: str) -> int:
            letters = re.match(r"([A-Za-z]+)", ref or "")
            if not letters:
                return 0
            idx = 0
            for ch in letters.group(1).upper():
                idx = idx * 26 + (ord(ch) - ord("A") + 1)
            return max(0, idx - 1)

        rows: List[List[Any]] = []
        for row_el in sroot.findall(f".//{ns}sheetData/{ns}row"):
            cells: Dict[int, Any] = {}
            max_col = -1
            for c_el in row_el.findall(f"{ns}c"):
                col_i = _col_idx(c_el.get("r", ""))
                max_col = max(max_col, col_i)
                ctype = c_el.get("t", "")
                if ctype == "inlineStr":
                    val: Any = "".join(t.text or "" for t in c_el.iter(f"{ns}t"))
                else:
                    v_el = c_el.find(f"{ns}v")
                    raw = v_el.text if v_el is not None else None
                    if raw is None:
                        val = None
                    elif ctype == "s" and raw.isdigit() and int(raw) < len(shared):
                        val = shared[int(raw)]
                    elif ctype == "b":
                        val = raw == "1"
                    else:
                        try:
                            val = float(raw) if "." in raw or "e" in raw.lower() else int(raw)
                        except ValueError:
                            val = raw
                cells[col_i] = val
            if max_col >= 0:
                rows.append([cells.get(i) for i in range(max_col + 1)])
        if not rows:
            return pd.DataFrame()
        headers = [str(h if h not in (None, "") else f"col_{i+1}").strip() for i, h in enumerate(rows[0])]
        return pd.DataFrame(rows[1:], columns=headers)


def _read_tabular_file(file_path: str) -> pd.DataFrame:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".csv", ".txt"):
        return pd.read_csv(file_path)
    if ext == ".tsv":
        return pd.read_csv(file_path, sep="\t")
    if ext == ".json":
        return pd.read_json(file_path)
    if ext in (".xlsx", ".xlsm"):
        try:
            return pd.read_excel(file_path)
        except Exception:
            return _load_xlsx_stdlib(file_path)
    if ext == ".xls":
        return pd.read_excel(file_path)
    if ext == ".sav":
        try:
            import pyreadstat  # type: ignore

            df, _ = pyreadstat.read_sav(file_path)
            return df
        except ImportError as exc:
            raise ResearchStudioError("读取 .sav SPSS 文件需要安装 pyreadstat，请先在 SPSS 中导出为 CSV 后导入。") from exc
    raise ResearchStudioError(f"暂不支持该数据格式：{ext}（支持 CSV / TSV / XLSX / JSON）。")


def _profile_dataframe(df: pd.DataFrame, file_path: str) -> Dict[str, Any]:
    rows, cols = int(df.shape[0]), int(df.shape[1])
    columns: List[Dict[str, Any]] = []
    warnings: List[str] = []

    dup_count = int(df.duplicated().sum()) if rows > 0 else 0
    if dup_count > 0:
        warnings.append(f"检测到 {dup_count} 条完全重复行，建议在清洗阶段去重。")

    for col in df.columns:
        ser = df[col]
        missing = int(ser.isna().sum())
        missing_pct = round((missing / rows) * 100.0, 2) if rows > 0 else 0.0
        if missing_pct > 20.0:
            warnings.append(f"列 `{col}` 缺失率达 {missing_pct}%（{missing}/{rows} 行）。")
        unique = int(ser.nunique(dropna=True))
        entry: Dict[str, Any] = {
            "name": str(col),
            "dtype": str(ser.dtype),
            "missing": missing,
            "missingPct": missing_pct,
            "unique": unique,
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
            "sampleValues": [str(v) for v in ser.dropna().head(4).tolist()],
        }
        if pd.api.types.is_numeric_dtype(ser) and ser.dropna().shape[0] > 0:
            clean = pd.to_numeric(ser, errors="coerce").dropna()
            if not clean.empty:
                entry["mean"] = round(float(clean.mean()), 4)
                entry["std"] = round(float(clean.std(ddof=1)) if len(clean) > 1 else 0.0, 4)
                entry["min"] = round(float(clean.min()), 4)
                entry["max"] = round(float(clean.max()), 4)
                q1, q3 = float(clean.quantile(0.25)), float(clean.quantile(0.75))
                iqr = q3 - q1
                if iqr > 0:
                    outliers = int(((clean < q1 - 3.0 * iqr) | (clean > q3 + 3.0 * iqr)).sum())
                    if outliers > 0:
                        warnings.append(f"数值列 `{col}` 存在 {outliers} 个极端离群点（> 3×IQR）。")
        columns.append(entry)

    preview_df = df.head(12).astype(object).fillna("")
    preview_rows = [
        {str(k): (v.item() if hasattr(v, "item") else v) for k, v in row.items()}
        for row in preview_df.to_dict(orient="records")
    ]
    return {
        "ok": True,
        "file": os.path.abspath(file_path),
        "fileName": os.path.basename(file_path),
        "rows": rows,
        "cols": cols,
        "duplicateRows": dup_count,
        "columns": columns,
        "previewRows": preview_rows,
        "qualityWarnings": warnings,
    }


def cmd_data_profile(args: argparse.Namespace) -> Dict[str, Any]:
    path = os.path.abspath(args.file)
    if not os.path.isfile(path):
        raise ResearchStudioError(f"找不到数据文件：{path}")
    df = _read_tabular_file(path)
    return _profile_dataframe(df, path)


def _strip_code_fences(raw: str) -> str:
    m = re.search(r"```python\s*\n([\s\S]*?)\n```", raw or "", re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"```\s*\n([\s\S]*?)\n```", raw or "")
    if m2:
        return m2.group(1).strip()
    return (raw or "").strip()


def _register_generated_figures(out_dir: str, script_path: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Promotes PNG/JPG/SVG charts in out_dir/figures into Plot Bundles so the user can circle-edit them."""
    from tools.figure_studio import cmd_ensure_bundle

    figs_dir = os.path.join(out_dir, "figures")
    fig_paths: List[str] = []
    bundles: List[Dict[str, Any]] = []
    if not os.path.isdir(figs_dir):
        return fig_paths, bundles
    for fn in sorted(os.listdir(figs_dir)):
        if fn.startswith(".") or not fn.lower().endswith((".png", ".jpg", ".jpeg", ".svg")):
            continue
        p = os.path.join(figs_dir, fn)
        if not os.path.isfile(p):
            continue
        fig_paths.append(p)
        try:
            res = cmd_ensure_bundle(
                argparse.Namespace(
                    dir=out_dir,
                    image=p,
                    title=os.path.splitext(fn)[0].replace("_", " "),
                    code_file=script_path if os.path.isfile(script_path) else "",
                    description="Generated during Research Studio Python execution",
                )
            )
            if res.get("bundle"):
                bundles.append(res["bundle"])
        except Exception:
            pass
    return fig_paths, bundles


def cmd_run_data_analysis(args: argparse.Namespace) -> Dict[str, Any]:
    from utils.llm_backend_utils import (
        call_llm_with_text_prompt,
        has_any_llm_credentials,
        resolve_task_model,
    )
    from utils.paper_banana_utils import identity_parse

    data_file = os.path.abspath(args.file)
    if not os.path.isfile(data_file):
        raise ResearchStudioError(f"找不到数据文件：{data_file}")
    if not has_any_llm_credentials():
        raise ResearchStudioError("未检测到可用大模型密钥，请先在「环境与算力」配置 API 密钥。")

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(os.path.join(out_dir, "figures"), exist_ok=True)
    with _track_studio_usage(out_dir) as usage_holder:
        _emit_progress("profile", "running", "正在扫描数据集轮廓、缺失率与异常值…")
        df = _read_tabular_file(data_file)
        profile = _profile_dataframe(df, data_file)
        _emit_progress(
            "profile",
            "completed",
            f"数据字典构建完成：{profile['rows']} 行 × {profile['cols']} 列（{len(profile['qualityWarnings'])} 条提醒）",
        )

        with open(os.path.join(out_dir, "data_dictionary.json"), "w", encoding="utf-8") as fh:
            json.dump(profile, fh, ensure_ascii=False, indent=2)

        shutil.copy2(data_file, os.path.join(out_dir, f"raw_data{os.path.splitext(data_file)[1].lower()}"))
        norm_csv = os.path.join(out_dir, "raw_data_normalized.csv")
        df.to_csv(norm_csv, index=False)

        code_model = resolve_task_model(args.model, "plot_code")
        writer_model = resolve_task_model(args.model, "section_writing")
        question = (args.question or "").strip() or "全面探索变量分布、关键相关关系、组间差异及显著性检验。"

        prompt = f"""You are a rigorous quantitative research scientist performing the 7-step reproducible data analysis workflow.
Working directory already contains `./raw_data_normalized.csv` (shape: {profile['rows']} rows x {profile['cols']} cols).
Create ONE self-contained Python script `analysis.py` that performs:
1. Reversible Data Cleaning: log each cleaning operation (e.g., duplicates dropped, missing handling, outlier flags) to `./cleaning_log.json` as a list of objects `{{"step": str, "rule": str, "rowsAffected": int, "reversible": true}}` (never overwrite raw_data).
2. Descriptive Statistics & Appropriate Statistical/Relationship Tests:
   - Compute summary stats for numeric & categorical columns.
   - Compute relevant group comparisons / correlations / regression fits for the research question below, including sample size `n`, exact metric/statistic, effect size (e.g., Cohen's d / Pearson r / Spearman rho / R^2), and bootstrap or normal 95% confidence interval!
   - Save ALL verified computed results as clean JSON to `./stats_results.json`.
3. Publication-Grade Visualizations:
   - Save 2 to 4 crisp 300-DPI PNG figures inside `./figures/` (e.g. `figures/fig_1_distribution.png`, `figures/fig_2_relationships.png`).
   - Use white background, legible labels with units, Okabe-Ito colorblind-safe colors, zero overlapping text, and ALWAYS wrap matplotlib calls with `matplotlib.use("Agg")` and `plt.close("all")`.

Research Question: {question}

Dataset Dictionary Summary:
{json.dumps({k: profile[k] for k in ("fileName", "rows", "cols", "duplicateRows", "columns", "qualityWarnings")}, ensure_ascii=False, indent=2)}

Sample Rows:
{json.dumps(profile["previewRows"][:8], ensure_ascii=False, indent=2)}

CRITICAL ENVIRONMENT CONSTRAINTS:
- Use ONLY Python standard library + `numpy` + `pandas` + `matplotlib` (`scipy`, `seaborn`, `sklearn`, `openpyxl` are NOT installed; implement t-test/p-value/correlation/OLS/CI using `numpy`, `pandas`, and `math`!).
- Do NOT call `plt.show()`.
- Ensure `./cleaning_log.json` and `./stats_results.json` are always written as valid JSON (convert numpy scalars with `float()` / `int()`).
- Return ONLY ```python ... ``` code.
"""

        _emit_progress(
            "codegen",
            "running",
            f"正在请求模型 {code_model} 编写可逆清洗与统计建模脚本 analysis.py…",
            model=code_model,
            est_input_tokens=_est_tokens(prompt),
        )
        resp = call_llm_with_text_prompt(
            prompt=prompt,
            model_name=code_model,
            generation_configs={"system_instruction": "Quantitative data scientist. Write deterministic, self-contained Python scripts using numpy, pandas, matplotlib only."},
            result_parsing_func=identity_parse,
        )
        script_code = _strip_code_fences(resp.get("parsed_response", ""))
        script_path = os.path.join(out_dir, "analysis.py")
        _emit_progress("codegen", "completed", "已生成初始 analysis.py 脚本", model=code_model)

        attempts = 0
        last_err = ""
        succeeded = False
        for attempt in range(1, 5):
            attempts = attempt
            _emit_progress("execute", "running", f"正在沙盒执行 analysis.py（第 {attempt}/4 轮）…")
            with open(script_path, "w", encoding="utf-8") as fh:
                fh.write(script_code + "\n")
            try:
                proc = subprocess.run(
                    [sys.executable, script_path],
                    cwd=out_dir,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                if proc.stdout and proc.stdout.strip():
                    _emit_log(proc.stdout.strip()[:1000], level="info")
                if proc.stderr and proc.stderr.strip():
                    _emit_log(proc.stderr.strip()[:1000], level="warn")
                stats_ok = os.path.isfile(os.path.join(out_dir, "stats_results.json"))
                if proc.returncode == 0 and stats_ok:
                    succeeded = True
                    _emit_progress("execute", "completed", f"analysis.py 第 {attempt} 轮执行通过")
                    break
                err_detail = (proc.stderr or proc.stdout or "").strip()[-1800:]
                if proc.returncode == 0 and not stats_ok:
                    err_detail += "\nMissing required output file: ./stats_results.json"
                last_err = err_detail
            except subprocess.TimeoutExpired:
                last_err = "Script timed out after 90 seconds."
                _emit_log(last_err, level="error")

            if attempt < 4:
                fix_prompt = (
                    f"`analysis.py` failed on attempt {attempt}:\n{last_err}\n\n"
                    "Remember: ONLY use Python stdlib + numpy + pandas + matplotlib (NO scipy/seaborn/sklearn). "
                    "Fix the script and ensure it writes `./cleaning_log.json`, `./stats_results.json`, and `./figures/*.png`. "
                    "Return ONLY ```python ... ```:\n"
                    f"```python\n{script_code}\n```"
                )
                _emit_progress(
                    "execute",
                    "running",
                    f"第 {attempt} 轮执行报错，正请求 {code_model} 自动诊断修复…",
                    model=code_model,
                    detail=last_err[:240],
                    est_input_tokens=_est_tokens(fix_prompt),
                )
                fix_resp = call_llm_with_text_prompt(
                    prompt=fix_prompt,
                    model_name=code_model,
                    generation_configs={"system_instruction": "Debug Python data analysis scripts cleanly."},
                    result_parsing_func=identity_parse,
                )
                script_code = _strip_code_fences(fix_resp.get("parsed_response", ""))

        if not succeeded:
            _emit_progress("execute", "error", f"代码连续 {attempts} 次执行未通过", detail=last_err[-400:])
            raise ResearchStudioError(f"数据分析代码连续 {attempts} 次执行未通过：\n{last_err[-600:]}")

        with open(os.path.join(out_dir, "stats_results.json"), "r", encoding="utf-8") as fh:
            stats_results = json.load(fh)
        cleaning_log: List[Dict[str, Any]] = []
        if os.path.isfile(os.path.join(out_dir, "cleaning_log.json")):
            try:
                with open(os.path.join(out_dir, "cleaning_log.json"), "r", encoding="utf-8") as fh:
                    cleaning_log = json.load(fh)
            except Exception:
                pass

        _emit_progress("figures", "running", "正在注册本次生成的图表至版本审阅台…")
        fig_paths, bundles = _register_generated_figures(out_dir, script_path)
        _emit_progress("figures", "completed", f"已注册 {len(bundles)} 组图表版本档案")

        report_prompt = f"""Based strictly on the verified Python execution outputs below, write a Chinese scientific Data Analysis Report in Markdown with 4 structured layers (现象 -> 证据 -> 解释 -> 局限), plus a Data Provenance table.

Research Question: {question}
Dataset: `{profile['fileName']}` ({profile['rows']} rows x {profile['cols']} cols)
Quality Warnings: {json.dumps(profile['qualityWarnings'], ensure_ascii=False)}
Cleaning Log (`cleaning_log.json`):
{json.dumps(cleaning_log, ensure_ascii=False, indent=2)[:4000]}
Verified Statistics (`stats_results.json`):
{json.dumps(stats_results, ensure_ascii=False, indent=2)[:9000]}
Generated Figures: {[os.path.basename(p) for p in fig_paths]}

STRICT RULES:
1. Every number, sample size n, effect size, CI, and p-value in your report MUST come verbatim from `stats_results.json` or `data_dictionary.json`. Never invent numbers.
2. Explicitly distinguish verified computed findings from speculative hypotheses or items requiring domain verification.
3. Structure with clear headings:
   ## 1. 数据概况与可逆清洗记录
   ## 2. 主要发现（现象）
   ## 3. 定量证据与统计检验（证据）
   ## 4. 机理解释与科学意义（解释）
   ## 5. 局限性与偏差检查（局限）
   ## 6. 溯源清单（原始数据 / 清洗日志 / 代码 / 图表）
"""
        _emit_progress(
            "report",
            "running",
            f"正在调用 {writer_model} 基于实测统计结果撰写四层分析报告…",
            model=writer_model,
            est_input_tokens=_est_tokens(report_prompt),
        )
        rep = call_llm_with_text_prompt(
            prompt=report_prompt,
            model_name=writer_model,
            generation_configs={"system_instruction": "Scientific analyst. Never extrapolate beyond computed statistics."},
            result_parsing_func=identity_parse,
        )
        report_md = (rep.get("parsed_response") or "").strip()
        report_path = os.path.join(out_dir, "report.md")
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(report_md + "\n")

        exp_snippet = (
            f"## Dataset & Cleaning Summary\n"
            f"- Source file: `{profile['fileName']}` ({profile['rows']} rows, {profile['cols']} columns)\n"
            f"- Reproducible script: `{script_path}`\n\n"
            f"## Verified Statistical Results (`stats_results.json`)\n"
            f"```json\n{json.dumps(stats_results, ensure_ascii=False, indent=2)[:6000]}\n```\n\n"
            f"## Analyst Synthesis\n{report_md}\n"
        )
        handoff = {
            "title": f"{os.path.splitext(profile['fileName'])[0]} - Quantitative Study",
            "recommendedTemplate": "journal-nature",
            "ideaMarkdown": f"# Research Question\n{question}\n\n# Dataset\n`{profile['fileName']}` ({profile['rows']} rows × {profile['cols']} columns)\n",
            "experimentalLogMarkdown": exp_snippet,
            "figurePaths": fig_paths,
        }
        with open(os.path.join(out_dir, "workspace_handoff.json"), "w", encoding="utf-8") as fh:
            json.dump(handoff, fh, ensure_ascii=False, indent=2)
        _emit_progress("report", "completed", "数据分析报告与工作台交接文件已归档", model=writer_model)

    return {
        "ok": True,
        "outDir": out_dir,
        "dataDictionary": profile,
        "cleaningLog": cleaning_log,
        "statsResults": stats_results,
        "figurePaths": fig_paths,
        "bundles": bundles,
        "reportMarkdown": report_md,
        "reportPath": report_path,
        "scriptPath": script_path,
        "attempts": attempts,
        "workspaceHandoff": handoff,
        "usageReport": usage_holder["final"],
    }


def _extract_attached_materials(files: List[str]) -> Tuple[str, List[Dict[str, Any]]]:
    """Reads PDFs, Markdown/text/code files, and hashes resources for reproducible SOP manifests."""
    from utils.pdf_utils import load_paper

    chunks: List[str] = []
    manifest: List[Dict[str, Any]] = []
    for raw_p in files:
        p = os.path.abspath(str(raw_p).strip())
        if not p or not os.path.isfile(p):
            continue
        sha = hashlib.sha256()
        with open(p, "rb") as fh:
            for block in iter(lambda: fh.read(65536), b""):
                sha.update(block)
        digest = sha.hexdigest()[:16]
        ext = os.path.splitext(p)[1].lower()
        read_mode = "full_read"
        excerpt = ""
        if ext == ".pdf":
            try:
                excerpt = load_paper(p, num_pages=40, min_size=10)
            except Exception:
                from pypdf import PdfReader

                reader = PdfReader(p)
                excerpt = "\n".join((pg.extract_text() or "") for pg in reader.pages[:40])
            excerpt = excerpt[:80000]
        elif ext in (".csv", ".tsv", ".xlsx", ".xls", ".json"):
            try:
                df = _read_tabular_file(p)
                prof = _profile_dataframe(df, p)
                excerpt = json.dumps(prof, ensure_ascii=False, indent=2)[:12000]
            except Exception as exc:
                excerpt = f"(Error reading tabular file: {exc})"
        else:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                excerpt = fh.read(40000)

        manifest.append(
            {
                "name": os.path.basename(p),
                "path": p,
                "sizeBytes": os.path.getsize(p),
                "sha256Prefix": digest,
                "readDepth": read_mode,
            }
        )
        chunks.append(f"### File: `{os.path.basename(p)}` (sha256:{digest}..., depth={read_mode})\n{excerpt}\n")
    return "\n\n".join(chunks), manifest


def _gather_lit_context(topic: str, library_items: List[Dict[str, Any]], query_online: bool, limit: int = 20) -> List[Dict[str, Any]]:
    seen = set()
    pool: List[Dict[str, Any]] = []
    for it in library_items or []:
        t = (it.get("title") or "").strip()
        norm = re.sub(r"[\W_]+", "", t.casefold())
        if norm and norm not in seen:
            seen.add(norm)
            pool.append(
                {
                    "title": t,
                    "authors": it.get("authors") or "",
                    "year": it.get("year"),
                    "venue": it.get("venue") or "",
                    "doi": it.get("doi") or "",
                    "url": it.get("url") or "",
                    "abstract": (it.get("abstractText") or "")[:900],
                    "notes": it.get("aiNotes") or "",
                    "readDepth": "full_read" if it.get("readStatus") == "full_read" else "abstract_only",
                    "gb7714": it.get("gb7714") or "",
                    "bibtex": it.get("bibtex") or "",
                }
            )
    if query_online and topic.strip():
        from tools.library_tools import _paper_to_library_item
        from utils.scholar_utils import multi_keyword_search

        for h in multi_keyword_search(topic.strip(), limit=limit, per_source=5):
            t = (h.get("title") or "").strip()
            norm = re.sub(r"[\W_]+", "", t.casefold())
            if not norm or norm in seen:
                continue
            seen.add(norm)
            converted = _paper_to_library_item(h, read_status="abstract_only")
            pool.append(
                {
                    "title": converted["title"],
                    "authors": converted["authors"],
                    "year": converted["year"],
                    "venue": converted["venue"],
                    "doi": converted["doi"],
                    "url": converted["url"],
                    "abstract": converted["abstractText"][:900],
                    "notes": "",
                    "readDepth": "abstract_only",
                    "gb7714": converted["gb7714"],
                    "bibtex": converted["bibtex"],
                }
            )
            if len(pool) >= limit + len(library_items or []):
                break
    return pool


def _run_numpy_math_benchmark(data_files: List[str], out_dir: str) -> Dict[str, Any]:
    """Runs pure-NumPy/Pandas EDA + multi-model benchmark (Linear, Ridge, Decision Stump Ensemble) if tabular data exists."""
    tab_files = [f for f in data_files if os.path.splitext(f)[1].lower() in (".csv", ".tsv", ".xlsx", ".xls")]
    if not tab_files:
        return {}
    df = _read_tabular_file(tab_files[0])
    num_df = df.select_dtypes(include=[np.number]).dropna()
    if num_df.shape[1] < 2 or num_df.shape[0] < 12:
        return {"dataset": os.path.basename(tab_files[0]), "rows": int(df.shape[0]), "cols": int(df.shape[1]), "note": "数值列不足 2 列或样本少于 12 行，已记录基础轮廓"}

    target_col = num_df.columns[-1]
    feature_cols = list(num_df.columns[:-1])
    X = num_df[feature_cols].to_numpy(dtype=float)
    y = num_df[target_col].to_numpy(dtype=float)

    split = max(4, int(0.8 * len(y)))
    X_tr, X_te = X[:split], X[split:]
    y_tr, y_te = y[:split], y[split:]
    mu, sd = X_tr.mean(axis=0), np.where(X_tr.std(axis=0) > 1e-8, X_tr.std(axis=0), 1.0)
    X_tr_n = (X_tr - mu) / sd
    X_te_n = (X_te - mu) / sd

    def _metrics(pred: np.ndarray) -> Dict[str, float]:
        err = y_te - pred
        mae = float(np.mean(np.abs(err)))
        rmse = float(np.sqrt(np.mean(err ** 2)))
        ss_tot = float(np.sum((y_te - y_te.mean()) ** 2)) or 1e-12
        r2 = float(1.0 - np.sum(err ** 2) / ss_tot)
        denom = np.where(np.abs(y_te) > 1e-6, np.abs(y_te), 1.0)
        mape = float(np.mean(np.abs(err) / denom) * 100.0)
        return {"MAE": round(mae, 4), "RMSE": round(rmse, 4), "MAPE_pct": round(mape, 2), "R2": round(r2, 4)}

    models_cmp: List[Dict[str, Any]] = []
    # 1. OLS
    Xa_tr = np.column_stack([np.ones(len(X_tr_n)), X_tr_n])
    Xa_te = np.column_stack([np.ones(len(X_te_n)), X_te_n])
    w_ols, *_ = np.linalg.lstsq(Xa_tr, y_tr, rcond=None)
    models_cmp.append({"model": "多元线性回归 (OLS)", **_metrics(Xa_te @ w_ols)})

    # 2. Ridge Regression
    lam = 1.0
    I = np.eye(Xa_tr.shape[1])
    I[0, 0] = 0.0
    w_ridge = np.linalg.solve(Xa_tr.T @ Xa_tr + lam * I, Xa_tr.T @ y_tr)
    models_cmp.append({"model": "岭回归 (Ridge λ=1.0)", **_metrics(Xa_te @ w_ridge)})

    # 3. Distance-weighted KNN
    k = min(5, max(2, len(X_tr_n) // 3))
    preds_knn = []
    for row in X_te_n:
        d = np.sqrt(np.sum((X_tr_n - row) ** 2, axis=1)) + 1e-6
        idx = np.argsort(d)[:k]
        wts = 1.0 / d[idx]
        preds_knn.append(float(np.sum(wts * y_tr[idx]) / np.sum(wts)))
    models_cmp.append({"model": f"距离加权近邻 (k={k})", **_metrics(np.array(preds_knn))})

    # Standardized feature importance + permutation sensitivity
    importance = sorted(
        [{"feature": str(col), "absStdWeight": round(float(abs(w)), 4)} for col, w in zip(feature_cols, w_ridge[1:])],
        key=lambda x: x["absStdWeight"],
        reverse=True,
    )

    # Plot benchmark comparison chart
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(os.path.join(out_dir, "figures"), exist_ok=True)
    chart_path = os.path.join(out_dir, "figures", "model_benchmark.png")
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2), dpi=220)
    short_labels = ["OLS", "Ridge", f"KNN(k={k})"]
    names = [short_labels[i] if i < len(short_labels) else f"Model {i + 1}" for i, _m in enumerate(models_cmp)]
    rmses = [m["RMSE"] for m in models_cmp]
    colors = ["#0072B2", "#009E73", "#E69F00"]
    axes[0].bar(names, rmses, color=colors[: len(names)], edgecolor="#1e293b", linewidth=0.7)
    axes[0].set_title(f"Model Comparison (Target: {target_col})")
    axes[0].set_ylabel("Test RMSE (lower is better)")
    axes[0].spines["top"].set_visible(False)
    axes[0].spines["right"].set_visible(False)

    top_imp = importance[:8][::-1]
    axes[1].barh([x["feature"] for x in top_imp], [x["absStdWeight"] for x in top_imp], color="#56B4E9", edgecolor="#1e293b", linewidth=0.7)
    axes[1].set_title("Standardized Feature Sensitivity")
    axes[1].set_xlabel("|Standardized Ridge Weight|")
    axes[1].spines["top"].set_visible(False)
    axes[1].spines["right"].set_visible(False)
    plt.tight_layout()
    plt.savefig(chart_path, dpi=220, facecolor="white")
    plt.close("all")

    bench = {
        "dataset": os.path.basename(tab_files[0]),
        "targetColumn": str(target_col),
        "trainRows": int(len(X_tr)),
        "testRows": int(len(X_te)),
        "models": models_cmp,
        "featureImportance": importance,
        "chartPath": chart_path,
    }
    with open(os.path.join(out_dir, "math_benchmark.json"), "w", encoding="utf-8") as fh:
        json.dump(bench, fh, ensure_ascii=False, indent=2)
    return bench


def cmd_run_sop(args: argparse.Namespace) -> Dict[str, Any]:
    from utils.llm_backend_utils import (
        call_llm_with_text_prompt,
        has_any_llm_credentials,
        resolve_task_model,
    )
    from utils.paper_banana_utils import identity_parse

    input_path = os.path.abspath(args.input_json)
    if not os.path.isfile(input_path):
        raise ResearchStudioError(f"找不到输入参数文件：{input_path}")
    with open(input_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    if not has_any_llm_credentials():
        raise ResearchStudioError("未检测到可用大模型密钥，请先在「环境与算力」配置 API 密钥。")

    sop = args.sop
    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "figures"), exist_ok=True)

    topic = (payload.get("topic") or payload.get("question") or "").strip()
    notes = (payload.get("notes") or "").strip()
    files = list(payload.get("files") or [])
    library_items = list(payload.get("libraryItems") or [])
    prefs_md = (payload.get("preferencesMarkdown") or "").strip()
    query_online = bool(payload.get("queryOnline", True))
    extra = payload.get("extra") or {}

    with _track_studio_usage(out_dir) as usage_holder:
        _emit_progress(
            "materials",
            "running",
            f"正在提取附带材料并计算 SHA-256 哈希清单（共 {len(files)} 个文件）…",
        )
        attached_text, resource_manifest = _extract_attached_materials(files)
        model = resolve_task_model(args.model, "literature_write" if sop == "lit_review" else "outline")
        _emit_progress(
            "materials",
            "completed",
            f"材料读取完成（{len(resource_manifest)} 份附件，目标模型 {model}）",
            model=model,
        )

        prefs_block = f"\n\n### Persistent Research Preferences & Long-Term Memory\n{prefs_md}\n" if prefs_md else ""

        extra_artifacts: Dict[str, Any] = {"resources": resource_manifest}
        recommended_template = "journal-nature"

        if sop == "paper_reading":
            if not attached_text and not notes:
                raise ResearchStudioError("论文精读 SOP 至少需要上传 1 篇 PDF 论文或填写论文文本摘要。")
            prompt = f"""Follow the 6-Step Deep Paper Reading SOP strictly based ONLY on the uploaded materials:
Step 1 Material Completeness Check (list what files/sections are present vs missing — e.g., SI/code/appendix).
Step 2 Full Paper Overview (Core Problem, Key Claim, Main Method, Main Result).
Step 3 Section-by-Section Role Map (Markdown table: 章节 | 核心任务 | 与上下章逻辑关系).
Step 4 Key Paragraph & Equation Deep-Dive (explain key assumptions, approximations, and empirical protocols with exact section/page provenance).
Step 5 Figure-Text Alignment Check (verify whether claims match described visual/quantitative evidence; flag any gap).
Step 6 Structural Architecture Diagram (valid Mermaid `flowchart LR` block inside ```mermaid ... ```) & Methodological Critique Checklist (strengths, hidden assumptions, reproducibility blockers).

Focus / Questions from user: {topic or '全面精读'}
Additional notes: {notes}
{prefs_block}

Uploaded Materials:
{attached_text[:72000]}
"""
        elif sop == "lit_review":
            recommended_template = "journal-chinese-gb7714" if (extra.get("language") == "zh") else "journal-nature"
            target_count = int(extra.get("targetCount") or 20)
            _emit_progress(
                "search",
                "running",
                f"正在整合本地文献库并检索多学术源（目标 {target_count} 篇，联网={query_online}）…",
            )
            lit_pool = _gather_lit_context(topic, library_items, query_online=query_online, limit=target_count)
            extra_artifacts["sources"] = lit_pool
            extra_artifacts["fullReadCount"] = sum(1 for x in lit_pool if x["readDepth"] == "full_read")
            extra_artifacts["abstractOnlyCount"] = sum(1 for x in lit_pool if x["readDepth"] == "abstract_only")
            _emit_progress(
                "search",
                "completed",
                f"来源池核验完成：共 {len(lit_pool)} 篇（全文精读 {extra_artifacts['fullReadCount']} 篇 · 仅摘要 {extra_artifacts['abstractOnlyCount']} 篇）",
            )

            prompt = f"""Execute the Anti-Fabrication 4-Step Thematic Literature Review SOP!
CRITICAL ANTI-HALLUCINATION RULES:
1. You may ONLY cite papers from the Verified Source Pool below, using `[1]`, `[2]`, ... matching the exact list indices! NEVER invent DOIs, authors, years, or papers.
2. Explicitly separate authors' own empirical findings from background citations, and record exact source read-depth (`full_read` vs `abstract_only`).
3. Anti-Hallucination Drafting Order: Draft the Thematic Literature Analysis & Evidence Matrix FIRST (so every synthesis claim is grounded in the real papers), THEN synthesize Research Background & Problem Statement!
4. Write in cohesive multi-sentence academic paragraphs (no fragmented 2-sentence chat bullets).

Deliver these exact Markdown sections:
## 1. 核心科学问题拆解
## 2. 统一证据提取矩阵（Markdown 表格：编号 [k] | 作者年份 | 阅读深度(全文/仅摘要) | 研究问题 | 核心方法 | 本研究自身发现 vs 转引他人结论 | 局限）
## 3. 主题式文献梳理与共识/冲突/空白分析（先写文献梳理再定背景，每句关键结论必须标注 `[k]`）
## 4. 研究背景与拟切入问题总结
## 5. 结论溯源索引表（结论摘要 | 对应来源 `[k]` | 证据边界）
## 6. 核验参考文献清单（GB/T 7714-2015 格式）

Topic / Research Scope: {topic or 'Literature Synthesis'}
User Constraints & Notes: {notes}
{prefs_block}

Verified Source Pool ({len(lit_pool)} entries):
{json.dumps([{ "idx": i + 1, **{k: v for k, v in p.items() if k != "bibtex"} } for i, p in enumerate(lit_pool)], ensure_ascii=False, indent=2)[:28000]}

Uploaded Full-Text Excerpts (if any):
{attached_text[:10000]}
"""
        elif sop == "topic_ideation":
            _emit_progress("search", "running", "正在汇总文献领域地图与既有研究基础…")
            lit_pool = _gather_lit_context(topic, library_items, query_online=query_online, limit=15)
            extra_artifacts["sources"] = lit_pool
            _emit_progress("search", "completed", f"已汇聚 {len(lit_pool)} 篇领域参考源")
            discipline = extra.get("discipline") or ""
            equipment = extra.get("equipment") or ""
            timeframe = extra.get("timeframe") or "2-3 周"
            prompt = f"""Execute the 5-Step Zero-to-One Research Topic Ideation SOP!
Output these exact structured Chinese Markdown sections:
## 1. 研究条件与资源约束清单（学科专业、导师主线、可用实验/算力条件、周期 `{timeframe}`、关键信息缺口）
## 2. 领域研究现状地图与 5 篇优先精读清单
- Explicitly state main trajectory, 3–5 competing technical routes, and key open scientific questions.
- Provide Priority Reading Order table (综述 -> 经典奠基 -> 近期前沿) ONLY using verified titles from the Source Pool / uploaded files, marking `full_read` vs `abstract_only`.
## 3. 三个具体候选选题横向对比（研究问题 | 灵感来源文献 | 相比已有工作的增量贡献 | 验证路线 | 主要风险与资源门槛）并给出首选推荐
## 4. 最小可行预实验验证方案（{timeframe} 内可执行：具体对照组、关键变量、评价指标、材料/算力清单与安全确认点）
## 5. 一页式导师讨论定题简报（明确区分「已有证据」与「待验证假设」）

Direction / Advisor Guidance: {topic}
Discipline: {discipline}
Available Instruments / Compute: {equipment}
Notes: {notes}
{prefs_block}

Verified Source Pool:
{json.dumps([{ "idx": i + 1, "title": p["title"], "year": p["year"], "venue": p["venue"], "readDepth": p["readDepth"], "abstract": p["abstract"][:400] } for i, p in enumerate(lit_pool)], ensure_ascii=False, indent=2)[:16000]}

Uploaded Paper Excerpts:
{attached_text[:14000]}
"""
        elif sop == "reproduction":
            target_level = extra.get("targetLevel") or "重新实现核心算法并用可控/替代基准定量验证"
            repo_url = extra.get("repoUrl") or ""
            prompt = f"""Execute the 7-Step Algorithm / Paper Reproduction SOP strictly grounded in the uploaded materials:
## 第一步：论文筛选、复现层级与验收边界清单（复现目标 `{target_level}`，明确要复现 vs 受数据/算力限制不复现的部分）
## 第二步：方法拆解与可运行工程规格说明（将方法拆解为独立模块：输入/输出张量或数据结构、关键公式、隐含假设、超参数与近似细节）
## 第三步：复现资源与文件哈希清单（列出附带文件的实际 SHA-256 前缀、版本约束及缺失资料的替代验证策略，不虚构未在材料中出现的基准分）
## 第四步：最小闭环代码脚手架（提供可运行的 Python 最小闭环代码 ```python ... ``` 及核心模块单元测试）
## 第五步：实验设置、指标语义对齐与参数记录规范（定义指标口径差异风险，如 IoU/F1 阈值定义）
## 第六步：三层验证对照表（正确性检查 / 模块消融实验 / 与原论文可比性条件审查）
## 第七步：复现归档 README 与边界声明模板

Target Paper / Task: {topic}
Official Repo URL (if any): {repo_url}
Notes: {notes}
{prefs_block}

Uploaded Materials & File Hash Manifest:
{json.dumps(resource_manifest, ensure_ascii=False, indent=2)}

Extracted Content:
{attached_text[:60000]}
"""
        elif sop == "math_modeling":
            recommended_template = "math-modeling-cumcm" if extra.get("contest") == "cumcm" else "math-modeling-mcm"
            _emit_progress("benchmark", "running", "正在运行 Python 探索性分析与多模型基准评测（OLS / Ridge / KNN）…")
            bench = _run_numpy_math_benchmark(files, out_dir)
            extra_artifacts["mathBenchmark"] = bench
            _emit_progress(
                "benchmark",
                "completed",
                f"数模基准评测完成（对比 {len(bench.get('models', []))} 类模型并生成特征灵敏度图）" if bench else "已检查附带数据，将生成完整数模推演框架",
            )
            prompt = f"""Execute the 6-Stage Mathematical Modeling Contest End-to-End SOP (problem restatement -> EDA -> assumptions & notation -> multi-model comparison -> sensitivity analysis -> contest draft)!
CRITICAL: If a Computed Python Benchmark section is provided below, quote those exact MAE/RMSE/MAPE/R² numbers and feature importance weights verbatim — NEVER invent model scores!

Deliver clean Markdown sections:
## 1. 竞赛摘要草稿（Summary Sheet / 摘要：核心思路 + 方法组合 + 定量提升）
## 2. 问题重述与子任务拆解（DAG 子任务链）
## 3. 模型假设与符号定义表（居中结构表格）
## 4. 数据清洗、探索性分析与周期/特征工程
## 5. 数学建模、求解算法与多模型定量对比（精确引用下方验证指标表）
## 6. 特征重要性排序、参数敏感性检验与模型评价/改进方向

Contest Problem Description:
{topic}

Additional Instructions: {notes}
{prefs_block}

Computed Python EDA & Multi-Model Benchmark (`math_benchmark.json`):
{json.dumps(bench, ensure_ascii=False, indent=2) if bench else 'No tabular CSV/XLSX data uploaded yet — design the exact modeling formulation and testable benchmark pipeline clearly.'}

Attached File Context:
{attached_text[:16000]}
"""
        else:
            raise ResearchStudioError(f"未知的 SOP 类型：{sop}")

        _emit_progress(
            "synthesize",
            "running",
            f"正在调用模型 {model} 执行推理与结构化报告生成（预计输入约 ~{_est_tokens(prompt)} tokens）…",
            model=model,
            est_input_tokens=_est_tokens(prompt),
        )
        resp = call_llm_with_text_prompt(
            prompt=prompt,
            model_name=model,
            generation_configs={"system_instruction": "You are PaperOrchestra Research Studio SOP Engine. Maintain strict provenance: never invent files, numbers, DOIs, or authors not grounded in provided inputs."},
            result_parsing_func=identity_parse,
        )
        report_md = (resp.get("parsed_response") or "").strip()
        report_path = os.path.join(out_dir, "report.md")
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(report_md + "\n")
        _emit_progress("synthesize", "completed", "模型生成完成，正在归档产物…", model=model)

        _emit_progress("finalize", "running", "正在写入报告、文献引用与工作台交接配置…")
        fig_paths, bundles = _register_generated_figures(out_dir, "")
        exp_log = report_md
        if extra_artifacts.get("sources"):
            bib_list = "\n\n".join(s.get("bibtex", "") for s in extra_artifacts["sources"] if s.get("bibtex"))
            if bib_list:
                with open(os.path.join(out_dir, "references.bib"), "w", encoding="utf-8") as fh:
                    fh.write(bib_list + "\n")
        if extra_artifacts.get("mathBenchmark"):
            exp_log += f"\n\n## Verified Computational Benchmark JSON\n```json\n{json.dumps(extra_artifacts['mathBenchmark'], ensure_ascii=False, indent=2)}\n```\n"

        handoff = {
            "title": topic[:80] or f"Research Studio - {sop}",
            "recommendedTemplate": recommended_template,
            "ideaMarkdown": f"# Topic & Research Scope\n{topic or sop}\n\n{notes}\n",
            "experimentalLogMarkdown": exp_log,
            "figurePaths": fig_paths,
        }
        with open(os.path.join(out_dir, "workspace_handoff.json"), "w", encoding="utf-8") as fh:
            json.dump(handoff, fh, ensure_ascii=False, indent=2)
        _emit_progress("finalize", "completed", "SOP 执行产物全部归档完成")

    out_payload = {
        "ok": True,
        "sop": sop,
        "outDir": out_dir,
        "reportMarkdown": report_md,
        "reportPath": report_path,
        "figurePaths": fig_paths,
        "bundles": bundles,
        "artifacts": extra_artifacts,
        "workspaceHandoff": handoff,
        "usageReport": usage_holder["final"],
    }
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(out_payload, fh, ensure_ascii=False, indent=2)
    return out_payload


def cmd_run_skill(args: argparse.Namespace) -> Dict[str, Any]:
    from utils.llm_backend_utils import (
        call_llm_with_text_prompt,
        has_any_llm_credentials,
        resolve_task_model,
    )
    from utils.paper_banana_utils import identity_parse

    skill_path = os.path.abspath(args.skill_file)
    if not os.path.isfile(skill_path):
        raise ResearchStudioError(f"找不到技能文件：{skill_path}")
    if not has_any_llm_credentials():
        raise ResearchStudioError("未检测到可用大模型密钥，请先在「环境与算力」配置 API 密钥。")

    with open(skill_path, "r", encoding="utf-8", errors="replace") as fh:
        skill_text = fh.read()

    files: List[str] = []
    if args.files_json and os.path.isfile(args.files_json):
        with open(args.files_json, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, list):
            files = [str(x) for x in loaded]

    prefs_md = ""
    if args.prefs_file and os.path.isfile(args.prefs_file):
        with open(args.prefs_file, "r", encoding="utf-8", errors="replace") as fh:
            prefs_md = fh.read().strip()

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    with _track_studio_usage(out_dir) as usage_holder:
        _emit_progress("materials", "running", f"正在读取技能正文与 {len(files)} 份附件材料…")
        attached_text, manifest = _extract_attached_materials(files)
        full_prompt = (
            f"{skill_text.strip()}\n\n"
            + (f"### Persistent Research Preferences & Long-Term Memory\n{prefs_md}\n\n" if prefs_md else "")
            + f"### User Task Request\n{args.prompt.strip()}\n\n"
            + (f"### Attached Reference Materials\n{attached_text[:24000]}\n" if attached_text else "")
        )
        model = resolve_task_model(args.model, "outline")
        _emit_progress(
            "materials",
            "completed",
            f"准备就绪，将调用 {model} 执行技能（约 ~{_est_tokens(full_prompt)} 输入 tokens）",
            model=model,
        )
        _emit_progress(
            "synthesize",
            "running",
            f"正在调用模型 {model} 执行技能指令…",
            model=model,
            est_input_tokens=_est_tokens(full_prompt),
        )
        resp = call_llm_with_text_prompt(
            prompt=full_prompt,
            model_name=model,
            generation_configs={"system_instruction": "Execute the custom academic skill instructions faithfully without fabricating evidence."},
            result_parsing_func=identity_parse,
        )
        output_md = (resp.get("parsed_response") or "").strip()
        output_path = os.path.join(out_dir, "skill_output.md")
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(output_md + "\n")
        _emit_progress("synthesize", "completed", "技能执行完成并已保存结果", model=model)

    return {
        "ok": True,
        "outDir": out_dir,
        "outputPath": output_path,
        "outputMarkdown": output_md,
        "resources": manifest,
        "usageReport": usage_holder["final"],
    }


def _normalize_github_raw_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme != "https":
        raise ResearchStudioError("仅允许使用 https:// 开头的远程技能地址。")
    if parsed.netloc == "github.com":
        parts = parsed.path.strip("/").split("/")
        # Convert github.com/owner/repo/blob/branch/path -> raw.githubusercontent.com/owner/repo/branch/path
        if len(parts) >= 5 and parts[2] == "blob":
            raw_path = "/".join([parts[0], parts[1], parts[3], *parts[4:]])
            return f"https://raw.githubusercontent.com/{raw_path}"
        # Repo root github.com/owner/repo -> try main/SKILL.md
        if len(parts) == 2:
            return f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/main/SKILL.md"
    return url.strip()


def cmd_fetch_skill_url(args: argparse.Namespace) -> Dict[str, Any]:
    raw_url = _normalize_github_raw_url(args.url)
    try:
        rsp = requests.get(raw_url, timeout=15, headers={"User-Agent": "PaperOrchestraStudio/1.0"})
        if rsp.status_code != 200 and raw_url.endswith("/main/SKILL.md"):
            raw_url = raw_url[:-len("/main/SKILL.md")] + "/master/SKILL.md"
            rsp = requests.get(raw_url, timeout=15, headers={"User-Agent": "PaperOrchestraStudio/1.0"})
        if rsp.status_code != 200:
            raise ResearchStudioError(f"拉取技能失败（HTTP {rsp.status_code}）：{raw_url}")
        content = rsp.text[:512000]
    except ResearchStudioError:
        raise
    except Exception as exc:
        raise ResearchStudioError(f"无法连接到 {raw_url}：{exc}") from exc

    name = "imported-skill"
    desc = ""
    m = re.match(r"^---\s*\n([\s\S]*?)\n---", content)
    if m:
        fm = m.group(1)
        nm = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        dm = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
        if nm:
            name = nm.group(1).strip().strip("\"'")
        if dm:
            desc = dm.group(1).strip().strip("\"'")
    return {
        "ok": True,
        "resolvedUrl": raw_url,
        "name": name,
        "description": desc,
        "content": content,
    }


def main(argv: Optional[List[str]] = None) -> int:
    install_output_redaction()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("data-profile")
    p.add_argument("--file", required=True)
    p.set_defaults(fn=cmd_data_profile)

    p = sub.add_parser("run-data-analysis")
    p.add_argument("--file", required=True)
    p.add_argument("--question", default="")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--model", default="auto")
    p.set_defaults(fn=cmd_run_data_analysis)

    p = sub.add_parser("run-sop")
    p.add_argument("--sop", required=True, choices=["paper_reading", "lit_review", "topic_ideation", "reproduction", "math_modeling"])
    p.add_argument("--input-json", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--model", default="auto")
    p.set_defaults(fn=cmd_run_sop)

    p = sub.add_parser("run-skill")
    p.add_argument("--skill-file", required=True)
    p.add_argument("--prompt", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--files-json", default="")
    p.add_argument("--prefs-file", default="")
    p.add_argument("--model", default="auto")
    p.set_defaults(fn=cmd_run_skill)

    p = sub.add_parser("fetch-skill-url")
    p.add_argument("--url", required=True)
    p.set_defaults(fn=cmd_fetch_skill_url)

    global _REAL_STDOUT
    args = ap.parse_args(argv)
    orig_stdout = sys.stdout
    _REAL_STDOUT = orig_stdout
    try:
        # Redirect incidental print() diagnostics from shared utils to stderr so
        # stdout stays clean for the final single-line JSON response.
        sys.stdout = sys.stderr
        result = args.fn(args)
    except ResearchStudioError as exc:
        result = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        result = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        sys.stdout = orig_stdout
    emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
