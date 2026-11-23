//! 「编辑并实时预览」: an Overleaf-like editor for one run's paper.
//!
//! Which files: the run's export source folder, exactly what the Overleaf
//! export uses ([`export::export_source_dir`], main file
//! [`export::pick_main_tex`]). The editable files are the `.tex` / `.bib`
//! files the export would ship ([`export::collect_export_files`]): hidden
//! files and folders, symlinks, review folders and the other top-level
//! documents (earlier drafts, template copies) are left out, so editing can
//! never change which document the export picks as the paper.
//!
//! Writes, all refused while the run is still running:
//! * only `.tex` / `.bib` files inside the source folder (after resolving
//!   symlinks);
//! * the first save of a file copies the original to
//!   `<run>/.edit_backup/<sourceRel>/<file>` (never overwritten afterwards),
//!   which [`restore_file`] copies back;
//! * every write is atomic (temp file in the same folder + rename) and is
//!   refused when the file changed on disk since the editor read it. On
//!   Windows a save first waits for a running compile, and a file another
//!   program still holds open is written in place.
//!
//! Builds: `latexmk` runs in the source folder with its output in
//! `<run>/.live_preview/<sourceRel>/`, outside the source folder, so no new
//! PDF or build file appears next to the sources (a PDF there would change
//! what `pick_main_tex` picks). SyncTeX maps between source lines and PDF
//! positions with the `synctex` command-line tool.
//!
//! Every entry point first checks that the folder is a run the app knows
//! (its canonical path equals the canonical `run_dir` of a row in `runs`).
//! The core functions take plain paths / rows so they can be tested without
//! Tauri or the database.

use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Component, Path, PathBuf};
use std::process::Stdio;
use std::sync::Mutex as StdMutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use rusqlite::Connection;
use serde::Serialize;
use tokio::io::AsyncReadExt;
use tokio::process::Command;
use tokio::sync::Mutex;
use uuid::Uuid;

use crate::artifacts::resolve_safe_subpath;
use crate::db::DbState;
use crate::env_config::{enriched_path_env, strip_windows_unc};
use crate::export;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Originals of edited files, under the run root.
pub const BACKUP_DIR: &str = ".edit_backup";
/// latexmk output folder, under the run root (never inside the source folder).
pub const PREVIEW_DIR: &str = ".live_preview";

const COMPILE_TIMEOUT: Duration = Duration::from_secs(180);
const SYNCTEX_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_ISSUES: usize = 50;
const LOG_TAIL_LINES: usize = 40;
const MAX_TAIL_LINE_CHARS: usize = 400;
const MAX_MESSAGE_CHARS: usize = 300;
const CONTEXT_CHARS: usize = 60;
/// Larger files are not opened in the editor.
const MAX_EDIT_BYTES: u64 = 16 * 1024 * 1024;
/// TeX's default line width; a log written with it wraps every longer line.
const TEX_WRAP: usize = 79;
/// TeX Live reads these from the environment: no wrapped log lines (so long
/// paths stay on one line) and a longer error context.
const TEX_ENV: &[(&str, &str)] = &[
    ("max_print_line", "10000"),
    ("error_line", "254"),
    ("half_error_line", "238"),
];
const EDITABLE_EXTS: &[&str] = &["tex", "bib"];

const ERR_RUN_ACTIVE: &str = "论文还在生成中，现在只能看，不能改。等运行结束后再编辑。";
const ERR_NOT_EDITABLE: &str = "只能编辑 .tex 和 .bib 文件。";
const ERR_OUTSIDE: &str = "这个文件不在论文文件夹里，不能打开。";
const ERR_CONFLICT: &str =
    "这个文件在别处被改过（比如用别的编辑器）。请关掉编辑页重新打开，免得互相覆盖。";
const ERR_NOT_UTF8: &str = "这个文件不是 UTF-8 编码，不能在这里编辑。";
const ERR_TOO_BIG: &str = "文件太大（超过 16 MB），不能在这里编辑。";
const ERR_NO_BACKUP: &str = "这个文件还没有在这里改过，没有可以恢复的原稿。";
const ERR_NO_MAIN: &str = "没找到论文的主 .tex 文件（带 \\documentclass 的那个）。";
const ERR_MAIN_NOT_TEX: &str = "主文件必须是 .tex 文件。";
const ERR_NOT_PAPER_FILE: &str = "这个文件不是论文正在用的源文件（可能是旧草稿），不能在这里改。";
const ERR_LATEXMK_MISSING: &str = "没找到 latexmk。请先安装 TeX（Mac 上是 MacTeX）。";
const ERR_SYNCTEX_MISSING: &str = "没找到 synctex。请先安装 TeX（Mac 上是 MacTeX）。";
/// The preview PDF or its SyncTeX file is missing (pdfTeX deletes the
/// SyncTeX file after a build that ships no pages).
const ERR_SYNC_STALE: &str = "上次编译没有生成新的 PDF，修好错误、重新编译后才能定位。";
const ERR_SYNC_TIMEOUT: &str = "定位用时太长，已经停止。请再试一次。";
/// What pdfTeX writes to the log after a build that shipped pages.
const PDF_WRITTEN_MARK: &str = "Output written on";
/// Upper bound for a save waiting on a running compile (Windows); the
/// compile itself is stopped after [`COMPILE_TIMEOUT`].
const SAVE_WAIT_LIMIT: Duration = Duration::from_secs(240);

/// Serialises saves and restores app-wide, so two writes can never both
/// decide that a file has no backup yet.
static WRITE_LOCK: StdMutex<()> = StdMutex::new(());

/// Managed state: one compile at a time, app-wide.
#[derive(Default)]
pub struct LiveCompileState {
    lock: Mutex<()>,
}

/// One row of the `runs` table: what the security check needs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunRow {
    pub run_dir: String,
    pub status: String,
}

