use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tokio::sync::{mpsc, Mutex};
use uuid::Uuid;

use crate::db::{
    create_run, finish_run, get_project, get_run, get_settings, insert_run_logs_batch, now_iso,
    update_run_stage, DbState,
};
use crate::diagnostics::mask_log_line;
use crate::env_config::{enriched_path_env, secret_values};
use crate::models::{RunLogEventPayload, RunRecord, RunStatusEventPayload};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub struct ActiveRunHandle {
    pub pid: Option<u32>,
    pub cancelled: Arc<AtomicBool>,
}

pub struct ActiveRunsState {
    pub runs: Mutex<HashMap<String, ActiveRunHandle>>,
}

impl ActiveRunsState {
    pub fn new() -> Self {
        Self {
            runs: Mutex::new(HashMap::new()),
        }
    }
}

#[derive(Serialize)]
struct FigureInfoEntry<'a> {
    name: &'a str,
    caption: &'a str,
}

fn sanitize_slug(input: &str) -> String {
    let mut out = String::new();
    for ch in input.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_lowercase());
        } else if (ch == ' ' || ch == '-' || ch == '_') && !out.ends_with('_') {
            out.push('_');
        }
        if out.len() >= 28 {
            break;
        }
    }
    let trimmed = out.trim_matches('_');
    if trimmed.is_empty() {
        "paper".to_string()
    } else {
        trimmed.to_string()
    }
}

fn detect_stage(current: &str, line: &str, use_plotting: bool) -> String {
    if line.contains("=== Initial Baseline Review ===")
        || line.contains("Starting dedicated formatting loop")
        || line.contains("ContentRefinementAgent")
    {
        return "refinement".to_string();
    }
    if line.contains("Agent SectionWritingAgent starting")
        || line.contains("SectionWritingAgent")
    {
        return "writing".to_string();
    }
    if use_plotting
        && (line.contains("Starting run_plotting_agent")
            || line.contains("Processing figure:")
            || line.contains("Figure processing complete")
            || line.contains("Saved figures/"))
    {
        return "plotting".to_string();
    }
    if line.contains("Starting Fast Hybrid Literature Agent")
        || line.contains("Starting run_literature_agent")
    {
        return "literature".to_string();
    }
    if line.contains("Final PDF saved to:") {
        return "completed".to_string();
    }
    current.to_string()
}

