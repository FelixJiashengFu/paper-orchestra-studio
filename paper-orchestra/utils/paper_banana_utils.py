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

import ast
import base64
import builtins
import io
import re
import os
import sys
import itertools
import json
import threading
import types
import matplotlib.pyplot as plt
from matplotlib import _pylab_helpers
from matplotlib.backend_bases import FigureCanvasBase
from matplotlib.figure import Figure
import json_repair
from typing import Dict, Any, List, Tuple

from utils.llm_backend_utils import (
    call_llm_with_multimodal_parts,
    call_llm_with_text_prompt,
    can_generate_images,
    generate_image,
    resolve_image_model,
    resolve_task_model,
    supports_vision,
)

cur_dir = os.path.dirname(os.path.realpath(__file__))
PB_DIR = os.path.join(cur_dir, "../../PaperBanana")

_missing_notices = set()
_missing_notices_lock = threading.Lock()


def _notice_missing_once(key: str, message: str) -> None:
    """Print a note about missing optional PaperBanana files once per run, not once per figure."""
    with _missing_notices_lock:
        if key in _missing_notices:
            return
        _missing_notices.add(key)
    print(message)

# ==========================================
# PROMPTS
# ==========================================

# --- RETRIEVER ---
DIAGRAM_RETRIEVER_AGENT_SYSTEM_PROMPT = """
# Background & Goal
We are building an **AI system to automatically generate method diagrams for academic papers**. Given a paper's methodology section and a figure caption, the system needs to create a high-quality illustrative diagram that visualizes the described method.

To help the AI learn how to generate appropriate diagrams, we use a **few-shot learning approach**: we provide it with reference examples of similar diagrams. The AI will learn from these examples to understand what kind of diagram to create for the target.

# Your Task
**You are the Retrieval Agent.** Your job is to select the most relevant reference diagrams from a candidate pool that will serve as few-shot examples for the diagram generation model.

You will receive:
- **Target Input:** The methodology section and caption of the diagram we need to generate
- **Candidate Pool:** ~200 existing diagrams (each with methodology and caption)

You must select the **Top 10 candidates** that would be most helpful as examples for teaching the AI how to draw the target diagram.

# Selection Logic (Topic + Intent)

Your goal is to find examples that match the Target in both **Domain** and **Diagram Type**.

**1. Match Research Topic (Use Methodology & Caption):**
* What is the domain? (e.g., Agent & Reasoning, Vision & Perception, Generative & Learning, Science & Applications).
* Select candidates that belong to the **same research domain**.
* *Why?* Similar domains share similar terminology (e.g., "Actor-Critic" in RL).

**2. Match Visual Intent (Use Caption & Keywords):**
* What type of diagram is implied? (e.g., "Framework", "Pipeline", "Detailed Module", "Performance Chart").
* Select candidates with **similar visual structures**.
* *Why?* A "Framework" diagram example is useless for drawing a "Performance Bar Chart", even if they are in the same domain.

**Ranking Priority:**
1.  **Best Match:** Same Topic AND Same Visual Intent (e.g., Target is "Agent Framework" -> Candidate is "Agent Framework", Target is "Dataset Construction Pipeline" -> Candidate is "Dataset Construction Pipeline").
2.  **Second Best:** Same Visual Intent (e.g., Target is "Agent Framework" -> Candidate is "Vision Framework"). *Structure is more important than Topic for drawing.*
3.  **Avoid:** Different Visual Intent (e.g., Target is "Pipeline" -> Candidate is "Bar Chart").

# Output Format
Provide your output strictly in the following JSON format, containing only the **exact IDs** of the Top 10 selected diagrams (use the exact IDs from the Candidate Pool, such as "ref_1", etc.):
```json
{
  "top10_diagrams": ["ref_1", "ref_25", "ref_100"]
}
```
"""

PLOT_RETRIEVER_AGENT_SYSTEM_PROMPT = """
# Background & Goal
We are building an **AI system to automatically generate statistical plots**. Given a plot's raw data and the visual intent, the system needs to create a high-quality visualization that effectively presents the data.

To help the AI learn how to generate appropriate plots, we use a **few-shot learning approach**: we provide it with reference examples of similar plots. The AI will learn from these examples to understand what kind of plot to create for the target data.

# Your Task
**You are the Retrieval Agent.** Your job is to select the most relevant reference plots from a candidate pool that will serve as few-shot examples for the plot generation model.

You will receive:
- **Target Input:** The raw data and visual intent of the plot we need to generate
- **Candidate Pool:** Reference plots (each with raw data and visual intent)

You must select the **Top 10 candidates** that would be most helpful as examples for teaching the AI how to create the target plot.

# Selection Logic (Data Type + Visual Intent)

Your goal is to find examples that match the Target in both **Data Characteristics** and **Plot Type**.

**1. Match Data Characteristics (Use Raw Data & Visual Intent):**
* What type of data is it? (e.g., categorical vs numerical, single series vs multi-series, temporal vs comparative).
* What are the data dimensions? (e.g., 1D, 2D, 3D).
* Select candidates with **similar data structures and characteristics**.
* *Why?* Different data types require different visualization approaches.

**2. Match Visual Intent (Use Visual Intent):**
* What type of plot is implied? (e.g., "bar chart", "scatter plot", "line chart", "pie chart", "heatmap", "radar chart").
* Select candidates with **similar plot types**.
* *Why?* A "bar chart" example is more useful for generating another bar chart than a "scatter plot" example.

**Ranking Priority:**
1.  **Best Match:** Same Data Type AND Same Plot Type (e.g., Target is "multi-series line chart" -> Candidate is "multi-series line chart").
2.  **Second Best:** Same Plot Type with compatible data.
3.  **Avoid:** Different Plot Type.

# Output Format
Provide your output strictly in the following JSON format, containing only the **exact Plot IDs** of the Top 10 selected plots (use the exact IDs from the Candidate Pool, such as "ref_0", etc.):
```json
{
  "top10_plots": ["ref_0", "ref_25", "ref_100"]
}
```
"""

