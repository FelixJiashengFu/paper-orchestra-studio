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
mod skills_store;
mod ssh_hosts;

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
            app.manage(diagnostics::ActiveStudioJobsState::new());
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
            commands::resume_run,
            commands::cancel_studio_job,
            commands::run_figure_studio,
            commands::run_data_profile,
            commands::run_data_analysis,
            commands::run_sop_workflow,
            commands::run_custom_skill,
            commands::list_library_items,
            commands::save_library_item,
            commands::delete_library_item,
            commands::run_library_tool,
            commands::run_latex_copilot,
            commands::list_research_memories,
            commands::save_research_memory,
            commands::delete_research_memory,
            commands::get_research_preferences,
            commands::save_research_preferences,
            commands::list_ssh_hosts,
            commands::save_ssh_host,
            commands::delete_ssh_host,
            commands::test_ssh_host,
            commands::exec_ssh_command,
            commands::scp_transfer,
            commands::list_skills,
            commands::save_skill,
            commands::delete_skill,
            commands::import_skill_from_file,
            commands::import_skill_from_url,
            commands::get_export_environment,
            commands::export_markdown_document,
            commands::export_markdown_to_google_drive,
        ])
        .run(tauri::generate_context!())
        .expect("error while running PaperOrchestra Studio");
}
