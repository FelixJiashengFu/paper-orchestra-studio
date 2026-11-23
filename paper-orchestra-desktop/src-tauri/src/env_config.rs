use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;

use uuid::Uuid;

use crate::models::{AppSettings, EnvSecretsConfig, EnvironmentStatus};
use crate::providers;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Folder name the source bundle is shipped in ("PaperOrchestra_源码/paper-orchestra").
const SOURCE_BUNDLE_DIR: &str = "PaperOrchestra_源码";

/// Where to look for the PaperOrchestra repo, in order, for `os`
/// (`std::env::consts::OS`). `home` is $HOME, `user_profile` %USERPROFILE%
/// (Windows often has no HOME, so it stands in for it there).
pub fn paper_orchestra_dir_candidates(
    os: &str,
    home: Option<PathBuf>,
    user_profile: Option<PathBuf>,
) -> Vec<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if os == "windows" {
        if let Some(profile) = &user_profile {
            candidates.push(
                profile
                    .join("Desktop")
                    .join(SOURCE_BUNDLE_DIR)
                    .join("paper-orchestra"),
            );
        }
    }
    candidates.push(PathBuf::from("../paper-orchestra"));
    candidates.push(PathBuf::from("paper-orchestra"));
    let home = home.or_else(|| if os == "windows" { user_profile } else { None });
    if let Some(home_path) = home {
        candidates.push(home_path.join("paper-orchestra"));
        candidates.push(home_path.join("Documents").join("paper-orchestra"));
        candidates.push(home_path.join("Desktop").join("paper-orchestra"));
        if os == "macos" {
            candidates.push(
                home_path
                    .join("Desktop")
                    .join(SOURCE_BUNDLE_DIR)
                    .join("paper-orchestra"),
            );
            candidates.push(
                home_path
                    .join("Documents")
                    .join(SOURCE_BUNDLE_DIR)
                    .join("paper-orchestra"),
            );
        }
    }
    candidates
}

fn env_path(name: &str) -> Option<PathBuf> {
    std::env::var_os(name)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

pub fn default_paper_orchestra_dir() -> String {
    let candidates = paper_orchestra_dir_candidates(
        std::env::consts::OS,
        env_path("HOME"),
        env_path("USERPROFILE"),
    );
    for p in &candidates {
        if p.join("paper_writing_cli.py").exists() {
            return p
                .canonicalize()
                .map(|c| strip_windows_unc(&c))
                .unwrap_or_else(|_| p.to_string_lossy().to_string());
        }
    }
    #[cfg(target_os = "windows")]
    {
        env_path("USERPROFILE")
            .map(|p| p.join("paper-orchestra").to_string_lossy().to_string())
            .unwrap_or_else(|| "../paper-orchestra".to_string())
    }
    #[cfg(not(target_os = "windows"))]
    {
        if let Ok(home) = std::env::var("HOME") {
            PathBuf::from(home)
                .join("paper-orchestra")
                .to_string_lossy()
                .to_string()
        } else {
            "../paper-orchestra".to_string()
        }
    }
}

pub fn default_python_path(paper_orchestra_dir: &str) -> String {
    let venv_win = Path::new(paper_orchestra_dir)
        .join(".venv")
        .join("Scripts")
        .join("python.exe");
    if venv_win.exists() {
        return venv_win.to_string_lossy().to_string();
    }
    let venv_unix = Path::new(paper_orchestra_dir)
        .join(".venv")
        .join("bin")
        .join("python");
    if venv_unix.exists() {
        return venv_unix.to_string_lossy().to_string();
    }
    let venv_unix3 = Path::new(paper_orchestra_dir)
        .join(".venv")
        .join("bin")
        .join("python3");
    if venv_unix3.exists() {
        return venv_unix3.to_string_lossy().to_string();
    }
    #[cfg(target_os = "windows")]
    {
        "python".to_string()
    }
    #[cfg(not(target_os = "windows"))]
    {
        "python3".to_string()
    }
}

/// A path typed as "~" or "~/..." in Settings, with "~" replaced by `home`.
/// The app runs no shell, so without this "~" would be a literal folder name.
pub fn expand_home(value: &str, home: Option<&Path>) -> String {
    let Some(home) = home else {
        return value.to_string();
    };
    if value == "~" {
        return home.to_string_lossy().to_string();
    }
    match value.strip_prefix("~/") {
        Some(rest) => home.join(rest).to_string_lossy().to_string(),
        None => value.to_string(),
    }
}

/// What the Python "选择" button should fill in. On macOS the file dialog
/// resolves symlinks, so choosing `<repo>/.venv/bin/python` returns the base
/// interpreter it links to, which lacks the venv's packages. When `picked` is
/// such a fully resolved file and the repo's venv python links to it, the venv
/// python is returned (the app says so in a toast). The dialog cannot tell
/// that apart from picking the base interpreter itself; typing its path keeps
/// it, because only the button calls this. A path that is itself a link
/// (another venv's python, /opt/homebrew/bin/python3, ...), as a Linux dialog
/// may return, is kept, as is anything else.
pub fn prefer_venv_python(picked: &str, repo: &str) -> String {
    if picked.trim().is_empty() || repo.trim().is_empty() {
        return picked.to_string();
    }
    let bin = Path::new(repo).join(".venv").join("bin");
    let candidates = [bin.join("python"), bin.join("python3")];
    if candidates.iter().any(|c| Path::new(picked) == c.as_path()) {
        return picked.to_string();
    }
    let Ok(target) = fs::canonicalize(picked) else {
        return picked.to_string();
    };
    if target != Path::new(picked) {
        return picked.to_string();
    }
    for candidate in &candidates {
        if fs::canonicalize(candidate)
            .map(|c| c == target)
            .unwrap_or(false)
        {
            return candidate.to_string_lossy().to_string();
        }
    }
    picked.to_string()
}

pub fn enriched_path_env() -> String {
    let current = std::env::var("PATH").unwrap_or_default();
    #[cfg(target_os = "macos")]
    {
        let home = env_path("HOME");
        merge_path_dirs(&current, &macos_extra_path_dirs(home.as_deref()), |d| {
            d.is_dir()
        })
    }
    #[cfg(not(target_os = "macos"))]
    {
        current
    }
}

/// Where a Mac keeps LaTeX and command-line tools that an app opened from
/// Finder or the Dock does not see: it only gets /usr/bin:/bin:/usr/sbin:/sbin,
/// never what ~/.zshrc adds. In order: MacTeX/BasicTeX, TinyTeX (installed
/// without sudo under ~/Library/TinyTeX/bin/<arch>, linked into ~/bin or
/// nowhere), plain TeX Live (newest year first), Homebrew, MacPorts.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn macos_extra_path_dirs(home: Option<&Path>) -> Vec<PathBuf> {
    let mut dirs = vec![PathBuf::from("/Library/TeX/texbin")];
    if let Some(home) = home {
        dirs.extend(sorted_subdirs(
            &home.join("Library").join("TinyTeX").join("bin"),
        ));
        dirs.push(home.join("bin"));
    }
    let mut years = sorted_subdirs(Path::new("/usr/local/texlive"));
    years.reverse();
    for year in years {
        for arch in [
            "universal-darwin",
            "aarch64-darwin",
            "x86_64-darwin",
            "x86_64-darwinlegacy",
        ] {
            dirs.push(year.join("bin").join(arch));
        }
    }
    for dir in ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"] {
        dirs.push(PathBuf::from(dir));
    }
    dirs
}

#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn sorted_subdirs(dir: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = fs::read_dir(dir)
        .map(|rd| {
            rd.flatten()
                .map(|e| e.path())
                .filter(|p| p.is_dir())
                .collect()
        })
        .unwrap_or_default();
    out.sort();
    out
}

/// `current` (a PATH value) followed by each of `extra` that `exists` and is
/// not already one of its entries. The inherited entries keep their order and
/// precedence.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn merge_path_dirs(current: &str, extra: &[PathBuf], exists: impl Fn(&Path) -> bool) -> String {
    let mut parts: Vec<String> = current
        .split(':')
        .filter(|p| !p.is_empty())
        .map(str::to_string)
        .collect();
    for dir in extra {
        let s = dir.to_string_lossy().to_string();
        if !parts
            .iter()
            .any(|p| p.trim_end_matches('/') == s.trim_end_matches('/'))
            && exists(dir)
        {
            parts.push(s);
        }
    }
    parts.join(":")
}

/// When `path` is missing because the disk it lives on is not mounted, a
/// Chinese message naming that disk; `None` when the path exists or is missing
/// for another reason. Follows symlinks by hand (a dangling link such as
/// ~/paper-orchestra -> /Volumes/ExternalDisk/... has no canonical form).
pub fn offline_volume_hint(path: &Path) -> Option<String> {
    if path.exists() {
        return None;
    }
    let mut cur = path.to_path_buf();
    for _ in 0..16 {
        // Walk up to the first ancestor that is a symlink or under /Volumes.
        let mut probe = cur.clone();
        let mut rest: Vec<std::ffi::OsString> = Vec::new();
        let mut resolved = None;
        loop {
            if let Ok(target) = fs::read_link(&probe) {
                let base = probe.parent().map(Path::to_path_buf).unwrap_or_default();
                let mut next = base.join(target);
                for part in rest.iter().rev() {
                    next.push(part);
                }
                resolved = Some(next);
                break;
            }
            match (probe.file_name(), probe.parent()) {
                (Some(name), Some(parent)) => {
                    rest.push(name.to_os_string());
                    probe = parent.to_path_buf();
                }
                _ => break,
            }
        }
        match resolved {
            Some(next) => cur = next,
            None => break,
        }
    }
    let mut parts = cur.components();
    parts.next(); // root
    let first = parts.next()?.as_os_str().to_string_lossy().to_string();
    if first != "Volumes" {
        return None;
    }
    let volume = parts.next()?.as_os_str().to_string_lossy().to_string();
    if Path::new("/Volumes").join(&volume).exists() {
        return None;
    }
    Some(format!("PaperOrchestra 所在的外接盘「{volume}」没有连接。请插上外接盘后再重试。"))
}

