//! "导出 Overleaf 包": zips a run's final LaTeX sources so they can be
//! uploaded to Overleaf (New Project -> Upload Project) or compiled elsewhere.
//!
//! Read-only with respect to the run: nothing inside the run directory is
//! created, changed or removed, and the archive may not be written into it.
//!
//! What goes in:
//! * the source folder is `content_refinement_workdir` when it holds a `.tex`
//!   at its top level (the folder ContentRefinementAgent compiled the final
//!   paper in), otherwise `latex_writeup`;
//! * of the top-level `.tex` files that are whole documents (`\documentclass`),
//!   only the main one (see [`pick_main_tex`]), so Overleaf's main-file
//!   detection cannot pick an intermediate draft; other `.tex` files (inputs)
//!   are kept;
//! * `.bib/.bst/.sty/.cls` and friends, and figures (`png/jpg/pdf/eps/...`)
//!   in any sub-folder;
//! * never: LaTeX build output (`aux/log/out/synctex/bbl/blg/...`), a PDF that
//!   is the compiled output of a `.tex` beside it, review screenshots and
//!   peer reviews, hidden files, symlinks.
//!
//! The `.bib` keeps its name, but holds only the entries the paper cites when
//! the citation audit wrote a matching `references_cited.bib` (see
//! [`cited_bib_swap`]). BibTeX prints cited entries only, so the compiled
//! paper is the same either way; the archive just no longer carries library
//! entries that were found during the search and never cited.
//!
//! Paths inside the archive are relative to the source folder and always use
//! forward slashes.

use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use chrono::{DateTime as ChronoDateTime, Datelike, Local, Timelike};
use uuid::Uuid;
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, DateTime as ZipDateTime, ZipWriter};

use crate::env_config::strip_windows_unc;
use crate::models::LatexExportBib;

/// Folder ContentRefinementAgent compiles the final paper in.
pub const REFINEMENT_DIR: &str = "content_refinement_workdir";
/// Folder SectionWritingAgent writes the draft into.
pub const WRITEUP_DIR: &str = "latex_writeup";

/// LaTeX sources (lower-case extensions).
const SOURCE_EXTS: &[&str] = &[
    "tex", "bib", "bst", "sty", "cls", "clo", "cfg", "def", "ldf", "fd", "bbx", "cbx", "lbx",
    "dbx", "ist",
];
/// Figures and other graphics LaTeX can include.
const GRAPHIC_EXTS: &[&str] = &[
    "png", "jpg", "jpeg", "pdf", "eps", "ps", "svg", "gif", "tif", "tiff", "bmp",
];
/// Already-compressed formats are stored as-is instead of deflated again.
const STORED_EXTS: &[&str] = &["png", "jpg", "jpeg", "gif"];
/// Sub-folders that hold pipeline bookkeeping, not paper sources.
const SKIP_DIRS: &[&str] = &["pdf_screenshots", "peer_reviews", "__MACOSX", "__pycache__"];
/// Final document name written by ContentRefinementAgent.
const FINAL_TEX: &str = "final_refined_paper.tex";
/// Fallback main documents, in order (SectionWritingAgent output, then template).
const FALLBACK_TEX: &[&str] = &["raw_draft_paper.tex", "template.tex"];
/// Guard against pathological trees (symlinks are never followed anyway).
const MAX_DEPTH: usize = 12;
/// Written by the citation audit at the run root: the library with only the
/// entries the paper cites.
pub const CITED_BIB: &str = "references_cited.bib";
/// Written by the citation audit at the run root.
const AUDIT_JSON: &str = "citation_audit.json";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExportEntry {
    pub abs: PathBuf,
    /// Path inside the archive, forward slashes.
    pub rel: String,
}

fn lower_ext(p: &Path) -> String {
    p.extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_ascii_lowercase())
        .unwrap_or_default()
}

fn is_hidden(name: &str) -> bool {
    name.starts_with('.')
}

/// Regular files (not symlinks) directly inside `dir`.
fn plain_files(dir: &Path) -> Vec<PathBuf> {
    let Ok(entries) = fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut out: Vec<PathBuf> = entries
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_file()).unwrap_or(false))
        .map(|e| e.path())
        .collect();
    out.sort();
    out
}

fn has_top_level_tex(dir: &Path) -> bool {
    dir.is_dir() && plain_files(dir).iter().any(|p| lower_ext(p) == "tex")
}

/// True for a real folder inside `run_dir`: a symlinked (or junction)
/// source folder could otherwise pull files from outside the run.
fn is_real_subdir(run_dir: &Path, dir: &Path) -> bool {
    let real_dir = fs::symlink_metadata(dir)
        .map(|m| m.file_type().is_dir())
        .unwrap_or(false);
    if !real_dir {
        return false;
    }
    match (run_dir.canonicalize(), dir.canonicalize()) {
        (Ok(run), Ok(d)) => d.starts_with(&run),
        _ => false,
    }
}

/// The folder whose sources are exported, or `None` when the run has no
/// LaTeX yet.
pub fn export_source_dir(run_dir: &Path) -> Option<PathBuf> {
    [REFINEMENT_DIR, WRITEUP_DIR]
        .iter()
        .map(|name| run_dir.join(name))
        .find(|dir| is_real_subdir(run_dir, dir) && has_top_level_tex(dir))
}

/// True when the file declares `\documentclass` outside a comment, i.e. it is
/// a complete document rather than an `\input` fragment.
fn is_tex_document(path: &Path) -> bool {
    let Ok(bytes) = fs::read(path) else {
        return false;
    };
    String::from_utf8_lossy(&bytes).lines().any(|line| {
        let code = match line.find('%') {
            // A `\%` is an escaped percent sign, not a comment; good enough
            // here because `\documentclass` comes first on its line anyway.
            Some(i) if i == 0 || line.as_bytes()[i - 1] != b'\\' => &line[..i],
            _ => line,
        };
        code.contains("\\documentclass")
    })
}

fn modified(p: &Path) -> SystemTime {
    fs::metadata(p)
        .and_then(|m| m.modified())
        .unwrap_or(SystemTime::UNIX_EPOCH)
}

fn file_name_of(p: &Path) -> String {
    p.file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default()
}

