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

import os
import json
import base64
import concurrent.futures
from datetime import datetime, timezone

from utils.common_utils import load_md_file
from utils.paper_banana_utils import (
    retrieve_few_shot_examples,
    predict_figure_content,
    style_figure_content,
    generate_figure_visuals_with_code,
    critique_and_revise_figure,
    generate_figure_caption,
)


def process_single_figure(
    fig_plan,
    raw_materials_dir,
    model_name="gemini-3.1-pro-preview",
    image_model_name="gemini-3-pro-image",
    max_critic_rounds=3,
    venue_figure_style="",
):
    print(f"Processing figure: {fig_plan.get('figure_id', 'Unknown')}")
    title = fig_plan.get("title", "")
    plot_type = fig_plan.get("plot_type", "").lower()
    data_source_str = fig_plan.get("data_source", "").lower()
    objective = fig_plan.get("objective", "")
    aspect_ratio = fig_plan.get("aspect_ratio", "16:9")

    if plot_type not in ["plot", "diagram"]:
        task_name = "plot" if "chart" in plot_type or "plot" in plot_type else "diagram"
    else:
        task_name = plot_type

    # Gather raw material
    raw_content = ""
    for filename in os.listdir(raw_materials_dir):
        if not filename.endswith(".md"):
            continue

        match = False
        filename_lower = filename.lower()
        if "idea" in data_source_str and "idea" in filename_lower:
            match = True
        if "experiment" in data_source_str and "experiment" in filename_lower:
            match = True

        if match or filename_lower in data_source_str:
            filepath = os.path.join(raw_materials_dir, filename)
            raw_content += f"\n--- {filename} ---\n"
            raw_content += load_md_file(filepath)

    if not raw_content:
        for filename in ["experimental_log.md", "idea_sparse.md"]:
            filepath = os.path.join(raw_materials_dir, filename)
            if os.path.exists(filepath):
                raw_content += f"\n--- {filename} ---\n"
                raw_content += load_md_file(filepath)

    # 1. Retrieve few-shot examples
    print(f"[{fig_plan.get('figure_id', 'Unknown')}] Retrieving few-shot examples...")
    visual_intent = f"Title: {title}\nObjective: {objective}"
    examples = retrieve_few_shot_examples(
        task_name=task_name,
        raw_content=raw_content,
        description=visual_intent,
        model_name=model_name,
    )

    # 2. Plan/Predict Content
    print(f"[{fig_plan.get('figure_id', 'Unknown')}] Planning figure content...")
    figure_desc = predict_figure_content(
        task_name=task_name,
        raw_content=raw_content,
        description=visual_intent,
        examples=examples,
        model_name=model_name,
    )

    # 3. Stylist Refinement
    print(f"[{fig_plan.get('figure_id', 'Unknown')}] Styling figure content...")
    styled_figure_desc = style_figure_content(
        task_name=task_name,
        raw_content=raw_content,
        description=visual_intent,
        figure_desc=figure_desc,
        model_name=model_name,
        extra_style_guide=venue_figure_style,
    )

    # 4. Initial Generation
    print(
        f"[{fig_plan.get('figure_id', 'Unknown')}] Generating initial visual with aspect ratio {aspect_ratio}..."
    )
    base64_img, plot_code = generate_figure_visuals_with_code(
        task_name=task_name,
        figure_description=styled_figure_desc,
        model_name=model_name,
        image_model_name=image_model_name,
        aspect_ratio=aspect_ratio,
    )

    current_description = styled_figure_desc
    current_img = base64_img
    current_code = plot_code or ""
    version_snapshots = []
    if current_img:
        version_snapshots.append(
            {
                "instruction": "initial generation",
                "base64_img": current_img,
                "code": current_code,
                "description": current_description,
            }
        )

    # 5. Critic Iteration
    critic_history = []
    for round_idx in range(max_critic_rounds):
        print(
            f"[{fig_plan.get('figure_id', 'Unknown')}] Running critic round {round_idx + 1}/{max_critic_rounds}..."
        )
        try:
            revised_desc, suggestions = critique_and_revise_figure(
                task_name=task_name,
                raw_content=raw_content,
                description=visual_intent,
                figure_desc=current_description,
                base64_image=current_img,
                round_idx=round_idx,
                model_name=model_name,
            )

            critic_history.append(
                {
                    "round": round_idx,
                    "suggestions": suggestions,
                    "revised_description": revised_desc,
                }
            )

            if suggestions.strip() == "No changes needed.":
                print(
                    f"[{fig_plan.get('figure_id', 'Unknown')}] Critic round {round_idx + 1}: No changes needed. Finishing refinement."
                )
                break

            print(
                f"[{fig_plan.get('figure_id', 'Unknown')}] Critic round {round_idx + 1}: Changes needed. Re-generating based on suggestions..."
            )
            current_description = revised_desc
            new_img, new_code = generate_figure_visuals_with_code(
                task_name=task_name,
                figure_description=current_description,
                model_name=model_name,
                image_model_name=image_model_name,
                aspect_ratio=aspect_ratio,
            )

            if new_img:
                current_img = new_img
                current_code = new_code or ""
                version_snapshots.append(
                    {
                        "instruction": f"critic round {round_idx + 1}: {suggestions[:240]}",
                        "base64_img": current_img,
                        "code": current_code,
                        "description": current_description,
                    }
                )
        except Exception as e:
            print(
                f"[ERROR in {fig_plan.get('figure_id', 'Unknown')} Critic Round {round_idx + 1}]: {e}"
            )
            break

    print(f"[{fig_plan.get('figure_id', 'Unknown')}] Generating caption...")
    caption = generate_figure_caption(
        task_name=task_name,
        raw_content=raw_content,
        description=visual_intent,
        figure_desc=current_description,
        base64_image=current_img,
        model_name=model_name,
    )

    clean_description = current_description
    if "Detailed Description:" in clean_description:
        clean_description = clean_description.replace(
            "Detailed Description:", ""
        ).strip()
    if "**Detailed Description:**" in clean_description:
        clean_description = clean_description.replace(
            "**Detailed Description:**", ""
        ).strip()

    print(f"[{fig_plan.get('figure_id', 'Unknown')}] Figure processing complete.")
    return {
        "figure_id": fig_plan.get("figure_id"),
        "title": title,
        "task_name": task_name,
        "description": clean_description,
        "caption": caption,
        "aspect_ratio": aspect_ratio,
        "critic_history": critic_history,
        "base64_img": current_img,
        "_version_snapshots": version_snapshots,
    }


