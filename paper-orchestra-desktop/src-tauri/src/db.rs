use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use uuid::Uuid;

use crate::env_config::{
    default_output_root_dir, default_paper_orchestra_dir, default_python_path, expand_home,
};
use crate::models::{
    AddProjectFigureInput, AppSettings, LibraryClaim, LibraryItem, Project, ProjectFigure,
    ProjectSummary, ResearchMemory, ResearchPreferences, RunLogEntry, RunRecord,
    SaveLibraryItemInput, SaveProjectInput, SaveResearchMemoryInput, SaveSshHostInput, SshHost,
    UpdateAppSettingsInput,
};

pub struct DbState {
    pub conn: Mutex<Connection>,
    pub db_path: String,
}

pub fn now_iso() -> String {
    Utc::now().to_rfc3339()
}

pub fn init_db(db_path: &Path) -> Result<Connection, String> {
    if let Some(parent) = db_path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create SQLite directory: {e}"))?;
    }

    let conn = Connection::open(db_path)
        .map_err(|e| format!("Failed to open SQLite database at {}: {e}", db_path.display()))?;

    conn.execute_batch(
        "
        PRAGMA journal_mode = WAL;
        PRAGMA foreign_keys = ON;
        PRAGMA busy_timeout = 5000;

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            template_name TEXT NOT NULL,
            writer_model TEXT NOT NULL,
            reflection_model TEXT NOT NULL,
            plotting_model TEXT NOT NULL,
            image_model TEXT NOT NULL,
            research_cutoff TEXT NOT NULL DEFAULT '',
            use_plotting INTEGER NOT NULL DEFAULT 1,
            plotting_max_critic_rounds INTEGER NOT NULL DEFAULT 3,
            idea_markdown TEXT NOT NULL DEFAULT '',
            experimental_log_markdown TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS project_figures (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            file_name TEXT NOT NULL,
            source_path TEXT NOT NULL,
            caption TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            current_stage TEXT NOT NULL,
            template_name TEXT NOT NULL,
            writer_model TEXT NOT NULL,
            reflection_model TEXT NOT NULL,
            use_plotting INTEGER NOT NULL DEFAULT 1,
            run_dir TEXT NOT NULL,
            pdf_path TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            exit_code INTEGER,
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS run_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            stage TEXT NOT NULL,
            line TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS library_items (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            folder TEXT NOT NULL DEFAULT '默认文献夹',
            title TEXT NOT NULL,
            authors TEXT NOT NULL DEFAULT '',
            year INTEGER,
            venue TEXT NOT NULL DEFAULT '',
            doi TEXT NOT NULL DEFAULT '',
            arxiv_id TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            pdf_path TEXT NOT NULL DEFAULT '',
            bibtex TEXT NOT NULL DEFAULT '',
            gb7714 TEXT NOT NULL DEFAULT '',
            abstract_text TEXT NOT NULL DEFAULT '',
            ai_notes TEXT NOT NULL DEFAULT '',
            key_claims_json TEXT NOT NULL DEFAULT '[]',
            tags_json TEXT NOT NULL DEFAULT '[]',
            read_status TEXT NOT NULL DEFAULT 'unread',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS research_memories (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL DEFAULT 'direction',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ssh_hosts (
            id TEXT PRIMARY KEY,
            alias TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 22,
            username TEXT NOT NULL DEFAULT 'root',
            auth_type TEXT NOT NULL DEFAULT 'key_path',
            key_path TEXT NOT NULL DEFAULT '',
            remote_work_dir TEXT NOT NULL DEFAULT '~',
            notes TEXT NOT NULL DEFAULT '',
            last_status TEXT NOT NULL DEFAULT '',
            last_gpu_info TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_project_figures_project_id ON project_figures(project_id, sort_order);
        CREATE INDEX IF NOT EXISTS idx_runs_project_id ON runs(project_id, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_run_logs_run_id ON run_logs(run_id, id);
        CREATE INDEX IF NOT EXISTS idx_library_items_updated ON library_items(updated_at DESC);
        ",
    )
    .map_err(|e| format!("Failed to initialize SQLite schema: {e}"))?;

    // Recover any interrupted runs from a prior crash or force close.
    let now = now_iso();
    conn.execute(
        "UPDATE runs
         SET status = 'cancelled',
             finished_at = COALESCE(finished_at, ?1),
             error_message = COALESCE(error_message, 'Application closed while run was active')
         WHERE status = 'running'",
        params![now],
    )
    .map_err(|e| format!("Failed to clean up interrupted runs: {e}"))?;

    // Seed default settings if absent.
    let default_repo = default_paper_orchestra_dir();
    let default_py = default_python_path(&default_repo);
    let default_out = default_output_root_dir(&default_repo);

    seed_setting_if_missing(&conn, "paper_orchestra_dir", &default_repo)?;
    seed_setting_if_missing(&conn, "python_path", &default_py)?;
    seed_setting_if_missing(&conn, "output_root_dir", &default_out)?;

    // No example project is seeded any more (Fe, 2026-11-19): a fresh database
    // starts empty. Example projects imported earlier stay until deleted.

    // One-time migration: switch untouched legacy Gemini defaults (with 0 runs) to 'auto'.
    let migrated_auto: Option<String> = conn
        .query_row(
            "SELECT value FROM app_settings WHERE key = 'migrated_auto_default_v1'",
            [],
            |r| r.get(0),
        )
        .optional()
        .map_err(|e| format!("Failed to query auto migration flag: {e}"))?;

    if migrated_auto.is_none() {
        conn.execute(
            "UPDATE projects
             SET writer_model = 'auto',
                 reflection_model = 'auto',
                 plotting_model = 'auto'
             WHERE writer_model = 'gemini-3.1-pro-preview'
               AND reflection_model = 'gemini-3.1-pro-preview'
               AND plotting_model = 'gemini-3.1-pro-preview'
               AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.project_id = projects.id)",
            [],
        )
        .map_err(|e| format!("Failed to migrate untouched projects to auto: {e}"))?;

        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('migrated_auto_default_v1', '1', ?1)",
            params![now_iso()],
        )
        .map_err(|e| format!("Failed to record auto migration flag: {e}"))?;
    }

    // One-time migration (2026-11-19): the image model moved from the workbench
    // to the per-step model panel (PO_TASK_MODEL_IMAGE). Projects that still name
    // the old default switch to 'auto', which resolves to the same model
    // (gemini-3-pro-image with a Gemini key, Matplotlib code without one), so
    // the panel's value applies. Other explicit models are left alone.
    let migrated_image: Option<String> = conn
        .query_row(
            "SELECT value FROM app_settings WHERE key = 'migrated_image_auto_v1'",
            [],
            |r| r.get(0),
        )
        .optional()
        .map_err(|e| format!("Failed to query image migration flag: {e}"))?;

    if migrated_image.is_none() {
        conn.execute(
            "UPDATE projects SET image_model = 'auto'
             WHERE lower(trim(image_model)) IN ('', 'gemini-3-pro-image', 'gemini-3-pro-image-preview',
                                                'gemini:gemini-3-pro-image', 'gemini:gemini-3-pro-image-preview')",
            [],
        )
        .map_err(|e| format!("Failed to migrate image models to auto: {e}"))?;

        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('migrated_image_auto_v1', '1', ?1)",
            params![now_iso()],
        )
        .map_err(|e| format!("Failed to record image migration flag: {e}"))?;
    }

    Ok(conn)
}

fn seed_setting_if_missing(conn: &Connection, key: &str, value: &str) -> Result<(), String> {
    conn.execute(
        "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?1, ?2, ?3)",
        params![key, value, now_iso()],
    )
    .map_err(|e| format!("Failed to seed setting '{key}': {e}"))?;
    Ok(())
}

fn get_setting(conn: &Connection, key: &str) -> Result<Option<String>, String> {
    conn.query_row(
        "SELECT value FROM app_settings WHERE key = ?1",
        params![key],
        |r| r.get(0),
    )
    .optional()
    .map_err(|e| format!("Failed to read setting '{key}': {e}"))
}

pub fn get_settings(conn: &Connection, db_path: &str) -> Result<AppSettings, String> {
    let repo = get_setting(conn, "paper_orchestra_dir")?.unwrap_or_else(default_paper_orchestra_dir);
    let python = get_setting(conn, "python_path")?.unwrap_or_else(|| default_python_path(&repo));
    let output = get_setting(conn, "output_root_dir")?.unwrap_or_else(|| default_output_root_dir(&repo));

    Ok(AppSettings {
        paper_orchestra_dir: repo,
        python_path: python,
        output_root_dir: output,
        db_path: db_path.to_string(),
    })
}

pub fn update_settings(
    conn: &Connection,
    db_path: &str,
    input: &UpdateAppSettingsInput,
) -> Result<AppSettings, String> {
    let now = now_iso();
    let home = std::env::var_os("HOME")
        .filter(|h| !h.is_empty())
        .map(PathBuf::from);
    let home = home.as_deref();
    let repo = expand_home(input.paper_orchestra_dir.trim(), home);
    // Saved as typed (with ~ expanded). The "选择" button maps a resolved
    // macOS pick back to the venv before it reaches this field.
    let python = expand_home(input.python_path.trim(), home);
    let output = expand_home(input.output_root_dir.trim(), home);
    for (key, val) in [
        ("paper_orchestra_dir", repo.as_str()),
        ("python_path", python.as_str()),
        ("output_root_dir", output.as_str()),
    ] {
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?1, ?2, ?3)",
            params![key, val, now],
        )
        .map_err(|e| format!("Failed to save setting '{key}': {e}"))?;
    }
    get_settings(conn, db_path)
}

