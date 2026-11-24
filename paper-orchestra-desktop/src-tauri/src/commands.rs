use std::fs;
use std::path::Path;
use std::time::Duration;

use chrono::Utc;
use tauri::{AppHandle, State};
use tauri_plugin_opener::OpenerExt;
use uuid::Uuid;

use crate::artifacts;
use crate::db::{self, DbState};
use crate::diagnostics;
use crate::env_config;
use crate::export;
use crate::live_edit::{
    self, LiveCompileResult, LiveCompileState, LiveEditFile, LiveEditSession, SyncBox, SyncSource,
};
use crate::models::{
    AddProjectFigureInput, AppSettings, EnvSecretsConfig, EnvironmentStatus, LibraryItem, Project,
    ProjectFigure, ProjectSummary, ResearchMemory, ResearchPreferences, RunArtifactsSummary,
    RunLogEntry, RunRecord, SaveLibraryItemInput, SaveProjectInput, SaveResearchMemoryInput,
    SaveSkillInput, SaveSshHostInput, SkillEntry, SshExecResult, SshHost, SshTestResult,
    UpdateAppSettingsInput,
};
use crate::runner;
use crate::skills_store;
use crate::ssh_hosts;

#[tauri::command]
pub fn get_settings(state: State<'_, DbState>) -> Result<AppSettings, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::get_settings(&conn, &state.db_path)
}

#[tauri::command]
pub fn update_settings(
    state: State<'_, DbState>,
    input: UpdateAppSettingsInput,
) -> Result<AppSettings, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::update_settings(&conn, &state.db_path, &input)
}

#[tauri::command]
pub fn get_env_config(state: State<'_, DbState>) -> Result<EnvSecretsConfig, String> {
    let repo_dir = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        db::get_settings(&conn, &state.db_path)?.paper_orchestra_dir
    };
    env_config::read_env_config(&repo_dir)
}

#[tauri::command]
pub fn save_env_config(
    state: State<'_, DbState>,
    config: EnvSecretsConfig,
) -> Result<(), String> {
    let repo_dir = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        db::get_settings(&conn, &state.db_path)?.paper_orchestra_dir
    };
    env_config::write_env_config(&repo_dir, &config)
}

/// Validates providers.json text without saving anything. Returns "" when it
/// is valid (or blank), otherwise the error message to show.
#[tauri::command]
pub fn validate_providers_json(text: String) -> String {
    if text.trim().is_empty() {
        return String::new();
    }
    env_config::validate_providers_json(&text).err().unwrap_or_default()
}

#[tauri::command]
pub fn check_environment(state: State<'_, DbState>) -> Result<EnvironmentStatus, String> {
    let settings = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        db::get_settings(&conn, &state.db_path)?
    };
    Ok(env_config::check_environment(&settings))
}

/// Runs `tools/check_providers.py --json [--ping] [--scholar]` with the saved
/// settings and returns its report (`{"providers": [...], "scholar": [...]}`).
/// With `list_models`, runs `--json --list-models` instead: every configured
/// provider's model list under `"models"` (free; the flags are ignored).
#[tauri::command]
pub async fn check_connections(
    state: State<'_, DbState>,
    ping: bool,
    scholar: bool,
    list_models: Option<bool>,
    ping_models: Option<std::collections::BTreeMap<String, String>>,
) -> Result<serde_json::Value, String> {
    let settings = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        db::get_settings(&conn, &state.db_path)?
    };
    let secrets = env_config::secret_values(&settings.paper_orchestra_dir);
    let repo = Path::new(&settings.paper_orchestra_dir);
    if list_models.unwrap_or(false) {
        diagnostics::run_check_script_with(
            &settings.python_path,
            repo,
            &diagnostics::list_models_args(),
            diagnostics::CHECK_TIMEOUT,
            &secrets,
        )
        .await
    } else if let Some(extra) = ping_models
        .as_ref()
        .map(diagnostics::ping_model_args)
        .filter(|extra| ping && !extra.is_empty())
    {
        // "测试模型" chosen on the diagnostics page for some providers.
        let mut args: Vec<&str> = diagnostics::check_args(ping, scholar);
        args.extend(extra.iter().map(String::as_str));
        diagnostics::run_check_script_with(
            &settings.python_path,
            repo,
            &args,
            diagnostics::CHECK_TIMEOUT,
            &secrets,
        )
        .await
    } else {
        diagnostics::run_check_script(
            &settings.python_path,
            repo,
            ping,
            scholar,
            diagnostics::CHECK_TIMEOUT,
            &secrets,
        )
        .await
    }
}

#[tauri::command]
pub fn list_projects(state: State<'_, DbState>) -> Result<Vec<ProjectSummary>, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::list_projects(&conn)
}

#[tauri::command]
pub fn get_project(state: State<'_, DbState>, id: String) -> Result<Project, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::get_project(&conn, &id)
}

#[tauri::command]
pub fn create_project(
    state: State<'_, DbState>,
    title: String,
    template_name: String,
) -> Result<Project, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::create_project(&conn, &title, &template_name)
}