/// The top-level `.tex` document that is the paper:
/// 1. `final_refined_paper.tex` (written by ContentRefinementAgent at the end);
/// 2. else the newest document that was compiled (a same-named `.pdf` beside
///    it): the last refinement round that produced a PDF;
/// 3. else `raw_draft_paper.tex`, then `template.tex`;
/// 4. else the newest document.
///
/// `None` when no top-level `.tex` declares `\documentclass`.
pub fn pick_main_tex(dir: &Path) -> Option<String> {
    let docs: Vec<PathBuf> = plain_files(dir)
        .into_iter()
        .filter(|p| lower_ext(p) == "tex" && is_tex_document(p))
        .collect();
    if docs.is_empty() {
        return None;
    }
    let named = |name: &str| docs.iter().find(|p| file_name_of(p) == name).cloned();
    let newest = |items: Vec<&PathBuf>| {
        items
            .into_iter()
            .max_by(|a, b| modified(a).cmp(&modified(b)).then_with(|| a.cmp(b)))
            .cloned()
    };

    let chosen = named(FINAL_TEX)
        .or_else(|| {
            newest(
                docs.iter()
                    .filter(|p| p.with_extension("pdf").is_file())
                    .collect(),
            )
        })
        .or_else(|| FALLBACK_TEX.iter().find_map(|n| named(n)))
        .or_else(|| newest(docs.iter().collect()));
    chosen.map(|p| file_name_of(&p))
}

/// Files of `src` that belong in the Overleaf archive, sorted by archive path.
pub fn collect_export_files(src: &Path) -> Result<Vec<ExportEntry>, String> {
    if !src.is_dir() {
        return Err(format!("LaTeX 源码目录不存在：{}", src.display()));
    }
    let main = pick_main_tex(src);
    let mut out = Vec::new();
    walk(src, src, 0, main.as_deref(), &mut out);
    out.sort_by(|a, b| a.rel.cmp(&b.rel));
    Ok(out)
}

fn walk(base: &Path, dir: &Path, depth: usize, main: Option<&str>, out: &mut Vec<ExportEntry>) {
    if depth > MAX_DEPTH {
        return;
    }
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    let mut entries: Vec<_> = entries.flatten().collect();
    entries.sort_by_key(|e| e.file_name());

    // Stems of the .tex files in this folder: a PDF with one of these stems is
    // that document's compiled output, not a figure.
    let tex_stems: Vec<String> = entries
        .iter()
        .map(|e| e.path())
        .filter(|p| lower_ext(p) == "tex")
        .filter_map(|p| p.file_stem().map(|s| s.to_string_lossy().to_string()))
        .collect();

    for entry in entries {
        let name = entry.file_name().to_string_lossy().to_string();
        if is_hidden(&name) {
            continue;
        }
        // file_type() does not follow symlinks: links are skipped entirely so
        // the archive can never pull in files from outside the run.
        let Ok(ft) = entry.file_type() else { continue };
        let path = entry.path();
        if ft.is_dir() {
            if !SKIP_DIRS.iter().any(|d| d.eq_ignore_ascii_case(&name)) {
                walk(base, &path, depth + 1, main, out);
            }
            continue;
        }
        if !ft.is_file() {
            continue;
        }
        let ext = lower_ext(&path);
        let is_source = SOURCE_EXTS.contains(&ext.as_str());
        let is_graphic = GRAPHIC_EXTS.contains(&ext.as_str());
        if !is_source && !is_graphic {
            continue;
        }
        if ext == "pdf" {
            let stem = path
                .file_stem()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default();
            if tex_stems.contains(&stem) {
                continue; // compiled output of a .tex beside it
            }
        }
        if depth == 0 && ext == "tex" && is_tex_document(&path) && main != Some(name.as_str()) {
            continue; // an intermediate draft / template copy, not the paper
        }
        let Ok(rel) = path.strip_prefix(base) else {
            continue;
        };
        let rel = rel
            .components()
            .map(|c| c.as_os_str().to_string_lossy().to_string())
            .collect::<Vec<_>>()
            .join("/");
        out.push(ExportEntry { abs: path, rel });
    }
}

/// Lower-cased keys of the entries in BibTeX text. `@string`, `@preamble` and
/// `@comment` blocks are not entries; an `@` that does not start a block (an
/// e-mail address in a note) is skipped.
fn bib_keys(text: &str) -> BTreeSet<String> {
    let bytes = text.as_bytes();
    let mut keys = BTreeSet::new();
    let mut i = 0;
    while let Some(off) = text[i..].find('@') {
        let at = i + off;
        let mut j = at + 1;
        while j < bytes.len() && bytes[j].is_ascii_alphabetic() {
            j += 1;
        }
        i = j.max(at + 1);
        let etype = text[at + 1..j].to_ascii_lowercase();
        let mut k = j;
        while k < bytes.len() && bytes[k].is_ascii_whitespace() {
            k += 1;
        }
        if etype.is_empty() || k >= bytes.len() || (bytes[k] != b'{' && bytes[k] != b'(') {
            continue;
        }
        i = k + 1;
        if matches!(etype.as_str(), "string" | "preamble" | "comment") {
            continue;
        }
        let rest = &text[k + 1..];
        let end = rest.find([',', '}', ')', '\n']).unwrap_or(rest.len());
        if rest[end..].starts_with(',') {
            let key = rest[..end].trim();
            if !key.is_empty() {
                keys.insert(key.to_ascii_lowercase());
            }
        }
    }
    keys
}

/// A regular file (not a symlink) inside `dir`.
fn plain_file_inside(dir: &Path, p: &Path) -> bool {
    let plain = fs::symlink_metadata(p)
        .map(|m| m.file_type().is_file())
        .unwrap_or(false);
    plain
        && match (dir.canonicalize(), p.canonicalize()) {
            (Ok(d), Ok(f)) => f.starts_with(&d),
            _ => false,
        }
}

/// The `.aux` of a compiled copy of the main document: a top-level `.tex` of
/// `src` with exactly the content of `main`, and an `.aux` written after it.
/// The pipeline compiles each round under its own name and saves the chosen
/// round as `main`, so the copy is usually the round that was kept.
fn compiled_twin_aux(src: &Path, main: &str) -> Option<PathBuf> {
    let main_text = fs::read(src.join(main)).ok()?;
    plain_files(src)
        .into_iter()
        .filter(|p| lower_ext(p) == "tex")
        .filter(|p| fs::read(p).map(|t| t == main_text).unwrap_or(false))
        .filter_map(|p| {
            let aux = p.with_extension("aux");
            (plain_file_inside(src, &aux) && modified(&aux) >= modified(&p)).then_some(aux)
        })
        .max_by(|a, b| modified(a).cmp(&modified(b)).then_with(|| a.cmp(b)))
}