pub fn list_projects(conn: &Connection) -> Result<Vec<ProjectSummary>, String> {
    let mut stmt = conn
        .prepare(
            "SELECT
                p.id,
                p.title,
                p.template_name,
                p.writer_model,
                p.use_plotting,
                p.updated_at,
                (SELECT COUNT(*) FROM runs r WHERE r.project_id = p.id) AS run_count,
                (SELECT r2.status FROM runs r2 WHERE r2.project_id = p.id ORDER BY r2.started_at DESC LIMIT 1) AS last_run_status
             FROM projects p
             ORDER BY p.updated_at DESC",
        )
        .map_err(|e| format!("Failed to prepare list_projects: {e}"))?;

    let rows = stmt
        .query_map([], |row| {
            Ok(ProjectSummary {
                id: row.get(0)?,
                title: row.get(1)?,
                template_name: row.get(2)?,
                writer_model: row.get(3)?,
                use_plotting: row.get::<_, i64>(4)? != 0,
                updated_at: row.get(5)?,
                run_count: row.get(6)?,
                last_run_status: row.get(7)?,
            })
        })
        .map_err(|e| format!("Failed to execute list_projects: {e}"))?;

    let mut out = Vec::new();
    for item in rows {
        out.push(item.map_err(|e| format!("Failed to read project summary row: {e}"))?);
    }
    Ok(out)
}

pub fn get_project(conn: &Connection, id: &str) -> Result<Project, String> {
    let mut project = conn
        .query_row(
            "SELECT
                id, title, template_name, writer_model, reflection_model,
                plotting_model, image_model, research_cutoff, use_plotting,
                plotting_max_critic_rounds, idea_markdown, experimental_log_markdown,
                created_at, updated_at
             FROM projects WHERE id = ?1",
            params![id],
            |row| {
                Ok(Project {
                    id: row.get(0)?,
                    title: row.get(1)?,
                    template_name: row.get(2)?,
                    writer_model: row.get(3)?,
                    reflection_model: row.get(4)?,
                    plotting_model: row.get(5)?,
                    image_model: row.get(6)?,
                    research_cutoff: row.get(7)?,
                    use_plotting: row.get::<_, i64>(8)? != 0,
                    plotting_max_critic_rounds: row.get(9)?,
                    idea_markdown: row.get(10)?,
                    experimental_log_markdown: row.get(11)?,
                    created_at: row.get(12)?,
                    updated_at: row.get(13)?,
                    figures: Vec::new(),
                })
            },
        )
        .map_err(|e| format!("Project '{id}' not found: {e}"))?;

    project.figures = list_project_figures(conn, id)?;
    Ok(project)
}

