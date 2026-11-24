use std::fs::{self, File, OpenOptions};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use tauri::{AppHandle, Emitter, Manager};

use crate::{artifacts, commands, db, diagnostics, env_config, live_edit, models::*};

macro_rules! wait_for {
    (sync, $call:expr) => { $call };
    (asynchronous, $call:expr) => { $call.await };
}

// Decode the same camelCase arguments as Tauri and call the existing command.
macro_rules! action {
    ($args:expr, $mode:ident, $command:ident, [$($context:expr),*], {$($field:ident: $ty:ty),*}) => {{
        #[derive(Deserialize)]
        #[serde(rename_all = "camelCase", deny_unknown_fields)]
        struct Input { $($field: $ty),* }
        let Input { $($field),* } = serde_json::from_value($args)
            .map_err(|error| format!("Invalid tool arguments: {error}"))?;
        let value = wait_for!($mode, commands::$command($($context,)* $($field),*))?;
        serde_json::to_value(value).map_err(|error| error.to_string())
    }};
}

pub fn streams(command: &str, args: &Value) -> bool {
    match command {
        "run_sop_workflow" | "run_data_analysis" | "run_custom_skill" => true,
        "run_figure_studio" => matches!(args["args"][0].as_str(), Some("create" | "revise")),
        _ => false,
    }
}