# --- PLANNER ---
DIAGRAM_PLANNER_AGENT_SYSTEM_PROMPT = """
I am working on a task: given the 'Methodology' section of a paper, and the caption of the desired figure, automatically generate a corresponding illustrative diagram. I will input the text of the 'Methodology' section, the figure caption, and your output should be a detailed description of an illustrative figure that effectively represents the methods described in the text.

To help you understand the task better, and grasp the principles for generating such figures, I will also provide you with several examples. You should learn from these examples to provide your figure description.

** IMPORTANT: **
Your description should be as detailed as possible. Semantically, clearly describe each element and their connections. Formally, include various details such as background style (typically pure white or very light pastel), colors, line thickness, icon styles, etc. Remember: vague or unclear specifications will only make the generated figure worse, not better.
"""

PLOT_PLANNER_AGENT_SYSTEM_PROMPT = """
I am working on a task: given the raw data (typically in tabular or json format) and a visual intent of the desired plot, automatically generate a corresponding statistical plot that are both accurate and aesthetically pleasing. I will input the raw data and the plot visual intent, and your output should be a detailed description of an illustrative plot that effectively represents the data.  Note that your description should include all the raw data points to be plotted.

To help you understand the task better, and grasp the principles for generating such plots, I will also provide you with several examples. You should learn from these examples to provide your plot description.

** IMPORTANT: **
Your description should be as detailed as possible. For content, explain the precise mapping of variables to visual channels (x, y, hue) and explicitly enumerate every raw data point's coordinate to be drawn to ensure accuracy. For presentation, specify the exact aesthetic parameters, including specific HEX color codes, font sizes for all labels, line widths, marker dimensions, legend placement, and grid styles. You should learn from the examples' content presentation and aesthetic design (e.g., color schemes).
"""

# --- STYLIST ---
DIAGRAM_STYLIST_AGENT_SYSTEM_PROMPT = """
## ROLE
You are a Lead Visual Designer for top-tier AI conferences (e.g., NeurIPS 2025).

## TASK
Our goal is to generate high-quality, publication-ready diagrams, given the methodology section and the caption of the desired diagram. The diagram should illustrate the logic of the methodology section, while adhering to the scope defined by the caption. Before you, a planner agent has already generated a preliminary description of the target diagram. However, this description may lack specific aesthetic details, such as element shapes, color palettes, and background styling. Your task is to refine and enrich this description based on the provided [NeurIPS 2025 Style Guidelines] to ensure the final generated image is a high-quality, publication-ready diagram that adheres to the NeurIPS 2025 aesthetic standards where appropriate. 

## INPUT DATA
-   **Detailed Description**: [The preliminary description of the figure]
-   **Style Guidelines**: [NeurIPS 2025 Style Guidelines]
-   **Methodology Section**: [Contextual content from the methodology section]
-   **Diagram Caption**: [Target diagram caption]

Note that you should primary focus on the detailed description and style guidelines. The methodology section and diagram caption are provided for context only, there's no need to regenerate a description from scratch, solely based on them, while ignoring the detailed description we already have.

**Crucial Instructions:**
1.  **Preserve Semantic Content:** Do NOT alter the semantic content, logic, or structure of the diagram. Your job is purely aesthetic refinement, not content editing. However, if you find some phrases or descriptions too verbose, you may simplify them appropriately while referencing the original methodology section to ensure semantic accuracy.
2.  **Preserve High-Quality Aesthetics and Intervene Only When Necessary:** First, evaluate the aesthetic quality implied by the input description. If the description already describes a high-quality, professional, and visually appealing diagram (e.g., nice 3D icons, rich textures, good color harmony), **PRESERVE IT**. Only apply strict Style Guide adjustments if the current description lacks detail, looks outdated, or is visually cluttered. Your goal is specific refinement, not blind standardization.
3.  **Respect Diversity:** Different domains have different styles. If the input describes a specific style (e.g., illustrative for agents) that works well, keep it.
4.  **Enrich Details:** If the input is plain, enrich it with specific visual attributes (colors, fonts, line styles, layout adjustments) defined in the guidelines.
5.  **Handle Icons with Care:** Be cautious when modifying icons as they may carry specific semantic meanings. Some icons have conventional technical meanings (e.g., snowflake = frozen/non-trainable, flame = trainable) - when encountering such icons, reference the original methodology section to verify their intent before making changes. However, purely decorative or symbolic icons can be freely enhanced and beautified. For examples, agent papers often use cute 2D robot avatars to represent agents.

## OUTPUT
Output ONLY the final polished Detailed Description. Do not include any conversational text or explanations.
"""

PLOT_STYLIST_AGENT_SYSTEM_PROMPT = """
## ROLE
You are a Lead Visual Designer for top-tier AI conferences (e.g., NeurIPS 2025).

## TASK
You are provided with a preliminary description of a statistical plot to be generated. However, this description may lack specific aesthetic details, such as color palettes, and background styling and font choices.

Your task is to refine and enrich this description based on the provided [NeurIPS 2025 Style Guidelines] to ensure the final generated image is a high-quality, publication-ready plot that strictly adheres to the NeurIPS 2025 aesthetic standards.

**Crucial Instructions:**
1.  **Enrich Details:** Focus on specifying visual attributes (colors, fonts, line styles, layout adjustments) defined in the guidelines.
2.  **Preserve Content:** Do NOT alter the semantic content, logic, or quantitative results of the plot. Your job is purely aesthetic refinement, not content editing.
3.  **Context Awareness:** Use the provided "Raw Data" and "Visual Intent of the Desired Plot" to understand the emphasis of the plot, ensuring the style supports the content effectively.

## INPUT DATA
-   **Detailed Description**: [The preliminary description of the plot]
-   **Style Guidelines**: [NeurIPS 2025 Style Guidelines]
-   **Raw Data**: [The raw data to be visualized]
-   **Visual Intent of the Desired Plot**: [Visual intent of the desired plot]

## OUTPUT
Output ONLY the final polished Detailed Description. Do not include any conversational text or explanations.
"""

