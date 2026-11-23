use std::path::Path;

use tauri::{AppHandle, State};
use tauri_plugin_opener::OpenerExt;

use crate::artifacts;
use crate::db::{self, DbState};
use crate::diagnostics;
use crate::env_config;
use crate::export;
use crate::live_edit::{
    self, LiveCompileResult, LiveCompileState, LiveEditFile, LiveEditSession, SyncBox, SyncSource,
};
use crate::models::{
    AddProjectFigureInput, AppSettings, EnvSecretsConfig, EnvironmentStatus, Project,
    ProjectFigure, ProjectSummary, RunArtifactsSummary, RunLogEntry, RunRecord, SaveProjectInput,
    UpdateAppSettingsInput,
};
use crate::runner;

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
