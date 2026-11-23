"""Stage-aware fake OpenAI-compatible LLM server for the PaperOrchestra e2e harness.

It answers POST <prefix>/v1/chat/completions the way a real model would have to
for the pipeline to get through every stage: it recognises the calling agent
from the system / user prompt (markers taken verbatim from methods/prompts/*,
utils/paper_banana_utils.py, autoraters/agent_review.py and the agents) and
returns a minimal but VALID answer in exactly the format that agent's parser
expects (```json outline```, search-query JSON, full ```latex``` documents
whose \\cite keys exist, runnable headless matplotlib code, review JSON, ...).

  <prefix>       "/run/<tag>" tags requests with a scenario name (default "default")
  GET  <prefix>/v1/models               -> model list
  POST <prefix>/v1/chat/completions     -> stage-specific answer + usage block
  POST <prefix>/v1/images/generations   -> a small JPEG (b64_json) + usage block

Every request is appended to the JSONL log (one object per line):
  {"ts", "run", "stage", "model", "n_images", "system_chars", "prompt_chars",
   "response_chars", "status", "prompt_tokens", "completion_tokens"}
A request no rule recognises is answered with HTTP 400 and logged as
stage "unknown", so a changed prompt shows up as a failed stage assertion.
The one exception is the connection test of tools/check_providers.py --ping (a
single user message "ping", no system prompt): it is answered "pong" and logged
as stage "provider_ping" together with the requested output-token limit.

Score script used by the reviewer stages (exercises accept AND revert paths of
ContentRefinementAgent): Overall = 5, +1 if the paper text contains "zqrev1",
-2 if it contains "zqrev2". The refinement stage inserts \\mbox{zqrev<N>} in
iteration N, so iteration 1 is accepted (5 -> 6) and iteration 2 is reverted
(6 -> 4), after which the formatting loop runs once.

Standalone use:  python tests/e2e/fake_llm_server.py --port 8765 --log /tmp/llm.jsonl
"""

import argparse
import base64
import io
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------------------
# Fixture content
# --------------------------------------------------------------------------

PAPER_TITLE = "Sparse Temporal Adapters for Parameter-Efficient Video Recognition"

FIG_DIAGRAM_ID = "fig_sta_framework_overview"
FIG_PLOT_ID = "fig_accuracy_vs_trainable_params"