pub async fn start_run(app: AppHandle, project_id: String) -> Result<RunRecord, String> {
    let (settings, project) = {
        let db = app.state::<DbState>();
        let conn = db.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        let settings = get_settings(&conn, &db.db_path)?;
        let project = get_project(&conn, &project_id)?;
        (settings, project)
    };

    if project.idea_markdown.trim().is_empty() {
        return Err("Research Idea cannot be empty before running PaperOrchestra.".to_string());
    }
    if project.experimental_log_markdown.trim().is_empty() {
        return Err("Experimental Log cannot be empty before running PaperOrchestra.".to_string());
    }

    let repo_dir = Path::new(&settings.paper_orchestra_dir);
    let cli_script = repo_dir.join("paper_writing_cli.py");
    if !cli_script.is_file() {
        if let Some(hint) = crate::env_config::offline_volume_hint(repo_dir) {
            return Err(hint);
        }
        return Err(format!(
            "paper_writing_cli.py not found in '{}'. Please verify Settings -> PaperOrchestra Directory.",
            settings.paper_orchestra_dir
        ));
    }

    let template_dir = repo_dir.join("templates").join(&project.template_name);
    if !template_dir.is_dir() {
        return Err(format!(
            "LaTeX template directory not found: {}",
            template_dir.display()
        ));
    }

    let run_id = Uuid::new_v4().to_string();
    let short_id = &run_id[..8];
    let stamp = Utc::now().format("%Y%m%d_%H%M%S");
    let folder_name = format!("{}_{}_{}", sanitize_slug(&project.title), stamp, short_id);
    let run_dir = Path::new(&settings.output_root_dir).join(folder_name);
    let raw_materials_dir = run_dir.join("raw_materials");
    let figures_dir = raw_materials_dir.join("figures");

    fs::create_dir_all(&figures_dir)
        .map_err(|e| format!("Failed to create run directory '{}': {e}", figures_dir.display()))?;

    fs::write(
        raw_materials_dir.join("idea_sparse.md"),
        project.idea_markdown.as_bytes(),
    )
    .map_err(|e| format!("Failed to write idea_sparse.md: {e}"))?;

    fs::write(
        raw_materials_dir.join("experimental_log.md"),
        project.experimental_log_markdown.as_bytes(),
    )
    .map_err(|e| format!("Failed to write experimental_log.md: {e}"))?;

    if !project.figures.is_empty() {
        let mut fig_infos = Vec::new();
        for fig in &project.figures {
            let src = Path::new(&fig.source_path);
            if !src.is_file() {
                return Err(format!(
                    "Attached figure file does not exist: {}",
                    fig.source_path
                ));
            }
            let dst = figures_dir.join(&fig.file_name);
            fs::copy(src, &dst)
                .map_err(|e| format!("Failed to copy figure '{}': {e}", fig.file_name))?;
            fig_infos.push(FigureInfoEntry {
                name: &fig.file_name,
                caption: &fig.caption,
            });
        }
        let info_json = serde_json::to_string_pretty(&fig_infos)
            .map_err(|e| format!("Failed to serialize figures info.json: {e}"))?;
        fs::write(figures_dir.join("info.json"), info_json.as_bytes())
            .map_err(|e| format!("Failed to write figures/info.json: {e}"))?;
    }

    let run_dir_str = run_dir.to_string_lossy().to_string();
    let run_record = RunRecord {
        id: run_id.clone(),
        project_id: project.id.clone(),
        status: "running".to_string(),
        current_stage: "outline".to_string(),
        template_name: project.template_name.clone(),
        writer_model: project.writer_model.clone(),
        reflection_model: project.reflection_model.clone(),
        use_plotting: project.use_plotting,
        run_dir: run_dir_str.clone(),
        pdf_path: None,
        started_at: now_iso(),
        finished_at: None,
        exit_code: None,
        error_message: None,
    };

    let created_run = {
        let db = app.state::<DbState>();
        let conn = db.conn.lock().map_err(|e| format!("DB lock poisoned: {e}"))?;
        create_run(&conn, &run_record)?
    };

    let mut cmd = Command::new(&settings.python_path);
    cmd.arg("-u")
        .arg(&cli_script)
        .arg("--raw_materials_dir")
        .arg(&raw_materials_dir)
        .arg("--output_dir")
        .arg(&run_dir)
        .arg("--latex_template_dir")
        .arg(&template_dir)
        .arg("--idea_filename")
        .arg("idea_sparse.md")
        .arg("--experimental_log_filename")
        .arg("experimental_log.md")
        .arg("--writer_model_name")
        .arg(&project.writer_model)
        .arg("--reflection_model_name")
        .arg(&project.reflection_model)
        .arg("--use_plotting")
        .arg(if project.use_plotting { "true" } else { "false" });

    // Literature up to and including the month the run starts. A value saved
    // on the project by older versions is ignored.
    cmd.arg("--research_cutoff").arg(current_research_cutoff());

    if project.use_plotting {
        cmd.arg("--plotting_model_name")
            .arg(&project.plotting_model)
            .arg("--image_model_name")
            .arg(&project.image_model)
            .arg("--plotting_max_critic_rounds")
            .arg(project.plotting_max_critic_rounds.to_string());
    }

    cmd.current_dir(repo_dir)
        .env("PATH", enriched_path_env())
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);

    #[cfg(target_os = "windows")]
    {
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            let err_msg = format!(
                "Failed to launch Python process '{}': {e}",
                settings.python_path
            );
            {
                let db = app.state::<DbState>();
                if let Ok(conn) = db.conn.lock() {
                    if let Ok(failed_run) = finish_run(
                        &conn,
                        &run_id,
                        "failed",
                        "outline",
                        None,
                        None,
                        Some(&err_msg),
                    ) {
                        let _ = app.emit("run-status", RunStatusEventPayload { run: failed_run });
                    }
                };
            }
            return Err(err_msg);
        }
    };

    let pid = child.id();
    let cancelled = Arc::new(AtomicBool::new(false));
    {
        let active = app.state::<ActiveRunsState>();
        let mut map = active.runs.lock().await;
        map.insert(
            run_id.clone(),
            ActiveRunHandle {
                pid,
                cancelled: Arc::clone(&cancelled),
            },
        );
    }

    let _ = app.emit(
        "run-status",
        RunStatusEventPayload {
            run: created_run.clone(),
        },
    );

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let app_for_task = app.clone();
    let run_id_for_task = run_id.clone();
    let project_id_for_task = project.id.clone();
    let use_plotting = project.use_plotting;
    // API keys from .env / providers.json, masked out of every stored or shown log line.
    let log_secrets = secret_values(&settings.paper_orchestra_dir);

    tauri::async_runtime::spawn(async move {
        let (tx, mut rx) = mpsc::unbounded_channel::<String>();

        if let Some(out) = stdout {
            let tx_out = tx.clone();
            tokio::spawn(async move {
                let mut reader = BufReader::new(out).lines();
                while let Ok(Some(line)) = reader.next_line().await {
                    if tx_out.send(line).is_err() {
                        break;
                    }
                }
            });
        }

        if let Some(err) = stderr {
            let tx_err = tx.clone();
            tokio::spawn(async move {
                let mut reader = BufReader::new(err).lines();
                while let Ok(Some(line)) = reader.next_line().await {
                    if tx_err.send(line).is_err() {
                        break;
                    }
                }
            });
        }
        drop(tx);

        let mut current_stage = "outline".to_string();
        let mut log_buffer: Vec<(String, String, String, String)> = Vec::new();
        let mut ticker = tokio::time::interval(Duration::from_millis(250));
        let mut stream_closed = false;

        while !stream_closed {
            tokio::select! {
                maybe_line = rx.recv() => {
                    match maybe_line {
                        Some(line) => {
                            let next_stage = detect_stage(&current_stage, &line, use_plotting);
                            if next_stage != current_stage {
                                current_stage = next_stage;
                                let db = app_for_task.state::<DbState>();
                                if let Ok(conn) = db.conn.lock() {
                                    let _ = update_run_stage(&conn, &run_id_for_task, &current_stage);
                                    if let Ok(updated) = get_run(&conn, &run_id_for_task) {
                                        let _ = app_for_task.emit("run-status", RunStatusEventPayload { run: updated });
                                    }
                                };
                            }
                            // Stage detection above reads the raw line; what is stored and shown is masked.
                            let line = mask_log_line(&line, &log_secrets);
                            let ts = now_iso();
                            log_buffer.push((run_id_for_task.clone(), current_stage.clone(), line.clone(), ts.clone()));
                            let _ = app_for_task.emit(
                                "run-log",
                                RunLogEventPayload {
                                    run_id: run_id_for_task.clone(),
                                    project_id: project_id_for_task.clone(),
                                    stage: current_stage.clone(),
                                    line,
                                    created_at: ts,
                                },
                            );
                            if log_buffer.len() >= 25 {
                                flush_logs(&app_for_task, &mut log_buffer);
                            }
                        }
                        None => {
                            stream_closed = true;
                        }
                    }
                }
                _ = ticker.tick() => {
                    if !log_buffer.is_empty() {
                        flush_logs(&app_for_task, &mut log_buffer);
                    }
                }
            }
        }

        flush_logs(&app_for_task, &mut log_buffer);

        let wait_result = child.wait().await;
        {
            let active = app_for_task.state::<ActiveRunsState>();
            let mut map = active.runs.lock().await;
            map.remove(&run_id_for_task);
        }

        let was_cancelled = cancelled.load(Ordering::SeqCst);
        let final_pdf = Path::new(&run_dir_str).join("final_paper.pdf");
        let pdf_exists = final_pdf.is_file();
        let pdf_path_opt = if pdf_exists {
            Some(final_pdf.to_string_lossy().to_string())
        } else {
            None
        };

        let (status, stage, exit_code, err_msg) = if was_cancelled {
            (
                "cancelled",
                current_stage.as_str(),
                wait_result.ok().and_then(|s| s.code().map(|c| c as i64)),
                Some("Run stopped by user".to_string()),
            )
        } else {
            match wait_result {
                Ok(exit) => {
                    let code = exit.code().map(|c| c as i64);
                    if exit.success() && pdf_exists {
                        ("succeeded", "completed", code, None)
                    } else if exit.success() {
                        (
                            "failed",
                            current_stage.as_str(),
                            code,
                            Some("Pipeline exited without producing final_paper.pdf. Check logs for details.".to_string()),
                        )
                    } else {
                        (
                            "failed",
                            current_stage.as_str(),
                            code,
                            Some(format!("Process exited with code {}", code.unwrap_or(-1))),
                        )
                    }
                }
                Err(e) => (
                    "failed",
                    current_stage.as_str(),
                    None,
                    Some(format!("Failed waiting for child process: {e}")),
                ),
            }
        };

        let db = app_for_task.state::<DbState>();
        if let Ok(conn) = db.conn.lock() {
            if let Ok(finished) = finish_run(
                &conn,
                &run_id_for_task,
                status,
                stage,
                pdf_path_opt.as_deref(),
                exit_code,
                err_msg.as_deref(),
            ) {
                let _ = app_for_task.emit("run-status", RunStatusEventPayload { run: finished });
            }
        };
    });

    Ok(created_run)
}