# --- VISUALIZER ---
DIAGRAM_VISUALIZER_AGENT_SYSTEM_PROMPT = """You are an expert scientific diagram illustrator. Generate high-quality scientific diagrams based on user requests."""
PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT = """You are an expert statistical plot illustrator. Write code to generate high-quality statistical plots based on user requests."""

# --- CRITIC ---
DIAGRAM_CRITIC_AGENT_SYSTEM_PROMPT = """
## ROLE
You are a Lead Visual Designer for top-tier AI conferences (e.g., NeurIPS 2025).

## TASK
Your task is to conduct a sanity check and provide a critique of the target diagram based on its content and presentation. You must ensure its alignment with the provided 'Methodology Section', 'Figure Caption'.

You are also provided with the 'Detailed Description' corresponding to the current diagram. If you identify areas for improvement in the diagram, you must list your specific critique and provide a revised version of the 'Detailed Description' that incorporates these corrections.

## CRITIQUE & REVISION RULES

1. Content
    -   **Fidelity & Alignment:** Ensure the diagram accurately reflects the method described in the "Methodology Section" and aligns with the "Figure Caption." Reasonable simplifications are allowed, but no critical components should be omitted or misrepresented. Also, the diagram should not contain any hallucinated content. Consistent with the provided methodology section & figure caption is always the most important thing.
    -   **Text QA:** Check for typographical errors, nonsensical text, or unclear labels within the diagram. Suggest specific corrections.
    -   **Validation of Examples:** Verify the accuracy of illustrative examples. If the diagram includes specific examples to aid understanding (e.g., molecular formulas, attention maps, mathematical expressions), ensure they are factually correct and logically consistent. If an example is incorrect, provide the correct version.
    -   **Caption Exclusion:** Ensure the figure caption text (e.g., "Figure 1: Overview...") is **not** included within the image visual itself. The caption should remain separate.

2. Presentation
    -   **Clarity & Readability:** Evaluate the overall visual clarity. If the flow is confusing or the layout is cluttered, suggest structural improvements.
    -   **Legend Management:** Be aware that the description&diagram may include a text-based legend explaining color coding. Since this is typically redundant, please excise such descriptions if found.

** IMPORTANT: **
Your Description should primarily be modifications based on the original description, rather than rewriting from scratch. If the original description has obvious problems in certain parts that require re-description, your description should be as detailed as possible. Semantically, clearly describe each element and their connections. Formally, include various details such as background, colors, line thickness, icon styles, etc. Remember: vague or unclear specifications will only make the generated figure worse, not better.

## INPUT DATA
-   **Target Diagram**: [The generated figure]
-   **Detailed Description**: [The detailed description of the figure]
-   **Methodology Section**: [Contextual content from the methodology section]
-   **Figure Caption**: [Target figure caption]

## OUTPUT
Provide your response strictly in the following JSON format.

```json
{
    "critic_suggestions": "Insert your detailed critique and specific suggestions for improvement here. If the diagram is perfect, write 'No changes needed.'",
    "revised_description": "Insert the fully revised detailed description here, incorporating all your suggestions. If no changes are needed, write 'No changes needed.'",
}
```
"""

PLOT_CRITIC_AGENT_SYSTEM_PROMPT = """
## ROLE
You are a Lead Visual Designer for top-tier AI conferences (e.g., NeurIPS 2025).

## TASK
Your task is to conduct a sanity check and provide a critique of the target plot based on its content and presentation. You must ensure its alignment with the provided 'Raw Data' and 'Visual Intent'.

You are also provided with the 'Detailed Description' corresponding to the current plot. If you identify areas for improvement in the plot, you must list your specific critique and provide a revised version of the 'Detailed Description' that incorporates these corrections.

## CRITIQUE & REVISION RULES

1. Content
    -   **Data Fidelity & Alignment:** Ensure the plot accurately represents all data points from the "Raw Data" and aligns with the "Visual Intent." All quantitative values must be correct. No data should be hallucinated, omitted, or misrepresented.
    -   **Text QA:** Check for typographical errors, nonsensical text, or unclear labels within the plot (axis labels, legend entries, annotations). Suggest specific corrections.
    -   **Validation of Values:** Verify the accuracy of all numerical values, axis scales, and data points. If any values are incorrect or inconsistent with the raw data, provide the correct values.
    -   **Caption Exclusion:** Ensure the figure caption text (e.g., "Figure 1: Performance comparison...") is **not** included within the image visual itself. The caption should remain separate.

2. Presentation
    -   **Clarity & Readability:** Evaluate the overall visual clarity. If the plot is confusing, cluttered, or hard to interpret, suggest structural improvements (e.g., better axis labeling, clearer legend, appropriate plot type).
    -   **Overlap & Layout:** Check for any overlapping elements that reduce readability, such as text labels being obscured by heavy hatching, grid lines, or other chart elements (e.g., pie chart labels inside dark slices). If overlaps exist, suggest adjusting element positions (e.g., moving labels outside the chart, using leader lines, or adjusting transparency).
    -   **Legend Management:** Be aware that the description&plot may include a text-based legend explaining symbols or colors. Since this is typically redundant in well-designed plots, please excise such descriptions if found.

3. Handling Generation Failures
    -   **Invalid Plot:** If the target plot is missing or replaced by a system notice (e.g., "[SYSTEM NOTICE]"), it means the previous description generated invalid code.
    -   **Action:** You must carefully analyze the "Detailed Description" for potential logical errors, complex syntax, or missing data references.
    -   **Revision:** Provide a simplified and robust version of the description to ensure it can be correctly rendered. Do not just repeat the same description.

## INPUT DATA
-   **Target Plot**: [The generated plot]
-   **Detailed Description**: [The detailed description of the plot]
-   **Raw Data**: [The raw data to be visualized]
-   **Visual Intent**: [Visual intent of the desired plot]

## OUTPUT
Provide your response strictly in the following JSON format.

```json
{
    "critic_suggestions": "Insert your detailed critique and specific suggestions for improvement here. If the plot is perfect, write 'No changes needed.'",
    "revised_description": "Insert the fully revised detailed description here, incorporating all your suggestions. If no changes are needed, write 'No changes needed.'",
}
```
"""


