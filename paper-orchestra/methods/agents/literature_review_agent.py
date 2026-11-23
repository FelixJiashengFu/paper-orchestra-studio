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

import json
import os
import re
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from google.genai import types  # type: ignore

from methods.prompts.literature_review_agent import (
    literature_review_agent_writter_prompt,
)
from utils.gemini_utils import call_gemini_with_contents, parse_gemini_latex_results
from utils.venue_profile import load_venue_profile, venue_note
from utils.providers import redact_secrets
from utils.llm_backend_utils import (
    call_deepseek_with_web_search,
    call_llm_with_text_prompt,
    literature_search_backend,
    resolve_task_model,
)
from utils.scholar_utils import (
    arxiv_keyword_search,
    enabled_sources,
    enrich_lookup,
    multi_keyword_search,
    reset_time_budget,
    s2_keyword_search,
    s2_title_search,
    start_time_budget,
    title_lookup,
)

# Sources the original non-Gemini discovery queried itself; the others only
# top up a result list that stayed short (see _discover_candidates_via_scholar_apis).
_ORIGINAL_DISCOVERY_SOURCES = ("semantic_scholar", "arxiv")
_SOURCE_NAMES = {
    "semantic_scholar": "Semantic Scholar", "openalex": "OpenAlex", "crossref": "Crossref",
    "arxiv": "arXiv", "dblp": "DBLP", "europepmc": "Europe PMC", "pubmed": "PubMed", "core": "CORE",
}


def _source_name(source) -> str:
    """'openalex' -> 'OpenAlex'; merged ids such as 'dblp+openalex' -> 'DBLP + OpenAlex'."""
    parts = [p for p in str(source or "").split("+") if p]
    return " + ".join(_SOURCE_NAMES.get(p, p) for p in parts) or "scholar"


class CandidatePaper(BaseModel):
    title: str
    year: int
    reason: str


class DiscoveryResult(BaseModel):
    section_name: str
    candidates: List[CandidatePaper]


# Without Gemini, discovery is keyword search (Semantic Scholar, arXiv and the
# other enabled sources), which also returns papers that merely share a word
# with the topic: an acronym from another field, a homonym ("PET" imaging for
# the PET interatomic potential). The writer prompt then has to cite ~90% of
# the library, so those papers end up cited. The relevance screen asks the
# literature model to name clear off-topic papers (title + abstract) and drops
# only those. Any failure keeps the library as it is. The Gemini path (search-
# grounded discovery) is unchanged. PO_RELEVANCE_SCREEN=0 turns it off.
_RELEVANCE_BATCH = 30
# Papers found as the title match of a title the outline model proposed for a
# must-have citation ("Targeted ... match for <hint>") were asked for by name,
# so they are not keyword noise and the screen leaves them alone.
_RELEVANCE_SKIP_REASON_PREFIX = "Targeted "
_RELEVANCE_PLACEHOLDER_REASONS = ("short reason", "<why it is off-topic>", "why it is off-topic")
_RELEVANCE_ABSTRACT_CHARS = 700
_RELEVANCE_PROMPT = """You are screening a reference library for a research paper.

The candidates below were found by keyword search, which returns false matches:
papers that only share a word, acronym or name with the topic but belong to an
unrelated field. "section" is the outline section the paper was found for, and
"found_by" is the search query that found it: a paper that matches what its
query was looking for (a dataset, metric, tool, method or baseline) is relevant
even when its own field differs from the paper's.

Mark a candidate as irrelevant ONLY if it clearly belongs to an unrelated topic
and could not reasonably be cited anywhere in this paper (introduction, related
work, background, methods or discussion). Papers on neighbouring topics, general
methods, datasets, tools or baselines the paper could build on are relevant.
When in doubt, keep the paper.

=== THE PAPER ===
{idea}

=== CORE PROBLEM ===
{problem}

=== CANDIDATES (JSON) ===
{candidates}

Return strictly valid JSON listing only the irrelevant candidates. If every
candidate is relevant, return:
```json
{{"irrelevant": []}}
```
Otherwise list each irrelevant candidate by its "id" with a short reason, in the
form {{"irrelevant": [{{"id": <candidate id>, "reason": "<why it is off-topic>"}}]}}.
"""


def _relevance_screen_enabled() -> bool:
    return os.getenv("PO_RELEVANCE_SCREEN", "1").strip().lower() not in ("0", "false", "no", "off")


class PaperData(BaseModel):
    citation_key: str
    title: str
    authors: List[str]
    venue: str
    year: int
    abstract: str
    citation_count: int
    found_in_section: str
    reason: str
    journal: Optional[str] = None
    volume: Optional[str] = None
    pages: Optional[str] = None
    publication_date: Optional[str] = None
    # Provenance for references_meta.json only. exclude=True keeps them out of
    # model_dump(), so the writer prompt and references.bib are unchanged.
    doi: Optional[str] = Field(default=None, exclude=True)
    url: Optional[str] = Field(default=None, exclude=True)
    source: Optional[str] = Field(default=None, exclude=True)