#[tauri::command]
pub fn save_project(state: State<'_, DbState>, input: SaveProjectInput) -> Result<Project, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::save_project(&conn, &input)
}

#[tauri::command]
pub fn delete_project(state: State<'_, DbState>, id: String) -> Result<(), String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::delete_project(&conn, &id)
}

#[tauri::command]
pub fn import_cvpr_example(state: State<'_, DbState>) -> Result<Project, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    let settings = db::get_settings(&conn, &state.db_path)?;
    db::import_cvpr_example(&conn, &settings.paper_orchestra_dir)
}

#[tauri::command]
pub fn add_project_figure(
    state: State<'_, DbState>,
    input: AddProjectFigureInput,
) -> Result<ProjectFigure, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::add_project_figure(&conn, &input)
}

#[tauri::command]
pub fn update_project_figure_caption(
    state: State<'_, DbState>,
    figure_id: String,
    caption: String,
) -> Result<(), String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::update_project_figure_caption(&conn, &figure_id, &caption)
}

#[tauri::command]
pub fn delete_project_figure(
    state: State<'_, DbState>,
    figure_id: String,
) -> Result<(), String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::delete_project_figure(&conn, &figure_id)
}

#[tauri::command]
pub async fn start_run(app: AppHandle, project_id: String) -> Result<RunRecord, String> {
    runner::start_run(app, project_id).await
}

#[tauri::command]
pub async fn stop_run(app: AppHandle, run_id: String) -> Result<(), String> {
    runner::stop_run(app, run_id).await
}

#[tauri::command]
pub fn list_runs(state: State<'_, DbState>, project_id: String) -> Result<Vec<RunRecord>, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::list_runs(&conn, &project_id)
}

#[tauri::command]
pub fn delete_run(state: State<'_, DbState>, run_id: String) -> Result<(), String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::delete_run(&conn, &run_id)
}

#[tauri::command]
pub fn get_run_logs(
    state: State<'_, DbState>,
    run_id: String,
    after_id: Option<i64>,
    limit: Option<i64>,
) -> Result<Vec<RunLogEntry>, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::get_run_logs(&conn, &run_id, after_id, limit)
}

#[tauri::command]
pub fn get_run_artifacts_summary(run_dir: String) -> Result<RunArtifactsSummary, String> {
    artifacts::get_run_artifacts_summary(&run_dir)
}

#[tauri::command]
pub fn read_run_text_artifact(run_dir: String, relative_path: String) -> Result<String, String> {
    artifacts::read_run_text_artifact(&run_dir, &relative_path)
}

/// Zips the run's final LaTeX sources for Overleaf; returns the written path.
#[tauri::command]
pub async fn export_run_latex(run_dir: String, dest_path: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || export::export_run_latex(&run_dir, &dest_path))
        .await
        .map_err(|e| format!("导出任务异常结束：{e}"))?
}

#[tauri::command]
pub fn read_file_base64(file_path: String) -> Result<String, String> {
    artifacts::read_file_as_base64(&file_path)
}

#[tauri::command]
pub fn open_path_in_system(app: AppHandle, target_path: String) -> Result<(), String> {
    let p = Path::new(&target_path);
    if !p.exists() {
        return Err(format!("Path does not exist: {target_path}"));
    }
    let opened = app
        .opener()
        .open_path(target_path.clone(), None::<&str>)
        .map_err(|e| format!("Failed to open path: {e}"));
    // macOS `open` fails silently for a file type no app claims (.md, .bib
    // without BibDesk); Windows shows its own "open with" picker instead.
    // Fall back to the default text editor for such files.
    #[cfg(target_os = "macos")]
    {
        if opened.is_err() && p.is_file() {
            let status = std::process::Command::new("/usr/bin/open")
                .args(["-t", "--"])
                .arg(&target_path)
                .status();
            if matches!(status, Ok(s) if s.success()) {
                return Ok(());
            }
        }
    }
    opened
}

/// The interpreter path the Python "选择" button should fill in; see
/// `env_config::prefer_venv_python`. `repo_dir` is the folder typed in the
/// form, which may not be saved yet.
#[tauri::command]
pub fn resolve_picked_python(picked: String, repo_dir: String) -> String {
    let home = std::env::var_os("HOME")
        .filter(|h| !h.is_empty())
        .map(std::path::PathBuf::from);
    let repo = env_config::expand_home(repo_dir.trim(), home.as_deref());
    env_config::prefer_venv_python(&picked, &repo)
}