# ==========================================
# WORKERS AND UTILS
# ==========================================


# pyplot keeps one set of figures per process, and up to five figures are
# drawn at once (plotting_agent's thread pool). Running the generated code one
# at a time keeps one figure's close("all") from wiping another's canvas.
_PLOT_EXEC_LOCK = threading.Lock()

# Files the generated code saves are removed afterwards; a file that was
# already there is put back if it is no bigger than this.
_PLOT_RESTORE_LIMIT = 64 * 1024 * 1024


def _capture_jpeg(fig, real_savefig):
    buf = io.BytesIO()
    real_savefig(fig, buf, format="jpeg", bbox_inches="tight", dpi=300)
    return buf.getvalue()


def _no_input(*args, **kwargs):
    raise EOFError("input() is not available while a plot is drawn")


def _plot_builtins():
    """The builtins the generated code sees: the real ones, except that
    input() fails at once instead of waiting for someone to type."""
    module = types.ModuleType("builtins")
    module.__dict__.update(vars(builtins))
    module.input = _no_input
    return module


def _figure_is_blank(fig) -> bool:
    return not (
        fig.axes
        or fig.artists
        or fig.lines
        or fig.patches
        or fig.texts
        or fig.images
        or fig.legends
        or getattr(fig, "subfigs", None)
    )


def _figure_has_data(fig) -> bool:
    """Plotted data (lines, bars, scatter, images, tables), not only text or a
    legend: tells a chart from a helper figure such as a shared legend."""
    if fig.images:
        return True
    return any(ax.lines or ax.patches or ax.collections or ax.images or ax.tables for ax in fig.axes)


def _open_figures():
    return [m.canvas.figure for m in _pylab_helpers.Gcf.get_all_fig_managers()]


def _saved_paths(fname, kwargs):
    """The file(s) matplotlib may write for savefig(fname, **kwargs)."""
    path = os.fsdecode(os.fspath(fname))
    paths = [path]
    if kwargs.get("format") is None and not os.path.splitext(path)[1][1:]:
        paths.append(path.rstrip(".") + "." + str(plt.rcParams["savefig.format"]))
    return [os.path.abspath(p) for p in paths]


def _is_main_guard(node) -> bool:
    """`if __name__ == "__main__":` in any spelling (reversed, `in (...)`,
    combined with `and`)."""
    if not isinstance(node, ast.If):
        return False
    for part in ast.walk(node.test):
        if not isinstance(part, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.In)) for op in part.ops):
            continue
        operands = [part.left, *part.comparators]
        has_name = any(isinstance(o, ast.Name) and o.id == "__name__" for o in operands)
        has_main = any(
            isinstance(c, ast.Constant) and c.value == "__main__"
            for o in operands
            for c in ast.walk(o)
        )
        if has_name and has_main:
            return True
    return False


_GUARD_TEMPLATE = (
    "__plot_guard__.enter()\n"
    "try:\n"
    "    pass\n"
    "except (Exception, SystemExit) as __plot_guard_error__:\n"
    "    __plot_guard__.failed(__plot_guard_error__)\n"
    "finally:\n"
    "    __plot_guard__.leave()\n"
)


def _wrap_main_guards(statements):
    for node in statements:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody"):
            inner = getattr(node, field, None)
            if isinstance(inner, list):
                _wrap_main_guards(inner)
        for clause in (getattr(node, "handlers", None) or []) + (getattr(node, "cases", None) or []):
            _wrap_main_guards(clause.body)
        if _is_main_guard(node):
            wrapper = ast.parse(_GUARD_TEMPLATE).body
            for stmt in wrapper:
                for part in ast.walk(stmt):
                    if hasattr(part, "lineno"):
                        part.lineno = part.end_lineno = node.lineno
                        part.col_offset = part.end_col_offset = 0
            wrapper[1].body = node.body
            node.body = wrapper


def _compile_plot_code(code: str):
    """Compile the code so that a failing `if __name__ == "__main__":` block
    (outside functions and classes) is reported to __plot_guard__ instead of
    failing the whole plot."""
    tree = ast.parse(code, "<plot>", "exec")
    _wrap_main_guards(tree.body)
    return compile(tree, "<plot>", "exec")


