#!/usr/bin/env python
"""End-to-end regression harness for the PaperOrchestra pipeline (offline, fakes only).

One command, run from the paper-orchestra directory:

    /home/claude/venv/bin/python tests/e2e/run_e2e.py            # HEAD, all scenarios below
    /home/claude/venv/bin/python tests/e2e/run_e2e.py --baseline # + the original code (c68cd16) and a diff

What a run does
  * builds a run folder exactly like the desktop app (src-tauri/src/runner.rs):
    <output_root>/<slug>_<stamp>_<id8>/raw_materials/{idea_sparse.md, experimental_log.md, figures/}
  * starts paper_writing_cli.py as a SUBPROCESS with runner.rs's flags
    (-u, --raw_materials_dir, --output_dir, --latex_template_dir templates/cvpr2025,
    --idea_filename, --experimental_log_filename, --writer/--reflection_model_name,
    --use_plotting true|false and, with plotting, --plotting_model_name, --image_model_name,
    --plotting_max_critic_rounds 3; cwd = repo; PYTHONUTF8=1, PYTHONIOENCODING=utf-8)
    in a clean environment (no real keys, no proxies)
  * LLM: tests/e2e/fake_llm_server.py, a stage-aware OpenAI-compatible server
  * scholarly APIs: tests/e2e/hooks/po_e2e_fake_scholar.py, loaded into the
    subprocess by tests/e2e/hooks/sitecustomize.py (PYTHONPATH); fake Semantic
    Scholar + arXiv answers, every other host blocked
  * LaTeX: the real pdflatex/bibtex when installed, otherwise a clearly
    labelled stub (see --latex)
  * then checks every original output of the 5-agent pipeline (outline,
    literature, plotting, section writing, content refinement, final PDF), the
    exact LLM call sequence per stage and the model routing per role, the stage
    progression the desktop app derives from stdout (detect_stage() parsed out of
    runner.rs), plus the additive reports of the new code (usage_report.json, references_meta.json,
    citation_audit.json/.md) when the code under test has them: exact contract keys and types,
    numbers that agree with what the fake server served, and a round trip through the desktop's
    own readers (paper-orchestra-desktop/src/runReports.ts, bundled with esbuild, run with node;
    an advisory warning instead when node / node_modules are missing)
  * plus one "check_providers" step (code under test with tools/check_providers.py only):
    tools/check_providers.py --json, then --json --ping --scholar, against the fake server
    and the fake scholarly APIs (shared contract 4, also fed through runReports.ts)

Scenarios (--scenarios, default: all)
  legacy  OPENAI_API_KEY + OPENAI_BASE_URL -> fake server, models gpt-4.1 / gpt-4o / gpt-4.1-mini
          (the only setup the original code understands; also used for the baseline)
  noplot  like legacy but --use_plotting false (methods/paper_writer.py) with one user-provided
          figure in raw_materials/figures + figures/info.json, as runner.rs writes for attachments
  faults  like legacy, but every additive report of the new code is broken on purpose: token
          extraction and the citation audit raise (hooks/po_e2e_faults.py) and the report paths
          (usage_report.json, citation_audit.*, references_meta.json) are directories, so every
          write fails; all original outputs must still be produced
  custom  providers.json (PO_PROVIDERS_FILE) declaring provider "fake",
          models fake:fake-writer / fake:fake-reflect / fake:fake-plot
  auto    same providers.json, every model "auto", PO_AUTO_MODEL=fake:fake-auto

Flags
  --repo PATH            paper-orchestra checkout to test (default: the one containing this file)
  --scenarios LIST       comma list of legacy,noplot,faults,custom,auto (default: all)
  --baseline             also run the scenarios the original code understands (legacy, noplot)
                         against it in a temporary git worktree of --baseline-ref and compare
                         every output with HEAD's run of the same scenario
  --baseline-ref REF     commit for --baseline (default: c68cd16)
  --baseline-worktree P  where to create that worktree (default: <work-dir>/orig); removed afterwards
  --baseline-repo PATH   use an existing checkout as the baseline instead of creating a worktree
  --work-dir PATH        where run folders and logs go (default: a new temp dir, kept afterwards)
  --clean                remove an auto-created work dir after a fully passing run
  --latex auto|real|stub auto (default) = real pdflatex+bibtex if on PATH, else the stub
  --jobs N               runs in parallel (default 4; each run has its own TMPDIR, so the
                         cross-process scholar rate limiters do not couple runs)
  --timeout SEC          per-run timeout (default 900)
  --python PATH          interpreter for the pipeline (default: this interpreter)
  --skip-check-providers do not run the tools/check_providers.py step
  --json PATH            also write the machine-readable results there
                         (default: <work-dir>/e2e_results.json)
  -v / --verbose         stream the pipeline output (prefixed with the run label)

Exit code: 0 = every HEAD check passed and no baseline comparison found a regression,
1 = otherwise, 2 = the harness itself could not run. (A check that also fails on the
original code is reported but only counts if HEAD fails it too.)
"""

import argparse
import collections
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = HERE.parent.parent
HOOKS = HERE / "hooks"
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HOOKS))

import fake_llm_server as fls  # noqa: E402
import po_e2e_fake_scholar as fake_scholar  # noqa: E402

# A dummy credential for the fake endpoint. Not a secret; the pipeline must
# never echo it (checked on every run's output).
DUMMY_KEY = "sk-po-e2e-dummy-not-a-secret-7f3a9c"
TEMPLATE = "cvpr2025"
IMAGE_MODEL = "gemini-3-pro-image-preview"  # the desktop default; no Gemini key -> matplotlib fallback

STAGE_ROLE = {
    "outline": "writer", "literature_query": "writer", "literature_screen": "writer", "literature_write": "writer",
    "section_writing": "writer",
    "plot_planner": "plotting", "diagram_planner": "plotting", "plot_stylist": "plotting",
    "diagram_stylist": "plotting", "plot_code": "plotting", "diagram_code": "plotting",
    "plot_critic": "plotting", "diagram_critic": "plotting", "figure_caption": "plotting",
    "peer_review": "reflection", "meta_review": "reflection", "content_refinement": "reflection",
    "format_review": "reflection", "format_fix": "reflection",
}

# LLM calls per stage implied by the fixtures and the agents' control flow:
# 7 literature search tasks; 2 figures x (1 critic round with changes + 1 without);
# reviews of v0, v1, v2 (3 ensemble + 1 meta each); refinement v1 accepted, v2 reverted;
# one formatting round.
EXPECTED_STAGE_COUNTS = {
    "outline": 1, "literature_query": 7, "literature_screen": 1, "literature_write": 1,
    "plot_planner": 1, "diagram_planner": 1, "plot_stylist": 1, "diagram_stylist": 1,
    "plot_code": 2, "diagram_code": 2, "plot_critic": 2, "diagram_critic": 2, "figure_caption": 2,
    "section_writing": 1, "peer_review": 9, "meta_review": 3, "content_refinement": 2,
    "format_review": 1, "format_fix": 1,
}

PLOTTING_STAGES = {s for s, role in STAGE_ROLE.items() if role == "plotting"}


# Calls HEAD adds on purpose (the original code never makes them): the relevance
# screen of the non-Gemini literature path (8 fixture papers -> one batch).
HEAD_ONLY_STAGES = {"literature_screen"}


def expected_stage_counts(use_plotting: bool, head: bool = True) -> Dict[str, int]:
    return {k: v for k, v in EXPECTED_STAGE_COUNTS.items()
            if (use_plotting or k not in PLOTTING_STAGES) and (head or k not in HEAD_ONLY_STAGES)}


# Report paths the "faults" scenario blocks with directories.
FAULT_BLOCKED_PATHS = ["usage_report.json", "citation_audit.json", "citation_audit.md",
                       "literature_agent_output/references_meta.json"]

PROVIDED_FIGURE = ("teaser_results.png", "Top-1 accuracy of the compared methods on the benchmark.")

FIXTURE_TITLES_USED = [p["title"] for p in fake_scholar.FIXTURE_PAPERS]  # the outline's searches reach all 8
FIXTURE_DOIS = {p["doi"] for p in fake_scholar.FIXTURE_PAPERS}

# What PLOT_CODE / DIAGRAM_CODE produce through execute_plot_code_worker when run alone.
EXPECTED_IMAGE_SIZES = {fls.FIG_PLOT_ID: (1169, 869), fls.FIG_DIAGRAM_ID: (1890, 690)}

TRACEBACK_MARKERS = ("Traceback (most recent call last)", "EXCEPTION in perform_writeup", "Writeup failed after")


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------


@dataclass
class Scenario:
    name: str
    description: str
    models: Dict[str, str]          # writer / reflection / plotting, as passed on the CLI
    wire_models: Dict[str, str]     # model name the fake server must see per role
    provider: str                   # provider name expected in usage_report.json
    env: Callable[[str, Path], Dict[str, str]]
    baseline_ok: bool = False       # can the original code run it?
    use_plotting: bool = True       # False: paper_writer.py with a user-provided figure
    inject_faults: bool = False     # break the additive reports on purpose (hooks/po_e2e_faults.py)


