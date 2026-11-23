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
# Parts of this file are adapted from Lattice (https://github.com/leo1oel/lattice),
# Copyright 2026 Leonardo and the Lattice contributors, licensed under the
# Apache License, Version 2.0. Adapted (ported from Rust to Python and changed):
#   src-tauri/lattice-core/src/latex/arxiv.rs, latex/arxiv/source.rs  -> cmd_arxiv, strip_comments
#   src-tauri/lattice-core/src/latex/submission_review.rs            -> cmd_blind
#   src-tauri/lattice-core/src/latex/build_log.rs                    -> cmd_diagnose
#   src-tauri/lattice-core/src/latex/changes.rs                      -> cmd_diff
#   src-tauri/lattice-core/src/citation_audit.rs (idea only)         -> cmd_cite_online
#
# Manuscript pre-submission audit (`cmd_manuscript_audit`) adapts rules and
# detection patterns from Boom5426/Nature-Paper-Skills (MIT / Apache-2.0):
#   scientific-prose-style/scripts/ai_tells.py
#   submission-audit/scripts/check_figure_refs.py
#   draft-marker-discipline/scripts/prose_wordcount.py
#   stats-reporting-audit, data-availability, nature-portfolio-playbook
# See THIRD_PARTY_NOTICES.md at the repository root.

"""
Pre-submission tools for a finished run. Each subcommand prints ONE JSON object
on stdout (the last line) and exits 0 whenever it ran; failures are reported as
{"error": "<Chinese message>"}.

    python tools/submission_tools.py arxiv            RUN_DIR [--out ZIP] [--keep-comments] [--merge-inputs]
    python tools/submission_tools.py blind            RUN_DIR
    python tools/submission_tools.py diagnose         RUN_DIR [--find-packages]
    python tools/submission_tools.py versions         RUN_DIR
    python tools/submission_tools.py diff             OLD_TEX NEW_TEX --out PDF [--only-changed-pages]
    python tools/submission_tools.py cite-online      RUN_DIR [--timeout SEC]
    python tools/submission_tools.py manuscript-audit RUN_DIR

Check rows have the shape {"id", "state": "ok"|"warn"|"fail", "label", "detail", "items": [...]}.
Nothing inside RUN_DIR is changed, except that cite-online saves its report as
RUN_DIR/citation_online_check.json. Builds run in temporary folders.
"""

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils import citation_audit as ca  # noqa: E402

REFINEMENT_DIR = "content_refinement_workdir"
WRITEUP_DIR = "latex_writeup"
FINAL_TEX = "final_refined_paper.tex"
FALLBACK_TEX = ["raw_draft_paper.tex", "template.tex"]
SKIP_DIRS = {"pdf_screenshots", "peer_reviews", "__MACOSX", "__pycache__"}
BUILD_OUTPUT_EXTS = {
    "aux", "log", "blg", "fdb_latexmk", "fls", "out", "bcf", "toc", "lof", "lot",
    "synctex", "gz", "nav", "snm", "vrb", "xdv", "dvi", "run.xml",
}
ARXIV_SIZE_LIMIT = 50_000_000
BUILD_TIMEOUT = 300
ONLINE_FILE = "citation_online_check.json"
# Scratch builds go next to the repo, not the system temp folder: the repo may
# sit on a roomy external disk while the system disk is nearly full.
SCRATCH_ROOT = os.path.join(REPO_ROOT, ".tmp_submission")


class ToolError(Exception):
    pass


# --------------------------------------------------------------------------- helpers