_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


def _source_meta(record) -> Dict[str, Optional[str]]:
    """doi / url / source of a scholar record as clean strings or None. Never raises."""
    out: Dict[str, Optional[str]] = {"doi": None, "url": None, "source": None}
    try:
        for name in out:
            value = record.get(name) if isinstance(record, dict) else None
            value = str(value).strip() if value not in (None, "") else ""
            if name == "doi":
                value = _DOI_PREFIX.sub("", value).strip()
            elif name == "url" and not re.match(r"^https?://", value, re.IGNORECASE):
                value = ""
            out[name] = value or None
    except Exception:
        pass
    return out


class WriterOutput(BaseModel):
    sections_written: List[str]
    latex_code: str


class HybridLiteratureAgent:
    def __init__(
        self,
        idea_path: str,
        experimental_log_path: str,
        latex_template_path: str,
        conference_guidelines_path: str,
        output_dir: str,
        model_name: str = "gemini-3.1-pro-preview",
        max_workers: int = 5,
    ):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        self.model_name = model_name
        self.max_workers = max_workers
        self.context_paths = {
            "idea.md": idea_path,
            "experimental_log.md": experimental_log_path,
            "template.tex": latex_template_path,
            "conference_guidelines.md": conference_guidelines_path,
        }
        self.context_files = self._load_context_files()
        self.venue_profile = load_venue_profile(conference_guidelines_path)
        self.google_search_tool = types.Tool(google_search=types.GoogleSearch())
        # Web search for candidate papers: ("gemini" | "deepseek", model) or ("", "")
        # for keyword search in the scholarly APIs (PO_TASK_MODEL_LITERATURE_SEARCH).
        # Read again at the start of run().
        self.search_backend, self.search_model = "", ""
        # A DeepSeek web-search request the API refused: not retried per task.
        self._deepseek_search_failed = False

        self.registry_lock = threading.Lock()
        self.print_lock = threading.Lock()

    def _safe_print(self, message: str):
        """Thread-safe printing to prevent garbled output."""
        with self.print_lock:
            print(message)

    def run(
        self,
        outline_path: str,
        cutoff_date: Optional[str] = None,
    ):
        if not cutoff_date:
            cutoff_date = datetime.datetime.now().strftime("%Y-%m")

        self._safe_print(
            f"🚀 Starting Fast Hybrid Literature Agent (Cutoff: {cutoff_date}, Threads: {self.max_workers})..."
        )

        with open(outline_path, "r", encoding="utf-8") as f:
            outline = json.load(f)

        outline_output_path = f"{self.output_dir}/outline_v1.json"
        unique_papers_registry: Dict[str, PaperData] = {}
        processed_tasks = set()

        # --- PHASE 1: DISCOVERY & RETRIEVAL ---
        self._safe_print(f"📚 Phase 1: Analyzing Plan & Executing Search Tasks...")
        self.search_backend, self.search_model = literature_search_backend()
        reset_time_budget()  # Phase 1b starts it (PO_ENRICH_TIME_BUDGET_MIN)
        self._deepseek_search_failed = False
        if self.search_backend:
            label = "Gemini + Google Search" if self.search_backend == "gemini" else "DeepSeek web search"
            self._safe_print(f"    -> Finding papers with {label} ({self.search_model}).")
        else:
            self._safe_print("    -> Finding papers by keyword search in the scholarly APIs.")

        search_tasks = self._collect_search_tasks(outline)
        self._safe_print(
            f"    -> Identified {len(search_tasks)} distinct search tasks."
        )

        self._execute_tasks_in_parallel(
            search_tasks, outline, unique_papers_registry, processed_tasks, cutoff_date
        )

        self._safe_print(
            f"\n✨ Final Count: {len(unique_papers_registry)} unique enriched papers retrieved."
        )

        # Gemini + Google Search results were never screened (as before); keyword
        # hits from the scholarly APIs and DeepSeek web search results are.
        if self.search_backend != "gemini" and _relevance_screen_enabled():
            self._screen_relevance(outline, unique_papers_registry)

        # --- PHASE 2: ASSET GENERATION ---
        final_papers_list = list(unique_papers_registry.values())
        bibtex_str = self._generate_bibtex(final_papers_list)
        citation_map = self._generate_citation_map(final_papers_list)
        references_meta = self._generate_references_meta(final_papers_list)

        self._safe_print("🔄 Injecting citations back into Outline...")
        updated_outline = self._inject_citations_into_outline(
            outline, unique_papers_registry
        )

        # --- PHASE 3: WRITING ---
        self._safe_print("✍️  Phase 3: Writing Introduction & Related Work...")
        latex_content = self._synthesize_content(
            updated_outline, final_papers_list, cutoff_date
        )

        # --- PHASE 4: SAVING ---
        self._save_outputs(
            latex_content,
            bibtex_str,
            citation_map,
            updated_outline,
            outline_output_path,
            references_meta=references_meta,
        )
        self._safe_print(
            f"✅ Done! Literature Review Outputs saved to {self.output_dir}/"
        )

    def _screen_relevance(self, outline: Dict, registry: Dict[str, "PaperData"]) -> None:
        """Drops clearly off-topic papers from the registry (keyword-search noise).

        Conservative by construction: only ids the model names as irrelevant are
        dropped, a batch it flags entirely is kept, and any error keeps everything.
        """
        try:
            total = len(registry)
            entries = [
                (key, paper)
                for key, paper in registry.items()
                if not str(paper.reason or "").startswith(_RELEVANCE_SKIP_REASON_PREFIX)
            ]
            if len(entries) < 2:
                return
            intro_plan = outline.get("intro_related_work_plan", {}) if isinstance(outline, dict) else {}
            strategy = intro_plan.get("introduction_strategy", {}) if isinstance(intro_plan, dict) else {}
            problem = strategy.get("problem_gap_hypothesis", "") if isinstance(strategy, dict) else ""
            idea = (self.context_files.get("idea.md", "") or "")[:6000]
            model = resolve_task_model(self.model_name, "relevance_screen")
            skipped = total - len(entries)
            self._safe_print(
                f"🧹 Relevance screen: checking {len(entries)} papers (model={model})"
                + (f"; {skipped} requested by title are kept as they are" if skipped else "")
                + "..."
            )
        except Exception as e:
            self._safe_print(f"    ⚠️ Relevance screen skipped: {e}")
            return

        dropped = []
        # Equal-sized batches of at most _RELEVANCE_BATCH, so no trailing batch of
        # one or two papers is left where the "flagged entirely" guard would
        # keep a correctly flagged paper.
        n_batches = -(-len(entries) // _RELEVANCE_BATCH)
        size = -(-len(entries) // n_batches)
        for start in range(0, len(entries), size):
            batch = entries[start:start + size]
            listing = [
                {
                    "id": i,
                    "title": paper.title,
                    "year": paper.year,
                    "venue": paper.venue,
                    "abstract": (paper.abstract or "")[:_RELEVANCE_ABSTRACT_CHARS],
                    "section": paper.found_in_section,
                    "found_by": str(paper.reason or "")[:200],
                }
                for i, (_, paper) in enumerate(batch)
            ]
            prompt = _RELEVANCE_PROMPT.format(
                idea=idea or "(not provided)",
                problem=str(problem or "(not provided)")[:2000],
                candidates=json.dumps(listing, ensure_ascii=False, indent=1),
            )
            try:
                rsp = call_llm_with_text_prompt(
                    prompt=prompt,
                    model_name=model,
                    generation_configs={"temperature": 0.0},
                )
                parsed = rsp.get("parsed_response") if isinstance(rsp, dict) else None
            except Exception as e:
                self._safe_print(f"    ⚠️ Relevance screen kept a batch of {len(batch)} papers (call failed: {e})")
                continue
            verdicts = parsed.get("irrelevant") if isinstance(parsed, dict) else None
            if not isinstance(verdicts, list):
                self._safe_print(f"    ⚠️ Relevance screen kept a batch of {len(batch)} papers (unreadable answer)")
                continue
            flagged = {}
            for v in verdicts:
                if not isinstance(v, dict):
                    continue
                try:
                    i = int(v.get("id"))
                except (TypeError, ValueError):
                    continue
                why = str(v.get("reason") or "").strip()
                if why.lower().strip("<>\"' .") in _RELEVANCE_PLACEHOLDER_REASONS:
                    continue  # the prompt's format example echoed back, not a verdict
                if 0 <= i < len(batch):
                    flagged[i] = why[:160]
            if flagged and len(flagged) >= len(batch):
                self._safe_print(
                    f"    ⚠️ Relevance screen flagged all {len(batch)} papers of a batch; keeping them."
                )
                continue
            for i, why in sorted(flagged.items()):
                dropped.append((batch[i][0], batch[i][1].title, why))

        with self.registry_lock:
            for key, _, _ in dropped:
                registry.pop(key, None)
        for _, title, why in dropped:
            self._safe_print(f"      -> 🗑️  Dropped off-topic paper: {title}" + (f" ({why})" if why else ""))
        self._safe_print(
            f"    -> Relevance screen kept {len(registry)} of {total} papers."
        )

    def _execute_tasks_in_parallel(
        self, tasks, outline, registry, processed_tasks_set, cutoff_date
    ):
        """Run a batch of search tasks concurrently."""
        active_tasks = []
        for task in tasks:
            task_id = f"{task['section']}:{task['focus']}"
            if task_id in processed_tasks_set:
                continue
            active_tasks.append(task)
            processed_tasks_set.add(task_id)

        if not active_tasks:
            return

        self._safe_print(f"   🔎 Phase 1a: Google Candidates Discovery (MaxPool=10)...")
        all_candidates_with_tasks = []

        with ThreadPoolExecutor(max_workers=10) as executor:  # Fast for Google Search!
            future_to_task = {
                executor.submit(
                    self._process_search_task_google, task, outline, cutoff_date
                ): task
                for task in active_tasks
            }
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    cands = future.result()
                    if cands:
                        for cand_item in cands:
                            all_candidates_with_tasks.append(
                                {"cand": cand_item, "task": task}
                            )
                except Exception as exc:
                    self._safe_print(
                        f"      ❌ Google Search failed for '{task['focus'][:20]}...': {exc}"
                    )

        self._safe_print(
            f"   📊 Phase 1b: Semantic Scholar Enrichment (Rate-limited, sequential)..."
        )

        start_time_budget()  # one budget for the run's lookups; later batches keep it
        for item in all_candidates_with_tasks:
            # Each source has its own throttle in utils/scholar_utils.py
            # (Semantic Scholar: <=1 req/s shared across threads and processes).
            self._enrich_and_register_paper(
                item["cand"], item["task"], registry, cutoff_date
            )

    def _collect_search_tasks(self, outline: Dict) -> List[Dict]:
        tasks = []
        plan = outline.get("intro_related_work_plan", {})

        # 1. Introduction Macro-Tasks
        intro_strat = plan.get("introduction_strategy", {})
        for direction in intro_strat.get("search_directions", []):
            tasks.append(
                {
                    "section": "Introduction",
                    "focus": direction,
                    "context": f"Hook: {intro_strat.get('hook_hypothesis', '')}. Gap: {intro_strat.get('problem_gap_hypothesis', '')}",
                    "search_type": "exploration",
                }
            )

        # 2. Related Work Micro-Tasks
        rw_strat = plan.get("related_work_strategy", {})
        for sub in rw_strat.get("subsections", []):
            queries = sub.get("limitation_search_queries", [])
            if not queries:
                queries = [
                    f"{sub.get('subsection_title', '')} {sub.get('methodology_cluster', '')}"
                ]

            for q in queries:
                tasks.append(
                    {
                        "section": f"Related Work: {sub.get('subsection_title', 'General')}",
                        "focus": q,
                        "context": f"Mission: {sub.get('sota_investigation_mission', '')}. Hypothesis: {sub.get('limitation_hypothesis', '')}",
                        "search_type": "exploration",
                    }
                )

        # 3. Targeted Tasks (Must-Haves from Section Plan)
        blueprint = outline.get("section_plan", [])
        for section in blueprint:
            section_title = section.get("section_title", "Unknown Section")
            for subsection in section.get("subsections", []):
                sub_title = subsection.get("subsection_title", "General")
                bullets = subsection.get("content_bullets", [])
                context_str = " ".join(bullets)

                hints = subsection.get("citation_hints", [])
                for hint in hints:
                    tasks.append(
                        {
                            "section": f"{section_title} - {sub_title}",
                            "focus": hint,
                            "context": f"Must-have citation for section covering: {context_str}",
                            "search_type": "targeted",
                        }
                    )

        return tasks

    def _inject_citations_into_outline(
        self, outline: Dict, registry: Dict[str, PaperData]
    ) -> Dict:
        # Group keys by section name
        section_to_keys = {}
        for p in registry.values():
            sec = p.found_in_section
            if sec not in section_to_keys:
                section_to_keys[sec] = []
            section_to_keys[sec].append(p.citation_key)

        plan = outline.get("intro_related_work_plan", {})

        # Introduction
        intro_strat = plan.get("introduction_strategy", {})
        intro_keys = section_to_keys.get("Introduction", [])
        intro_strat["citation_candidates"] = list(set(intro_keys))

        # Related Work
        rw_strat = plan.get("related_work_strategy", {})
        for sub in rw_strat.get("subsections", []):
            key = f"Related Work: {sub.get('subsection_title', 'General')}"
            sub["citation_candidates"] = section_to_keys.get(key, [])

        # Other Sections
        blueprint = outline.get("section_plan", [])
        for section in blueprint:
            section_title = section.get("section_title", "Unknown Section")
            for sub in section.get("subsections", []):
                sub_title = sub.get("subsection_title", "General")
                key = f"{section_title} - {sub_title}"
                sub["citation_candidates"] = section_to_keys.get(key, [])
                if "citation_hints" in sub:
                    del sub["citation_hints"]

        return outline

    def _process_search_task_google(self, task, outline, cutoff_date) -> list:
        if task.get("search_type") == "targeted":
            self._safe_print(
                f"   🎯 Targeted Search [{task['section']}]: '{task['focus']}'"
            )
        else:
            self._safe_print(
                f"   🔎 Exploration [{task['section']}]: '{task['focus']}'"
            )

        retry_count = 0
        while retry_count < 3:
            try:
                candidates = self._discover_candidates(task, outline, cutoff_date)
                if candidates:
                    return candidates
                retry_count += 1
            except Exception as e:
                retry_count += 1
        return []

    def _enrich_and_register_paper(self, cand, task, registry, cutoff_date):
        cand_title = getattr(cand, "title", None) or cand.get("title")
        cand_year = getattr(cand, "year", None) or cand.get("year")
        cand_reason = getattr(cand, "reason", None) or cand.get("reason", "")

        norm_title = self._normalize_title(cand_title)

        with self.registry_lock:
            if norm_title in registry:
                return

        try:
            # Semantic Scholar as before; other sources only when it cannot answer.
            s2_data = enrich_lookup(cand_title, cand_year, cutoff_date)

            if s2_data:
                if not self._is_paper_allowed(s2_data, cutoff_date):
                    return

                if not s2_data.get("abstract", ""):
                    return

                authors_list = [a["name"] for a in s2_data.get("authors", [])]
                j_info = s2_data.get("journal") or {}

                abstract_text = s2_data.get("abstract") or ""
                if len(abstract_text) > 1500:
                    abstract_text = abstract_text[:1500] + "... [Truncated]"

                source_meta = _source_meta(s2_data)

                full_obj = PaperData(
                    citation_key=self._generate_key(
                        s2_data.get("authors", []),
                        s2_data.get("year"),
                        s2_data["title"],
                    ),
                    title=s2_data["title"],
                    authors=authors_list,
                    venue=s2_data.get("venue") or "arXiv",
                    year=s2_data.get("year") or cand_year,
                    abstract=abstract_text,
                    citation_count=s2_data.get("citationCount") or 0,
                    found_in_section=task["section"],
                    reason=cand_reason,
                    journal=j_info.get("name"),
                    volume=j_info.get("volume"),
                    pages=j_info.get("pages"),
                    publication_date=s2_data.get("publicationDate"),
                    **source_meta,
                )

                final_norm_title = self._normalize_title(full_obj.title)

                with self.registry_lock:
                    if final_norm_title not in registry:
                        registry[final_norm_title] = full_obj
                        self._safe_print(
                            f"      -> 📚 Added enriched paper: {full_obj.title}"
                        )
        except Exception as e:
            pass

    def _is_paper_allowed(self, s2_data: Dict, cutoff_date_str: str) -> bool:
        if not cutoff_date_str:
            return True

        c_parts = [int(x) for x in cutoff_date_str.split("-")]
        c_y = c_parts[0]
        c_m = c_parts[1] if len(c_parts) > 1 else 12
        c_d = c_parts[2] if len(c_parts) > 2 else 1

        p_y, p_m, p_d = None, None, None
        if s2_data.get("publicationDate"):
            try:
                p_parts = [int(x) for x in str(s2_data["publicationDate"]).split("-")]
                p_y = p_parts[0]
                p_m = p_parts[1] if len(p_parts) > 1 else None
                p_d = p_parts[2] if len(p_parts) > 2 else None
            except:
                pass

        if p_y is None and s2_data.get("year"):
            p_y = int(s2_data["year"])

        if p_y is None:
            return True
        if p_y > c_y:
            return False
        if p_y < c_y:
            return True
        if p_m is None:
            return True
        if p_m > c_m:
            return False
        if p_m < c_m:
            return True
        if p_d is None:
            return True
        if p_d > c_d:
            return False

        return True

    def _discover_candidates(self, task, outline, cutoff_date) -> List[CandidatePaper]:
        intro_plan = outline.get("intro_related_work_plan", {}).get(
            "introduction_strategy", {}
        )
        core_problem = intro_plan.get("problem_gap_hypothesis", "N/A")

        if task.get("search_type") == "targeted":
            prompt = f"""
            Identify the SPECIFIC academic paper described by this hint: "{task['focus']}".
            
            CONTEXT: {task['context']}
            CUTOFF DATE: Published BEFORE {cutoff_date}
            
            INSTRUCTIONS:
            1. Use Google Search to find the EXACT title and year of this paper.
            2. If the hint refers to a dataset, model architecture, or baseline (e.g., "research paper introducing..."), you MUST find the primary, original paper that introduced it.
            3. Return EXACTLY 1 candidate paper that matches best.

            YOU MUST return the results in json format (```json content```) following this schema:
            {DiscoveryResult.model_json_schema()}
            """
        else:
            prompt = f"""
            Find 10-15 highly relevant, influential academic papers matching the search task. Do not force results; only return strictly relevant papers.
            
            SEARCH TASK: {task['focus']}
            CONTEXT FOR TASK: {task['context']}
            BROADER PROJECT PROBLEM: {core_problem}
            CUTOFF DATE: Published BEFORE {cutoff_date}
            
            INSTRUCTIONS:
            1. Use Google Search to find REAL papers (ArXiv, CVPR, NeurIPS, ICML, ICLR, etc.).
            2. Only return papers published in top-tier conferences or journals, or highly-cited works, or the exact state-of-the-art relevant to the task.

            YOU MUST return the results in json format (```json content```) following this schema:
            {DiscoveryResult.model_json_schema()}
            """

        if self.search_backend == "deepseek" and not getattr(self, "_deepseek_search_failed", False):
            # The prompt says "Google Search"; any web search does the job.
            ds_prompt = prompt.replace("Use Google Search", "Use web search")
            try:
                response_dict = call_deepseek_with_web_search(ds_prompt, self.search_model)
                parsed = response_dict.get("parsed_response")
                found = parsed.get("candidates", []) if isinstance(parsed, dict) else []
                found = [c for c in found if isinstance(c, dict) and c.get("title")]
                if found and task.get("search_type") == "targeted":
                    # Papers asked for by name are never dropped by the relevance
                    # screen; it recognises them by this reason prefix.
                    for c in found:
                        c["reason"] = f"Targeted DeepSeek web search match for {task['focus']}"
                if found:
                    return found
                self._safe_print(f"    ⚠️ DeepSeek web search found nothing usable for '{task['focus']}'; "
                                 "trying the scholarly APIs.")
            except Exception as e:
                if getattr(e, "status_code", None) in (400, 404, 422):
                    self._deepseek_search_failed = True
                self._safe_print(f"    ⚠️ DeepSeek web search failed ({redact_secrets(e)}); using the scholarly APIs instead.")
            return self._discover_candidates_via_scholar_apis(task, core_problem, cutoff_date)

        if self.search_backend == "gemini":
            response_dict = call_gemini_with_contents(
                model_name=self.search_model,
                contents=[prompt],
                generation_configs={
                    "tools": [self.google_search_tool],
                    "temperature": 0.1 if task.get("search_type") == "targeted" else 0.4,
                },
            )
            if response_dict["parsed_response"]:
                return response_dict["parsed_response"].get("candidates", [])
            return []

        # No web search: generate search queries with the configured LLM and query Semantic Scholar + arXiv.
        return self._discover_candidates_via_scholar_apis(
            task, core_problem, cutoff_date
        )

    def _discover_candidates_via_scholar_apis(
        self, task, core_problem: str, cutoff_date: str
    ) -> List[Dict]:
        query_prompt = f"""
        Generate 2 concise academic search queries (3-6 keywords each) and optional likely paper titles to find real papers for this search task.

        SEARCH TYPE: {task.get('search_type', 'open')}
        SEARCH TASK: {task['focus']}
        CONTEXT: {task['context']}
        BROADER PROBLEM: {core_problem}
        CUTOFF DATE: Before {cutoff_date}

        Return strictly valid JSON:
        ```json
        {{
          "queries": ["query 1", "query 2"],
          "likely_titles": ["Optional exact paper title if targeted"]
        }}
        ```
        """
        queries: List[str] = [task["focus"]]
        likely_titles: List[str] = []
        try:
            query_model = resolve_task_model(self.model_name, "literature_query")
            rsp = call_llm_with_text_prompt(
                prompt=query_prompt,
                model_name=query_model,
                generation_configs={"temperature": 0.2},
            )
            parsed = rsp.get("parsed_response") or {}
            if isinstance(parsed, dict):
                queries = [q for q in parsed.get("queries", []) if isinstance(q, str) and q.strip()] or queries
                likely_titles = [t for t in parsed.get("likely_titles", []) if isinstance(t, str) and t.strip()]
        except Exception as e:
            self._safe_print(f"    ⚠️ Query generation fallback for '{task['focus']}': {e}")

        candidates: List[Dict] = []
        seen_titles = set()
        sources = enabled_sources()
        use_s2 = "semantic_scholar" in sources
        use_arxiv = "arxiv" in sources

        if use_s2:
            for title in likely_titles[:3]:
                hit = s2_title_search(title, cutoff_date=cutoff_date)
                if hit and hit.get("title"):
                    norm = hit["title"].strip().lower()
                    if norm not in seen_titles:
                        seen_titles.add(norm)
                        candidates.append(
                            {
                                "title": hit["title"],
                                "year": hit.get("year") or 2024,
                                "reason": f"Targeted Semantic Scholar match for {task['focus']}",
                            }
                        )

        for q in queries[:2]:
            if use_s2:
                for hit in s2_keyword_search(q, limit=6, cutoff_date=cutoff_date):
                    title = (hit.get("title") or "").strip()
                    if title and title.lower() not in seen_titles:
                        seen_titles.add(title.lower())
                        candidates.append(
                            {
                                "title": title,
                                "year": hit.get("year") or 2024,
                                "reason": f"Semantic Scholar match for query: {q}",
                            }
                        )
            if len(candidates) < 4 and use_arxiv:
                for hit in arxiv_keyword_search(q, max_results=5, cutoff_date=cutoff_date):
                    title = (hit.get("title") or "").strip()
                    if title and title.lower() not in seen_titles:
                        seen_titles.add(title.lower())
                        candidates.append(
                            {
                                "title": title,
                                "year": hit.get("year") or 2024,
                                "reason": f"arXiv match for query: {q}",
                            }
                        )

        # Additive: the other enabled sources (OpenAlex, Crossref, ...) only top
        # up a list that stayed short (Semantic Scholar / arXiv down, blocked or
        # disabled). Their hits come after the ones above, so whenever the
        # original sources answer well the candidates are exactly as before.
        extra = [n for n in sources if n not in _ORIGINAL_DISCOVERY_SOURCES]
        if len(candidates) < 4 and extra:
            try:
                for title in likely_titles[:3]:
                    hit = title_lookup(title, cutoff_date=cutoff_date, require_abstract=False, sources=extra)
                    if hit and hit.get("title"):
                        norm = hit["title"].strip().lower()
                        if norm not in seen_titles:
                            seen_titles.add(norm)
                            candidates.append(
                                {
                                    "title": hit["title"],
                                    "year": hit.get("year") or 2024,
                                    "reason": f"Targeted {_source_name(hit.get('source'))} match for {task['focus']}",
                                }
                            )
                for q in queries[:2]:
                    for hit in multi_keyword_search(q, limit=6, cutoff_date=cutoff_date, sources=extra, per_source=5):
                        title = (hit.get("title") or "").strip()
                        if title and title.lower() not in seen_titles:
                            seen_titles.add(title.lower())
                            candidates.append(
                                {
                                    "title": title,
                                    "year": hit.get("year") or 2024,
                                    "reason": f"{_source_name(hit.get('source'))} match for query: {q}",
                                }
                            )
            except Exception as e:
                self._safe_print(f"    ⚠️ Extra scholar sources skipped for '{task['focus']}': {e}")

        limit = 1 if task.get("search_type") == "targeted" else 12
        return candidates[:limit]

    def _synthesize_content(self, outline, papers: List[PaperData], cutoff_date: str):
        paper_keys = [p.citation_key for p in papers]
        papers_payload = [p.model_dump() for p in papers]

        context_payload = {
            "template.tex": self.context_files.get("template.tex", ""),
            "intro_related_work_plan": outline.get("intro_related_work_plan", {}),
            "project_idea": self.context_files.get("idea.md", ""),
            "project_experimental_log": self.context_files.get(
                "experimental_log.md", ""
            ),
            "citation_checklist": paper_keys,
            "collected_papers": papers_payload,
        }

        writer_system_prompt = literature_review_agent_writter_prompt.format(
            paper_count=len(papers),
            min_cite_paper_count=int(len(papers) * 0.9),
            cutoff_date=cutoff_date,
        ) + venue_note(self.venue_profile, "literature")

        task = (
            "Generate the LaTeX for the Introduction section, which carries the related-work "
            "discussion (template.tex has no Related Work section)."
            if self.venue_profile
            else "Generate the LaTeX for Introduction and Related Work sections."
        )
        user_prompt = task + "\n\n" + json.dumps(context_payload)
        write_model = resolve_task_model(self.model_name, "literature_write")
        self._safe_print(f"    -> Synthesizing Intro & Related Work using model: {write_model}")
        response_dict = call_llm_with_text_prompt(
            model_name=write_model,
            prompt=user_prompt,
            generation_configs={"system_instruction": writer_system_prompt},
            result_parsing_func=parse_gemini_latex_results,
        )
        return response_dict["parsed_response"]

    def _generate_key(self, authors_raw, year, title):
        if not authors_raw:
            first_author = "Unknown"
        elif isinstance(authors_raw[0], dict):
            first_author = authors_raw[0]["name"].split()[-1]
        else:
            first_author = str(authors_raw[0]).split()[-1]

        clean_author = re.sub(r"[^a-zA-Z]", "", first_author).capitalize()
        year_str = str(year) if year else "2024"
        clean_title_str = re.sub(r"[^a-zA-Z0-9\s]", "", title.lower())
        words = clean_title_str.split()
        stopwords = {
            "the",
            "a",
            "an",
            "in",
            "on",
            "at",
            "for",
            "to",
            "of",
            "and",
            "is",
            "are",
            "with",
            "by",
            "study",
        }
        meaningful = [w.capitalize() for w in words if w not in stopwords]
        title_part = "".join(meaningful[:2]) if meaningful else "Paper"
        return f"{clean_author}{year_str}{title_part}"

    def _generate_bibtex(self, papers: List[PaperData]) -> str:
        entries = []
        seen_keys = set()
        for p in papers:
            base_key = p.citation_key
            unique_key = base_key
            suffix = "a"
            while unique_key in seen_keys:
                unique_key = f"{base_key}{suffix}"
                suffix = chr(ord(suffix) + 1)
            p.citation_key = unique_key
            seen_keys.add(unique_key)

            author_str = " and ".join(p.authors) if p.authors else "Unknown"
            entry_type = "article" if p.journal else "inproceedings"

            entry = f"@{entry_type}{{{p.citation_key},\n  title={{{p.title}}},\n  author={{{author_str}}},\n"
            if p.journal:
                entry += f"  journal={{{p.journal}}},\n"
            else:
                entry += f"  booktitle={{{p.venue}}},\n"
            entry += f"  year={{{p.year}}}"
            if p.volume:
                entry += f",\n  volume={{{p.volume}}}"
            if p.pages:
                entry += f",\n  pages={{{p.pages}}}"
            entry += "\n}"
            entries.append(entry)
        return "\n\n".join(entries)

    def _generate_citation_map(self, papers: List[PaperData]) -> Dict[str, Dict]:
        return {
            p.citation_key: {
                "citation_key": p.citation_key,
                "title": p.title,
                "authors": p.authors,
                "venue": p.venue,
                "year": p.year,
                "abstract": p.abstract.strip(),
            }
            for p in papers
            if p.abstract
        }

    def _generate_references_meta(self, papers: List[PaperData]) -> Optional[Dict[str, Dict]]:
        """{bibkey: {title, year, doi, url, source}} for references_meta.json (None on error).

        Call after _generate_bibtex, which settles the final (de-duplicated) keys.
        """
        try:
            meta: Dict[str, Dict] = {}
            for p in papers:
                try:
                    year = int(p.year) if p.year is not None else None
                except (TypeError, ValueError):
                    year = None
                meta[p.citation_key] = {
                    "title": p.title,
                    "year": year,
                    "doi": getattr(p, "doi", None),
                    "url": getattr(p, "url", None),
                    "source": getattr(p, "source", None),
                }
            return meta
        except Exception as e:
            self._safe_print(f"    ⚠️ references_meta.json skipped: {e}")
            return None

    def _save_outputs(self, latex, bibtex, citation_map, outline, outline_path, references_meta=None):
        with open(
            f"{self.output_dir}/updated_template.tex", "w", encoding="utf-8"
        ) as f:
            f.write(latex)
        with open(f"{self.output_dir}/references.bib", "w", encoding="utf-8") as f:
            f.write(bibtex)
        with open(f"{self.output_dir}/citation_map.json", "w", encoding="utf-8") as f:
            json.dump(citation_map, f, indent=4)
        with open(outline_path, "w", encoding="utf-8") as f:
            json.dump(outline, f, indent=4)
        # Sidecar next to references.bib (DOIs/URLs stay out of the .bib: some
        # .bst styles print them raw and "_" would break LaTeX). Without fresh
        # metadata an older sidecar in this folder is removed, never kept.
        meta_path = f"{self.output_dir}/references_meta.json"
        try:
            if references_meta is None:
                raise ValueError("no reference metadata")
            meta_text = json.dumps(references_meta, indent=2, ensure_ascii=False)
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write(meta_text)
        except Exception as e:
            if references_meta is not None:
                self._safe_print(f"    ⚠️ Could not write references_meta.json: {e}")
            try:
                if os.path.exists(meta_path):
                    os.remove(meta_path)
            except Exception:
                pass

    def _load_context_files(self) -> Dict[str, str]:
        loaded = {}
        for name, path in self.context_paths.items():
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    loaded[name] = f.read()
            else:
                loaded[name] = ""
        return loaded

    def _normalize_title(self, title):
        return re.sub(r"[^a-z0-9]", "", title.lower())