OUTLINE = {
    "plotting_plan": [
        {
            "figure_id": FIG_DIAGRAM_ID,
            "title": "Overview of Sparse Temporal Adapters",
            "plot_type": "diagram",
            "data_source": "idea.md",
            "objective": "Schematic of a frozen video transformer with sparse temporal adapters between blocks.",
            "aspect_ratio": "16:9",
        },
        {
            "figure_id": FIG_PLOT_ID,
            "title": "Accuracy versus trainable parameters",
            "plot_type": "plot",
            "data_source": "experimental_log.md",
            "objective": "Bar Chart comparing top-1 accuracy of full fine-tuning, adapters and STA.",
            "aspect_ratio": "4:3",
        },
    ],
    "intro_related_work_plan": {
        "introduction_strategy": {
            "hook_hypothesis": "Video transformers dominate action recognition but are expensive to adapt.",
            "problem_gap_hypothesis": "Full fine-tuning of video transformers is too costly for many downstream tasks.",
            "search_directions": [
                "Survey of efficient video understanding",
                # Each fixture paper is reachable from exactly one search task, so the
                # section a paper is attributed to (first registration wins in the agent)
                # does not depend on thread timing.
                "Residual video backbones revisited",
            ],
        },
        "related_work_strategy": {
            "overview": "Contrast parameter-efficient tuning with long-range temporal modeling.",
            "subsections": [
                {
                    "subsection_title": "2.1 Parameter-Efficient Transfer",
                    "methodology_cluster": "Adapters and low-rank updates",
                    "sota_investigation_mission": "Find adapter and low-rank adaptation methods for vision backbones.",
                    "limitation_hypothesis": "They ignore temporal structure.",
                    "limitation_search_queries": ["parameter-efficient adapters low-rank adaptation limitations"],
                    "bridge_to_our_method": "STA adapts only sparse temporal interactions.",
                },
                {
                    "subsection_title": "2.2 Temporal Modeling",
                    "methodology_cluster": "Sparse attention over frames",
                    "sota_investigation_mission": "Find sparse attention methods for long videos.",
                    "limitation_hypothesis": "They require full training of the backbone.",
                    "limitation_search_queries": ["long-range temporal modeling sparse attention cost"],
                    "bridge_to_our_method": "STA keeps the backbone frozen.",
                },
            ],
        },
    },
    "section_plan": [
        {
            "section_title": "Abstract",
            "subsections": [
                {
                    "subsection_title": "Abstract Content",
                    "content_bullets": ["State the adaptation cost problem.", "Introduce STA.", "Report results."],
                    "citation_hints": [],
                }
            ],
        },
        {
            "section_title": "3. Methodology",
            "subsections": [
                {
                    "subsection_title": "3.1 Sparse Temporal Adapters",
                    "content_bullets": ["Formalize the adapter update from idea.md."],
                    "citation_hints": ["Lindqvist et al. (Temporal Transformers for Video Recognition)"],
                },
                {
                    "subsection_title": "3.2 Training Objective",
                    "content_bullets": ["Cross-entropy objective on frozen features."],
                    "citation_hints": [],
                },
            ],
        },
        {
            "section_title": "4. Experiments",
            "subsections": [
                {
                    "subsection_title": "4.1 Experimental Setup",
                    "content_bullets": ["Dataset, optimizer and schedule from experimental_log.md."],
                    "citation_hints": [
                        "research paper or technical report introducing 'Kinetics-Style Benchmarks for Human Action Recognition'",
                        "Brandt (Decoupled Weight Decay for Adaptive Optimizers)",
                    ],
                },
                {
                    "subsection_title": "4.2 Main Results",
                    "content_bullets": ["Compare against full fine-tuning and adapters."],
                    "citation_hints": [],
                },
            ],
        },
    ],
}

PLOT_CODE = """```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

methods = ["Full FT", "Adapter", "STA (ours)"]
accuracy = [78.1, 77.4, 79.3]
fig, ax = plt.subplots(figsize=(4.0, 3.0))
bars = ax.bar(methods, accuracy, color=["#9aa5b1", "#5b7da8", "#2b4a70"])
ax.set_ylabel("Top-1 accuracy (%)")
ax.set_ylim(70, 82)
for bar, value in zip(bars, accuracy):
    ax.text(bar.get_x() + bar.get_width() / 2, value + 0.3, f"{value:.1f}", ha="center", fontsize=8)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
fig.tight_layout()
```"""

DIAGRAM_CODE = """```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

fig, ax = plt.subplots(figsize=(6.4, 2.4))
ax.set_xlim(0, 10)
ax.set_ylim(0, 3)
ax.axis("off")
labels = ["Frames", "Frozen block", "Sparse temporal adapter", "Classifier"]
for i, label in enumerate(labels):
    x = 0.3 + i * 2.45
    ax.add_patch(FancyBboxPatch((x, 1.0), 2.0, 1.0, boxstyle="round,pad=0.05", fc="#eef2f7", ec="#2b4a70"))
    ax.text(x + 1.0, 1.5, label, ha="center", va="center", fontsize=8)
    if i < len(labels) - 1:
        ax.add_patch(FancyArrowPatch((x + 2.05, 1.5), (x + 2.4, 1.5), arrowstyle="->", mutation_scale=10))
fig.tight_layout()
```"""

CAPTIONS = {
    "plot": "Top-1 accuracy of full fine-tuning, bottleneck adapters and sparse temporal adapters.",
    "diagram": "Overview of sparse temporal adapters inserted between the blocks of a frozen video transformer.",
}