fn list_project_figures(conn: &Connection, project_id: &str) -> Result<Vec<ProjectFigure>, String> {
    let mut stmt = conn
        .prepare(
            "SELECT id, project_id, file_name, source_path, caption, sort_order, created_at
             FROM project_figures
             WHERE project_id = ?1
             ORDER BY sort_order ASC, created_at ASC",
        )
        .map_err(|e| format!("Failed to prepare list_project_figures: {e}"))?;

    let rows = stmt
        .query_map(params![project_id], |row| {
            Ok(ProjectFigure {
                id: row.get(0)?,
                project_id: row.get(1)?,
                file_name: row.get(2)?,
                source_path: row.get(3)?,
                caption: row.get(4)?,
                sort_order: row.get(5)?,
                created_at: row.get(6)?,
            })
        })
        .map_err(|e| format!("Failed to query figures: {e}"))?;

    let mut figures = Vec::new();
    for item in rows {
        figures.push(item.map_err(|e| format!("Failed to read figure row: {e}"))?);
    }
    Ok(figures)
}

pub fn create_project(conn: &Connection, title: &str, template_name: &str) -> Result<Project, String> {
    let id = Uuid::new_v4().to_string();
    let now = now_iso();
    let clean_title = if title.trim().is_empty() {
        "Untitled Paper Project"
    } else {
        title.trim()
    };
    let clean_template = if template_name.trim().is_empty() {
        "cvpr2025"
    } else {
        template_name.trim()
    };

    conn.execute(
        "INSERT INTO projects (
            id, title, template_name, writer_model, reflection_model,
            plotting_model, image_model, research_cutoff, use_plotting,
            plotting_max_critic_rounds, idea_markdown, experimental_log_markdown,
            created_at, updated_at
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
        params![
            id,
            clean_title,
            clean_template,
            "auto",
            "auto",
            "auto",
            "auto",
            "",
            1_i64,
            3_i64,
            "",
            "",
            now,
            now
        ],
    )
    .map_err(|e| format!("Failed to create project: {e}"))?;

    get_project(conn, &id)
}

pub fn save_project(conn: &Connection, input: &SaveProjectInput) -> Result<Project, String> {
    let now = now_iso();
    let affected = conn
        .execute(
            "UPDATE projects SET
                title = ?2,
                template_name = ?3,
                writer_model = ?4,
                reflection_model = ?5,
                plotting_model = ?6,
                image_model = ?7,
                research_cutoff = ?8,
                use_plotting = ?9,
                plotting_max_critic_rounds = ?10,
                idea_markdown = ?11,
                experimental_log_markdown = ?12,
                updated_at = ?13
             WHERE id = ?1",
            params![
                input.id,
                input.title.trim(),
                input.template_name.trim(),
                input.writer_model.trim(),
                input.reflection_model.trim(),
                input.plotting_model.trim(),
                input.image_model.trim(),
                input.research_cutoff.trim(),
                if input.use_plotting { 1_i64 } else { 0_i64 },
                input.plotting_max_critic_rounds.clamp(1, 10),
                input.idea_markdown,
                input.experimental_log_markdown,
                now
            ],
        )
        .map_err(|e| format!("Failed to update project: {e}"))?;

    if affected == 0 {
        return Err(format!("Project '{}' does not exist", input.id));
    }

    get_project(conn, &input.id)
}

pub fn delete_project(conn: &Connection, id: &str) -> Result<(), String> {
    conn.execute("DELETE FROM projects WHERE id = ?1", params![id])
        .map_err(|e| format!("Failed to delete project: {e}"))?;
    Ok(())
}

pub fn import_cvpr_example(conn: &Connection, paper_orchestra_dir: &str) -> Result<Project, String> {
    let examples_dir = Path::new(paper_orchestra_dir)
        .join("frontend")
        .join("examples");
    let json_path = examples_dir.join("cvpr_example.json");
    if !json_path.exists() {
        return Err(format!(
            "CVPR example file not found at {}",
            json_path.display()
        ));
    }

    let raw = fs::read_to_string(&json_path)
        .map_err(|e| format!("Failed to read cvpr_example.json: {e}"))?;
    let parsed: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|e| format!("Failed to parse cvpr_example.json: {e}"))?;

    let idea = parsed
        .get("idea")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let log = parsed
        .get("log")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let fig_name = parsed
        .get("figure_path")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let fig_caption = parsed
        .get("figure_caption")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    let id = Uuid::new_v4().to_string();
    let now = now_iso();
    conn.execute(
        "INSERT INTO projects (
            id, title, template_name, writer_model, reflection_model,
            plotting_model, image_model, research_cutoff, use_plotting,
            plotting_max_critic_rounds, idea_markdown, experimental_log_markdown,
            created_at, updated_at
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
        params![
            id,
            "TSAM: Referring Audio-Visual Segmentation (CVPR Example)",
            "cvpr2025",
            "auto",
            "auto",
            "auto",
            "auto",
            "",
            1_i64,
            3_i64,
            idea,
            log,
            now,
            now
        ],
    )
    .map_err(|e| format!("Failed to insert example project: {e}"))?;

    if !fig_name.is_empty() {
        let fig_path = examples_dir.join(fig_name);
        if fig_path.exists() {
            let _ = add_project_figure(
                conn,
                &AddProjectFigureInput {
                    project_id: id.clone(),
                    source_path: fig_path.to_string_lossy().to_string(),
                    caption: fig_caption.to_string(),
                },
            );
        }
    }

    get_project(conn, &id)
}

