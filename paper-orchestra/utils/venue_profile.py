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

"""What kind of venue a LaTeX template is for.

The agents' prompts were written for machine-learning conferences (NeurIPS,
ICLR, CVPR: double-blind, a separate Related Work section, page limits). A
journal template (e.g. templates/journal-acs-jctc-jpc-jcim) carries a
venue.json next to its guidelines.md; venue_note() turns it into a short
block appended to an agent's prompt so the paper is planned, written and
reviewed in that journal's terms. Templates without venue.json (cvpr2025,
iclr2025) get an empty note, so their prompts stay exactly as they were.
"""

import json
import os
from typing import Any, Dict, Optional

VENUE_FILENAME = "venue.json"

# Keys every journal venue.json has (see the journal templates).
REQUIRED_KEYS = (
    "kind",
    "venue_name",
    "short_name",
    "field",
    "review_model",
    "separate_related_work_section",
    "section_order",
    "abstract_limit",
    "writing_style",
    "reviewer_focus",
    "figure_style",
    "extras",
)

ROLES = ("outline", "literature", "writing", "refinement", "review", "meta_review")


def load_venue_profile(path: Optional[str]) -> Optional[Dict[str, Any]]:
    """The venue.json in the folder of `path` (a guidelines.md or
    template.tex path, or the folder itself), or None when there is none, it
    cannot be read, or it is not a journal profile."""
    if not path:
        return None
    folder = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    try:
        with open(os.path.join(folder, VENUE_FILENAME), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("kind") != "journal":
        return None
    return data


def _text(profile: Dict[str, Any], key: str, default: str = "") -> str:
    value = profile.get(key, default)
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip() if value is not None else default


def venue_note(profile: Optional[Dict[str, Any]], role: str) -> str:
    """A block to append to the prompt of the agent playing `role` (one of
    ROLES). Empty when there is no journal profile."""
    if not profile:
        return ""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")

    venue = _text(profile, "venue_name") or "the target journal"
    short = _text(profile, "short_name")
    label = f"{venue} ({short})" if short and short not in venue else venue
    field = _text(profile, "field") or "its field"
    sections = _text(profile, "section_order")
    abstract = _text(profile, "abstract_limit")
    style = _text(profile, "writing_style")
    focus = _text(profile, "reviewer_focus")
    figures = _text(profile, "figure_style")
    extras = _text(profile, "extras")
    review_model = _text(profile, "review_model") or "single-blind"

    if role in ("review", "meta_review"):
        who = "an expert referee" if role == "review" else "the handling editor"
        lines = [
            "",
            "### Target venue",
            f"You act as {who} for {label}, a peer-reviewed journal in {field}, "
            "not a machine-learning conference. Read every mention of 'the conference' "
            "in the instructions and rubric as this journal, and judge by its standards.",
        ]
        if focus:
            lines.append(f"What referees at this journal weigh: {focus}")
        lines.append(
            "- Audit claim-to-evidence alignment (flag overclaiming or unsupported causal leaps), "
            "statistical reporting completeness (sample size n, exact tests, error bar definitions, "
            "effect sizes/confidence intervals), and consistency between figure panels, legends and text."
        )
        lines.append(
            "Author names in the manuscript are placeholders by design; do not penalise "
            "them, and do not ask for anonymisation."
        )
        return "\n".join(lines) + "\n"

    lines = [
        "",
        "### Target venue (this overrides conference-specific wording above)",
        f"This paper is for {label}, a peer-reviewed journal in {field}, not a "
        "machine-learning conference. Where the instructions above mention NeurIPS, ICML, "
        "CVPR, ICLR or ML conferences, double-blind anonymity, a separate Related Work "
        "section, 'experiments', baselines or page limits, apply them in this journal's terms:",
        f"- Review model: {review_model}. Keep the author placeholder block of template.tex "
        "exactly as it is; never invent author names, affiliations, funding, grant numbers, "
        "dates or data.",
    ]
    if sections:
        lines.append(
            f"- Structure: the \\section commands of template.tex, in this order: {sections}. "
            "There is no separate Related Work section: literature context and the state of "
            "the art belong in the Introduction (and in Results and Discussion when comparing "
            "with earlier computed or measured values). Do not add sections template.tex does "
            "not have."
        )
    if abstract:
        lines.append(f"- Abstract: {abstract}.")
    lines.append(
        "- Length: follow the target length and word limits in conference_guidelines.md "
        "instead of a conference page limit."
    )
    if style:
        lines.append(f"- Style: {style}")
    if extras:
        lines.append(
            f"- Journal-specific parts: {extras} (how to fill them is in conference_guidelines.md)."
        )

    if role == "outline":
        lines.append(
            "- Keep the JSON keys exactly as specified (intro_related_work_plan, "
            "related_work_strategy, ...): they drive the literature search. Plan the "
            "related-work material as background paragraphs of the Introduction. Treat the "
            "calculations and analyses in experimental_log.md as the study's 'experiments'."
        )
        lines.append(
            "- Claim-driven architecture: ensure every Results subsection and every planned "
            "figure owns one explicit scientific claim backed by concrete evidence in the logs; "
            "assign distinct panel roles (a, b, c) when planning multi-panel figures."
        )
        if figures:
            lines.append(f"- Figures (write the plotting_plan to match): {figures}")
    elif role == "literature":
        lines.append(
            "- template.tex has no Related Work section: write the related-work discussion "
            "as part of the Introduction, add no new \\section, and leave every other section "
            "untouched. The citation requirements above still apply."
        )
    elif role == "writing":
        lines.append(
            "- Methods and computational details must be reproducible and come only from the "
            "provided materials (software and version, level of theory or force field, basis "
            "set, dispersion and solvation treatment, convergence criteria, sampling length, "
            "reference data). Leave out what the materials do not state; do not guess it."
        )
        lines.append(
            "- Use \\ce{...} for chemical formulas and reactions and \\SI/\\qty/\\si for "
            "quantities, as the template provides; keep units consistent."
        )
        lines.append(
            "- Prose discipline (anti-AI & anti-defensive style): zero em-dashes (---) and "
            "zero semicolons in the abstract, and at most one em-dash or semicolon per body "
            "paragraph (never paired em-dashes); avoid AI clichés ('delve', 'tapestry', "
            "'realm', 'holistic', 'underscore', 'pivotal', 'seamless', 'harness', 'intricate', "
            "'it is worth noting', 'Taken together,'). Lead paragraphs with findings rather "
            "than defensive caveats, and hedge once rather than stacking hedges."
        )
        lines.append(
            "- Statistical reporting: whenever quantitative comparisons or inferential tests "
            "appear in the provided materials, report sample size n, exact test or metric, "
            "error bar definition (s.d., s.e.m., CI) and exact p/effect size; never invent numbers."
        )
        lines.append(
            "- Back-matter statements (data or code availability, conflicts of interest, "
            "author contributions, acknowledgements, supporting information) may only state "
            "what the materials say; otherwise write 'To be completed by the authors.'"
        )
        if figures:
            lines.append(f"- Figures: {figures}")
    elif role == "refinement":
        if focus:
            lines.append(f"- Revise as a referee of this journal would judge it: {focus}")
        lines.append(
            "- Polish prose for publication: remove em-dash/semicolon tics (none in abstract, "
            "<= 1 per paragraph), trim redundant hedges and empty throat-clearing, verify "
            "every figure/panel reference matches its caption, and keep all numbers and "
            "back-matter statements strictly faithful to the provided materials."
        )
        lines.append(
            "- 'Preserve anonymity' here means: keep the author placeholders unchanged. Keep "
            "the journal-specific parts (TOC graphic or graphical abstract, highlights, "
            "statements) present and compilable."
        )
    return "\n".join(lines) + "\n"