# --------------------------------------------------------------------------
# LaTeX helpers
# --------------------------------------------------------------------------

_TEX_SPECIALS = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
                 "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex_escape(text: str) -> str:
    return "".join(_TEX_SPECIALS.get(ch, ch) for ch in str(text))


def fill_block(tex: str, block: str, content: str, placeholder: str = "") -> str:
    """Replaces the body between the template's '%%%%%%%%% <BLOCK> %%%%%%%%%' markers."""
    marker = "%%%%%%%%% " + block + " %%%%%%%%%"
    pattern = re.compile(re.escape(marker) + r"\n(.*?)\n" + re.escape(marker), re.DOTALL)
    if pattern.search(tex):
        return pattern.sub(lambda m: marker + "\n" + content + "\n" + marker, tex, count=1)
    if placeholder and placeholder in tex:
        return tex.replace(placeholder, content, 1)
    return tex


def _cite(keys) -> str:
    keys = [k for k in keys if k]
    return ("~\\cite{" + ",".join(keys) + "}") if keys else ""


def _between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = text.find(end, i)
    return text[i:j] if j >= 0 else text[i:]


# --------------------------------------------------------------------------
# Stage responders: (system_text, user_text) -> assistant content
# --------------------------------------------------------------------------


def respond_outline(system, user):
    return "```json\n" + json.dumps(OUTLINE, indent=2) + "\n```"


def respond_literature_query(system, user):
    search_type = (re.search(r"SEARCH TYPE:\s*(.+)", user) or [None, "exploration"])[1].strip()
    focus = (re.search(r"SEARCH TASK:\s*(.+)", user) or [None, ""])[1].strip()
    if search_type == "targeted":
        title = (re.search(r"\(([^()]+)\)", focus) or re.search(r"'([^']+)'", focus))
        title = title.group(1).strip() if title else focus
        body = {"queries": [title], "likely_titles": [title]}
    else:
        words = focus.split()
        body = {"queries": [focus, " ".join(reversed(words))], "likely_titles": []}
    return "```json\n" + json.dumps(body, indent=2) + "\n```"


def respond_literature_write(system, user):
    payload = json.loads(user.split("\n\n", 1)[1])
    tex = payload["template.tex"]
    # Sorted: the checklist order follows thread completion order in the agent,
    # and a deterministic draft lets run_e2e.py diff two runs byte for byte.
    keys = sorted(payload.get("citation_checklist") or [])
    half = (len(keys) + 1) // 2
    intro = (
        "Video transformers are the dominant architecture for action recognition" + _cite(keys[:half]) + ", "
        "yet adapting them to a new task usually means updating every parameter. "
        "We study how much of this cost can be avoided by adapting only sparse temporal interactions."
    )
    related = (
        "\\paragraph{Parameter-efficient transfer.} Adapters and low-rank updates reduce the number of "
        "trainable parameters" + _cite(keys[half:]) + ". "
        "\\paragraph{Temporal modeling.} Sparse attention extends the temporal range of video models "
        "but still trains the full backbone."
    )
    tex = fill_block(tex, "INTRODUCTION", intro, "Introduction here.")
    tex = fill_block(tex, "RELATED WORK", related, "Related work here.")
    return "```latex\n" + tex + "\n```"


def respond_plot_planner(system, user):
    return ("Detailed Description: a bar chart with three bars (Full FT 78.1, Adapter 77.4, STA 79.3), "
            "y axis Top-1 accuracy (%) from 70 to 82, value labels above bars.")


def respond_diagram_planner(system, user):
    return ("Detailed Description: four rounded boxes from left to right (Frames, Frozen block, "
            "Sparse temporal adapter, Classifier) connected by arrows on a white background.")


def respond_stylist(system, user):
    desc = _between(user, "Detailed Description: ", "\nStyle Guidelines:").strip() or "Figure."
    return desc + " Style: muted ink-blue palette, thin dark outlines, 8 pt sans-serif labels."