def _providers_file(url: str, run_root: Path) -> Path:
    path = run_root / "providers.json"
    path.write_text(json.dumps({"providers": [{
        "name": "fake", "type": "openai", "base_url": url, "api_key_env": "PO_E2E_FAKE_KEY",
        "vision": True, "models": ["fake-writer", "fake-reflect", "fake-plot", "fake-auto"],
        "default_model": "fake-auto",
    }]}, indent=2), encoding="utf-8")
    return path


SCENARIOS = {
    "legacy": Scenario(
        "legacy", "OPENAI_API_KEY + OPENAI_BASE_URL -> fake server, explicit gpt-* models",
        {"writer": "gpt-4.1", "reflection": "gpt-4o", "plotting": "gpt-4.1-mini"},
        {"writer": "gpt-4.1", "reflection": "gpt-4o", "plotting": "gpt-4.1-mini"},
        "openai",
        lambda url, root: {"OPENAI_API_KEY": DUMMY_KEY, "OPENAI_BASE_URL": url},
        baseline_ok=True,
    ),
    "noplot": Scenario(
        "noplot", "legacy keys, --use_plotting false with a provided figure + figures/info.json",
        {"writer": "gpt-4.1", "reflection": "gpt-4o", "plotting": "gpt-4.1-mini"},
        {"writer": "gpt-4.1", "reflection": "gpt-4o", "plotting": "gpt-4.1-mini"},
        "openai",
        lambda url, root: {"OPENAI_API_KEY": DUMMY_KEY, "OPENAI_BASE_URL": url},
        baseline_ok=True, use_plotting=False,
    ),
    "faults": Scenario(
        "faults", "legacy keys + every additive report broken on purpose (raising hooks, unwritable paths)",
        {"writer": "gpt-4.1", "reflection": "gpt-4o", "plotting": "gpt-4.1-mini"},
        {"writer": "gpt-4.1", "reflection": "gpt-4o", "plotting": "gpt-4.1-mini"},
        "openai",
        lambda url, root: {"OPENAI_API_KEY": DUMMY_KEY, "OPENAI_BASE_URL": url, "PO_E2E_INJECT_FAULTS": "1",
                           "PO_E2E_FAULT_LOG": str(root / "injected_faults.jsonl")},
        inject_faults=True,
    ),
    "custom": Scenario(
        "custom", "providers.json provider 'fake', explicit fake:<model> refs",
        {"writer": "fake:fake-writer", "reflection": "fake:fake-reflect", "plotting": "fake:fake-plot"},
        {"writer": "fake-writer", "reflection": "fake-reflect", "plotting": "fake-plot"},
        "fake",
        lambda url, root: {"PO_PROVIDERS_FILE": str(_providers_file(url, root)), "PO_E2E_FAKE_KEY": DUMMY_KEY},
    ),
    "auto": Scenario(
        "auto", "only provider 'fake' configured, all models 'auto', PO_AUTO_MODEL=fake:fake-auto",
        {"writer": "auto", "reflection": "auto", "plotting": "auto"},
        {"writer": "fake-auto", "reflection": "fake-auto", "plotting": "fake-auto"},
        "fake",
        lambda url, root: {"PO_PROVIDERS_FILE": str(_providers_file(url, root)), "PO_E2E_FAKE_KEY": DUMMY_KEY,
                           "PO_AUTO_MODEL": "fake:fake-auto"},
    ),
}


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class RunResult:
    label: str
    scenario: str
    code_under_test: str
    repo: str
    run_dir: str = ""
    exit_code: Optional[int] = None
    seconds: float = 0.0
    log_path: str = ""
    checks: List[Check] = field(default_factory=list)
    warnings: List[Check] = field(default_factory=list)   # recorded, never fail the run
    stage_counts: Dict[str, int] = field(default_factory=dict)
    desktop_stages: List[str] = field(default_factory=list)
    manifest: List[str] = field(default_factory=list)
    head_extras: Optional[bool] = None
    error: str = ""

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, bool(ok), detail))
        return bool(ok)

    def warn(self, name: str, ok: bool, detail: str = "") -> bool:
        self.warnings.append(Check(name, bool(ok), detail))
        return bool(ok)

    @property
    def passed(self) -> bool:
        return not self.error and all(c.ok for c in self.checks)


# --------------------------------------------------------------------------
# LaTeX
# --------------------------------------------------------------------------

STUB_PDFLATEX = r'''#!{python}
# STUB pdflatex written by tests/e2e/run_e2e.py --latex stub. NOT a TeX engine:
# it renders the document body as plain text into <name>.pdf so the pipeline
# can continue when TeX Live is not installed.
import re, sys, textwrap
import pymupdf
args = [a for a in sys.argv[1:] if not a.startswith("-")]
base = args[-1][:-4] if args and args[-1].endswith(".tex") else (args[-1] if args else "")
try:
    src = open(base + ".tex", encoding="utf-8", errors="replace").read()
except OSError as e:
    print(f"! stub pdflatex: cannot read {{base}}.tex: {{e}}")
    sys.exit(1)
if "\\begin{{document}}" not in src or "\\end{{document}}" not in src:
    print("! stub pdflatex: no document environment")
    sys.exit(1)
body = src.split("\\begin{{document}}", 1)[1].split("\\end{{document}}", 1)[0]
body = re.sub(r"(?<!\\)%.*", "", body)
title = re.search(r"\\title\{{([^}}]*)\}}", src)
text = ((title.group(1) + "\n\n") if title else "") + "[STUB PDF - not produced by TeX]\n\n" + body
lines = []
for para in text.splitlines():
    lines.extend(textwrap.wrap(para, 110) or [""])
doc = pymupdf.open()
for i in range(0, max(1, len(lines)), 70):
    page = doc.new_page()
    page.insert_text((40, 50), "\n".join(lines[i:i + 70]), fontsize=7)
doc.save(base + ".pdf")
print("Output written on " + base + ".pdf (STUB)")
'''

STUB_BIBTEX = r'''#!{python}
# STUB bibtex written by tests/e2e/run_e2e.py --latex stub (does nothing).
print("This is the e2e STUB bibtex: no bibliography processing.")
'''


def prepare_latex(mode: str, work_dir: Path, python: str):
    """Returns (mode actually used, extra PATH dir or None)."""
    have_real = bool(shutil.which("pdflatex") and shutil.which("bibtex"))
    if mode == "real" and not have_real:
        raise SystemExit("--latex real: pdflatex/bibtex not found on PATH")
    if mode == "real" or (mode == "auto" and have_real):
        return "real", None
    stub_dir = work_dir / "latex_stub_bin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    for name, body in (("pdflatex", STUB_PDFLATEX), ("bibtex", STUB_BIBTEX)):
        path = stub_dir / name
        path.write_text(body.format(python=python), encoding="utf-8")
        path.chmod(0o755)
    banner = "!" * 78
    print(f"\n{banner}\n!! LaTeX STUB MODE: pdflatex/bibtex are NOT installed (or --latex stub).\n"
          f"!! The compile step uses {stub_dir}/pdflatex, which writes plain-text PDFs.\n"
          f"!! PDF checks are weakened; this is NOT a real LaTeX compile.\n{banner}\n", flush=True)
    return "stub", stub_dir


# --------------------------------------------------------------------------
# One pipeline run
# --------------------------------------------------------------------------

_print_lock = threading.Lock()