def _write_plot_bundle(figures_dir: str, fig_id_clean: str, res: dict) -> str:
    """Writes a versioned Plot Bundle under `figures/bundles/<fig_id_clean>/`."""
    bundle_dir = os.path.join(figures_dir, "bundles", fig_id_clean)
    os.makedirs(bundle_dir, exist_ok=True)
    snapshots = res.get("_version_snapshots") or []
    if not snapshots and res.get("base64_img"):
        snapshots = [
            {
                "instruction": "initial generation",
                "base64_img": res["base64_img"],
                "code": "",
                "description": res.get("description", ""),
            }
        ]
    versions_meta = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for idx, snap in enumerate(snapshots, start=1):
        ver = f"v{idx}"
        ver_dir = os.path.join(bundle_dir, ver)
        os.makedirs(ver_dir, exist_ok=True)
        img_rel = f"{ver}/image.jpg"
        with open(os.path.join(bundle_dir, img_rel), "wb") as fh:
            fh.write(base64.b64decode(snap["base64_img"]))
        code_rel = None
        if (snap.get("code") or "").strip():
            code_rel = f"{ver}/plot.py"
            with open(os.path.join(bundle_dir, code_rel), "w", encoding="utf-8") as fh:
                fh.write(snap["code"])
        desc_rel = f"{ver}/description.txt"
        with open(os.path.join(bundle_dir, desc_rel), "w", encoding="utf-8") as fh:
            fh.write(snap.get("description") or "")
        meta = {
            "version": ver,
            "created_at": now_iso,
            "instruction": snap.get("instruction", ""),
            "bbox": None,
            "image_rel": img_rel,
            "code_rel": code_rel,
            "description_rel": desc_rel,
        }
        with open(os.path.join(ver_dir, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        versions_meta.append(meta)

    active_ver = versions_meta[-1]["version"] if versions_meta else "v1"
    manifest = {
        "figure_id": res.get("figure_id") or fig_id_clean,
        "title": res.get("title") or fig_id_clean,
        "task_name": res.get("task_name") or "plot",
        "caption": res.get("caption") or "",
        "aspect_ratio": res.get("aspect_ratio") or "16:9",
        "active_version": active_ver,
        "target_rel_path": f"figures/{fig_id_clean}.jpg",
        "versions": versions_meta,
    }
    with open(os.path.join(bundle_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return f"figures/bundles/{fig_id_clean}"


class PlottingAgent:
    def __init__(
        self,
        model_name: str = "gemini-3.1-pro-preview",
        image_model_name: str = "gemini-3-pro-image",
        max_critic_rounds: int = 3,
        venue_figure_style: str = "",
    ):
        self.model_name = model_name
        self.image_model_name = image_model_name
        self.max_critic_rounds = max_critic_rounds
        self.venue_figure_style = venue_figure_style

    def run(
        self,
        outline_json_path: str,
        raw_materials_dir: str,
        output_filepath: str = None,
    ):
        with open(outline_json_path, "r", encoding="utf-8") as f:
            outline_data = json.load(f)

        plotting_plan = outline_data.get("plotting_plan", [])

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(
                    process_single_figure,
                    plan,
                    raw_materials_dir,
                    self.model_name,
                    self.image_model_name,
                    self.max_critic_rounds,
                    self.venue_figure_style,
                ): plan
                for plan in plotting_plan
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    res = future.result()
                    results.append(res)
                except Exception as e:
                    plan = futures[future]
                    import traceback

                    print(f"[ERROR] Failed processing {plan.get('figure_id')}: {e}")
                    traceback.print_exc()

        if output_filepath:
            output_dir = os.path.dirname(output_filepath)
            os.makedirs(output_dir, exist_ok=True)
            figures_dir = os.path.join(output_dir, "figures")
            os.makedirs(figures_dir, exist_ok=True)

            for res in results:
                if res.get("base64_img"):
                    fig_id_clean = res["figure_id"].replace(" ", "_").lower()
                    img_path = os.path.join(figures_dir, f"{fig_id_clean}.jpg")
                    try:
                        with open(img_path, "wb") as fh:
                            fh.write(base64.b64decode(res["base64_img"]))
                        print(f"Saved {img_path}")
                        res["image_path"] = f"figures/{fig_id_clean}.jpg"
                        res["bundle_dir"] = _write_plot_bundle(figures_dir, fig_id_clean, res)
                    except Exception as e:
                        print(f"Failed to save image {fig_id_clean}: {e}")

                res.pop("base64_img", None)
                res.pop("_version_snapshots", None)

            with open(output_filepath, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=4)
        else:
            for res in results:
                res.pop("_version_snapshots", None)

        return results
