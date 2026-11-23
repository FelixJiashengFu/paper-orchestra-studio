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

import argparse
import sys
import os
import shutil
from datetime import datetime


def _mask_api_keys_in_output():
    """Masks API keys in everything the run prints (tracebacks included). Never raises."""
    try:
        from utils.providers import install_output_redaction

        install_output_redaction()
    except Exception as e:
        print(f"Warning: could not enable API key masking for the log ({type(e).__name__}); continuing.")


def main():
    _mask_api_keys_in_output()
    parser = argparse.ArgumentParser(
        description="CLI for generating a single paper from the Paper Orchestra pipeline."
    )

    parser.add_argument(
        "--raw_materials_dir",
        required=True,
        help="Directory containing the raw materials (e.g. idea and experimental log).",
    )
    parser.add_argument(
        "--output_dir",
        default=None,
        help="Directory to output the generated paper. If not specified, an automatic folder is created.",
    )
    parser.add_argument(
        "--latex_template_dir",
        required=True,
        help="Directory containing the LaTeX template (e.g. templates/cvpr2025, templates/iclr2025, or a journal template such as templates/journal-acs-jctc-jpc-jcim).",
    )
    parser.add_argument(
        "--idea_filename",
        default="idea_sparse.md",
        help="Filename of the idea document within raw_materials. Default: idea_sparse.md",
    )
    parser.add_argument(
        "--experimental_log_filename",
        default="experimental_log.md",
        help="Filename of the experimental log within raw_materials. Default: experimental_log.md",
    )
    parser.add_argument(
        "--writer_model_name",
        default="auto",
        help="LLM for writer and literature agents (default: auto).",
    )
    parser.add_argument(
        "--reflection_model_name",
        default="auto",
        help="LLM for reflection agents (default: auto).",
    )
    parser.add_argument(
        "--research_cutoff",
        default=None,
        help="Year-Month cutoff date for literature review (e.g., 2024-11). If not provided, defaults to current Year-Month.",
    )
    parser.add_argument(
        "--use_plotting",
        type=lambda x: (str(x).lower() in ["true", "1", "yes"]),
        default=False,
        help="Enable plotting agent workflow to generate figures from code.",
    )
    parser.add_argument(
        "--plotting_model_name",
        default="auto",
        help="LLM for the plotting agent (default: auto).",
    )
    parser.add_argument(
        "--image_model_name",
        default="gemini-3-pro-image",
        help="Vision model if required by plotting agent.",
    )
    parser.add_argument(
        "--plotting_max_critic_rounds",
        type=int,
        default=3,
        help="Maximum rounds of self-correction in the plotting agent.",
    )

    args = parser.parse_args()

    if args.research_cutoff is None:
        args.research_cutoff = datetime.now().strftime("%Y-%m")

    from utils.llm_backend_utils import (
        credential_problems,
        describe_auto_routing,
        literature_search_backend,
        resolve_image_model,
        has_any_llm_credentials,
    )

    requested_models = [args.writer_model_name, args.reflection_model_name]
    if args.use_plotting:
        requested_models.append(args.plotting_model_name)
    only_auto = all(str(m).strip().lower() in ("", "auto") for m in requested_models)
    if only_auto and not has_any_llm_credentials():
        print(
            "Error: No LLM API credentials found. Set any provider key in .env "
            "(e.g. OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, DEEPSEEK_API_KEY, DASHSCOPE_API_KEY, "
            "MOONSHOT_API_KEY, ZHIPU_API_KEY, OPENROUTER_API_KEY ...) or declare one in providers.json."
        )
        sys.exit(1)
    # Fail now with a clear message instead of on the first LLM call. Explicit
    # local models (e.g. ollama:qwen2.5:14b) need no key and pass this check.
    problems = credential_problems(requested_models)
    if problems:
        for problem in problems:
            print(f"Error: {problem}")
        sys.exit(1)

    if any(
        str(m).strip().lower() == "auto"
        for m in [
            args.writer_model_name,
            args.reflection_model_name,
            args.plotting_model_name,
        ]
    ):
        print("[Auto Model Routing] Active per-task assignments:")
        for task_name, resolved_model in describe_auto_routing().items():
            print(f"  - {task_name}: {resolved_model}")
        search_backend, search_model = literature_search_backend()
        print(f"  - literature_search: {search_backend + ' web search (' + search_model + ')' if search_model else 'off (scholarly APIs)'}")
        if args.use_plotting and str(args.image_model_name).strip().lower() == "auto":
            print(f"  - image: {resolve_image_model('auto') or 'none (Matplotlib code)'}")

    if not os.path.exists(args.raw_materials_dir):
        print(
            f"Error: Raw materials directory '{args.raw_materials_dir}' does not exist."
        )
        sys.exit(1)

    if args.output_dir:
        base_dir = args.output_dir
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = f"./paper_output_{timestamp}"

    os.makedirs(base_dir, exist_ok=True)

    target_raw_materials = os.path.join(base_dir, "raw_materials")
    if os.path.abspath(target_raw_materials) != os.path.abspath(args.raw_materials_dir):
        if os.path.exists(target_raw_materials):
            shutil.rmtree(target_raw_materials)
        shutil.copytree(args.raw_materials_dir, target_raw_materials)

    if not args.use_plotting:
        has_figures = False
        figures_dir = os.path.join(args.raw_materials_dir, "figures")
        if os.path.exists(figures_dir):
            for f in os.listdir(figures_dir):
                if f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg")):
                    has_figures = True
                    break

        if not has_figures:
            print("\n" + "=" * 80)
            print(
                f"⚠️ WARNING: No figures (.pdf, .png, .jpg) detected in the {figures_dir} directory."
            )
            print(
                "Since you are running WITHOUT plotting, we are using the provided materials directly."
            )
            print(
                f"Please make sure you provide all necessary figures inside {figures_dir}."
            )
            print("If figures are missing, compilation may fail.")
            print(
                f"If you don't have figures, you can use the plotting agent to generate them by running with --use_plotting."
            )
            print("=" * 80 + "\n")

    if args.use_plotting:
        from methods.paper_writer_with_plotting import write_single_paper

        print(f"🚀 Starting Paper Generation (WITH PLOTTING) in {base_dir}")
        write_single_paper(
            base_dir=base_dir,
            latex_template_dir=args.latex_template_dir,
            idea_filename=args.idea_filename,
            experimental_log_filename=args.experimental_log_filename,
            writer_model_name=args.writer_model_name,
            reflection_model_name=args.reflection_model_name,
            plotting_model_name=args.plotting_model_name,
            image_model_name=args.image_model_name,
            research_cutoff=args.research_cutoff,
            plotting_max_critic_rounds=args.plotting_max_critic_rounds,
        )
    else:
        from methods.paper_writer import write_single_paper

        provided_figures_dir = os.path.join(args.raw_materials_dir, "figures")
        print(
            f"🚀 Starting Paper Generation (WITHOUT PLOTTING), with figures stored in {provided_figures_dir}"
        )
        write_single_paper(
            base_dir=base_dir,
            latex_template_dir=args.latex_template_dir,
            idea_filename=args.idea_filename,
            experimental_log_filename=args.experimental_log_filename,
            writer_model_name=args.writer_model_name,
            reflection_model_name=args.reflection_model_name,
            research_cutoff=args.research_cutoff,
        )


if __name__ == "__main__":
    main()