def _say(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _clean_env(extra_path: Optional[Path]) -> Dict[str, str]:
    """Only what a process needs: no real API keys, base URLs or proxies leak into the run."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "SHELL", "TERM",
            "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LOCALAPPDATA", "APPDATA", "USERPROFILE")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PO_PRICING_ONLINE"] = "0"  # no price-list / exchange-rate downloads (utils/pricing.py)
    if extra_path:
        env["PATH"] = str(extra_path) + os.pathsep + env.get("PATH", "")
    return env


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "project"


def _write_provided_figure(path: Path) -> None:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(im)
    for i, (h, color) in enumerate(((390, (154, 165, 177)), (360, (91, 125, 168)), (450, (43, 74, 112)))):
        draw.rectangle([120 + i * 250, 560 - h, 300 + i * 250, 560], fill=color)
    draw.line([80, 560, 860, 560], fill="black", width=3)
    im.save(path, format="PNG")


def run_pipeline(label: str, scenario: Scenario, repo: Path, code_under_test: str, server, work_dir: Path,
                 args, latex_mode: str, stub_dir: Optional[Path]) -> RunResult:
    res = RunResult(label=label, scenario=scenario.name, code_under_test=code_under_test, repo=str(repo))
    run_root = work_dir / label
    if run_root.exists():
        shutil.rmtree(run_root)
    (run_root / "tmp").mkdir(parents=True)
    (run_root / "mplconfig").mkdir()
    output_root = run_root / "output"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"{_slug('e2e ' + scenario.name)}_{stamp}_{uuid.uuid4().hex[:8]}"
    raw = run_dir / "raw_materials"
    (raw / "figures").mkdir(parents=True)
    shutil.copy(FIXTURES / "idea_sparse.md", raw / "idea_sparse.md")
    shutil.copy(FIXTURES / "experimental_log.md", raw / "experimental_log.md")
    if not scenario.use_plotting:
        # What runner.rs does for an attached figure: copy it and write figures/info.json.
        name, caption = PROVIDED_FIGURE
        _write_provided_figure(raw / "figures" / name)
        (raw / "figures" / "info.json").write_text(json.dumps([{"name": name, "caption": caption}], indent=2),
                                                   encoding="utf-8")
    if scenario.inject_faults:
        # Directories where the additive reports want to write files: every write must fail.
        for rel in FAULT_BLOCKED_PATHS:
            (run_dir / rel).mkdir(parents=True)
    res.run_dir = str(run_dir)

    scholar_log = run_root / "scholar_requests.jsonl"
    env = _clean_env(stub_dir)
    env.update({
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",            # as runner.rs
        "PYTHONDONTWRITEBYTECODE": "1",                              # keep checkouts clean
        "PYTHONPATH": str(HOOKS),                                    # loads hooks/sitecustomize.py
        "PO_E2E_FAKE_SCHOLAR": "1", "PO_E2E_SCHOLAR_LOG": str(scholar_log),
        "TMPDIR": str(run_root / "tmp"), "TEMP": str(run_root / "tmp"), "TMP": str(run_root / "tmp"),
        "MPLCONFIGDIR": str(run_root / "mplconfig"), "MPLBACKEND": "Agg",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })
    env.update(scenario.env(server.url_for(label), run_root))

    cmd = [args.python, "-u", str(repo / "paper_writing_cli.py"),
           "--raw_materials_dir", str(raw),
           "--output_dir", str(run_dir),
           "--latex_template_dir", str(repo / "templates" / TEMPLATE),
           "--idea_filename", "idea_sparse.md",
           "--experimental_log_filename", "experimental_log.md",
           "--writer_model_name", scenario.models["writer"],
           "--reflection_model_name", scenario.models["reflection"],
           "--use_plotting", "true" if scenario.use_plotting else "false"]
    if scenario.use_plotting:  # runner.rs passes these only when plotting is on
        cmd += ["--plotting_model_name", scenario.models["plotting"],
                "--image_model_name", IMAGE_MODEL,
                "--plotting_max_critic_rounds", "3"]
    (run_root / "command.json").write_text(json.dumps({"cwd": str(repo), "argv": cmd}, indent=2))

    log_path = run_root / "pipeline_output.log"
    res.log_path = str(log_path)
    _say(f"[{label}] starting ({code_under_test}, scenario {scenario.name}: {scenario.description})")
    t0 = time.time()
    out_lines: List[str] = []
    try:
        proc = subprocess.Popen(cmd, cwd=str(repo), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", start_new_session=True)
    except OSError as e:
        res.error = f"could not start the pipeline: {e}"
        return res

    def pump():
        with open(log_path, "w", encoding="utf-8") as log:
            for line in proc.stdout:
                out_lines.append(line)
                log.write(line)
                if args.verbose:
                    _say(f"[{label}] {line.rstrip()}")

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        proc.wait()
        res.error = f"timed out after {args.timeout}s"
    reader.join(timeout=30)
    res.exit_code = proc.returncode
    res.seconds = round(time.time() - t0, 1)
    output = "".join(out_lines)

    entries = server.entries_for(label)
    scholar_entries = []
    if scholar_log.exists():
        scholar_entries = [json.loads(l) for l in scholar_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    res.stage_counts = dict(sorted(collections.Counter(e["stage"] for e in entries).items()))
    (run_root / "llm_requests.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    try:
        verify_core(res, run_dir, output, entries, scholar_entries, scenario, latex_mode)
        res.head_extras = (repo / "utils" / "usage_tracker.py").exists()
        if scenario.inject_faults:
            verify_faults(res, run_dir, run_root, entries, output)
        elif res.head_extras:
            verify_head_extras(res, run_dir, entries, scenario)
    except Exception as e:  # a harness bug must not look like a pass
        import traceback

        res.add("harness verification ran without crashing", False, traceback.format_exc(limit=3))
    res.manifest = manifest(run_dir)
    n_ok = sum(c.ok for c in res.checks)
    _say(f"[{label}] finished in {res.seconds}s, exit {res.exit_code}: "
         f"{'PASS' if res.passed else 'FAIL'} ({n_ok}/{len(res.checks)} checks)")
    return res


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

_CITE_RE = re.compile(r"\\(?:no)?cite[a-zA-Z]*\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]*)\}")


def cited_keys(tex: str) -> set:
    tex = re.sub(r"(?<!\\)%.*", "", tex)
    keys = set()
    for group in _CITE_RE.findall(tex):
        keys.update(k.strip() for k in group.split(",") if k.strip())
    return keys


def bib_entries(text: str) -> Dict[str, str]:
    out = {}
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", text, re.DOTALL):
        if m.group(1).lower() in ("string", "comment", "preamble"):
            continue
        out[m.group(2)] = m.group(0)
    return out


def _load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _pdf_info(path: Path):
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        text = "".join(page.get_text() for page in doc)
        return doc.page_count, text


def _image_size(path: Path):
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _nonempty(path: Path, min_bytes: int = 1) -> bool:
    return path.is_file() and path.stat().st_size >= min_bytes


def verify_core(res: RunResult, run_dir: Path, output: str, entries, scholar_entries, scenario: Scenario,
                latex_mode: str) -> None:
    add = res.add
    # --- process ---
    add("exit code 0", res.exit_code == 0 and not res.error, f"exit={res.exit_code} {res.error}".strip())
    hits = [m for m in TRACEBACK_MARKERS if m in output]
    add("no traceback / retry / failure banner in output", not hits, ", ".join(hits))
    add("pipeline reported the final PDF", ">> Final PDF saved to" in output)
    add("dummy API key never printed", DUMMY_KEY not in output)

    # --- Step 1: outline ---
    outline_path = run_dir / "outline.json"
    outline = _load_json(outline_path) if _nonempty(outline_path) else None
    add("outline.json: valid JSON with plotting_plan / intro_related_work_plan / section_plan",
        isinstance(outline, dict) and all(k in outline for k in ("plotting_plan", "intro_related_work_plan",
                                                                    "section_plan")))
    if isinstance(outline, dict):
        intro = outline.get("intro_related_work_plan", {}).get("introduction_strategy", {})
        add("outline.json: replaced by literature outline_v1 (citation_candidates injected)",
            bool(intro.get("citation_candidates")) and "citation_hints" not in json.dumps(outline.get("section_plan")))

    # --- Step 2a: literature ---
    lit = run_dir / "literature_agent_output"
    bib_text = (lit / "references.bib").read_text(encoding="utf-8") if _nonempty(lit / "references.bib") else ""
    bib = bib_entries(bib_text)
    add(f"literature_agent_output/references.bib: {len(FIXTURE_TITLES_USED)} entries",
        len(bib) == len(FIXTURE_TITLES_USED), f"{len(bib)} entries")
    add("references.bib has no doi/url fields (contract 2)",
        not re.search(r"^\s*(doi|url)\s*=", bib_text, re.IGNORECASE | re.MULTILINE))
    cmap = _load_json(lit / "citation_map.json") if _nonempty(lit / "citation_map.json") else {}
    add("literature_agent_output/citation_map.json: keys == .bib keys", bool(cmap) and set(cmap) == set(bib),
        f"map={sorted(cmap)} bib={sorted(bib)}")
    if cmap:
        titles = {_alnum(v.get("title", "")) for v in cmap.values()}
        missing = [t for t in FIXTURE_TITLES_USED if _alnum(t) not in titles]
        add("citation_map.json: every fixture paper the searches should find was enriched", not missing,
            "missing: " + "; ".join(missing))
    add("literature_agent_output/outline_v1.json: valid JSON", _nonempty(lit / "outline_v1.json")
        and isinstance(_load_json(lit / "outline_v1.json"), dict))
    upd = (lit / "updated_template.tex").read_text(encoding="utf-8") if _nonempty(lit / "updated_template.tex") else ""
    add("literature_agent_output/updated_template.tex: intro/related work written with citations",
        "\\section{Introduction}" in upd and "\\cite{" in upd and "Introduction here." not in upd)
    add("updated_template.tex cites only keys in references.bib", bool(upd) and cited_keys(upd) <= set(bib),
        f"unknown: {sorted(cited_keys(upd) - set(bib))}")

    # --- Step 2b: plotting (paper_writer_with_plotting.py only) ---
    plotting = scenario.use_plotting
    pres = _load_json(run_dir / "plotting_results.json") if _nonempty(run_dir / "plotting_results.json") else None
    if not plotting:
        add("no plotting_results.json without --use_plotting (as before)", pres is None)
        pres = None
    else:
        ok = isinstance(pres, list) and len(pres) == 2
        add("plotting_results.json: 2 figures with image_path + caption", ok and all(
            p.get("image_path") and p.get("caption") for p in pres), f"{len(pres) if isinstance(pres, list) else pres}")
    if isinstance(pres, list):
        for p in pres:
            img = run_dir / p.get("image_path", "missing")
            add(f"plot image {p.get('image_path')} is a decodable JPEG",
                _nonempty(img) and img.read_bytes()[:3] == b"\xff\xd8\xff" and _image_size(img) is not None)
            expected = EXPECTED_IMAGE_SIZES.get(p.get("figure_id"))
            if expected and _nonempty(img):
                size = _image_size(img)
                res.warn(f"plot image {p.get('figure_id')} has the size its own code renders "
                         f"(advisory: concurrent figures share pyplot state in execute_plot_code_worker)",
                         size is not None and all(abs(a - b) <= 40 for a, b in zip(size, expected)),
                         f"got {size}, single-threaded render gives {expected}")
            add(f"plot {p.get('figure_id')}: critic changed it once, then accepted",
                [r.get("suggestions") for r in p.get("critic_history", [])][-1:] == ["No changes needed."]
                and len(p.get("critic_history", [])) == 2)

    # --- Step 3: section writing ---
    lw = run_dir / "latex_writeup"
    draft = (lw / "raw_draft_paper.tex").read_text(encoding="utf-8") if _nonempty(lw / "raw_draft_paper.tex") else ""
    n_figs = 2 if plotting else 1
    add(f"latex_writeup/raw_draft_paper.tex: full paper with {n_figs} figure(s), table and citations",
        "\\documentclass" in draft and draft.count("\\includegraphics") == n_figs and "\\begin{tabular}" in draft
        and "\\cite{" in draft and fls.PAPER_TITLE in draft)
    info = _load_json(lw / "figures" / "info.json") if _nonempty(lw / "figures" / "info.json") else None
    add(f"latex_writeup/figures/info.json lists the {n_figs} {'generated' if plotting else 'provided'} figure(s)",
        isinstance(info, list) and len(info) == n_figs and all((lw / "figures" / f["name"]).is_file() for f in info))
    add("latex_writeup/references.bib == literature references.bib",
        _nonempty(lw / "references.bib") and (lw / "references.bib").read_text(encoding="utf-8") == bib_text)
    add("latex_writeup/template.tex == updated_template.tex",
        _nonempty(lw / "template.tex") and (lw / "template.tex").read_text(encoding="utf-8") == upd)

    # --- Step 4: content refinement ---
    crw = run_dir / "content_refinement_workdir"
    expected_files = ["initial_draft.tex", "initial_draft.pdf", "refined_paper_v1.tex", "refined_paper_v1.pdf",
                      "refined_paper_v2.tex", "refined_paper_v2.pdf", "content_refinement_worklog.json",
                      "peer_reviews/review_v0.json", "peer_reviews/review_v1.json", "peer_reviews/review_v2.json",
                      "format_candidate_v0.tex", "formatted_candidate_v1.tex", "formatted_candidate_v1.pdf",
                      "format_refinement_worklog.json", "final_refined_paper.tex", "final_refined_paper.pdf",
                      "pdf_screenshots/content_refinement/v0/grid_1.png",
                      "pdf_screenshots/content_refinement/v1/grid_1.png",
                      "pdf_screenshots/format_refinement/v0/grid_1.png",
                      "pdf_screenshots/format_refinement/v1/grid_1.png"]
    missing = [f for f in expected_files if not _nonempty(crw / f)]
    add("content_refinement_workdir: all refinement/format artefacts present", not missing, f"missing: {missing}")
    if _nonempty(crw / "content_refinement_worklog.json"):
        wl = _load_json(crw / "content_refinement_worklog.json")
        outcomes = {k: v.get("outcome") for k, v in wl.items()}
        add("refinement loop: v1 accepted (score up), v2 reverted (score down)",
            outcomes == {"v1": "ACCEPTED_SCORE_INCREASE", "v2": "REJECTED_SCORE_DECREASE"}, str(outcomes))
    if _nonempty(crw / "format_refinement_worklog.json"):
        fwl = _load_json(crw / "format_refinement_worklog.json")
        add("formatting loop: v1 compiled and accepted",
            fwl.get("v1", {}).get("outcome") == "ACCEPTED_COMPILE_SUCCESS", json.dumps(fwl)[:200])
    final_tex = (crw / "final_refined_paper.tex").read_text(encoding="utf-8") \
        if _nonempty(crw / "final_refined_paper.tex") else ""
    add("final_refined_paper.tex = accepted v1 + format fix (has zqrev1, no zqrev2, 0.95\\linewidth)",
        "zqrev1" in final_tex and "zqrev2" not in final_tex and "0.95\\linewidth" in final_tex)

    # --- time breakdown + final PDF ---
    tb = _load_json(run_dir / "time_breakdown.json") if _nonempty(run_dir / "time_breakdown.json") else {}
    if plotting:
        want = {"Start_Time_Absolute", "OutlineAgent", "Parallel_Lit_and_Plot", "SectionWritingAgent",
                "ContentRefinementAgent", "Total_Time_Span"}
        add("time_breakdown.json has every stage timing", set(tb) == want, f"{sorted(tb)}")
    else:
        add("no time_breakdown.json without --use_plotting (paper_writer.py never wrote one)",
            not (run_dir / "time_breakdown.json").exists())
    pdf = run_dir / "final_paper.pdf"
    min_kb = 20 if latex_mode == "real" else 1  # the stub writes small plain-text PDFs
    add(f"final_paper.pdf exists, %PDF, > {min_kb} KB",
        _nonempty(pdf, min_kb * 1000) and pdf.read_bytes()[:4] == b"%PDF",
        f"{pdf.stat().st_size if pdf.exists() else 0} bytes")
    if _nonempty(crw / "final_refined_paper.pdf") and pdf.exists():
        add("final_paper.pdf == content_refinement_workdir/final_refined_paper.pdf",
            pdf.read_bytes() == (crw / "final_refined_paper.pdf").read_bytes())
    if pdf.exists() and pdf.read_bytes()[:4] == b"%PDF":
        pages, text = _pdf_info(pdf)
        flat = _alnum(text)
        add("final_paper.pdf text has the title and the accepted revision marker",
            _alnum(fls.PAPER_TITLE) in flat and "zqrev1" in flat and "zqrev2" not in flat, f"{pages} page(s)")
        if latex_mode == "real":
            missing_refs = [t for t in FIXTURE_TITLES_USED if _alnum(t) not in flat]
            add("final_paper.pdf (real TeX): bibliography lists every cited fixture paper", not missing_refs,
                "missing: " + "; ".join(missing_refs))
            add("final_paper.pdf (real TeX): no unresolved citations/references", "[?" not in text and "??" not in text)
            add(f"final_paper.pdf (real TeX): {n_figs} figure(s) embedded",
                len(set(re.findall(r"Figure (\d)\.", text))) == n_figs, str(sorted(set(re.findall(r"Figure \d", text)))))

    # --- fake LLM traffic ---
    counts = collections.Counter(e["stage"] for e in entries)
    expected_counts = expected_stage_counts(plotting, head=not res.label.startswith("orig-"))
    add("every agent stage reached the fake LLM", all(counts.get(s) for s in expected_counts),
        "missing: " + ", ".join(s for s in expected_counts if not counts.get(s)))
    add("no unrecognised / failed LLM request", not counts.get("unknown") and all(e["status"] == 200 for e in entries),
        str([e for e in entries if e["status"] != 200][:3]))
    add("LLM calls per stage match the agents' control flow", dict(counts) == expected_counts,
        _counter_diff(expected_counts, counts))
    wrong = sorted({(e["stage"], e["model"]) for e in entries
                    if e["stage"] in STAGE_ROLE and e["model"] != scenario.wire_models[STAGE_ROLE[e["stage"]]]})
    add("each stage used its role's model (writer / reflection / plotting)", not wrong, str(wrong[:6]))
    vision_stages = {e["stage"] for e in entries if e["n_images"]}
    want_vision = ({"plot_critic", "diagram_critic", "figure_caption", "format_review"} if plotting
                   else {"section_writing", "format_review"})
    add("images sent to the vision stages (" + ", ".join(sorted(want_vision)) + ")",
        want_vision <= vision_stages, str(sorted(vision_stages)))

    # --- what the desktop app makes of the output (runner.rs detect_stage) ---
    rules = desktop_stage_rules()
    if rules:
        seq = desktop_stage_sequence(output, plotting, rules)
        res.desktop_stages = seq
        # literature and plotting run in parallel threads, so their lines interleave (and the
        # desktop's stage label may flip between them); everything after them is sequential.
        want_parallel = {"literature"} | ({"plotting"} if plotting else set())
        tail = seq[seq.index("writing"):] if "writing" in seq else []
        head_part = seq[1:seq.index("writing")] if "writing" in seq else seq[1:]
        add("desktop stage detection (runner.rs) walks every stage and ends 'completed'",
            seq[0] == "outline" and set(head_part) == want_parallel
            and tail == ["writing", "refinement", "completed"], " -> ".join(seq))

    # --- fake scholar traffic ---
    hosts = collections.Counter(e["host"] for e in scholar_entries)
    add("fake Semantic Scholar was queried", hosts.get("api.semanticscholar.org", 0) > 0, str(dict(hosts)))
    blocked = [e for e in scholar_entries if e.get("blocked")]
    add("no request tried to reach the real network", not blocked, str(blocked[:3]))


RUNNER_RS = DEFAULT_REPO.parent / "paper-orchestra-desktop" / "src-tauri" / "src" / "runner.rs"


def desktop_stage_rules():
    """[(stage, needs_use_plotting, [substrings])] from detect_stage() in the desktop's runner.rs, in order."""
    if not RUNNER_RS.is_file():
        return None
    src = RUNNER_RS.read_text(encoding="utf-8")
    m = re.search(r"fn detect_stage\(.*?\)\s*->\s*String\s*\{(.*?)\n\}", src, re.DOTALL)
    if not m:
        return None
    rules = []
    for cond, stage in re.findall(r"if\s+(.*?)\{\s*return\s+\"(\w+)\"\.to_string\(\);\s*\}", m.group(1), re.DOTALL):
        needles = re.findall(r'line\.contains\("((?:[^"\\]|\\.)*)"\)', cond)
        if needles:
            rules.append((stage, "use_plotting" in cond, [n.encode().decode("unicode_escape") for n in needles]))
    return rules or None


def desktop_stage_sequence(output: str, use_plotting: bool, rules) -> List[str]:
    """The stages the desktop app would show for this stdout (same first-match logic as detect_stage)."""
    current, seq = "outline", ["outline"]
    for line in output.splitlines():
        for stage, needs_plotting, needles in rules:
            if needs_plotting and not use_plotting:
                continue
            if any(n in line for n in needles):
                if stage != current:
                    current = stage
                    seq.append(stage)
                break
    return seq


def _counter_diff(expected: Dict[str, int], got) -> str:
    keys = sorted(set(expected) | set(got))
    return ", ".join(f"{k}: expected {expected.get(k, 0)} got {got.get(k, 0)}" for k in keys
                     if expected.get(k, 0) != got.get(k, 0))


def _is_iso8601(value) -> bool:
    try:
        dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _int_or_none(v) -> bool:
    return v is None or (isinstance(v, int) and not isinstance(v, bool))


def verify_faults(res: RunResult, run_dir: Path, run_root: Path, entries, output: str) -> None:
    """The core checks already ran; here: the faults really fired and were contained."""
    log = run_root / "injected_faults.jsonl"
    fired = collections.Counter()
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                fired[json.loads(line)["function"]] += 1
    if res.head_extras:
        res.add("[faults] token extraction raised on every LLM response and the run went on",
                fired.get("extract_tokens", 0) == len(entries), f"fired {dict(fired)}, {len(entries)} LLM calls")
        res.add("[faults] citation audit raised once and the run went on",
                fired.get("audit_run", 0) + fired.get("build_audit", 0) >= 1, f"fired {dict(fired)}")
    res.add("[faults] blocked report paths left as they were (no crash, no partial files)",
            all((run_dir / rel).is_dir() for rel in FAULT_BLOCKED_PATHS))
    if res.head_extras:
        res.add("[faults] the citation-audit failure was reported as a warning (log a warning, continue)",
                "injected fault in utils.citation_audit" in output)


def verify_head_extras(res: RunResult, run_dir: Path, entries, scenario: Scenario) -> None:
    """The additive per-run reports of the new code (shared contracts 1-3)."""
    add = res.add
    ints = lambda d, keys: isinstance(d, dict) and all(isinstance(d.get(k), int) for k in keys)  # noqa: E731
    # contract 1: usage_report.json
    up = run_dir / "usage_report.json"
    rep = _load_json(up) if _nonempty(up) else None
    tot_keys = ("calls", "errors", "input_tokens", "output_tokens")
    # Optional additions: a top-level "cost" estimate, and per row the finer
    # counters (usage_tracker.DETAIL_KEYS, ints) and "cost".
    detail_keys = ("cache_read_tokens", "cache_write_tokens", "cache_write_1h_tokens", "image_output_tokens",
                   "long_calls", "long_input_tokens", "long_output_tokens", "search_queries", "images")
    shape_ok = (isinstance(rep, dict) and {"generated_at", "totals", "by_model"} <= set(rep)
                <= {"generated_at", "totals", "by_model", "cost"}
                and (isinstance(rep.get("cost"), dict) if "cost" in rep else True)
                and _is_iso8601(rep.get("generated_at")) and ints(rep.get("totals"), tot_keys)
                and set(rep["totals"]) == set(tot_keys)
                and isinstance(rep.get("by_model"), list) and all(
                    isinstance(m, dict) and {"provider", "model", *tot_keys} <= set(m)
                    <= {"provider", "model", "cost", *tot_keys, *detail_keys}
                    and ints(m, [k for k in detail_keys if k in m])
                    and isinstance(m.get("provider"), str) and isinstance(m.get("model"), str) and ints(m, tot_keys)
                    for m in rep.get("by_model", [])))
    add("[extra] usage_report.json matches contract 1 (keys and types)", shape_ok)
    if shape_ok:
        calls = len(entries)
        in_tok = sum(e.get("prompt_tokens", 0) for e in entries)
        out_tok = sum(e.get("completion_tokens", 0) for e in entries)
        t = rep["totals"]
        add("[extra] usage_report totals == what the fake server served",
            (t["calls"], t["errors"], t["input_tokens"], t["output_tokens"]) == (calls, 0, in_tok, out_tok),
            f"report={t} server=calls {calls}, in {in_tok}, out {out_tok}")
        providers = {m["provider"] for m in rep["by_model"]}
        models = {m["model"] for m in rep["by_model"]}
        add("[extra] usage_report by_model names the provider and models used",
            providers == {scenario.provider} and models == {e["model"] for e in entries},
            f"providers={providers} models={models}")
        served = collections.defaultdict(lambda: [0, 0, 0])
        for e in entries:
            row = served[e["model"]]
            row[0] += 1
            row[1] += e.get("prompt_tokens", 0)
            row[2] += e.get("completion_tokens", 0)
        reported = {m["model"]: [m["calls"], m["input_tokens"], m["output_tokens"]] for m in rep["by_model"]}
        add("[extra] usage_report per-model calls/tokens == what the fake server served per model",
            reported == dict(served) and [(m["provider"], m["model"]) for m in rep["by_model"]]
            == sorted((m["provider"], m["model"]) for m in rep["by_model"]),
            f"report={reported} server={dict(served)}")
        check_desktop_reader(res, run_dir.parent.parent.parent, "usage", up, "usage_report.json")
    # contract 2: references_meta.json
    lit = run_dir / "literature_agent_output"
    meta = _load_json(lit / "references_meta.json") if _nonempty(lit / "references_meta.json") else None
    bib = bib_entries((lit / "references.bib").read_text(encoding="utf-8")) if _nonempty(lit / "references.bib") else {}
    meta_ok = isinstance(meta, dict) and all(
        isinstance(v, dict) and set(v) == {"title", "year", "doi", "url", "source"}
        and isinstance(v.get("title"), str) and _int_or_none(v.get("year"))
        and all(v.get(k) is None or isinstance(v.get(k), str) for k in ("doi", "url", "source"))
        for v in meta.values())
    add("[extra] references_meta.json matches contract 2, keys == .bib keys", meta_ok and set(meta) == set(bib),
        f"meta={sorted(meta or {})}")
    if meta_ok:
        add("[extra] references_meta.json carries the fixture DOIs and sources",
            all(v.get("doi") in FIXTURE_DOIS and v.get("source") for v in meta.values()))
        add("[extra] references_meta.json URLs are http(s) only",
            all(v.get("url") is None or str(v["url"]).startswith(("http://", "https://")) for v in meta.values()))
        bib_text = (lit / "references.bib").read_text(encoding="utf-8") if _nonempty(lit / "references.bib") else ""
        add("[extra] references.bib itself carries no doi/url fields (they live in references_meta.json)",
            not re.search(r"(?im)^\s*(doi|url)\s*=", bib_text))
    # contract 3: citation_audit.json / .md
    ca = _load_json(run_dir / "citation_audit.json") if _nonempty(run_dir / "citation_audit.json") else None
    summary_keys = ("cited", "bib_entries", "in_bibliography", "missing_in_bib", "uncited", "with_doi", "verified")
    entry_ok = lambda e: (isinstance(e, dict)  # noqa: E731
                          and set(e) == {"key", "title", "year", "doi", "url", "source", "cited", "status"}
                          and isinstance(e.get("key"), str) and isinstance(e.get("title"), str)
                          and _int_or_none(e.get("year")) and isinstance(e.get("cited"), bool)
                          and e.get("status") in ("verified", "metadata_only", "unverified"))
    ca_ok = (isinstance(ca, dict)
             and set(ca) == {"generated_at", "tex_file", "bib_file", "cited_bib_file", "summary", "missing_in_bib",
                             "uncited", "entries"}
             and isinstance(ca.get("cited_bib_file"), str)
             and set(ca.get("summary") or {}) == set(summary_keys)
             and _is_iso8601(ca.get("generated_at")) and isinstance(ca.get("tex_file"), str)
             and isinstance(ca.get("bib_file"), str) and ints(ca.get("summary"), summary_keys)
             and isinstance(ca.get("missing_in_bib"), list) and isinstance(ca.get("uncited"), list)
             and isinstance(ca.get("entries"), list) and all(entry_ok(e) for e in ca["entries"]))
    add("[extra] citation_audit.json matches contract 3 (exact keys and types)", ca_ok)
    if ca_ok:
        tex_rel = ca["tex_file"]
        tex_path = run_dir / tex_rel
        add("[extra] citation_audit tex_file is relative and exists", not os.path.isabs(tex_rel) and tex_path.is_file(),
            tex_rel)
        s = ca["summary"]
        cited = cited_keys(tex_path.read_text(encoding="utf-8")) if tex_path.is_file() else set()
        add("[extra] citation_audit summary agrees with the compiled tex and .bib",
            s["cited"] == len(cited) and s["bib_entries"] == len(bib) and s["missing_in_bib"] == 0
            and ca["missing_in_bib"] == [] and s["uncited"] == len(ca["uncited"]) == len(set(bib) - cited)
            and s["verified"] == len(bib) == s["with_doi"],
            f"summary={s} computed cited={len(cited)} bib={len(bib)}")
        add("[extra] citation_audit audits the tex that was compiled last (final_refined_paper.tex)",
            tex_rel == "content_refinement_workdir/final_refined_paper.tex", tex_rel)
        bib_rel = ca["bib_file"]
        add("[extra] citation_audit bib_file is relative and exists",
            bool(bib_rel) and not os.path.isabs(bib_rel) and (run_dir / bib_rel).is_file()
            and set(bib_entries((run_dir / bib_rel).read_text(encoding="utf-8"))) == set(bib), bib_rel)
        if meta_ok:
            mism = [e["key"] for e in ca["entries"] if e["key"] not in meta
                    or any(e[k] != meta[e["key"]][k] for k in ("doi", "url", "source"))]
            add("[extra] citation_audit entries carry references_meta.json's DOI/URL/source, all verified",
                not mism and all(e["status"] == "verified" for e in ca["entries"])
                and [e["key"] for e in ca["entries"]] == list(bib), f"mismatched={mism}")
        check_desktop_reader(res, run_dir.parent.parent.parent, "audit", run_dir / "citation_audit.json",
                             "citation_audit.json")
    add("[extra] citation_audit.md written", _nonempty(run_dir / "citation_audit.md", 50))


# --------------------------------------------------------------------------
# The desktop's readers (paper-orchestra-desktop/src/runReports.ts) under Node
# --------------------------------------------------------------------------

DESKTOP_DIR = DEFAULT_REPO.parent / "paper-orchestra-desktop"
_TS_LOCK = threading.Lock()
_TS_STATE: Dict[str, tuple] = {}

# Feeds one report through the desktop's reader and compares the result with the
# raw JSON: for a report that follows its contract the reader must be lossless.
TS_CHECK_SCRIPT = r"""
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { isDeepStrictEqual } from "node:util";
const [bundle, kind, file] = process.argv.slice(2);
const mod = await import(pathToFileURL(bundle).href);
const text = readFileSync(file, "utf8");
const raw = JSON.parse(text);
const parsed = kind === "usage" ? mod.parseUsageReport(text)
  : kind === "audit" ? mod.parseCitationAudit(text)
  : mod.normalizeConnectionReport(raw);
const ok = parsed !== null && isDeepStrictEqual(parsed, raw);
let detail = "";
if (!ok) {
  const a = JSON.stringify(raw), b = JSON.stringify(parsed);
  let i = 0;
  while (i < a.length && a[i] === b?.[i]) i++;
  detail = `differs at char ${i}: file=${a.slice(Math.max(0, i - 60), i + 80)} reader=${(b ?? "null").slice(Math.max(0, i - 60), i + 80)}`;
}
process.stdout.write(JSON.stringify({ ok, detail }));
"""


def _ts_readers(work_dir: Path):
    """(argv prefix for node, "") once runReports.ts is bundled; (None, reason) when it cannot be."""
    with _TS_LOCK:
        if "result" in _TS_STATE:
            return _TS_STATE["result"]
        node = shutil.which("node")
        src = DESKTOP_DIR / "src" / "runReports.ts"
        esbuild = DESKTOP_DIR / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
        if not (node and src.is_file() and esbuild.exists()):
            result = (None, f"skipped: node {'found' if node else 'missing'}, {src} "
                            f"{'found' if src.is_file() else 'missing'}, esbuild "
                            f"{'found' if esbuild.exists() else 'missing (run npm install in paper-orchestra-desktop)'}")
        else:
            out_dir = work_dir / "desktop_ts"
            out_dir.mkdir(parents=True, exist_ok=True)
            bundle = out_dir / "runReports.mjs"
            try:
                r = subprocess.run([str(esbuild), str(src), "--bundle", "--format=esm", "--platform=node",
                                    f"--outfile={bundle}", "--log-level=error"],
                                   capture_output=True, text=True, timeout=120)
                ok, why = r.returncode == 0 and bundle.is_file(), (r.stderr or r.stdout)[-400:]
            except (OSError, subprocess.SubprocessError) as e:
                ok, why = False, str(e)
            if ok:
                script = out_dir / "check_reader.mjs"
                script.write_text(TS_CHECK_SCRIPT, encoding="utf-8")
                result = ([node, str(script), str(bundle)], "")
            else:
                result = (None, f"esbuild could not bundle {src.name}: {why}")
        _TS_STATE["result"] = result
        return result


def check_desktop_reader(res: RunResult, work_dir: Path, kind: str, path: Path, what: str) -> None:
    """kind: usage | audit | check. A missing Node toolchain is an advisory warning, not a failure."""
    name = f"[desktop] runReports.ts reads {what} exactly as written"
    argv, why = _ts_readers(work_dir)
    if argv is None:
        res.warn(name, False, why)
        return
    try:
        r = subprocess.run(argv + [kind, str(path)], capture_output=True, text=True, timeout=60)
        out = json.loads(r.stdout)
    except Exception as e:
        res.add(name, False, f"{type(e).__name__}: {e}")
        return
    res.add(name, out.get("ok") is True, out.get("detail", ""))


PROVIDER_ROW_KEYS = {"name", "label", "kind", "configured", "model", "model_source", "ok", "busy", "latency_ms",
                     "error"}
SCHOLAR_ROW_KEYS = {"name", "ok", "busy", "latency_ms", "hits", "error"}
# What the fake scholarly APIs answer (hooks/po_e2e_fake_scholar.py): real results from these
# two, HTTP 404 from the other default sources.
SCHOLAR_ANSWERING = {"semantic_scholar", "arxiv"}
SCHOLAR_DEFAULT = {"semantic_scholar", "openalex", "crossref", "arxiv", "dblp", "europepmc", "pubmed"}


def _contract4_problems(report) -> List[str]:
    """Shared contract 4, exactly: key sets and types of every row."""
    problems = []
    if not (isinstance(report, dict) and set(report) == {"providers", "scholar"}):
        return [f"top-level keys {sorted(report) if isinstance(report, dict) else type(report).__name__}"]
    for row in report["providers"]:
        if not (isinstance(row, dict) and set(row) == PROVIDER_ROW_KEYS and isinstance(row["name"], str)
                and isinstance(row["label"], str) and row["kind"] in ("openai", "anthropic", "gemini")
                and isinstance(row["configured"], bool) and (row["model"] is None or isinstance(row["model"], str))
                and row["ok"] in (None, True, False) and _int_or_none(row["latency_ms"])
                and (row["error"] is None or isinstance(row["error"], str))):
            problems.append(f"provider row {row}")
    for row in report["scholar"]:
        if not (isinstance(row, dict) and set(row) == SCHOLAR_ROW_KEYS and isinstance(row["name"], str)
                and row["ok"] in (None, True, False) and _int_or_none(row["latency_ms"])
                and _int_or_none(row["hits"]) and (row["error"] is None or isinstance(row["error"], str))):
            problems.append(f"scholar row {row}")
    names = [r.get("name") for r in report["providers"] if isinstance(r, dict)]
    if len(names) != len(set(names)):
        problems.append(f"duplicate provider names {names}")
    return problems


def run_check_providers(label: str, repo: Path, code_under_test: str, server, work_dir: Path, args) -> RunResult:
    """tools/check_providers.py --json [--ping --scholar] against the fake LLM and scholar fakes."""
    res = RunResult(label=label, scenario="check_providers", code_under_test=code_under_test, repo=str(repo))
    run_root = work_dir / label
    if run_root.exists():
        shutil.rmtree(run_root)
    (run_root / "tmp").mkdir(parents=True)
    res.run_dir = str(run_root)
    url = server.url_for(label)
    providers = run_root / "providers.json"
    providers.write_text(json.dumps({"providers": [
        {"name": "fake", "type": "openai", "base_url": url, "api_key_env": "PO_E2E_FAKE_KEY",
         "default_model": "fake-auto"},
        {"name": "deadproxy", "type": "openai", "base_url": "http://127.0.0.1:9/v1",
         "api_key_env": "PO_E2E_FAKE_KEY", "default_model": "m"},
    ]}, indent=2), encoding="utf-8")
    scholar_log = run_root / "scholar_requests.jsonl"
    env = _clean_env(None)
    # The tool loads <repo>/.env without overriding variables that are set: blank all of
    # them, so a developer's real keys can never be pinged from here.
    dot_env = repo / ".env"
    if dot_env.is_file():
        for line in dot_env.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if m:
                env[m.group(1)] = ""
    env.update({
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",  # as diagnostics.rs
        "PYTHONPATH": str(HOOKS), "PO_E2E_FAKE_SCHOLAR": "1", "PO_E2E_SCHOLAR_LOG": str(scholar_log),
        "TMPDIR": str(run_root / "tmp"), "TEMP": str(run_root / "tmp"), "TMP": str(run_root / "tmp"),
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        "OPENAI_API_KEY": DUMMY_KEY, "OPENAI_BASE_URL": url,
        "PO_PROVIDERS_FILE": str(providers), "PO_E2E_FAKE_KEY": DUMMY_KEY,
    })
    tool = repo / "tools" / "check_providers.py"
    _say(f"[{label}] starting ({code_under_test}, tools/check_providers.py --json / --json --ping --scholar)")
    t0 = time.time()

    def invoke(flags, name):
        cmd = [args.python, str(tool), "--json", *flags]
        t = time.time()
        try:
            p = subprocess.run(cmd, cwd=str(repo), env=env, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180)
        except subprocess.TimeoutExpired:
            res.add(f"check_providers {name}: finished within 180 s", False)
            return None
        secs = time.time() - t
        (run_root / f"{name}.stdout.txt").write_text(p.stdout, encoding="utf-8")
        (run_root / f"{name}.stderr.txt").write_text(p.stderr, encoding="utf-8")
        lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
        report = None
        if len(lines) == 1:
            try:
                report = json.loads(lines[0])
            except json.JSONDecodeError:
                pass
        res.add(f"check_providers {name}: exit 0, stdout is exactly one JSON object",
                p.returncode == 0 and isinstance(report, dict),
                f"exit {p.returncode}, {len(lines)} stdout line(s), stderr tail: {p.stderr[-300:]!r}")
        res.add(f"check_providers {name}: the dummy key is never printed",
                DUMMY_KEY not in p.stdout and DUMMY_KEY not in p.stderr)
        if isinstance(report, dict):
            problems = _contract4_problems(report)
            res.add(f"check_providers {name}: matches contract 4 (exact keys and types)", not problems,
                    "; ".join(problems)[:600])
            (run_root / f"{name}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                                   encoding="utf-8")
            check_desktop_reader(res, work_dir, "check", run_root / f"{name}.json", f"the {name} report")
            return report if not problems else None
        return None

    try:
        n_before = len(server.entries_for(label))
        cfg = invoke([], "config")
        if cfg is not None:
            rows = {r["name"]: r for r in cfg["providers"]}
            res.add("check_providers config: nothing is sent without --ping",
                    len(server.entries_for(label)) == n_before
                    and all(r["ok"] is None and r["latency_ms"] is None for r in cfg["providers"] + cfg["scholar"]))
            res.add("check_providers config: configured flags follow the environment and providers.json",
                    all(rows.get(n, {}).get("configured") is True for n in ("openai", "fake", "deadproxy"))
                    and rows.get("anthropic", {}).get("configured") is False
                    and rows.get("fake", {}).get("model") == "fake-auto",
                    str({n: (r["configured"], r["model"]) for n, r in rows.items() if r["configured"]}))
        live = invoke(["--ping", "--scholar"], "ping_scholar")
        if live is not None:
            rows = {r["name"]: r for r in live["providers"]}
            pings = [e for e in server.entries_for(label) if e["stage"] == "provider_ping"]
            res.add("check_providers --ping: the fake-served providers answer ok with a latency",
                    all(rows.get(n, {}).get("ok") is True and isinstance(rows[n]["latency_ms"], int)
                        and rows[n]["error"] is None for n in ("openai", "fake")),
                    str({n: rows.get(n) for n in ("openai", "fake")}))
            res.add("check_providers --ping: an unreachable provider is ok=false with a short error",
                    rows.get("deadproxy", {}).get("ok") is False and bool(rows["deadproxy"]["error"])
                    and len(rows["deadproxy"]["error"]) <= 240, str(rows.get("deadproxy")))
            res.add("check_providers --ping: providers that are not configured are not pinged (ok=null)",
                    all(r["ok"] is None for r in live["providers"] if not r["configured"]))
            res.add("check_providers --ping: one tiny request (<= 8 output tokens) per reachable provider",
                    sorted(e["model"] for e in pings) == ["fake-auto", "gpt-6.2-sol"]  # OpenAI platform default
                    and all(isinstance(e.get("max_tokens"), int) and e["max_tokens"] <= 8 for e in pings),
                    str([(e["model"], e.get("max_tokens")) for e in pings]))
            srows = {r["name"]: r for r in live["scholar"]}
            res.add("check_providers --scholar: one row per default source",
                    set(srows) == SCHOLAR_DEFAULT, str(sorted(srows)))
            res.add("check_providers --scholar: answering sources ok with hit counts, failing ones ok=false + error",
                    all(srows.get(n, {}).get("ok") is True and isinstance(srows[n]["hits"], int)
                        for n in SCHOLAR_ANSWERING)
                    and all(r["ok"] is False and r["error"] and r["hits"] is None
                            for n, r in srows.items() if n not in SCHOLAR_ANSWERING),
                    str(srows)[:600])
            scholar = [json.loads(ln) for ln in scholar_log.read_text(encoding="utf-8").splitlines()
                       if ln.strip()] if scholar_log.exists() else []
            res.add("check_providers --scholar: one query per source, nothing reached the real network",
                    len(scholar) == len(SCHOLAR_DEFAULT) and not any(e.get("blocked") for e in scholar),
                    str(collections.Counter(e["host"] for e in scholar)))
    except Exception:  # a harness bug must not look like a pass
        import traceback

        res.add("harness verification ran without crashing", False, traceback.format_exc(limit=3))
    res.seconds = round(time.time() - t0, 1)
    res.exit_code = 0
    res.stage_counts = dict(collections.Counter(e["stage"] for e in server.entries_for(label)))
    n_ok = sum(c.ok for c in res.checks)
    _say(f"[{label}] finished in {res.seconds}s: {'PASS' if res.passed else 'FAIL'} ({n_ok}/{len(res.checks)} checks)")
    return res


# --------------------------------------------------------------------------
# Manifest / comparison with the baseline
# --------------------------------------------------------------------------


def manifest(run_dir: Path) -> List[str]:
    if not run_dir.is_dir():
        return []
    return sorted(str(p.relative_to(run_dir)).replace(os.sep, "/") for p in run_dir.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts)


def _shape(obj):
    if isinstance(obj, dict):
        return {k: _shape(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_shape(obj[0])] if obj else []
    return type(obj).__name__


def _sorted_lists(obj):
    if isinstance(obj, dict):
        return {k: _sorted_lists(v) for k, v in obj.items()}
    if isinstance(obj, list):
        items = [_sorted_lists(v) for v in obj]
        try:
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True))
        except TypeError:
            return items
    return obj


def _first_diff(a: str, b: str) -> str:
    la, lb = a.splitlines(), b.splitlines()
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            return f"line {i + 1}: original={x[:120]!r} head={y[:120]!r}"
    return f"length differs: original {len(la)} lines, head {len(lb)} lines"


CONTENT_COMPARE = [
    ("literature_agent_output/references.bib", "bib"),
    ("literature_agent_output/citation_map.json", "json"),
    ("literature_agent_output/updated_template.tex", "text"),
    ("literature_agent_output/outline_v1.json", "json_unordered"),
    ("outline.json", "json_unordered"),
    ("plotting_results.json", "json_unordered"),
    ("latex_writeup/raw_draft_paper.tex", "text"),
    ("latex_writeup/figures/info.json", "json_unordered"),
    ("content_refinement_workdir/content_refinement_worklog.json", "json"),
    ("content_refinement_workdir/format_refinement_worklog.json", "json"),
    ("content_refinement_workdir/peer_reviews/review_v0.json", "json"),
    ("content_refinement_workdir/final_refined_paper.tex", "text"),
    ("time_breakdown.json", "keys"),
]


def compare(orig: RunResult, head: RunResult) -> Dict[str, list]:
    o_dir, h_dir = Path(orig.run_dir), Path(head.run_dir)
    o_files, h_files = set(orig.manifest), set(head.manifest)
    regressions, additive, same = [], [], []
    for f in sorted(o_files - h_files):
        regressions.append(f"output produced by the original but not by HEAD: {f}")
    for f in sorted(h_files - o_files):
        additive.append(f)
    # JSON shapes of every common JSON file
    for f in sorted(o_files & h_files):
        if f.endswith(".json"):
            try:
                so, sh = _shape(_load_json(o_dir / f)), _shape(_load_json(h_dir / f))
            except Exception as e:
                regressions.append(f"{f}: could not parse ({e})")
                continue
            if so != sh:
                regressions.append(f"JSON shape differs: {f}: original={json.dumps(so)[:200]} head={json.dumps(sh)[:200]}")
    # selected contents
    for rel, kind in CONTENT_COMPARE:
        a, b = o_dir / rel, h_dir / rel
        if not (a.is_file() and b.is_file()):
            continue
        if kind == "bib":
            ea, eb = bib_entries(a.read_text(encoding="utf-8")), bib_entries(b.read_text(encoding="utf-8"))
            equal = ea == eb
            detail = f"keys only in original {sorted(set(ea) - set(eb))}, only in HEAD {sorted(set(eb) - set(ea))}"
        elif kind == "text":
            ta, tb = a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8")
            equal, detail = ta == tb, (_first_diff(ta, tb) if ta != tb else "")
        elif kind == "keys":
            equal = set(_load_json(a)) == set(_load_json(b))
            detail = f"{sorted(_load_json(a))} vs {sorted(_load_json(b))}"
        else:
            ja, jb = _load_json(a), _load_json(b)
            if kind == "json_unordered":
                ja, jb = _sorted_lists(ja), _sorted_lists(jb)
            equal = ja == jb
            detail = f"original={json.dumps(ja, sort_keys=True)[:160]} head={json.dumps(jb, sort_keys=True)[:160]}"
        (same if equal else regressions).append(rel if equal else f"content differs: {rel}: {detail}")
    # LLM call sequence and check outcomes
    # (compared as visited set + final stage: the literature/plotting interleaving is thread timing)
    if orig.desktop_stages and (set(orig.desktop_stages) != set(head.desktop_stages)
                                or orig.desktop_stages[-1:] != head.desktop_stages[-1:]):
        regressions.append(f"desktop stage detection differs: original {' -> '.join(orig.desktop_stages)}, "
                           f"HEAD {' -> '.join(head.desktop_stages) or '(none)'}")
    head_counts = {k: v for k, v in (head.stage_counts or {}).items() if k not in HEAD_ONLY_STAGES}
    if orig.stage_counts != head_counts:
        regressions.append("LLM calls per stage differ: " + _counter_diff(orig.stage_counts, head_counts)
                           .replace("expected", "original").replace("got", "HEAD"))
    o_ok = {c.name: c.ok for c in orig.checks}
    for c in head.checks:
        if o_ok.get(c.name) and not c.ok:
            regressions.append(f"check passes on the original but fails on HEAD: {c.name} ({c.detail})")
    return {"regressions": regressions, "additive_outputs_in_head": additive, "identical_content": same}


# --------------------------------------------------------------------------
# Baseline worktree
# --------------------------------------------------------------------------


def make_worktree(repo: Path, ref: str, path: Path):
    # The checkout under test may be a plain copy (e.g. a mutated one); then the
    # git repository this harness lives in provides the baseline.
    for candidate in (repo, DEFAULT_REPO):
        r = subprocess.run(["git", "-C", str(candidate), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if r.returncode == 0:
            top = Path(r.stdout.strip())
            sub = candidate.resolve().relative_to(top.resolve())
            break
    else:
        raise SystemExit("--baseline needs a git repository (or pass --baseline-repo)")
    if path.exists():
        subprocess.run(["git", "-C", str(top), "worktree", "remove", "--force", str(path)],
                       capture_output=True, text=True)
        if path.exists():
            raise SystemExit(f"--baseline-worktree {path} exists and is not a removable worktree")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(top), "worktree", "add", "--detach", str(path), ref], check=True,
                   capture_output=True, text=True)

    def cleanup():
        r = subprocess.run(["git", "-C", str(top), "worktree", "remove", "--force", str(path)],
                           capture_output=True, text=True)
        subprocess.run(["git", "-C", str(top), "worktree", "prune"], capture_output=True, text=True)
        if r.returncode != 0:
            _say(f"warning: could not remove the baseline worktree {path}: {r.stderr.strip()}")

    return path / sub, cleanup


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def _print_run(res: RunResult) -> None:
    print(f"\n=== {res.label}: {res.code_under_test}, scenario '{res.scenario}' -> "
          f"{'PASS' if res.passed else 'FAIL'} (exit {res.exit_code}, {res.seconds}s)")
    print(f"    run dir: {res.run_dir}\n    log:     {res.log_path}")
    if res.error:
        print(f"    ERROR: {res.error}")
    for c in res.checks:
        mark = "ok  " if c.ok else "FAIL"
        line = f"    [{mark}] {c.name}"
        if not c.ok and c.detail:
            line += f"\n           -> {c.detail[:600]}"
        print(line)
    for w in res.warnings:
        if not w.ok:
            print(f"    [warn] {w.name}\n           -> {w.detail[:600]}")
    print(f"    LLM calls per stage: {res.stage_counts}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PaperOrchestra end-to-end regression harness (see module docstring).")
    ap.add_argument("--repo", default=str(DEFAULT_REPO))
    ap.add_argument("--scenarios", default="legacy,noplot,faults,custom,auto")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--baseline-ref", default="c68cd16")
    ap.add_argument("--baseline-worktree", default=None)
    ap.add_argument("--baseline-repo", default=None)
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--latex", choices=("auto", "real", "stub"), default="auto")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--json", default=None)
    ap.add_argument("--skip-check-providers", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not (repo / "paper_writing_cli.py").is_file():
        print(f"error: {repo} is not a paper-orchestra checkout (no paper_writing_cli.py)")
        return 2
    names = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"error: unknown scenario(s) {unknown}; choose from {sorted(SCENARIOS)}")
        return 2
    baseline_names = [n for n in names if SCENARIOS[n].baseline_ok]
    if args.baseline and not baseline_names:
        names.insert(0, "legacy")  # the comparison needs at least one scenario the original understands
        baseline_names = ["legacy"]

    created_work_dir = args.work_dir is None
    work_dir = Path(args.work_dir or tempfile.mkdtemp(prefix="po-e2e-")).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    print(f"PaperOrchestra e2e harness\n  repo:     {repo}\n  work dir: {work_dir}\n  python:   {args.python}")

    latex_mode, stub_dir = prepare_latex(args.latex, work_dir, args.python)
    print(f"  LaTeX:    {latex_mode}" + (f" ({shutil.which('pdflatex')})" if latex_mode == "real" else " (STUB)"))

    server = fls.FakeLLMServer(log_path=str(work_dir / "fake_llm_requests.jsonl")).start()
    print(f"  fake LLM: {server.base_url}/run/<label>/v1")

    cleanup = None
    what = "HEAD (working tree)" if repo == DEFAULT_REPO.resolve() else f"code under test ({repo})"
    plan = [(f"head-{n}", SCENARIOS[n], repo, what) for n in names]
    t_start = time.time()
    try:
        if args.baseline:
            if args.baseline_repo:
                base_repo = Path(args.baseline_repo).resolve()
            else:
                wt = Path(args.baseline_worktree).resolve() if args.baseline_worktree else work_dir / "orig"
                base_repo, cleanup = make_worktree(repo, args.baseline_ref, wt)
                print(f"  baseline: git worktree of {args.baseline_ref} at {wt}")
            for n in baseline_names:
                plan.append((f"orig-{n}", SCENARIOS[n], base_repo, f"ORIGINAL ({args.baseline_ref})"))

        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            futures = [pool.submit(run_pipeline, label, sc, rp, what, server, work_dir, args, latex_mode, stub_dir)
                       for label, sc, rp, what in plan]
            if not args.skip_check_providers and (repo / "tools" / "check_providers.py").is_file():
                futures.append(pool.submit(run_check_providers, "head-check_providers", repo, what, server,
                                           work_dir, args))
            results = [f.result() for f in futures]
    finally:
        server.stop()
        if cleanup:
            cleanup()

    for res in results:
        _print_run(res)

    by_label = {r.label: r for r in results}
    comparisons = {}
    for n in (baseline_names if args.baseline else []):
        orig, head = by_label.get(f"orig-{n}"), by_label.get(f"head-{n}")
        if not (orig and head):
            continue
        comparison = comparisons[n] = compare(orig, head)
        print(f"\n=== Baseline comparison '{n}': original ({args.baseline_ref}) vs HEAD")
        print(f"    original run: {'PASS' if orig.passed else 'FAIL'}, HEAD run: {'PASS' if head.passed else 'FAIL'}")
        print(f"    identical content: {len(comparison['identical_content'])} files "
              f"({', '.join(comparison['identical_content'])})")
        print(f"    additive outputs only in HEAD: {comparison['additive_outputs_in_head'] or 'none'}")
        if comparison["regressions"]:
            print("    REGRESSIONS:")
            for r in comparison["regressions"]:
                print(f"      - {r}")
        else:
            print("    regressions: none")

    # A failing original run is the baseline's problem, not HEAD's: only HEAD runs and
    # the comparisons decide the result.
    head_ok = all(r.passed for r in results if r.label.startswith("head-"))
    ok = head_ok and not any(c["regressions"] for c in comparisons.values())
    summary = {
        "ok": ok, "latex_mode": latex_mode, "seconds": round(time.time() - t_start, 1), "work_dir": str(work_dir),
        "runs": [{
            "label": r.label, "code": r.code_under_test, "scenario": r.scenario, "passed": r.passed,
            "exit_code": r.exit_code, "seconds": r.seconds, "run_dir": r.run_dir, "log": r.log_path,
            "error": r.error, "stage_counts": r.stage_counts, "desktop_stages": r.desktop_stages,
            "head_extras_checked": r.head_extras,
            "checks": [c.__dict__ for c in r.checks], "warnings": [w.__dict__ for w in r.warnings],
            "manifest": r.manifest,
        } for r in results],
        "comparisons": comparisons,
    }
    json_path = Path(args.json) if args.json else work_dir / "e2e_results.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'} in {summary['seconds']}s "
          f"(LaTeX: {latex_mode}{' - STUB, not a real compile' if latex_mode == 'stub' else ''})")
    if ok and created_work_dir and args.clean and not args.json:
        shutil.rmtree(work_dir, ignore_errors=True)
        print("(work dir removed by --clean)")
    else:
        print(f"results JSON: {json_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