/// 「导入填写单」: reads a form (PDF / .md / .txt / .docx) with
/// tools/parse_intake_form.py and returns `{fields, layout, warnings}`.
/// Nothing is saved here: the workbench shows the fields for review first.
#[tauri::command]
pub async fn parse_intake_form(state: State<'_, DbState>, path: String) -> Result<serde_json::Value, String> {
    let settings = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        db::get_settings(&conn, &state.db_path)?
    };
    if !Path::new(&path).is_file() {
        return Err(format!("找不到文件：{path}"));
    }
    let out = diagnostics::run_repo_script(
        &settings.python_path,
        Path::new(&settings.paper_orchestra_dir),
        diagnostics::INTAKE_SCRIPT,
        "填写单解析脚本",
        &[path.as_str()],
        std::time::Duration::from_secs(60),
        "读取填写单",
        "文件可能太大或已损坏。",
    )
    .await?;
    diagnostics::parse_intake_output(&out.stdout, &out.stderr)
}

/// 「投稿工具」: runs one subcommand of tools/submission_tools.py and returns
/// its JSON report. Builds run in scratch folders; nothing in the run changes
/// except the online citation check's own report file.
#[tauri::command]
pub async fn run_submission_tool(
    state: State<'_, DbState>,
    args: Vec<String>,
) -> Result<serde_json::Value, String> {
    diagnostics::validate_submission_args(&args)?;
    let settings = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        db::get_settings(&conn, &state.db_path)?
    };
    let repo = Path::new(&settings.paper_orchestra_dir);
    if let Some(hint) = env_config::offline_volume_hint(repo) {
        return Err(hint);
    }
    let argv: Vec<&str> = args.iter().map(String::as_str).collect();
    let out = diagnostics::run_repo_script(
        &settings.python_path,
        repo,
        diagnostics::SUBMISSION_SCRIPT,
        "投稿工具脚本",
        &argv,
        std::time::Duration::from_secs(900),
        "投稿工具",
        "论文可能太大，或 LaTeX 编译卡住了。",
    )
    .await?;
    diagnostics::parse_tool_output(&out.stdout, &out.stderr, "投稿工具")
}

#[tauri::command]
pub fn reveal_path_in_explorer(app: AppHandle, target_path: String) -> Result<(), String> {
    let p = Path::new(&target_path);
    if !p.exists() {
        return Err(format!("Path does not exist: {target_path}"));
    }
    app.opener()
        .reveal_item_in_dir(target_path)
        .map_err(|e| format!("Failed to reveal item: {e}"))
}

// ---- 「编辑并实时预览」: thin wrappers, see live_edit.rs ----

/// Opens one run's paper for editing: its source folder, main file and the
/// `.tex` / `.bib` files that can be edited.
#[tauri::command]
pub async fn live_edit_open(
    state: State<'_, DbState>,
    run_dir: String,
) -> Result<LiveEditSession, String> {
    let runs = live_edit::registered_runs(&state)?;
    live_edit::open(run_dir, runs).await
}

#[tauri::command]
pub async fn live_edit_read(
    state: State<'_, DbState>,
    run_dir: String,
    file: String,
) -> Result<LiveEditFile, String> {
    let runs = live_edit::registered_runs(&state)?;
    live_edit::read(run_dir, file, runs).await
}

/// Saves one file (the first save keeps the original as a backup). The
/// returned `content` is empty; `mtimeMs` is the file's new time. On
/// Windows it waits for a running compile first (TeX keeps the files open).
#[tauri::command]
pub async fn live_edit_save(
    state: State<'_, DbState>,
    compile_state: State<'_, LiveCompileState>,
    run_dir: String,
    file: String,
    content: String,
    expected_mtime_ms: Option<u64>,
) -> Result<LiveEditFile, String> {
    let runs = live_edit::registered_runs(&state)?;
    live_edit::save(&compile_state, run_dir, file, content, expected_mtime_ms, runs).await
}

/// Puts the original (the backup from the first save) back.
#[tauri::command]
pub async fn live_edit_restore(
    state: State<'_, DbState>,
    compile_state: State<'_, LiveCompileState>,
    run_dir: String,
    file: String,
) -> Result<LiveEditFile, String> {
    let runs = live_edit::registered_runs(&state)?;
    live_edit::restore(&compile_state, run_dir, file, runs).await
}

/// latexmk into `<run>/.live_preview/<sourceRel>/`; one compile at a time.
#[tauri::command]
pub async fn live_edit_compile(
    state: State<'_, DbState>,
    compile_state: State<'_, LiveCompileState>,
    run_dir: String,
    main_file: String,
) -> Result<LiveCompileResult, String> {
    let runs = live_edit::registered_runs(&state)?;
    live_edit::compile(&compile_state, run_dir, main_file, runs).await
}

/// Source line -> PDF box (SyncTeX).
#[tauri::command]
pub async fn live_edit_sync_view(
    state: State<'_, DbState>,
    run_dir: String,
    main_file: String,
    file: String,
    line: u32,
    column: u32,
) -> Result<Option<SyncBox>, String> {
    let runs = live_edit::registered_runs(&state)?;
    live_edit::sync_view(run_dir, main_file, file, line, column, runs).await
}

/// PDF position -> source file and line (SyncTeX).
#[tauri::command]
pub async fn live_edit_sync_edit(
    state: State<'_, DbState>,
    run_dir: String,
    main_file: String,
    page: u32,
    x: f64,
    y: f64,
) -> Result<Option<SyncSource>, String> {
    let runs = live_edit::registered_runs(&state)?;
    live_edit::sync_edit(run_dir, main_file, page, x, y, runs).await
}