def execute_plot_code_worker(code_text: str) -> str:
    """
    Independent plot code execution worker:
    1. Extract code
    2. Execute plotting
    3. Return JPEG as Base64 string

    At the end the current figure is captured, as before, if it has anything
    on it. Generated code often ends with plt.savefig("name.png") or
    plt.show() and then plt.close() or plt.clf(), which used to leave nothing
    (or a blank canvas) to capture. So when a drawn figure is closed or
    cleared, an in-memory copy is kept if it could be the result, and the best
    copy is used: plotted data beats a figure with only text or a legend, then
    a figure saved to a file beats one shown or saved to a buffer, which beats
    one only closed; then the larger, then the later one. Files the code saves
    really are written (so code that reads them back works) and are removed
    afterwards, so nothing piles up in the backend's working folder; a file
    that already existed gets its old content back.

    The code runs once, as __main__, with a clean sys.argv; sys.exit() with
    status 0 counts as finishing normally. Nothing waits for a person:
    show() and pause() return at once, ginput() and waitforbuttonpress() get
    no clicks, and input() fails. Such `if __name__ == "__main__":`
    blocks used to be skipped, so if one fails, the result is what it would
    have been without it: the figures open then are closed, copies made in it
    are dropped, and the figure that was current when it started is used.
    """
    match = re.search(r"```python(.*?)```", code_text, re.DOTALL)
    code_clean = match.group(1).strip() if match else code_text.strip()

    with _PLOT_EXEC_LOCK:
        plt.switch_backend("Agg")
        plt.close("all")
        plt.rcdefaults()

        real_show, real_close, real_pause = plt.show, plt.close, plt.pause
        real_event_loop = FigureCanvasBase.start_event_loop
        real_fig_savefig = Figure.savefig
        real_clear_attrs = {name: Figure.__dict__.get(name) for name in ("clear", "clf")}
        real_clear = {name: getattr(Figure, name) for name in ("clear", "clf")}
        pylab_modules = [sys.modules.get(n) for n in ("matplotlib.pylab", "pylab")]
        real_argv = sys.argv
        touched = {}  # absolute path -> its old bytes, None if it was absent, ... to leave as is
        snaps = {}  # (figure token, generation) -> (has data, level, area, order, JPEG bytes)
        levels = {}  # figure token -> 2 saved to a file, 1 shown or saved to a buffer
        order = itertools.count()
        guards = []  # one entry per __main__ block being run
        fallback = []  # what was current when a failed __main__ block started
        clearing = []

        def _token(fig):
            # Stored on the figure, so closed figures can still be freed.
            return fig.__dict__.setdefault("_plot_worker_token", object())

        def _key(fig):
            return (_token(fig), fig.__dict__.get("_plot_worker_generation", 0))

        def _rank(fig):
            w, h = fig.get_size_inches()
            return (_figure_has_data(fig), levels.get(_token(fig), 0), w * h)

        def _consider(fig):
            """Keep a copy of a drawn figure if it could be the result. True
            if it is settled: copied, or it can never win."""
            if _figure_is_blank(fig):
                return False
            rank = _rank(fig)
            if any(snap[:3] > rank for snap in snaps.values()):
                return True  # can never win
            try:
                jpeg = _capture_jpeg(fig, real_fig_savefig)
            except Exception:
                return False
            key = _key(fig)
            kept = set().union(*(g["keys"] for g in guards))  # a failed block falls back to these
            for other in [k for k in snaps if k != key and k not in kept]:
                del snaps[other]
            snaps[key] = rank + (next(order), jpeg)
            fallback.clear()  # drawn after a failed __main__ block: that counts now
            return True

        def _remember(path):
            if path in touched:
                return
            try:
                if os.path.exists(path) and any(
                    os.path.exists(seen) and os.path.samefile(path, seen) for seen in touched
                ):
                    touched[path] = ...  # the same file under another name: handled there
                elif os.path.isfile(path) and os.path.getsize(path) <= _PLOT_RESTORE_LIMIT:
                    with open(path, "rb") as fh:
                        touched[path] = fh.read()
                elif os.path.lexists(path):
                    touched[path] = ...  # too big, not a file, or a link: leave it alone
                else:
                    touched[path] = None
            except OSError:
                touched[path] = ...

        def _fig_savefig(self, *args, **kwargs):
            fname = args[0] if args else kwargs.get("fname")
            to_file = isinstance(fname, (str, bytes, os.PathLike))
            if to_file:
                for path in _saved_paths(fname, kwargs):
                    _remember(path)
                try:
                    real_fig_savefig(self, *args, **kwargs)
                except Exception:
                    pass  # e.g. a figures/ folder that doesn't exist: keep drawing
            else:
                real_fig_savefig(self, *args, **kwargs)
            if not _figure_is_blank(self):
                token = _token(self)
                levels[token] = max(levels.get(token, 0), 2 if to_file else 1)
                if all(f is not self for f in _open_figures()):
                    _consider(self)  # already closed, or never managed by pyplot
            return None

        def _make_clear(name):
            real = real_clear[name]

            def _clear(self, *args, **kwargs):
                if clearing:  # clf() calls clear() (or the other way round)
                    return real(self, *args, **kwargs)
                clearing.append(True)
                try:
                    _consider(self)
                    levels.pop(_token(self), None)
                    self.__dict__["_plot_worker_generation"] = _key(self)[1] + 1
                    return real(self, *args, **kwargs)
                finally:
                    clearing.pop()

            return _clear

        def _show(*args, **kwargs):
            for fig in _open_figures():
                if not _figure_is_blank(fig):
                    token = _token(fig)
                    levels[token] = max(levels.get(token, 0), 1)

        def _close(*args, **kwargs):
            before = _open_figures()
            result = real_close(*args, **kwargs)
            still_open = {id(f) for f in _open_figures()}
            gone = [(_rank(f), i, f) for i, f in enumerate(before) if id(f) not in still_open]
            # Only the best of them can be the result (on a tie, the one
            # pyplot activated last), so stop at the first one that settles.
            for _, _, fig in sorted(gone, key=lambda item: item[:2], reverse=True):
                if _consider(fig):
                    break
            return result

        def _pause(*args, **kwargs):
            return real_pause(0)  # draw and mark as shown, but don't wait

        def _no_event_loop(self, timeout=0):
            return None  # ginput(), waitforbuttonpress(): nobody will click

        class _Guard:
            """Told when the code's __main__ block starts, fails and ends."""

            def enter(self):
                current = None
                if plt.get_fignums() and not _figure_is_blank(plt.gcf()):
                    try:
                        current = _capture_jpeg(plt.gcf(), real_fig_savefig)
                    except Exception:
                        current = None
                guards.append({"keys": set(snaps), "current": current})

            def failed(self, error):
                if isinstance(error, SystemExit) and error.code in (None, 0):
                    raise error
                print(f"Plot code failed in its __main__ block ({error!r}); using the figure drawn before it.")
                state = guards[-1]
                real_close("all")
                for key in [k for k in snaps if k not in state["keys"]]:
                    del snaps[key]
                fallback[:] = [state["current"]] if state["current"] is not None else []

            def leave(self):
                guards.pop()

        def _patch(on):
            funcs = {"show": (_show, real_show), "close": (_close, real_close), "pause": (_pause, real_pause)}
            for name, (patched, real) in funcs.items():
                setattr(plt, name, patched if on else real)
            for mod in pylab_modules + [sys.modules.get(n) for n in ("matplotlib.pylab", "pylab")]:
                if mod is None:
                    continue
                for name, (patched, real) in funcs.items():
                    if on or getattr(mod, name, None) is patched:
                        setattr(mod, name, patched if on else real)
            FigureCanvasBase.start_event_loop = _no_event_loop if on else real_event_loop
            Figure.savefig = _fig_savefig if on else real_fig_savefig
            for name, original in real_clear_attrs.items():
                if on:
                    setattr(Figure, name, _make_clear(name))
                elif original is not None:
                    setattr(Figure, name, original)
                elif name in Figure.__dict__:
                    delattr(Figure, name)

        def _restore_files():
            for path, before in touched.items():
                try:
                    if before is None:
                        if os.path.isfile(path):
                            os.remove(path)
                    elif before is not ...:
                        same = False
                        if os.path.isfile(path):
                            with open(path, "rb") as fh:
                                same = fh.read() == before
                        if not same:
                            with open(path, "wb") as fh:
                                fh.write(before)
                except OSError:
                    pass

        try:
            try:
                _patch(True)
                sys.argv = ["plot.py"]
                exec(
                    _compile_plot_code(code_clean),
                    {"__name__": "__main__", "__builtins__": _plot_builtins(), "__plot_guard__": _Guard()},
                )
            except SystemExit as stop:
                if stop.code not in (None, 0):
                    raise RuntimeError(f"the plot code exited with status {stop.code!r}")
            finally:
                try:
                    _patch(False)
                finally:
                    sys.argv = real_argv
                    _restore_files()

            fig = plt.gcf() if plt.get_fignums() else None
            if fig is not None and not _figure_is_blank(fig):
                img_bytes = _capture_jpeg(fig, real_fig_savefig)
            elif fallback:
                img_bytes = fallback[0]
            else:
                for other in _open_figures():
                    _consider(other)  # drawn figures that are open but not current
                if snaps:
                    img_bytes = max(snaps.values(), key=lambda snap: snap[:4])[4]
                elif fig is not None:
                    img_bytes = _capture_jpeg(fig, real_fig_savefig)  # blank, as before
                else:
                    img_bytes = None
            plt.close("all")
            if img_bytes is None:
                return None
            return base64.b64encode(img_bytes).decode("utf-8")

        except Exception as e:
            plt.close("all")
            print(f"Error executing plot code: {e}")
            return None