#[rustfmt::skip]
pub async fn dispatch(app: &AppHandle, command: &str, args: Value) -> Result<Value, String> {
    let state = || app.state::<db::DbState>();
    let jobs = || app.state::<diagnostics::ActiveStudioJobsState>();
    let compile = || app.state::<live_edit::LiveCompileState>();
    match command {
        "get_settings" => action!(args, sync, get_settings, [state()], {}),
        "check_environment" => action!(args, sync, check_environment, [state()], {}),
        "check_connections" => action!(args, asynchronous, check_connections, [state()], {
            ping: bool, scholar: bool, list_models: Option<bool>, ping_models: Option<std::collections::BTreeMap<String, String>>
        }),
        "list_projects" => action!(args, sync, list_projects, [state()], {}),
        "get_project" => action!(args, sync, get_project, [state()], {id: String}),
        "create_project" => action!(args, sync, create_project, [state()], {title: String, template_name: String}),
        "update_project" => update_project(app, args),
        "delete_project" => action!(args, sync, delete_project, [state()], {id: String}),
        "import_cvpr_example" => action!(args, sync, import_cvpr_example, [state()], {}),
        "add_project_figure" => action!(args, sync, add_project_figure, [state()], {input: AddProjectFigureInput}),
        "update_project_figure_caption" => action!(args, sync, update_project_figure_caption, [state()], {figure_id: String, caption: String}),
        "delete_project_figure" => action!(args, sync, delete_project_figure, [state()], {figure_id: String}),
        "parse_intake_form" => action!(args, asynchronous, parse_intake_form, [state()], {path: String}),
        "start_run" => action!(args, asynchronous, start_run, [app.clone()], {project_id: String}),
        "resume_run" => action!(args, asynchronous, resume_run, [app.clone()], {run_id: String}),
        "stop_run" => action!(args, asynchronous, stop_run, [app.clone()], {run_id: String}),
        "delete_run" => action!(args, sync, delete_run, [state()], {run_id: String}),
        "list_runs" => action!(args, sync, list_runs, [state()], {project_id: String}),
        "get_run_logs" => action!(args, sync, get_run_logs, [state()], {run_id: String, after_id: Option<i64>, limit: Option<i64>}),
        "get_run_artifacts_summary" => action!(args, sync, get_run_artifacts_summary, [], {run_dir: String}),
        "read_run_text_artifact" => read_artifact(app, args),
        "copy_run_pdf" => copy_run_pdf(app, args),
        "export_run_latex" => {
            check_new_output(&args, "destPath")?;
            action!(args, asynchronous, export_run_latex, [], {run_dir: String, dest_path: String})
        }
        "run_submission_tool" => {
            if let Some(argv) = args["args"].as_array() {
                if let Some(pair) = argv.windows(2).find(|pair| pair[0] == "--out") {
                    check_new_output(&json!({ "path": pair[1] }), "path")?;
                }
            }
            action!(args, asynchronous, run_submission_tool, [state()], {args: Vec<String>})
        }
        "live_edit_open" => action!(args, asynchronous, live_edit_open, [state()], {run_dir: String}),
        "live_edit_read" => action!(args, asynchronous, live_edit_read, [state()], {run_dir: String, file: String}),
        "live_edit_save" => {
            if args["expectedMtimeMs"].as_u64().is_none() {
                return Err("Read the file first and supply its expectedMtimeMs.".into());
            }
            action!(args, asynchronous, live_edit_save, [state(), compile()], {run_dir: String, file: String, content: String, expected_mtime_ms: Option<u64>})
        }
        "live_edit_restore" => action!(args, asynchronous, live_edit_restore, [state(), compile()], {run_dir: String, file: String}),
        "live_edit_compile" => action!(args, asynchronous, live_edit_compile, [state(), compile()], {run_dir: String, main_file: String}),
        "live_edit_sync_view" => action!(args, asynchronous, live_edit_sync_view, [state()], {run_dir: String, main_file: String, file: String, line: u32, column: u32}),
        "live_edit_sync_edit" => action!(args, asynchronous, live_edit_sync_edit, [state()], {run_dir: String, main_file: String, page: u32, x: f64, y: f64}),
        "run_latex_copilot" => action!(args, asynchronous, run_latex_copilot, [state()], {args: Vec<String>}),
        "run_figure_studio" => action!(args, asynchronous, run_figure_studio, [app.clone(), state(), jobs()], {args: Vec<String>, job_id: Option<String>}),
        "run_data_profile" => action!(args, asynchronous, run_data_profile, [state()], {file_path: String}),
        "run_data_analysis" => action!(args, asynchronous, run_data_analysis, [app.clone(), state(), jobs()], {file_path: String, question: String, model: Option<String>, job_id: Option<String>}),
        "run_sop_workflow" => action!(args, asynchronous, run_sop_workflow, [app.clone(), state(), jobs()], {sop: String, payload: Value, model: Option<String>, job_id: Option<String>}),
        "run_custom_skill" => action!(args, asynchronous, run_custom_skill, [app.clone(), state(), jobs()], {skill_id: String, prompt: String, attached_files: Vec<String>, model: Option<String>, job_id: Option<String>}),
        "list_library_items" => browse_library(app, args),
        "delete_library_item" => action!(args, sync, delete_library_item, [state()], {id: String}),
        "save_library_item" => action!(args, sync, save_library_item, [state()], {input: SaveLibraryItemInput}),
        "run_library_tool" => action!(args, asynchronous, run_library_tool, [state()], {args: Vec<String>, items_payload: Option<Value>}),
        "list_research_memories" => action!(args, sync, list_research_memories, [state()], {}),
        "save_research_memory" => action!(args, sync, save_research_memory, [state()], {input: SaveResearchMemoryInput}),
        "delete_research_memory" => action!(args, sync, delete_research_memory, [state()], {id: String}),
        "get_research_preferences" => action!(args, sync, get_research_preferences, [state()], {}),
        "save_research_preferences" => action!(args, sync, save_research_preferences, [state()], {prefs: ResearchPreferences}),
        "list_ssh_hosts" => {
            let mut hosts = commands::list_ssh_hosts(state())?;
            for host in &mut hosts {
                host.key_path.clear();
            }
            Ok(json!(hosts))
        }
        "test_ssh_host" => action!(args, asynchronous, test_ssh_host, [state()], {id: String}),
        "list_skills" => {
            let skills = commands::list_skills(app.clone())?;
            Ok(json!(skills.into_iter().map(|skill| json!({
                "id": skill.id, "name": skill.name, "description": skill.description,
                "category": skill.category, "builtin": skill.builtin, "updatedAt": skill.updated_at,
            })).collect::<Vec<_>>()))
        }
        "get_skill" => get_skill(app, args),
        "delete_skill" => action!(args, sync, delete_skill, [app.clone()], {skill_id: String}),
        "save_skill" => action!(args, sync, save_skill, [app.clone()], {input: SaveSkillInput}),
        "import_skill_from_file" => action!(args, sync, import_skill_from_file, [app.clone()], {path: String}),
        "import_skill_from_url" => action!(args, asynchronous, import_skill_from_url, [app.clone(), state()], {url: String}),
        "get_export_environment" => action!(args, asynchronous, get_export_environment, [state()], {}),
        "export_markdown_document" => {
            check_new_output(&args, "outputPath")?;
            action!(args, asynchronous, export_markdown_document, [state()], {
                markdown: String, title: String, format: String, output_path: String
            })
        }
        "export_markdown_to_google_drive" => action!(args, asynchronous, export_markdown_to_google_drive, [state()], {
            markdown: String, title: String, format: String, drive_dir: Option<String>
        }),
        "show_studio_view" => show_view(app, args),
        _ => Err("Unknown PaperOrchestra Studio action.".into()),
    }
}