// ---- ClawsGO Feature Suite: Resume, Figure Studio, Research Studio, Library, Memory, Skills, SSH ----

fn load_settings_and_secrets(state: &State<'_, DbState>) -> Result<(AppSettings, Vec<String>), String> {
    let settings = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        db::get_settings(&conn, &state.db_path)?
    };
    let repo = Path::new(&settings.paper_orchestra_dir);
    if let Some(hint) = env_config::offline_volume_hint(repo) {
        return Err(hint);
    }
    let secrets = env_config::secret_values(&settings.paper_orchestra_dir);
    Ok((settings, secrets))
}

fn new_studio_session_dir(output_root: &str, tag: &str) -> Result<std::path::PathBuf, String> {
    let clean: String = tag
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_'))
        .collect();
    let stamp = Utc::now().format("%Y%m%d_%H%M%S");
    let short = &Uuid::new_v4().to_string()[..6];
    let dir = Path::new(output_root)
        .join("studio_sessions")
        .join(format!("{stamp}_{}_{}", if clean.is_empty() { "session" } else { &clean }, short));
    fs::create_dir_all(&dir).map_err(|e| format!("创建工坊输出目录失败：{e}"))?;
    Ok(dir)
}

fn extract_flag_value<'a>(args: &'a [String], flag: &str) -> Option<&'a str> {
    let mut i = 1;
    while i + 1 < args.len() {
        if args[i] == flag {
            return Some(args[i + 1].as_str());
        }
        i += 1;
    }
    None
}

fn resolve_job_id(job_id: Option<String>) -> String {
    job_id
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| Uuid::new_v4().to_string())
}

#[tauri::command]
pub async fn cancel_studio_job(
    jobs: State<'_, diagnostics::ActiveStudioJobsState>,
    job_id: String,
) -> Result<bool, String> {
    Ok(jobs.cancel(job_id.trim()).await)
}

#[tauri::command]
pub async fn resume_run(app: AppHandle, run_id: String) -> Result<RunRecord, String> {
    runner::resume_run(app, run_id).await
}

#[tauri::command]
pub async fn run_figure_studio(
    app: AppHandle,
    state: State<'_, DbState>,
    jobs: State<'_, diagnostics::ActiveStudioJobsState>,
    mut args: Vec<String>,
    job_id: Option<String>,
) -> Result<serde_json::Value, String> {
    let (settings, secrets) = load_settings_and_secrets(&state)?;
    let figure_studio_root = Path::new(&settings.output_root_dir).join("figure_studio");
    fs::create_dir_all(&figure_studio_root).map_err(|e| format!("创建绘图工坊目录失败：{e}"))?;
    let root_str = figure_studio_root.to_string_lossy().to_string();

    // Fill default workspace path when standalone mode omits --dir / --out-dir value
    for i in 0..args.len() {
        if matches!(args[i].as_str(), "--dir" | "--out-dir") {
            if let Some(next) = args.get_mut(i + 1) {
                if next.trim().is_empty() {
                    *next = root_str.clone();
                }
            }
        }
    }

    diagnostics::validate_figure_studio_args(&args)?;
    let subcmd = args.first().map(String::as_str).unwrap_or("");
    if matches!(subcmd, "revise" | "create") {
        let jid = resolve_job_id(job_id);
        let dir_hint = extract_flag_value(&args, "--bundle")
            .or_else(|| extract_flag_value(&args, "--out-dir"))
            .map(str::to_string);
        diagnostics::run_streaming_json_cli(
            &app,
            &jobs,
            &jid,
            dir_hint.as_deref(),
            &settings.python_path,
            Path::new(&settings.paper_orchestra_dir),
            diagnostics::FIGURE_STUDIO_SCRIPT,
            "绘图工坊",
            &args,
            Duration::from_secs(240),
            &secrets,
        )
        .await
    } else {
        diagnostics::run_json_cli(
            &settings.python_path,
            Path::new(&settings.paper_orchestra_dir),
            diagnostics::FIGURE_STUDIO_SCRIPT,
            "绘图工坊",
            &args,
            Duration::from_secs(240),
            &secrets,
        )
        .await
    }
}

#[tauri::command]
pub async fn run_data_profile(
    state: State<'_, DbState>,
    file_path: String,
) -> Result<serde_json::Value, String> {
    let (settings, secrets) = load_settings_and_secrets(&state)?;
    let args = vec!["data-profile".to_string(), "--file".to_string(), file_path];
    diagnostics::validate_research_studio_args(&args)?;
    diagnostics::run_json_cli(
        &settings.python_path,
        Path::new(&settings.paper_orchestra_dir),
        diagnostics::RESEARCH_STUDIO_SCRIPT,
        "数据概况分析",
        &args,
        // Pandas and NumPy may take over a minute to load from an external volume.
        Duration::from_secs(180),
        &secrets,
    )
    .await
}