def respond_plot_code(system, user):
    return PLOT_CODE


def respond_diagram_code(system, user):
    return DIAGRAM_CODE


def respond_critic(system, user):
    if "[critic-revised]" in user:
        body = {"critic_suggestions": "No changes needed.", "revised_description": "No changes needed."}
    else:
        desc = _between(user, "Detailed Description: ", "\n").strip() or "Figure."
        body = {"critic_suggestions": "Increase label font size slightly.",
                "revised_description": desc + " Labels 9 pt. [critic-revised]"}
    return "```json\n" + json.dumps(body, indent=2) + "\n```"


def respond_caption(system, user):
    task = (re.search(r"Task Type:\s*(\w+)", user) or [None, "plot"])[1].strip()
    return CAPTIONS.get(task, CAPTIONS["plot"])


def respond_retriever(system, user):
    key = "top10_plots" if "top10_plots" in system else "top10_diagrams"
    return "```json\n" + json.dumps({key: []}) + "\n```"


def respond_section_writing(system, user):
    tex = _between(user, "--- INPUT: template.tex (DO NOT CHANGE EXISTING TEXT. FILL MISSING SECTIONS.) ---\n",
                   "\n\n**INSTRUCTION**")
    keys = sorted(re.findall(r"^--- Key: (.+?) ---$", user, re.MULTILINE))
    figures = re.findall(r"- Filename: (.+)\n\s*- Caption: (.*)", user)

    def figure_env(name, caption):
        label = re.sub(r"[^A-Za-z0-9]+", "_", name.rsplit(".", 1)[0])
        return ("\\begin{figure}[t]\n\\centering\n"
                f"\\includegraphics[width=\\linewidth]{{figures/{name.strip()}}}\n"
                f"\\caption{{{tex_escape(caption.strip())}}}\n\\label{{fig:{label}}}\n\\end{{figure}}\n")

    method_figs = [f for f in figures if "framework" in f[0] or "overview" in f[0]]
    other_figs = [f for f in figures if f not in method_figs]
    abstract = ("Adapting video transformers usually requires updating every parameter. We propose Sparse "
                "Temporal Adapters (STA), which train only sparse temporal interactions on top of a frozen "
                "backbone, and reach 79.3\\% top-1 accuracy with 1.2M trainable parameters.")
    method = ("We insert a sparse temporal adapter after every block of a frozen video transformer"
              + _cite(keys[:1]) + ". Given frame tokens $x_t$, the adapter computes\n"
              "\\begin{equation}\n  h_t = x_t + W_u \\, \\sigma\\big(W_d \\sum_{s \\in \\mathcal{N}(t)} x_s\\big),\n"
              "  \\label{eq:sta}\n\\end{equation}\n"
              "where $\\mathcal{N}(t)$ is a sparse set of temporal neighbours.\n"
              + "".join(figure_env(n, c) for n, c in method_figs))
    table = ("\\begin{table}[t]\n\\centering\n\\small\n\\begin{tabular}{lcc}\n\\toprule\n"
             "Method & Top-1 (\\%) & Trainable params (M) \\\\\n\\midrule\n"
             "Full fine-tuning & 78.1 & 86.0 \\\\\nAdapter & 77.4 & 3.1 \\\\\n"
             "STA (ours) & \\textbf{79.3} & \\textbf{1.2} \\\\\n\\bottomrule\n\\end{tabular}\n"
             "\\caption{Main results on the action recognition benchmark.}\n\\label{tab:main}\n\\end{table}\n")
    experiments = ("We follow the standard benchmark protocol" + _cite(keys[1:3]) + " and train with "
                   "decoupled weight decay for 30 epochs. Table~\\ref{tab:main} summarises the results.\n"
                   + table + "".join(figure_env(n, c) for n, c in other_figs))
    conclusion = "STA adapts frozen video transformers with a small fraction of the parameters."
    tex = tex.replace("\\title{My Title}", "\\title{" + PAPER_TITLE + "}", 1)
    tex = fill_block(tex, "ABSTRACT", abstract, "Abstract here.")
    tex = fill_block(tex, "METHOD", method, "Methodology here.")
    tex = fill_block(tex, "EXPERIMENTS", experiments, "Experimental setup, results, and analysis here.")
    tex = fill_block(tex, "CONCLUSION", conclusion, "Conclusion here.")
    return "```latex\n" + tex + "\n```"