def emit(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def row(id_: str, state: str, label: str, detail: str = "", items: Optional[List[Any]] = None) -> Dict[str, Any]:
    return {"id": id_, "state": state, "label": label, "detail": detail, "items": items or []}


def read_text(path: str) -> str:
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def is_tex_document(path: str) -> bool:
    try:
        text = read_text(path)
    except OSError:
        return False
    for line in text.splitlines():
        i = comment_start(line)
        if "\\documentclass" in (line if i is None else line[:i]):
            return True
    return False


def source_dir(run_dir: str) -> str:
    for name in (REFINEMENT_DIR, WRITEUP_DIR):
        d = os.path.join(run_dir, name)
        if os.path.isdir(d) and not os.path.islink(d) and any(
            n.lower().endswith(".tex") and os.path.isfile(os.path.join(d, n)) for n in os.listdir(d)
        ):
            return d
    raise ToolError("这次运行还没有生成 LaTeX 源码，等运行出 PDF 后再试。")


def tex_documents(d: str) -> List[str]:
    return sorted(
        n for n in os.listdir(d)
        if n.lower().endswith(".tex") and os.path.isfile(os.path.join(d, n)) and is_tex_document(os.path.join(d, n))
    )


def pick_main_tex(d: str) -> str:
    """Same order as the app's export (export.rs pick_main_tex)."""
    docs = tex_documents(d)
    if not docs:
        raise ToolError("运行目录里没有完整的 LaTeX 文档（缺少 \\documentclass）。")
    if FINAL_TEX in docs:
        return FINAL_TEX
    mtime = lambda n: os.path.getmtime(os.path.join(d, n))  # noqa: E731
    compiled = [n for n in docs if os.path.isfile(os.path.join(d, n[:-4] + ".pdf"))]
    if compiled:
        return max(compiled, key=lambda n: (mtime(n), n))
    for n in FALLBACK_TEX:
        if n in docs:
            return n
    return max(docs, key=lambda n: (mtime(n), n))


def tex_env() -> Dict[str, str]:
    env = dict(os.environ)
    extra = ["/Library/TeX/texbin", "/usr/local/bin", "/opt/homebrew/bin"]
    env["PATH"] = os.pathsep.join([env.get("PATH", "")] + [p for p in extra if os.path.isdir(p)])
    return env


def which(tool: str) -> Optional[str]:
    return shutil.which(tool, path=tex_env()["PATH"])


def run(cmd: List[str], cwd: str, timeout: int = BUILD_TIMEOUT) -> Tuple[int, str, bool]:
    """(exit code, combined output, timed out)."""
    try:
        p = subprocess.run(
            cmd, cwd=cwd, env=tex_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
        )
        return p.returncode, p.stdout.decode("utf-8", errors="replace"), False
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else ""
        return -1, out, True


def copy_sources(src: str, dest: str) -> None:
    """Copy a source folder without build output, review material, hidden files or symlinks."""
    for root, dirs, files in os.walk(src):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")
                   and not os.path.islink(os.path.join(root, x))]
        rel = os.path.relpath(root, src)
        for name in files:
            p = os.path.join(root, name)
            if name.startswith(".") or os.path.islink(p):
                continue
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in BUILD_OUTPUT_EXTS:
                continue
            target = os.path.join(dest, rel, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(p, target)


def latexmk_build(folder: str, main: str, bibtex: bool = True, timeout: int = BUILD_TIMEOUT,
                  halt: bool = False) -> Dict[str, Any]:
    """Build like the pipeline does (nonstopmode, keep going past errors) unless `halt`.

    `ok` means a PDF came out; `errors` lists what TeX complained about.
    """
    if not which("latexmk"):
        raise ToolError("没找到 latexmk。请安装 MacTeX / TeX Live（Windows 装 MiKTeX 并勾选 Perl）后再试。")
    cmd = ["latexmk", "-pdf", "-recorder", "-interaction=nonstopmode", "-file-line-error", "-no-shell-escape"]
    cmd.append("-halt-on-error" if halt else "-f")
    if not bibtex:
        cmd.append("-bibtex-")
    cmd.append(main)
    started = time.time()
    code, out, timed_out = run(cmd, folder, timeout)
    pdf = os.path.join(folder, main[:-4] + ".pdf")
    log_path = os.path.join(folder, main[:-4] + ".log")
    log = read_text(log_path) if os.path.isfile(log_path) else out
    pages = None
    m = re.search(r"Output written on .+? \((\d+) pages?", log.replace("\n", ""))
    if m:
        pages = int(m.group(1))
    pdf_fresh = os.path.isfile(pdf) and os.path.getmtime(pdf) >= started - 1
    return {
        "ok": pdf_fresh and not timed_out,
        "clean": code == 0 and pdf_fresh and not timed_out,
        "errors": parse_log(log, main)["errors"] if not (code == 0) else [],
        "timedOut": timed_out,
        "seconds": round(time.time() - started, 1),
        "pages": pages,
        "log": log,
        "pdf": pdf,
    }


# --------------------------------------------------------------------------- comments (from Lattice source.rs)

VERBATIM_ENVS = ["verbatim", "verbatim*", "Verbatim", "Verbatim*", "BVerbatim", "LVerbatim",
                 "lstlisting", "minted", "alltt", "filecontents", "filecontents*"]
VERBATIM_ARG_CMDS = {"url", "href", "path"}
TODO_RE = re.compile(r"(?i:\btodo\b|\bfixme\b)|\bXXX\b")


def _verbatim_arg_end(line: str, backslash: int) -> Optional[int]:
    n = len(line)
    j = backslash + 1
    while j < n and line[j].isascii() and line[j].isalpha():
        j += 1
    name = line[backslash + 1:j]
    if name == "verb":
        k = j + 1 if j < n and line[j] == "*" else j
        if k >= n:
            return None
        close = line.find(line[k], k + 1)
        return n if close < 0 else close + 1
    if name not in VERBATIM_ARG_CMDS:
        return None
    k = j
    while k < n and line[k].isspace():
        k += 1
    if k >= n or line[k] != "{":
        return None
    depth = 0
    for at in range(k, n):
        if line[at] == "{":
            depth += 1
        elif line[at] == "}":
            depth -= 1
            if depth == 0:
                return at + 1
    return n


def comment_start(line: str) -> Optional[int]:
    """First % that is neither escaped nor inside \\verb / \\url / \\href / \\path."""
    at, n = 0, len(line)
    while at < n:
        c = line[at]
        if c == "%":
            return at
        if c == "\\":
            end = _verbatim_arg_end(line, at)
            at = end if end is not None else at + 2
        else:
            at += 1
    return None


def strip_comments(source: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Source without % comments, and the comments it had (line, text, todo).

    A whole-line comment goes with its line; a trailing comment keeps its %
    (it still swallows the line end) and loses its text; a bare % stays; a
    comment environment goes whole; verbatim text is never touched.
    """
    out: List[str] = []
    comments: List[Dict[str, Any]] = []
    verbatim: Optional[str] = None
    in_comment_env = False
    for number, line in enumerate(source.splitlines(keepends=True), 1):
        body = line.rstrip("\r\n")
        ending = line[len(body):]

        def note(said: str) -> None:
            said = said.strip()
            if said:
                comments.append({"line": number, "text": said[:200], "todo": bool(TODO_RE.search(said))})

        if verbatim:
            if "\\end{%s}" % verbatim in body:
                verbatim = None
            out.append(line)
            continue
        if in_comment_env:
            if "\\end{comment}" in body:
                in_comment_env = False
            note(body.replace("\\end{comment}", ""))
            continue
        cs = comment_start(body)
        code = body if cs is None else body[:cs]
        if code.lstrip().startswith("\\begin{comment}"):
            in_comment_env = "\\end{comment}" not in code
            note(body.replace("\\begin{comment}", "").replace("\\end{comment}", ""))
            continue
        opened = [(code.find("\\begin{%s}" % e) + len("\\begin{%s}" % e), e) for e in VERBATIM_ENVS
                  if "\\begin{%s}" % e in code]
        if opened:
            pos, env = min(opened)
            if "\\end{%s}" % env not in body[pos:]:
                verbatim = env
            out.append(line)
            continue
        if cs is None:
            out.append(line)
            continue
        said = body[cs + 1:]
        if not said.strip():
            out.append(line)
            continue
        note(said)
        if body[:cs].strip():
            out.append(body[:cs + 1] + ending)
    return "".join(out), comments


def code_lines(source: str) -> List[Tuple[int, str]]:
    """(line number, code without comment) for lines TeX reads as commands."""
    result = []
    verbatim = None
    in_comment_env = False
    for number, line in enumerate(source.splitlines(), 1):
        if verbatim:
            if "\\end{%s}" % verbatim in line:
                verbatim = None
            continue
        if in_comment_env:
            if "\\end{comment}" in line:
                in_comment_env = False
            continue
        cs = comment_start(line)
        code = line if cs is None else line[:cs]
        if code.lstrip().startswith("\\begin{comment}"):
            in_comment_env = "\\end{comment}" not in code
            continue
        for e in VERBATIM_ENVS:
            if "\\begin{%s}" % e in code and "\\end{%s}" % e not in code:
                verbatim = e
        if code.strip():
            result.append((number, code))
    return result


INPUT_RE = re.compile(r"\\(input|include)\s*\{([^{}]+)\}")


def merge_inputs(text: str, folder: str, depth: int = 0, merged: Optional[List[str]] = None) -> Tuple[str, List[str]]:
    """Replace \\input{x} / \\include{x} of files inside `folder` by their text."""
    merged = merged if merged is not None else []
    if depth > 16:
        return text, merged
    out_lines = []
    for line in text.splitlines(keepends=True):
        cs = comment_start(line)
        code = line if cs is None else line[:cs]
        m = INPUT_RE.search(code)
        if not m:
            out_lines.append(line)
            continue
        name = m.group(2).strip()
        cand = name if name.lower().endswith(".tex") else name + ".tex"
        path = os.path.normpath(os.path.join(folder, cand))
        if not path.startswith(os.path.normpath(folder) + os.sep) or not os.path.isfile(path):
            out_lines.append(line)
            continue
        inner, _ = merge_inputs(read_text(path), folder, depth + 1, merged)
        inner = re.split(r"\\endinput\b", inner, maxsplit=1)[0]
        rel = os.path.relpath(path, folder).replace(os.sep, "/")
        if rel not in merged:
            merged.append(rel)
        before, after = code[:m.start()], line[m.end():]
        prefix = "\\clearpage\n" if m.group(1) == "include" else ""
        out_lines.append(before + prefix + inner.rstrip("\n") + "\n" + (after if after.strip() else ""))
    return "".join(out_lines), merged


# --------------------------------------------------------------------------- arxiv

def recorded_inputs(folder: str, main: str) -> Tuple[List[str], List[str]]:
    """Relative files TeX read (INPUT) and wrote (OUTPUT) inside `folder`, from the .fls."""
    fls = os.path.join(folder, main[:-4] + ".fls")
    if not os.path.isfile(fls):
        return [], []
    pwd = folder
    inputs, outputs = [], []
    real_folder = os.path.realpath(folder)
    for line in read_text(fls).splitlines():
        if line.startswith("PWD "):
            pwd = line[4:].strip()
            continue
        kind, _, path = line.partition(" ")
        if kind not in ("INPUT", "OUTPUT"):
            continue
        full = os.path.realpath(path if os.path.isabs(path) else os.path.join(pwd, path))
        if not full.startswith(real_folder + os.sep):
            continue
        rel = os.path.relpath(full, real_folder).replace(os.sep, "/")
        (inputs if kind == "INPUT" else outputs).append(rel)
    return sorted(set(inputs)), sorted(set(outputs))


def cmd_arxiv(a: argparse.Namespace) -> Dict[str, Any]:
    run_dir = os.path.abspath(a.run_dir)
    src = source_dir(run_dir)
    main = pick_main_tex(src)
    checks: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="po-arxiv-") as scratch:
        build_dir = os.path.join(scratch, "build")
        copy_sources(src, build_dir)
        first = latexmk_build(build_dir, main, bibtex=True)
        if not os.path.isfile(os.path.join(build_dir, main[:-4] + ".fls")):
            raise ToolError("论文在临时文件夹里编译失败，没法确定要打包哪些文件。请先看「LaTeX 诊断」。")
        inputs, outputs = recorded_inputs(build_dir, main)
        written = set(outputs)
        files = [p for p in inputs if p not in written or p.endswith(".bbl")]
        if main not in files:
            files.insert(0, main)
        main_text = read_text(os.path.join(build_dir, main))
        uses_bib = bool(re.search(r"\\(?:bibliography\s*\{|printbibliography\b)",
                                  "\n".join(c for _, c in code_lines(main_text))))
        bbl = main[:-4] + ".bbl"
        has_bbl = os.path.isfile(os.path.join(build_dir, bbl))
        if has_bbl and bbl not in files:
            files.append(bbl)
        # Figures referenced but missing (latexmk would have failed with halt-on-error).
        missing = sorted(set(re.findall(r"File [`']([^`']+)[`'] not found", first["log"])))

        stage = os.path.join(scratch, "stage")
        os.makedirs(stage)
        comments_all: List[Dict[str, Any]] = []
        merged: List[str] = []
        for rel in files:
            srcp = os.path.join(build_dir, rel)
            if not os.path.isfile(srcp):
                continue
            dst = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(srcp, dst)
        if a.merge_inputs:
            text, merged = merge_inputs(read_text(os.path.join(stage, main)), stage)
            with open(os.path.join(stage, main), "w", encoding="utf-8") as f:
                f.write(text)
            for rel in merged:
                p = os.path.join(stage, rel)
                if os.path.isfile(p):
                    os.remove(p)
            files = [f for f in files if f not in merged]
        for rel in files:
            p = os.path.join(stage, rel)
            ext = rel.rsplit(".", 1)[-1].lower()
            if ext not in ("tex", "tikz", "pgf", "ltx", "inc") or not os.path.isfile(p):
                continue
            stripped, comments = strip_comments(read_text(p))
            for c in comments:
                c["file"] = rel
            comments_all.extend(comments)
            if not a.keep_comments:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(stripped)
        sizes = {rel: os.path.getsize(os.path.join(stage, rel)) for rel in files
                 if os.path.isfile(os.path.join(stage, rel))}
        total = sum(sizes.values())

        test_dir = os.path.join(scratch, "test")
        shutil.copytree(stage, test_dir)
        test = latexmk_build(test_dir, main, bibtex=False, timeout=180)

        figures = [f for f in files if f.rsplit(".", 1)[-1].lower() in
                   ("pdf", "png", "jpg", "jpeg", "eps", "ps", "svg", "gif", "tif", "tiff", "mps")]
        if missing:
            checks.append(row("figures", "fail", "图片和输入文件", f"有 {len(missing)} 个文件找不到", missing))
        else:
            checks.append(row("figures", "ok", "图片和输入文件", f"{len(figures)} 张图片、{len(files)} 个文件都在包里"))
        if not uses_bib:
            checks.append(row("bibliography", "ok", "参考文献", "论文没有用 BibTeX，不需要 .bbl"))
        elif has_bbl:
            checks.append(row("bibliography", "ok", "参考文献", f"已带上 {bbl}（arXiv 不运行 BibTeX）"))
        else:
            checks.append(row("bibliography", "fail", "参考文献", "论文用了 \\bibliography，但没生成 .bbl，arXiv 上参考文献会是空的"))
        if test["ok"] and not test["errors"]:
            checks.append(row("compile", "ok", "单独编译", f"打包后的文件能独立编译（{test['pages'] or '?'} 页，{test['seconds']} 秒）"))
        elif test["ok"]:
            checks.append(row("compile", "warn", "单独编译",
                              f"能编出 PDF（{test['pages'] or '?'} 页），但有 {len(test['errors'])} 处 LaTeX 错误，"
                              "arXiv 可能拒收或排版出错，建议先修好（看「LaTeX 诊断」）",
                              [f"{e['file']}:{e['line'] or '?'}  {e['message']}" for e in test["errors"][:5]]))
        elif test["timedOut"]:
            checks.append(row("compile", "fail", "单独编译", "打包后的文件编译超时（超过 3 分钟）"))
        else:
            errs = [d["message"] for d in parse_log(test["log"], main)["errors"]][:5]
            checks.append(row("compile", "fail", "单独编译", "打包后的文件单独编译失败", errs))
        todos = [c for c in comments_all if c["todo"]]
        if a.keep_comments:
            state = "warn" if comments_all else "ok"
            detail = (f"保留了 {len(comments_all)} 条 % 注释，arXiv 会公开它们" + (f"，其中 {len(todos)} 条 TODO" if todos else "")) if comments_all else "没有注释"
        else:
            state = "warn" if todos else "ok"
            detail = (f"已删掉 {len(comments_all)} 条 % 注释" + (f"，其中 {len(todos)} 条是 TODO，确认都处理完了" if todos else "")) if comments_all else "没有注释"
        checks.append(row("comments", state, "注释", detail,
                          [f"{c['file']}:{c['line']}  {c['text']}" for c in (todos or comments_all)[:50]]))
        if total > ARXIV_SIZE_LIMIT:
            checks.append(row("size", "fail", "大小", f"{total / 1e6:.1f} MB，超过 arXiv 的 50 MB 上限"))
        else:
            checks.append(row("size", "ok", "大小", f"{total / 1e6:.2f} MB（上限 50 MB）"))
        if merged:
            checks.append(row("merged", "ok", "合并输入文件", f"已把 {len(merged)} 个 \\input 文件并进主文档", merged))

        blocked = any(c["state"] == "fail" for c in checks)
        zip_path = None
        if a.out and not blocked:
            zip_path = os.path.abspath(a.out if a.out.lower().endswith(".zip") else a.out + ".zip")
            if os.path.commonpath([zip_path, run_dir]) == run_dir:
                raise ToolError("压缩包不能存进运行目录里，请换个位置。")
            tmp_zip = zip_path + ".part"
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z:
                for rel in sorted(sizes):
                    z.write(os.path.join(stage, rel), rel)
            os.replace(tmp_zip, zip_path)
        return {
            "kind": "arxiv",
            "rootDocument": main,
            "sourceDir": os.path.basename(src),
            "files": [{"path": k, "bytes": v} for k, v in sorted(sizes.items())],
            "totalBytes": total,
            "checks": checks,
            "blocked": blocked,
            "zip": zip_path,
        }


# --------------------------------------------------------------------------- blind review (from Lattice submission_review.rs)

ACK_RE = re.compile(r"(?i)\\(?:(?:sub)*section|paragraph|chapter)\*?\s*(?:\[[^\]]*\])?\s*\{\s*acknowledge?ments?\b"
                    r"|\\begin\s*\{\s*(?:acknowledge?ments?|acks?)\s*\}")
ACCOUNT_RE = re.compile(r"(?i)(?:github\.com|gitlab\.com|bitbucket\.org|codeberg\.org|huggingface\.co)/"
                        r"([A-Za-z0-9][A-Za-z0-9_.-]*)[^\s{}\\%,;)]*|https?://[^\s{}\\%]*/~[A-Za-z][^\s{}\\%,;)]*")
SELF_CITE_RE = re.compile(
    r"(?i)\b(?:our|my)\s+(?:own\s+)?(?:previous|prior|earlier|recent|past|preceding)\s+"
    r"(?:work|paper|stud(?:y|ies)|publication|research|approach|method|system)s?\b"
    r"|\bwe\s+(?:have\s+)?(?:previously|earlier)\s+(?:show|shown|showed|propose|proposed|introduce|introduced|"
    r"present|presented|report|reported|demonstrate|demonstrated|describe|described)\b"
    r"|我们(?:之前|先前|此前|以前)的(?:工作|论文|研究)")
PACKAGE_RE = re.compile(r"\\(?:usepackage|documentclass)\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}")
ANON_NAMES = re.compile(r"(?i)anonymous|ambitious ai researcher|author names? withheld|paper id|\\cvprPaperID")


def detect_venue(code: str) -> Tuple[Optional[str], bool, str]:
    """(venue, double-blind review mode, why) from the preamble's packages."""
    for m in PACKAGE_RE.finditer(code):
        opts = (m.group(1) or "").lower()
        for pkg in [p.strip().lower() for p in m.group(2).split(",")]:
            if pkg.startswith("iclr") and "conference" in pkg:
                final = "\\iclrfinalcopy" in code and not re.search(r"%\s*\\iclrfinalcopy", code)
                return "ICLR", not final, "已开 \\iclrfinalcopy（正式版）" if final else "审稿版"
            if pkg.startswith("neurips"):
                final = any(o in opts for o in ("final", "preprint"))
                return "NeurIPS", not final, f"选项 [{opts}]" if final else "审稿版"
            if pkg.startswith("icml"):
                final = "accepted" in opts
                return "ICML", not final, "选项 [accepted]" if final else "审稿版"
            if pkg in ("cvpr", "iccv", "eccv", "wacv"):
                review = "review" in opts or "rebuttal" in opts
                return pkg.upper(), review, "选项 [review]" if review else "没开 [review]，按正式版处理"
            if pkg.startswith("aaai"):
                anon = "anonymous" in opts or "submission" in opts
                return "AAAI", anon, "选项 [submission]" if anon else "正式版"
            if pkg == "acmart":
                anon = "anonymous" in opts
                return "ACM", anon, "选项 [anonymous]" if anon else "没开 [anonymous]，按正式版处理"
            if pkg == "ieeetran":
                return "IEEE", False, "IEEE 会议通常单盲"
            if pkg in ("acl", "acl2023", "acl2024", "acl2025", "naacl", "emnlp"):
                review = "review" in opts
                return "ACL", review, "选项 [review]" if review else "正式版"
    return None, False, ""


def braced_arg(text: str, start: int) -> Tuple[str, int]:
    i = text.find("{", start)
    if i < 0:
        return "", start
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
    return text[i + 1:], len(text)


def pdf_font_and_meta_checks(pdf: str) -> List[Dict[str, Any]]:
    out = []
    try:
        import pymupdf as fitz
    except Exception:  # noqa: BLE001
        try:
            import fitz  # older PyMuPDF
        except Exception:  # noqa: BLE001
            return [row("fonts", "warn", "字体", "没装 PyMuPDF，跳过 PDF 字体检查")]
    try:
        doc = fitz.open(pdf)
    except Exception as e:  # noqa: BLE001
        return [row("fonts", "warn", "字体", f"打不开 PDF：{e}")]
    meta = doc.metadata or {}
    author = (meta.get("author") or "").strip()
    if author and not ANON_NAMES.search(author):
        out.append(row("pdf-meta", "fail", "PDF 元数据", f"PDF 的「作者」字段写着「{author}」，审稿人能看到"))
    else:
        out.append(row("pdf-meta", "ok", "PDF 元数据", "PDF 的作者字段里没有名字"))
    type3, unembedded = set(), set()
    for page in doc:
        for xref, ext, ftype, basefont, *_ in page.get_fonts(full=False):
            name = re.sub(r"^[A-Z]{6}\+", "", basefont or "?")
            if ftype == "Type3":
                type3.add(name)
            elif ext == "n/a":
                unembedded.add(name)
    doc.close()
    if type3 or unembedded:
        items = [f"Type 3：{n}" for n in sorted(type3)] + [f"未嵌入：{n}" for n in sorted(unembedded)]
        out.append(row("fonts", "fail" if unembedded else "warn", "字体",
                       "有会议排版检查会拒收的字体（常见来源：matplotlib 图片没设 pdf.fonttype=42）", items))
    else:
        out.append(row("fonts", "ok", "字体", "所有字体都已嵌入，没有 Type 3"))
    return out


def cmd_blind(a: argparse.Namespace) -> Dict[str, Any]:
    run_dir = os.path.abspath(a.run_dir)
    src = source_dir(run_dir)
    main = pick_main_tex(src)
    text = read_text(os.path.join(src, main))
    merged, _ = merge_inputs(text, src)
    lines = code_lines(merged)
    code = "\n".join(c for _, c in lines)
    venue, blind, why = detect_venue(code)
    checks: List[Dict[str, Any]] = []

    def hits(regex: re.Pattern) -> List[str]:
        return [f"第 {n} 行：{c.strip()[:120]}" for n, c in lines if regex.search(c)]

    if venue and blind:
        m = re.search(r"\\author\b", code)
        authors = braced_arg(code, m.end())[0] if m else ""
        plain = re.sub(r"\\[A-Za-z@]+|[{}\\]", " ", authors)
        if authors and not ANON_NAMES.search(authors) and plain.strip():
            checks.append(row("authors", "fail", "作者信息", "审稿版里 \\author 写了真实信息", [plain.strip()[:200]]))
        else:
            checks.append(row("authors", "ok", "作者信息", "作者是匿名或占位符"))
        thanks = hits(re.compile(r"\\thanks\s*\{"))
        checks.append(row("thanks", "fail" if thanks else "ok", "脚注致谢 \\thanks",
                          f"找到 {len(thanks)} 处" if thanks else "没有", thanks))
        ack = hits(ACK_RE)
        checks.append(row("ack", "fail" if ack else "ok", "致谢段落",
                          "审稿版不应有致谢（会暴露基金和单位）" if ack else "没有", ack))
        links = hits(ACCOUNT_RE)
        checks.append(row("links", "warn" if links else "ok", "个人主页 / 代码仓库链接",
                          "这些链接可能指向你的账号，审稿版请换成匿名链接（如 anonymous.4open.science）" if links else "没有",
                          links))
        selfc = hits(SELF_CITE_RE)
        checks.append(row("self-cite", "warn" if selfc else "ok", "「我们之前的工作」",
                          "用第三人称引用自己的工作" if selfc else "没有", selfc))
        pdfauthor = hits(re.compile(r"pdfauthor\s*="))
        if pdfauthor:
            checks.append(row("pdfauthor", "fail", "hyperref 的 pdfauthor", "会把名字写进 PDF 元数据", pdfauthor))
    pdf = os.path.join(src, main[:-4] + ".pdf")
    if os.path.isfile(pdf):
        font_meta = pdf_font_and_meta_checks(pdf)
        if not (venue and blind):
            font_meta = [r for r in font_meta if r["id"] != "pdf-meta"]
        checks.extend(font_meta)
    else:
        checks.append(row("fonts", "warn", "字体", "还没有编译好的 PDF，跳过字体检查"))
    summary = (f"{venue} 模板，{why}：按双盲审稿检查" if venue and blind
               else f"{venue} 模板，{why}：不需要匿名，只查字体" if venue
               else "没认出会议模板，只查字体")
    return {"kind": "blind", "rootDocument": main, "venue": venue, "doubleBlind": blind,
            "summary": summary, "checks": checks}


# --------------------------------------------------------------------------- log diagnostics (from Lattice build_log.rs)

ERROR_HINTS = [
    (r"Undefined control sequence", "用了没定义的命令：拼写错了，或者少加载了宏包"),
    (r"Missing \$ inserted", "数学符号写在了正文里：给公式加上 $...$"),
    (r"Missing \\begin\{document\}", "导言区有正文内容或乱码"),
    (r"Missing \} inserted|Extra \}|Too many \}'s", "花括号没配对"),
    (r"Environment (\S+) undefined", "这个环境没定义：少加载了宏包"),
    (r"File [`'](.+?)[`'] not found", "缺文件"),
    (r"Misplaced alignment tab character &", "& 用在了表格外：正文里写 \\&"),
    (r"Illegal unit of measure", "长度缺单位（如 0.5 应写 0.5\\linewidth 或 3pt）"),
    (r"There's no line here to end", "\\\\ 用在了段落开头或空行后"),
    (r"Float\(s\) lost", "浮动体放在了不允许的位置（如 minipage 里放 figure）"),
    (r"Unicode character (.+?) \(U\+", "有 pdfLaTeX 不支持的字符（中文、特殊符号）：换成 LaTeX 命令或改用 XeLaTeX"),
    (r"Emergency stop", "编译中断"),
    (r"Dimension too large", "尺寸太大，常见于图片缩放或 pgfplots 数据"),
]


def zh_hint(message: str) -> str:
    for pat, hint in ERROR_HINTS:
        if re.search(pat, message):
            return hint
    return ""


def unwrap_log(log: str) -> str:
    out = []
    for line in log.splitlines():
        out.append(line)
        if len(line) != 79:
            out.append("\n")
    return "".join(out)


def parse_log(log: str, main: str) -> Dict[str, Any]:
    log = unwrap_log(log)
    errors, warnings = [], []
    seen = set()
    for m in re.finditer(r"(?m)^(\.?/?[^\s:]+\.(?:tex|sty|cls|bbl)):(\d+):\s*(.+)$", log):
        msg = m.group(3).strip()
        key = (m.group(1), m.group(2), msg)
        if key in seen:
            continue
        seen.add(key)
        errors.append({"file": m.group(1).lstrip("./"), "line": int(m.group(2)), "message": msg, "hint": zh_hint(msg)})
    for m in re.finditer(r"(?m)^! (.+)$", log):
        msg = m.group(1).strip()
        if any(e["message"] == msg for e in errors):
            continue
        tail = log[m.end():m.end() + 600]
        ln = re.search(r"(?m)^l\.(\d+)", tail)
        errors.append({"file": main, "line": int(ln.group(1)) if ln else None, "message": msg, "hint": zh_hint(msg)})
    undefined_cites = sorted(set(re.findall(r"Citation [`']([^`']+)[`'] (?:on page \d+ )?undefined", log)))
    undefined_refs = sorted(set(re.findall(r"Reference [`']([^`']+)[`'] (?:on page \d+ )?undefined", log)))
    missing_files = sorted(set(re.findall(r"File [`']([^`']+\.(?:sty|cls|bst|bbx|cbx|def|cfg|fd|tex))[`'] not found", log)))
    overfull = len(re.findall(r"Overfull \\hbox \(([\d.]+)pt", log))
    big_overfull = [float(x) for x in re.findall(r"Overfull \\hbox \(([\d.]+)pt", log) if float(x) > 10]
    for m in re.finditer(r"(?m)^(?:LaTeX|Package (\S+)|Class (\S+)) Warning:\s*(.+)$", log):
        w = m.group(3).strip()
        if "Citation" in w or "Reference" in w or "Label(s) may have changed" in w:
            continue
        warnings.append(w[:200])
    return {"errors": errors, "undefinedCitations": undefined_cites, "undefinedReferences": undefined_refs,
            "missingFiles": missing_files, "overfull": overfull, "bigOverfull": len(big_overfull),
            "warnings": sorted(set(warnings))[:30]}


def find_tex_package(file_name: str) -> Optional[str]:
    if not which("tlmgr"):
        return None
    code, out, timed_out = run(["tlmgr", "search", "--global", "--file", "/" + file_name], os.getcwd(), timeout=40)
    if code != 0 or timed_out:
        return None
    for line in out.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+):\s*$", line.strip())
        if m:
            return m.group(1)
    return None


def cmd_diagnose(a: argparse.Namespace) -> Dict[str, Any]:
    run_dir = os.path.abspath(a.run_dir)
    src = source_dir(run_dir)
    main = pick_main_tex(src)
    log_path = os.path.join(src, main[:-4] + ".log")
    compiled_now = False
    if os.path.isfile(log_path):
        log = read_text(log_path)
    else:
        with tempfile.TemporaryDirectory(prefix="po-diag-") as scratch:
            copy_sources(src, scratch)
            log = latexmk_build(scratch, main, bibtex=True)["log"]
        compiled_now = True
    p = parse_log(log, main)
    checks = []
    if p["errors"]:
        checks.append(row("errors", "fail", "编译错误", f"{len(p['errors'])} 处",
                          [f"{e['file']}:{e['line'] or '?'}  {e['message']}" + (f"（{e['hint']}）" if e["hint"] else "")
                           for e in p["errors"][:40]]))
    else:
        checks.append(row("errors", "ok", "编译错误", "没有"))
    if p["missingFiles"]:
        items = []
        for f in p["missingFiles"]:
            pkg = find_tex_package(f) if a.find_packages else None
            items.append(f"{f}：" + (f"在终端运行 sudo tlmgr install {pkg}" if pkg
                                     else "可能是模板自带的文件没放进来，或要用 TeX Live 管理器安装"))
        checks.append(row("missing", "fail", "缺少的宏包 / 文件", f"{len(items)} 个", items))
    if p["undefinedCitations"]:
        checks.append(row("cites", "fail", "未定义的引用", "PDF 里会显示成 [?]，.bib 里缺这些条目", p["undefinedCitations"]))
    else:
        checks.append(row("cites", "ok", "未定义的引用", "没有"))
    if p["undefinedReferences"]:
        checks.append(row("refs", "warn", "未定义的交叉引用", "PDF 里会显示成 ??，\\ref 的标签不存在", p["undefinedReferences"]))
    else:
        checks.append(row("refs", "ok", "未定义的交叉引用", "没有"))
    if p["bigOverfull"]:
        checks.append(row("overfull", "warn", "超出版心的行", f"{p['overfull']} 处 Overfull，其中 {p['bigOverfull']} 处超出 10pt，能明显看到文字伸出页边"))
    else:
        checks.append(row("overfull", "ok", "超出版心的行", f"{p['overfull']} 处轻微 Overfull，看不出来" if p["overfull"] else "没有"))
    if p["warnings"]:
        checks.append(row("warnings", "warn", "其他警告", f"{len(p['warnings'])} 条", p["warnings"]))
    return {"kind": "diagnose", "rootDocument": main, "compiledNow": compiled_now,
            "logFile": os.path.relpath(log_path, run_dir) if not compiled_now else None, "checks": checks,
            "texAvailable": bool(which("pdflatex")), "latexdiffAvailable": bool(which("latexdiff"))}


# --------------------------------------------------------------------------- versions + latexdiff (from Lattice changes.rs)

def version_label(name: str) -> str:
    if name == FINAL_TEX:
        return "最终版"
    m = re.match(r"refined_paper_v(\d+)\.tex$", name)
    if m:
        return f"润色第 {m.group(1)} 轮"
    return {"raw_draft_paper.tex": "初稿（写作阶段）", "initial_draft.tex": "润色前的初稿",
            "template.tex": "模板"}.get(name, name)


def cmd_versions(a: argparse.Namespace) -> Dict[str, Any]:
    run_dir = os.path.abspath(a.run_dir)
    out = []
    seen_names = set()
    for sub in (WRITEUP_DIR, REFINEMENT_DIR):
        d = os.path.join(run_dir, sub)
        if not os.path.isdir(d):
            continue
        for name in tex_documents(d):
            if name in ("template.tex",) or name.startswith("format_candidate") or name in seen_names:
                continue
            seen_names.add(name)
            p = os.path.join(d, name)
            out.append({"path": p, "name": name, "folder": sub, "label": version_label(name),
                        "hasPdf": os.path.isfile(p[:-4] + ".pdf"), "mtime": os.path.getmtime(p)})
    out.sort(key=lambda v: v["mtime"])
    return {"kind": "versions", "versions": out}


def latexdiff_text(old: str, new: str, cwd: str, simplified: bool, only_changed: bool) -> Tuple[Optional[str], str]:
    cmd = ["latexdiff", "--flatten", "--no-label", "--type=UNDERLINE", "--exclude-textcmd=section,subsection",
           "--subtype=ONLYCHANGEDPAGE" if only_changed else "--subtype=SAFE",
           "--math-markup=off" if simplified else "--math-markup=whole"]
    if simplified or only_changed:
        cmd.append("--graphics-markup=none")
    if simplified:
        cmd.append("--disable-citation-markup")
    cmd += [old, new]
    try:
        p = subprocess.run(cmd, cwd=cwd, env=tex_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=180)
    except subprocess.TimeoutExpired:
        return None, "latexdiff 超时"
    if p.returncode != 0:
        err = [x for x in p.stderr.decode("utf-8", "replace").splitlines() if x.strip()]
        return None, err[-1] if err else "latexdiff 失败"
    return p.stdout.decode("utf-8", "replace"), ""


def cmd_diff(a: argparse.Namespace) -> Dict[str, Any]:
    old, new = os.path.abspath(a.old_tex), os.path.abspath(a.new_tex)
    for p in (old, new):
        if not os.path.isfile(p):
            raise ToolError(f"找不到文件：{p}")
    if not which("latexdiff"):
        raise ToolError("没找到 latexdiff。MacTeX 自带它；用 BasicTeX 的话在终端运行 sudo tlmgr install latexdiff。")
    out_pdf = os.path.abspath(a.out if a.out.lower().endswith(".pdf") else a.out + ".pdf")
    tries = []
    with tempfile.TemporaryDirectory(prefix="po-diff-") as scratch:
        work = os.path.join(scratch, "new")
        copy_sources(os.path.dirname(new), work)
        diff_name = "changes_" + os.path.basename(new)
        # The newer version may already carry LaTeX errors of its own (the
        # pipeline builds past them); marks are fine as long as they add none.
        baseline = len(latexmk_build(work, os.path.basename(new), bibtex=True, timeout=240)["errors"])
        for simplified in (False, True):
            text, err = latexdiff_text(old, new, os.path.dirname(new), simplified, a.only_changed_pages)
            if text is None:
                tries.append(err)
                continue
            with open(os.path.join(work, diff_name), "w", encoding="utf-8") as f:
                f.write(text)
            b = latexmk_build(work, diff_name, bibtex=True, timeout=240)
            if b["ok"] and (b["clean"] or simplified or len(b["errors"]) <= baseline):
                shutil.copy2(b["pdf"], out_pdf)
                return {"kind": "diff", "pdf": out_pdf, "simplified": simplified, "pages": b["pages"],
                        "old": old, "new": new}
            first = parse_log(b["log"], diff_name)["errors"][:1]
            tries.append(first[0]["message"] if first else "带修改标记的文档编译失败")
    raise ToolError("没能生成对比 PDF：" + "；".join(tries))


# --------------------------------------------------------------------------- online citation check

UA = "PaperOrchestraStudio/1.0 (citation check)"


def norm_title(t: str) -> str:
    t = ca.clean_latex_text(t or "")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def similarity(a: str, b: str) -> float:
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def first_author_surname(authors: str) -> str:
    first = re.split(r"\s+and\s+", ca.clean_latex_text(authors or ""), maxsplit=1)[0].strip()
    if "," in first:
        return first.split(",", 1)[0].strip()
    return first.split()[-1] if first.split() else ""


def http_json(url: str, params: Optional[Dict[str, str]], timeout: float) -> Optional[Dict[str, Any]]:
    import requests
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=timeout)
        except Exception:  # noqa: BLE001
            time.sleep(1 + attempt)
            continue
        if r.status_code == 404:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 + 2 * attempt)
            continue
        try:
            return r.json()
        except ValueError:
            return None
    raise ConnectionError(f"{url} 无响应")