#[tauri::command]
pub async fn run_data_analysis(
    app: AppHandle,
    state: State<'_, DbState>,
    jobs: State<'_, diagnostics::ActiveStudioJobsState>,
    file_path: String,
    question: String,
    model: Option<String>,
    job_id: Option<String>,
) -> Result<serde_json::Value, String> {
    let (settings, secrets) = load_settings_and_secrets(&state)?;
    let out_dir = new_studio_session_dir(&settings.output_root_dir, "data_analysis")?;
    let out_str = out_dir.to_string_lossy().to_string();
    let args = vec![
        "run-data-analysis".to_string(),
        "--file".to_string(),
        file_path,
        "--question".to_string(),
        question,
        "--out-dir".to_string(),
        out_str.clone(),
        "--model".to_string(),
        model.unwrap_or_else(|| "auto".to_string()),
    ];
    diagnostics::validate_research_studio_args(&args)?;
    let jid = resolve_job_id(job_id);
    diagnostics::run_streaming_json_cli(
        &app,
        &jobs,
        &jid,
        Some(&out_str),
        &settings.python_path,
        Path::new(&settings.paper_orchestra_dir),
        diagnostics::RESEARCH_STUDIO_SCRIPT,
        "数据分析工作流",
        &args,
        Duration::from_secs(360),
        &secrets,
    )
    .await
}

#[tauri::command]
pub async fn run_sop_workflow(
    app: AppHandle,
    state: State<'_, DbState>,
    jobs: State<'_, diagnostics::ActiveStudioJobsState>,
    sop: String,
    payload: serde_json::Value,
    model: Option<String>,
    job_id: Option<String>,
) -> Result<serde_json::Value, String> {
    let (settings, secrets) = load_settings_and_secrets(&state)?;
    let prefs_md = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        db::build_research_preferences_markdown(&conn).unwrap_or_default()
    };
    let lib_items = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        db::list_library_items(&conn).unwrap_or_default()
    };

    let out_dir = new_studio_session_dir(&settings.output_root_dir, &sop)?;
    let mut enriched = match payload {
        serde_json::Value::Object(m) => m,
        _ => serde_json::Map::new(),
    };
    if !prefs_md.trim().is_empty() && !enriched.contains_key("preferencesMarkdown") {
        enriched.insert(
            "preferencesMarkdown".to_string(),
            serde_json::Value::String(prefs_md),
        );
    }
    if !enriched.contains_key("libraryItems") && !lib_items.is_empty() {
        if let Ok(v) = serde_json::to_value(lib_items.into_iter().take(40).collect::<Vec<_>>()) {
            enriched.insert("libraryItems".to_string(), v);
        }
    }

    let input_path = out_dir.join("sop_input.json");
    let raw_json = serde_json::to_string_pretty(&serde_json::Value::Object(enriched))
        .map_err(|e| format!("序列化 SOP 参数失败：{e}"))?;
    fs::write(&input_path, raw_json.as_bytes()).map_err(|e| format!("写入 SOP 输入失败：{e}"))?;

    let out_str = out_dir.to_string_lossy().to_string();
    let args = vec![
        "run-sop".to_string(),
        "--sop".to_string(),
        sop,
        "--input-json".to_string(),
        input_path.to_string_lossy().to_string(),
        "--out-dir".to_string(),
        out_str.clone(),
        "--model".to_string(),
        model.unwrap_or_else(|| "auto".to_string()),
    ];
    diagnostics::validate_research_studio_args(&args)?;
    let jid = resolve_job_id(job_id);
    diagnostics::run_streaming_json_cli(
        &app,
        &jobs,
        &jid,
        Some(&out_str),
        &settings.python_path,
        Path::new(&settings.paper_orchestra_dir),
        diagnostics::RESEARCH_STUDIO_SCRIPT,
        "科研 SOP 流水线",
        &args,
        Duration::from_secs(360),
        &secrets,
    )
    .await
}