def identity_parse(response: str):
    return response


# ==========================================
# FULL PIPELINE AGENT STEPS
# ==========================================


def retrieve_few_shot_examples(
    task_name: str,
    raw_content: str,
    description: str,
    model_name: str = "gemini-3.1-pro-preview",
) -> List[Dict]:
    """
    Retrieves Top-10 relevant examples from PaperBanana reference dataset.
    """
    if task_name == "plot":
        system_prompt = PLOT_RETRIEVER_AGENT_SYSTEM_PROMPT
        target_labels = ["Visual Intent", "Raw Data"]
        candidate_labels = ["Plot ID", "Visual Intent", "Raw Data"]
        candidate_type = "Plot"
        output_key = "top10_plots"
        limit = None
    else:
        system_prompt = DIAGRAM_RETRIEVER_AGENT_SYSTEM_PROMPT
        target_labels = ["Caption", "Methodology section"]
        candidate_labels = ["Diagram ID", "Caption", "Methodology section"]
        candidate_type = "Diagram"
        output_key = "top10_diagrams"
        limit = 200

    ref_file = os.path.join(PB_DIR, f"data/PaperBananaBench/{task_name}/ref.json")
    if not os.path.exists(ref_file):
        _notice_missing_once(
            f"ref:{task_name}",
            f"Note: PaperBanana reference figures for '{task_name}' are not installed "
            f"({os.path.normpath(ref_file)}); figures are planned without few-shot examples. "
            "参考图库未安装，绘图不使用范例（原版即如此，可忽略；每次运行只提示一次）。",
        )
        return []

    with open(ref_file, "r", encoding="utf-8") as f:
        candidate_pool = json.load(f)
        if limit:
            candidate_pool = candidate_pool[:limit]

    user_prompt = f"**Target Input**\n- {target_labels[0]}: {description}\n- {target_labels[1]}: {raw_content}\n\n**Candidate Pool**\n"

    for idx, item in enumerate(candidate_pool):
        user_prompt += f"Candidate {candidate_type} {idx+1}:\n"
        user_prompt += f"- {candidate_labels[0]}: {item['id']}\n"
        user_prompt += f"- {candidate_labels[1]}: {item.get('visual_intent', item.get('caption', ''))}\n"
        user_prompt += f"- {candidate_labels[2]}: {str(item.get('content', ''))}\n\n"

    user_prompt += "Now, based on the Target Input and the Candidate Pool, select the Top 10 most relevant examples."

    target_model = resolve_task_model(model_name, "plot_retrieval")
    response_dict = call_llm_with_text_prompt(
        prompt=user_prompt,
        model_name=target_model,
        generation_configs={"system_instruction": system_prompt},
        result_parsing_func=identity_parse,
    )

    raw_response = response_dict["parsed_response"].strip()
    try:
        parsed = json_repair.loads(raw_response)
        retrieved_ids = parsed.get(output_key, [])
    except Exception as e:
        print(f"Warning: Failed to parse retrieval result: {e}")
        retrieved_ids = []

    id_to_item = {item["id"]: item for item in candidate_pool}
    examples = [id_to_item[ref_id] for ref_id in retrieved_ids if ref_id in id_to_item]
    return examples