def _review(overall: int) -> dict:
    return {
        "Summary": "The paper proposes sparse temporal adapters for frozen video transformers.",
        "Strengths": ["Clear method", "Small number of trainable parameters"],
        "Weaknesses": ["Limited ablations"],
        "Originality": 3, "Quality": 3, "Clarity": 3, "Significance": 3,
        "Questions": ["How is the neighbourhood chosen?"],
        "Limitations": ["Single benchmark"],
        "Ethical Concerns": False,
        "Soundness": 3, "Presentation": 3, "Contribution": 3,
        "Overall": overall, "Confidence": 4,
        "Decision": "Accept" if overall >= 6 else "Reject",
    }


def respond_peer_review(system, user):
    paper = user.split("Here is the paper you are asked to review:", 1)[-1]
    overall = 5 + (1 if "zqrev1" in paper else 0) - (2 if "zqrev2" in paper else 0)
    return ("THOUGHT:\nThe method is simple and the evaluation is narrow.\n\nREVIEW JSON:\n```json\n"
            + json.dumps(_review(overall), indent=2) + "\n```")


def respond_meta_review(system, user):
    return "THOUGHT:\nAggregating the reviews.\n\nREVIEW JSON:\n```json\n" + json.dumps(_review(5), indent=2) + "\n```"


def respond_content_refinement(system, user):
    iteration = int((re.search(r"REFLECTION ITERATION (\d+)", user) or [None, "1"])[1])
    tex = _between(user, "--- CURRENT LATEX SOURCE ---\n", "\n\nINSTRUCTION:")
    note = f"This revision clarifies the neighbourhood selection (\\mbox{{zqrev{iteration}}})."
    marker = "%%%%%%%%% CONCLUSION %%%%%%%%%\n"
    if marker in tex:
        tex = tex.replace(marker, marker + note + "\n", 1)
    else:
        tex = tex.replace("\\end{document}", note + "\n\\end{document}", 1)
    worklog = {"addressed_weaknesses": [f"Clarified neighbourhood selection (iteration {iteration})"],
               "integrated_answers": ["Explained the sparse neighbourhood"],
               "actions_taken": ["Edited the conclusion"]}
    return "```json\n" + json.dumps(worklog, indent=2) + "\n```\n\n```latex\n" + tex + "\n```"


def respond_format_review(system, user):
    body = {"figure_and_tables": {"Figure 1": {"detected_issue": "The figure touches the column margin.",
                                               "suggested_fix": "Scale it to 0.95 of the column width."},
                                  "Table 1": {"detected_issue": "None", "suggested_fix": "None"}},
            "other_issues": []}
    return "```json\n" + json.dumps(body, indent=2) + "\n```"


def respond_format_fix(system, user):
    tex = _between(user, "--- CURRENT LATEX SOURCE ---\n", "\n\nOutput the Full Revised LaTeX only")
    tex = tex.replace("\\includegraphics[width=\\linewidth]", "\\includegraphics[width=0.95\\linewidth]")
    return "```latex\n" + tex + "\n```"


def respond_literature_screen(system, user):
    # Every fixture paper is on-topic: the screen keeps the whole library, so the
    # outputs stay identical to the original code's.
    return "```json\n" + json.dumps({"irrelevant": []}) + "\n```"


def respond_google_discovery(system, user):
    return "```json\n" + json.dumps({"section_name": "unknown", "candidates": []}) + "\n```"