pub fn default_output_root_dir(paper_orchestra_dir: &str) -> String {
    Path::new(paper_orchestra_dir)
        .join("desktop_runs")
        .to_string_lossy()
        .to_string()
}

pub fn strip_windows_unc(path: &Path) -> String {
    let s = path.to_string_lossy().to_string();
    // A network share comes back as \\?\UNC\server\share\...; its plain
    // form is \\server\share\..., not the relative UNC\server\....
    if let Some(share) = s.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{share}")
    } else if let Some(stripped) = s.strip_prefix(r"\\?\") {
        stripped.to_string()
    } else {
        s
    }
}

/// Generates the managed-key table plus typed accessors from one list, so the
/// .env key, the struct field and the append order can never drift apart.
macro_rules! managed_env_keys {
    ($($key:literal => $field:ident),* $(,)?) => {
        /// .env keys with a dedicated field in `EnvSecretsConfig`, in the order
        /// missing ones are appended.
        pub const MANAGED_KEYS: &[&str] = &[$($key),*];

        fn managed_get<'a>(cfg: &'a EnvSecretsConfig, key: &str) -> Option<&'a str> {
            match key {
                $($key => Some(cfg.$field.as_str()),)*
                _ => None,
            }
        }

        fn managed_set(cfg: &mut EnvSecretsConfig, key: &str, val: String) -> bool {
            match key {
                $($key => { cfg.$field = val; true })*
                _ => false,
            }
        }
    };
}

managed_env_keys! {
    "ANTHROPIC_API_KEY" => anthropic_api_key,
    "ANTHROPIC_BASE_URL" => anthropic_base_url,
    "DEEPSEEK_API_KEY" => deepseek_api_key,
    "DEEPSEEK_BASE_URL" => deepseek_base_url,
    "SEMANTIC_SCHOLAR_API_KEY" => semantic_scholar_api_key,
    "OPENAI_API_KEY" => openai_api_key,
    "OPENAI_BASE_URL" => openai_base_url,
    "VERTEX_AI_PROJECT" => vertex_ai_project,
    "VERTEX_AI_LOCATION" => vertex_ai_location,
    "GEMINI_API_KEY" => gemini_api_key,
    "SMTP_EMAIL" => smtp_email,
    "SMTP_PASSWORD" => smtp_password,
    "S2_MIN_INTERVAL_SEC" => s2_min_interval_sec,
    "OPENALEX_EMAIL" => openalex_email,
    "OPENALEX_API_KEY" => openalex_api_key,
    "CROSSREF_MAILTO" => crossref_mailto,
    "CROSSREF_PLUS_TOKEN" => crossref_plus_token,
    "NCBI_API_KEY" => ncbi_api_key,
    "NCBI_EMAIL" => ncbi_email,
    "CORE_API_KEY" => core_api_key,
    "PO_SCHOLAR_SOURCES" => scholar_sources,
    "PO_AUTO_MODEL" => auto_model,
    "PO_DEFAULT_PROVIDER" => default_provider,
}

/// Default scholarly source order (`scholar_utils._DEFAULT_ORDER`).
const SCHOLAR_SOURCES: &[&str] = &[
    "semantic_scholar",
    "openalex",
    "crossref",
    "arxiv",
    "dblp",
    "europepmc",
    "pubmed",
    "core",
];

/// One `KEY=VALUE` line of a .env file.
struct EnvAssignment<'a> {
    export: bool,
    key: &'a str,
    raw_value: &'a str,
}

/// Parses an assignment line; comments, blank lines, lines without "=" and
/// lines whose key is not a valid variable name yield `None` (and are kept
/// verbatim on save).
fn parse_assignment(line: &str) -> Option<EnvAssignment<'_>> {
    let t = line.trim_start();
    if t.is_empty() || t.starts_with('#') {
        return None;
    }
    let (export, rest) = match t.strip_prefix("export") {
        Some(r) if r.starts_with([' ', '\t']) => (true, r.trim_start()),
        _ => (false, t),
    };
    let (k, v) = rest.split_once('=')?;
    let key = k.trim();
    if !is_valid_env_key(key) {
        return None;
    }
    Some(EnvAssignment {
        export,
        key,
        raw_value: v.trim(),
    })
}

/// One logical entry of a .env file.
enum EnvLine<'a> {
    Single(&'a str),
    /// A quoted value that python-dotenv reads across several lines (e.g. a
    /// PEM key). It is kept verbatim and not offered for editing: a one-line
    /// input would cut it apart.
    MultiLine(&'a [&'a str]),
}

/// Groups physical lines into entries, joining quoted multi-line values.
fn env_lines<'a>(lines: &'a [&'a str]) -> Vec<EnvLine<'a>> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        match multiline_end(lines, i) {
            Some(end) => {
                out.push(EnvLine::MultiLine(&lines[i..=end]));
                i = end + 1;
            }
            None => {
                out.push(EnvLine::Single(lines[i]));
                i += 1;
            }
        }
    }
    out
}

/// Last line of a quoted value that opens on line `i` and closes on a later
/// line; `None` for ordinary lines and for quotes that never close.
fn multiline_end(lines: &[&str], i: usize) -> Option<usize> {
    let raw = parse_assignment(lines[i])?.raw_value;
    let (quote, escapable): (char, &[char]) = match raw.chars().next()? {
        '\'' => ('\'', &['\'', '\\']),
        '"' => ('"', &['"', '\'', '\\']),
        _ => return None,
    };
    if parse_quoted(&raw[1..], quote, escapable).is_some() {
        return None;
    }
    let mut text = raw[1..].to_string();
    for (j, line) in lines.iter().enumerate().skip(i + 1).take(500) {
        text.push('\n');
        text.push_str(line);
        if parse_quoted(&text, quote, escapable).is_some() {
            return Some(j);
        }
    }
    None
}

/// The inline comment after a value, with the whitespace before it
/// (`"  # main key"`), or "".
fn inline_comment(raw: &str) -> &str {
    let raw = raw.trim();
    if let Some(q @ ('\'' | '"')) = raw.chars().next() {
        let Some(end) = quoted_end(&raw[1..], q) else { return "" };
        let rest = &raw[1 + end..];
        return if rest.trim_start().starts_with('#') { rest } else { "" };
    }
    let mut prev_ws = false;
    for (i, c) in raw.char_indices() {
        if c == '#' && prev_ws {
            return &raw[raw[..i].trim_end().len()..];
        }
        prev_ws = c.is_whitespace();
    }
    ""
}

/// Byte offset just past the closing `quote` in `rest` (the text after the
/// opening quote), honouring backslash escapes.
fn quoted_end(rest: &str, quote: char) -> Option<usize> {
    let mut chars = rest.char_indices();
    while let Some((i, c)) = chars.next() {
        if c == quote {
            return Some(i + c.len_utf8());
        }
        if c == '\\' {
            chars.next();
        }
    }
    None
}