def generate_figure_caption(
    task_name: str,
    raw_content: str,
    description: str,
    figure_desc: str,
    base64_image: str = None,
    model_name: str = "gemini-3.1-pro-preview",
) -> str:
    """
    Generates a caption for the figure based on its final description and context.
    Requirements:
    - The caption should be concise and informative, and can be directly used as a caption for academic papers.
    - The caption should NOT contain "Figure X:" prefix, as the latex template will add it automatically.
    - The caption should NOT contain any markdown formatting, it should be a plain text.
    """
    prompt = f"""
    ## INPUT DATA
    -   Task Type: {task_name}
    -   Contextual Section: {raw_content}
    -   Overall Figure Intent: {description}
    -   Detailed Figure Description: {figure_desc}
    
    Please provide the final caption for this figure based on the system instructions.
    Requirements:
    - The caption should be concise and informative, and can be directly used as a caption for academic papers.
    - The caption MUST NOT contain a "Figure X:" or "Caption X:" prefix, as the latex template will add it automatically.
    - The caption MUST NOT contain any markdown formatting (like bold, italics, etc), it should be plain text.
    
    Respond with the plain text caption only.
    """

    generation_configs = {
        "temperature": 0.5,
    }

    caption_model = resolve_task_model(model_name, "plot_caption")
    if base64_image and len(base64_image) > 100 and supports_vision(caption_model):
        parts = [
            {"type": "text", "text": prompt},
            {
                "type": "image_bytes",
                "data": base64.b64decode(base64_image),
                "mime_type": "image/jpeg",
            },
        ]
        response_dict = call_llm_with_multimodal_parts(
            parts=parts,
            model_name=caption_model,
            result_parsing_func=identity_parse,
            generation_configs=generation_configs,
            check_parsed_response_not_none=True,
        )
    else:
        response_dict = call_llm_with_text_prompt(
            prompt=prompt,
            model_name=caption_model,
            result_parsing_func=identity_parse,
            generation_configs=generation_configs,
            check_parsed_response_not_none=True,
        )

    res = response_dict.get("parsed_response", "")
    if res.startswith("Caption:"):
        res = res.replace("Caption:", "").strip()
    elif res.startswith("**Caption:**"):
        res = res.replace("**Caption:**", "").strip()

    return res


def predict_figure_content(
    task_name: str,
    raw_content: str,
    description: str,
    examples: List[Dict],
    model_name: str = "gemini-3.1-pro-preview",
) -> str:
    """
    Predicts the detailed figure description using LLM and few-shot examples.
    """
    if task_name == "plot":
        system_prompt = PLOT_PLANNER_AGENT_SYSTEM_PROMPT
        content_label = "Plot Raw Data"
        visual_intent_label = "Visual Intent of the Desired Plot"
    else:
        system_prompt = DIAGRAM_PLANNER_AGENT_SYSTEM_PROMPT
        content_label = "Methodology Section"
        visual_intent_label = "Diagram Caption"

    user_prompt = ""
    parts: List[Dict[str, Any]] = []

    for idx, item in enumerate(examples):
        user_prompt += f"Example {idx+1}:\n"

        item_content = item["content"]
        if isinstance(item_content, (dict, list)):
            item_content = json.dumps(item_content)

        user_prompt += f"{content_label}: {item_content}\n"
        user_prompt += f"{visual_intent_label}: {item.get('visual_intent', item.get('caption', ''))}\nReference {task_name.capitalize()}: "
        parts.append({"type": "text", "text": user_prompt})

        # Resolve related image base64
        image_path = os.path.join(
            PB_DIR, f"data/PaperBananaBench/{task_name}", item["path_to_gt_image"]
        )
        if os.path.exists(image_path):
            parts.append({"type": "image_path", "path": image_path})

        user_prompt = ""

    user_prompt += f"Now, based on the following {content_label.lower()} and {visual_intent_label.lower()}, provide a detailed description for the figure to be generated.\n"
    user_prompt += (
        f"{content_label}: {raw_content}\n{visual_intent_label}: {description}\n"
    )
    user_prompt += "Detailed description of the target figure to be generated"
    if task_name == "diagram":
        user_prompt += " (do not include figure titles)"
    user_prompt += ":"

    parts.append({"type": "text", "text": user_prompt})

    plan_model = resolve_task_model(model_name, "plot_plan")
    response_dict = call_llm_with_multimodal_parts(
        parts=parts,
        model_name=plan_model,
        system_prompt=system_prompt,
        result_parsing_func=identity_parse,
    )
    return response_dict["parsed_response"].strip()


def style_figure_content(
    task_name: str,
    raw_content: str,
    description: str,
    figure_desc: str,
    model_name: str = "gemini-3.1-pro-preview",
    extra_style_guide: str = "",
) -> str:
    """
    Refines the basic figure description into a professionally styled description.
    """
    if task_name == "plot":
        system_prompt = PLOT_STYLIST_AGENT_SYSTEM_PROMPT
        context_labels = ["Raw Data", "Visual Intent of the Desired Plot"]
    else:
        system_prompt = DIAGRAM_STYLIST_AGENT_SYSTEM_PROMPT
        context_labels = ["Methodology Section", "Diagram Caption"]

    style_guide_path = os.path.join(
        PB_DIR, f"style_guides/neurips2025_{task_name}_style_guide.md"
    )
    style_guide = ""
    if os.path.exists(style_guide_path):
        with open(style_guide_path, "r", encoding="utf-8") as f:
            style_guide = f.read()
    elif not extra_style_guide:
        _notice_missing_once(
            f"style:{task_name}",
            f"Note: PaperBanana style guide for '{task_name}' is not installed "
            f"({os.path.normpath(style_guide_path)}); styling without it. "
            "配图风格指南未安装，套风格时不用指南（原版即如此，可忽略；每次运行只提示一次）。",
        )
    if extra_style_guide:
        style_guide = (
            f"{style_guide}\n\n{extra_style_guide}".strip()
            if style_guide
            else extra_style_guide.strip()
        )

    user_prompt = (
        f"Detailed Description: {figure_desc}\nStyle Guidelines: {style_guide}\n"
    )
    user_prompt += f"{context_labels[0]}: {raw_content}\n"
    user_prompt += f"{context_labels[1]}: {description}\nYour Output:"

    target_model = resolve_task_model(model_name, "plot_style")
    response_dict = call_llm_with_text_prompt(
        prompt=user_prompt,
        model_name=target_model,
        generation_configs={"system_instruction": system_prompt},
        result_parsing_func=identity_parse,
    )
    return response_dict["parsed_response"].strip()