# (stage, where, marker) - first match wins; "system" rules are checked before "user" rules.
RULES = [
    ("outline", "system", "venue-compliant paper outline", respond_outline),
    ("literature_write", "system", "Task: Write the introduction and related work section", respond_literature_write),
    ("section_writing", "system", "Task: Complete a research paper by writing the missing sections",
     respond_section_writing),
    ("content_refinement", "system", "Task: Revise and strengthen a LaTeX research paper", respond_content_refinement),
    ("peer_review", "system", "You are a reviewer. You write peer review of academic papers", respond_peer_review),
    ("meta_review", "system", "meta-reviewing a paper", respond_meta_review),
    ("figure_retriever", "system", "**You are the Retrieval Agent.**", respond_retriever),
    ("plot_planner", "system", "given the raw data (typically in tabular or json format)", respond_plot_planner),
    ("diagram_planner", "system", "given the 'Methodology' section of a paper, and the caption of the desired figure",
     respond_diagram_planner),
    ("plot_stylist", "system", "You are provided with a preliminary description of a statistical plot",
     respond_stylist),
    ("diagram_stylist", "system", "a planner agent has already generated a preliminary description of the target diagram",
     respond_stylist),
    ("plot_critic", "system", "provide a critique of the target plot", respond_critic),
    ("diagram_critic", "system", "provide a critique of the target diagram", respond_critic),
    ("plot_code", "user", "Use python matplotlib to generate a statistical plot", respond_plot_code),
    ("diagram_code", "user", "Write clean Python matplotlib (patches / FancyBboxPatch / FancyArrowPatch) code",
     respond_diagram_code),
    ("literature_query", "user", "Generate 2 concise academic search queries", respond_literature_query),
    ("literature_screen", "user", "You are screening a reference library for a research paper",
     respond_literature_screen),
    ("figure_caption", "user", "Please provide the final caption for this figure", respond_caption),
    ("format_review", "user", "Analyze the provided page images of a research paper for formatting issues",
     respond_format_review),
    ("format_fix", "user", "You are a LaTeX formatting expert.", respond_format_fix),
    ("literature_google_discovery", "user", "Use Google Search to find", respond_google_discovery),
]

# Every stage a full --use_plotting run must reach (figure_retriever only runs
# when the PaperBanana reference set is installed; google discovery only with Gemini).
REQUIRED_STAGES = [
    "outline", "literature_query", "literature_screen", "literature_write",
    "plot_planner", "diagram_planner", "plot_stylist", "diagram_stylist",
    "plot_code", "diagram_code", "plot_critic", "diagram_critic", "figure_caption",
    "section_writing", "peer_review", "meta_review", "content_refinement", "format_review", "format_fix",
]


def classify(system: str, user: str):
    for stage, where, marker, fn in RULES:
        if where == "system" and marker in system:
            return stage, fn
    for stage, where, marker, fn in RULES:
        if where == "user" and marker in user:
            return stage, fn
    return "unknown", None


def _message_text(messages):
    system_parts, user_parts, n_images = [], [], 0
    for m in messages or []:
        content = m.get("content")
        texts = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    texts.append(part.get("text") or "")
                elif part.get("type") == "image_url":
                    n_images += 1
        target = system_parts if m.get("role") in ("system", "developer") else user_parts
        target.extend(texts)
    return "\n".join(system_parts), "\n".join(user_parts), n_images