pub fn add_project_figure(
    conn: &Connection,
    input: &AddProjectFigureInput,
) -> Result<ProjectFigure, String> {
    let source = Path::new(&input.source_path);
    if !source.exists() {
        return Err(format!("Figure file not found: {}", input.source_path));
    }
    let file_name = source
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| "Invalid figure filename".to_string())?
        .to_string();

    let next_order: i64 = conn
        .query_row(
            "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM project_figures WHERE project_id = ?1",
            params![input.project_id],
            |r| r.get(0),
        )
        .unwrap_or(0);

    let fig = ProjectFigure {
        id: Uuid::new_v4().to_string(),
        project_id: input.project_id.clone(),
        file_name,
        source_path: input.source_path.clone(),
        caption: input.caption.clone(),
        sort_order: next_order,
        created_at: now_iso(),
    };

    conn.execute(
        "INSERT INTO project_figures (id, project_id, file_name, source_path, caption, sort_order, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![
            fig.id,
            fig.project_id,
            fig.file_name,
            fig.source_path,
            fig.caption,
            fig.sort_order,
            fig.created_at
        ],
    )
    .map_err(|e| format!("Failed to insert figure: {e}"))?;

    touch_project_updated_at(conn, &input.project_id)?;
    Ok(fig)
}

pub fn update_project_figure_caption(
    conn: &Connection,
    figure_id: &str,
    caption: &str,
) -> Result<(), String> {
    let project_id: Option<String> = conn
        .query_row(
            "SELECT project_id FROM project_figures WHERE id = ?1",
            params![figure_id],
            |r| r.get(0),
        )
        .optional()
        .map_err(|e| format!("Failed to look up figure: {e}"))?;

    conn.execute(
        "UPDATE project_figures SET caption = ?2 WHERE id = ?1",
        params![figure_id, caption],
    )
    .map_err(|e| format!("Failed to update figure caption: {e}"))?;

    if let Some(pid) = project_id {
        touch_project_updated_at(conn, &pid)?;
    }
    Ok(())
}

pub fn delete_project_figure(conn: &Connection, figure_id: &str) -> Result<(), String> {
    let project_id: Option<String> = conn
        .query_row(
            "SELECT project_id FROM project_figures WHERE id = ?1",
            params![figure_id],
            |r| r.get(0),
        )
        .optional()
        .map_err(|e| format!("Failed to look up figure: {e}"))?;

    conn.execute(
        "DELETE FROM project_figures WHERE id = ?1",
        params![figure_id],
    )
    .map_err(|e| format!("Failed to delete figure: {e}"))?;

    if let Some(pid) = project_id {
        touch_project_updated_at(conn, &pid)?;
    }
    Ok(())
}

fn touch_project_updated_at(conn: &Connection, project_id: &str) -> Result<(), String> {
    conn.execute(
        "UPDATE projects SET updated_at = ?2 WHERE id = ?1",
        params![project_id, now_iso()],
    )
    .map_err(|e| format!("Failed to touch project timestamp: {e}"))?;
    Ok(())
}

pub fn create_run(conn: &Connection, run: &RunRecord) -> Result<RunRecord, String> {
    conn.execute(
        "INSERT INTO runs (
            id, project_id, status, current_stage, template_name,
            writer_model, reflection_model, use_plotting, run_dir,
            pdf_path, started_at, finished_at, exit_code, error_message
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
        params![
            run.id,
            run.project_id,
            run.status,
            run.current_stage,
            run.template_name,
            run.writer_model,
            run.reflection_model,
            if run.use_plotting { 1_i64 } else { 0_i64 },
            run.run_dir,
            run.pdf_path,
            run.started_at,
            run.finished_at,
            run.exit_code,
            run.error_message
        ],
    )
    .map_err(|e| format!("Failed to create run record: {e}"))?;
    get_run(conn, &run.id)
}

pub fn update_run_stage(conn: &Connection, run_id: &str, stage: &str) -> Result<(), String> {
    conn.execute(
        "UPDATE runs SET current_stage = ?2 WHERE id = ?1 AND status = 'running'",
        params![run_id, stage],
    )
    .map_err(|e| format!("Failed to update run stage: {e}"))?;
    Ok(())
}

pub fn finish_run(
    conn: &Connection,
    run_id: &str,
    status: &str,
    current_stage: &str,
    pdf_path: Option<&str>,
    exit_code: Option<i64>,
    error_message: Option<&str>,
) -> Result<RunRecord, String> {
    conn.execute(
        "UPDATE runs SET
            status = ?2,
            current_stage = ?3,
            pdf_path = COALESCE(?4, pdf_path),
            finished_at = ?5,
            exit_code = ?6,
            error_message = ?7
         WHERE id = ?1",
        params![
            run_id,
            status,
            current_stage,
            pdf_path,
            now_iso(),
            exit_code,
            error_message
        ],
    )
    .map_err(|e| format!("Failed to finalize run record: {e}"))?;
    get_run(conn, run_id)
}

pub fn get_run(conn: &Connection, run_id: &str) -> Result<RunRecord, String> {
    conn.query_row(
        "SELECT
            id, project_id, status, current_stage, template_name,
            writer_model, reflection_model, use_plotting, run_dir,
            pdf_path, started_at, finished_at, exit_code, error_message
         FROM runs WHERE id = ?1",
        params![run_id],
        map_run_row,
    )
    .map_err(|e| format!("Run '{run_id}' not found: {e}"))
}