def critique_and_revise_figure(
    task_name: str,
    raw_content: str,
    description: str,
    figure_desc: str,
    base64_image: str,
    round_idx: int,
    model_name: str = "gemini-3.1-pro-preview",
) -> Tuple[str, str]:
    """
    Critiques the generated figure and provides revised descriptions.
    """
    if task_name == "plot":
        system_prompt = PLOT_CRITIC_AGENT_SYSTEM_PROMPT
        critique_target = "Target Plot for Critique:"
        context_labels = ["Raw Data", "Visual Intent"]
    else:
        system_prompt = DIAGRAM_CRITIC_AGENT_SYSTEM_PROMPT
        critique_target = "Target Diagram for Critique:"
        context_labels = ["Methodology Section", "Figure Caption"]

    critic_model = resolve_task_model(model_name, "plot_critic")
    parts: List[Dict[str, Any]] = [
        {"type": "text", "text": critique_target},
    ]

    if base64_image and len(base64_image) > 100 and supports_vision(critic_model):
        parts.append(
            {
                "type": "image_bytes",
                "data": base64.b64decode(base64_image),
                "mime_type": "image/jpeg",
            }
        )
    elif base64_image and len(base64_image) > 100:
        parts.append(
            {
                "type": "text",
                "text": "\n[SYSTEM NOTICE] Plot rendered successfully (image attachment omitted for text-only critic model). Check description for precision and layout clarity.",
            }
        )
    else:
        parts.append(
            {
                "type": "text",
                "text": "\n[SYSTEM NOTICE] The plot image could not be generated based on the current description (likely due to invalid code). Please check the description for errors (e.g., syntax issues, missing data) and provide a revised version.",
            }
        )

    user_prompt = f"\nDetailed Description: {figure_desc}\n{context_labels[0]}: {raw_content}\n{context_labels[1]}: {description}\nYour Output:"
    parts.append({"type": "text", "text": user_prompt})

    response_dict = call_llm_with_multimodal_parts(
        parts=parts,
        model_name=critic_model,
        system_prompt=system_prompt,
        result_parsing_func=identity_parse,
    )

    cleaned_response = (
        response_dict["parsed_response"]
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )
    try:
        eval_result = json_repair.loads(cleaned_response)
        if not isinstance(eval_result, dict):
            eval_result = {}
    except Exception as e:
        eval_result = {}

    critic_suggestions = eval_result.get("critic_suggestions", "No changes needed.")
    revised_description = eval_result.get("revised_description", "No changes needed.")

    if revised_description.strip() == "No changes needed.":
        revised_description = figure_desc

    return revised_description, critic_suggestions


def generate_figure_visuals(
    task_name: str,
    figure_description: str,
    model_name: str = "gemini-3.1-pro-preview",
    image_model_name: str = "gemini-3-pro-image",
    aspect_ratio: str = "16:9",
) -> str:
    """
    Generates the figure based on the task type.
    """
    code_model = resolve_task_model(model_name, "plot_code")
    image_model_name = resolve_image_model(image_model_name)
    if task_name == "plot":
        prompt_text = f"Use python matplotlib to generate a statistical plot based strictly on the verified numbers in the description (DO NOT invent extra points or error bars): {figure_description}\n Only provide the code without any explanations. Code:"
        response_dict = call_llm_with_text_prompt(
            prompt=prompt_text,
            model_name=code_model,
            generation_configs={"system_instruction": PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT},
            result_parsing_func=identity_parse,
        )
        code_text = response_dict["parsed_response"]
        base64_img = execute_plot_code_worker(code_text)
        return base64_img
    else:
        # Prompt for diagram image generation
        if can_generate_images(image_model_name):
            prompt_text = f"Render an image based on the following detailed description: {figure_description}\n Note that do not include figure titles in the image. Diagram: "
            img_b64 = generate_image(
                image_model_name=image_model_name,
                prompt=prompt_text,
                aspect_ratio=aspect_ratio,
                system_instruction=DIAGRAM_VISUALIZER_AGENT_SYSTEM_PROMPT,
            )
            if img_b64:
                return img_b64
            print(" >> [Plotting] Image model returned nothing; falling back to Matplotlib.")

        print(
            " >> [Plotting] Image generation unavailable for this model/provider; generating clean Matplotlib schematic diagram via Python code."
        )
        fallback_prompt = (
            f"Write clean Python matplotlib (patches / FancyBboxPatch / FancyArrowPatch) code to draw a publication-grade schematic architecture diagram matching this description (do NOT put a 'Figure X:' title in the image, and do NOT invent numerical metrics):\n{figure_description}\n\nReturn ONLY ```python ... ``` code."
        )
        response_dict = call_llm_with_text_prompt(
            prompt=fallback_prompt,
            model_name=resolve_task_model(model_name, "diagram_code"),
            generation_configs={"system_instruction": PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT},
            result_parsing_func=identity_parse,
        )
        return execute_plot_code_worker(response_dict["parsed_response"])
