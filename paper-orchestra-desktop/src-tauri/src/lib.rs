mod artifacts;
mod commands;
mod db;
mod diagnostics;
mod env_config;
mod export;
mod live_edit;
mod models;
mod providers;
mod runner;

use std::sync::Mutex;
use tauri::Manager;

use db::{init_db, DbState};
use runner::ActiveRunsState;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let app_data_dir = app
                .path()
                .app_local_data_dir()
                .map_err(|e| format!("Failed to resolve app_local_data_dir: {e}"))?;
            let db_path = app_data_dir.join("paper_orchestra_studio.db");
            let conn = init_db(&db_path)?;

            app.manage(DbState {
                conn: Mutex::new(conn),
                db_path: db_path.to_string_lossy().to_string(),
            });
            app.manage(ActiveRunsState::new());
            app.manage(live_edit::LiveCompileState::default());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_settings,
            commands::update_settings,
            commands::get_env_config,
            commands::save_env_config,
            commands::check_environment,
            commands::check_connections,
            commands::validate_providers_json,
            commands::list_projects,
            commands::get_project,
            commands::create_project,
            commands::save_project,
            commands::delete_project,
            commands::import_cvpr_example,
            commands::add_project_figure,
            commands::update_project_figure_caption,
            commands::delete_project_figure,
            commands::start_run,
            commands::stop_run,
            commands::list_runs,
            commands::delete_run,
            commands::get_run_logs,
            commands::get_run_artifacts_summary,
            commands::read_run_text_artifact,
            commands::export_run_latex,
            commands::read_file_base64,
            commands::open_path_in_system,
            commands::reveal_path_in_explorer,
            commands::resolve_picked_python,
            commands::parse_intake_form,
            commands::run_submission_tool,
            commands::live_edit_open,
            commands::live_edit_read,
            commands::live_edit_save,
            commands::live_edit_restore,
            commands::live_edit_compile,
            commands::live_edit_sync_view,
            commands::live_edit_sync_edit,
        ])
        .run(tauri::generate_context!())
        .expect("error while running PaperOrchestra Studio");
}
