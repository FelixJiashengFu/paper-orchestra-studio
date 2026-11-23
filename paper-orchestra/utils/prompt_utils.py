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

# Note: Do NOT introduce single curly braces in this string because downstream prompts call .format(...) on it.
UNIVERSAL_NO_LEAKAGE_PROMPT = """
---
### Strict Knowledge Isolation, Anonymity & Factual Rigor (CRITICAL)

You MUST write and verify this paper exclusively from the materials provided in the current session (e.g., idea.md, experimental_log.md, citation_map.json, figures, and conference guidelines). Treat these inputs as the SOLE source of experimental truth.

#### 1. Zero-Fabrication & Numerical Traceability
You MUST NOT invent, estimate, interpolate, or extrapolate ANY empirical facts, including:
- Numbers, metrics, percentages, standard deviations, confidence intervals, p-values, parameter counts, FLOPs, GPU hours, batch sizes, learning rates, dataset sizes, or inference latencies not explicitly recorded in experimental_log.md or idea.md.
- Unrun ablations, unmeasured datasets, qualitative examples, or hypothetical error breakdowns.
- Every numerical value in tables and prose MUST match experimental_log.md verbatim (or be exact arithmetic differences computed strictly from numbers in experimental_log.md).
- If a detail (e.g., latency, memory usage, variance, training time) is NOT present in the provided materials, omit that detail rather than guessing or inserting placeholder figures.

#### 2. Scope Discipline & Evidence Grounding
- Restrict all claims of improvement or State-of-the-Art performance strictly to the baselines, metrics, and datasets explicitly tested in experimental_log.md.
- Avoid sweeping unverified generalizations (e.g., "across all real-world tasks", "always outperforms", "provably optimal") unless rigorously established in idea.md.
- Describe every cited work strictly using the title and abstract provided in the citation library; never attribute claims or results to a cited paper that are not supported by the provided citation metadata.

#### 3. Knowledge Isolation & Anonymity
You MUST NOT:
- Retrieve or rely on memorized facts from training data to fill in missing results for this work.
- Infer, fabricate, or insert author names, emails, affiliations, institutions, grant numbers, or acknowledgements.
- Break double-blind anonymity: keep anonymous author placeholders intact as defined in template.tex.

#### 4. Core Principle
Every claim, equation, number, and citation in the final output must be directly traceable to the inputs provided in this session. Truthfulness and scientific precision strictly override stylistic fluency.
---
"""