/// `^[A-Za-z_][A-Za-z0-9_]*$`
pub fn is_valid_env_key(key: &str) -> bool {
    let mut chars = key.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn strip_bom(s: &str) -> &str {
    s.strip_prefix('\u{feff}').unwrap_or(s)
}

/// Reads `<repo>/.env` (BOM stripped); `None` when the file does not exist.
fn read_env_text(repo: &Path) -> Result<Option<String>, String> {
    let env_path = repo.join(".env");
    if !env_path.exists() {
        return Ok(None);
    }
    let bytes = fs::read(&env_path)
        .map_err(|e| format!("读取 .env 失败（{}）：{e}", env_path.display()))?;
    Ok(Some(strip_bom(&String::from_utf8_lossy(&bytes)).to_string()))
}

/// Every valid single-line assignment in the file; the last occurrence of a
/// key wins, as with python-dotenv. Multi-line values are left out (they are
/// preserved verbatim on save).
fn parse_env_map(content: &str) -> BTreeMap<String, String> {
    let lines: Vec<&str> = content.lines().collect();
    env_lines(&lines)
        .into_iter()
        .filter_map(|entry| match entry {
            EnvLine::Single(line) => parse_assignment(line),
            EnvLine::MultiLine(_) => None,
        })
        .map(|a| (a.key.to_string(), unquote_env_value(a.raw_value)))
        .collect()
}

/// Identifies exactly what [`read_env_config`] read: the repo dir, `.env`
/// and providers.json (path and bytes).
fn source_fingerprint(repo: &Path) -> String {
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    fs::canonicalize(repo).unwrap_or_else(|_| repo.to_path_buf()).hash(&mut h);
    let env_bytes = fs::read(repo.join(".env")).ok();
    env_bytes.hash(&mut h);
    let env = env_bytes
        .map(|b| parse_env_map(strip_bom(&String::from_utf8_lossy(&b))))
        .unwrap_or_default();
    let providers = providers_file_path(repo, env.get("PO_PROVIDERS_FILE").map(String::as_str));
    providers.hash(&mut h);
    fs::read(&providers).ok().hash(&mut h);
    format!("{:016x}", h.finish())
}

/// `providers_utils.providers_file_path()`: PO_PROVIDERS_FILE (absolute, or
/// relative to the repo) or `<repo>/providers.json`.
fn providers_file_path(repo: &Path, po_providers_file: Option<&str>) -> PathBuf {
    match po_providers_file.map(str::trim).filter(|s| !s.is_empty()) {
        Some(custom) if Path::new(custom).is_absolute() => PathBuf::from(custom),
        Some(custom) => repo.join(custom),
        None => repo.join("providers.json"),
    }
}

/// Text of the providers file, "" when it does not exist.
fn read_providers_text(path: &Path) -> Result<String, String> {
    if !path.is_file() {
        return Ok(String::new());
    }
    let bytes = fs::read(path)
        .map_err(|e| format!("读取 providers.json 失败（{}）：{e}", path.display()))?;
    Ok(strip_bom(&String::from_utf8_lossy(&bytes)).to_string())
}

fn config_from_env_map(env: &BTreeMap<String, String>) -> EnvSecretsConfig {
    let mut cfg = EnvSecretsConfig::default();
    for (key, val) in env {
        if !managed_set(&mut cfg, key, val.clone()) {
            cfg.extra_env.insert(key.clone(), val.clone());
        }
    }
    cfg
}

pub fn read_env_config(paper_orchestra_dir: &str) -> Result<EnvSecretsConfig, String> {
    let repo = Path::new(paper_orchestra_dir);
    let env = read_env_text(repo)?
        .map(|text| parse_env_map(&text))
        .unwrap_or_default();
    let mut cfg = config_from_env_map(&env);
    let providers_path = providers_file_path(repo, env.get("PO_PROVIDERS_FILE").map(String::as_str));
    cfg.providers_json = read_providers_text(&providers_path)?;
    cfg.source_fingerprint = source_fingerprint(repo);
    Ok(cfg)
}

/// Values that must never be shown in the UI: every non-trivial value of a
/// secret-looking key in .env, and secret-looking string fields of
/// providers.json. Used to mask text that comes back from Python.
pub fn secret_values(paper_orchestra_dir: &str) -> Vec<String> {
    fn secretish(key: &str) -> bool {
        let k = key.to_ascii_uppercase();
        // `api_key_env` / `*_ENV` values name a variable; they are not secrets.
        if k.ends_with("_ENV") {
            return false;
        }
        ["KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PASS"]
            .iter()
            .any(|s| k.ends_with(s))
            || k.contains("API_KEY")
            || k.contains("APIKEY")
    }
    fn collect_json(v: &serde_json::Value, out: &mut Vec<String>) {
        match v {
            serde_json::Value::Object(map) => {
                for (k, val) in map {
                    match val {
                        serde_json::Value::String(s) if secretish(k) => out.push(s.clone()),
                        _ => collect_json(val, out),
                    }
                }
            }
            serde_json::Value::Array(items) => items.iter().for_each(|i| collect_json(i, out)),
            _ => {}
        }
    }

    let repo = Path::new(paper_orchestra_dir);
    let env = read_env_text(repo)
        .ok()
        .flatten()
        .map(|text| parse_env_map(&text))
        .unwrap_or_default();
    let mut out: Vec<String> = env
        .iter()
        .filter(|(k, _)| secretish(k))
        .map(|(_, v)| v.trim().to_string())
        .collect();
    let providers = providers_file_path(repo, env.get("PO_PROVIDERS_FILE").map(String::as_str));
    if let Ok(text) = read_providers_text(&providers) {
        if let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) {
            collect_json(&json, &mut out);
        }
    }
    out.retain(|v| v.chars().count() >= 8);
    out.sort();
    out.dedup();
    out
}

/// Checks that providers.json text has the shape utils/providers.py accepts.
pub fn validate_providers_json(text: &str) -> Result<(), String> {
    providers::parse_providers_json(text).map(|_| ())
}

pub fn write_env_config(paper_orchestra_dir: &str, cfg: &EnvSecretsConfig) -> Result<(), String> {
    let dir = Path::new(paper_orchestra_dir);
    if !dir.is_dir() {
        return Err(format!("PaperOrchestra 目录不存在：{}", dir.display()));
    }

    // ---- validate everything before touching any file ----
    let mut extra: BTreeMap<String, String> = BTreeMap::new();
    for (raw_key, raw_val) in &cfg.extra_env {
        let key = raw_key.trim();
        if key.is_empty() {
            return Err("环境变量名不能为空".to_string());
        }
        if !is_valid_env_key(key) {
            return Err(format!(
                "环境变量名“{key}”无效：只能包含英文字母、数字和下划线，且不能以数字开头"
            ));
        }
        if MANAGED_KEYS.contains(&key) {
            return Err(format!("{key} 已有专门的设置项，请在对应输入框中填写，不要放在自定义变量里"));
        }
        extra.insert(key.to_string(), sanitize_env_value(raw_val));
    }
    let providers_text = cfg.providers_json.trim();
    if !providers_text.is_empty() {
        validate_providers_json(providers_text)?;
    }
    // Refuse to overwrite files that changed since the config was read (an
    // external edit, or a different repo dir): the UI would otherwise drop
    // keys it never saw.
    if !cfg.source_fingerprint.is_empty() && cfg.source_fingerprint != source_fingerprint(dir) {
        return Err(
            ".env 或 providers.json 在读取后被修改过（或仓库目录已切换），为避免覆盖，本次未写入。请重新保存。"
                .to_string(),
        );
    }

    // ---- .env ----
    let env_path = dir.join(".env");
    let existing = read_env_text(dir)?.unwrap_or_default();
    let newline = if existing.contains("\r\n") { "\r\n" } else { "\n" };

    let mut seen_managed: Vec<&str> = Vec::new();
    let mut seen_extra: Vec<&str> = Vec::new();
    let mut output_lines: Vec<String> = Vec::new();

    let existing_lines: Vec<&str> = existing.lines().collect();
    for entry in env_lines(&existing_lines) {
        let raw_line = match entry {
            EnvLine::Single(line) => line,
            EnvLine::MultiLine(block) => {
                output_lines.extend(block.iter().map(|l| l.to_string()));
                continue;
            }
        };
        let Some(a) = parse_assignment(raw_line) else {
            output_lines.push(raw_line.to_string());
            continue;
        };
        let new_val = if let Some(v) = managed_get(cfg, a.key) {
            if !seen_managed.contains(&a.key) {
                seen_managed.push(a.key);
            }
            sanitize_env_value(v)
        } else {
            match extra.get_key_value(a.key) {
                // Present in extra_env (an empty value keeps the line as `KEY=`).
                Some((k, v)) => {
                    if !seen_extra.contains(&k.as_str()) {
                        seen_extra.push(k.as_str());
                    }
                    v.clone()
                }
                // Unmanaged key removed from extra_env: drop the line.
                None => continue,
            }
        };
        if unquote_env_value(a.raw_value) == new_val {
            // Unchanged: keep the user's quoting / inline comment as written.
            output_lines.push(raw_line.to_string());
        } else {
            output_lines.push(rewrite_env_line(a.export, a.key, &new_val, inline_comment(a.raw_value)));
        }
    }

    if output_lines.is_empty() {
        output_lines.push("# Managed by PaperOrchestra Studio".to_string());
    }
    // Managed keys the file does not have yet: only those with a value.
    for key in MANAGED_KEYS {
        if !seen_managed.contains(key) {
            let val = sanitize_env_value(managed_get(cfg, key).unwrap_or_default());
            if !val.is_empty() {
                output_lines.push(format_env_line(false, key, &val));
            }
        }
    }
    for (key, val) in &extra {
        if !val.is_empty() && !seen_extra.contains(&key.as_str()) {
            output_lines.push(format_env_line(false, key, val));
        }
    }

    let content = format!("{}{newline}", output_lines.join(newline));

    // providers.json goes where Python will look after this save.
    let providers_path = if providers_text.is_empty() {
        None
    } else {
        Some(providers_file_path(dir, extra.get("PO_PROVIDERS_FILE").map(String::as_str)))
    };

    atomic_write(&env_path, &content).map_err(|e| format!("写入 .env 失败：{e}"))?;
    if let Some(path) = providers_path {
        let unchanged = read_providers_text(&path).is_ok_and(|old| old == cfg.providers_json);
        if !unchanged {
            atomic_write(&path, &cfg.providers_json)
                .map_err(|e| format!("写入 providers.json 失败：{e}"))?;
        }
    }
    Ok(())
}

/// Writes through a temp file in the same directory and renames it over the
/// target, so a crash never leaves a half-written file. Keeps the target's
/// permissions and follows a symlinked target.
fn atomic_write(path: &Path, content: &str) -> Result<(), String> {
    let target = match fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_symlink() => {
            fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
        }
        _ => path.to_path_buf(),
    };
    let dir = target
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    if !dir.is_dir() {
        return Err(format!("目录不存在：{}", dir.display()));
    }
    let file_name = target
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| "file".to_string());
    let tmp = dir.join(format!(".{file_name}.{}.tmp", Uuid::new_v4().simple()));

    let written = (|| -> std::io::Result<()> {
        let mut opts = fs::OpenOptions::new();
        opts.write(true).create_new(true);
        // A new file may hold API keys: owner-only unless the target already
        // has its own permissions (copied below).
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let mut f = opts.open(&tmp)?;
        f.write_all(content.as_bytes())?;
        f.sync_all()?;
        drop(f);
        if let Ok(meta) = fs::metadata(&target) {
            fs::set_permissions(&tmp, meta.permissions())?;
        }
        Ok(())
    })();
    if let Err(e) = written {
        let _ = fs::remove_file(&tmp);
        return Err(format!("{}：{e}", target.display()));
    }

    if fs::rename(&tmp, &target).is_ok() {
        return Ok(());
    }
    // Windows can refuse to rename over an existing file (e.g. while another
    // process has it open): remove the old file, then move the new one in.
    let fallback = (|| -> std::io::Result<()> {
        if target.exists() {
            fs::remove_file(&target)?;
        }
        fs::rename(&tmp, &target).or_else(|_| fs::copy(&tmp, &target).map(|_| ()))
    })();
    match fallback {
        Ok(()) => {
            let _ = fs::remove_file(&tmp);
            Ok(())
        }
        Err(e) if target.exists() => {
            let _ = fs::remove_file(&tmp);
            Err(format!("{}：{e}", target.display()))
        }
        Err(e) => Err(format!(
            "{}：{e}（新内容保存在 {}）",
            target.display(),
            tmp.display()
        )),
    }
}