/// A checked run: canonical paths only.
#[derive(Debug, Clone)]
pub struct EditContext {
    pub run_dir: PathBuf,
    pub source_dir: PathBuf,
    /// Source folder relative to the run, `/` separators.
    pub source_rel: String,
    pub run_active: bool,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct LiveEditSession {
    pub run_dir: String,
    pub source_dir: String,
    pub source_rel: String,
    pub main_file: String,
    pub files: Vec<String>,
    pub backed_up: Vec<String>,
    pub run_active: bool,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct LiveEditFile {
    pub file: String,
    pub content: String,
    pub mtime_ms: u64,
    /// The file's original is kept in the backup folder (after the call).
    pub has_backup: bool,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum IssueSeverity {
    Error,
    Warning,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct LiveCompileIssue {
    /// Relative to the source folder; only for an editable file in it.
    pub file: Option<String>,
    pub line: Option<u32>,
    pub message: String,
    pub severity: IssueSeverity,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct LiveCompileResult {
    pub ok: bool,
    pub pdf_path: Option<String>,
    pub issues: Vec<LiveCompileIssue>,
    pub warnings: u32,
    pub duration_ms: u64,
    pub log_tail: String,
}

/// PDF points from the page's top-left; `v` is the baseline and the box
/// spans y from `v - height` to `v`.
#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SyncBox {
    pub page: u32,
    pub h: f64,
    pub v: f64,
    pub width: f64,
    pub height: f64,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SyncSource {
    pub file: String,
    pub line: u32,
}

// ---------------------------------------------------------------------------
// Entry points used by the Tauri commands
// ---------------------------------------------------------------------------

/// `run_dir` / `status` of every run in the database.
pub fn registered_runs(db: &DbState) -> Result<Vec<RunRow>, String> {
    let conn = db.conn.lock().map_err(|e| format!("数据库暂时不能用：{e}"))?;
    load_run_rows(&conn)
}

pub fn load_run_rows(conn: &Connection) -> Result<Vec<RunRow>, String> {
    let mut stmt = conn
        .prepare("SELECT run_dir, status FROM runs")
        .map_err(|e| format!("读取运行记录失败：{e}"))?;
    let rows = stmt
        .query_map([], |row| {
            Ok(RunRow {
                run_dir: row.get(0)?,
                status: row.get(1)?,
            })
        })
        .map_err(|e| format!("读取运行记录失败：{e}"))?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|e| format!("读取运行记录失败：{e}"))
}

pub async fn open(run_dir: String, runs: Vec<RunRow>) -> Result<LiveEditSession, String> {
    blocking(move || {
        let ctx = resolve_context(&run_dir, &runs)?;
        open_session(&run_dir, &ctx)
    })
    .await
}

pub async fn read(run_dir: String, file: String, runs: Vec<RunRow>) -> Result<LiveEditFile, String> {
    blocking(move || read_file(&resolve_context(&run_dir, &runs)?, &file)).await
}

pub async fn save(
    state: &LiveCompileState,
    run_dir: String,
    file: String,
    content: String,
    expected_mtime_ms: Option<u64>,
    runs: Vec<RunRow>,
) -> Result<LiveEditFile, String> {
    let _compile_done = wait_for_compile(state).await;
    blocking(move || {
        save_file(
            &resolve_context(&run_dir, &runs)?,
            &file,
            &content,
            expected_mtime_ms,
        )
    })
    .await
}

pub async fn restore(
    state: &LiveCompileState,
    run_dir: String,
    file: String,
    runs: Vec<RunRow>,
) -> Result<LiveEditFile, String> {
    let _compile_done = wait_for_compile(state).await;
    blocking(move || restore_file(&resolve_context(&run_dir, &runs)?, &file)).await
}

/// Windows: pdflatex keeps the `.tex` files open without delete sharing
/// while it builds, so replacing one fails until the build ends. A save or
/// restore therefore waits for a running compile (holding the compile lock
/// while it writes). A compile never waits for a save, so this cannot
/// deadlock; the wait is bounded anyway, and [`replace_file`] still writes
/// in place if the file stays locked. Elsewhere a rename works while TeX
/// reads the file: no wait.
async fn wait_for_compile(state: &LiveCompileState) -> Option<tokio::sync::MutexGuard<'_, ()>> {
    if !cfg!(windows) {
        return None;
    }
    tokio::time::timeout(SAVE_WAIT_LIMIT, state.lock.lock())
        .await
        .ok()
}

/// Waits for any other compile to finish first.
pub async fn compile(
    state: &LiveCompileState,
    run_dir: String,
    main_file: String,
    runs: Vec<RunRow>,
) -> Result<LiveCompileResult, String> {
    let ctx = blocking(move || resolve_context(&run_dir, &runs)).await?;
    let _one_at_a_time = state.lock.lock().await;
    compile_in(&ctx, &main_file).await
}

pub async fn sync_view(
    run_dir: String,
    main_file: String,
    file: String,
    line: u32,
    column: u32,
    runs: Vec<RunRow>,
) -> Result<Option<SyncBox>, String> {
    let ctx = blocking(move || resolve_context(&run_dir, &runs)).await?;
    sync_view_in(&ctx, &main_file, &file, line, column).await
}

pub async fn sync_edit(
    run_dir: String,
    main_file: String,
    page: u32,
    x: f64,
    y: f64,
    runs: Vec<RunRow>,
) -> Result<Option<SyncSource>, String> {
    let ctx = blocking(move || resolve_context(&run_dir, &runs)).await?;
    sync_edit_in(&ctx, &main_file, page, x, y).await
}

async fn blocking<T, F>(f: F) -> Result<T, String>
where
    T: Send + 'static,
    F: FnOnce() -> Result<T, String> + Send + 'static,
{
    tokio::task::spawn_blocking(f)
        .await
        .map_err(|e| format!("后台任务异常结束：{e}"))?
}

// ---------------------------------------------------------------------------
// Run check and path safety
// ---------------------------------------------------------------------------

pub fn is_active_status(status: &str) -> bool {
    matches!(status, "running" | "queued")
}

/// Checks that `run_dir` is a run in `runs` and finds its source folder.
pub fn resolve_context(run_dir: &str, runs: &[RunRow]) -> Result<EditContext, String> {
    let given = run_dir.trim();
    if given.is_empty() {
        return Err("没有指定要编辑的运行。".to_string());
    }
    let canon = Path::new(given)
        .canonicalize()
        .map_err(|_| format!("找不到这次运行的文件夹：{given}"))?;
    let mut known = false;
    let mut active = false;
    for row in runs {
        let same = row.run_dir == given
            || Path::new(&row.run_dir)
                .canonicalize()
                .map(|c| c == canon)
                .unwrap_or(false);
        if same {
            known = true;
            active |= is_active_status(&row.status);
        }
    }
    if !known {
        return Err("这个文件夹不是本应用里的一次运行，不能编辑。".to_string());
    }
    if !canon.is_dir() {
        return Err(format!("找不到这次运行的文件夹：{given}"));
    }
    let source = export::export_source_dir(&canon)
        .ok_or_else(|| "这次运行还没有 LaTeX 源码，暂时不能编辑。".to_string())?;
    let source_dir = source
        .canonicalize()
        .map_err(|_| "这次运行还没有 LaTeX 源码，暂时不能编辑。".to_string())?;
    let source_rel = source_dir
        .strip_prefix(&canon)
        .ok()
        .map(rel_string)
        .filter(|r| !r.is_empty())
        .ok_or_else(|| "LaTeX 源码文件夹的位置不对，不能编辑。".to_string())?;
    Ok(EditContext {
        run_dir: canon,
        source_dir,
        source_rel,
        run_active: active,
    })
}

/// Normal components joined with `/`.
fn rel_string(p: &Path) -> String {
    p.components()
        .filter_map(|c| match c {
            Component::Normal(s) => Some(s.to_string_lossy().to_string()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("/")
}

fn join_rel(base: &Path, rel: &str) -> PathBuf {
    rel.split('/')
        .filter(|s| !s.is_empty())
        .fold(base.to_path_buf(), |p, seg| p.join(seg))
}

/// A `.bib` that a document class writes for itself (the ACS `achemso`
/// class leaves an `acs-<job>.bib` control file per build). Not worth editing.
fn is_generated_bib(path: &Path) -> bool {
    use std::io::Read;
    if !path
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("bib"))
    {
        return false;
    }
    let mut head = [0u8; 200];
    let Ok(mut f) = fs::File::open(path) else {
        return false;
    };
    let n = f.read(&mut head).unwrap_or(0);
    String::from_utf8_lossy(&head[..n]).contains("auxiliary file used by the `achemso' bundle")
}

pub fn is_editable_name(name: &str) -> bool {
    let last = name.rsplit(['/', '\\']).next().unwrap_or(name);
    match last.rsplit_once('.') {
        Some((stem, ext)) => {
            !stem.is_empty() && EDITABLE_EXTS.contains(&ext.to_ascii_lowercase().as_str())
        }
        None => false,
    }
}

/// `file` checked like every other run path ([`resolve_safe_subpath`]), then
/// written with `/` and without `.` segments.
pub fn normalize_rel(source_dir: &Path, file: &str) -> Result<String, String> {
    resolve_safe_subpath(source_dir, file).map_err(|_| format!("文件路径不安全：{file}"))?;
    let segs: Vec<&str> = file
        .split(['/', '\\'])
        .filter(|s| !s.is_empty() && *s != ".")
        .collect();
    if segs.is_empty() {
        return Err("没有指定文件。".to_string());
    }
    Ok(segs.join("/"))
}

/// Path inside the source folder for a source-folder-relative path written
/// by TeX, BibTeX or SyncTeX (`./x.tex`, an absolute path, ...): only for an
/// existing editable file that stays inside the folder after resolving
/// symlinks. `source_dir` must be canonical.
pub fn rel_inside_source(path: &str, source_dir: &Path) -> Option<String> {
    let trimmed = path.trim();
    if trimmed.is_empty() {
        return None;
    }
    let p = Path::new(trimmed);
    let abs = if p.is_absolute() {
        p.to_path_buf()
    } else {
        source_dir.join(p)
    };
    let canon = abs.canonicalize().ok()?;
    if !canon.is_file() {
        return None;
    }
    let rel = rel_string(canon.strip_prefix(source_dir).ok()?);
    (!rel.is_empty() && is_editable_name(&rel)).then_some(rel)
}

/// Path for the frontend: no `\\?\` prefix, `/` separators.
fn json_path(p: &Path) -> String {
    let s = strip_windows_unc(p);
    if cfg!(windows) {
        s.replace('\\', "/")
    } else {
        s
    }
}

/// Path for an external program (no `\\?\` prefix on Windows).
fn plain_path(p: &Path) -> PathBuf {
    PathBuf::from(strip_windows_unc(p))
}

impl EditContext {
    fn backup_path(&self, rel: &str) -> PathBuf {
        join_rel(&join_rel(&self.run_dir.join(BACKUP_DIR), &self.source_rel), rel)
    }

    fn has_backup(&self, rel: &str) -> bool {
        self.backup_path(rel).is_file()
    }

    fn preview_path(&self) -> PathBuf {
        join_rel(&self.run_dir.join(PREVIEW_DIR), &self.source_rel)
    }

    /// The preview folder, created when asked; canonical, inside the run and
    /// outside the source folder. `None` when it does not exist (and was not
    /// to be created).
    fn preview_dir(&self, create: bool) -> Result<Option<PathBuf>, String> {
        let dir = self.preview_path();
        if create {
            let mut segs = vec![PREVIEW_DIR];
            segs.extend(self.source_rel.split('/'));
            real_dir_under(&self.run_dir, &segs, "预览文件夹")?;
        } else if !dir.is_dir() {
            return Ok(None);
        }
        let canon = dir
            .canonicalize()
            .map_err(|e| format!("无法打开预览文件夹：{e}"))?;
        if !canon.starts_with(&self.run_dir) || canon.starts_with(&self.source_dir) {
            return Err("预览文件夹的位置不安全，已停止。".to_string());
        }
        Ok(Some(canon))
    }

    /// `(normalised relative path, canonical file)` of an existing editable
    /// file inside the source folder.
    pub fn resolve_editable(&self, file: &str) -> Result<(String, PathBuf), String> {
        let rel = normalize_rel(&self.source_dir, file)?;
        if !is_editable_name(&rel) {
            return Err(ERR_NOT_EDITABLE.to_string());
        }
        let joined = join_rel(&self.source_dir, &rel);
        let is_file = fs::metadata(&joined).map(|m| m.is_file()).unwrap_or(false);
        if !is_file {
            return Err(format!("找不到文件：{rel}"));
        }
        let canon = joined
            .canonicalize()
            .map_err(|_| format!("找不到文件：{rel}"))?;
        if !canon.starts_with(&self.source_dir) {
            return Err(ERR_OUTSIDE.to_string());
        }
        if !is_editable_name(&canon.to_string_lossy()) {
            return Err(ERR_NOT_EDITABLE.to_string());
        }
        Ok((rel, canon))
    }
}

// ---------------------------------------------------------------------------
// Session, read, save, restore
// ---------------------------------------------------------------------------

/// The `.tex` / `.bib` files the export ships, `main` first, then sorted.
pub fn editable_files(source_dir: &Path, main: &str) -> Result<Vec<String>, String> {
    let mut files: Vec<String> = export::collect_export_files(source_dir)?
        .into_iter()
        .map(|e| e.rel)
        .filter(|rel| is_editable_name(rel) && rel != main)
        .filter(|rel| !is_generated_bib(&source_dir.join(rel)))
        .collect();
    files.sort();
    files.insert(0, main.to_string());
    Ok(files)
}

pub fn open_session(run_dir: &str, ctx: &EditContext) -> Result<LiveEditSession, String> {
    let main = export::pick_main_tex(&ctx.source_dir).ok_or_else(|| ERR_NO_MAIN.to_string())?;
    let files = editable_files(&ctx.source_dir, &main)?;
    let backed_up = files
        .iter()
        .filter(|f| ctx.has_backup(f))
        .cloned()
        .collect();
    Ok(LiveEditSession {
        run_dir: run_dir.to_string(),
        source_dir: json_path(&ctx.source_dir),
        source_rel: ctx.source_rel.clone(),
        main_file: main,
        files,
        backed_up,
        run_active: ctx.run_active,
    })
}

pub fn mtime_ms(meta: &fs::Metadata) -> u64 {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn file_mtime_ms(p: &Path) -> Result<u64, String> {
    fs::metadata(p)
        .map(|m| mtime_ms(&m))
        .map_err(|e| format!("读取文件信息失败：{e}"))
}

pub fn read_file(ctx: &EditContext, file: &str) -> Result<LiveEditFile, String> {
    let (rel, target) = ctx.resolve_editable(file)?;
    // The time is taken before the content: a change in between makes the
    // next save report a conflict instead of silently overwriting it.
    let meta = fs::metadata(&target).map_err(|e| format!("读取文件失败：{e}"))?;
    if meta.len() > MAX_EDIT_BYTES {
        return Err(ERR_TOO_BIG.to_string());
    }
    let bytes = fs::read(&target).map_err(|e| format!("读取文件失败：{e}"))?;
    let content = String::from_utf8(bytes).map_err(|_| ERR_NOT_UTF8.to_string())?;
    Ok(LiveEditFile {
        has_backup: ctx.has_backup(&rel),
        file: rel,
        content,
        mtime_ms: mtime_ms(&meta),
    })
}

pub fn save_file(
    ctx: &EditContext,
    file: &str,
    content: &str,
    expected_mtime_ms: Option<u64>,
) -> Result<LiveEditFile, String> {
    if ctx.run_active {
        return Err(ERR_RUN_ACTIVE.to_string());
    }
    if content.len() as u64 > MAX_EDIT_BYTES {
        return Err(ERR_TOO_BIG.to_string());
    }
    let (rel, target) = ctx.resolve_editable(file)?;
    let main = export::pick_main_tex(&ctx.source_dir).ok_or_else(|| ERR_NO_MAIN.to_string())?;
    if !editable_files(&ctx.source_dir, &main)?.contains(&rel) {
        return Err(ERR_NOT_PAPER_FILE.to_string());
    }

    let _guard = WRITE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let meta = fs::metadata(&target).map_err(|e| format!("读取文件信息失败：{e}"))?;
    let current = mtime_ms(&meta);
    if expected_mtime_ms.is_some_and(|expected| expected != current) {
        return Err(ERR_CONFLICT.to_string());
    }
    if meta.permissions().readonly() {
        return Err("这个文件是只读的，不能保存。".to_string());
    }
    let old = fs::read(&target).map_err(|e| format!("读取文件失败：{e}"))?;
    if old == content.as_bytes() {
        // Nothing to write, and no backup either.
        return Ok(LiveEditFile {
            has_backup: ctx.has_backup(&rel),
            file: rel,
            content: String::new(),
            mtime_ms: current,
        });
    }
    ensure_backup(ctx, &rel, &old)?;
    replace_file(&target, content.as_bytes()).map_err(|e| format!("保存失败：{e}"))?;
    Ok(LiveEditFile {
        has_backup: ctx.has_backup(&rel),
        mtime_ms: file_mtime_ms(&target)?,
        file: rel,
        content: String::new(),
    })
}

/// `base` joined with `segs`, creating missing folders one level at a time
/// and refusing to pass through anything that is not a real folder (a
/// symlink or junction could lead out of the run). `base` must be canonical.
fn real_dir_under(base: &Path, segs: &[&str], what: &str) -> Result<PathBuf, String> {
    let unsafe_dir = || format!("{what}的位置不安全，已停止。");
    let is_real_dir = |p: &Path| {
        fs::symlink_metadata(p)
            .map(|m| m.file_type().is_dir())
            .unwrap_or(false)
    };
    let mut dir = base.to_path_buf();
    for seg in segs.iter().filter(|s| !s.is_empty()) {
        dir.push(seg);
        match fs::symlink_metadata(&dir) {
            Ok(m) if m.file_type().is_dir() => {}
            Ok(_) => return Err(unsafe_dir()),
            Err(e) if e.kind() == io::ErrorKind::NotFound => match fs::create_dir(&dir) {
                Ok(()) => {}
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists && is_real_dir(&dir) => {}
                Err(e) => return Err(format!("无法创建{what}：{e}")),
            },
            Err(e) => return Err(format!("无法打开{what}：{e}")),
        }
    }
    let inside = dir
        .canonicalize()
        .map(|c| c.starts_with(base))
        .unwrap_or(false);
    if !inside {
        return Err(unsafe_dir());
    }
    Ok(dir)
}

/// Keeps `original` as the backup of `rel` unless a backup already exists.
fn ensure_backup(ctx: &EditContext, rel: &str, original: &[u8]) -> Result<(), String> {
    let parts: Vec<&str> = rel.split('/').collect();
    let (name, parents) = parts
        .split_last()
        .ok_or_else(|| "备份位置不对，已停止保存。".to_string())?;
    let mut segs = vec![BACKUP_DIR];
    segs.extend(ctx.source_rel.split('/'));
    segs.extend(parents);
    let dir = real_dir_under(&ctx.run_dir, &segs, "备份文件夹")?;
    let backup = dir.join(name);
    if fs::symlink_metadata(&backup).is_ok() {
        return Ok(());
    }
    atomic_write(&backup, original).map_err(|e| format!("备份原稿失败：{e}"))
}

pub fn restore_file(ctx: &EditContext, file: &str) -> Result<LiveEditFile, String> {
    if ctx.run_active {
        return Err(ERR_RUN_ACTIVE.to_string());
    }
    let (rel, target) = ctx.resolve_editable(file)?;
    let backup = ctx.backup_path(&rel);

    let _guard = WRITE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let plain = fs::symlink_metadata(&backup)
        .map(|m| m.file_type().is_file())
        .unwrap_or(false);
    if !plain {
        return Err(ERR_NO_BACKUP.to_string());
    }
    let inside = backup
        .canonicalize()
        .map(|c| c.starts_with(&ctx.run_dir))
        .unwrap_or(false);
    if !inside {
        return Err(ERR_NO_BACKUP.to_string());
    }
    let bytes = fs::read(&backup).map_err(|e| format!("读取原稿失败：{e}"))?;
    let content = String::from_utf8(bytes).map_err(|_| ERR_NOT_UTF8.to_string())?;
    replace_file(&target, content.as_bytes()).map_err(|e| format!("恢复原稿失败：{e}"))?;
    Ok(LiveEditFile {
        has_backup: ctx.has_backup(&rel),
        mtime_ms: file_mtime_ms(&target)?,
        file: rel,
        content,
    })
}

/// Replaces a source file's content: atomically when possible. Windows
/// refuses the rename while another program has the file open without
/// delete sharing (TeX does); the content is then written in place (TeX
/// allows that). The original is already in the backup.
fn replace_file(target: &Path, bytes: &[u8]) -> io::Result<()> {
    match atomic_write(target, bytes) {
        Err(e) if cfg!(windows) && is_sharing_violation(e.raw_os_error()) => {
            write_in_place(target, bytes)
        }
        result => result,
    }
}

/// Windows error codes for "another program has this file open":
/// ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION.
fn is_sharing_violation(code: Option<i32>) -> bool {
    matches!(code, Some(5 | 32 | 33))
}

/// Truncates `target` and writes `bytes` into the same file.
fn write_in_place(target: &Path, bytes: &[u8]) -> io::Result<()> {
    let mut f = OpenOptions::new().write(true).truncate(true).open(target)?;
    f.write_all(bytes)?;
    f.sync_all()
}

/// Writes a temp file next to `target` and renames it over `target`; keeps
/// the permissions of an existing `target`.
fn atomic_write(target: &Path, bytes: &[u8]) -> io::Result<()> {
    let dir = target
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "no parent folder"))?;
    let name = target
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "no file name"))?
        .to_string_lossy()
        .to_string();
    // Hidden, so neither the editor nor the export lists a leftover.
    let tmp = dir.join(format!(".{name}.{}.po-edit.tmp", Uuid::new_v4().simple()));
    let perms = fs::metadata(target).ok().map(|m| m.permissions());
    let result = (|| {
        let mut f = OpenOptions::new().write(true).create_new(true).open(&tmp)?;
        f.write_all(bytes)?;
        f.sync_all()?;
        drop(f);
        if let Some(perms) = perms {
            let _ = fs::set_permissions(&tmp, perms);
        }
        rename_with_retry(&tmp, target)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}

/// Windows refuses to replace a file another program has open for a moment
/// (e.g. TeX reading it), so a failed rename is retried briefly.
fn rename_with_retry(from: &Path, to: &Path) -> io::Result<()> {
    let mut last = None;
    for attempt in 0..10 {
        match fs::rename(from, to) {
            Ok(()) => return Ok(()),
            Err(e) => last = Some(e),
        }
        if attempt < 9 {
            std::thread::sleep(Duration::from_millis(50));
        }
    }
    Err(last.unwrap_or_else(|| io::Error::other("rename failed")))
}

// ---------------------------------------------------------------------------
// Compile
// ---------------------------------------------------------------------------

type Stamp = Option<(SystemTime, u64)>;

fn stamp(p: &Path) -> Stamp {
    let m = fs::metadata(p).ok().filter(|m| m.is_file())?;
    Some((m.modified().unwrap_or(UNIX_EPOCH), m.len()))
}

struct CompilePrep {
    main_rel: String,
    outdir: PathBuf,
    stem: String,
    pdf_before: Stamp,
    log_before: Stamp,
}

fn main_tex(ctx: &EditContext, main_file: &str) -> Result<String, String> {
    let (rel, _) = ctx.resolve_editable(main_file)?;
    if !rel.to_ascii_lowercase().ends_with(".tex") {
        return Err(ERR_MAIN_NOT_TEX.to_string());
    }
    Ok(rel)
}

fn stem_of(rel: &str) -> String {
    Path::new(rel)
        .file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default()
}

fn prepare_compile(ctx: &EditContext, main_file: &str) -> Result<CompilePrep, String> {
    let main_rel = main_tex(ctx, main_file)?;
    let outdir = ctx
        .preview_dir(true)?
        .ok_or_else(|| "无法创建预览文件夹。".to_string())?;
    let stem = stem_of(&main_rel);
    Ok(CompilePrep {
        pdf_before: stamp(&outdir.join(format!("{stem}.pdf"))),
        log_before: stamp(&outdir.join(format!("{stem}.log"))),
        main_rel,
        outdir,
        stem,
    })
}

pub async fn compile_in(ctx: &EditContext, main_file: &str) -> Result<LiveCompileResult, String> {
    let started = Instant::now();
    let prep = {
        let ctx = ctx.clone();
        let main_file = main_file.to_string();
        blocking(move || prepare_compile(&ctx, &main_file)).await?
    };
    let args = vec![
        "-pdf".to_string(),
        "-interaction=nonstopmode".to_string(),
        "-file-line-error".to_string(),
        "-synctex=1".to_string(),
        "-f".to_string(),
        format!("-outdir={}", strip_windows_unc(&prep.outdir)),
        prep.main_rel.clone(),
    ];
    let run = run_tool(
        "latexmk",
        &args,
        &plain_path(&ctx.source_dir),
        TEX_ENV,
        COMPILE_TIMEOUT,
    )
    .await
    .map_err(|e| {
        if e.kind() == io::ErrorKind::NotFound {
            ERR_LATEXMK_MISSING.to_string()
        } else {
            format!("无法启动 latexmk：{e}")
        }
    })?;
    let ctx = ctx.clone();
    blocking(move || Ok(finish_compile(&ctx, &prep, &run, started))).await
}

fn read_lossy(p: &Path) -> Option<String> {
    fs::read(p)
        .ok()
        .map(|b| String::from_utf8_lossy(&b).into_owned())
}

fn finish_compile(
    ctx: &EditContext,
    prep: &CompilePrep,
    run: &ToolRun,
    started: Instant,
) -> LiveCompileResult {
    let pdf = prep.outdir.join(format!("{}.pdf", prep.stem));
    let log = prep.outdir.join(format!("{}.log", prep.stem));
    let blg = prep.outdir.join(format!("{}.blg", prep.stem));
    let output = format!("{}\n{}", run.stdout, run.stderr);
    // Nothing changed since the last build: its PDF and log are current.
    let up_to_date = !run.timed_out
        && (output.contains("are up-to-date") || output.contains("Nothing to do for"));
    let pdf_after = stamp(&pdf);
    let log_after = stamp(&log);
    let log_fresh = log_after.is_some() && (log_after != prep.log_before || up_to_date);
    let log_text = log_fresh.then(|| read_lossy(&log)).flatten();
    // "Up to date" only counts when that last build really wrote the PDF:
    // after one that shipped no pages (or stopped on a fatal error) the PDF
    // on disk is an older one and the log has no "Output written on".
    let last_build_wrote_pdf = log_text
        .as_deref()
        .is_some_and(|t| t.contains(PDF_WRITTEN_MARK));
    let ok = !run.timed_out
        && pdf_after.is_some()
        && (pdf_after != prep.pdf_before || (up_to_date && last_build_wrote_pdf));

    let locate = |p: &str| rel_inside_source(p, &ctx.source_dir);
    let mut errors = Vec::new();
    let mut warnings = Vec::new();
    let mut warning_count = 0;
    if run.timed_out {
        errors.push(plain_issue(
            "编译超过 3 分钟还没结束，已经停止。可能是论文太大，或者某处写法让 LaTeX 卡住了。",
        ));
    }
    let log_tail = match log_text {
        Some(text) => {
            let parsed = parse_latex_log(&text, &locate);
            errors.extend(parsed.errors);
            warnings = parsed.warnings;
            warning_count = parsed.warning_count;
            if let Some(blg_text) = read_lossy(&blg) {
                errors.extend(parse_blg(&blg_text, &locate));
            }
            tail_lines(&text, LOG_TAIL_LINES)
        }
        None => {
            if !run.timed_out {
                errors.push(plain_issue("编译没能运行起来，请看下面 latexmk 的输出。"));
            }
            tail_lines(output.trim(), LOG_TAIL_LINES)
        }
    };
    if !ok && errors.is_empty() {
        errors.push(plain_issue("这次没有生成新的 PDF，请看下面的日志。"));
    }

    let mut issues = dedupe(errors);
    issues.extend(dedupe(warnings));
    issues.truncate(MAX_ISSUES);
    LiveCompileResult {
        ok,
        pdf_path: pdf_after.map(|_| json_path(&pdf)),
        issues,
        warnings: warning_count,
        duration_ms: started.elapsed().as_millis() as u64,
        log_tail,
    }
}

fn plain_issue(message: &str) -> LiveCompileIssue {
    LiveCompileIssue {
        file: None,
        line: None,
        message: message.to_string(),
        severity: IssueSeverity::Error,
    }
}

fn dedupe(items: Vec<LiveCompileIssue>) -> Vec<LiveCompileIssue> {
    let mut out: Vec<LiveCompileIssue> = Vec::with_capacity(items.len());
    for item in items {
        if !out.contains(&item) {
            out.push(item);
        }
    }
    out
}

fn clip(s: &str, max_chars: usize) -> String {
    if s.chars().count() <= max_chars {
        s.to_string()
    } else {
        let mut out: String = s.chars().take(max_chars).collect();
        out.push('…');
        out
    }
}

/// The last `max_chars` characters, with `…` in front when cut.
fn clip_front(s: &str, max_chars: usize) -> String {
    let n = s.chars().count();
    if n <= max_chars {
        s.to_string()
    } else {
        let tail: String = s.chars().skip(n - max_chars).collect();
        format!("…{tail}")
    }
}

pub fn tail_lines(text: &str, n: usize) -> String {
    let lines: Vec<&str> = text.lines().collect();
    lines[lines.len().saturating_sub(n)..]
        .iter()
        .map(|l| clip(l, MAX_TAIL_LINE_CHARS))
        .collect::<Vec<_>>()
        .join("\n")
}

// ---------------------------------------------------------------------------
// Log parsing
// ---------------------------------------------------------------------------

#[derive(Debug, Default)]
pub struct ParsedLog {
    pub errors: Vec<LiveCompileIssue>,
    pub warnings: Vec<LiveCompileIssue>,
    pub warning_count: u32,
}

fn is_full_width(line: &str) -> bool {
    line.len() == TEX_WRAP || line.chars().count() == TEX_WRAP
}

/// True when TeX wrapped the log at 79 columns (the environment override was
/// ignored, e.g. by MiKTeX): no longer line and some lines exactly that wide.
fn is_wrapped_log(lines: &[&str]) -> bool {
    lines.iter().all(|l| l.chars().count() <= TEX_WRAP) && lines.iter().any(|l| is_full_width(l))
}

/// A path as TeX prints it before `:<line>:`: no blank in its last segment,
/// which has a short alphanumeric extension.
fn looks_like_path(p: &str) -> bool {
    if p.is_empty() || p.starts_with(char::is_whitespace) || p.contains(": ") {
        return false;
    }
    let last = p.rsplit(['/', '\\']).next().unwrap_or(p);
    if last.chars().any(char::is_whitespace) {
        return false;
    }
    match last.rsplit_once('.') {
        Some((stem, ext)) => {
            !stem.is_empty()
                && (1..=10).contains(&ext.len())
                && ext.chars().all(|c| c.is_ascii_alphanumeric())
        }
        None => false,
    }
}

/// `path:line: message` (TeX's `-file-line-error` form).
pub fn split_file_line_error(line: &str) -> Option<(&str, u32, &str)> {
    let mut from = 0;
    while let Some(off) = line[from..].find(':') {
        let colon = from + off;
        let rest = &line[colon + 1..];
        let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
        if digits > 0 && rest[digits..].starts_with(':') {
            let path = &line[..colon];
            if looks_like_path(path) {
                if let Ok(n) = rest[..digits].parse::<u32>() {
                    return Some((path, n, rest[digits + 1..].trim()));
                }
            }
        }
        from = colon + 1;
    }
    None
}

fn is_error_start(line: &str) -> bool {
    line.starts_with('!') || split_file_line_error(line).is_some()
}

/// TeX's `l.<n> <text read so far>` line after an error: the text, which
/// ends where TeX stopped.
fn error_context(lines: &[&str], from: usize) -> Option<String> {
    for line in lines.iter().skip(from).take(20) {
        if is_error_start(line) {
            return None;
        }
        if let Some(rest) = line.strip_prefix("l.") {
            let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
            if digits > 0 {
                let text = rest[digits..].trim();
                return (!text.is_empty()).then(|| clip_front(text, CONTEXT_CHARS));
            }
        }
    }
    None
}

fn base_name(p: &str) -> &str {
    p.rsplit(['/', '\\']).next().unwrap_or(p)
}

fn error_issue(
    path: Option<&str>,
    line: Option<u32>,
    message: &str,
    context: Option<String>,
    locate: &dyn Fn(&str) -> Option<String>,
) -> LiveCompileIssue {
    let mut text = if message.trim().is_empty() {
        "LaTeX 报错".to_string()
    } else {
        message.trim().to_string()
    };
    if let Some(ctx) = context {
        text.push_str(&format!("（出错位置：{ctx}）"));
    }
    let (file, line) = match path {
        Some(p) => match locate(p) {
            Some(rel) => (Some(rel), line),
            None => {
                // Not an editable file (build output, a class file): say
                // where it was without making the entry clickable.
                text = match line {
                    Some(n) => format!("{} 第 {n} 行：{text}", base_name(p)),
                    None => format!("{}：{text}", base_name(p)),
                };
                (None, None)
            }
        },
        None => (None, None),
    };
    LiveCompileIssue {
        file,
        line,
        message: clip(&text, MAX_MESSAGE_CHARS),
        severity: IssueSeverity::Error,
    }
}

fn is_warning_start(line: &str) -> bool {
    line.starts_with("LaTeX Warning:")
        || line.starts_with("LaTeX Font Warning:")
        || ((line.starts_with("Package ") || line.starts_with("Class "))
            && line.contains(" Warning: "))
        || line.starts_with("pdfTeX warning")
}

/// `(natbib)    more text`: the continuation of a package's message.
fn continuation_text(line: &str) -> Option<&str> {
    let rest = line.strip_prefix('(')?;
    let close = rest.find(')')?;
    let name = &rest[..close];
    if name.is_empty() || close > 40 || name.contains(char::is_whitespace) {
        return None;
    }
    let after = &rest[close + 1..];
    (after.is_empty() || after.starts_with(char::is_whitespace)).then(|| after.trim())
}

fn collapse_spaces(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn warning_issue(text: &str) -> LiveCompileIssue {
    let text = collapse_spaces(text);
    let message = match text.find("Warning: ") {
        Some(idx) => {
            let head = text[..idx].trim();
            let body = text[idx + "Warning: ".len()..].trim();
            match head
                .strip_prefix("Package ")
                .or_else(|| head.strip_prefix("Class "))
            {
                Some(name) => format!("警告（{name}）：{body}"),
                None => format!("警告：{body}"),
            }
        }
        None => format!("警告：{text}"),
    };
    LiveCompileIssue {
        file: None,
        line: None,
        message: clip(&message, MAX_MESSAGE_CHARS),
        severity: IssueSeverity::Warning,
    }
}

/// Errors (`path:line: message` and `! message`) and LaTeX / package / class
/// warnings of a pdflatex log. `locate` maps a path TeX printed to an
/// editable file in the source folder.
pub fn parse_latex_log(log: &str, locate: &dyn Fn(&str) -> Option<String>) -> ParsedLog {
    let lines: Vec<&str> = log.lines().collect();
    let wrapped = is_wrapped_log(&lines);
    let mut out = ParsedLog::default();
    let mut i = 0;
    while i < lines.len() {
        let line = lines[i];
        if let Some((path, lineno, msg)) = split_file_line_error(line) {
            // A long path wraps: its start is on the full-width line(s) above.
            let mut full_path = path.to_string();
            if wrapped {
                let mut j = i;
                while j > 0 && i - j < 8 && is_full_width(lines[j - 1]) {
                    let prev = lines[j - 1];
                    if is_error_start(prev) || prev.contains(char::is_whitespace) {
                        break;
                    }
                    full_path.insert_str(0, prev);
                    j -= 1;
                }
            }
            let mut message = msg.to_string();
            let mut k = i;
            while wrapped && is_full_width(lines[k]) && k + 1 < lines.len() && k - i < 3 {
                k += 1;
                message.push_str(lines[k]);
            }
            let context = error_context(&lines, k + 1);
            out.errors
                .push(error_issue(Some(&full_path), Some(lineno), &message, context, locate));
            i = k + 1;
            continue;
        }
        if let Some(rest) = line.strip_prefix('!') {
            let mut message = rest.trim().to_string();
            let mut k = i;
            while wrapped && is_full_width(lines[k]) && k + 1 < lines.len() && k - i < 3 {
                k += 1;
                message.push_str(lines[k]);
            }
            if !message.trim().is_empty() {
                let context = error_context(&lines, k + 1);
                out.errors.push(error_issue(None, None, &message, context, locate));
            }
            i = k + 1;
            continue;
        }
        if is_warning_start(line) {
            let mut text = line.to_string();
            let mut k = i;
            while k + 1 < lines.len() && k - i < 8 {
                let next = lines[k + 1];
                if let Some(more) = continuation_text(next) {
                    text.push(' ');
                    text.push_str(more);
                } else if wrapped && is_full_width(lines[k]) {
                    text.push_str(next);
                } else {
                    break;
                }
                k += 1;
            }
            out.warning_count += 1;
            out.warnings.push(warning_issue(&text));
            i = k + 1;
            continue;
        }
        i += 1;
    }
    out
}

/// BibTeX errors from a `.blg`: `message---line N of file F` and the
/// "could not open / found no" lines.
pub fn parse_blg(blg: &str, locate: &dyn Fn(&str) -> Option<String>) -> Vec<LiveCompileIssue> {
    let mut out = Vec::new();
    for line in blg.lines() {
        let line = line.trim_end();
        if let Some(idx) = line.find("---line ") {
            let msg = line[..idx].trim();
            let rest = &line[idx + "---line ".len()..];
            let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
            let file = rest[digits..].trim_start().strip_prefix("of file ");
            if let (Ok(n), Some(file)) = (rest[..digits].parse::<u32>(), file) {
                out.push(error_issue(
                    Some(file.trim()),
                    Some(n),
                    &format!("BibTeX：{msg}"),
                    None,
                    locate,
                ));
                continue;
            }
        }
        if line.starts_with("I couldn't open") || line.starts_with("I found no") {
            out.push(error_issue(None, None, &format!("BibTeX：{line}"), None, locate));
        }
    }
    out
}

// ---------------------------------------------------------------------------
// SyncTeX
// ---------------------------------------------------------------------------

/// Fields of the first record of `synctex view` / `synctex edit` output.
fn first_synctex_record(out: &str) -> Vec<(&str, &str)> {
    let mut fields = Vec::new();
    let mut in_result = false;
    let mut seen_output = false;
    for line in out.lines() {
        let line = line.trim_end_matches('\r');
        if line == "SyncTeX result begin" {
            in_result = true;
            continue;
        }
        if !in_result {
            continue;
        }
        if line == "SyncTeX result end" {
            break;
        }
        if line.starts_with("Output:") {
            if seen_output {
                break;
            }
            seen_output = true;
            continue;
        }
        if let Some((key, value)) = line.split_once(':') {
            fields.push((key, value));
        }
    }
    fields
}

fn field<'a>(fields: &[(&str, &'a str)], key: &str) -> Option<&'a str> {
    fields.iter().find(|(k, _)| *k == key).map(|(_, v)| *v)
}

fn finite(v: Option<&str>) -> Option<f64> {
    v?.trim().parse::<f64>().ok().filter(|x| x.is_finite())
}

pub fn parse_synctex_view(out: &str) -> Option<SyncBox> {
    let fields = first_synctex_record(out);
    let page = field(&fields, "Page")?.trim().parse::<u32>().ok().filter(|p| *p >= 1)?;
    Some(SyncBox {
        page,
        h: finite(field(&fields, "h"))?,
        v: finite(field(&fields, "v"))?,
        width: finite(field(&fields, "W")).unwrap_or(0.0),
        height: finite(field(&fields, "H")).unwrap_or(0.0),
    })
}

/// `(Input path as SyncTeX printed it, line)`.
pub fn parse_synctex_edit(out: &str) -> Option<(String, u32)> {
    let fields = first_synctex_record(out);
    // The path may itself contain ':' (Windows drives): everything after the
    // first one is the value, which split_once already gives.
    let input = field(&fields, "Input")?.trim();
    let line = field(&fields, "Line")?.trim().parse::<i64>().ok()?;
    if input.is_empty() || line < 1 || line > u32::MAX as i64 {
        return None;
    }
    Some((input.to_string(), line as u32))
}

struct SyncPrep {
    pdf: PathBuf,
    outdir: PathBuf,
}

/// The preview PDF of `main_file`; [`ERR_SYNC_STALE`] unless it and its
/// SyncTeX file exist.
fn sync_prep(ctx: &EditContext, main_file: &str) -> Result<SyncPrep, String> {
    let main_rel = main_tex(ctx, main_file)?;
    let outdir = ctx
        .preview_dir(false)?
        .ok_or_else(|| ERR_SYNC_STALE.to_string())?;
    let stem = stem_of(&main_rel);
    let pdf = outdir.join(format!("{stem}.pdf"));
    let has_synctex = ["synctex.gz", "synctex"]
        .iter()
        .any(|ext| outdir.join(format!("{stem}.{ext}")).is_file());
    if !(pdf.is_file() && has_synctex) {
        return Err(ERR_SYNC_STALE.to_string());
    }
    Ok(SyncPrep { pdf, outdir })
}

fn map_synctex_err(e: io::Error) -> String {
    if e.kind() == io::ErrorKind::NotFound {
        ERR_SYNCTEX_MISSING.to_string()
    } else {
        format!("无法启动 synctex：{e}")
    }
}

pub async fn sync_view_in(
    ctx: &EditContext,
    main_file: &str,
    file: &str,
    line: u32,
    column: u32,
) -> Result<Option<SyncBox>, String> {
    let (prep, target) = {
        let ctx = ctx.clone();
        let main_file = main_file.to_string();
        let file = file.to_string();
        blocking(move || {
            let (_, target) = ctx.resolve_editable(&file)?;
            Ok((sync_prep(&ctx, &main_file)?, target))
        })
        .await?
    };
    let input = format!("{}:{}:{}", line.max(1), column, strip_windows_unc(&target));
    let args = vec![
        "view".to_string(),
        "-i".to_string(),
        input,
        "-o".to_string(),
        strip_windows_unc(&prep.pdf),
    ];
    let run = run_tool("synctex", &args, &plain_path(&prep.outdir), &[], SYNCTEX_TIMEOUT)
        .await
        .map_err(map_synctex_err)?;
    if run.timed_out {
        return Err(ERR_SYNC_TIMEOUT.to_string());
    }
    Ok(parse_synctex_view(&run.stdout))
}

pub async fn sync_edit_in(
    ctx: &EditContext,
    main_file: &str,
    page: u32,
    x: f64,
    y: f64,
) -> Result<Option<SyncSource>, String> {
    if page == 0 || !x.is_finite() || !y.is_finite() {
        return Ok(None);
    }
    let prep = {
        let ctx = ctx.clone();
        let main_file = main_file.to_string();
        blocking(move || sync_prep(&ctx, &main_file)).await?
    };
    let args = vec![
        "edit".to_string(),
        "-o".to_string(),
        format!("{page}:{x}:{y}:{}", strip_windows_unc(&prep.pdf)),
    ];
    let run = run_tool("synctex", &args, &plain_path(&prep.outdir), &[], SYNCTEX_TIMEOUT)
        .await
        .map_err(map_synctex_err)?;
    if run.timed_out {
        return Err(ERR_SYNC_TIMEOUT.to_string());
    }
    let Some((input, line)) = parse_synctex_edit(&run.stdout) else {
        return Ok(None);
    };
    let source_dir = ctx.source_dir.clone();
    blocking(move || {
        Ok(rel_inside_source(&input, &source_dir).map(|file| SyncSource { file, line }))
    })
    .await
}

// ---------------------------------------------------------------------------
// External programs
// ---------------------------------------------------------------------------

struct ToolRun {
    stdout: String,
    stderr: String,
    timed_out: bool,
}

/// Runs `program` with the enriched PATH and no console window; kills it
/// (and its children) after `timeout`.
async fn run_tool(
    program: &str,
    args: &[String],
    cwd: &Path,
    extra_env: &[(&str, &str)],
    timeout: Duration,
) -> io::Result<ToolRun> {
    let mut cmd = Command::new(program);
    cmd.args(args)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let path_env = enriched_path_env();
    if !path_env.is_empty() {
        cmd.env("PATH", path_env);
    }
    for (key, value) in extra_env {
        cmd.env(key, value);
    }
    #[cfg(target_os = "windows")]
    {
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = cmd.spawn()?;
    let pid = child.id();
    let mut stdout = child.stdout.take();
    let mut stderr = child.stderr.take();
    let finished = {
        let read_out = async {
            let mut buf = Vec::new();
            if let Some(s) = stdout.as_mut() {
                let _ = s.read_to_end(&mut buf).await;
            }
            buf
        };
        let read_err = async {
            let mut buf = Vec::new();
            if let Some(s) = stderr.as_mut() {
                let _ = s.read_to_end(&mut buf).await;
            }
            buf
        };
        let all = async { tokio::join!(read_out, read_err, child.wait()) };
        tokio::time::timeout(timeout, all).await
    };
    match finished {
        Ok((out, err, _status)) => Ok(ToolRun {
            stdout: String::from_utf8_lossy(&out).into_owned(),
            stderr: String::from_utf8_lossy(&err).into_owned(),
            timed_out: false,
        }),
        Err(_) => {
            if let Some(pid) = pid {
                kill_children(pid).await;
            }
            let _ = child.kill().await;
            Ok(ToolRun {
                stdout: String::new(),
                stderr: String::new(),
                timed_out: true,
            })
        }
    }
}

/// latexmk starts pdflatex / bibtex as child processes: stop those too.
async fn kill_children(pid: u32) {
    #[cfg(target_os = "windows")]
    {
        let mut kill = Command::new("taskkill");
        kill.args(["/PID", &pid.to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        kill.creation_flags(CREATE_NO_WINDOW);
        let _ = kill.status().await;
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = Command::new("pkill")
            .args(["-KILL", "-P", &pid.to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .await;
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn achemso_control_bib_is_not_listed() {
        let dir = std::env::temp_dir().join(format!("po_live_bib_{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("main.tex"), "\\documentclass{article}\\begin{document}x\\end{document}\n").unwrap();
        fs::write(dir.join("references.bib"), "@article{a, title={A}}\n").unwrap();
        fs::write(
            dir.join("acs-main.bib"),
            "This is an auxiliary file used by the `achemso' bundle.\nThis file may safely be deleted.\n",
        )
        .unwrap();
        let files = editable_files(&dir, "main.tex").unwrap();
        assert_eq!(files, vec!["main.tex".to_string(), "references.bib".to_string()]);
        fs::remove_dir_all(&dir).ok();
    }

    use super::*;

    /// Scratch space: `PO_TEST_TMP`, else the external test volume when it is
    /// mounted (the internal disk is nearly full), else the system temp dir.
    fn scratch_root() -> PathBuf {
        if let Ok(dir) = std::env::var("PO_TEST_TMP") {
            return PathBuf::from(dir);
        }
        let ext = Path::new("/Volumes/PODev/po-test/live-editor");
        if ext.is_dir() {
            ext.to_path_buf()
        } else {
            std::env::temp_dir()
        }
    }

    /// Temp folder removed on drop, even when an assertion fails.
    struct TempDir(PathBuf);

    impl TempDir {
        fn new(tag: &str) -> Self {
            let dir = scratch_root().join(format!("po_live_{tag}_{}", Uuid::new_v4().simple()));
            fs::create_dir_all(&dir).unwrap();
            TempDir(dir.canonicalize().unwrap())
        }
        fn put(&self, rel: &str, body: &str) -> PathBuf {
            let p = self.0.join(rel);
            fs::create_dir_all(p.parent().unwrap()).unwrap();
            fs::write(&p, body).unwrap();
            p
        }
        fn s(&self) -> String {
            self.0.to_string_lossy().to_string()
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    const DOC: &str = "\\documentclass{article}\n\\begin{document}\nHello\n\\input{sec/intro}\n\\end{document}\n";

    /// A finished run with a refinement folder like the pipeline leaves it.
    fn sample_run(tag: &str) -> TempDir {
        let run = TempDir::new(tag);
        run.put("content_refinement_workdir/paper.tex", DOC);
        run.put("content_refinement_workdir/paper.pdf", "%PDF");
        run.put("content_refinement_workdir/old_draft.tex", DOC);
        run.put("content_refinement_workdir/references.bib", "@article{a, title={A}}\n");
        run.put("content_refinement_workdir/sec/intro.tex", "Intro text.\n");
        run.put("content_refinement_workdir/achemso.cls", "% class\n");
        run.put("content_refinement_workdir/.hidden/secret.tex", "x");
        run.put("content_refinement_workdir/notes.txt", "notes");
        // paper.tex must be the newest compiled document.
        let old = SystemTime::now() - Duration::from_secs(3600);
        let f = fs::File::options()
            .write(true)
            .open(run.0.join("content_refinement_workdir/old_draft.tex"))
            .unwrap();
        f.set_modified(old).unwrap();
        run
    }

    fn rows(run: &TempDir, status: &str) -> Vec<RunRow> {
        vec![RunRow {
            run_dir: run.s(),
            status: status.to_string(),
        }]
    }

    fn ctx_of(run: &TempDir, status: &str) -> EditContext {
        resolve_context(&run.s(), &rows(run, status)).unwrap()
    }

    // ---- run check -------------------------------------------------------

    #[test]
    fn only_registered_runs_are_accepted() {
        let run = sample_run("reg");
        let other = sample_run("reg_other");
        let ctx = ctx_of(&run, "succeeded");
        assert_eq!(ctx.source_rel, "content_refinement_workdir");
        assert_eq!(ctx.source_dir, run.0.join("content_refinement_workdir"));
        assert!(!ctx.run_active);

        // Same folder spelled differently still matches its row.
        // Plain form (no Windows `\\?\` prefix, where `/` and `.` are literal).
        let dotted = format!("{}/./", strip_windows_unc(&run.0));
        assert!(resolve_context(&dotted, &rows(&run, "failed")).is_ok());

        assert!(resolve_context(&other.s(), &rows(&run, "succeeded")).is_err());
        assert!(resolve_context("", &rows(&run, "succeeded")).is_err());
        assert!(resolve_context(&run.0.join("nope").to_string_lossy(), &rows(&run, "succeeded")).is_err());

        assert!(ctx_of(&run, "running").run_active);
        assert!(ctx_of(&run, "queued").run_active);
        assert!(!ctx_of(&run, "cancelled").run_active);

        // A run without LaTeX sources yet.
        let empty = TempDir::new("reg_empty");
        let err = resolve_context(&empty.s(), &rows(&empty, "running")).unwrap_err();
        assert!(err.contains("LaTeX"), "{err}");
    }

    #[test]
    fn run_rows_come_from_the_runs_table() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE runs (id TEXT PRIMARY KEY, status TEXT NOT NULL, run_dir TEXT NOT NULL);
             INSERT INTO runs VALUES ('a', 'running', '/x/a'), ('b', 'succeeded', '/x/b');",
        )
        .unwrap();
        let mut got = load_run_rows(&conn).unwrap();
        got.sort_by(|a, b| a.run_dir.cmp(&b.run_dir));
        assert_eq!(
            got,
            vec![
                RunRow { run_dir: "/x/a".into(), status: "running".into() },
                RunRow { run_dir: "/x/b".into(), status: "succeeded".into() },
            ]
        );
    }

    // ---- session ---------------------------------------------------------

    #[test]
    fn session_lists_the_papers_sources_main_first() {
        let run = sample_run("session");
        let ctx = ctx_of(&run, "succeeded");
        let s = open_session(&run.s(), &ctx).unwrap();
        assert_eq!(s.main_file, "paper.tex");
        // old_draft.tex is another whole document: the export leaves it out,
        // and so does the editor. Hidden folders, .cls and .txt are not listed.
        assert_eq!(s.files, vec!["paper.tex", "references.bib", "sec/intro.tex"]);
        assert!(s.backed_up.is_empty());
        assert_eq!(s.source_rel, "content_refinement_workdir");
        assert!(s.source_dir.ends_with("/content_refinement_workdir"));
        assert_eq!(s.run_dir, run.s());

        let json = serde_json::to_value(&s).unwrap();
        for key in ["runDir", "sourceDir", "sourceRel", "mainFile", "files", "backedUp", "runActive"] {
            assert!(json.get(key).is_some(), "{key}");
        }

        save_file(&ctx, "sec/intro.tex", "Changed.\n", None).unwrap();
        let s = open_session(&run.s(), &ctx).unwrap();
        assert_eq!(s.backed_up, vec!["sec/intro.tex"]);
        assert_eq!(s.main_file, "paper.tex");
    }

    // ---- path safety -----------------------------------------------------

    #[test]
    fn unsafe_and_non_source_paths_are_refused() {
        let run = sample_run("paths");
        run.put("outside.tex", "secret");
        let ctx = ctx_of(&run, "succeeded");

        for bad in ["../outside.tex", "sec/../../outside.tex", "..\\outside.tex", "C:\\x.tex"] {
            let err = ctx.resolve_editable(bad).unwrap_err();
            assert!(err.contains("不安全"), "{bad}: {err}");
        }
        let abs = run.0.join("outside.tex").to_string_lossy().to_string();
        assert!(ctx.resolve_editable(&abs).is_err());
        assert!(read_file(&ctx, &abs).is_err());

        assert_eq!(ctx.resolve_editable("achemso.cls").unwrap_err(), ERR_NOT_EDITABLE);
        assert_eq!(ctx.resolve_editable("notes.txt").unwrap_err(), ERR_NOT_EDITABLE);
        assert!(save_file(&ctx, "notes.txt", "x", None).is_err());
        assert!(save_file(&ctx, "missing.tex", "x", None).unwrap_err().contains("找不到"));
        assert!(ctx.resolve_editable("").is_err());

        // Normalised names.
        assert_eq!(ctx.resolve_editable("./sec//intro.tex").unwrap().0, "sec/intro.tex");
        assert_eq!(ctx.resolve_editable("sec\\intro.tex").unwrap().0, "sec/intro.tex");

        // Another whole document is readable but not writable.
        assert!(read_file(&ctx, "old_draft.tex").is_ok());
        assert_eq!(save_file(&ctx, "old_draft.tex", "x", None).unwrap_err(), ERR_NOT_PAPER_FILE);
        assert_eq!(fs::read_to_string(run.0.join("content_refinement_workdir/old_draft.tex")).unwrap(), DOC);
    }

    #[cfg(unix)]
    #[test]
    fn symlinks_cannot_escape_the_source_folder() {
        let run = sample_run("symlink");
        let outside = run.put("outside.tex", "secret");
        let src = run.0.join("content_refinement_workdir");
        std::os::unix::fs::symlink(&outside, src.join("link.tex")).unwrap();
        let ctx = ctx_of(&run, "succeeded");
        assert_eq!(ctx.resolve_editable("link.tex").unwrap_err(), ERR_OUTSIDE);
        assert!(save_file(&ctx, "link.tex", "pwned", None).is_err());
        assert_eq!(fs::read_to_string(&outside).unwrap(), "secret");
        assert_eq!(rel_inside_source(&src.join("link.tex").to_string_lossy(), &ctx.source_dir), None);

        // A planted .edit_backup symlink pointing out of the run is refused.
        let elsewhere = TempDir::new("symlink_target");
        std::os::unix::fs::symlink(&elsewhere.0, run.0.join(BACKUP_DIR)).unwrap();
        let err = save_file(&ctx, "paper.tex", "changed", None).unwrap_err();
        assert!(err.contains("备份"), "{err}");
        assert_eq!(fs::read_to_string(src.join("paper.tex")).unwrap(), DOC);
        assert!(fs::read_dir(&elsewhere.0).unwrap().next().is_none());
    }

    // ---- save / backup / restore ----------------------------------------

    #[test]
    fn first_save_keeps_the_original_and_restore_brings_it_back() {
        let run = sample_run("backup");
        let ctx = ctx_of(&run, "succeeded");
        let src = run.0.join("content_refinement_workdir");
        let backup = run.0.join(".edit_backup/content_refinement_workdir/paper.tex");

        let opened = read_file(&ctx, "paper.tex").unwrap();
        assert_eq!(opened.content, DOC);
        assert!(!opened.has_backup);
        assert!(!backup.exists());
        assert_eq!(restore_file(&ctx, "paper.tex").unwrap_err(), ERR_NO_BACKUP);

        let saved = save_file(&ctx, "paper.tex", "v1", Some(opened.mtime_ms)).unwrap();
        assert_eq!(saved.file, "paper.tex");
        assert_eq!(saved.content, "");
        assert!(saved.has_backup);
        assert!(read_file(&ctx, "paper.tex").unwrap().has_backup);
        assert_eq!(saved.mtime_ms, file_mtime_ms(&src.join("paper.tex")).unwrap());
        assert_eq!(fs::read_to_string(src.join("paper.tex")).unwrap(), "v1");
        assert_eq!(fs::read_to_string(&backup).unwrap(), DOC);

        // Later saves never touch the backup.
        let saved2 = save_file(&ctx, "paper.tex", "v2", Some(saved.mtime_ms)).unwrap();
        assert_eq!(fs::read_to_string(src.join("paper.tex")).unwrap(), "v2");
        assert_eq!(fs::read_to_string(&backup).unwrap(), DOC);
        // Saving the same text again changes nothing.
        let same = save_file(&ctx, "paper.tex", "v2", Some(saved2.mtime_ms)).unwrap();
        assert_eq!(same.mtime_ms, saved2.mtime_ms);
        assert!(same.has_backup);

        // No temp files left behind, nothing new in the source folder.
        let names: Vec<String> = fs::read_dir(&src)
            .unwrap()
            .flatten()
            .map(|e| e.file_name().to_string_lossy().to_string())
            .filter(|n| n.contains("po-edit"))
            .collect();
        assert!(names.is_empty(), "{names:?}");

        let restored = restore_file(&ctx, "paper.tex").unwrap();
        assert_eq!(restored.content, DOC);
        assert!(restored.has_backup);
        assert_eq!(restored.mtime_ms, file_mtime_ms(&src.join("paper.tex")).unwrap());
        assert_eq!(fs::read_to_string(src.join("paper.tex")).unwrap(), DOC);
        assert!(backup.is_file(), "the backup stays for another restore");
        // pick_main_tex still picks the paper after all of this.
        assert_eq!(export::pick_main_tex(&src).as_deref(), Some("paper.tex"));
    }

    #[test]
    fn a_file_changed_elsewhere_is_not_overwritten() {
        let run = sample_run("conflict");
        let ctx = ctx_of(&run, "succeeded");
        let path = run.0.join("content_refinement_workdir/references.bib");
        let opened = read_file(&ctx, "references.bib").unwrap();

        // Another editor writes the file.
        fs::write(&path, "@article{other, title={B}}\n").unwrap();
        let f = fs::File::options().write(true).open(&path).unwrap();
        f.set_modified(SystemTime::now() + Duration::from_secs(5)).unwrap();
        drop(f);

        let err = save_file(&ctx, "references.bib", "mine", Some(opened.mtime_ms)).unwrap_err();
        assert_eq!(err, ERR_CONFLICT);
        assert_eq!(fs::read_to_string(&path).unwrap(), "@article{other, title={B}}\n");
        assert!(!run.0.join(".edit_backup").exists());

        // Without an expected time the save goes through.
        assert!(save_file(&ctx, "references.bib", "mine", None).is_ok());
        assert_eq!(fs::read_to_string(&path).unwrap(), "mine");
    }

    #[test]
    fn nothing_is_written_while_the_run_is_active() {
        let run = sample_run("active");
        let ctx = ctx_of(&run, "running");
        assert!(read_file(&ctx, "paper.tex").is_ok());
        assert_eq!(save_file(&ctx, "paper.tex", "x", None).unwrap_err(), ERR_RUN_ACTIVE);
        assert_eq!(restore_file(&ctx, "paper.tex").unwrap_err(), ERR_RUN_ACTIVE);
        assert_eq!(fs::read_to_string(run.0.join("content_refinement_workdir/paper.tex")).unwrap(), DOC);
        let s = open_session(&run.s(), &ctx).unwrap();
        assert!(s.run_active);
    }

    #[test]
    fn saving_unchanged_text_makes_no_backup() {
        let run = sample_run("noop");
        let ctx = ctx_of(&run, "succeeded");
        let opened = read_file(&ctx, "sec/intro.tex").unwrap();
        assert!(!opened.has_backup);
        let same = save_file(&ctx, "sec/intro.tex", &opened.content, Some(opened.mtime_ms)).unwrap();
        assert!(!same.has_backup);
        assert_eq!(same.mtime_ms, opened.mtime_ms);
        assert!(!run.0.join(BACKUP_DIR).exists());
        assert!(!read_file(&ctx, "sec/intro.tex").unwrap().has_backup);
        assert_eq!(restore_file(&ctx, "sec/intro.tex").unwrap_err(), ERR_NO_BACKUP);
    }

    #[test]
    fn in_place_write_replaces_the_content() {
        let run = sample_run("inplace");
        let path = run.0.join("content_refinement_workdir/sec/intro.tex");
        write_in_place(&path, "short".as_bytes()).unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "short");
        replace_file(&path, "atomic".as_bytes()).unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "atomic");

        assert!(is_sharing_violation(Some(32)));
        assert!(is_sharing_violation(Some(5)));
        assert!(is_sharing_violation(Some(33)));
        assert!(!is_sharing_violation(Some(2)));
        assert!(!is_sharing_violation(None));
    }

    /// Windows: a save waits until the running compile ends (TeX keeps the
    /// file open). Elsewhere it does not wait.
    #[test]
    fn saves_wait_for_a_running_compile_only_on_windows() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        rt.block_on(async {
            let run = sample_run("wait");
            let state = std::sync::Arc::new(LiveCompileState::default());
            let compiling = state.lock.lock().await;
            let (st, dir, runs) = (state.clone(), run.s(), rows(&run, "succeeded"));
            let mut task = tokio::spawn(async move {
                save(&st, dir, "paper.tex".into(), "v1".into(), None, runs).await
            });
            if cfg!(windows) {
                assert!(tokio::time::timeout(Duration::from_millis(300), &mut task).await.is_err());
                drop(compiling);
                assert!(task.await.unwrap().unwrap().has_backup);
            } else {
                let saved = tokio::time::timeout(Duration::from_secs(10), &mut task)
                    .await
                    .expect("a save must not wait for the compile here")
                    .unwrap()
                    .unwrap();
                assert!(saved.has_backup);
                drop(compiling);
            }
            let src = run.0.join("content_refinement_workdir/paper.tex");
            assert_eq!(fs::read_to_string(src).unwrap(), "v1");
        });
    }

    #[test]
    fn non_utf8_files_are_not_opened() {
        let run = sample_run("latin1");
        fs::write(run.0.join("content_refinement_workdir/references.bib"), b"@misc{a, note={caf\xe9}}").unwrap();
        let ctx = ctx_of(&run, "succeeded");
        assert_eq!(read_file(&ctx, "references.bib").unwrap_err(), ERR_NOT_UTF8);
    }

    // ---- log parsing -----------------------------------------------------

    /// Excerpt of a real pdflatex log (refined_paper_v3, achemso), written
    /// with max_print_line=10000, plus a few other kinds of messages.
    const LOG: &str = r#"This is pdfTeX, Version 3.141592653-2.6-1.40.29 (TeX Live 2026) (preloaded format=pdflatex 2026.7.7)  19 NOV 2026 17:31
 file:line:error style messages enabled.
Package: infwarerr 2019/12/03 v1.5 Providing info/warning/error messages (HO)
Overfull \hbox (1.53575pt too wide) in paragraph at lines 123--139
[2] [3] [4] [5] [6]
./refined_paper_v3.tex:391: Undefined control sequence.
l.391 Let $\mathbf{x}\in\mathbb
                               {R}^{3N}$ collect the $3N$ Cartesian coordinates of a
The control sequence at the end of the top line
of your error message was never \def'ed. If you have

./refined_paper_v3.tex:394: Undefined control sequence.
l.394 $\mathbf{F}_{\mathrm{head}}(\mathbf{x})\in\mathbb
                                                       {R}^{3N}$. A conservative model is
Package natbib Warning: Citation `Smith2020' on page 7 undefined on input line 402.
LaTeX Warning: Reference `fig:missing' on page 8 undefined on input line 455.
LaTeX Font Warning: Font shape `OT1/cmr/m/n' in size <10.95> not available
(Font)              size <10> substituted on input line 97.
Class achemso Warning: Unknown option `foo' on input line 3.
/Volumes/PODev/po-test/live-editor/run-copy/.live_preview/content_refinement_workdir/refined_paper_v3.bbl:191: Misplaced alignment tab character &.
<argument> ACS applied materials &
                                   interfaces
l.191 ...Materials' Surfaces. \emph{ACS applied materials & interfaces}
./sec/intro.tex:12: LaTeX Error: File `figures/missing.png' not found.
! Emergency stop.
<*> refined_paper_v3.tex
*** (job aborted, no legal \end found)
"#;

    fn sample_locate(p: &str) -> Option<String> {
        let p = p.trim_start_matches("./");
        (p == "refined_paper_v3.tex" || p == "sec/intro.tex" || p == "references.bib")
            .then(|| p.to_string())
    }

    #[test]
    fn log_errors_and_warnings_are_found() {
        let parsed = parse_latex_log(LOG, &sample_locate);
        let errs: Vec<(Option<&str>, Option<u32>, &str)> = parsed
            .errors
            .iter()
            .map(|e| (e.file.as_deref(), e.line, e.message.as_str()))
            .collect();
        assert_eq!(errs.len(), 5, "{errs:#?}");
        assert_eq!(errs[0].0, Some("refined_paper_v3.tex"));
        assert_eq!(errs[0].1, Some(391));
        assert_eq!(
            errs[0].2,
            "Undefined control sequence.（出错位置：Let $\\mathbf{x}\\in\\mathbb）"
        );
        assert_eq!(errs[1].1, Some(394));
        // Build output outside the source folder: located in the text only.
        assert_eq!(errs[2].0, None);
        assert_eq!(errs[2].1, None);
        assert!(errs[2].2.starts_with("refined_paper_v3.bbl 第 191 行：Misplaced alignment tab character &."), "{}", errs[2].2);
        assert!(errs[2].2.contains("ACS applied materials & interfaces"));
        assert_eq!(errs[3], (Some("sec/intro.tex"), Some(12), "LaTeX Error: File `figures/missing.png' not found."));
        assert_eq!(errs[4], (None, None, "Emergency stop."));
        assert!(parsed.errors.iter().all(|e| e.severity == IssueSeverity::Error));

        assert_eq!(parsed.warning_count, 4);
        let warns: Vec<&str> = parsed.warnings.iter().map(|w| w.message.as_str()).collect();
        assert_eq!(warns[0], "警告（natbib）：Citation `Smith2020' on page 7 undefined on input line 402.");
        assert_eq!(warns[1], "警告：Reference `fig:missing' on page 8 undefined on input line 455.");
        assert_eq!(
            warns[2],
            "警告：Font shape `OT1/cmr/m/n' in size <10.95> not available size <10> substituted on input line 97."
        );
        assert_eq!(warns[3], "警告（achemso）：Unknown option `foo' on input line 3.");
        assert!(parsed.warnings.iter().all(|w| w.severity == IssueSeverity::Warning && w.file.is_none()));
    }

    /// The same messages in a log wrapped at 79 columns (TeX's default).
    #[test]
    fn wrapped_logs_are_joined_back() {
        let bbl_error = "/Volumes/PODev/po-test/live-editor/run-copy/.live_preview/content_refinement_workdir/refined_paper_v3.bbl:311: Misplaced alignment tab character &.";
        let warning = "Package natbib Warning: Citation `averyveryverylongcitationkey2020' on page 7 undefined on input line 402.";
        let (e1, e2) = bbl_error.split_at(TEX_WRAP);
        let (w1, w2) = warning.split_at(TEX_WRAP);
        // e1 is exactly what TeX printed in the real wrapped log.
        assert_eq!(e1, "/Volumes/PODev/po-test/live-editor/run-copy/.live_preview/content_refinement_wo");
        let log = format!(
            "[38] [39]\n{e1}\n{e2}\n<argument> ACS applied materials &\n\
             l.311 ...\\emph{{ACS applied materials & interfaces}}\n\n{w1}\n{w2}\n\n\
             ./refined_paper_v3.tex:391: Undefined control sequence.\n\
             l.391 Let $\\mathbf{{x}}\\in\\mathbb\n"
        );
        let seen = std::cell::RefCell::new(Vec::new());
        let locate = |p: &str| {
            seen.borrow_mut().push(p.to_string());
            sample_locate(p)
        };
        let parsed = parse_latex_log(&log, &locate);
        assert_eq!(parsed.errors.len(), 2, "{:#?}", parsed.errors);
        assert_eq!(
            seen.borrow()[0],
            "/Volumes/PODev/po-test/live-editor/run-copy/.live_preview/content_refinement_workdir/refined_paper_v3.bbl"
        );
        assert!(parsed.errors[0].message.starts_with("refined_paper_v3.bbl 第 311 行："));
        assert_eq!(parsed.errors[1].file.as_deref(), Some("refined_paper_v3.tex"));
        assert_eq!(parsed.warning_count, 1);
        assert_eq!(
            parsed.warnings[0].message,
            "警告（natbib）：Citation `averyveryverylongcitationkey2020' on page 7 undefined on input line 402."
        );
    }

    #[test]
    fn file_line_error_detection() {
        assert_eq!(split_file_line_error("./a.tex:12: Oops"), Some(("./a.tex", 12, "Oops")));
        assert_eq!(
            split_file_line_error("C:\\paper\\a.tex:7: LaTeX Error: x"),
            Some(("C:\\paper\\a.tex", 7, "LaTeX Error: x"))
        );
        // Real line from a run under a folder with Chinese characters and a space.
        assert_eq!(
            split_file_line_error("/Volumes/PODev/po-test/live-editor/backend-only/中文 目录/run/.live_preview/content_refinement_workdir/refined_paper_v3.bbl:191: Misplaced alignment tab character &."),
            Some((
                "/Volumes/PODev/po-test/live-editor/backend-only/中文 目录/run/.live_preview/content_refinement_workdir/refined_paper_v3.bbl",
                191,
                "Misplaced alignment tab character &."
            ))
        );
        assert_eq!(split_file_line_error("LaTeX Warning: Reference `fig:3' on page 2"), None);
        assert_eq!(split_file_line_error("Chapter 1:12: not a file"), None);
        assert_eq!(split_file_line_error("l.12 \\foo"), None);
        assert_eq!(split_file_line_error("Package: infwarerr 2019/12/03 v1.5"), None);
    }

    #[test]
    fn bibtex_errors_are_found() {
        let blg = "This is BibTeX, Version 0.99e (TeX Live 2026)\n\
Database file #2: references.bib\n\
I was expecting a `,' or a `}'---line 23 of file references.bib\n\
 :        title = {Foo\n\
I'm skipping whatever remains of this entry\n\
Repeated entry---line 40 of file ./references.bib\n\
I couldn't open database file nothere.bib\n\
Warning--I didn't find a database entry for \"Missing2020\"\n\
(There were 3 error messages)\n";
        let issues = parse_blg(blg, &sample_locate);
        assert_eq!(issues.len(), 3, "{issues:#?}");
        assert_eq!(issues[0].file.as_deref(), Some("references.bib"));
        assert_eq!(issues[0].line, Some(23));
        assert_eq!(issues[0].message, "BibTeX：I was expecting a `,' or a `}'");
        assert_eq!(issues[1].file.as_deref(), Some("references.bib"));
        assert_eq!(issues[1].line, Some(40));
        assert_eq!(issues[2].file, None);
        assert_eq!(issues[2].message, "BibTeX：I couldn't open database file nothere.bib");
    }

    #[test]
    fn tex_paths_map_into_the_source_folder() {
        let run = sample_run("locate");
        let ctx = ctx_of(&run, "succeeded");
        let src = &ctx.source_dir;
        assert_eq!(rel_inside_source("./paper.tex", src).as_deref(), Some("paper.tex"));
        assert_eq!(rel_inside_source("sec/intro.tex", src).as_deref(), Some("sec/intro.tex"));
        let dotted = format!("{}/./sec/intro.tex", strip_windows_unc(src));
        assert_eq!(rel_inside_source(&dotted, src).as_deref(), Some("sec/intro.tex"));
        assert_eq!(rel_inside_source("achemso.cls", src), None);
        assert_eq!(rel_inside_source("../content_refinement_workdir/../paper.tex", src), None);
        assert_eq!(rel_inside_source("/usr/local/texlive/2026/texmf-dist/tex/latex/base/article.cls", src), None);
        assert_eq!(rel_inside_source("missing.tex", src), None);
    }

    // ---- SyncTeX ---------------------------------------------------------

    /// Real `synctex view -i 267:0:<abs>/refined_paper_v3.tex -o <pdf>`
    /// output on the scratch copy (first two of three records).
    const VIEW_OUT: &str = "This is SyncTeX command line utility, version 1.5
SyncTeX result begin
Output:/Volumes/PODev/po-test/live-editor/run-copy/.live_preview/content_refinement_workdir/refined_paper_v3.pdf
Page:5
x:113.967003
y:107.862801
h:71.999802
v:110.187393
W:468.000366
H:11.799335
before:
offset:-1
middle:
after:
Output:/Volumes/PODev/po-test/live-editor/run-copy/.live_preview/content_refinement_workdir/refined_paper_v3.pdf
Page:6
x:113.967003
y:103.524361
h:71.999802
v:110.187393
W:468.000366
H:11.799335
before:
offset:-1
middle:
after:
SyncTeX result end
";

    /// Real `synctex edit -o 5:200:105:<pdf>` output on the scratch copy.
    const EDIT_OUT: &str = "This is SyncTeX command line utility, version 1.5
SyncTeX result begin
Output:/Volumes/PODev/po-test/live-editor/run-copy/.live_preview/content_refinement_workdir/refined_paper_v3.pdf
Input:/Volumes/PODev/po-test/live-editor/run-copy/content_refinement_workdir/./refined_paper_v3.tex
Line:267
Column:-1
Offset:0
Context:
SyncTeX result end
";

    #[test]
    fn synctex_view_output_is_parsed() {
        let b = parse_synctex_view(VIEW_OUT).unwrap();
        assert_eq!(b.page, 5);
        assert!((b.h - 71.999802).abs() < 1e-9);
        assert!((b.v - 110.187393).abs() < 1e-9);
        assert!((b.width - 468.000366).abs() < 1e-9);
        assert!((b.height - 11.799335).abs() < 1e-9);
        let json = serde_json::to_value(&b).unwrap();
        for key in ["page", "h", "v", "width", "height"] {
            assert!(json.get(key).is_some(), "{key}");
        }

        // Nothing found: only the banner, or a warning.
        assert_eq!(parse_synctex_view("This is SyncTeX command line utility, version 1.5\n"), None);
        assert_eq!(
            parse_synctex_view("This is SyncTeX command line utility, version 1.5\nSyncTeX Warning: No tag for /x/nonexist.tex\n"),
            None
        );
        assert_eq!(parse_synctex_view("SyncTeX result begin\nOutput:x.pdf\nPage:0\nh:1\nv:2\nSyncTeX result end\n"), None);
    }

    #[test]
    fn synctex_edit_output_is_parsed() {
        assert_eq!(
            parse_synctex_edit(EDIT_OUT),
            Some((
                "/Volumes/PODev/po-test/live-editor/run-copy/content_refinement_workdir/./refined_paper_v3.tex".to_string(),
                267
            ))
        );
        let windows = "SyncTeX result begin\nOutput:C:/r/out.pdf\nInput:C:\\r\\src\\./main.tex\nLine:12\nColumn:-1\nSyncTeX result end\n";
        assert_eq!(parse_synctex_edit(windows), Some(("C:\\r\\src\\./main.tex".to_string(), 12)));
        assert_eq!(parse_synctex_edit("This is SyncTeX command line utility, version 1.5\n"), None);
        assert_eq!(parse_synctex_edit("SyncTeX result begin\nOutput:o.pdf\nInput:/a.tex\nLine:0\nSyncTeX result end\n"), None);

        // The Input path maps back to the source folder.
        let run = sample_run("edit_map");
        let ctx = ctx_of(&run, "succeeded");
        let out = EDIT_OUT.replace(
            "/Volumes/PODev/po-test/live-editor/run-copy/content_refinement_workdir/./refined_paper_v3.tex",
            &format!("{}/./sec/intro.tex", strip_windows_unc(&ctx.source_dir)),
        );
        let (input, line) = parse_synctex_edit(&out).unwrap();
        assert_eq!(line, 267);
        assert_eq!(rel_inside_source(&input, &ctx.source_dir).as_deref(), Some("sec/intro.tex"));
    }

    #[test]
    fn sync_needs_the_pdf_and_its_synctex_file() {
        let rt = tokio::runtime::Runtime::new().unwrap();
        let run = sample_run("sync_stale");
        let ctx = ctx_of(&run, "succeeded");
        let check = |ctx: &EditContext| {
            let view = rt.block_on(sync_view_in(ctx, "paper.tex", "paper.tex", 3, 1));
            assert_eq!(view.unwrap_err(), ERR_SYNC_STALE);
            let edit = rt.block_on(sync_edit_in(ctx, "paper.tex", 1, 100.0, 100.0));
            assert_eq!(edit.unwrap_err(), ERR_SYNC_STALE);
        };
        // Never compiled.
        check(&ctx);
        // A PDF whose SyncTeX file pdfTeX deleted (the last build shipped
        // no pages).
        let out = run.0.join(".live_preview/content_refinement_workdir");
        fs::create_dir_all(&out).unwrap();
        fs::write(out.join("paper.pdf"), "%PDF").unwrap();
        check(&ctx);
        // A SyncTeX file without its PDF.
        fs::remove_file(out.join("paper.pdf")).unwrap();
        fs::write(out.join("paper.synctex.gz"), "x").unwrap();
        check(&ctx);
        // Both there.
        fs::write(out.join("paper.pdf"), "%PDF").unwrap();
        let prep = sync_prep(&ctx, "paper.tex").unwrap();
        assert_eq!(prep.pdf, out.canonicalize().unwrap().join("paper.pdf"));
    }

    #[test]
    fn up_to_date_counts_only_when_the_last_build_wrote_the_pdf() {
        let run = sample_run("uptodate");
        let ctx = ctx_of(&run, "succeeded");
        let out = run.0.join(".live_preview/content_refinement_workdir");
        fs::create_dir_all(&out).unwrap();
        fs::write(out.join("paper.pdf"), "%PDF old").unwrap();
        let up_to_date = ToolRun {
            stdout: "Latexmk: Nothing to do for 'paper.tex'.\nLatexmk: All targets (paper.pdf) are up-to-date\n".into(),
            stderr: String::new(),
            timed_out: false,
        };
        let finish = |log: &str| {
            fs::write(out.join("paper.log"), log).unwrap();
            let prep = prepare_compile(&ctx, "paper.tex").unwrap();
            finish_compile(&ctx, &prep, &up_to_date, Instant::now())
        };

        // The last build shipped no pages: the PDF on disk is an older one.
        let r = finish("This is pdfTeX, Version 3.141592653-2.6-1.40.29\nNo pages of output.\n");
        assert!(!r.ok);
        assert!(r.pdf_path.is_some());
        assert_eq!(r.issues[0].message, "这次没有生成新的 PDF，请看下面的日志。");

        // It stopped on a fatal error: that error is reported.
        let r = finish("This is pdfTeX\n! Emergency stop.\n<*> paper.tex\nNo pages of output.\n");
        assert!(!r.ok);
        assert_eq!(r.issues[0].message, "Emergency stop.");

        // It wrote the PDF: still current.
        let r = finish("This is pdfTeX\nOutput written on /r/.live_preview/src/paper.pdf (3 pages, 1234 bytes).\n");
        assert!(r.ok, "{:?}", r.issues);
        assert!(r.issues.is_empty());

        // A new PDF counts without that line too.
        fs::write(out.join("paper.log"), "This is pdfTeX\n").unwrap();
        let prep = prepare_compile(&ctx, "paper.tex").unwrap();
        fs::write(out.join("paper.pdf"), "%PDF new and longer").unwrap();
        let built = ToolRun { stdout: String::new(), stderr: String::new(), timed_out: false };
        assert!(finish_compile(&ctx, &prep, &built, Instant::now()).ok);
    }

    #[test]
    fn compile_result_serialises_with_contract_names() {
        let r = LiveCompileResult {
            ok: true,
            pdf_path: Some("/r/.live_preview/src/main.pdf".into()),
            issues: vec![plain_issue("x")],
            warnings: 2,
            duration_ms: 5,
            log_tail: "t".into(),
        };
        let json = serde_json::to_value(&r).unwrap();
        for key in ["ok", "pdfPath", "issues", "warnings", "durationMs", "logTail"] {
            assert!(json.get(key).is_some(), "{key}");
        }
        assert_eq!(json["issues"][0]["severity"], "error");
        assert!(json["issues"][0]["file"].is_null());
        let f = serde_json::to_value(LiveEditFile {
            file: "a.tex".into(),
            content: String::new(),
            mtime_ms: 1,
            has_backup: true,
        })
        .unwrap();
        assert!(f.get("mtimeMs").is_some());
        assert_eq!(f["hasBackup"], true);
        let s = serde_json::to_value(SyncSource { file: "a.tex".into(), line: 3 }).unwrap();
        assert_eq!(s["line"], 3);
    }

    #[test]
    fn tail_keeps_the_last_lines() {
        let text = (1..=100).map(|n| n.to_string()).collect::<Vec<_>>().join("\n");
        let tail = tail_lines(&text, 40);
        assert_eq!(tail.lines().count(), 40);
        assert!(tail.starts_with("61\n") && tail.ends_with("100"));
        assert_eq!(tail_lines("", 40), "");
    }
}