#[tauri::command]
pub async fn run_custom_skill(
    app: AppHandle,
    state: State<'_, DbState>,
    jobs: State<'_, diagnostics::ActiveStudioJobsState>,
    skill_id: String,
    prompt: String,
    attached_files: Vec<String>,
    model: Option<String>,
    job_id: Option<String>,
) -> Result<serde_json::Value, String> {
    let app_data_dir = crate::app_data_dir(&app)?;
    let skill = skills_store::resolve_skill_by_id(&app_data_dir, &skill_id)?;
    let (settings, secrets) = load_settings_and_secrets(&state)?;
    let prefs_md = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        db::build_research_preferences_markdown(&conn).unwrap_or_default()
    };
    let out_dir = new_studio_session_dir(&settings.output_root_dir, &skill.id)?;
    let out_str = out_dir.to_string_lossy().to_string();
    let skill_path = out_dir.join("active_skill.md");
    let files_path = out_dir.join("attached_files.json");
    let prefs_path = out_dir.join("prefs.md");
    fs::write(&skill_path, skill.content.as_bytes()).map_err(|e| format!("写入技能文件失败：{e}"))?;
    fs::write(
        &files_path,
        serde_json::to_string(&attached_files).unwrap_or_else(|_| "[]".to_string()).as_bytes(),
    )
    .map_err(|e| format!("写入附件列表失败：{e}"))?;
    fs::write(&prefs_path, prefs_md.as_bytes()).map_err(|e| format!("写入偏好失败：{e}"))?;

    let args = vec![
        "run-skill".to_string(),
        "--skill-file".to_string(),
        skill_path.to_string_lossy().to_string(),
        "--prompt".to_string(),
        prompt,
        "--out-dir".to_string(),
        out_str.clone(),
        "--files-json".to_string(),
        files_path.to_string_lossy().to_string(),
        "--prefs-file".to_string(),
        prefs_path.to_string_lossy().to_string(),
        "--model".to_string(),
        model.unwrap_or_else(|| "auto".to_string()),
    ];
    diagnostics::validate_research_studio_args(&args)?;
    let jid = resolve_job_id(job_id);
    diagnostics::run_streaming_json_cli(
        &app,
        &jobs,
        &jid,
        Some(&out_str),
        &settings.python_path,
        Path::new(&settings.paper_orchestra_dir),
        diagnostics::RESEARCH_STUDIO_SCRIPT,
        "技能执行",
        &args,
        Duration::from_secs(240),
        &secrets,
    )
    .await
}

#[tauri::command]
pub fn list_library_items(state: State<'_, DbState>) -> Result<Vec<LibraryItem>, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::list_library_items(&conn)
}

#[tauri::command]
pub fn save_library_item(
    state: State<'_, DbState>,
    input: SaveLibraryItemInput,
) -> Result<LibraryItem, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::save_library_item(&conn, &input)
}

#[tauri::command]
pub fn delete_library_item(state: State<'_, DbState>, id: String) -> Result<(), String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::delete_library_item(&conn, &id)
}

#[tauri::command]
pub async fn run_library_tool(
    state: State<'_, DbState>,
    mut args: Vec<String>,
    items_payload: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let (settings, secrets) = load_settings_and_secrets(&state)?;
    if let Some(items) = items_payload {
        let session_dir = new_studio_session_dir(&settings.output_root_dir, "library")?;
        let json_path = session_dir.join("library_items.json");
        let serialized = serde_json::to_string_pretty(&items)
            .map_err(|e| format!("序列化文献列表失败：{e}"))?;
        fs::write(&json_path, serialized.as_bytes()).map_err(|e| format!("写入临时文献列表失败：{e}"))?;
        args.push("--input-json".to_string());
        args.push(json_path.to_string_lossy().to_string());
    }
    diagnostics::validate_library_tool_args(&args)?;
    diagnostics::run_json_cli(
        &settings.python_path,
        Path::new(&settings.paper_orchestra_dir),
        diagnostics::LIBRARY_TOOLS_SCRIPT,
        "文献库工具",
        &args,
        Duration::from_secs(240),
        &secrets,
    )
    .await
}

#[tauri::command]
pub async fn run_latex_copilot(
    state: State<'_, DbState>,
    args: Vec<String>,
) -> Result<serde_json::Value, String> {
    let (settings, secrets) = load_settings_and_secrets(&state)?;
    diagnostics::validate_latex_copilot_args(&args)?;
    diagnostics::run_json_cli(
        &settings.python_path,
        Path::new(&settings.paper_orchestra_dir),
        diagnostics::LATEX_COPILOT_SCRIPT,
        "LaTeX 智能助手",
        &args,
        Duration::from_secs(180),
        &secrets,
    )
    .await
}

#[tauri::command]
pub fn list_research_memories(state: State<'_, DbState>) -> Result<Vec<ResearchMemory>, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::list_research_memories(&conn)
}

#[tauri::command]
pub fn save_research_memory(
    state: State<'_, DbState>,
    input: SaveResearchMemoryInput,
) -> Result<ResearchMemory, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::save_research_memory(&conn, &input)
}

#[tauri::command]
pub fn delete_research_memory(state: State<'_, DbState>, id: String) -> Result<(), String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::delete_research_memory(&conn, &id)
}

#[tauri::command]
pub fn get_research_preferences(state: State<'_, DbState>) -> Result<ResearchPreferences, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::get_research_preferences(&conn)
}

#[tauri::command]
pub fn save_research_preferences(
    state: State<'_, DbState>,
    prefs: ResearchPreferences,
) -> Result<ResearchPreferences, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::save_research_preferences(&conn, &prefs)
}

#[tauri::command]
pub fn list_ssh_hosts(state: State<'_, DbState>) -> Result<Vec<SshHost>, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::list_ssh_hosts(&conn)
}