def crossref_item(it: Dict[str, Any]) -> Dict[str, Any]:
    year = None
    for k in ("published-print", "published-online", "issued", "created"):
        parts = (it.get(k) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    authors = [" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in it.get("author", [])][:6]
    return {"source": "Crossref", "title": (it.get("title") or [""])[0], "doi": it.get("DOI"), "year": year,
            "venue": (it.get("container-title") or [""])[0], "authors": authors,
            "url": f"https://doi.org/{it['DOI']}" if it.get("DOI") else it.get("URL")}


def _surname_matches(surname: str, authors: List[str]) -> bool:
    flat = lambda x: re.sub(r"[^a-z]", "", norm_title(x))  # noqa: E731
    want = flat(surname)
    if not want or not authors:
        return True
    for a in authors[:3] + authors[-1:]:
        last = flat(a.split()[-1] if a.split() else a)
        if want in flat(a) or difflib.SequenceMatcher(None, want, last).ratio() >= 0.8:
            return True
    return False


def check_entry(key: str, fields: Dict[str, str], timeout: float) -> Dict[str, Any]:
    """Look one .bib entry up in Crossref (by DOI, then title), arXiv and Semantic Scholar.

    status: verified (title matches; `suggestions` may offer a DOI or a newer
    published version), fix (matches but the .bib disagrees on DOI or first
    author), mismatch (only a similar title), not_found, error (every source
    failed), skipped (no title and no DOI).
    """
    title = ca.clean_latex_text(fields.get("title", ""))
    doi = (fields.get("doi") or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    year = re.search(r"\d{4}", fields.get("year", "") or "")
    year = int(year.group(0)) if year else None
    surname = first_author_surname(fields.get("author", ""))
    eprint = (fields.get("eprint") or "").strip()
    if not eprint:
        m = re.search(r"arxiv[^0-9]*(\d{4}\.\d{4,5})",
                      " ".join(fields.get(k, "") for k in ("journal", "url", "note", "howpublished")), re.I)
        eprint = m.group(1) if m else ""
    res: Dict[str, Any] = {"key": key, "title": title, "year": year, "doi": doi or None, "status": "not_found",
                           "found": None, "score": 0.0, "notes": [], "suggestions": []}
    if not title and not doi:
        res["status"] = "skipped"
        res["notes"].append("条目没有标题和 DOI，没法核对")
        return res
    candidates: List[Dict[str, Any]] = []
    failures: List[str] = []
    good = lambda: title and any(similarity(c["title"], title) >= 0.9 for c in candidates)  # noqa: E731

    def attempt(name: str, fn) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failures.append(f"{name}：{e}")

    def by_doi() -> None:
        j = http_json(f"https://api.crossref.org/works/{doi}", None, timeout)
        if j and j.get("message"):
            c = crossref_item(j["message"])
            c["viaDoi"] = True
            candidates.append(c)
        else:
            res["notes"].append(f"DOI {doi} 在 Crossref 查不到")

    def by_title() -> None:
        q = {"query.bibliographic": title, "rows": "5"}
        if surname:
            q["query.author"] = surname
        j = http_json("https://api.crossref.org/works", q, timeout)
        for it in ((j or {}).get("message") or {}).get("items", []):
            candidates.append(crossref_item(it))

    def by_arxiv() -> None:
        import requests
        r = requests.get("https://export.arxiv.org/api/query", params={"id_list": eprint},
                         headers={"User-Agent": UA}, timeout=timeout)
        m = re.search(r"<entry>.*?<title>(.*?)</title>.*?<published>(\d{4})", r.text, re.S)
        if m:
            candidates.append({"source": "arXiv", "title": re.sub(r"\s+", " ", m.group(1)).strip(), "doi": None,
                               "year": int(m.group(2)), "venue": "arXiv", "authors": [],
                               "url": f"https://arxiv.org/abs/{eprint}"})

    def by_arxiv_title() -> None:
        import requests
        words = re.findall(r"[A-Za-z0-9]+", title)[:12]
        if not words:
            return
        q = " AND ".join(f"ti:{w}" for w in words if len(w) > 2) or f"ti:{words[0]}"
        r = requests.get("https://export.arxiv.org/api/query", params={"search_query": q, "max_results": "5"},
                         headers={"User-Agent": UA}, timeout=timeout)
        for m in re.finditer(r"<entry>.*?<id>http://arxiv.org/abs/([^<]+)</id>.*?<published>(\d{4}).*?<title>(.*?)</title>", r.text, re.S):
            candidates.append({"source": "arXiv", "title": re.sub(r"\s+", " ", m.group(3)).strip(), "doi": None,
                               "year": int(m.group(2)), "venue": "arXiv", "authors": [],
                               "url": f"https://arxiv.org/abs/{re.sub(r'v[0-9]+$', '', m.group(1))}"})

    def by_s2() -> None:
        j = http_json("https://api.semanticscholar.org/graph/v1/paper/search/match",
                      {"query": title, "fields": "title,year,venue,externalIds,url,authors"}, timeout)
        for it in (j or {}).get("data", [])[:1]:
            ext = it.get("externalIds") or {}
            candidates.append({"source": "Semantic Scholar", "title": it.get("title", ""), "doi": ext.get("DOI"),
                               "year": it.get("year"), "venue": it.get("venue", ""),
                               "authors": [a.get("name", "") for a in it.get("authors", [])][:6],
                               "url": it.get("url")})

    if doi:
        attempt("Crossref", by_doi)
    if title and not good():
        attempt("Crossref", by_title)
    if title and eprint and not good():
        attempt("arXiv", by_arxiv)
    if title and not good():
        attempt("Semantic Scholar", by_s2)
    if title and not good():
        attempt("arXiv", by_arxiv_title)
    if not candidates:
        if failures:
            res["status"] = "error"
            res["notes"].append("网络错误，稍后重试：" + "；".join(failures)[:200])
        else:
            res["notes"].append("Crossref、arXiv、Semantic Scholar 都没找到这篇，可能是编出来的文献，请人工核实")
        return res
    best = max(candidates, key=lambda c: (similarity(c["title"], title) if title else (1.0 if c.get("viaDoi") else 0)))
    score = similarity(best["title"], title) if title else 1.0
    res["found"] = best
    res["score"] = round(score, 3)
    if score >= 0.9:
        problems = []
        if best.get("doi") and doi and best["doi"].lower() != doi.lower() and not best.get("viaDoi"):
            problems.append(f"DOI 不一致：.bib 写 {doi}，数据库是 {best['doi']}")
        if surname and not _surname_matches(surname, best.get("authors") or []):
            problems.append(f"第一作者「{surname}」和数据库里的作者（{', '.join((best.get('authors') or [])[:3])}）对不上")
        if best.get("doi") and not doi:
            res["suggestions"].append(f"可补 DOI：{best['doi']}")
        if year and best.get("year") and int(best["year"]) != year:
            venue = best.get("venue") or ""
            if int(best["year"]) > year and venue and "arxiv" not in venue.lower():
                res["suggestions"].append(f"已正式发表：{venue}（{best['year']}），可以改引正式版")
            else:
                res["suggestions"].append(f"年份不同：.bib 写 {year}，{best['source']} 是 {best['year']}")
        res["status"] = "fix" if problems else "verified"
        res["notes"].extend(problems)
    elif score >= 0.7:
        res["status"] = "mismatch"
        res["notes"].append(f"只找到相近的标题：「{best['title'][:120]}」，请人工确认")
    elif failures:
        res["status"] = "error"
        res["notes"].append("有数据库没连上，没法下结论，稍后重试：" + "；".join(failures)[:200])
    else:
        res["notes"].append("没找到标题对得上的文献，可能是编出来的，请人工核实")
    return res


def cmd_cite_online(a: argparse.Namespace) -> Dict[str, Any]:
    run_dir = os.path.abspath(a.run_dir)
    tex_path = ca.find_final_tex(run_dir)
    if not tex_path:
        raise ToolError("这次运行还没有最终的 LaTeX 源码。")
    tex = read_text(tex_path)
    cited, _ = ca.parse_citations(tex)
    bib_files = ca.find_bib_files(run_dir, tex_path, tex)
    entries: Dict[str, Dict[str, str]] = {}
    for bf in bib_files:
        for k, v in ca.parse_bib(read_text(bf)).items():
            entries.setdefault(k.lower(), dict(v, _key=k))
    keys = [k for k in dict.fromkeys(c.lower() for c in cited) if k in entries]
    if not keys:
        raise ToolError("没找到被引用的 .bib 条目。")
    try:
        import requests  # noqa: F401
    except Exception:  # noqa: BLE001
        raise ToolError("Python 环境里没有 requests，先运行 pip install requests。")
    lock = threading.Lock()
    results: List[Dict[str, Any]] = []

    def one(k: str) -> None:
        r = check_entry(entries[k]["_key"], entries[k], a.timeout)
        with lock:
            results.append(r)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(one, keys))
    order = {"not_found": 0, "mismatch": 1, "fix": 2, "error": 3, "skipped": 4, "verified": 5}
    results.sort(key=lambda r: (order.get(r["status"], 9), r["key"].lower()))
    counts: Dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    report = {"kind": "cite-online", "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "texFile": os.path.relpath(tex_path, run_dir), "total": len(results), "counts": counts,
              "entries": results}
    tmp = os.path.join(run_dir, ONLINE_FILE + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(run_dir, ONLINE_FILE))
    return report


# --------------------------------------------------------------------------- manuscript audit (from Nature-Paper-Skills)

AUDIT_RARE_TELLS = (
    ("delve", re.compile(r"\bdelv(?:e|es|ed|ing)\b", re.I)),
    ("tapestry/realm", re.compile(r"\b(?:tapestry|tapestries|realms?)\b", re.I)),
    ("holistic", re.compile(r"\bholistic(?:ally)?\b", re.I)),
    ("in its own right", re.compile(r"\bin (?:its|their) own right\b", re.I)),
    ("not merely / more than just", re.compile(r"\b(?:not merely|more than just|not simply)\b", re.I)),
    ("underscore", re.compile(r"\bunderscor(?:e|es|ed|ing)\b", re.I)),
    ("shed light", re.compile(r"\bshed(?:s|ding)? (?:new )?light\b", re.I)),
    ("unlock", re.compile(r"\bunlock(?:s|ed|ing)?\b", re.I)),
    ("paradigm", re.compile(r"\bparadigm(?:s|atic)?\b", re.I)),
    ("seamless", re.compile(r"\bseamless(?:ly)?\b", re.I)),
    ("pivotal", re.compile(r"\bpivotal\b", re.I)),
    ("harness", re.compile(r"\bharness(?:es|ed|ing)?\b", re.I)),
    ("intricate", re.compile(r"\bintricate(?:ly)?\b", re.I)),
    ("it is worth noting", re.compile(r"\b(?:it is worth not(?:ing|e)|it should be noted|it is important to note)\b", re.I)),
    ("In this way,", re.compile(r"\bIn this way,")),
    ("crucially/critically", re.compile(r"\b(?:crucially|critically)\b", re.I)),
    ("this highlights/underscores", re.compile(r"\b(?:This|These|which) (?:further )?(?:highlight|underscore)s?\b", re.I)),
    ("paves the way / opens avenues", re.compile(r"\b(?:motivat\w+ (?:future|further)|open\w* (?:new )?avenues|future work (?:could|should|will)|paves? the way|paved the way)\b", re.I)),
)

AUDIT_DENSE_TELLS = (
    ("ultimately", re.compile(r"\bultimately\b", re.I), 0.30),
    ("fundamental(ly)", re.compile(r"\bfundamental(?:ly)?\b", re.I), 0.40),
    ("importantly", re.compile(r"\bimportantly\b", re.I), 0.79),
    ("remarkable/strikingly", re.compile(r"\b(?:remarkabl[ey]|strikingly)\b", re.I), 0.27),
    ("indeed", re.compile(r"\bindeed\b", re.I), 1.34),
    ("notably", re.compile(r"\bnotably\b", re.I), 1.77),
    ("not only ... but", re.compile(r"\bnot only\b[^.]{0,120}?\bbut\b", re.I), 0.40),
    ("Taken together/Collectively,", re.compile(r"\b(?:Together|Taken together|Collectively),"), 0.70),
    ("coined compound modifier", re.compile(r"\b[a-z]{3,}-(?:available|aware|centric|agnostic|native|grounded|ready|oriented|first)\b", re.I), 1.22),
)

_AUDIT_DRAFT_MACROS = ("TODO", "note", "directive", "superseded", "draftfig")
_AUDIT_DROP_CMD = re.compile(r"\\(?:cite\w*|ref|eqref|autoref|cref|Cref|label|url|href|includegraphics|bibliographystyle|bibliography)\*?(?:\[[^\]]*\])*\{[^}]*\}")
_AUDIT_MATH = re.compile(r"\$\$[\s\S]*?\$\$|\$[^$]*\$|\\\[[\s\S]*?\\\]")
_AUDIT_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'-]*")
_AUDIT_BOLD = re.compile(r"\\textbf\{([^{}]*)\}")
_AUDIT_FIG_REF = re.compile(
    r"(?<!\w)(?P<kind>Extended\s+Data\s+(?:Figs?\.|Figures?)|"
    r"Supplementary\s+(?:Figs?\.|Figures?)|(?:Figs?\.|Figures?))\s*"
    r"(?P<num>\d+)(?P<panels>[a-z](?:\s*[-\u2013]\s*[a-z])?)?(?:\s*[-\u2013]\s*(?P<end>\d+))?",
    re.I,
)
_AUDIT_STUB_PHRASES = (
    "Abstract here.",
    "Introduction here.",
    "Results here.",
    "Discussion here.",
    "Methods here.",
    "Data availability here.",
    "Code availability here.",
    "Acknowledgements here.",
    "Author contributions here.",
    "Competing interests here.",
    "Supplementary information here.",
)
_AUDIT_MARKER_PATTERNS = (
    ("TODO/FIXME 标记", re.compile(r"\\(?:TODO|note|directive|superseded|draftfig)\s*\{|\b(?:TODO|FIXME|TBD|XXX)\b")),
    ("待补充占位符", re.compile(r"\[citation needed\]|\?\?\?|To be completed by the authors", re.I)),
)
_AUDIT_STATS_CLAIM = re.compile(
    r"(?i)\bp\s*[<>=]\s*0?\.\d+|\bp[- ]values?\b|\bstatistically significant\b|"
    r"\bANOVA\b|\bt[- ]tests?\b|\bMann[- ]Whitney\b|\bWilcoxon\b|\bchi[- ]square\b"
)
_AUDIT_SAMPLE_SIZE = re.compile(
    r"(?i)\bn\s*=\s*\d+|\bsample sizes?\b|\breplicates?\b|\btrials?\b|\bsamples?\b|"
    r"\bfolds?\b|\bseeds?\b|\bsubjects?\b|\bpatients?\b|\bcells?\b|\bruns?\b"
)
_AUDIT_UNCERTAINTY = re.compile(
    r"(?i)\bCI\b|confidence interval|\bs\.d\.|\bs\.e\.m\.|\bSD\b|\bSEM\b|±|\\pm\b|"
    r"\bIQR\b|\bRMSE\b|\bMAE\b|\bAUC\b|\bCohen|effect size|\bR\^2\b|\br\s*="
)
_AUDIT_P_BARE = re.compile(r"(?i)\bp\s*<\s*0?\.(?:05|01|001)\b")
_AUDIT_P_EXACT = re.compile(r"(?i)\bp\s*=\s*0?\.\d+")
_AUDIT_AI_FIG = re.compile(r"(?i)\b(?:Midjourney|DALL[\s-]?E|Stable Diffusion|generated (?:by|with|using) (?:generative )?AI|AI-generated (?:figure|image|illustration))\b")


def _strip_tex_macro(text: str, name: str) -> str:
    """Remove every \\name{...} with brace counting (never raises on malformed braces)."""
    out: List[str] = []
    i = 0
    tag = "\\" + name + "{"
    while True:
        j = text.find(tag, i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:j])
        k = j + len(tag)
        depth = 1
        while k < len(text) and depth > 0:
            if text[k] == "\\" and k + 1 < len(text):
                k += 2
                continue
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
            k += 1
        if depth > 0:
            out.append(text[j:])
            return "".join(out)
        i = k


def _strip_float_envs(text: str) -> str:
    for env in ("figure", "table"):
        text = re.sub(rf"\\begin\{{{env}\*?}}[\s\S]*?\\end\{{{env}\*?}}", " ", text)
    return text


def _tex_prose_words(text: str) -> List[str]:
    clean = _AUDIT_DROP_CMD.sub(" ", text)
    clean = _AUDIT_MATH.sub(" ", clean)
    clean = re.sub(r"\\(?:begin|end)\{[^}]*\}(?:\[[^\]]*\])?", " ", clean)
    clean = re.sub(r"\\[A-Za-z@]+\*?", " ", clean)
    clean = clean.replace("{", " ").replace("}", " ").replace("~", " ")
    return _AUDIT_WORD.findall(clean)


def _body_lines(lines: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    started = False
    for num, code in lines:
        if not started:
            m = re.search(r"\\begin\{document\}", code)
            if not m:
                continue
            started = True
            tail = code[m.end():]
            if tail.strip():
                out.append((num, tail))
            continue
        if re.search(r"\\end\{document\}", code):
            head = code[:code.find("\\end{document}")]
            if head.strip():
                out.append((num, head))
            break
        out.append((num, code))
    return out if started else lines


def _extract_abstract_block(body: str) -> str:
    m = re.search(r"\\begin\{abstract\}([\s\S]*?)\\end\{abstract\}", body)
    if m:
        return m.group(1)
    m = re.search(r"\\abstract\b", body)
    return braced_arg(body, m.end())[0] if m else ""


def _load_src_venue(src: str) -> Optional[Dict[str, Any]]:
    vp = os.path.join(src, "venue.json")
    if not os.path.isfile(vp):
        return None
    try:
        with open(vp, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _audit_wordcount(body: str, abstract_raw: str, venue: Optional[Dict[str, Any]], is_journal: bool) -> Dict[str, Any]:
    stripped = body
    for macro in _AUDIT_DRAFT_MACROS:
        stripped = _strip_tex_macro(stripped, macro)
    stripped = _strip_float_envs(stripped)
    stripped = re.sub(r"\\begin\{abstract\}[\s\S]*?\\end\{abstract\}", " ", stripped)
    body_words = len(_tex_prose_words(stripped))
    abs_words = len(_tex_prose_words(abstract_raw))
    abs_limit = 200
    if venue:
        m = re.search(r"(\d{2,3})\s*words", str(venue.get("abstract_limit", "")), re.I)
        if m:
            abs_limit = int(m.group(1))
    elif not is_journal:
        abs_limit = 250
    items: List[str] = []
    state = "ok"
    if abstract_raw and abs_words > abs_limit:
        state = "warn"
        items.append(f"摘要共 {abs_words} 词，超过建议上限 {abs_limit} 词")
    elif not abstract_raw:
        state = "warn"
        items.append("未检测到 abstract 环境或 \\abstract")
    detail = f"正文约 {body_words} 词，摘要 {abs_words} 词"
    if is_journal or venue:
        detail += f"（摘要上限 {abs_limit} 词）"
    return row("prose_wordcount", state, "正文与摘要字数", detail, items)


def _audit_ai_tells(body_lines_list: List[Tuple[int, str]], abstract_raw: str) -> Dict[str, Any]:
    cleaned_lines: List[Tuple[int, str]] = []
    for num, code in body_lines_list:
        line = _AUDIT_DROP_CMD.sub(" ", code)
        line = _AUDIT_MATH.sub(" ", line)
        line = line.replace("---", "\u2014")
        cleaned_lines.append((num, line))

    words = sum(len(_AUDIT_WORD.findall(t)) for _, t in cleaned_lines)
    findings: List[str] = []

    for key, rx in AUDIT_RARE_TELLS:
        for num, line in cleaned_lines:
            for m in rx.finditer(line):
                ctx = line[max(0, m.start() - 30):min(len(line), m.end() + 30)].strip()
                findings.append(f"第 {num} 行 [{key}]：{ctx}")

    if words >= 1000:
        for key, rx, base in AUDIT_DENSE_TELLS:
            hits = [(num, m, line) for num, line in cleaned_lines for m in rx.finditer(line)]
            rate = 1e4 * len(hits) / words if words else 0.0
            if len(hits) >= 2 and rate >= 3.0 * base:
                for num, m, line in hits[:3]:
                    ctx = line[max(0, m.start() - 25):min(len(line), m.end() + 25)].strip()
                    findings.append(f"第 {num} 行 [{key} 偏密 {rate / base:.1f}x]：{ctx}")

    if abstract_raw:
        abs_norm = _AUDIT_MATH.sub(" ", _AUDIT_DROP_CMD.sub(" ", abstract_raw)).replace("---", "\u2014")
        abs_dashes = abs_norm.count("\u2014")
        abs_semis = abs_norm.count(";")
        if abs_dashes:
            findings.append(f"摘要含 {abs_dashes} 个长破折号（Nature 风格建议摘要 0 个）")
        if abs_semis:
            findings.append(f"摘要含 {abs_semis} 个分号（Nature 风格建议摘要 0 个）")

    # Paragraph-level em-dash and semicolon budget + bold body sentences
    para_start, para_buf = None, []
    paras: List[Tuple[int, str]] = []
    for num, line in cleaned_lines + [(0, "")]:
        s = line.strip()
        if s and not s.startswith("\\section") and not s.startswith("\\subsection"):
            para_start = para_start or num
            para_buf.append(s)
        elif para_buf:
            paras.append((para_start or num, " ".join(para_buf)))
            para_start, para_buf = None, []
    for pnum, para in paras:
        dashes = para.count("\u2014")
        if dashes > 1:
            findings.append(f"第 {pnum} 行起段落含 {dashes} 个长破折号（建议每段最多 1 个，避免成对破折号）")
        semis = para.count(";")
        if semis > 1:
            findings.append(f"第 {pnum} 行起段落含 {semis} 个分号（建议每段最多 1 个）")

    for num, code in body_lines_list:
        if re.search(r"\\(?:caption|section|subsection|subsubsection|paragraph|title)\b", code):
            continue
        for m in _AUDIT_BOLD.finditer(code):
            if len(_AUDIT_WORD.findall(m.group(1))) >= 6:
                findings.append(f"第 {num} 行 [正文长粗体句]：{m.group(1).strip()[:80]}")

    state = "warn" if findings else "ok"
    detail = f"发现 {len(findings)} 处可优化表述/标点节奏" if findings else f"已检查约 {words} 词，未检出明显 AI 腔与标点滥用"
    return row("ai_tells", state, "AI 写作痕迹与标点节奏", detail, findings[:20])


def _audit_figure_refs(body: str, is_journal: bool) -> Dict[str, Any]:
    all_labels = set(re.findall(r"\\label\{([^}]+)\}", body))
    all_refs = set(
        target.strip()
        for grp in re.findall(r"\\(?:autoref|cref|Cref|ref)\*?\{([^}]+)\}", body)
        for target in grp.split(",")
    )
    fig_blocks = re.findall(r"\\begin\{figure\*?}([\s\S]*?)\\end\{figure\*?}", body)
    defined_fig_labels: List[Tuple[int, str]] = []
    caption_issues: List[str] = []

    for idx, block in enumerate(fig_blocks, 1):
        lbls = re.findall(r"\\label\{([^}]+)\}", block)
        for lbl in lbls:
            defined_fig_labels.append((idx, lbl))
        cm = re.search(r"\\caption\b", block)
        if cm:
            cap_text, _ = braced_arg(block, cm.end())
            cap_words = len(_tex_prose_words(cap_text))
            if cap_words > 350:
                caption_issues.append(f"Fig. {idx} 图注约 {cap_words} 词（Nature 风格建议 ≤ 350 词）")
            if is_journal and cap_text.strip() and not re.match(r"^\s*\\textbf\{", cap_text):
                caption_issues.append(f"Fig. {idx} 图注未以加粗一句话总标题（\\textbf{{...}}）开头")

    plain_body = _strip_float_envs(body)
    cited_main_nums: Set[int] = set()
    cited_panels: Dict[int, Set[str]] = {}
    for m in _AUDIT_FIG_REF.finditer(plain_body):
        kind_raw = " ".join(m.group("kind").lower().split())
        if kind_raw.startswith(("extended data", "supplementary")):
            continue
        start_n = int(m.group("num"))
        end_n = int(m.group("end") or start_n)
        for n in range(start_n, min(end_n, start_n + 50) + 1):
            cited_main_nums.add(n)
            if m.group("panels"):
                cited_panels.setdefault(n, set()).add(m.group("panels").lower().replace(" ", ""))

    issues: List[str] = []
    for idx, lbl in defined_fig_labels:
        if lbl not in all_refs and idx not in cited_main_nums:
            issues.append(f"已定义图标签 \\label{{{lbl}}}（Fig. {idx}）在正文中未被引用")
    for ref_lbl in sorted(all_refs):
        if ref_lbl.lower().startswith("fig:") and ref_lbl not in all_labels:
            issues.append(f"正文引用 \\ref{{{ref_lbl}}} 找不到对应的 \\label")
    if fig_blocks:
        for n in sorted(cited_main_nums):
            if n > len(fig_blocks):
                issues.append(f"正文提到 Fig. {n}，但文档只定义了 {len(fig_blocks)} 个 figure 环境")
    issues.extend(caption_issues)

    state = "warn" if issues else "ok"
    detail = f"共 {len(fig_blocks)} 个图环境，正文引用编号 {sorted(cited_main_nums) or '无'}"
    if issues:
        detail += f"；发现 {len(issues)} 项需核对"
    return row("figure_refs", state, "图表与图注一致性", detail, issues[:20])


def _audit_draft_markers(body_lines_list: List[Tuple[int, str]]) -> Dict[str, Any]:
    stubs: List[str] = []
    markers: List[str] = []
    for num, code in body_lines_list:
        for stub in _AUDIT_STUB_PHRASES:
            if stub in code:
                stubs.append(f"第 {num} 行残留模板占位句：{stub}")
        for label, rx in _AUDIT_MARKER_PATTERNS:
            if rx.search(code):
                markers.append(f"第 {num} 行 [{label}]：{code.strip()[:100]}")
    if stubs:
        return row("draft_markers", "fail", "草稿标记与占位符", f"发现 {len(stubs)} 处未替换的模板占位文本", (stubs + markers)[:20])
    if markers:
        return row("draft_markers", "warn", "草稿标记与占位符", f"发现 {len(markers)} 处待补全或草稿标记", markers[:20])
    return row("draft_markers", "ok", "草稿标记与占位符", "没有残留模板占位符或 TODO 标记")


def _audit_stats_reporting(body: str) -> Dict[str, Any]:
    plain = _strip_float_envs(body)
    stat_hits = _AUDIT_STATS_CLAIM.findall(plain)
    if not stat_hits:
        return row("stats_reporting", "ok", "统计报告规范", "正文未出现显著性检验（p 值 / ANOVA / t-test 等）表述")
    items: List[str] = []
    has_n = bool(_AUDIT_SAMPLE_SIZE.search(body))
    has_uncertainty = bool(_AUDIT_UNCERTAINTY.search(body))
    has_bare_p = bool(_AUDIT_P_BARE.search(plain))
    has_exact_p = bool(_AUDIT_P_EXACT.search(plain))
    if not has_n:
        items.append("正文与 Methods 未检测到样本量（n = ... / replicates / samples）说明")
    if not has_uncertainty:
        items.append("未检测到误差棒或效应量/置信区间指标（SD / SEM / CI / ± / RMSE / MAE 等）")
    if has_bare_p and not has_exact_p:
        items.append("存在阈值型 p < 0.05/0.01 表述，建议尽量同时给出精确 p 值与效应量")
    state = "warn" if items else "ok"
    detail = f"检测到 {len(stat_hits)} 处统计检验表述" + (f"，有 {len(items)} 项建议补充" if items else "，样本量与不确定度要素齐全")
    return row("stats_reporting", state, "统计报告规范", detail, items)


def _audit_back_matter(body: str, code: str, is_journal: bool, venue: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    items: List[str] = []
    if _AUDIT_AI_FIG.search(code):
        items.append("检测到疑似声明图件由生成式 AI 直接生成的文字（Nature 系列要求最终数据图/示意图须由代码或人工绘制）")
    if not is_journal:
        state = "warn" if items else "ok"
        detail = "当前为会议模板，不强制期刊后置声明" if not items else "检测到潜在 AI 生图合规提示"
        return row("back_matter", state, "期刊必备声明与合规", detail, items)

    required = ("Methods", "Data availability", "Code availability", "Author contributions", "Competing interests")
    missing = [s for s in required if not re.search(rf"\\section\*?\s*\{{\s*{re.escape(s)}\s*\}}", body, re.I)]
    for s in missing:
        items.append(f"缺少期刊章节 \\section*{{{s}}}")
    if "To be completed by the authors" in body:
        items.append("部分声明写为 \"To be completed by the authors.\"（投稿前请由作者补全）")
    label = (venue.get("short_name") or venue.get("venue_name")) if venue else "Nature 风格期刊"
    state = "warn" if items else "ok"
    detail = f"按 {label} 规范核对：{len(required) - len(missing)}/{len(required)} 个核心板块完整"
    return row("back_matter", state, "期刊必备声明与合规", detail, items)


def cmd_manuscript_audit(a: argparse.Namespace) -> Dict[str, Any]:
    run_dir = os.path.abspath(a.run_dir)
    src = source_dir(run_dir)
    main = pick_main_tex(src)
    text = read_text(os.path.join(src, main))
    merged, _ = merge_inputs(text, src)
    lines = code_lines(merged)
    code = "\n".join(c for _, c in lines)
    b_lines = _body_lines(lines)
    body = "\n".join(c for _, c in b_lines)
    abstract_raw = _extract_abstract_block(body)
    venue = _load_src_venue(src)
    is_journal = bool(
        venue
        or re.search(r"\\bibliographystyle\s*\{\s*naturemag\s*\}", code)
        or (
            re.search(r"\\section\*\s*\{\s*Methods\s*\}", body, re.I)
            and re.search(r"\\section\*\s*\{\s*Data availability\s*\}", body, re.I)
        )
    )
    checks = [
        _audit_wordcount(body, abstract_raw, venue, is_journal),
        _audit_ai_tells(b_lines, abstract_raw),
        _audit_figure_refs(body, is_journal),
        _audit_draft_markers(b_lines),
        _audit_stats_reporting(body),
        _audit_back_matter(body, code, is_journal, venue),
    ]
    warns = sum(1 for c in checks if c["state"] == "warn")
    fails = sum(1 for c in checks if c["state"] == "fail")
    venue_label = (venue.get("short_name") or venue.get("venue_name")) if venue else ("期刊模式" if is_journal else "会议模式")
    summary = (
        f"{venue_label} · 6 项体检全部通过"
        if fails == 0 and warns == 0
        else f"{venue_label} · {fails} 项阻塞 / {warns} 项待优化"
    )
    return {
        "kind": "manuscript-audit",
        "rootDocument": main,
        "summary": summary,
        "checks": checks,
    }


# --------------------------------------------------------------------------- main

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("arxiv")
    p.add_argument("run_dir")
    p.add_argument("--out")
    p.add_argument("--keep-comments", action="store_true")
    p.add_argument("--merge-inputs", action="store_true")
    p.set_defaults(fn=cmd_arxiv)
    p = sub.add_parser("blind")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_blind)
    p = sub.add_parser("diagnose")
    p.add_argument("run_dir")
    p.add_argument("--find-packages", action="store_true")
    p.set_defaults(fn=cmd_diagnose)
    p = sub.add_parser("versions")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_versions)
    p = sub.add_parser("diff")
    p.add_argument("old_tex")
    p.add_argument("new_tex")
    p.add_argument("--out", required=True)
    p.add_argument("--only-changed-pages", action="store_true")
    p.set_defaults(fn=cmd_diff)
    p = sub.add_parser("cite-online")
    p.add_argument("run_dir")
    p.add_argument("--timeout", type=float, default=20.0)
    p.set_defaults(fn=cmd_cite_online)
    p = sub.add_parser("manuscript-audit")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_manuscript_audit)
    a = ap.parse_args(argv)
    try:
        os.makedirs(SCRATCH_ROOT, exist_ok=True)
        tempfile.tempdir = SCRATCH_ROOT
    except OSError:
        pass
    try:
        emit(a.fn(a))
    except ToolError as e:
        emit({"error": str(e)})
    except Exception as e:  # noqa: BLE001
        emit({"error": f"{type(e).__name__}: {e}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