pub fn list_runs(conn: &Connection, project_id: &str) -> Result<Vec<RunRecord>, String> {
    let mut stmt = conn
        .prepare(
            "SELECT
                id, project_id, status, current_stage, template_name,
                writer_model, reflection_model, use_plotting, run_dir,
                pdf_path, started_at, finished_at, exit_code, error_message
             FROM runs
             WHERE project_id = ?1
             ORDER BY started_at DESC",
        )
        .map_err(|e| format!("Failed to prepare list_runs: {e}"))?;

    let rows = stmt
        .query_map(params![project_id], map_run_row)
        .map_err(|e| format!("Failed to execute list_runs: {e}"))?;

    let mut runs = Vec::new();
    for item in rows {
        runs.push(item.map_err(|e| format!("Failed to read run row: {e}"))?);
    }
    Ok(runs)
}

fn map_run_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<RunRecord> {
    Ok(RunRecord {
        id: row.get(0)?,
        project_id: row.get(1)?,
        status: row.get(2)?,
        current_stage: row.get(3)?,
        template_name: row.get(4)?,
        writer_model: row.get(5)?,
        reflection_model: row.get(6)?,
        use_plotting: row.get::<_, i64>(7)? != 0,
        run_dir: row.get(8)?,
        pdf_path: row.get(9)?,
        started_at: row.get(10)?,
        finished_at: row.get(11)?,
        exit_code: row.get(12)?,
        error_message: row.get(13)?,
    })
}

pub fn delete_run(conn: &Connection, run_id: &str) -> Result<(), String> {
    conn.execute("DELETE FROM runs WHERE id = ?1", params![run_id])
        .map_err(|e| format!("Failed to delete run: {e}"))?;
    Ok(())
}

pub fn insert_run_logs_batch(
    conn: &mut Connection,
    entries: &[(String, String, String, String)],
) -> Result<(), String> {
    if entries.is_empty() {
        return Ok(());
    }
    let tx = conn
        .transaction()
        .map_err(|e| format!("Failed to begin log transaction: {e}"))?;
    {
        let mut stmt = tx
            .prepare_cached(
                "INSERT INTO run_logs (run_id, stage, line, created_at) VALUES (?1, ?2, ?3, ?4)",
            )
            .map_err(|e| format!("Failed to prepare log insert: {e}"))?;
        for (run_id, stage, line, created_at) in entries {
            stmt.execute(params![run_id, stage, line, created_at])
                .map_err(|e| format!("Failed to insert run log: {e}"))?;
        }
    }
    tx.commit()
        .map_err(|e| format!("Failed to commit log transaction: {e}"))?;
    Ok(())
}

pub fn get_run_logs(
    conn: &Connection,
    run_id: &str,
    after_id: Option<i64>,
    limit: Option<i64>,
) -> Result<Vec<RunLogEntry>, String> {
    let min_id = after_id.unwrap_or(0);
    let max_rows = limit.unwrap_or(5000).clamp(1, 20000);
    let mut stmt = conn
        .prepare(
            "SELECT id, run_id, stage, line, created_at
             FROM run_logs
             WHERE run_id = ?1 AND id > ?2
             ORDER BY id ASC
             LIMIT ?3",
        )
        .map_err(|e| format!("Failed to prepare get_run_logs: {e}"))?;

    let rows = stmt
        .query_map(params![run_id, min_id, max_rows], |row| {
            Ok(RunLogEntry {
                id: row.get(0)?,
                run_id: row.get(1)?,
                stage: row.get(2)?,
                line: row.get(3)?,
                created_at: row.get(4)?,
            })
        })
        .map_err(|e| format!("Failed to query run logs: {e}"))?;

    let mut out = Vec::new();
    for item in rows {
        out.push(item.map_err(|e| format!("Failed to read log row: {e}"))?);
    }
    Ok(out)
}

pub fn resume_run_record(conn: &Connection, run_id: &str) -> Result<RunRecord, String> {
    conn.execute(
        "UPDATE runs
         SET status = 'running',
             finished_at = NULL,
             exit_code = NULL,
             error_message = NULL
         WHERE id = ?1",
        params![run_id],
    )
    .map_err(|e| format!("Failed to reset run for resume: {e}"))?;
    get_run(conn, run_id)
}

pub fn list_library_items(conn: &Connection) -> Result<Vec<LibraryItem>, String> {
    let mut stmt = conn
        .prepare(
            "SELECT id, project_id, folder, title, authors, year, venue, doi, arxiv_id, url,
                    pdf_path, bibtex, gb7714, abstract_text, ai_notes, key_claims_json, tags_json,
                    read_status, created_at, updated_at
             FROM library_items
             ORDER BY updated_at DESC",
        )
        .map_err(|e| format!("Failed to prepare list_library_items: {e}"))?;

    let rows = stmt
        .query_map([], |r| {
            let claims_raw: String = r.get(15)?;
            let tags_raw: String = r.get(16)?;
            Ok(LibraryItem {
                id: r.get(0)?,
                project_id: r.get(1)?,
                folder: r.get(2)?,
                title: r.get(3)?,
                authors: r.get(4)?,
                year: r.get(5)?,
                venue: r.get(6)?,
                doi: r.get(7)?,
                arxiv_id: r.get(8)?,
                url: r.get(9)?,
                pdf_path: r.get(10)?,
                bibtex: r.get(11)?,
                gb7714: r.get(12)?,
                abstract_text: r.get(13)?,
                ai_notes: r.get(14)?,
                key_claims: serde_json::from_str::<Vec<LibraryClaim>>(&claims_raw).unwrap_or_default(),
                tags: serde_json::from_str::<Vec<String>>(&tags_raw).unwrap_or_default(),
                read_status: r.get(17)?,
                created_at: r.get(18)?,
                updated_at: r.get(19)?,
            })
        })
        .map_err(|e| format!("Failed to query library_items: {e}"))?;

    let mut out = Vec::new();
    for item in rows {
        out.push(item.map_err(|e| format!("Failed to read library row: {e}"))?);
    }
    Ok(out)
}