def _tiny_jpeg_b64() -> str:
    try:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 36), (43, 74, 112)).save(buf, format="JPEG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    server_version = "PaperOrchestraFakeLLM/1.0"

    def log_message(self, *args):  # keep the harness output clean
        pass

    def _run_tag(self) -> str:
        m = re.match(r"^/run/([^/]+)/", self.path)
        return m.group(1) if m else "default"

    def _send(self, status, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/v1/models"):
            self._send(200, {"object": "list", "data": [{"id": "fake-model", "object": "model"}]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": {"message": "invalid JSON body"}})
            return
        path = self.path.split("?", 1)[0]
        model = str(body.get("model", ""))
        if path.endswith("/images/generations"):
            self.server.record({"run": self._run_tag(), "stage": "image_generation", "model": model,
                                "n_images": 0, "system_chars": 0, "prompt_chars": len(str(body.get("prompt", ""))),
                                "response_chars": 0, "status": 200, "prompt_tokens": 8, "completion_tokens": 16})
            self._send(200, {"created": int(time.time()), "data": [{"b64_json": _tiny_jpeg_b64()}],
                             "usage": {"input_tokens": 8, "output_tokens": 16, "total_tokens": 24}})
            return
        if not path.endswith("/chat/completions"):
            self._send(404, {"error": {"message": f"unknown path {path}"}})
            return
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            self._send(400, {"error": {"message": "messages must be a non-empty list"}})
            return
        system, user, n_images = _message_text(messages)
        if not system and user.strip() == "ping" and len(messages) == 1:
            # tools/check_providers.py --ping (not a pipeline stage)
            limit = body.get("max_tokens", body.get("max_completion_tokens"))
            self.server.record({"run": self._run_tag(), "stage": "provider_ping", "model": model, "n_images": 0,
                                "system_chars": 0, "prompt_chars": len(user), "response_chars": 4, "status": 200,
                                "prompt_tokens": 1, "completion_tokens": 1, "max_tokens": limit})
            self._send(200, {
                "id": f"chatcmpl-e2e-{self.server.next_id()}", "object": "chat.completion",
                "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "pong"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
            return
        stage, fn = classify(system, user)
        entry = {"run": self._run_tag(), "stage": stage, "model": model, "n_images": n_images,
                 "system_chars": len(system), "prompt_chars": len(user)}
        if fn is None:
            entry.update(response_chars=0, status=400)
            self.server.record(entry)
            self._send(400, {"error": {"message": "fake LLM server: unrecognised pipeline stage",
                                       "type": "invalid_request_error"}})
            return
        try:
            content = fn(system, user)
        except Exception as e:  # a responder bug must be visible, not a silent bad answer
            entry.update(response_chars=0, status=500, error=f"{type(e).__name__}: {e}")
            self.server.record(entry)
            self._send(500, {"error": {"message": f"fake responder for {stage} failed: {e}"}})
            return
        prompt_tokens = max(1, (len(system) + len(user)) // 4) + 85 * n_images
        completion_tokens = max(1, len(content) // 4)
        entry.update(response_chars=len(content), status=200,
                     prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        self.server.record(entry)
        self._send(200, {
            "id": f"chatcmpl-e2e-{self.server.next_id()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens},
        })


class FakeLLMServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, host="127.0.0.1", port=0, log_path=None):
        super().__init__((host, port), _Handler)
        self.log_path = log_path
        self.entries = []
        self._lock = threading.Lock()
        self._counter = 0
        self._thread = None

    @property
    def base_url(self) -> str:
        return f"http://{self.server_address[0]}:{self.server_address[1]}"

    def url_for(self, tag: str) -> str:
        """OpenAI-style base URL (ending in /v1) whose requests are logged under `tag`."""
        return f"{self.base_url}/run/{tag}/v1"

    def next_id(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def record(self, entry: dict) -> None:
        entry = dict(entry, ts=round(time.time(), 3))
        with self._lock:
            self.entries.append(entry)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")

    def entries_for(self, tag: str):
        with self._lock:
            return [e for e in self.entries if e.get("run") == tag]

    def start(self):
        self._thread = threading.Thread(target=self.serve_forever, name="fake-llm", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--log", default=None, help="JSONL request log")
    args = ap.parse_args()
    server = FakeLLMServer(args.host, args.port, args.log)
    print(f"fake LLM server on {server.base_url}/v1 (log: {args.log or '-'})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