/// Lower-cased keys of the `\citation{...}` lines LaTeX wrote into `aux` and
/// into the `.aux` files it `\@input`s (one per `\include`): every key BibTeX
/// is asked for, whichever command, macro or input file cited it. `None` when
/// a file cannot be read or parsed, the document does not use BibTeX (no
/// `\bibdata`, e.g. biblatex), or it cites everything (`\nocite{*}`).
fn aux_citations(src: &Path, aux: &Path) -> Option<BTreeSet<String>> {
    let mut keys = BTreeSet::new();
    let mut bibdata = false;
    let mut todo = vec![aux.to_path_buf()];
    let mut read = 0;
    while let Some(path) = todo.pop() {
        read += 1;
        if read > 64 {
            return None;
        }
        let text = String::from_utf8_lossy(&fs::read(&path).ok()?).to_string();
        for line in text.lines() {
            let line = line.trim();
            if let Some(rest) = line.strip_prefix("\\citation{") {
                for key in rest.strip_suffix('}')?.split(',') {
                    let key = key.trim();
                    if key == "*" {
                        return None;
                    }
                    if !key.is_empty() {
                        keys.insert(key.to_ascii_lowercase());
                    }
                }
            } else if line.starts_with("\\bibdata{") {
                bibdata = true;
            } else if let Some(rest) = line.strip_prefix("\\@input{") {
                let child = src.join(rest.strip_suffix('}')?);
                if !plain_file_inside(src, &child) {
                    return None;
                }
                todo.push(child);
            }
        }
    }
    bibdata.then_some(keys)
}

/// Whether the archive's `.bib` can hold only the cited entries: what it then
/// holds, and the file to take its content from. `None` ships the library as
/// it is, which is what happens unless all of this holds:
/// * `citation_audit.json` audited this folder's main `.tex` and its only
///   `.bib`, found no citation missing from it, and names
///   `references_cited.bib`, a plain file at the run root;
/// * `references_cited.bib` is not older than either of them, so an edit made
///   after the audit cannot be lost;
/// * it holds every entry the audit found cited and nothing from elsewhere,
///   and it leaves at least one library entry out;
/// * a compiled copy of the main document exists, and every key its `.aux`
///   asked BibTeX for is in `references_cited.bib` (BibTeX matches keys
///   case-insensitively and sees citations the audit's parser may not).
pub fn cited_bib_swap(
    run_dir: &Path,
    src: &Path,
    entries: &[ExportEntry],
) -> Option<(LatexExportBib, PathBuf)> {
    let raw = fs::read_to_string(run_dir.join(AUDIT_JSON)).ok()?;
    let audit: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let field = |k: &str| {
        audit
            .get(k)
            .and_then(|v| v.as_str())
            .map(|v| v.replace('\\', "/"))
    };
    if field("cited_bib_file")? != CITED_BIB {
        return None;
    }
    let bibs: Vec<&ExportEntry> = entries
        .iter()
        .filter(|e| lower_ext(&e.abs) == "bib")
        .collect();
    let [bib] = bibs.as_slice() else {
        return None; // several .bib files: the audit's single copy would duplicate entries
    };
    let src_name = file_name_of(src);
    if field("bib_file")? != format!("{src_name}/{}", bib.rel) {
        return None;
    }
    let main = pick_main_tex(src)?;
    if field("tex_file")? != format!("{src_name}/{main}") {
        return None;
    }
    let cited_path = run_dir.join(CITED_BIB);
    let plain = fs::symlink_metadata(&cited_path)
        .map(|m| m.file_type().is_file())
        .unwrap_or(false);
    if !plain {
        return None;
    }
    let written = modified(&cited_path);
    if written < modified(&bib.abs) || written < modified(&src.join(&main)) {
        return None;
    }
    let cited: BTreeSet<String> = audit
        .get("entries")?
        .as_array()?
        .iter()
        .filter(|e| e.get("cited").and_then(|v| v.as_bool()) == Some(true))
        .filter_map(|e| e.get("key").and_then(|v| v.as_str()))
        .map(|k| k.to_ascii_lowercase())
        .collect();
    let missing = audit.get("missing_in_bib")?.as_array()?;
    if !missing.is_empty() {
        return None;
    }
    let kept = bib_keys(&String::from_utf8_lossy(&fs::read(&cited_path).ok()?));
    let library = bib_keys(&String::from_utf8_lossy(&fs::read(&bib.abs).ok()?));
    if kept.is_empty() || !cited.is_subset(&kept) || !kept.is_subset(&library) {
        return None;
    }
    let asked = aux_citations(src, &compiled_twin_aux(src, &main)?)?;
    if asked.is_empty() || !asked.is_subset(&kept) {
        return None;
    }
    let dropped = library.len() - kept.len();
    if dropped == 0 {
        return None;
    }
    Some((
        LatexExportBib {
            file: bib.rel.clone(),
            kept: kept.len(),
            dropped,
        },
        cited_path,
    ))
}

/// Validates and normalises the archive destination: an absolute path, `.zip`
/// appended when missing, an existing parent folder, and not inside the run
/// directory (`run_canon` must be canonical).
pub fn resolve_destination(run_canon: &Path, dest_path: &str) -> Result<PathBuf, String> {
    let raw = dest_path.trim();
    if raw.is_empty() {
        return Err("请选择导出位置。".to_string());
    }
    let mut dest = PathBuf::from(raw);
    if !dest.is_absolute() {
        return Err(format!("导出位置必须是完整路径：{raw}"));
    }
    if lower_ext(&dest) != "zip" {
        dest = PathBuf::from(format!("{raw}.zip"));
    }
    let file_name = dest
        .file_name()
        .map(|n| n.to_os_string())
        .ok_or_else(|| format!("导出位置缺少文件名：{raw}"))?;
    let parent = dest
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .ok_or_else(|| format!("导出位置无效：{raw}"))?;
    if !parent.is_dir() {
        return Err(format!("导出位置所在的文件夹不存在：{}", parent.display()));
    }
    let parent_canon = parent
        .canonicalize()
        .map_err(|e| format!("无法解析导出位置（{}）：{e}", parent.display()))?;
    if parent_canon.starts_with(run_canon) {
        return Err("不能导出到运行目录内部，请选择运行目录以外的位置。".to_string());
    }
    let dest = parent_canon.join(file_name);
    if dest.is_dir() {
        return Err(format!(
            "导出位置是一个文件夹，请输入 .zip 文件名：{}",
            dest.display()
        ));
    }
    Ok(dest)
}