pub fn save_library_item(conn: &Connection, input: &SaveLibraryItemInput) -> Result<LibraryItem, String> {
    let title = input.title.trim();
    if title.is_empty() {
        return Err("文献标题不能为空。".to_string());
    }
    let now = now_iso();
    let id = input
        .id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    let created_at: String = conn
        .query_row("SELECT created_at FROM library_items WHERE id = ?1", params![id], |r| r.get(0))
        .optional()
        .map_err(|e| format!("Failed to check library_item: {e}"))?
        .unwrap_or_else(|| now.clone());

    let folder = if input.folder.trim().is_empty() {
        "默认文献夹"
    } else {
        input.folder.trim()
    };
    let read_status = if input.read_status.trim().is_empty() {
        "unread"
    } else {
        input.read_status.trim()
    };
    let claims_json = serde_json::to_string(&input.key_claims).unwrap_or_else(|_| "[]".to_string());
    let tags_json = serde_json::to_string(&input.tags).unwrap_or_else(|_| "[]".to_string());

    conn.execute(
        "INSERT OR REPLACE INTO library_items
            (id, project_id, folder, title, authors, year, venue, doi, arxiv_id, url,
             pdf_path, bibtex, gb7714, abstract_text, ai_notes, key_claims_json, tags_json,
             read_status, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20)",
        params![
            id,
            input.project_id,
            folder,
            title,
            input.authors.trim(),
            input.year,
            input.venue.trim(),
            input.doi.trim(),
            input.arxiv_id.trim(),
            input.url.trim(),
            input.pdf_path.trim(),
            input.bibtex.trim(),
            input.gb7714.trim(),
            input.abstract_text.trim(),
            input.ai_notes.trim(),
            claims_json,
            tags_json,
            read_status,
            created_at,
            now
        ],
    )
    .map_err(|e| format!("Failed to save library item: {e}"))?;

    list_library_items(conn)?
        .into_iter()
        .find(|x| x.id == id)
        .ok_or_else(|| "Saved library item not found".to_string())
}

pub fn delete_library_item(conn: &Connection, id: &str) -> Result<(), String> {
    conn.execute("DELETE FROM library_items WHERE id = ?1", params![id])
        .map_err(|e| format!("Failed to delete library item: {e}"))?;
    Ok(())
}

pub fn list_research_memories(conn: &Connection) -> Result<Vec<ResearchMemory>, String> {
    let mut stmt = conn
        .prepare("SELECT id, category, title, content, enabled, updated_at FROM research_memories ORDER BY updated_at DESC")
        .map_err(|e| format!("Failed to prepare list_research_memories: {e}"))?;
    let rows = stmt
        .query_map([], |r| {
            Ok(ResearchMemory {
                id: r.get(0)?,
                category: r.get(1)?,
                title: r.get(2)?,
                content: r.get(3)?,
                enabled: r.get::<_, i64>(4)? != 0,
                updated_at: r.get(5)?,
            })
        })
        .map_err(|e| format!("Failed to query research_memories: {e}"))?;
    let mut out = Vec::new();
    for r in rows {
        out.push(r.map_err(|e| format!("Failed to read research_memory: {e}"))?);
    }
    Ok(out)
}

pub fn save_research_memory(conn: &Connection, input: &SaveResearchMemoryInput) -> Result<ResearchMemory, String> {
    if input.title.trim().is_empty() || input.content.trim().is_empty() {
        return Err("记忆标题与内容均不能为空。".to_string());
    }
    let id = input
        .id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    let category = if input.category.trim().is_empty() {
        "direction"
    } else {
        input.category.trim()
    };
    let now = now_iso();
    conn.execute(
        "INSERT OR REPLACE INTO research_memories (id, category, title, content, enabled, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![
            id,
            category,
            input.title.trim(),
            input.content.trim(),
            if input.enabled { 1_i64 } else { 0_i64 },
            now
        ],
    )
    .map_err(|e| format!("Failed to save research_memory: {e}"))?;
    list_research_memories(conn)?
        .into_iter()
        .find(|m| m.id == id)
        .ok_or_else(|| "Saved memory not found".to_string())
}

pub fn delete_research_memory(conn: &Connection, id: &str) -> Result<(), String> {
    conn.execute("DELETE FROM research_memories WHERE id = ?1", params![id])
        .map_err(|e| format!("Failed to delete research_memory: {e}"))?;
    Ok(())
}

pub fn get_research_preferences(conn: &Connection) -> Result<ResearchPreferences, String> {
    if let Some(raw) = get_setting(conn, "research_preferences")? {
        if let Ok(prefs) = serde_json::from_str::<ResearchPreferences>(&raw) {
            return Ok(prefs);
        }
    }
    Ok(ResearchPreferences::default())
}

pub fn save_research_preferences(
    conn: &Connection,
    prefs: &ResearchPreferences,
) -> Result<ResearchPreferences, String> {
    let serialized = serde_json::to_string(prefs)
        .map_err(|e| format!("Failed to serialize research_preferences: {e}"))?;
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('research_preferences', ?1, ?2)",
        params![serialized, now_iso()],
    )
    .map_err(|e| format!("Failed to save research_preferences: {e}"))?;
    Ok(prefs.clone())
}

pub fn build_research_preferences_markdown(conn: &Connection) -> Result<String, String> {
    let prefs = get_research_preferences(conn)?;
    let memories = list_research_memories(conn)?;
    let mut lines: Vec<String> = Vec::new();
    if !prefs.output_language.trim().is_empty() {
        lines.push(format!("- Preferred output language: {}", prefs.output_language.trim()));
    }
    if !prefs.citation_style.trim().is_empty() {
        lines.push(format!("- Preferred citation standard: {}", prefs.citation_style.trim()));
    }
    if !prefs.delivery_format.trim().is_empty() {
        lines.push(format!("- Preferred deliverable format: {}", prefs.delivery_format.trim()));
    }
    if !prefs.custom_rules.trim().is_empty() {
        lines.push(format!("- Custom standing rules:\n{}", prefs.custom_rules.trim()));
    }
    let enabled_mem: Vec<&ResearchMemory> = memories.iter().filter(|m| m.enabled).collect();
    if !enabled_mem.is_empty() {
        lines.push("\n#### Enabled Long-Term Research Memories".to_string());
        for m in enabled_mem {
            lines.push(format!("- [{}] **{}**: {}", m.category, m.title, m.content));
        }
    }
    Ok(lines.join("\n"))
}