/// Value of a .env assignment as python-dotenv reads it: quotes removed
/// (with `\\` and the quote escapes decoded; other escapes are kept as
/// written), and an unquoted value cut at an inline ` #` comment.
fn unquote_env_value(raw: &str) -> String {
    let raw = raw.trim();
    let quoted = match raw.chars().next() {
        Some('\'') => parse_quoted(&raw[1..], '\'', &['\'', '\\']),
        Some('"') => parse_quoted(&raw[1..], '"', &['"', '\'', '\\']),
        _ => None,
    };
    if let Some(value) = quoted {
        return value;
    }
    if raw.starts_with(['\'', '"']) {
        return raw.to_string(); // unterminated quote: take it literally
    }
    let mut prev_ws = false;
    for (i, c) in raw.char_indices() {
        if c == '#' && prev_ws {
            return raw[..i].trim_end().to_string();
        }
        prev_ws = c.is_whitespace();
    }
    raw.to_string()
}

/// Content up to the closing `quote`, or `None` if there is none.
fn parse_quoted(rest: &str, quote: char, escapable: &[char]) -> Option<String> {
    let mut out = String::new();
    let mut chars = rest.chars();
    while let Some(c) = chars.next() {
        if c == quote {
            return Some(out);
        }
        if c != '\\' {
            out.push(c);
            continue;
        }
        match chars.next() {
            Some(n) if escapable.contains(&n) => out.push(n),
            Some(n) => {
                out.push('\\');
                out.push(n);
            }
            None => out.push('\\'),
        }
    }
    None
}

/// Quotes a value only when python-dotenv would otherwise misread it
/// (whitespace, which an inline " #" comment needs, or a leading quote).
fn quote_env_value(val: &str) -> String {
    let needs_quotes = val.chars().any(char::is_whitespace) || val.starts_with(['"', '\'']);
    if needs_quotes {
        format!("\"{}\"", val.replace('\\', "\\\\").replace('"', "\\\""))
    } else {
        val.to_string()
    }
}

fn format_env_line(export: bool, key: &str, val: &str) -> String {
    let prefix = if export { "export " } else { "" };
    format!("{prefix}{key}={}", quote_env_value(val))
}

/// A changed assignment, keeping the line's inline comment. An empty value
/// is written as `""` then, because python-dotenv would read `KEY=  # c` as
/// the value "# c".
fn rewrite_env_line(export: bool, key: &str, val: &str, comment: &str) -> String {
    if comment.is_empty() {
        return format_env_line(export, key, val);
    }
    let prefix = if export { "export " } else { "" };
    let value = if val.is_empty() { "\"\"".to_string() } else { quote_env_value(val) };
    let sep = if comment.starts_with(char::is_whitespace) { "" } else { " " };
    format!("{prefix}{key}={value}{sep}{comment}")
}

fn sanitize_env_value(raw: &str) -> String {
    raw.replace(['\r', '\n'], "").trim().to_string()
}

/// `scholar_utils.enabled_sources()` evaluated against the .env values.
fn resolve_scholar_sources(env: &BTreeMap<String, String>) -> Vec<String> {
    let get = |k: &str| env.get(k).map(|v| v.trim()).unwrap_or("");
    let raw = get("PO_SCHOLAR_SOURCES");
    let names: Vec<String> = if raw.is_empty() {
        SCHOLAR_SOURCES.iter().map(|s| s.to_string()).collect()
    } else {
        raw.split(',')
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty())
            .collect()
    };
    let has_core_key = !get("CORE_API_KEY").is_empty();
    let mut out: Vec<String> = Vec::new();
    for name in names {
        let name = match name.as_str() {
            "s2" | "semanticscholar" => "semantic_scholar".to_string(),
            "epmc" => "europepmc".to_string(),
            _ => name,
        };
        if !SCHOLAR_SOURCES.contains(&name.as_str()) {
            continue;
        }
        if name == "core" && !has_core_key {
            continue;
        }
        if !out.contains(&name) {
            out.push(name);
        }
    }
    out
}

pub fn list_templates(paper_orchestra_dir: &str) -> Vec<String> {
    let templates_dir = Path::new(paper_orchestra_dir).join("templates");
    let Ok(entries) = fs::read_dir(&templates_dir) else {
        return Vec::new();
    };
    let mut items: Vec<String> = entries
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir() && e.path().join("template.tex").exists())
        .filter_map(|e| e.file_name().into_string().ok())
        .collect();
    items.sort();
    items
}

pub fn check_environment(settings: &AppSettings) -> EnvironmentStatus {
    let repo_path = PathBuf::from(&settings.paper_orchestra_dir);
    let paper_orchestra_exists = repo_path.is_dir();
    let offline_hint = offline_volume_hint(&repo_path).unwrap_or_default();
    let cli_script_exists = repo_path.join("paper_writing_cli.py").is_file();

    let (python_exists, python_version) = run_version_probe(&settings.python_path, &["--version"]);
    let (pdflatex_available, pdflatex_version) = run_version_probe("pdflatex", &["--version"]);
    let (bibtex_available, _) = run_version_probe("bibtex", &["--version"]);

    let templates = list_templates(&settings.paper_orchestra_dir);
    let db_size_bytes = fs::metadata(&settings.db_path).map(|m| m.len()).unwrap_or(0);

    let env = read_env_text(&repo_path)
        .ok()
        .flatten()
        .map(|text| parse_env_map(&text))
        .unwrap_or_default();
    let llm = inspect_llm_config(&repo_path, &env);
    let set = |k: &str| env.get(k).is_some_and(|v| !v.trim().is_empty());
    let (auto_model, task_models) = effective_routing(&env, &llm);

    EnvironmentStatus {
        paper_orchestra_exists,
        offline_hint,
        cli_script_exists,
        python_exists,
        python_version,
        pdflatex_available,
        pdflatex_version,
        bibtex_available,
        templates,
        has_anthropic_key: set("ANTHROPIC_API_KEY"),
        has_deepseek_key: set("DEEPSEEK_API_KEY"),
        // providers.py reads GEMINI_API_KEY only (as the original code did).
        has_gemini_key: set("GEMINI_API_KEY"),
        has_vertex_ai: set("VERTEX_AI_PROJECT") && set("VERTEX_AI_LOCATION"),
        has_openai_key: set("OPENAI_API_KEY"),
        has_semantic_scholar_key: set("SEMANTIC_SCHOLAR_API_KEY"),
        configured_providers: llm.configured_providers,
        custom_providers: llm.custom_providers,
        providers_json_error: llm.providers_json_error,
        scholar_sources: resolve_scholar_sources(&env),
        auto_model,
        task_models,
        auto_fallback_model: llm.auto_fallback_model,
        db_path: settings.db_path.clone(),
        db_size_bytes,
    }
}

struct LlmConfigStatus {
    configured_providers: Vec<String>,
    custom_providers: Vec<String>,
    providers_json_error: String,
    auto_fallback_model: String,
    /// providers.json `auto_model` ("" if absent).
    file_auto_model: String,
    /// providers.json `task_models`.
    file_task_models: BTreeMap<String, String>,
}

/// What `llm_backend_utils` routes "auto" with: `PO_AUTO_MODEL` / `PO_TASK_MODEL_<TASK>`
/// from `.env`, falling back to providers.json `auto_model` / `task_models`
/// (`_global_auto_model()` and `_task_override()`). Task ids are lower-case.
fn effective_routing(
    env: &BTreeMap<String, String>,
    llm: &LlmConfigStatus,
) -> (String, BTreeMap<String, String>) {
    let env_auto = env.get("PO_AUTO_MODEL").map(|v| v.trim()).unwrap_or_default();
    let auto_model = if env_auto.is_empty() { llm.file_auto_model.clone() } else { env_auto.to_string() };
    let mut tasks = llm.file_task_models.clone();
    for (key, val) in env {
        if let Some(task) = key.strip_prefix("PO_TASK_MODEL_") {
            let val = val.trim();
            // Python looks up PO_TASK_MODEL_<TASK> with the task upper-cased, and
            // environment names are case-sensitive on macOS/Linux.
            if !task.is_empty() && task == task.to_ascii_uppercase() && !val.is_empty() {
                tasks.insert(task.to_ascii_lowercase(), val.to_string());
            }
        }
    }
    (auto_model, tasks)
}

/// Provider part of the environment check. An invalid providers.json makes
/// every provider lookup in Python raise, so no run can start: nothing is
/// reported as configured then.
fn inspect_llm_config(repo: &Path, env: &BTreeMap<String, String>) -> LlmConfigStatus {
    let path = providers_file_path(repo, env.get("PO_PROVIDERS_FILE").map(String::as_str));
    let parsed = read_providers_text(&path)
        .and_then(|text| providers::parse_providers_json(&text).map(|custom| (custom, text)));
    match parsed {
        Ok((custom, text)) => {
            let (file_auto_model, file_task_models) = providers::routing_config(&text);
            LlmConfigStatus {
                configured_providers: providers::configured_providers(env, &custom),
                custom_providers: providers::custom_provider_names(&custom),
                providers_json_error: String::new(),
                auto_fallback_model: providers::auto_fallback_model(env, &custom),
                file_auto_model,
                file_task_models,
            }
        }
        Err(err) => LlmConfigStatus {
            configured_providers: Vec::new(),
            custom_providers: Vec::new(),
            providers_json_error: err,
            auto_fallback_model: String::new(),
            file_auto_model: String::new(),
            file_task_models: BTreeMap::new(),
        },
    }
}