#[tauri::command]
pub fn save_ssh_host(
    app: AppHandle,
    state: State<'_, DbState>,
    input: SaveSshHostInput,
) -> Result<SshHost, String> {
    ssh_hosts::validate_host_fields(&input.alias, &input.host, input.port, &input.username)?;
    let id = input
        .id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| Uuid::new_v4().to_string());

    let resolved_key_path = if let Some(pem) = input.private_key_pem.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        let app_data_dir = crate::app_data_dir(&app)?;
        ssh_hosts::save_ssh_private_key(&app_data_dir, &id, pem)?
            .to_string_lossy()
            .to_string()
    } else {
        input.key_path.trim().to_string()
    };

    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::save_ssh_host(&conn, &id, &input, &resolved_key_path)
}

#[tauri::command]
pub fn delete_ssh_host(state: State<'_, DbState>, id: String) -> Result<(), String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
    db::delete_ssh_host(&conn, &id)
}

#[tauri::command]
pub async fn test_ssh_host(state: State<'_, DbState>, id: String) -> Result<SshTestResult, String> {
    let (host, secrets) = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        let host = db::get_ssh_host(&conn, &id)?;
        let settings = db::get_settings(&conn, &state.db_path)?;
        let secrets = env_config::secret_values(&settings.paper_orchestra_dir);
        (host, secrets)
    };
    let res = ssh_hosts::test_ssh_host(&host, &secrets).await;
    let status = if res.ok { "online" } else { "error" };
    let gpu_summary = if res.gpus.is_empty() {
        if res.ok {
            res.system_info.clone()
        } else {
            res.error.clone().unwrap_or_default()
        }
    } else {
        res.gpus
            .iter()
            .map(|g| format!("{} ({:.0}%, {:.0}/{:.0} MB)", g.name, g.utilization_pct, g.mem_used_mb, g.mem_total_mb))
            .collect::<Vec<_>>()
            .join(" | ")
    };
    if let Ok(conn) = state.conn.lock() {
        let _ = db::update_ssh_host_status(&conn, &id, status, &gpu_summary);
    }
    Ok(res)
}

#[tauri::command]
pub async fn exec_ssh_command(
    state: State<'_, DbState>,
    id: String,
    command: String,
    work_dir: Option<String>,
) -> Result<SshExecResult, String> {
    let (host, secrets) = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        let host = db::get_ssh_host(&conn, &id)?;
        let settings = db::get_settings(&conn, &state.db_path)?;
        let secrets = env_config::secret_values(&settings.paper_orchestra_dir);
        (host, secrets)
    };
    let dir = work_dir.unwrap_or_else(|| host.remote_work_dir.clone());
    ssh_hosts::exec_ssh_command(&host, &command, Some(&dir), &secrets).await
}

#[tauri::command]
pub async fn scp_transfer(
    state: State<'_, DbState>,
    id: String,
    direction: String,
    local_path: String,
    remote_path: String,
) -> Result<String, String> {
    let host = {
        let conn = state.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        db::get_ssh_host(&conn, &id)?
    };
    ssh_hosts::scp_transfer(&host, &direction, &local_path, &remote_path).await
}

#[tauri::command]
pub fn list_skills(app: AppHandle) -> Result<Vec<SkillEntry>, String> {
    let app_data_dir = crate::app_data_dir(&app)?;
    skills_store::list_skills(&app_data_dir)
}

#[tauri::command]
pub fn save_skill(app: AppHandle, input: SaveSkillInput) -> Result<SkillEntry, String> {
    let app_data_dir = crate::app_data_dir(&app)?;
    skills_store::save_skill(&app_data_dir, &input)
}

#[tauri::command]
pub fn delete_skill(app: AppHandle, skill_id: String) -> Result<(), String> {
    let app_data_dir = crate::app_data_dir(&app)?;
    skills_store::delete_skill(&app_data_dir, &skill_id)
}

#[tauri::command]
pub fn import_skill_from_file(app: AppHandle, path: String) -> Result<SkillEntry, String> {
    let p = Path::new(&path);
    if !p.is_file() {
        return Err(format!("找不到技能文件：{path}"));
    }
    let raw = fs::read_to_string(p).map_err(|e| format!("读取技能文件失败：{e}"))?;
    let stem = p.file_stem().and_then(|s| s.to_str()).unwrap_or("imported-skill");
    let app_data_dir = crate::app_data_dir(&app)?;
    skills_store::save_skill(
        &app_data_dir,
        &SaveSkillInput {
            id: None,
            name: stem.to_string(),
            description: format!("从本地文件导入 ({})", p.file_name().and_then(|s| s.to_str()).unwrap_or("")),
            category: "导入技能".to_string(),
            content: raw,
        },
    )
}