fn check_new_output(args: &Value, key: &str) -> Result<(), String> {
    let path = args[key].as_str().map(Path::new).filter(|p| p.is_absolute())
        .ok_or_else(|| format!("{key} must be an absolute output path."))?;
    match path.symlink_metadata() {
        Ok(_) => Err(format!("Output already exists: {}", path.display())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}

pub fn redact(app: &AppHandle, value: &mut Value) {
    if let Ok(settings) = commands::get_settings(app.state()) {
        let secrets = env_config::secret_values(&settings.paper_orchestra_dir);
        diagnostics::mask_strings(value, &secrets);
    }
}

fn update_project(app: &AppHandle, args: Value) -> Result<Value, String> {
    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct Input {
        id: String,
        expected_updated_at: String,
        patch: Map<String, Value>,
    }
    let input: Input = serde_json::from_value(args).map_err(|e| e.to_string())?;
    let state = app.state::<db::DbState>();
    let conn = state.conn.lock().map_err(|e| e.to_string())?;
    let current = db::get_project(&conn, &input.id)?;
    if current.updated_at != input.expected_updated_at {
        return Err("The project changed. Read it again before applying the edit.".into());
    }
    let mut updated = serde_json::to_value(current).map_err(|e| e.to_string())?;
    for (key, value) in input.patch {
        match key.as_str() {
            "title" | "templateName" | "writerModel" | "reflectionModel" | "plottingModel"
            | "imageModel" | "researchCutoff" | "usePlotting" | "plottingMaxCriticRounds"
            | "ideaMarkdown" | "experimentalLogMarkdown" => updated[&key] = value,
            _ => return Err(format!("Invalid project field: {key}")),
        }
    }
    updated["expectedUpdatedAt"] = json!(input.expected_updated_at);
    let save = serde_json::from_value(updated).map_err(|e| e.to_string())?;
    let saved = db::save_project(&conn, &save)?;
    drop(conn);
    let _ = app.emit_to("main", "paper-orchestra-project-updated", &saved.id);
    serde_json::to_value(saved).map_err(|e| e.to_string())
}

fn registered_run_dir(app: &AppHandle, path: &str) -> Result<PathBuf, String> {
    let dir = Path::new(path);
    if !dir.is_absolute() {
        return Err("Use the absolute run directory from list_runs.".into());
    }
    let root = dir.canonicalize().map_err(|e| e.to_string())?;
    let state = app.state::<db::DbState>();
    let runs = live_edit::registered_runs(&state)?;
    if runs.iter().any(|run| Path::new(&run.run_dir).canonicalize().ok().as_ref() == Some(&root)) {
        Ok(root)
    } else {
        Err("Use a registered Studio run directory from list_runs.".into())
    }
}

fn read_artifact(app: &AppHandle, args: Value) -> Result<Value, String> {
    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct Input {
        run_dir: String,
        relative_path: String,
    }
    let input: Input = serde_json::from_value(args).map_err(|e| e.to_string())?;
    let root = registered_run_dir(app, &input.run_dir)?;
    let candidate = artifacts::resolve_safe_subpath(&root, &input.relative_path)?;
    let target = candidate.canonicalize().map_err(|e| e.to_string())?;
    let extension = target.extension().and_then(|value| value.to_str()).unwrap_or_default();
    let is_text = matches!(extension.to_ascii_lowercase().as_str(), "json" | "md" | "log" | "tex" | "bib" | "txt" | "csv" | "sty");
    if !target.starts_with(&root) || !is_text || candidate.file_name() != target.file_name() {
        return Err("Select a text artifact in the registered run directory.".into());
    }
    let metadata = target.metadata().map_err(|e| e.to_string())?;
    if !metadata.is_file() || metadata.len() > 1024 * 1024 {
        return Err("Select a text file of at most 1 MiB. Open larger files by path in Studio.".into());
    }
    fs::read_to_string(target).map(Value::String).map_err(|e| e.to_string())
}

fn copy_run_pdf(app: &AppHandle, args: Value) -> Result<Value, String> {
    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct Input {
        run_dir: String,
        dest_path: PathBuf,
    }
    check_new_output(&args, "destPath")?;
    let input: Input = serde_json::from_value(args).map_err(|e| e.to_string())?;
    if !input.dest_path.extension().and_then(|ext| ext.to_str()).is_some_and(|ext| ext.eq_ignore_ascii_case("pdf")) {
        return Err("The destination must end in .pdf.".into());
    }
    let root = registered_run_dir(app, &input.run_dir)?;
    let pdf_path = root.join("final_paper.pdf").canonicalize()
        .map_err(|_| "This run does not have a final PDF.")?;
    if !pdf_path.starts_with(&root) || !pdf_path.is_file() {
        return Err("This run does not have a final PDF.".into());
    }
    let mut source = File::open(pdf_path).map_err(|e| e.to_string())?;
    let mut destination = OpenOptions::new().write(true).create_new(true).open(&input.dest_path)
        .map_err(|e| e.to_string())?;
    if let Err(error) = std::io::copy(&mut source, &mut destination) {
        drop(destination);
        let _ = fs::remove_file(&input.dest_path);
        return Err(error.to_string());
    }
    Ok(json!({"path": input.dest_path}))
}

fn browse_library(app: &AppHandle, args: Value) -> Result<Value, String> {
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Input {
        folder: Option<String>,
        offset: Option<usize>,
        limit: Option<usize>,
    }
    let input: Input = serde_json::from_value(args).map_err(|e| e.to_string())?;
    let items: Vec<_> = commands::list_library_items(app.state())?.into_iter()
        .filter(|item| input.folder.as_ref().is_none_or(|folder| &item.folder == folder))
        .collect();
    let offset = input.offset.unwrap_or(0);
    let limit = input.limit.unwrap_or(25).clamp(1, 100);
    let total = items.len();
    let next = offset.saturating_add(limit);
    let items: Vec<_> = items.into_iter().skip(offset).take(limit).collect();
    Ok(json!({"items": items, "total": total, "nextOffset": (next < total).then_some(next)}))
}

fn get_skill(app: &AppHandle, args: Value) -> Result<Value, String> {
    let id = args["id"].as_str().ok_or("A Studio skill ID is required.")?;
    let skill = crate::skills_store::resolve_skill_by_id(&crate::app_data_dir(app)?, id)?;
    serde_json::to_value(skill).map_err(|e| e.to_string())
}

fn show_view(app: &AppHandle, args: Value) -> Result<Value, String> {
    #[derive(Deserialize, Serialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct View {
        tab: String,
    }
    let view: View = serde_json::from_value(args).map_err(|e| e.to_string())?;
    if !matches!(view.tab.as_str(), "workspace" | "studio" | "library" | "runs" | "artifacts" | "settings") {
        return Err("Unknown Studio view.".into());
    }
    let window = app.get_webview_window("main").ok_or("Studio's main window is closed.")?;
    window.show().map_err(|e| e.to_string())?;
    window.unminimize().map_err(|e| e.to_string())?;
    window.set_focus().map_err(|e| e.to_string())?;
    app.emit_to("main", "paper-orchestra-show-view", &view.tab).map_err(|e| e.to_string())?;
    Ok(json!(view))
}