fn run_version_probe(program: &str, args: &[&str]) -> (bool, String) {
    if program.trim().is_empty() {
        return (false, String::new());
    }
    let mut cmd = Command::new(program);
    cmd.args(args);
    let path_env = enriched_path_env();
    if !path_env.is_empty() {
        cmd.env("PATH", path_env);
    }
    #[cfg(target_os = "windows")]
    {
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    match cmd.output() {
        Ok(out) if out.status.success() => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            let stderr = String::from_utf8_lossy(&out.stderr);
            let combined = if stdout.trim().is_empty() {
                stderr.trim()
            } else {
                stdout.trim()
            };
            let first_line = combined.lines().next().unwrap_or("").trim().to_string();
            (true, first_line)
        }
        _ => (false, String::new()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use uuid::Uuid;

    /// Temp repo dir that is removed on drop, even when an assertion fails.
    struct TempRepo(PathBuf);

    impl TempRepo {
        fn new(env: Option<&str>) -> Self {
            let dir = std::env::temp_dir().join(format!("po_env_test_{}", Uuid::new_v4()));
            fs::create_dir_all(&dir).unwrap();
            if let Some(text) = env {
                fs::write(dir.join(".env"), text).unwrap();
            }
            TempRepo(dir)
        }
        fn path(&self) -> String {
            self.0.to_string_lossy().to_string()
        }
        fn read(&self, name: &str) -> String {
            fs::read_to_string(self.0.join(name)).unwrap()
        }
    }

    impl Drop for TempRepo {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn windows_unc_prefixes_are_stripped() {
        assert_eq!(strip_windows_unc(Path::new(r"\\?\UNC\srv\share\a")), r"\\srv\share\a");
        assert_eq!(strip_windows_unc(Path::new(r"\\?\C:\x")), r"C:\x");
        assert_eq!(strip_windows_unc(Path::new(r"C:\x")), r"C:\x");
        assert_eq!(strip_windows_unc(Path::new(r"\\srv\share\a")), r"\\srv\share\a");
        assert_eq!(strip_windows_unc(Path::new("/Users/me/run")), "/Users/me/run");
    }

    fn settings_for(repo: &TempRepo) -> AppSettings {
        AppSettings {
            paper_orchestra_dir: repo.path(),
            python_path: String::new(),
            output_root_dir: String::new(),
            db_path: repo.0.join("none.db").to_string_lossy().to_string(),
        }
    }

    #[test]
    fn env_round_trip_preserves_comments_and_updates_keys() {
        let repo = TempRepo::new(Some("# Header comment\nGEMINI_API_KEY=old_key\nCUSTOM_VAR=keep_me\n"));
        let dir_str = repo.path();
        let mut cfg = read_env_config(&dir_str).unwrap();
        assert_eq!(cfg.gemini_api_key, "old_key");

        cfg.gemini_api_key = "new_key".to_string();
        cfg.openai_api_key = "sk-test".to_string();
        cfg.anthropic_api_key = "sk-ant-test".to_string();
        cfg.deepseek_api_key = "sk-ds-test".to_string();
        write_env_config(&dir_str, &cfg).unwrap();

        let content = repo.read(".env");
        assert!(content.contains("# Header comment"));
        assert!(content.contains("CUSTOM_VAR=keep_me"));
        assert!(content.contains("GEMINI_API_KEY=new_key"));
        assert!(content.contains("OPENAI_API_KEY=sk-test"));
        assert!(content.contains("ANTHROPIC_API_KEY=sk-ant-test"));
        assert!(content.contains("DEEPSEEK_API_KEY=sk-ds-test"));

        let reread = read_env_config(&dir_str).unwrap();
        assert_eq!(reread.gemini_api_key, "new_key");
        assert_eq!(reread.openai_api_key, "sk-test");
        assert_eq!(reread.anthropic_api_key, "sk-ant-test");
        assert_eq!(reread.deepseek_api_key, "sk-ds-test");
    }

    #[test]
    fn reads_new_managed_keys_and_extra_env() {
        let repo = TempRepo::new(Some(concat!(
            "export DASHSCOPE_API_KEY=sk-qwen\n",
            "PO_AUTO_MODEL=\"qwen:qwen-max\"\n",
            "PO_SCHOLAR_SOURCES='openalex, s2'\n",
            "S2_MIN_INTERVAL_SEC=1.5  # one call per 1.5 s\n",
            "OPENALEX_EMAIL=me@example.org\n",
            "CORE_API_KEY=core-k\n",
            "PO_TASK_MODEL_WRITER=moonshot:kimi-k2\n",
            "not a valid line\n",
            "bad key=1\n",
        )));
        let cfg = read_env_config(&repo.path()).unwrap();
        assert_eq!(cfg.auto_model, "qwen:qwen-max");
        assert_eq!(cfg.scholar_sources, "openalex, s2");
        assert_eq!(cfg.s2_min_interval_sec, "1.5");
        assert_eq!(cfg.openalex_email, "me@example.org");
        assert_eq!(cfg.core_api_key, "core-k");
        assert_eq!(
            cfg.extra_env,
            BTreeMap::from([
                ("DASHSCOPE_API_KEY".to_string(), "sk-qwen".to_string()),
                ("PO_TASK_MODEL_WRITER".to_string(), "moonshot:kimi-k2".to_string()),
            ])
        );
        assert_eq!(cfg.providers_json, "");
    }

    #[test]
    fn extra_env_round_trip_update_remove_and_append() {
        let repo = TempRepo::new(Some(concat!(
            "# Providers\n",
            "MOONSHOT_API_KEY=old\n",
            "export ZHIPU_API_KEY=z\n",
            "\n",
            "# Task routing\n",
            "PO_TASK_MODEL_WRITER=qwen:qwen-max\n",
            "OPENAI_API_KEY=sk-o\n",
        )));
        let dir = repo.path();
        let mut cfg = read_env_config(&dir).unwrap();
        assert_eq!(cfg.extra_env.len(), 3);

        cfg.extra_env.insert("MOONSHOT_API_KEY".into(), "new".into()); // update in place
        cfg.extra_env.remove("ZHIPU_API_KEY"); // removed from the map
        cfg.extra_env.insert("PO_TASK_MODEL_WRITER".into(), "   ".into()); // emptied
        cfg.extra_env.insert("ARK_API_KEY".into(), "ark-1".into()); // new
        cfg.extra_env.insert("EMPTY_NEW".into(), "".into()); // new but empty: not written
        cfg.extra_env.insert("WITH_SPACE".into(), "a b #c".into());
        write_env_config(&dir, &cfg).unwrap();

        let content = repo.read(".env");
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(lines[0], "# Providers");
        assert_eq!(lines[1], "MOONSHOT_API_KEY=new");
        assert_eq!(lines[2], "");
        assert_eq!(lines[3], "# Task routing");
        assert_eq!(lines[4], "PO_TASK_MODEL_WRITER="); // emptied, not removed
        assert_eq!(lines[5], "OPENAI_API_KEY=sk-o");
        assert!(!content.contains("ZHIPU_API_KEY"));
        assert!(!content.contains("EMPTY_NEW"));
        // Empty managed keys are not appended; new extra keys go at the end.
        assert!(!content.contains("PO_DEFAULT_PROVIDER"));
        assert_eq!(lines.len(), 8, "{content}");
        assert!(content.ends_with("ARK_API_KEY=ark-1\nWITH_SPACE=\"a b #c\"\n"));

        let reread = read_env_config(&dir).unwrap();
        assert_eq!(
            reread.extra_env,
            BTreeMap::from([
                ("ARK_API_KEY".to_string(), "ark-1".to_string()),
                ("MOONSHOT_API_KEY".to_string(), "new".to_string()),
                ("PO_TASK_MODEL_WRITER".to_string(), "".to_string()),
                ("WITH_SPACE".to_string(), "a b #c".to_string()),
            ])
        );
        assert_eq!(reread.openai_api_key, "sk-o");

        // Saving the re-read config again changes nothing.
        write_env_config(&dir, &reread).unwrap();
        assert_eq!(repo.read(".env"), content);
    }

    #[test]
    fn write_preserves_comments_quoting_and_line_endings() {
        let original = concat!(
            "# PaperOrchestra keys\r\n",
            "  # indented comment\r\n",
            "\r\n",
            "OPENAI_API_KEY=\"sk-quoted\"  # main key\r\n",
            "export KIMI_API_KEY=kimi\r\n",
            "garbage line without equals\r\n",
            "# trailing comment\r\n",
        );
        let repo = TempRepo::new(Some(original));
        let dir = repo.path();
        let mut cfg = read_env_config(&dir).unwrap();
        assert_eq!(cfg.openai_api_key, "sk-quoted");
        cfg.extra_env.insert("KIMI_API_KEY".into(), "kimi-2".into());
        write_env_config(&dir, &cfg).unwrap();

        let content = repo.read(".env");
        assert!(content.starts_with(concat!(
            "# PaperOrchestra keys\r\n",
            "  # indented comment\r\n",
            "\r\n",
            "OPENAI_API_KEY=\"sk-quoted\"  # main key\r\n",
            "export KIMI_API_KEY=kimi-2\r\n",
            "garbage line without equals\r\n",
            "# trailing comment\r\n",
        )));
        assert!(!content.replace("\r\n", "").contains('\n'), "mixed line endings");

        // No temp files are left behind.
        let leftovers: Vec<_> = fs::read_dir(&repo.0)
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().ends_with(".tmp"))
            .collect();
        assert!(leftovers.is_empty());
    }

    #[test]
    fn keeps_inline_comments_and_empty_placeholders() {
        let repo = TempRepo::new(Some(concat!(
            "OPENAI_API_KEY=sk-old  # work account\n",
            "ANTHROPIC_API_KEY=\"sk-a\" # personal\n",
            "DEEPSEEK_API_KEY=sk-d # cleared below\n",
            "DASHSCOPE_API_KEY=\n",
            "MOONSHOT_API_KEY=\n",
        )));
        let dir = repo.path();
        let mut cfg = read_env_config(&dir).unwrap();
        assert_eq!(cfg.extra_env.get("DASHSCOPE_API_KEY").map(String::as_str), Some(""));
        cfg.openai_api_key = "sk-new".into();
        cfg.anthropic_api_key = "sk b".into();
        cfg.deepseek_api_key = "".into();
        cfg.extra_env.remove("MOONSHOT_API_KEY"); // row deleted in the UI
        write_env_config(&dir, &cfg).unwrap();
        assert_eq!(
            repo.read(".env"),
            concat!(
                "OPENAI_API_KEY=sk-new  # work account\n",
                "ANTHROPIC_API_KEY=\"sk b\" # personal\n",
                "DEEPSEEK_API_KEY=\"\" # cleared below\n",
                "DASHSCOPE_API_KEY=\n",
            )
        );
        let reread = read_env_config(&dir).unwrap();
        assert_eq!(reread.openai_api_key, "sk-new");
        assert_eq!(reread.anthropic_api_key, "sk b");
        assert_eq!(reread.deepseek_api_key, "");
        assert_eq!(inline_comment("v#x"), "");
        assert_eq!(inline_comment("'a # b'#c"), "#c");
        assert_eq!(rewrite_env_line(false, "K", "v", "#c"), "K=v #c");
    }

    #[test]
    fn multi_line_values_are_kept_verbatim() {
        let original = concat!(
            "PRIVATE_KEY=\"-----BEGIN KEY-----\n",
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC==\n",
            "-----END KEY-----\"\n",
            "OPENAI_API_KEY=sk-o\n",
            "BROKEN=\"never closed\n",
        );
        let repo = TempRepo::new(Some(original));
        let dir = repo.path();
        let mut cfg = read_env_config(&dir).unwrap();
        // Neither the block nor its continuation lines show up as variables.
        assert_eq!(
            cfg.extra_env.keys().collect::<Vec<_>>(),
            ["BROKEN"],
            "{:?}",
            cfg.extra_env
        );
        cfg.openai_api_key = "sk-new".into();
        write_env_config(&dir, &cfg).unwrap();
        assert_eq!(repo.read(".env"), original.replace("sk-o", "sk-new"));
    }

    #[test]
    fn refuses_to_save_over_files_changed_since_read() {
        let repo = TempRepo::new(Some("OPENAI_API_KEY=sk-o\n"));
        let dir = repo.path();
        let mut cfg = read_env_config(&dir).unwrap();
        assert!(!cfg.source_fingerprint.is_empty());
        cfg.openai_api_key = "sk-new".into();
        // Edited in a text editor while the settings page was open.
        fs::write(repo.0.join(".env"), "OPENAI_API_KEY=sk-o\nOPENROUTER_API_KEY=sk-or\n").unwrap();
        assert!(write_env_config(&dir, &cfg).unwrap_err().contains("被修改"));
        assert!(repo.read(".env").contains("OPENROUTER_API_KEY=sk-or"));
        // providers.json counts too.
        let mut cfg = read_env_config(&dir).unwrap();
        fs::write(repo.0.join("providers.json"), "[]").unwrap();
        cfg.openai_api_key = "sk-new".into();
        assert!(write_env_config(&dir, &cfg).is_err());

        // A config read from another repo dir cannot be written here.
        let other = TempRepo::new(Some("OPENAI_API_KEY=\n"));
        let mut from_other = read_env_config(&other.path()).unwrap();
        from_other.openai_api_key = "sk-x".into();
        assert!(write_env_config(&dir, &from_other).is_err());

        // A fresh read saves fine; an empty fingerprint skips the check.
        let mut cfg = read_env_config(&dir).unwrap();
        cfg.openai_api_key = "sk-new".into();
        write_env_config(&dir, &cfg).unwrap();
        assert!(repo.read(".env").contains("OPENROUTER_API_KEY=sk-or"));
        cfg.source_fingerprint.clear();
        write_env_config(&dir, &cfg).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn new_env_file_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let repo = TempRepo::new(None);
        let cfg = EnvSecretsConfig { openai_api_key: "sk".into(), ..Default::default() };
        write_env_config(&repo.path(), &cfg).unwrap();
        let mode = fs::metadata(repo.0.join(".env")).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
        // An existing file keeps its own mode.
        fs::set_permissions(repo.0.join(".env"), fs::Permissions::from_mode(0o640)).unwrap();
        write_env_config(&repo.path(), &cfg).unwrap();
        let mode = fs::metadata(repo.0.join(".env")).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o640);
    }

    #[test]
    fn write_creates_env_when_missing() {
        let repo = TempRepo::new(None);
        let cfg = EnvSecretsConfig {
            auto_model: "qwen:qwen-max".into(),
            extra_env: BTreeMap::from([("DASHSCOPE_API_KEY".to_string(), "sk-q".to_string())]),
            ..Default::default()
        };
        write_env_config(&repo.path(), &cfg).unwrap();
        let content = repo.read(".env");
        assert!(content.starts_with("# Managed by PaperOrchestra Studio\n"));
        assert!(content.contains("\nPO_AUTO_MODEL=qwen:qwen-max\n"));
        assert!(content.ends_with("DASHSCOPE_API_KEY=sk-q\n"));
        assert!(!repo.0.join("providers.json").exists());
    }

    #[test]
    fn rejects_invalid_extra_env_keys_without_writing() {
        let original = "OPENAI_API_KEY=sk-o\n";
        let repo = TempRepo::new(Some(original));
        let dir = repo.path();
        for bad in ["1ABC", "MY-KEY", "MY KEY", "键", "A=B"] {
            let mut cfg = read_env_config(&dir).unwrap();
            cfg.openai_api_key = "changed".into();
            cfg.extra_env.insert(bad.into(), "v".into());
            let err = write_env_config(&dir, &cfg).unwrap_err();
            assert!(err.contains("无效"), "{bad}: {err}");
        }
        let mut cfg = read_env_config(&dir).unwrap();
        cfg.extra_env.insert("OPENAI_API_KEY".into(), "dup".into());
        assert!(write_env_config(&dir, &cfg).unwrap_err().contains("专门的设置项"));
        assert_eq!(repo.read(".env"), original);

        assert!(is_valid_env_key("_A1"));
        assert!(is_valid_env_key("po_task_model_writer"));
        assert!(!is_valid_env_key(""));
    }

    #[test]
    fn strips_newlines_from_values() {
        let repo = TempRepo::new(None);
        let cfg = EnvSecretsConfig {
            openai_api_key: "sk-a\r\nINJECTED=1".into(),
            extra_env: BTreeMap::from([("X_KEY".to_string(), "line1\nY=2".to_string())]),
            ..Default::default()
        };
        write_env_config(&repo.path(), &cfg).unwrap();
        let content = repo.read(".env");
        assert!(content.contains("\nOPENAI_API_KEY=sk-aINJECTED=1\n"));
        assert!(content.contains("\nX_KEY=line1Y=2\n"));
        assert!(!content.contains("\nINJECTED=") && !content.contains("\nY=2"));
    }

    #[test]
    fn providers_json_is_validated_before_anything_is_written() {
        let original_env = "OPENAI_API_KEY=sk-o\n";
        let original_providers = "[{\"name\": \"keep\"}]";
        let repo = TempRepo::new(Some(original_env));
        fs::write(repo.0.join("providers.json"), original_providers).unwrap();
        let dir = repo.path();

        let mut cfg = read_env_config(&dir).unwrap();
        assert_eq!(cfg.providers_json, original_providers);
        cfg.openai_api_key = "changed".into();
        for (bad, needle) in [
            ("{not json", "JSON"),
            ("[{\"type\": \"openai\"}]", "name"),
            ("[{\"name\": \"x\", \"type\": \"cohere\"}]", "type"),
        ] {
            cfg.providers_json = bad.into();
            let err = write_env_config(&dir, &cfg).unwrap_err();
            assert!(err.contains(needle), "{err}");
            assert_eq!(repo.read(".env"), original_env);
            assert_eq!(repo.read("providers.json"), original_providers);
        }

        // Empty text leaves the existing file alone.
        cfg.providers_json = "  ".into();
        write_env_config(&dir, &cfg).unwrap();
        assert_eq!(repo.read("providers.json"), original_providers);
        assert!(repo.read(".env").contains("OPENAI_API_KEY=changed"));

        // Valid text is written exactly as given (after a fresh read, as the UI does).
        cfg.source_fingerprint = read_env_config(&dir).unwrap().source_fingerprint;
        let text = "{\n  \"providers\": [{\"name\": \"relay\", \"type\": \"anthropic\"}]\n}\n";
        cfg.providers_json = text.into();
        write_env_config(&dir, &cfg).unwrap();
        assert_eq!(repo.read("providers.json"), text);
        assert_eq!(read_env_config(&dir).unwrap().providers_json, text);
    }

    #[test]
    fn providers_file_follows_po_providers_file() {
        let repo = TempRepo::new(Some("PO_PROVIDERS_FILE=config/my_providers.json\n"));
        fs::create_dir_all(repo.0.join("config")).unwrap();
        fs::write(repo.0.join("config/my_providers.json"), "[]").unwrap();
        let dir = repo.path();
        let mut cfg = read_env_config(&dir).unwrap();
        assert_eq!(cfg.providers_json, "[]");
        cfg.providers_json = "[{\"name\": \"a\"}]".into();
        write_env_config(&dir, &cfg).unwrap();
        assert_eq!(repo.read("config/my_providers.json"), "[{\"name\": \"a\"}]");
        assert!(!repo.0.join("providers.json").exists());

        let abs = repo.0.join("abs.json");
        fs::write(&abs, "{\"providers\": []}").unwrap();
        let p = providers_file_path(Path::new("/elsewhere"), Some(abs.to_str().unwrap()));
        assert_eq!(p, abs);
        assert_eq!(providers_file_path(Path::new("/r"), Some("  ")), Path::new("/r/providers.json"));
    }

    #[test]
    fn check_environment_reports_task_models_like_python_routes_them() {
        // .env wins over providers.json, blank .env values fall back to the file.
        let repo = TempRepo::new(Some(concat!(
            "ANTHROPIC_API_KEY=sk-a\n",
            "PO_TASK_MODEL_OUTLINE=claude-opus-5-5\n",
            "PO_TASK_MODEL_SECTION_WRITING= deepseek-flash \n",
            "PO_TASK_MODEL_PLOT_PLAN=\n",
            "PO_TASK_MODEL_Peer_Review=qwen:qwen-max\n",
        )));
        fs::write(
            repo.0.join("providers.json"),
            r#"{"auto_model": "anthropic:claude-sonnet-5-5", "task_models": {"outline": "qwen:qwen-max", "plot_plan": "gemini-4-argon"}}"#,
        )
        .unwrap();
        let st = check_environment(&settings_for(&repo));
        assert_eq!(st.auto_model, "anthropic:claude-sonnet-5-5");
        assert_eq!(st.task_models.len(), 3);
        assert_eq!(st.task_models["outline"], "claude-opus-5-5");
        assert_eq!(st.task_models["section_writing"], "deepseek-flash");
        assert_eq!(st.task_models["plot_plan"], "gemini-4-argon");
    }

    #[test]
    fn check_environment_reports_providers_and_sources() {
        let repo = TempRepo::new(Some(concat!(
            "OPENAI_API_KEY=sk-o\n",
            "DASHSCOPE_API_KEY=sk-q\n",
            "OLLAMA_BASE_URL=http://localhost:11434/v1\n",
            "VLLM_API_KEY=\n",
            "RELAY_API_KEY=r\n",
            "PO_AUTO_MODEL= qwen:qwen-max \n",
            "PO_SCHOLAR_SOURCES=arxiv,S2,epmc,unknown,arxiv,core\n",
        )));
        fs::write(
            repo.0.join("providers.json"),
            r#"{"providers": [{"name": "relay"}, {"name": "spare", "type": "gemini"}]}"#,
        )
        .unwrap();
        let st = check_environment(&settings_for(&repo));
        assert_eq!(st.configured_providers, ["openai", "qwen", "ollama", "relay"]);
        assert_eq!(st.custom_providers, ["relay", "spare"]);
        assert_eq!(st.providers_json_error, "");
        assert_eq!(st.scholar_sources, ["arxiv", "semantic_scholar", "europepmc"]);
        assert_eq!(st.auto_model, "qwen:qwen-max");
        assert!(st.has_openai_key && !st.has_anthropic_key);

        assert_eq!(st.auto_fallback_model, "qwen:qwen-max");

        // Python raises on every provider lookup with a broken file: no run can start.
        fs::write(repo.0.join("providers.json"), "[{\"name\": \"relay\",}]").unwrap();
        let st = check_environment(&settings_for(&repo));
        assert!(st.providers_json_error.contains("JSON"), "{}", st.providers_json_error);
        assert!(st.custom_providers.is_empty());
        assert!(st.configured_providers.is_empty());
        assert_eq!(st.auto_fallback_model, "");

        // An empty file declares nothing (Python ignores it too).
        fs::write(repo.0.join("providers.json"), "  \n").unwrap();
        let st = check_environment(&settings_for(&repo));
        assert_eq!(st.providers_json_error, "");
        assert_eq!(st.configured_providers, ["openai", "qwen", "ollama"]);
    }

    #[test]
    fn google_api_key_is_not_a_gemini_key() {
        let repo = TempRepo::new(Some("GOOGLE_API_KEY=g\n"));
        let st = check_environment(&settings_for(&repo));
        assert!(!st.has_gemini_key);
        assert!(st.configured_providers.is_empty());
    }

    #[test]
    fn scholar_sources_resolution() {
        let env = |pairs: &[(&str, &str)]| -> BTreeMap<String, String> {
            pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
        };
        assert_eq!(
            resolve_scholar_sources(&env(&[])),
            ["semantic_scholar", "openalex", "crossref", "arxiv", "dblp", "europepmc", "pubmed"]
        );
        assert_eq!(
            resolve_scholar_sources(&env(&[("CORE_API_KEY", "k")])).last().map(String::as_str),
            Some("core")
        );
        assert_eq!(
            resolve_scholar_sources(&env(&[("PO_SCHOLAR_SOURCES", " SemanticScholar , pubmed,,s2 ")])),
            ["semantic_scholar", "pubmed"]
        );
        assert_eq!(
            resolve_scholar_sources(&env(&[("PO_SCHOLAR_SOURCES", "core,dblp"), ("CORE_API_KEY", " ")])),
            ["dblp"]
        );
        assert!(resolve_scholar_sources(&env(&[("PO_SCHOLAR_SOURCES", "bogus")])).is_empty());
        assert_eq!(resolve_scholar_sources(&env(&[("PO_SCHOLAR_SOURCES", "  ")])).len(), 7);
    }

    #[test]
    fn unquote_and_quote_match_python_dotenv() {
        assert_eq!(unquote_env_value("plain"), "plain");
        assert_eq!(unquote_env_value("a#b"), "a#b");
        assert_eq!(unquote_env_value("val # comment"), "val");
        assert_eq!(unquote_env_value("'single # kept'"), "single # kept");
        assert_eq!(unquote_env_value(r#""dq \"x\" \\ end" # c"#), r#"dq "x" \ end"#);
        assert_eq!(unquote_env_value("\"unterminated"), "\"unterminated");
        assert_eq!(unquote_env_value(r"'it\'s \\ \n'"), r"it's \ \n");
        assert_eq!(unquote_env_value(r#""keep \n escapes""#), r"keep \n escapes");
        for v in ["plain", "a b", "it's here", "say \"hi\" it's", "'lead", r"C:\path with space\x", r"\\srv\share x"] {
            assert_eq!(unquote_env_value(&quote_env_value(v)), v, "{v}");
        }
        assert_eq!(quote_env_value(r"D:\repo\providers.json"), r"D:\repo\providers.json");
        assert_eq!(quote_env_value(r"C:\a b"), r#""C:\\a b""#);
    }

    #[test]
    fn env_config_serde_matches_frontend_contract() {
        let json = serde_json::to_value(EnvSecretsConfig::default()).unwrap();
        let mut keys: Vec<&str> = json.as_object().unwrap().keys().map(String::as_str).collect();
        keys.sort_unstable();
        let mut expected = vec![
            "anthropicApiKey", "anthropicBaseUrl", "deepseekApiKey", "deepseekBaseUrl",
            "geminiApiKey", "vertexAiProject", "vertexAiLocation", "openaiApiKey",
            "openaiBaseUrl", "semanticScholarApiKey", "smtpEmail", "smtpPassword",
            "s2MinIntervalSec", "openalexEmail", "openalexApiKey", "crossrefMailto",
            "crossrefPlusToken", "ncbiApiKey", "ncbiEmail", "coreApiKey", "scholarSources",
            "autoModel", "defaultProvider", "extraEnv", "providersJson", "sourceFingerprint",
        ];
        expected.sort_unstable();
        assert_eq!(keys, expected);

        // Partial payloads (older frontends) still deserialize.
        let cfg: EnvSecretsConfig =
            serde_json::from_str(r#"{"openaiApiKey": "sk", "extraEnv": {"A": "1"}}"#).unwrap();
        assert_eq!(cfg.openai_api_key, "sk");
        assert_eq!(cfg.extra_env.get("A").map(String::as_str), Some("1"));
        assert_eq!(cfg.providers_json, "");

        let status = serde_json::to_value(EnvironmentStatus::default()).unwrap();
        for key in [
            "configuredProviders", "customProviders", "providersJsonError", "scholarSources", "autoModel",
            "autoFallbackModel",
        ] {
            assert!(status.get(key).is_some(), "{key}");
        }
    }

    #[test]
    fn typed_home_paths_are_expanded() {
        let home = Path::new("/Users/alice");
        let under_home = |rest: &str| home.join(rest).to_string_lossy().to_string();
        assert_eq!(expand_home("~", Some(home)), "/Users/alice");
        assert_eq!(
            expand_home("~/paper-orchestra", Some(home)),
            under_home("paper-orchestra")
        );
        assert_eq!(
            expand_home("~/paper-orchestra/.venv/bin/python", Some(home)),
            under_home("paper-orchestra/.venv/bin/python")
        );
        // The separator after the home folder is the platform's: a backslash
        // on Windows, which Windows reads fine.
        #[cfg(unix)]
        assert_eq!(
            expand_home("~/paper-orchestra/.venv/bin/python", Some(home)),
            "/Users/alice/paper-orchestra/.venv/bin/python"
        );
        // Only a leading "~" or "~/" is a home folder.
        for v in [
            "~alice/x",
            "/a/~/b",
            "python3",
            r"D:\projects\paper-orchestra",
            "",
        ] {
            assert_eq!(expand_home(v, Some(home)), v);
        }
        assert_eq!(expand_home("~/x", None), "~/x");
    }

    #[cfg(unix)]
    #[test]
    fn dangling_link_into_unplugged_volume_names_the_disk() {
        let dir = std::env::temp_dir().join(format!("po-offline-{}", Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        let link = dir.join("paper-orchestra");
        std::os::unix::fs::symlink("/Volumes/ExternalDisk/paper-orchestra", &link).unwrap();
        let other = dir.join("other");
        std::os::unix::fs::symlink("/Volumes/没插的盘/x", &other).unwrap();
        assert!(offline_volume_hint(&link).unwrap().contains("ExternalDisk"));
        assert!(offline_volume_hint(&other.join("sub")).unwrap().contains("没插的盘"));
        assert_eq!(offline_volume_hint(&dir), None);
        assert_eq!(offline_volume_hint(&dir.join("missing")), None);
        fs::remove_dir_all(&dir).ok();
    }

    #[cfg(unix)]
    #[test]
    fn picking_the_interpreter_a_venv_links_to_keeps_the_venv() {
        let repo = TempRepo::new(None);
        let base = repo.0.join("base").join("bin");
        fs::create_dir_all(&base).unwrap();
        fs::write(base.join("python3.13"), "#!/bin/sh\n").unwrap();
        std::os::unix::fs::symlink("python3.13", base.join("python3")).unwrap();
        let venv_bin = repo.0.join(".venv").join("bin");
        fs::create_dir_all(&venv_bin).unwrap();
        std::os::unix::fs::symlink(base.join("python3"), venv_bin.join("python3")).unwrap();
        std::os::unix::fs::symlink("python3", venv_bin.join("python")).unwrap();
        let repo_s = repo.path();
        let venv_python = venv_bin.join("python").to_string_lossy().to_string();

        // What the macOS dialog hands back for .venv/bin/python: the resolved
        // base file (fully resolved, e.g. /private/var/... rather than /var/...).
        let resolved = fs::canonicalize(base.join("python3.13"))
            .unwrap()
            .to_string_lossy()
            .to_string();
        assert_eq!(prefer_venv_python(&resolved, &repo_s), venv_python);
        // Links chosen on purpose are kept: the base's python3 link, and
        // another venv made from the same base.
        let base_link = base.join("python3").to_string_lossy().to_string();
        assert_eq!(prefer_venv_python(&base_link, &repo_s), base_link);
        let other_venv = repo.0.join("venvs").join("po").join("bin");
        fs::create_dir_all(&other_venv).unwrap();
        std::os::unix::fs::symlink(base.join("python3"), other_venv.join("python")).unwrap();
        let other_venv_python = other_venv.join("python").to_string_lossy().to_string();
        assert_eq!(
            prefer_venv_python(&other_venv_python, &repo_s),
            other_venv_python
        );
        // The venv paths themselves stay as typed.
        assert_eq!(prefer_venv_python(&venv_python, &repo_s), venv_python);
        let venv_python3 = venv_bin.join("python3").to_string_lossy().to_string();
        assert_eq!(prefer_venv_python(&venv_python3, &repo_s), venv_python3);
        // A different interpreter, a bare command name and a missing file are kept.
        let other = repo.0.join("other");
        fs::write(&other, "x").unwrap();
        let other_s = other.to_string_lossy().to_string();
        assert_eq!(prefer_venv_python(&other_s, &repo_s), other_s);
        assert_eq!(prefer_venv_python("python3", &repo_s), "python3");
        assert_eq!(
            prefer_venv_python("/no/such/python", &repo_s),
            "/no/such/python"
        );
        // No venv in the repo: unchanged.
        let empty = TempRepo::new(None);
        assert_eq!(prefer_venv_python(&resolved, &empty.path()), resolved);
    }

    #[test]
    fn merge_path_keeps_inherited_order_and_adds_missing_dirs_once() {
        let extra = [
            PathBuf::from("/Library/TeX/texbin"),
            PathBuf::from("/usr/bin"),
            PathBuf::from("/opt/homebrew/bin"),
            PathBuf::from("/nowhere/bin"),
        ];
        let exists = |p: &Path| p != Path::new("/nowhere/bin");
        // The Finder/Dock PATH: inherited entries first, extras after, no
        // duplicate of /usr/bin, and missing directories left out.
        assert_eq!(
            merge_path_dirs("/usr/bin:/bin:/usr/sbin:/sbin", &extra, exists),
            "/usr/bin:/bin:/usr/sbin:/sbin:/Library/TeX/texbin:/opt/homebrew/bin"
        );
        // Exact entries are compared, not substrings (/usr/bin/x is not /usr/bin).
        assert_eq!(
            merge_path_dirs("/usr/bin/x", &[PathBuf::from("/usr/bin")], |_| true),
            "/usr/bin/x:/usr/bin"
        );
        // A trailing slash on an inherited entry still counts as the same dir.
        assert_eq!(
            merge_path_dirs(
                "/opt/homebrew/bin/",
                &[PathBuf::from("/opt/homebrew/bin")],
                |_| true
            ),
            "/opt/homebrew/bin/"
        );
        assert_eq!(
            merge_path_dirs("", &extra[..1], |_| true),
            "/Library/TeX/texbin"
        );
    }

    // PATH here is the macOS one, separated by ":", so a Windows temp folder
    // (which starts with a drive letter and ":") cannot be written in it.
    #[cfg(unix)]
    #[test]
    fn mac_path_dirs_find_tinytex_and_home_bin() {
        let home = TempRepo::new(None);
        let tiny = home.0.join("Library").join("TinyTeX").join("bin");
        fs::create_dir_all(tiny.join("universal-darwin")).unwrap();
        fs::create_dir_all(tiny.join("x86_64-darwin")).unwrap();
        fs::write(tiny.join("README"), "not a dir").unwrap();
        let dirs = macos_extra_path_dirs(Some(&home.0));
        assert_eq!(dirs[0], PathBuf::from("/Library/TeX/texbin"));
        assert_eq!(dirs[1], tiny.join("universal-darwin"));
        assert_eq!(dirs[2], tiny.join("x86_64-darwin"));
        assert_eq!(dirs[3], home.0.join("bin"));
        assert!(!dirs.contains(&tiny.join("README")));
        for d in ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"] {
            assert!(dirs.contains(&PathBuf::from(d)), "{d}");
        }
        // pdflatex in TinyTeX is found through the merged PATH.
        let merged = merge_path_dirs("/usr/bin:/bin", &dirs, |p| p.is_dir());
        assert!(merged
            .split(':')
            .any(|p| Path::new(p) == tiny.join("universal-darwin")));
        // No HOME: the system-wide places only.
        let no_home = macos_extra_path_dirs(None);
        assert_eq!(no_home[0], PathBuf::from("/Library/TeX/texbin"));
        assert!(!no_home
            .iter()
            .any(|p| p.ends_with("bin") && p.starts_with(&home.0)));
    }

    #[test]
    fn repo_dir_candidates_per_os() {
        let home = PathBuf::from("/Users/u");
        let mac = paper_orchestra_dir_candidates("macos", Some(home.clone()), None);
        let bundle = |base: &str| home.join(base).join("PaperOrchestra_源码").join("paper-orchestra");
        // existing candidates keep their order, the source-bundle folders come last
        assert_eq!(
            mac,
            vec![
                PathBuf::from("../paper-orchestra"),
                PathBuf::from("paper-orchestra"),
                home.join("paper-orchestra"),
                home.join("Documents").join("paper-orchestra"),
                home.join("Desktop").join("paper-orchestra"),
                bundle("Desktop"),
                bundle("Documents"),
            ]
        );

        let linux = paper_orchestra_dir_candidates("linux", Some(home.clone()), None);
        assert_eq!(linux.len(), 5);
        assert!(!linux.iter().any(|p| p.to_string_lossy().contains("PaperOrchestra_源码")));

        // Windows: the Desktop bundle first, then relative and USERPROFILE paths.
        let profile = PathBuf::from(r"C:\Users\u");
        let win = paper_orchestra_dir_candidates("windows", None, Some(profile.clone()));
        assert_eq!(win[0], profile.join("Desktop").join("PaperOrchestra_源码").join("paper-orchestra"));
        assert_eq!(win[1], PathBuf::from("../paper-orchestra"));
        assert!(win.contains(&profile.join("Documents").join("paper-orchestra")));
        assert_eq!(paper_orchestra_dir_candidates("windows", None, None).len(), 2);
    }

    #[test]
    fn secret_values_cover_env_and_providers_json() {
        let repo = TempRepo::new(Some(
            "OPENAI_API_KEY=sk-test-1234567890\nSMTP_PASSWORD='hunter2hunter2'\nOPENAI_BASE_URL=https://api.example.com/v1\nSHORT_KEY=abc\n",
        ));
        fs::write(
            repo.0.join("providers.json"),
            r#"{"providers": {"acme": {"base_url": "https://acme.test", "api_key": "acme-secret-value"}}}"#,
        )
        .unwrap();
        let secrets = secret_values(&repo.path());
        assert!(secrets.contains(&"sk-test-1234567890".to_string()), "{secrets:?}");
        assert!(secrets.contains(&"hunter2hunter2".to_string()), "{secrets:?}");
        assert!(secrets.contains(&"acme-secret-value".to_string()), "{secrets:?}");
        assert!(!secrets.iter().any(|s| s.contains("example.com") || s == "abc"), "{secrets:?}");
    }

    #[test]
    fn secret_values_skip_variable_names_from_api_key_env() {
        let repo = TempRepo::new(Some("RELAY_API_TOKEN=relay-secret-value-123\n"));
        fs::write(
            repo.0.join("providers.json"),
            r#"{"providers": [{"name": "relay", "api_key_env": "RELAY_API_TOKEN"}]}"#,
        )
        .unwrap();
        let secrets = secret_values(&repo.path());
        assert!(secrets.contains(&"relay-secret-value-123".to_string()), "{secrets:?}");
        assert!(!secrets.contains(&"RELAY_API_TOKEN".to_string()), "{secrets:?}");
    }
}