pub fn list_ssh_hosts(conn: &Connection) -> Result<Vec<SshHost>, String> {
    let mut stmt = conn
        .prepare(
            "SELECT id, alias, host, port, username, auth_type, key_path, remote_work_dir,
                    notes, last_status, last_gpu_info, created_at, updated_at
             FROM ssh_hosts ORDER BY updated_at DESC",
        )
        .map_err(|e| format!("Failed to prepare list_ssh_hosts: {e}"))?;
    let rows = stmt
        .query_map([], map_ssh_row)
        .map_err(|e| format!("Failed to query ssh_hosts: {e}"))?;
    let mut out = Vec::new();
    for r in rows {
        out.push(r.map_err(|e| format!("Failed to read ssh_host row: {e}"))?);
    }
    Ok(out)
}

fn map_ssh_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<SshHost> {
    Ok(SshHost {
        id: r.get(0)?,
        alias: r.get(1)?,
        host: r.get(2)?,
        port: r.get(3)?,
        username: r.get(4)?,
        auth_type: r.get(5)?,
        key_path: r.get(6)?,
        remote_work_dir: r.get(7)?,
        notes: r.get(8)?,
        last_status: r.get(9)?,
        last_gpu_info: r.get(10)?,
        created_at: r.get(11)?,
        updated_at: r.get(12)?,
    })
}

pub fn get_ssh_host(conn: &Connection, id: &str) -> Result<SshHost, String> {
    conn.query_row(
        "SELECT id, alias, host, port, username, auth_type, key_path, remote_work_dir,
                notes, last_status, last_gpu_info, created_at, updated_at
         FROM ssh_hosts WHERE id = ?1",
        params![id],
        map_ssh_row,
    )
    .map_err(|e| format!("找不到计算主机 '{id}': {e}"))
}

