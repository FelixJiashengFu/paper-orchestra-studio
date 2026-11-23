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

from utils.common_utils import load_md_file
from utils.venue_profile import load_venue_profile, venue_note
from utils.llm_backend_utils import call_llm_with_text_prompt, resolve_task_model
import json

from methods.prompts.outline_agent import outline_agent_system_prompt


class OutlineAgent:
    def __init__(
        self, model_name: str = "gemini-3.1-pro-preview", cutoff_date: str = "2024-10"
    ):
        self.model_name = model_name
        self.cutoff_date = cutoff_date

    def run(
        self,
        idea_file: str,
        experimental_log_file: str,
        latex_template_file: str,
        conference_guidelines_file: str,
        output_filepath: str = None,
    ):
        system_prompt = outline_agent_system_prompt.format(cutoff_date=self.cutoff_date)
        # Journal templates carry a venue.json; conference ones add nothing.
        system_prompt += venue_note(load_venue_profile(conference_guidelines_file), "outline")
        user_prompt = "\n\n".join(
            [
                "'idea.md':\n" + load_md_file(idea_file),
                "'experimental_log.md':\n" + load_md_file(experimental_log_file),
                "'template.tex':\n" + load_md_file(latex_template_file),
                load_md_file(conference_guidelines_file),
            ]
        )
        target_model = resolve_task_model(self.model_name, "outline")
        print(f"[OutlineAgent] Using model: {target_model}")
        response_dict = call_llm_with_text_prompt(
            prompt=user_prompt,
            model_name=target_model,
            generation_configs={"system_instruction": system_prompt},
        )
        json_result = response_dict["parsed_response"]
        if output_filepath is not None:
            with open(output_filepath, "w", encoding="utf-8") as f:
                json.dump(json_result, f, indent=4)
                print(f"outline.json output written to {output_filepath}")
        return json_result
