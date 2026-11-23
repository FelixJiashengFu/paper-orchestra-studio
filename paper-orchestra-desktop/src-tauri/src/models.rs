use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppSettings {
    pub paper_orchestra_dir: String,
    pub python_path: String,
    pub output_root_dir: String,
    pub db_path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateAppSettingsInput {
    pub paper_orchestra_dir: String,
    pub python_path: String,
    pub output_root_dir: String,
}

/// Mirror of `EnvSecretsConfig` in src/types.ts. `#[serde(default)]` lets a
/// partial payload (e.g. from an older frontend) deserialize with "" / {}.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct EnvSecretsConfig {
    pub anthropic_api_key: String,
    pub anthropic_base_url: String,
    pub deepseek_api_key: String,
    pub deepseek_base_url: String,
    pub gemini_api_key: String,
    pub vertex_ai_project: String,
    pub vertex_ai_location: String,
    pub openai_api_key: String,
    pub openai_base_url: String,
    pub semantic_scholar_api_key: String,
    pub smtp_email: String,
    pub smtp_password: String,

    // ---- Scholarly search platforms (paper-orchestra/utils/scholar_utils.py) ----
    /// S2_MIN_INTERVAL_SEC
    pub s2_min_interval_sec: String,
    /// OPENALEX_EMAIL
    pub openalex_email: String,
    /// OPENALEX_API_KEY
    pub openalex_api_key: String,
    /// CROSSREF_MAILTO
    pub crossref_mailto: String,
    /// CROSSREF_PLUS_TOKEN
    pub crossref_plus_token: String,
    /// NCBI_API_KEY
    pub ncbi_api_key: String,
    /// NCBI_EMAIL
    pub ncbi_email: String,
    /// CORE_API_KEY
    pub core_api_key: String,
    /// PO_SCHOLAR_SOURCES
    pub scholar_sources: String,

    // ---- Model routing (paper-orchestra/utils/providers.py) ----
    /// PO_AUTO_MODEL
    pub auto_model: String,
    /// PO_DEFAULT_PROVIDER
    pub default_provider: String,

    /// Every other KEY=VALUE of .env. Saving writes exactly these back: a key
    /// missing from the map is removed from .env, an existing key with an
    /// empty value is kept as `KEY=`, and a new key with an empty value is
    /// not written.
    pub extra_env: BTreeMap<String, String>,

    /// Raw text of providers.json ("" when the file does not exist). Saving ""
    /// leaves an existing file untouched.
    pub providers_json: String,

    /// Fingerprint of the repo dir, .env and providers.json this config was
    /// read from. Saving refuses to write when the files no longer match it
    /// ("" skips the check).
    pub source_fingerprint: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EnvironmentStatus {
    pub paper_orchestra_exists: bool,
    /// Non-empty when the repo is missing because its external disk is unplugged.
    pub offline_hint: String,
    pub cli_script_exists: bool,
    pub python_exists: bool,
    pub python_version: String,
    pub pdflatex_available: bool,
    pub pdflatex_version: String,
    pub bibtex_available: bool,
    pub templates: Vec<String>,
    pub has_anthropic_key: bool,
    pub has_deepseek_key: bool,
    pub has_gemini_key: bool,
    pub has_vertex_ai: bool,
    pub has_openai_key: bool,
    pub has_semantic_scholar_key: bool,
    /// LLM providers that currently have credentials (builtin presets + providers.json).
    pub configured_providers: Vec<String>,
    /// Names declared in providers.json (configured or not).
    pub custom_providers: Vec<String>,
    /// "" when providers.json is absent or valid, otherwise the error text.
    pub providers_json_error: String,
    /// Scholarly sources that will actually be queried, in order.
    pub scholar_sources: Vec<String>,
    /// PO_AUTO_MODEL, else providers.json `auto_model` ("" if neither).
    pub auto_model: String,
    /// Per-task models ({"outline": "claude-opus-5-5", ...}, lower-case task ids):
    /// PO_TASK_MODEL_<TASK> from .env, else providers.json `task_models`.
    pub task_models: BTreeMap<String, String>,
    /// "provider:model" that `auto` falls back to when no legacy key is set
    /// (`_non_legacy_default`), "" when there is none.
    pub auto_fallback_model: String,
    pub db_path: String,
    pub db_size_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectFigure {
    pub id: String,
    pub project_id: String,
    pub file_name: String,
    pub source_path: String,
    pub caption: String,
    pub sort_order: i64,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Project {
    pub id: String,
    pub title: String,
    pub template_name: String,
    pub writer_model: String,
    pub reflection_model: String,
    pub plotting_model: String,
    pub image_model: String,
    pub research_cutoff: String,
    pub use_plotting: bool,
    pub plotting_max_critic_rounds: i64,
    pub idea_markdown: String,
    pub experimental_log_markdown: String,
    pub created_at: String,
    pub updated_at: String,
    pub figures: Vec<ProjectFigure>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectSummary {
    pub id: String,
    pub title: String,
    pub template_name: String,
    pub writer_model: String,
    pub use_plotting: bool,
    pub updated_at: String,
    pub run_count: i64,
    pub last_run_status: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SaveProjectInput {
    pub id: String,
    pub title: String,
    pub template_name: String,
    pub writer_model: String,
    pub reflection_model: String,
    pub plotting_model: String,
    pub image_model: String,
    pub research_cutoff: String,
    pub use_plotting: bool,
    pub plotting_max_critic_rounds: i64,
    pub idea_markdown: String,
    pub experimental_log_markdown: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AddProjectFigureInput {
    pub project_id: String,
    pub source_path: String,
    pub caption: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunRecord {
    pub id: String,
    pub project_id: String,
    pub status: String,
    pub current_stage: String,
    pub template_name: String,
    pub writer_model: String,
    pub reflection_model: String,
    pub use_plotting: bool,
    pub run_dir: String,
    pub pdf_path: Option<String>,
    pub started_at: String,
    pub finished_at: Option<String>,
    pub exit_code: Option<i64>,
    pub error_message: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunLogEntry {
    pub id: i64,
    pub run_id: String,
    pub stage: String,
    pub line: String,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunLogEventPayload {
    pub run_id: String,
    pub project_id: String,
    pub stage: String,
    pub line: String,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunStatusEventPayload {
    pub run: RunRecord,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunArtifactsSummary {
    pub run_dir: String,
    pub final_pdf_path: Option<String>,
    pub has_outline: bool,
    pub has_citation_map: bool,
    pub has_plotting_results: bool,
    pub has_content_worklog: bool,
    pub has_format_worklog: bool,
    pub has_time_breakdown: bool,
    pub latex_files: Vec<String>,
    pub generated_figure_paths: Vec<String>,
    /// `usage_report.json` (LLM token usage) exists.
    pub has_usage_report: bool,
    /// `citation_audit.json` exists.
    pub has_citation_audit: bool,
    /// Absolute path of `citation_audit.md`, when it exists.
    pub citation_audit_md_path: Option<String>,
    /// `literature_agent_output/references_meta.json` exists.
    pub has_references_meta: bool,
    /// Run-relative folder the Overleaf export would zip
    /// (`content_refinement_workdir` or `latex_writeup`), `None` when the run
    /// has no LaTeX yet.
    pub latex_export_source: Option<String>,
    /// Absolute path of `references_cited.bib` (the citation audit's copy of
    /// the library with only the cited entries), when it exists.
    pub cited_bib_path: Option<String>,
    /// Set when the Overleaf export will put only the cited entries into the
    /// archive's `.bib` (see `export::cited_bib_swap`), `None` when it ships
    /// the library as it is.
    pub latex_export_bib: Option<LatexExportBib>,
}

/// The `.bib` of the Overleaf archive holds the cited entries only.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct LatexExportBib {
    /// Archive path of the `.bib` whose content is replaced, e.g. `references.bib`.
    pub file: String,
    /// Entries in the archive's `.bib`.
    pub kept: usize,
    /// Library entries left out (none of them is cited).
    pub dropped: usize,
}