fn flush_logs(app: &AppHandle, buffer: &mut Vec<(String, String, String, String)>) {
    if buffer.is_empty() {
        return;
    }
    let db = app.state::<DbState>();
    if let Ok(mut conn) = db.conn.lock() {
        let _ = insert_run_logs_batch(&mut conn, buffer);
    }
    buffer.clear();
}

pub async fn stop_run(app: AppHandle, run_id: String) -> Result<(), String> {
    let pid_opt = {
        let active = app.state::<ActiveRunsState>();
        let map = active.runs.lock().await;
        if let Some(handle) = map.get(&run_id) {
            handle.cancelled.store(true, Ordering::SeqCst);
            handle.pid
        } else {
            return Err("Run is not currently active".to_string());
        }
    };

    if let Some(pid) = pid_opt {
        #[cfg(target_os = "windows")]
        {
            let mut kill_cmd = std::process::Command::new("taskkill");
            kill_cmd
                .args(["/PID", &pid.to_string(), "/T", "/F"])
                .creation_flags(CREATE_NO_WINDOW);
            let _ = kill_cmd.output();
        }
        #[cfg(not(target_os = "windows"))]
        {
            let _ = std::process::Command::new("pkill")
                .args(["-TERM", "-P", &pid.to_string()])
                .output();
            let _ = std::process::Command::new("kill")
                .args(["-TERM", &pid.to_string()])
                .output();
        }
    }

    Ok(())
}

/// The `--research_cutoff` for a run starting now. The pipeline keeps papers
/// published *before* the cutoff month, so this is the month after the current
/// local one: everything up to and including this month (today's newest
/// papers too) is allowed.
fn current_research_cutoff() -> String {
    use chrono::Datelike;
    let now = chrono::Local::now();
    cutoff_after(now.year(), now.month())
}

/// The cutoff that allows papers up to and including `year`-`month`.
fn cutoff_after(year: i32, month: u32) -> String {
    if month >= 12 {
        format!("{}-01", year + 1)
    } else {
        format!("{year}-{:02}", month + 1)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cutoff_includes_the_current_month() {
        assert_eq!(cutoff_after(2026, 11), "2026-12");
        assert_eq!(cutoff_after(2026, 1), "2026-02");
        assert_eq!(cutoff_after(2026, 9), "2026-10");
        assert_eq!(cutoff_after(2026, 12), "2027-01");

        use chrono::Datelike;
        let now = chrono::Local::now();
        let cutoff = current_research_cutoff();
        assert_eq!(cutoff, cutoff_after(now.year(), now.month()));
        assert_eq!(cutoff.len(), 7);
    }
}