fn zip_time(p: &Path) -> Option<ZipDateTime> {
    let t: ChronoDateTime<Local> = fs::metadata(p).ok()?.modified().ok()?.into();
    ZipDateTime::from_date_and_time(
        u16::try_from(t.year()).ok()?,
        t.month() as u8,
        t.day() as u8,
        t.hour() as u8,
        t.minute() as u8,
        t.second().min(59) as u8,
    )
    .ok()
}

/// Where the zip is built before it is moved into place. On macOS that is the
/// temp folder: the save dialog lets the app write the file the user chose,
/// but a hidden sibling in ~/Downloads, ~/Desktop or ~/Documents would raise a
/// separate "access this folder" prompt. Elsewhere it is the destination's
/// folder, so the final step is an atomic rename.
fn staging_dir(dest: &Path) -> PathBuf {
    if cfg!(target_os = "macos") {
        std::env::temp_dir()
    } else {
        dest.parent()
            .unwrap_or_else(|| Path::new("."))
            .to_path_buf()
    }
}

/// Copy `tmp` to `dest`. If that fails, a half-written `dest` is removed, but
/// a file that was already there and was not touched (e.g. read-only) stays.
fn copy_into_place(tmp: &Path, dest: &Path) -> Result<(), String> {
    let stamp = |p: &Path| fs::metadata(p).ok().map(|m| (m.len(), m.modified().ok()));
    let before = stamp(dest);
    match fs::copy(tmp, dest) {
        Ok(_) => Ok(()),
        Err(e) => {
            if before.is_none() || stamp(dest) != before {
                let _ = fs::remove_file(dest);
            }
            Err(format!("无法保存到 {}：{e}", dest.display()))
        }
    }
}

fn write_zip(dest: &Path, entries: &[ExportEntry]) -> Result<(), String> {
    write_zip_via(dest, entries, &staging_dir(dest))
}

fn write_zip_via(dest: &Path, entries: &[ExportEntry], staging: &Path) -> Result<(), String> {
    let parent = dest.parent().unwrap_or_else(|| Path::new("."));
    let tmp = staging.join(format!(
        ".{}.{}.part",
        dest.file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default(),
        Uuid::new_v4().simple()
    ));

    let result = (|| -> Result<(), String> {
        let file = File::create(&tmp)
            .map_err(|e| format!("无法创建导出文件（{}）：{e}", tmp.display()))?;
        let mut zip = ZipWriter::new(file);
        for entry in entries {
            let ext = lower_ext(&entry.abs);
            let method = if STORED_EXTS.contains(&ext.as_str()) {
                CompressionMethod::Stored
            } else {
                CompressionMethod::Deflated
            };
            let mut opts = SimpleFileOptions::default()
                .compression_method(method)
                .unix_permissions(0o644)
                .large_file(
                    fs::metadata(&entry.abs)
                        .map(|m| m.len() >= u32::MAX as u64)
                        .unwrap_or(false),
                );
            if let Some(t) = zip_time(&entry.abs) {
                opts = opts.last_modified_time(t);
            }
            zip.start_file(entry.rel.as_str(), opts)
                .map_err(|e| format!("写入压缩包失败（{}）：{e}", entry.rel))?;
            let mut src = File::open(&entry.abs)
                .map_err(|e| format!("读取文件失败（{}）：{e}", entry.abs.display()))?;
            io::copy(&mut src, &mut zip)
                .map_err(|e| format!("写入压缩包失败（{}）：{e}", entry.rel))?;
        }
        let mut file = zip.finish().map_err(|e| format!("写入压缩包失败：{e}"))?;
        file.flush().map_err(|e| format!("写入压缩包失败：{e}"))?;
        file.sync_all()
            .map_err(|e| format!("写入压缩包失败：{e}"))?;
        drop(file);
        if staging == parent {
            return fs::rename(&tmp, dest)
                .map_err(|e| format!("无法保存到 {}：{e}", dest.display()));
        }
        // Built in the temp folder (macOS): moving it into place replaces the
        // old file in one step on the same disk. Another disk, or a refused
        // move, falls back to a copy.
        if fs::rename(&tmp, dest).is_ok() {
            return Ok(());
        }
        let copied = copy_into_place(&tmp, dest);
        let _ = fs::remove_file(&tmp);
        copied
    })();

    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}