#[tauri::command]
pub async fn import_skill_from_url(
    app: AppHandle,
    state: State<'_, DbState>,
    url: String,
) -> Result<SkillEntry, String> {
    let (settings, secrets) = load_settings_and_secrets(&state)?;
    let args = vec!["fetch-skill-url".to_string(), "--url".to_string(), url.clone()];
    diagnostics::validate_research_studio_args(&args)?;
    let res = diagnostics::run_json_cli(
        &settings.python_path,
        Path::new(&settings.paper_orchestra_dir),
        diagnostics::RESEARCH_STUDIO_SCRIPT,
        "远程技能拉取",
        &args,
        Duration::from_secs(30),
        &secrets,
    )
    .await?;

    let name = res["name"].as_str().unwrap_or("github-skill").to_string();
    let desc = res["description"].as_str().unwrap_or("从 URL 导入").to_string();
    let content = res["content"]
        .as_str()
        .ok_or_else(|| "远程响应缺少技能正文内容。".to_string())?
        .to_string();

    let app_data_dir = crate::app_data_dir(&app)?;
    skills_store::save_skill(
        &app_data_dir,
        &SaveSkillInput {
            id: None,
            name,
            description: desc,
            category: "GitHub / 市场导入".to_string(),
            content,
        },
    )
}

fn write_temp_export_markdown(output_root: &str, markdown: &str) -> Result<std::path::PathBuf, String> {
    let dir = Path::new(output_root).join("studio_sessions").join("_exports");
    fs::create_dir_all(&dir).map_err(|e| format!("创建临时导出目录失败：{e}"))?;
    let md_path = dir.join(format!("{}.md", Uuid::new_v4()));
    fs::write(&md_path, markdown.as_bytes()).map_err(|e| format!("写入临时 Markdown 失败：{e}"))?;
    Ok(md_path)
}

#[tauri::command]
pub async fn get_export_environment(state: State<'_, DbState>) -> Result<serde_json::Value, String> {
    let (settings, secrets) = load_settings_and_secrets(&state)?;
    let args = vec!["google-drive-status".to_string()];
    diagnostics::validate_doc_exporter_args(&args)?;
    diagnostics::run_json_cli(
        &settings.python_path,
        Path::new(&settings.paper_orchestra_dir),
        diagnostics::DOC_EXPORTER_SCRIPT,
        "检测导出环境",
        &args,
        Duration::from_secs(20),
        &secrets,
    )
    .await
}

#[tauri::command]
pub async fn export_markdown_document(
    state: State<'_, DbState>,
    markdown: String,
    title: String,
    format: String,
    output_path: String,
) -> Result<serde_json::Value, String> {
    let fmt = format.trim().to_ascii_lowercase();
    if !matches!(fmt.as_str(), "docx" | "pdf" | "html") {
        return Err(format!("不支持的导出格式：{format}"));
    }
    let (settings, secrets) = load_settings_and_secrets(&state)?;
    let tmp_md = write_temp_export_markdown(&settings.output_root_dir, &markdown)?;
    let mut args = vec![
        "export".to_string(),
        "--input-md".to_string(),
        tmp_md.to_string_lossy().to_string(),
        "--format".to_string(),
        fmt,
        "--out".to_string(),
        output_path,
    ];
    if !title.trim().is_empty() {
        args.push("--title".to_string());
        args.push(title.trim().to_string());
    }
    let valid = diagnostics::validate_doc_exporter_args(&args);
    if let Err(e) = valid {
        let _ = fs::remove_file(&tmp_md);
        return Err(e);
    }
    let res = diagnostics::run_json_cli(
        &settings.python_path,
        Path::new(&settings.paper_orchestra_dir),
        diagnostics::DOC_EXPORTER_SCRIPT,
        "文档导出",
        &args,
        Duration::from_secs(90),
        &secrets,
    )
    .await;
    let _ = fs::remove_file(&tmp_md);
    res
}

#[tauri::command]
pub async fn export_markdown_to_google_drive(
    state: State<'_, DbState>,
    markdown: String,
    title: String,
    format: String,
    drive_dir: Option<String>,
) -> Result<serde_json::Value, String> {
    let fmt = format.trim().to_ascii_lowercase();
    if !matches!(fmt.as_str(), "docx" | "pdf") {
        return Err(format!("Google Drive 导出仅支持 docx 或 pdf：{format}"));
    }
    let (settings, secrets) = load_settings_and_secrets(&state)?;
    let tmp_md = write_temp_export_markdown(&settings.output_root_dir, &markdown)?;
    let mut args = vec![
        "export-google-drive".to_string(),
        "--input-md".to_string(),
        tmp_md.to_string_lossy().to_string(),
        "--format".to_string(),
        fmt,
    ];
    if !title.trim().is_empty() {
        args.push("--title".to_string());
        args.push(title.trim().to_string());
    }
    if let Some(d) = drive_dir.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        args.push("--drive-dir".to_string());
        args.push(d.to_string());
    }
    let valid = diagnostics::validate_doc_exporter_args(&args);
    if let Err(e) = valid {
        let _ = fs::remove_file(&tmp_md);
        return Err(e);
    }
    let res = diagnostics::run_json_cli(
        &settings.python_path,
        Path::new(&settings.paper_orchestra_dir),
        diagnostics::DOC_EXPORTER_SCRIPT,
        "导出至 Google Drive",
        &args,
        Duration::from_secs(90),
        &secrets,
    )
    .await;
    let _ = fs::remove_file(&tmp_md);
    res
}