pub fn save_ssh_host(conn: &Connection, id: &str, input: &SaveSshHostInput, resolved_key_path: &str) -> Result<SshHost, String> {
    let now = now_iso();
    let created_at: String = conn
        .query_row("SELECT created_at FROM ssh_hosts WHERE id = ?1", params![id], |r| r.get(0))
        .optional()
        .map_err(|e| format!("Failed to check ssh_host: {e}"))?
        .unwrap_or_else(|| now.clone());
    let (last_status, last_gpu_info): (String, String) = conn
        .query_row("SELECT last_status, last_gpu_info FROM ssh_hosts WHERE id = ?1", params![id], |r| {
            Ok((r.get(0)?, r.get(1)?))
        })
        .optional()
        .map_err(|e| format!("Failed to query ssh_host status: {e}"))?
        .unwrap_or_default();
    let auth_type = if input.auth_type.trim().is_empty() { "key_path" } else { input.auth_type.trim() };
    let remote_dir = if input.remote_work_dir.trim().is_empty() { "~" } else { input.remote_work_dir.trim() };
    conn.execute(
        "INSERT OR REPLACE INTO ssh_hosts
            (id, alias, host, port, username, auth_type, key_path, remote_work_dir,
             notes, last_status, last_gpu_info, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
        params![
            id,
            input.alias.trim(),
            input.host.trim(),
            input.port,
            input.username.trim(),
            auth_type,
            resolved_key_path.trim(),
            remote_dir,
            input.notes.trim(),
            last_status,
            last_gpu_info,
            created_at,
            now
        ],
    )
    .map_err(|e| format!("Failed to save ssh_host: {e}"))?;
    get_ssh_host(conn, id)
}

pub fn update_ssh_host_status(conn: &Connection, id: &str, status: &str, gpu_info: &str) -> Result<(), String> {
    conn.execute(
        "UPDATE ssh_hosts SET last_status = ?2, last_gpu_info = ?3, updated_at = ?4 WHERE id = ?1",
        params![id, status, gpu_info, now_iso()],
    )
    .map_err(|e| format!("Failed to update ssh_host status: {e}"))?;
    Ok(())
}

pub fn delete_ssh_host(conn: &Connection, id: &str) -> Result<(), String> {
    conn.execute("DELETE FROM ssh_hosts WHERE id = ?1", params![id])
        .map_err(|e| format!("Failed to delete ssh_host: {e}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_db_path(name: &str) -> std::path::PathBuf {
        std::env::temp_dir()
            .join("paper_orchestra_desktop_tests")
            .join(format!("{name}_{}.db", Uuid::new_v4()))
    }

    #[test]
    fn image_model_default_moves_to_auto_once() {
        let db_path = temp_db_path("image_auto");
        let conn = init_db(&db_path).expect("init_db");
        assert_eq!(create_project(&conn, "New", "").expect("create").image_model, "auto");
        conn.execute_batch(
            "INSERT INTO projects (id, title, template_name, writer_model, reflection_model, plotting_model,
                                   image_model, created_at, updated_at)
             VALUES ('old', 'Old', 'cvpr2025', 'auto', 'auto', 'auto', 'gemini-3-pro-image', 't', 't'),
                    ('pre', 'Pre', 'cvpr2025', 'auto', 'auto', 'auto', 'gemini-3-pro-image-preview', 't', 't'),
                    ('own', 'Own', 'cvpr2025', 'auto', 'auto', 'auto', 'openai:gpt-image-1', 't', 't');
             DELETE FROM app_settings WHERE key = 'migrated_image_auto_v1';",
        )
        .expect("seed");
        drop(conn);
        let conn = init_db(&db_path).expect("init_db again");
        let image = |id: &str| -> String {
            conn.query_row("SELECT image_model FROM projects WHERE id = ?1", params![id], |r| r.get(0))
                .expect("row")
        };
        assert_eq!(image("old"), "auto");
        assert_eq!(image("pre"), "auto");
        assert_eq!(image("own"), "openai:gpt-image-1"); // a chosen model stays
        drop(conn);
        let _ = std::fs::remove_file(&db_path);
    }

    #[test]
    fn sqlite_crud_and_cascade_work() {
        let db_path = temp_db_path("crud");
        let mut conn = init_db(&db_path).expect("init_db");

        // Create and save project
        let created = create_project(&conn, "Test Paper", "iclr2025").expect("create_project");
        assert_eq!(created.title, "Test Paper");
        assert_eq!(created.template_name, "iclr2025");

        let saved = save_project(
            &conn,
            &SaveProjectInput {
                id: created.id.clone(),
                title: "Updated Paper".to_string(),
                template_name: "cvpr2025".to_string(),
                writer_model: "gemini-3.1-pro-preview".to_string(),
                reflection_model: "gemini-3.1-pro-preview".to_string(),
                plotting_model: "gemini-3.1-pro-preview".to_string(),
                image_model: "gemini-3-pro-image-preview".to_string(),
                research_cutoff: "2025-10".to_string(),
                use_plotting: false,
                plotting_max_critic_rounds: 2,
                idea_markdown: "# Idea".to_string(),
                experimental_log_markdown: "# Log".to_string(),
            },
        )
        .expect("save_project");
        assert_eq!(saved.title, "Updated Paper");
        assert!(!saved.use_plotting);

        // Create run and insert batched logs
        let run = create_run(
            &conn,
            &RunRecord {
                id: "run-1".to_string(),
                project_id: created.id.clone(),
                status: "running".to_string(),
                current_stage: "outline".to_string(),
                template_name: "cvpr2025".to_string(),
                writer_model: "gemini-3.1-pro-preview".to_string(),
                reflection_model: "gemini-3.1-pro-preview".to_string(),
                use_plotting: false,
                run_dir: "C:/tmp/run-1".to_string(),
                pdf_path: None,
                started_at: now_iso(),
                finished_at: None,
                exit_code: None,
                error_message: None,
            },
        )
        .expect("create_run");
        assert_eq!(run.status, "running");

        insert_run_logs_batch(
            &mut conn,
            &[
                ("run-1".to_string(), "outline".to_string(), "line 1".to_string(), now_iso()),
                ("run-1".to_string(), "writing".to_string(), "line 2".to_string(), now_iso()),
            ],
        )
        .expect("insert_run_logs_batch");

        let logs = get_run_logs(&conn, "run-1", None, None).expect("get_run_logs");
        assert_eq!(logs.len(), 2);
        assert_eq!(logs[1].stage, "writing");

        // Deleting project cascades to runs and run_logs
        delete_project(&conn, &created.id).expect("delete_project");
        let remaining_logs = get_run_logs(&conn, "run-1", None, None).expect("get_run_logs after delete");
        assert!(remaining_logs.is_empty());

        drop(conn);
        let _ = fs::remove_file(&db_path);
    }

    #[test]
    fn library_memory_and_ssh_hosts_crud_work() {
        let db_path = temp_db_path("clawsgo_tables");
        let conn = init_db(&db_path).expect("init_db");

        let lib = save_library_item(
            &conn,
            &SaveLibraryItemInput {
                title: "GrabCut Interactive Foreground Extraction".to_string(),
                authors: "Rother, Kolmogorov, Blake".to_string(),
                year: Some(2004),
                venue: "SIGGRAPH".to_string(),
                doi: "10.1145/1015706.1015720".to_string(),
                tags: vec!["CV".to_string(), "Segmentation".to_string()],
                read_status: "full_read".to_string(),
                ..Default::default()
            },
        )
        .expect("save_library_item");
        assert_eq!(list_library_items(&conn).unwrap().len(), 1);
        delete_library_item(&conn, &lib.id).unwrap();
        assert!(list_library_items(&conn).unwrap().is_empty());

        save_research_preferences(
            &conn,
            &ResearchPreferences {
                output_language: "zh".to_string(),
                citation_style: "GB/T 7714".to_string(),
                delivery_format: "latex".to_string(),
                custom_rules: "Always include effect sizes".to_string(),
            },
        )
        .unwrap();
        let mem = save_research_memory(
            &conn,
            &SaveResearchMemoryInput {
                id: None,
                category: "direction".to_string(),
                title: "Catalysis".to_string(),
                content: "Prefer DFT B3LYP benchmarks".to_string(),
                enabled: true,
            },
        )
        .unwrap();
        let md = build_research_preferences_markdown(&conn).unwrap();
        assert!(md.contains("GB/T 7714"));
        assert!(md.contains("DFT B3LYP"));
        delete_research_memory(&conn, &mem.id).unwrap();

        let host = save_ssh_host(
            &conn,
            "h-1",
            &SaveSshHostInput {
                id: None,
                alias: "555".to_string(),
                host: "region.autodl.pro".to_string(),
                port: 31245,
                username: "root".to_string(),
                auth_type: "key_path".to_string(),
                key_path: "/tmp/id".to_string(),
                private_key_pem: None,
                remote_work_dir: "/root/exp".to_string(),
                notes: "".to_string(),
            },
            "/tmp/id",
        )
        .unwrap();
        assert_eq!(list_ssh_hosts(&conn).unwrap().len(), 1);
        update_ssh_host_status(&conn, &host.id, "online", "RTX 4090").unwrap();
        assert_eq!(get_ssh_host(&conn, &host.id).unwrap().last_gpu_info, "RTX 4090");
        delete_ssh_host(&conn, &host.id).unwrap();
        assert!(list_ssh_hosts(&conn).unwrap().is_empty());

        drop(conn);
        let _ = fs::remove_file(&db_path);
    }
}