/// Zips the run's final LaTeX sources into `dest_path` (".zip" is appended
/// when missing) and returns the path written.
pub fn export_run_latex(run_dir: &str, dest_path: &str) -> Result<String, String> {
    let run = Path::new(run_dir);
    if run_dir.trim().is_empty() || !run.is_dir() {
        return Err(format!("运行目录不存在：{run_dir}"));
    }
    let run_canon = run
        .canonicalize()
        .map_err(|e| format!("无法解析运行目录（{run_dir}）：{e}"))?;
    let src = export_source_dir(&run_canon).ok_or_else(|| {
        format!("这次运行还没有可导出的 LaTeX 源码：{REFINEMENT_DIR} 和 {WRITEUP_DIR} 中都没有 .tex 文件。")
    })?;
    let dest = resolve_destination(&run_canon, dest_path)?;
    let mut entries = collect_export_files(&src)?;
    if !entries
        .iter()
        .any(|e| e.rel.to_ascii_lowercase().ends_with(".tex"))
    {
        return Err("没有找到可导出的 .tex 文件。".to_string());
    }
    if let Some((bib, cited)) = cited_bib_swap(&run_canon, &src, &entries) {
        if let Some(e) = entries.iter_mut().find(|e| e.rel == bib.file) {
            e.abs = cited; // same archive name, so \bibliography{...} still finds it
        }
    }
    write_zip(&dest, &entries)?;
    Ok(strip_windows_unc(&dest))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    /// Temp dir removed on drop, even when an assertion fails.
    struct TempDir(PathBuf);

    impl TempDir {
        fn new(tag: &str) -> Self {
            let dir = std::env::temp_dir().join(format!("po_export_{tag}_{}", Uuid::new_v4()));
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

    const DOC: &str = "\\documentclass{article}\n\\begin{document}x\\end{document}\n";

    fn rels(entries: &[ExportEntry]) -> Vec<&str> {
        entries.iter().map(|e| e.rel.as_str()).collect()
    }

    /// A content_refinement_workdir as ContentRefinementAgent leaves it.
    fn refinement_run(run: &TempDir) {
        let w = REFINEMENT_DIR;
        for doc in [
            "template.tex",
            "raw_draft_paper.tex",
            "initial_draft.tex",
            "content_refinement_v1.tex",
            "format_candidate_v0.tex",
            "final_refined_paper.tex",
        ] {
            run.put(&format!("{w}/{doc}"), DOC);
        }
        run.put(
            &format!("{w}/macros.tex"),
            "\\newcommand{\\x}{y}\n% \\documentclass only in a comment\n",
        );
        run.put(&format!("{w}/sections/intro.tex"), "\\section{Intro}\n");
        run.put(&format!("{w}/references.bib"), "@article{a, title={A}}\n");
        run.put(&format!("{w}/ieee_fullname.bst"), "ENTRY{}{}{}\n");
        run.put(&format!("{w}/cvpr.sty"), "\\ProvidesPackage{cvpr}\n");
        run.put(&format!("{w}/custom.cls"), "\\ProvidesClass{custom}\n");
        run.put(&format!("{w}/figures/arch.png"), "png");
        run.put(&format!("{w}/figures/plot.pdf"), "%PDF figure");
        run.put(&format!("{w}/figures/info.json"), "[]");
        // build junk
        for junk in [
            "final_refined_paper.pdf",
            "initial_draft.pdf",
            "final_refined_paper.aux",
            "final_refined_paper.log",
            "final_refined_paper.out",
            "final_refined_paper.bbl",
            "final_refined_paper.blg",
            "final_refined_paper.synctex.gz",
            "final_refined_paper.fdb_latexmk",
            "content_refinement_worklog.json",
            ".DS_Store",
            "pdf_screenshots/content_refinement/v0/page_1.png",
            "peer_reviews/review_initial.json",
        ] {
            run.put(&format!("{w}/{junk}"), "junk");
        }
        // the draft folder exists too, but the refinement folder wins
        run.put(&format!("{WRITEUP_DIR}/raw_draft_paper.tex"), DOC);
        run.put("final_paper.pdf", "%PDF");
    }

    #[test]
    fn selects_final_sources_and_excludes_build_junk() {
        let run = TempDir::new("select");
        refinement_run(&run);
        let src = export_source_dir(&run.0).unwrap();
        assert!(src.ends_with(REFINEMENT_DIR));
        assert_eq!(
            pick_main_tex(&src).as_deref(),
            Some("final_refined_paper.tex")
        );
        let entries = collect_export_files(&src).unwrap();
        assert_eq!(
            rels(&entries),
            vec![
                "custom.cls",
                "cvpr.sty",
                "figures/arch.png",
                "figures/plot.pdf",
                "final_refined_paper.tex",
                "ieee_fullname.bst",
                "macros.tex",
                "references.bib",
                "sections/intro.tex",
            ]
        );
    }

    #[test]
    fn main_tex_fallbacks() {
        let dir = TempDir::new("main");
        dir.put("template.tex", DOC);
        dir.put("raw_draft_paper.tex", DOC);
        assert_eq!(
            pick_main_tex(&dir.0).as_deref(),
            Some("raw_draft_paper.tex")
        );
        // a compiled refinement round beats the uncompiled drafts
        dir.put("content_refinement_v2.tex", DOC);
        dir.put("content_refinement_v2.pdf", "%PDF");
        assert_eq!(
            pick_main_tex(&dir.0).as_deref(),
            Some("content_refinement_v2.tex")
        );
        dir.put(FINAL_TEX, DOC);
        assert_eq!(pick_main_tex(&dir.0).as_deref(), Some(FINAL_TEX));

        let only_fragments = TempDir::new("frag");
        only_fragments.put("a.tex", "\\section{A}");
        assert_eq!(pick_main_tex(&only_fragments.0), None);
        // with no document at all every .tex is kept
        assert_eq!(
            rels(&collect_export_files(&only_fragments.0).unwrap()),
            vec!["a.tex"]
        );
    }

    #[test]
    fn falls_back_to_latex_writeup() {
        let run = TempDir::new("writeup");
        run.put(
            &format!("{REFINEMENT_DIR}/content_refinement_worklog.json"),
            "{}",
        );
        run.put(&format!("{WRITEUP_DIR}/template.tex"), DOC);
        run.put(&format!("{WRITEUP_DIR}/raw_draft_paper.tex"), DOC);
        run.put(&format!("{WRITEUP_DIR}/references.bib"), "@a{}");
        run.put(&format!("{WRITEUP_DIR}/figures/fig1.jpg"), "jpg");
        let src = export_source_dir(&run.0).unwrap();
        assert!(src.ends_with(WRITEUP_DIR));
        assert_eq!(
            rels(&collect_export_files(&src).unwrap()),
            vec!["figures/fig1.jpg", "raw_draft_paper.tex", "references.bib"]
        );

        let empty = TempDir::new("nolatex");
        empty.put("outline.json", "{}");
        assert_eq!(export_source_dir(&empty.0), None);
        let out = TempDir::new("nolatex_out");
        let err = export_run_latex(&empty.s(), &out.0.join("a.zip").to_string_lossy()).unwrap_err();
        assert!(err.contains("没有可导出的 LaTeX"), "{err}");
    }

    #[cfg(unix)]
    #[test]
    fn symlinks_are_not_followed() {
        let run = TempDir::new("link");
        let outside = TempDir::new("outside");
        outside.put("secret.tex", "secret");
        run.put(&format!("{WRITEUP_DIR}/raw_draft_paper.tex"), DOC);
        std::os::unix::fs::symlink(
            outside.0.join("secret.tex"),
            run.0.join(WRITEUP_DIR).join("leak.tex"),
        )
        .unwrap();
        std::os::unix::fs::symlink(&outside.0, run.0.join(WRITEUP_DIR).join("figures")).unwrap();
        let src = export_source_dir(&run.0).unwrap();
        assert_eq!(
            rels(&collect_export_files(&src).unwrap()),
            vec!["raw_draft_paper.tex"]
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_symlinked_source_folder_is_not_exported() {
        let run = TempDir::new("linkdir");
        let outside = TempDir::new("linkdir_outside");
        outside.put("final_refined_paper.tex", DOC);
        outside.put("private.bib", "@misc{x, title={private}}");
        std::os::unix::fs::symlink(&outside.0, run.0.join(REFINEMENT_DIR)).unwrap();
        assert_eq!(export_source_dir(&run.0), None);
        // a real latex_writeup beside it is used instead
        run.put(&format!("{WRITEUP_DIR}/raw_draft_paper.tex"), DOC);
        assert_eq!(export_source_dir(&run.0), Some(run.0.join(WRITEUP_DIR)));
    }

    #[test]
    fn writes_a_readable_zip_with_forward_slashes() {
        let run = TempDir::new("zip");
        refinement_run(&run);
        let out = TempDir::new("zip_out");
        let written = export_run_latex(&run.s(), &out.0.join("paper").to_string_lossy()).unwrap();
        assert!(written.ends_with("paper.zip"), "{written}");

        let mut archive = zip::ZipArchive::new(File::open(&written).unwrap()).unwrap();
        let mut names: Vec<String> = (0..archive.len())
            .map(|i| archive.by_index(i).unwrap().name().to_string())
            .collect();
        names.sort();
        assert!(names.contains(&"figures/arch.png".to_string()));
        assert!(names.contains(&"sections/intro.tex".to_string()));
        assert!(names
            .iter()
            .all(|n| !n.contains('\\') && !n.starts_with('/')));
        assert!(!names
            .iter()
            .any(|n| n.ends_with(".aux") || n.ends_with(".log") || n.contains("pdf_screenshots")));

        let mut body = String::new();
        archive
            .by_name("final_refined_paper.tex")
            .unwrap()
            .read_to_string(&mut body)
            .unwrap();
        assert_eq!(body, DOC);
        // no temp file left behind, nothing written into the run
        let leftovers: Vec<_> = fs::read_dir(&out.0)
            .unwrap()
            .flatten()
            .map(|e| e.file_name())
            .collect();
        assert_eq!(leftovers.len(), 1, "{leftovers:?}");
        assert!(!run.0.join("paper.zip").exists());
    }

    #[test]
    fn refuses_unsafe_destinations() {
        let run = TempDir::new("safety");
        run.put(&format!("{WRITEUP_DIR}/raw_draft_paper.tex"), DOC);
        let run_s = run.s();

        // inside the run dir, directly or in a sub-folder, or via ".."
        for dest in [
            run.0.join("export.zip"),
            run.0.join(WRITEUP_DIR).join("export.zip"),
            run.0.join(WRITEUP_DIR).join("..").join("x.zip"),
        ] {
            let err = export_run_latex(&run_s, &dest.to_string_lossy()).unwrap_err();
            assert!(err.contains("运行目录内部"), "{dest:?}: {err}");
            assert!(!dest.exists());
        }
        // the run dir must exist
        let err =
            export_run_latex(&run.0.join("missing").to_string_lossy(), "/tmp/x.zip").unwrap_err();
        assert!(err.contains("运行目录不存在"), "{err}");
        let err = export_run_latex("", "/tmp/x.zip").unwrap_err();
        assert!(err.contains("运行目录不存在"), "{err}");

        let out = TempDir::new("safety_out");
        // relative, empty, missing parent, a directory
        assert!(export_run_latex(&run_s, "relative.zip")
            .unwrap_err()
            .contains("完整路径"));
        assert!(export_run_latex(&run_s, "  ")
            .unwrap_err()
            .contains("请选择"));
        let missing_parent = out.0.join("nope").join("a.zip");
        assert!(export_run_latex(&run_s, &missing_parent.to_string_lossy())
            .unwrap_err()
            .contains("不存在"));
        fs::create_dir_all(out.0.join("folder.zip")).unwrap();
        assert!(
            export_run_latex(&run_s, &out.0.join("folder.zip").to_string_lossy())
                .unwrap_err()
                .contains("文件夹")
        );

        // a sibling folder whose name merely starts like the run dir is fine
        let sibling = PathBuf::from(format!("{run_s}_exports"));
        fs::create_dir_all(&sibling).unwrap();
        let ok = export_run_latex(&run_s, &sibling.join("a.ZIP").to_string_lossy());
        let _ = fs::remove_dir_all(&sibling);
        assert!(ok.unwrap().ends_with("a.ZIP"));
    }

    const LIBRARY: &str = "@string{prl = {Phys. Rev. Lett.}}\n\
@article{Cited1, title={A}, journal=prl}\n\
@inproceedings{cited2, title={B}}\n\
@article{OffTopic, title={Calabi-Yau}, note={mail x@y.org}}\n";
    const CITED_ONLY: &str = "% references_cited.bib: the entries of ...\n\n\
@string{prl = {Phys. Rev. Lett.}}\n\
@article{Cited1, title={A}, journal=prl}\n\
@inproceedings{cited2, title={B}}\n";

    fn audit_json(tex: &str, bib: &str, cited_bib: &str) -> String {
        serde_json::json!({
            "tex_file": tex, "bib_file": bib, "cited_bib_file": cited_bib,
            "summary": {"uncited": 1},
            "missing_in_bib": [],
            "entries": [
                {"key": "Cited1", "cited": true},
                {"key": "cited2", "cited": true},
                {"key": "OffTopic", "cited": false},
            ],
        })
        .to_string()
    }

    /// A refinement run after the citation audit: 3 library entries, 2 cited.
    fn audited_run(run: &TempDir) {
        refinement_run(run);
        run.put(&format!("{REFINEMENT_DIR}/references.bib"), LIBRARY);
        run.put(
            AUDIT_JSON,
            &audit_json(
                "content_refinement_workdir/final_refined_paper.tex",
                "content_refinement_workdir/references.bib",
                CITED_BIB,
            ),
        );
        // what pdflatex recorded when it compiled the paper (natbib, BibTeX)
        run.put(&format!("{REFINEMENT_DIR}/final_refined_paper.aux"), AUX);
        run.put(CITED_BIB, CITED_ONLY); // written last, as the audit does
    }

    const AUX: &str = "\\relax\n\\citation{Cited1}\n\\citation{cited2,Cited1}\n\\bibstyle{plainnat}\n\\bibdata{references}\n";

    fn swap(run: &TempDir) -> Option<LatexExportBib> {
        let src = export_source_dir(&run.0).unwrap();
        let entries = collect_export_files(&src).unwrap();
        cited_bib_swap(&run.0, &src, &entries).map(|(b, _)| b)
    }

    #[test]
    fn bib_keys_reads_entries_only() {
        let keys = bib_keys(LIBRARY);
        let want: BTreeSet<String> = ["cited1", "cited2", "offtopic"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(keys, want);
        let odd = "@COMMENT{x, y}\n@preamble{\"\\\\def\"}\n@Book ( Paren1 ,\n title={P})\n@misc{ spaced key2 , t={}}\n@\n@article{nocomma}\n";
        let want: BTreeSet<String> = ["paren1", "spaced key2"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(bib_keys(odd), want);
        assert!(bib_keys("").is_empty());
    }

    #[test]
    fn export_ships_only_the_cited_entries_under_the_same_name() {
        let run = TempDir::new("cited");
        audited_run(&run);
        assert_eq!(
            swap(&run),
            Some(LatexExportBib {
                file: "references.bib".into(),
                kept: 2,
                dropped: 1
            })
        );
        let out = TempDir::new("cited_out");
        let written = export_run_latex(&run.s(), &out.0.join("p.zip").to_string_lossy()).unwrap();
        let mut archive = zip::ZipArchive::new(File::open(&written).unwrap()).unwrap();
        let mut body = String::new();
        archive
            .by_name("references.bib")
            .unwrap()
            .read_to_string(&mut body)
            .unwrap();
        assert_eq!(body, CITED_ONLY);
        assert!(archive.by_name(CITED_BIB).is_err()); // not a second copy
                                                      // the run itself is untouched
        let lib = fs::read_to_string(run.0.join(REFINEMENT_DIR).join("references.bib")).unwrap();
        assert_eq!(lib, LIBRARY);
    }

    #[test]
    fn citations_from_included_files_count() {
        let run = TempDir::new("cited_include");
        audited_run(&run);
        run.put(
            &format!("{REFINEMENT_DIR}/final_refined_paper.aux"),
            "\\relax\n\\citation{Cited1}\n\\@input{sections/intro.aux}\n\\bibdata{references}\n",
        );
        run.put(
            &format!("{REFINEMENT_DIR}/sections/intro.aux"),
            "\\relax\n\\citation{CITED2}\n",
        );
        assert_eq!(swap(&run).map(|b| b.kept), Some(2));
        // ...and one the cited copy lacks stops the swap
        run.put(
            &format!("{REFINEMENT_DIR}/sections/intro.aux"),
            "\\citation{OffTopic}\n",
        );
        assert_eq!(swap(&run), None);
    }

    #[test]
    fn library_ships_as_is_unless_the_audit_matches_the_export() {
        let w = REFINEMENT_DIR;
        let tex = format!("{w}/final_refined_paper.tex");
        let bib = format!("{w}/references.bib");
        type Change = Box<dyn Fn(&TempDir)>;
        let cases: Vec<(&str, Change)> = vec![
            (
                "no audit",
                Box::new(|r: &TempDir| fs::remove_file(r.0.join(AUDIT_JSON)).unwrap()),
            ),
            (
                "older audit without a cited bib",
                Box::new({
                    let (t, b) = (tex.clone(), bib.clone());
                    move |r: &TempDir| {
                        r.put(AUDIT_JSON, &audit_json(&t, &b, ""));
                    }
                }),
            ),
            (
                "another bib was audited",
                Box::new({
                    let t = tex.clone();
                    move |r: &TempDir| {
                        r.put(
                            AUDIT_JSON,
                            &audit_json(&t, "latex_writeup/references.bib", CITED_BIB),
                        );
                    }
                }),
            ),
            (
                "another tex was audited",
                Box::new({
                    let b = bib.clone();
                    move |r: &TempDir| {
                        r.put(
                            AUDIT_JSON,
                            &audit_json(
                                "content_refinement_workdir/initial_draft.tex",
                                &b,
                                CITED_BIB,
                            ),
                        );
                    }
                }),
            ),
            (
                "cited bib missing",
                Box::new(|r: &TempDir| fs::remove_file(r.0.join(CITED_BIB)).unwrap()),
            ),
            (
                "cited bib lacks a cited entry",
                Box::new(|r: &TempDir| {
                    r.put(CITED_BIB, "@article{Cited1, title={A}}\n");
                }),
            ),
            (
                "cited bib has an entry the library lacks",
                Box::new(|r: &TempDir| {
                    r.put(
                        CITED_BIB,
                        &format!("{CITED_ONLY}@misc{{stranger, title={{S}}}}\n"),
                    );
                }),
            ),
            (
                "nothing left out",
                Box::new(|r: &TempDir| {
                    r.put(CITED_BIB, LIBRARY);
                }),
            ),
            (
                "two bib files",
                Box::new(|r: &TempDir| {
                    r.put(&format!("{REFINEMENT_DIR}/extra.bib"), "@misc{e, t={}}\n");
                }),
            ),
            (
                "the audit found a citation missing from the library",
                Box::new(|r: &TempDir| {
                    let text = fs::read_to_string(r.0.join(AUDIT_JSON)).unwrap();
                    r.put(
                        AUDIT_JSON,
                        &text.replace("\"missing_in_bib\":[]", "\"missing_in_bib\":[\"Ghost\"]"),
                    );
                }),
            ),
            (
                "LaTeX asked for a key the cited copy lacks (another case, a macro)",
                Box::new(|r: &TempDir| {
                    r.put(
                        &format!("{REFINEMENT_DIR}/final_refined_paper.aux"),
                        &format!("{AUX}\\citation{{offtopic}}\n"),
                    );
                }),
            ),
            (
                "no compiled copy of the paper",
                Box::new(|r: &TempDir| {
                    fs::remove_file(r.0.join(REFINEMENT_DIR).join("final_refined_paper.aux"))
                        .unwrap()
                }),
            ),
            (
                "only a different document was compiled",
                Box::new(|r: &TempDir| {
                    fs::remove_file(r.0.join(REFINEMENT_DIR).join("final_refined_paper.aux"))
                        .unwrap();
                    r.put(
                        &format!("{REFINEMENT_DIR}/content_refinement_v1.tex"),
                        "\\documentclass{article} other",
                    );
                    r.put(&format!("{REFINEMENT_DIR}/content_refinement_v1.aux"), AUX);
                }),
            ),
            (
                "not BibTeX (no \\bibdata)",
                Box::new(|r: &TempDir| {
                    r.put(
                        &format!("{REFINEMENT_DIR}/final_refined_paper.aux"),
                        "\\relax\n\\citation{Cited1}\n",
                    );
                }),
            ),
            (
                "\\nocite{*}",
                Box::new(|r: &TempDir| {
                    r.put(
                        &format!("{REFINEMENT_DIR}/final_refined_paper.aux"),
                        &format!("{AUX}\\citation{{*}}\n"),
                    );
                }),
            ),
            (
                "an included file's aux is missing",
                Box::new(|r: &TempDir| {
                    r.put(
                        &format!("{REFINEMENT_DIR}/final_refined_paper.aux"),
                        &format!("{AUX}\\@input{{sections/intro.aux}}\n"),
                    );
                }),
            ),
            (
                "tex edited after the audit",
                Box::new(|r: &TempDir| {
                    let later = SystemTime::now() + std::time::Duration::from_secs(120);
                    let f = File::options()
                        .write(true)
                        .open(r.0.join(REFINEMENT_DIR).join(FINAL_TEX))
                        .unwrap();
                    f.set_modified(later).unwrap();
                }),
            ),
        ];
        for (what, change) in cases {
            let run = TempDir::new("asis");
            audited_run(&run);
            change(&run);
            assert_eq!(swap(&run), None, "{what}");
            let out = TempDir::new("asis_out");
            let written =
                export_run_latex(&run.s(), &out.0.join("p.zip").to_string_lossy()).unwrap();
            let mut archive = zip::ZipArchive::new(File::open(&written).unwrap()).unwrap();
            let mut body = String::new();
            archive
                .by_name("references.bib")
                .unwrap()
                .read_to_string(&mut body)
                .unwrap();
            assert_eq!(body, LIBRARY, "{what}");
        }
    }

    #[test]
    fn destination_normalisation() {
        let run = TempDir::new("dest");
        let out = TempDir::new("dest_out");
        let d =
            resolve_destination(&run.0, &out.0.join("paper.overleaf").to_string_lossy()).unwrap();
        assert_eq!(d, out.0.join("paper.overleaf.zip"));
        let d =
            resolve_destination(&run.0, &format!("  {}  ", out.0.join("p.zip").display())).unwrap();
        assert_eq!(d, out.0.join("p.zip"));
    }

    /// Exports a real pipeline run folder (e.g. one from paper-orchestra's
    /// tests/e2e harness) so the zip can be compiled outside the app. Run with:
    /// `PO_EXPORT_RUN_DIR=<run dir> PO_EXPORT_DEST=<file.zip> cargo test real_run_export -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn real_run_export_smoke() {
        let run = std::env::var("PO_EXPORT_RUN_DIR").expect("PO_EXPORT_RUN_DIR");
        let dest = std::env::var("PO_EXPORT_DEST").expect("PO_EXPORT_DEST");
        let written = export_run_latex(&run, &dest).unwrap();
        let mut zip = zip::ZipArchive::new(fs::File::open(&written).unwrap()).unwrap();
        let names: Vec<String> = (0..zip.len())
            .map(|i| zip.by_index(i).unwrap().name().to_string())
            .collect();
        println!("{written}\n{}", names.join("\n"));
        let docs: Vec<String> = names
            .iter()
            .filter(|n| !n.contains('/') && n.ends_with(".tex"))
            .filter(|n| {
                let mut text = String::new();
                zip.by_name(n.as_str())
                    .unwrap()
                    .read_to_string(&mut text)
                    .unwrap();
                text.contains("\\documentclass")
            })
            .cloned()
            .collect();
        assert_eq!(docs.len(), 1, "exactly one complete document: {docs:?}");
        assert!(names.iter().any(|n| n.ends_with(".bib")), "{names:?}");
    }

    #[test]
    fn zip_built_in_another_folder_lands_at_the_destination_only() {
        // What macOS does (staging in the temp folder): the destination folder
        // gets the chosen file and nothing else, and no temp file is left.
        let src = TempDir::new("stage_src");
        let staging = TempDir::new("stage_tmp");
        let out = TempDir::new("stage_out");
        let tex = src.put("main.tex", DOC);
        let entries = vec![ExportEntry {
            rel: "main.tex".to_string(),
            abs: tex,
        }];
        let dest = out.0.join("paper.zip");
        write_zip_via(&dest, &entries, &staging.0).unwrap();
        let names: Vec<String> = fs::read_dir(&out.0)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
            .collect();
        assert_eq!(names, vec!["paper.zip".to_string()]);
        assert_eq!(fs::read_dir(&staging.0).unwrap().count(), 0);
        let mut zip = zip::ZipArchive::new(File::open(&dest).unwrap()).unwrap();
        let mut body = String::new();
        zip.by_name("main.tex")
            .unwrap()
            .read_to_string(&mut body)
            .unwrap();
        assert_eq!(body, DOC);

        // A failed write leaves neither a temp file nor a partial zip.
        let missing = vec![ExportEntry {
            rel: "gone.tex".to_string(),
            abs: src.0.join("gone.tex"),
        }];
        let dest2 = out.0.join("broken.zip");
        assert!(write_zip_via(&dest2, &missing, &staging.0).is_err());
        assert!(!dest2.exists());
        assert_eq!(fs::read_dir(&staging.0).unwrap().count(), 0);

        // Exporting again over the same file replaces it.
        let tex2 = src.put("main.tex", "v2");
        let entries2 = vec![ExportEntry {
            rel: "main.tex".to_string(),
            abs: tex2,
        }];
        write_zip_via(&dest, &entries2, &staging.0).unwrap();
        let mut zip = zip::ZipArchive::new(File::open(&dest).unwrap()).unwrap();
        let mut body = String::new();
        zip.by_name("main.tex")
            .unwrap()
            .read_to_string(&mut body)
            .unwrap();
        assert_eq!(body, "v2");
        assert_eq!(fs::read_dir(&staging.0).unwrap().count(), 0);
    }

    #[test]
    fn a_failed_copy_keeps_an_untouched_old_file_and_drops_a_new_partial_one() {
        let out = TempDir::new("copy_place");
        let old = out.put("paper.zip", "old export");
        // The copy fails before it touches the destination: the old file stays.
        assert!(copy_into_place(&out.0.join("no-such.part"), &old).is_err());
        assert_eq!(fs::read_to_string(&old).unwrap(), "old export");
        // Nothing was there before: nothing is left behind.
        let fresh = out.0.join("fresh.zip");
        assert!(copy_into_place(&out.0.join("no-such.part"), &fresh).is_err());
        assert!(!fresh.exists());
        // A good copy lands.
        let part = out.put("x.part", "new export");
        copy_into_place(&part, &fresh).unwrap();
        assert_eq!(fs::read_to_string(&fresh).unwrap(), "new export");
    }
}
