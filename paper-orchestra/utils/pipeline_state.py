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

"""Stage graph state (pipeline_state.json), Loop resume checkpoints, and preference injection."""

from datetime import datetime, timezone
import json
import os
import os.path as osp
import threading
from typing import Any, Dict, List, Optional, Tuple


_STATE_LOCK = threading.Lock()
STATE_FILENAME = "pipeline_state.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_nodes(use_plotting: bool, models: Dict[str, str]) -> List[Dict[str, Any]]:
    writer = models.get("writer", "auto")
    plotting = models.get("plotting", "auto")
    reflection = models.get("reflection", "auto")
    return [
        {
            "id": "outline",
            "label": "大纲规划",
            "depends_on": [],
            "status": "pending",
            "model": writer,
            "started_at": None,
            "finished_at": None,
            "artifact": "outline.json",
            "error": None,
            "note": None,
        },
        {
            "id": "literature",
            "label": "文献调研",
            "depends_on": ["outline"],
            "status": "pending",
            "model": writer,
            "started_at": None,
            "finished_at": None,
            "artifact": "literature_agent_output/citation_map.json",
            "error": None,
            "note": None,
        },
        {
            "id": "plotting",
            "label": "科研绘图",
            "depends_on": ["outline"],
            "status": "pending" if use_plotting else "skipped",
            "model": plotting if use_plotting else "",
            "started_at": None,
            "finished_at": None,
            "artifact": "plotting_results.json",
            "error": None,
            "note": None if use_plotting else "已关闭自动绘图（使用自带配图）",
        },
        {
            "id": "writing",
            "label": "章节写作",
            "depends_on": ["literature", "plotting"],
            "status": "pending",
            "model": writer,
            "started_at": None,
            "finished_at": None,
            "artifact": "latex_writeup/raw_draft_paper.tex",
            "error": None,
            "note": None,
        },
        {
            "id": "refinement",
            "label": "润色与排版",
            "depends_on": ["writing"],
            "status": "pending",
            "model": reflection,
            "started_at": None,
            "finished_at": None,
            "artifact": "content_refinement_workdir/final_refined_paper.tex",
            "error": None,
            "note": None,
        },
        {
            "id": "compile",
            "label": "终稿 PDF",
            "depends_on": ["refinement"],
            "status": "pending",
            "model": reflection,
            "started_at": None,
            "finished_at": None,
            "artifact": "final_paper.pdf",
            "error": None,
            "note": None,
        },
    ]


def write_pipeline_state(base_dir: str, state: Dict[str, Any]) -> None:
    if not base_dir:
        return
    os.makedirs(base_dir, exist_ok=True)
    path = osp.join(base_dir, STATE_FILENAME)
    tmp_path = f"{path}.{os.getpid()}.tmp"
    with _STATE_LOCK:
        state["updated_at"] = _utc_now()
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def init_pipeline_state(
    base_dir: str,
    use_plotting: bool,
    resume: bool,
    models: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    models = models or {}
    path = osp.join(base_dir, STATE_FILENAME)
    prior_issues: List[Dict[str, Any]] = []
    if resume and osp.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict) and isinstance(loaded.get("issues"), list):
                prior_issues = list(loaded["issues"])
        except (OSError, ValueError):
            pass

    state: Dict[str, Any] = {
        "version": 1,
        "resumed": bool(resume),
        "updated_at": _utc_now(),
        "nodes": _default_nodes(use_plotting, models),
        "issues": prior_issues,
    }
    write_pipeline_state(base_dir, state)
    return state


def mark_node(
    base_dir: str,
    state: Dict[str, Any],
    node_id: str,
    status: str,
    *,
    artifact: Optional[str] = None,
    error: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    now = _utc_now()
    for node in state.get("nodes", []):
        if node.get("id") != node_id:
            continue
        node["status"] = status
        if status == "running" and not node.get("started_at"):
            node["started_at"] = now
        if status in ("completed", "failed", "skipped"):
            if not node.get("started_at"):
                node["started_at"] = now
            node["finished_at"] = now
        if artifact is not None:
            node["artifact"] = artifact
        node["error"] = error
        if note is not None:
            node["note"] = note
        break
    write_pipeline_state(base_dir, state)


def add_issue(
    base_dir: str,
    state: Dict[str, Any],
    stage: str,
    message: str,
    severity: str = "error",
) -> None:
    entry = {
        "timestamp": _utc_now(),
        "stage": stage,
        "severity": severity,
        "message": str(message).strip()[:1200],
    }
    state.setdefault("issues", []).append(entry)
    write_pipeline_state(base_dir, state)


def checkpoint_ready_outline(base_dir: str) -> bool:
    path = osp.join(base_dir, "outline.json")
    if not osp.isfile(path) or osp.getsize(path) <= 2:
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return isinstance(data, dict) and len(data) > 0
    except (OSError, ValueError):
        return False


def checkpoint_ready_literature(base_dir: str) -> bool:
    lit_dir = osp.join(base_dir, "literature_agent_output")
    required = ("citation_map.json", "references.bib", "outline_v1.json", "updated_template.tex")
    for name in required:
        p = osp.join(lit_dir, name)
        if not osp.isfile(p) or osp.getsize(p) <= 0:
            return False
    try:
        with open(osp.join(lit_dir, "citation_map.json"), "r", encoding="utf-8") as fh:
            cmap = json.load(fh)
        return isinstance(cmap, dict)
    except (OSError, ValueError):
        return False


def checkpoint_ready_plotting(base_dir: str) -> Tuple[bool, Optional[List[Dict[str, Any]]]]:
    path = osp.join(base_dir, "plotting_results.json")
    if not osp.isfile(path):
        return False, None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return True, data
    except (OSError, ValueError):
        pass
    return False, None


def checkpoint_ready_writing(base_dir: str) -> bool:
    path = osp.join(base_dir, "latex_writeup", "raw_draft_paper.tex")
    if not osp.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return len(fh.read().strip()) > 200
    except OSError:
        return False


def effective_idea_path(base_dir: str, materials_dir: str, idea_file_path: str) -> str:
    """Returns `idea_file_path` unchanged unless `raw_materials/research_preferences.md`
    exists and is non-empty, in which case returns a combined markdown file path.
    """
    prefs_path = osp.join(materials_dir, "research_preferences.md")
    if not osp.isfile(prefs_path):
        return idea_file_path
    try:
        with open(prefs_path, "r", encoding="utf-8") as fh:
            prefs_text = fh.read().strip()
    except OSError:
        return idea_file_path
    if not prefs_text:
        return idea_file_path
    with open(idea_file_path, "r", encoding="utf-8") as fh:
        idea_text = fh.read()
    combined_path = osp.join(base_dir, ".prefs_idea_combined.md")
    with open(combined_path, "w", encoding="utf-8") as fh:
        fh.write(
            f"{idea_text.rstrip()}\n\n"
            f"---\n### Persistent Research Preferences & Long-Term Memory\n{prefs_text}\n"
        )
    return combined_path
