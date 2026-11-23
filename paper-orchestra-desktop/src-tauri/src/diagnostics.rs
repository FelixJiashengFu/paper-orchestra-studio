//! "测试连接 / 测试文献源": runs `tools/check_providers.py --json` from the
//! PaperOrchestra repo and hands its JSON report to the frontend.
//!
//! Contract (see paper-orchestra/tools/check_providers.py): one JSON object on
//! stdout, `{"providers": [...], "scholar": [...]}` (plus `"models": [...]`
//! with `--list-models`), exit code 0 whenever the
//! script ran. The script itself redacts keys in its error strings; the text
//! that reaches the UI from here is masked once more as a second line of
//! defence (known secret values from .env / providers.json, plus anything
//! that looks like an API key).

use std::collections::{BTreeMap, HashMap};
use std::path::Path;
use std::process::Stdio;
use std::time::Duration;

use chrono::Utc;
use serde_json::{Map, Value};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, BufReader};
use tokio::process::Command;
use tokio::sync::{oneshot, Mutex};

use crate::env_config::enriched_path_env;
use crate::models::StudioEventPayload;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Script path relative to the repo root.
pub const CHECK_SCRIPT: &str = "tools/check_providers.py";
/// 「导入填写单」 parser, relative to the repo root.
pub const INTAKE_SCRIPT: &str = "tools/parse_intake_form.py";
/// Whole-run limit (the script uses ~30 s per call on its own).
pub const CHECK_TIMEOUT: Duration = Duration::from_secs(120);

/// Longest stderr/stdout excerpt quoted in an error message.
const MAX_DETAIL_CHARS: usize = 600;

/// Arguments passed to the script.
pub fn check_args(ping: bool, scholar: bool) -> Vec<&'static str> {
    let mut args = vec!["--json"];
    if ping {
        args.push("--ping");
    }
    if scholar {
        args.push("--scholar");
    }
    args
}

/// `--ping-model=NAME=MODEL` arguments: the model each listed provider is tested
/// with on the diagnostics page ("测试模型"). Entries that are empty or do not look
/// like a provider name / model id are dropped rather than passed to Python.
pub fn ping_model_args(overrides: &BTreeMap<String, String>) -> Vec<String> {
    overrides
        .iter()
        .filter_map(|(name, model)| {
            let (name, model) = (name.trim(), model.trim());
            // providers.json allows any name (e.g. 硅基流动): reject only what
            // would break NAME=MODEL parsing.
            let name_ok = !name.is_empty()
                && name.chars().count() <= 64
                && !name.contains('=')
                && !name.starts_with('-')
                && !name.chars().any(|c| c.is_control());
            let model_ok = !model.is_empty()
                && model.len() <= 200
                && !model.starts_with('-')
                && !model.chars().any(|c| c.is_control() || c.is_whitespace());
            (name_ok && model_ok).then(|| format!("--ping-model={name}={model}"))
        })
        .collect()
}

/// Arguments for "从各家 API 拉取最新模型": each configured provider's model
/// list (free: no generation request), reported under `"models"`.
pub fn list_models_args() -> Vec<&'static str> {
    vec!["--json", "--list-models"]
}

/// Runs the check script with `python` in `repo` and returns its parsed report.
pub async fn run_check_script(
    python: &str,
    repo: &Path,
    ping: bool,
    scholar: bool,
    timeout: Duration,
    secrets: &[String],
) -> Result<Value, String> {
    run_check_script_with(python, repo, &check_args(ping, scholar), timeout, secrets).await
}

/// [`run_check_script`] with explicit script arguments.
pub async fn run_check_script_with(
    python: &str,
    repo: &Path,
    args: &[&str],
    timeout: Duration,
    secrets: &[String],
) -> Result<Value, String> {
    let out = run_repo_script(
        python,
        repo,
        CHECK_SCRIPT,
        "连接检测脚本",
        args,
        timeout,
        "连接检测",
        "请检查网络或代理设置后重试。",
    )
    .await?;
    parse_check_output(out.success, out.code, &out.stdout, &out.stderr, secrets)
}

/// What a repo script printed.
pub struct ScriptOutput {
    pub success: bool,
    pub code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
}

/// Runs `python <repo>/<script_rel> args...` in `repo` with a time limit.
/// `what` names the script and `task` the job in the Chinese error messages.
#[allow(clippy::too_many_arguments)]
pub async fn run_repo_script(
    python: &str,
    repo: &Path,
    script_rel: &str,
    what: &str,
    args: &[&str],
    timeout: Duration,
    task: &str,
    timeout_hint: &str,
) -> Result<ScriptOutput, String> {
    let python = python.trim();
    if python.is_empty() {
        return Err("尚未设置 Python 路径，请先在「本地环境」中填写并保存。".to_string());
    }
    if !repo.is_dir() {
        return Err(format!(
            "PaperOrchestra 目录不存在：{}。请在「本地环境」中检查目录设置。",
            repo.display()
        ));
    }
    let script = script_rel.split('/').fold(repo.to_path_buf(), |p, part| p.join(part));
    if !script.is_file() {
        return Err(format!(
            "未找到{what} {script_rel}（{}）。请确认「PaperOrchestra 目录」指向包含该脚本的最新版本。",
            repo.display()
        ));
    }

    let mut cmd = Command::new(python);
    cmd.arg(&script)
        .args(args)
        .current_dir(repo)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let path_env = enriched_path_env();
    if !path_env.is_empty() {
        cmd.env("PATH", path_env);
    }
    #[cfg(target_os = "windows")]
    {
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = cmd.spawn().map_err(|e| {
        format!("无法启动 Python（{python}）：{e}。请在「本地环境」中检查 Python 路径。")
    })?;
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
        let all = async {
            let (out, err, status) = tokio::join!(read_out, read_err, child.wait());
            (out, err, status)
        };
        tokio::time::timeout(timeout, all).await
    };

    let (out, err, status) = match finished {
        Ok(v) => v,
        Err(_) => {
            let _ = child.kill().await;
            return Err(format!(
                "{task}超时（超过 {} 秒），已终止进程。{timeout_hint}",
                timeout.as_secs()
            ));
        }
    };
    let status = status.map_err(|e| format!("等待{task}进程结束失败：{e}"))?;
    Ok(ScriptOutput {
        success: status.success(),
        code: status.code(),
        stdout: String::from_utf8_lossy(&out).into_owned(),
        stderr: String::from_utf8_lossy(&err).into_owned(),
    })
}

/// The intake parser's JSON line: the result, or its `error` as an Err.
pub fn parse_intake_output(stdout: &str, stderr: &str) -> Result<Value, String> {
    let parsed = stdout
        .lines()
        .rev()
        .map(str::trim)
        .filter(|l| l.starts_with('{'))
        .find_map(|l| serde_json::from_str::<Value>(l).ok());
    match parsed {
        Some(v) => match v.get("error").and_then(Value::as_str) {
            Some(err) => Err(err.to_string()),
            None if v.get("fields").map(Value::is_object).unwrap_or(false) => Ok(v),
            None => Err("填写单解析脚本的输出不完整。".to_string()),
        },
        None => {
            let detail: String = stderr.trim().chars().rev().take(MAX_DETAIL_CHARS).collect::<Vec<_>>().into_iter().rev().collect();
            Err(if detail.is_empty() {
                "填写单解析脚本没有输出结果。".to_string()
            } else {
                format!("填写单解析失败：{detail}")
            })
        }
    }
}

/// tools/submission_tools.py (arXiv pack, blind-review check, LaTeX
/// diagnostics, latexdiff, online citation check, lean-audit). Adapted from Lattice
/// (Apache-2.0); see paper-orchestra/THIRD_PARTY_NOTICES.md.
pub const SUBMISSION_SCRIPT: &str = "tools/submission_tools.py";
pub const FIGURE_STUDIO_SCRIPT: &str = "tools/figure_studio.py";
pub const RESEARCH_STUDIO_SCRIPT: &str = "tools/research_studio.py";
pub const LIBRARY_TOOLS_SCRIPT: &str = "tools/library_tools.py";
pub const LATEX_COPILOT_SCRIPT: &str = "tools/latex_copilot.py";
pub const DOC_EXPORTER_SCRIPT: &str = "tools/doc_exporter.py";

/// Subcommands the app may run.
pub const SUBMISSION_COMMANDS: &[&str] = &[
    "arxiv",
    "blind",
    "diagnose",
    "versions",
    "diff",
    "cite-online",
    "manuscript-audit",
    "lean-audit",
];
/// Flags the app may pass; every other argument must be an absolute path.
const SUBMISSION_FLAGS: &[&str] = &[
    "--out",
    "--keep-comments",
    "--merge-inputs",
    "--find-packages",
    "--only-changed-pages",
];

fn validate_flagged_tool_args(
    args: &[String],
    what: &str,
    cmds: &[&str],
    bool_flags: &[&str],
    path_flags: &[&str],
    value_flags: &[&str],
) -> Result<(), String> {
    let Some(cmd) = args.first() else {
        return Err(format!("缺少{what}的子命令。"));
    };
    if !cmds.contains(&cmd.as_str()) {
        return Err(format!("未知的{what}子命令：{cmd}"));
    }
    let mut i = 1;
    while i < args.len() {
        let flag = args[i].as_str();
        if bool_flags.contains(&flag) {
            i += 1;
        } else if path_flags.contains(&flag) {
            let val = args
                .get(i + 1)
                .ok_or_else(|| format!("{what}参数 {flag} 缺少路径值。"))?;
            if !val.trim().is_empty() && !Path::new(val).is_absolute() {
                return Err(format!("{what}路径 {flag} 必须是绝对路径：{val}"));
            }
            i += 2;
        } else if value_flags.contains(&flag) {
            if i + 1 >= args.len() {
                return Err(format!("{what}参数 {flag} 缺少值。"));
            }
            i += 2;
        } else {
            return Err(format!("不支持的{what}参数：{flag}"));
        }
    }
    Ok(())
}

pub fn validate_figure_studio_args(args: &[String]) -> Result<(), String> {
    validate_flagged_tool_args(
        args,
        "绘图工坊",
        &["list", "ensure-bundle", "revise", "restore", "create"],
        &[],
        &["--dir", "--bundle", "--base-dir", "--out-dir", "--image", "--code-file", "--data-file", "--ref-image"],
        &["--title", "--description", "--instruction", "--bbox", "--model", "--image-model", "--version", "--mode", "--prompt"],
    )
}

pub fn validate_research_studio_args(args: &[String]) -> Result<(), String> {
    validate_flagged_tool_args(
        args,
        "科研工坊",
        &["data-profile", "run-data-analysis", "run-sop", "run-skill", "fetch-skill-url"],
        &[],
        &["--file", "--out-dir", "--input-json", "--skill-file", "--files-json", "--prefs-file"],
        &["--question", "--model", "--sop", "--prompt", "--url"],
    )
}

pub fn validate_library_tool_args(args: &[String]) -> Result<(), String> {
    validate_flagged_tool_args(
        args,
        "文献库工具",
        &["search-online", "import-pdf", "import-doi", "build-graph", "export-bib"],
        &["--expand", "--no-llm"],
        &["--pdf", "--input-json"],
        &["--query", "--limit", "--model", "--doi", "--format"],
    )
}

pub fn validate_latex_copilot_args(args: &[String]) -> Result<(), String> {
    validate_flagged_tool_args(
        args,
        "LaTeX 助手",
        &["edit", "trace-citations"],
        &[],
        &["--file", "--run-dir", "--tex-file"],
        &["--instruction", "--selection-start", "--selection-end", "--error-log", "--model"],
    )
}

pub fn validate_doc_exporter_args(args: &[String]) -> Result<(), String> {
    validate_flagged_tool_args(
        args,
        "文档导出",
        &["google-drive-status", "export", "export-google-drive"],
        &[],
        &["--input-md", "--out", "--drive-dir"],
        &["--format", "--title"],
    )
}

/// Runs a repo Python CLI (`script_rel`) safely, masks secrets in error output,
/// and parses its final stdout JSON object.
pub async fn run_json_cli(
    python: &str,
    repo: &Path,
    script_rel: &str,
    what: &str,
    args: &[String],
    timeout: Duration,
    secrets: &[String],
) -> Result<Value, String> {
    let argv: Vec<&str> = args.iter().map(String::as_str).collect();
    let out = run_repo_script(
        python,
        repo,
        script_rel,
        what,
        &argv,
        timeout,
        what,
        "请检查模型密钥、网络连接或输入参数后重试。",
    )
    .await?;
    let mut val = parse_tool_output(&out.stdout, &out.stderr, what)?;
    mask_strings(&mut val, secrets);
    Ok(val)
}

pub const STUDIO_EVENT_PREFIX: &str = "@@PO_STUDIO_EVENT ";

pub struct ActiveStudioJobsState {
    pub jobs: Mutex<HashMap<String, oneshot::Sender<()>>>,
}

impl ActiveStudioJobsState {
    pub fn new() -> Self {
        Self {
            jobs: Mutex::new(HashMap::new()),
        }
    }

    pub async fn cancel(&self, job_id: &str) -> bool {
        let tx = {
            let mut guard = self.jobs.lock().await;
            guard.remove(job_id)
        };
        if let Some(tx) = tx {
            let _ = tx.send(());
            true
        } else {
            false
        }
    }
}

pub fn parse_studio_stderr_line(
    job_id: &str,
    line: &str,
    secrets: &[String],
) -> Option<StudioEventPayload> {
    let trimmed = line.trim_end_matches(&['\r', '\n'][..]);
    if let Some(raw_json) = trimmed.strip_prefix(STUDIO_EVENT_PREFIX) {
        if let Ok(mut val) = serde_json::from_str::<Value>(raw_json) {
            mask_strings(&mut val, secrets);
            let kind = val
                .get("kind")
                .and_then(Value::as_str)
                .unwrap_or("log")
                .to_string();
            let timestamp = val
                .get("timestamp")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| Utc::now().to_rfc3339());
            return Some(StudioEventPayload {
                job_id: job_id.to_string(),
                kind,
                stage: val.get("stage").and_then(Value::as_str).map(str::to_string),
                status: val.get("status").and_then(Value::as_str).map(str::to_string),
                message: val
                    .get("message")
                    .and_then(Value::as_str)
                    .map(|s| mask_secrets(s, secrets)),
                model: val.get("model").and_then(Value::as_str).map(str::to_string),
                detail: val
                    .get("detail")
                    .and_then(Value::as_str)
                    .map(|s| mask_secrets(s, secrets)),
                est_input_tokens: val.get("estInputTokens").and_then(Value::as_u64),
                out_dir: val.get("outDir").and_then(Value::as_str).map(str::to_string),
                usage: val.get("usage").cloned(),
                line: val
                    .get("line")
                    .and_then(Value::as_str)
                    .map(|s| mask_log_line(s, secrets)),
                level: val.get("level").and_then(Value::as_str).map(str::to_string),
                timestamp,
            });
        }
    }
    if trimmed.trim().is_empty() {
        return None;
    }
    let masked = mask_log_line(trimmed, secrets);
    let low = masked.to_lowercase();
    let level = if low.contains("traceback")
        || low.contains("error")
        || low.contains("exception")
        || low.contains("失败")
    {
        "error"
    } else if low.contains("warn") || low.contains("retry") || low.contains("重试") {
        "warn"
    } else {
        "info"
    };
    Some(StudioEventPayload {
        job_id: job_id.to_string(),
        kind: "log".to_string(),
        stage: None,
        status: None,
        message: None,
        model: None,
        detail: None,
        est_input_tokens: None,
        out_dir: None,
        usage: None,
        line: Some(masked),
        level: Some(level.to_string()),
        timestamp: Utc::now().to_rfc3339(),
    })
}

fn emit_studio_event(app: &AppHandle, payload: StudioEventPayload) {
    let _ = app.emit("studio-event", payload);
}

#[allow(clippy::too_many_arguments)]
pub async fn run_streaming_json_cli(
    app: &AppHandle,
    jobs: &ActiveStudioJobsState,
    job_id: &str,
    out_dir_hint: Option<&str>,
    python: &str,
    repo: &Path,
    script_rel: &str,
    what: &str,
    args: &[String],
    timeout: Duration,
    secrets: &[String],
) -> Result<Value, String> {
    let python = python.trim();
    if python.is_empty() {
        return Err("尚未设置 Python 路径，请先在「本地环境」中填写并保存。".to_string());
    }
    if !repo.is_dir() {
        return Err(format!(
            "PaperOrchestra 目录不存在：{}。请在「本地环境」中检查目录设置。",
            repo.display()
        ));
    }
    let script = script_rel
        .split('/')
        .fold(repo.to_path_buf(), |p, part| p.join(part));
    if !script.is_file() {
        return Err(format!(
            "未找到{what} {script_rel}（{}）。请确认「PaperOrchestra 目录」指向包含该脚本的最新版本。",
            repo.display()
        ));
    }

    let mut cmd = Command::new(python);
    cmd.arg(&script)
        .args(args)
        .current_dir(repo)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let path_env = enriched_path_env();
    if !path_env.is_empty() {
        cmd.env("PATH", path_env);
    }
    #[cfg(target_os = "windows")]
    {
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = cmd.spawn().map_err(|e| {
        format!("无法启动 Python（{python}）：{e}。请在「本地环境」中检查 Python 路径。")
    })?;
    let mut stdout = child.stdout.take();
    let stderr = child.stderr.take();

    let (cancel_tx, cancel_rx) = oneshot::channel::<()>();
    {
        let mut guard = jobs.jobs.lock().await;
        guard.insert(job_id.to_string(), cancel_tx);
    }

    emit_studio_event(
        app,
        StudioEventPayload {
            job_id: job_id.to_string(),
            kind: "started".to_string(),
            stage: Some("init".to_string()),
            status: Some("running".to_string()),
            message: Some(format!("已启动{what}…")),
            out_dir: out_dir_hint.map(str::to_string),
            timestamp: Utc::now().to_rfc3339(),
            ..Default::default()
        },
    );

    let read_out = async {
        let mut buf = Vec::new();
        if let Some(s) = stdout.as_mut() {
            let _ = s.read_to_end(&mut buf).await;
        }
        String::from_utf8_lossy(&buf).into_owned()
    };

    let read_err = async {
        let mut acc = String::new();
        if let Some(err_pipe) = stderr {
            let mut reader = BufReader::new(err_pipe).lines();
            while let Ok(Some(line)) = reader.next_line().await {
                if let Some(ev) = parse_studio_stderr_line(job_id, &line, secrets) {
                    emit_studio_event(app, ev);
                }
                acc.push_str(&line);
                acc.push('\n');
            }
        }
        acc
    };

    let run_fut = async {
        let (out_str, err_str, wait_res) = tokio::join!(read_out, read_err, child.wait());
        (out_str, err_str, wait_res)
    };

    enum Outcome {
        Cancelled,
        TimedOut,
        Finished(String, String, Result<std::process::ExitStatus, std::io::Error>),
    }

    let outcome = tokio::select! {
        biased;
        _ = cancel_rx => {
            let _ = child.kill().await;
            let _ = child.wait().await;
            Outcome::Cancelled
        }
        res = tokio::time::timeout(timeout, run_fut) => {
            match res {
                Ok((out_str, err_str, wait_res)) => Outcome::Finished(out_str, err_str, wait_res),
                Err(_) => {
                    let _ = child.kill().await;
                    let _ = child.wait().await;
                    Outcome::TimedOut
                }
            }
        }
    };

    {
        let mut guard = jobs.jobs.lock().await;
        guard.remove(job_id);
    }

    match outcome {
        Outcome::Cancelled => {
            let msg = "已手动停止该工坊任务。".to_string();
            emit_studio_event(
                app,
                StudioEventPayload {
                    job_id: job_id.to_string(),
                    kind: "cancelled".to_string(),
                    status: Some("cancelled".to_string()),
                    message: Some(msg.clone()),
                    timestamp: Utc::now().to_rfc3339(),
                    ..Default::default()
                },
            );
            Err(msg)
        }
        Outcome::TimedOut => {
            let msg = format!(
                "{what}超时（超过 {} 秒），已终止进程。请检查模型密钥、网络连接或输入参数后重试。",
                timeout.as_secs()
            );
            emit_studio_event(
                app,
                StudioEventPayload {
                    job_id: job_id.to_string(),
                    kind: "error".to_string(),
                    status: Some("error".to_string()),
                    message: Some(msg.clone()),
                    timestamp: Utc::now().to_rfc3339(),
                    ..Default::default()
                },
            );
            Err(msg)
        }
        Outcome::Finished(out_str, err_str, wait_res) => {
            let _status = wait_res.map_err(|e| format!("等待{what}进程结束失败：{e}"))?;
            match parse_tool_output(&out_str, &err_str, what) {
                Ok(mut val) => {
                    mask_strings(&mut val, secrets);
                    emit_studio_event(
                        app,
                        StudioEventPayload {
                            job_id: job_id.to_string(),
                            kind: "done".to_string(),
                            status: Some("completed".to_string()),
                            message: Some(format!("{what}执行完成")),
                            timestamp: Utc::now().to_rfc3339(),
                            ..Default::default()
                        },
                    );
                    Ok(val)
                }
                Err(e) => {
                    let masked = mask_secrets(&e, secrets);
                    emit_studio_event(
                        app,
                        StudioEventPayload {
                            job_id: job_id.to_string(),
                            kind: "error".to_string(),
                            status: Some("error".to_string()),
                            message: Some(masked.clone()),
                            timestamp: Utc::now().to_rfc3339(),
                            ..Default::default()
                        },
                    );
                    Err(masked)
                }
            }
        }
    }
}

/// Checks a submission-tool call: a known subcommand, known flags, and
/// absolute paths for everything else (no option injection through a path).
pub fn validate_submission_args(args: &[String]) -> Result<(), String> {
    let Some(cmd) = args.first() else {
        return Err("缺少投稿工具的子命令。".to_string());
    };
    if !SUBMISSION_COMMANDS.contains(&cmd.as_str()) {
        return Err(format!("未知的投稿工具：{cmd}"));
    }
    for a in &args[1..] {
        if a.starts_with('-') {
            if !SUBMISSION_FLAGS.contains(&a.as_str()) {
                return Err(format!("不支持的参数：{a}"));
            }
        } else if !Path::new(a).is_absolute() {
            return Err(format!("路径必须是绝对路径：{a}"));
        }
    }
    Ok(())
}

/// The submission tool's last JSON line, or its `error` as an Err.
pub fn parse_tool_output(stdout: &str, stderr: &str, what: &str) -> Result<Value, String> {
    let parsed = stdout
        .lines()
        .rev()
        .map(str::trim)
        .filter(|l| l.starts_with('{'))
        .find_map(|l| serde_json::from_str::<Value>(l).ok());
    match parsed {
        Some(v) => match v.get("error").and_then(Value::as_str) {
            Some(err) => Err(err.to_string()),
            None => Ok(v),
        },
        None => {
            let detail = tail_excerpt(stderr);
            Err(if detail.is_empty() {
                format!("{what}没有输出结果。")
            } else {
                format!("{what}失败：{detail}")
            })
        }
    }
}

/// Turns the script's output into the report, or a short Chinese error.
///
/// A JSON report on stdout wins even when the exit code is not 0; stray lines
/// printed before it (library warnings) are skipped.
pub fn parse_check_output(
    success: bool,
    code: Option<i32>,
    stdout: &str,
    stderr: &str,
    secrets: &[String],
) -> Result<Value, String> {
    if let Some(mut map) = extract_json_object(stdout) {
        for key in ["providers", "scholar"] {
            if !map.get(key).is_some_and(Value::is_array) {
                map.insert(key.to_string(), Value::Array(Vec::new()));
            }
        }
        let mut report = Value::Object(map);
        mask_strings(&mut report, secrets);
        return Ok(report);
    }

    let source = if stderr.trim().is_empty() {
        stdout
    } else {
        stderr
    };
    let detail = mask_secrets(&tail_excerpt(source), secrets);
    if !success {
        let code = code
            .map(|c| c.to_string())
            .unwrap_or_else(|| "未知".to_string());
        if detail.is_empty() {
            Err(format!("连接检测脚本运行失败（退出码 {code}），没有输出。"))
        } else {
            Err(format!("连接检测脚本运行失败（退出码 {code}）：{detail}"))
        }
    } else if stdout.trim().is_empty() {
        Err("连接检测脚本没有输出结果。".to_string())
    } else {
        let snippet = mask_secrets(&tail_excerpt(stdout), secrets);
        Err(format!("连接检测脚本的输出不是有效的 JSON：{snippet}"))
    }
}

/// The report object: the whole stdout, or the last line starting with `{`
/// through the end (a pretty-printed report after some log lines).
fn extract_json_object(stdout: &str) -> Option<Map<String, Value>> {
    let as_object = |text: &str| match serde_json::from_str::<Value>(text.trim()) {
        Ok(Value::Object(map)) => Some(map),
        _ => None,
    };
    let text = stdout.trim_start_matches('\u{feff}');
    if let Some(map) = as_object(text) {
        return Some(map);
    }
    let mut starts: Vec<usize> = Vec::new();
    let mut offset = 0;
    for line in text.split_inclusive('\n') {
        if line.trim_start().starts_with('{') {
            starts.push(offset);
        }
        offset += line.len();
    }
    starts.into_iter().rev().find_map(|start| {
        let rest = &text[start..];
        as_object(rest).or_else(|| as_object(rest.lines().next().unwrap_or("")))
    })
}

/// Last few non-empty lines, at most [`MAX_DETAIL_CHARS`] characters.
fn tail_excerpt(text: &str) -> String {
    let lines: Vec<&str> = text
        .lines()
        .map(str::trim_end)
        .filter(|l| !l.trim().is_empty())
        .collect();
    let start = lines.len().saturating_sub(6);
    let joined = lines[start..].join("\n");
    let count = joined.chars().count();
    if count <= MAX_DETAIL_CHARS {
        joined
    } else {
        let skip = count - MAX_DETAIL_CHARS;
        format!("…{}", joined.chars().skip(skip).collect::<String>())
    }
}

/// Masks the free-text fields (`error`, `message`, `detail`) anywhere in the
/// report; names, labels and model ids are left as they are.
fn mask_strings(v: &mut Value, secrets: &[String]) {
    match v {
        Value::Array(items) => items.iter_mut().for_each(|i| mask_strings(i, secrets)),
        Value::Object(map) => {
            for (key, val) in map.iter_mut() {
                match val {
                    Value::String(s) if matches!(key.as_str(), "error" | "message" | "detail") => {
                        *s = mask_secrets(s, secrets);
                    }
                    _ => mask_strings(val, secrets),
                }
            }
        }
        _ => {}
    }
}

/// `sk-abcdef…` -> `sk-a…(已隐藏)`: at most 4 leading characters, and never
/// more than a quarter of the secret (an 8-character secret keeps 2).
fn mask_one(secret: &str) -> String {
    let keep = (secret.chars().count() / 4).min(4);
    let head: String = secret.chars().take(keep).collect();
    format!("{head}…(已隐藏)")
}

const KEY_PREFIXES: &[&str] = &[
    "sk-", "sk_", "pk-", "rk-", "AIza", "ghp_", "gho_", "xoxb-", "xoxp-", "hf_", "key-",
];
/// Generic prefixes that also start ordinary words (`key-frames-…`): they count
/// in [`mask_secrets`], but not in run-log lines, where paths must stay readable.
const GENERIC_PREFIXES: &[&str] = &["pk-", "rk-", "key-"];

/// True for a word that looks like an API key / token. With `prefixed_only`,
/// only words that start with a known key prefix count (no length/character
/// heuristic), which keeps run-folder names in log paths readable.
fn looks_like_key(word: &str, prefixed_only: bool) -> bool {
    let len = word.chars().count();
    if len < 20
        || !word
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
    {
        return false;
    }
    if KEY_PREFIXES
        .iter()
        .filter(|p| !(prefixed_only && GENERIC_PREFIXES.contains(p)))
        .any(|p| word.starts_with(p))
    {
        return true;
    }
    if prefixed_only {
        return false;
    }
    let digits = word.chars().filter(char::is_ascii_digit).count();
    let letters = word.chars().filter(char::is_ascii_alphabetic).count();
    len >= 32 && digits >= 4 && letters >= 4 && !word.contains('.')
}

/// Replaces known secret values and key-shaped words.
pub fn mask_secrets(text: &str, secrets: &[String]) -> String {
    mask_text(text, secrets, false)
}

/// Masking for pipeline run-log lines: known secret values (from .env /
/// providers.json) and words with a known key prefix (`sk-…`, `AIza…`).
/// Unlike [`mask_secrets`] it never touches other long words, so paths with
/// run-folder names (`<slug>_<stamp>_<id8>`) stay intact.
pub fn mask_log_line(text: &str, secrets: &[String]) -> String {
    mask_text(text, secrets, true)
}

fn mask_text(text: &str, secrets: &[String], prefixed_only: bool) -> String {
    let mut out = text.to_string();
    let mut known: Vec<&String> = secrets
        .iter()
        .filter(|s| s.trim().chars().count() >= 8)
        .collect();
    known.sort_by_key(|s| std::cmp::Reverse(s.len()));
    for secret in known {
        let secret = secret.trim();
        if out.contains(secret) {
            out = out.replace(secret, &mask_one(secret));
        }
    }
    let mut result = String::with_capacity(out.len());
    let mut word = String::new();
    let flush = |word: &mut String, result: &mut String| {
        if looks_like_key(word, prefixed_only) {
            result.push_str(&mask_one(word));
        } else {
            result.push_str(word);
        }
        word.clear();
    };
    for c in out.chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.') {
            word.push(c);
        } else {
            flush(&mut word, &mut result);
            result.push(c);
        }
    }
    flush(&mut word, &mut result);
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn submission_args_are_allowlisted() {
        let ok = |v: &[&str]| validate_submission_args(&v.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        // An absolute path on this platform (`/runs/a` has no drive on Windows).
        let (run, out) = if cfg!(windows) { (r"C:\runs\a", r"C:\tmp\x.zip") } else { ("/runs/a", "/tmp/x.zip") };
        assert!(ok(&["blind", run]).is_ok());
        assert!(ok(&["manuscript-audit", run]).is_ok());
        assert!(ok(&["lean-audit", run]).is_ok());
        assert!(ok(&["arxiv", run, "--out", out, "--merge-inputs"]).is_ok());
        assert!(ok(&["rm", "/x"]).is_err());
        assert!(ok(&["blind", "--evil"]).is_err());
        assert!(ok(&["blind", "relative/path"]).is_err());
        assert!(ok(&[]).is_err());

        let str_vec = |xs: &[&str]| xs.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert!(validate_figure_studio_args(&str_vec(&["revise", "--bundle", run, "--instruction", "move legend", "--bbox", "0.1,0.2,0.3,0.4"])).is_ok());
        assert!(validate_figure_studio_args(&str_vec(&["revise", "--bundle", "rel/path", "--instruction", "x"])).is_err());
        assert!(validate_research_studio_args(&str_vec(&["data-profile", "--file", run])).is_ok());
        assert!(validate_library_tool_args(&str_vec(&["search-online", "--query", "attention", "--expand"])).is_ok());
        assert!(validate_latex_copilot_args(&str_vec(&["trace-citations", "--run-dir", run])).is_ok());
    }

    #[test]
    fn tool_output_returns_report_or_error() {
        let v = parse_tool_output("warning\n{\"kind\":\"blind\"}\n", "", "x").unwrap();
        assert_eq!(v["kind"], "blind");
        assert_eq!(parse_tool_output("{\"error\":\"坏了\"}", "", "x").unwrap_err(), "坏了");
        assert!(parse_tool_output("", "Traceback boom", "投稿工具").unwrap_err().starts_with("投稿工具失败"));
    }

    #[test]
    fn intake_output_result_and_errors() {
        let ok = parse_intake_output("warning line\n{\"fields\": {\"title\": \"T\"}, \"warnings\": []}\n", "");
        assert_eq!(ok.unwrap()["fields"]["title"], "T");
        assert_eq!(parse_intake_output("{\"error\": \"没认出\"}", "").unwrap_err(), "没认出");
        assert!(parse_intake_output("", "Traceback: boom").unwrap_err().contains("boom"));
        assert!(parse_intake_output("{\"x\": 1}", "").is_err());
    }
    use serde_json::json;

    #[test]
    fn parses_a_clean_report() {
        let out = r#"{"providers":[{"name":"deepseek","label":"DeepSeek","kind":"openai","configured":true,"model":"deepseek-chat","ok":true,"latency_ms":321,"error":null}],"scholar":[]}"#;
        let v = parse_check_output(true, Some(0), out, "", &[]).unwrap();
        assert_eq!(v["providers"][0]["name"], "deepseek");
        assert_eq!(v["providers"][0]["latency_ms"], 321);
        assert_eq!(v["scholar"], json!([]));
    }

    #[test]
    fn skips_log_lines_and_accepts_pretty_json() {
        let out = "Loading .env\nWARNING: something\n{\n  \"providers\": [],\n  \"scholar\": [{\"name\": \"openalex\", \"ok\": true, \"latency_ms\": 80, \"hits\": 3, \"error\": null}]\n}\n";
        let v = parse_check_output(true, Some(0), out, "", &[]).unwrap();
        assert_eq!(v["scholar"][0]["hits"], 3);

        let compact_after_noise = "noise {\nnot json\n{\"providers\": []}\n";
        let v = parse_check_output(true, Some(0), compact_after_noise, "", &[]).unwrap();
        assert_eq!(v["providers"], json!([]));
        assert_eq!(v["scholar"], json!([]), "missing arrays are filled in");
    }

    #[test]
    fn report_wins_over_a_non_zero_exit_code() {
        let v = parse_check_output(false, Some(1), r#"{"providers":[]}"#, "Traceback ...", &[])
            .unwrap();
        assert!(v["providers"].is_array());
    }

    #[test]
    fn errors_are_chinese_and_short() {
        let err = parse_check_output(
            false,
            Some(2),
            "",
            "Traceback (most recent call last):\n  File \"x\"\nModuleNotFoundError: No module named 'openai'\n",
            &[],
        )
        .unwrap_err();
        assert!(err.contains("运行失败（退出码 2）"), "{err}");
        assert!(err.contains("ModuleNotFoundError"), "{err}");

        let err = parse_check_output(false, None, "", "", &[]).unwrap_err();
        assert!(err.contains("没有输出"), "{err}");

        let err = parse_check_output(true, Some(0), "hello world", "", &[]).unwrap_err();
        assert!(err.contains("不是有效的 JSON"), "{err}");

        let err = parse_check_output(true, Some(0), "[1, 2]", "", &[]).unwrap_err();
        assert!(err.contains("不是有效的 JSON"), "{err}");

        let long = "x".repeat(5000);
        let err = parse_check_output(false, Some(1), "", &long, &[]).unwrap_err();
        assert!(err.chars().count() < MAX_DETAIL_CHARS + 80, "{}", err.len());
    }

    #[test]
    fn secrets_are_masked_in_reports_and_errors() {
        let secret = "my-very-private-value-1234".to_string();
        let secrets = vec![secret.clone()];
        let out = format!(
            r#"{{"providers":[{{"name":"openai","ok":false,"error":"401 for key {secret} / sk-proj-abcdefghijklmnopqrstuv"}}],"scholar":[]}}"#
        );
        let v = parse_check_output(true, Some(0), &out, "", &secrets).unwrap();
        assert_eq!(v["providers"][0]["name"], "openai");
        let e = v["providers"][0]["error"].as_str().unwrap();
        assert!(!e.contains(&secret), "{e}");
        assert!(!e.contains("abcdefghijklmnopqrstuv"), "{e}");
        assert!(e.contains("已隐藏"), "{e}");

        let stderr = format!("bad key AIzaSyD-0123456789abcdefghij {secret}");
        let err = parse_check_output(false, Some(1), "", &stderr, &secrets).unwrap_err();
        assert!(
            !err.contains(&secret) && !err.contains("0123456789abcdefghij"),
            "{err}"
        );

        // ordinary words, paths, model names and URLs survive
        let plain = "HTTP 404 at https://api.deepseek.com/v1/chat for model deepseek-chat in /home/u/paper-orchestra";
        assert_eq!(mask_secrets(plain, &[]), plain);
    }

    #[test]
    fn run_log_lines_mask_keys_but_keep_run_folder_paths() {
        let secret = "my-very-private-value-1234".to_string();
        let secrets = vec![secret.clone()];
        // what openai_utils prints when a gateway echoes the key back
        let line = format!(
            "Warning: openai API call (model=gpt-4.1) failed on attempt 1 of 5. Error: Error code: 401 - \
             Incorrect API key provided: sk-proj-abcdefghijklmnopqrstuv / {secret}"
        );
        let masked = mask_log_line(&line, &secrets);
        assert!(!masked.contains(&secret), "{masked}");
        assert!(!masked.contains("abcdefghijklmnopqrstuv"), "{masked}");
        assert!(
            masked.starts_with("Warning: openai API call (model=gpt-4.1)"),
            "{masked}"
        );

        // run folders (<slug>_<stamp>_<id8>) and stage markers are left alone
        let path = " /home/u/out/my_paper_title_20261118_013524_ed9e9f05/final_paper.pdf";
        assert_eq!(mask_log_line(path, &secrets), path);
        let stage = "Agent SectionWritingAgent starting (content_refinement_workdir/final_refined_paper.tex)";
        assert_eq!(mask_log_line(stage, &secrets), stage);
        assert_eq!(mask_log_line("", &secrets), "");
        // generic prefixes (key-, pk-, rk-) do not mask ordinary path segments in log lines
        let generic = " /data/key-frames-experiments-2026/out and /srv/pk-dataset-archive-2026/x";
        assert_eq!(mask_log_line(generic, &secrets), generic);
        assert!(!mask_secrets("key-abcdefghij0123456789", &[]).contains("abcdefghij0123456789"));
    }

    #[test]
    fn short_secrets_keep_at_most_a_quarter() {
        assert_eq!(mask_one("abcdefgh"), "ab…(已隐藏)");
        assert_eq!(mask_one("sk-abcdefghijklmnop"), "sk-a…(已隐藏)");
        let out = mask_secrets("value 12345678 here", &["12345678".to_string()]);
        assert_eq!(out, "value 12…(已隐藏) here");
    }

    #[test]
    fn list_models_args_ask_for_json_and_models_only() {
        assert_eq!(list_models_args(), vec!["--json", "--list-models"]);
    }

    #[test]
    fn model_lists_pass_through_with_errors_masked() {
        let secret = "AIzaSyTESTSECRETVALUE0123456789";
        let out = format!(
            r#"{{"providers":[],"scholar":[],"models":[{{"name":"gemini","ok":true,"error":null,"models":["gemini-3.8-flash"]}},{{"name":"bad","ok":false,"error":"401 for key {secret}","models":[]}}]}}"#
        );
        let v = parse_check_output(true, Some(0), &out, "", &[secret.to_string()]).unwrap();
        assert_eq!(v["models"][0]["models"][0], "gemini-3.8-flash");
        let e = v["models"][1]["error"].as_str().unwrap();
        assert!(!e.contains(secret), "{e}");
    }

    #[test]
    fn ping_model_args_keep_only_sane_entries() {
        let mut m = BTreeMap::new();
        m.insert("gemini".to_string(), " gemini-3.8-flash ".to_string());
        m.insert("qwen".to_string(), "".to_string());
        m.insert("bad=name".to_string(), "x".to_string());
        m.insert("硅基流动".to_string(), "Qwen/Qwen3-8B".to_string());
        m.insert("openrouter".to_string(), "anthropic/claude-sonnet-5.5".to_string());
        m.insert("evil".to_string(), "--timeout".to_string());
        m.insert("spaces".to_string(), "a b".to_string());
        assert_eq!(
            ping_model_args(&m),
            vec![
                "--ping-model=gemini=gemini-3.8-flash".to_string(),
                "--ping-model=openrouter=anthropic/claude-sonnet-5.5".to_string(),
                "--ping-model=硅基流动=Qwen/Qwen3-8B".to_string(),
            ]
        );
    }

    #[test]
    fn args_follow_the_flags() {
        assert_eq!(check_args(false, false), vec!["--json"]);
        assert_eq!(check_args(true, false), vec!["--json", "--ping"]);
        assert_eq!(
            check_args(true, true),
            vec!["--json", "--ping", "--scholar"]
        );
    }

    #[tokio::test]
    async fn missing_script_and_python_give_clear_errors() {
        let repo = std::env::temp_dir().join(format!("po_diag_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&repo).unwrap();
        let err = run_check_script("python3", &repo, false, false, CHECK_TIMEOUT, &[])
            .await
            .unwrap_err();
        assert!(err.contains("未找到连接检测脚本"), "{err}");

        std::fs::create_dir_all(repo.join("tools")).unwrap();
        std::fs::write(repo.join("tools").join("check_providers.py"), "print('{}')").unwrap();
        let err = run_check_script(
            "/definitely/not/a/python",
            &repo,
            false,
            false,
            CHECK_TIMEOUT,
            &[],
        )
        .await
        .unwrap_err();
        assert!(err.contains("无法启动 Python"), "{err}");

        let err = run_check_script("", &repo, false, false, CHECK_TIMEOUT, &[])
            .await
            .unwrap_err();
        assert!(err.contains("Python 路径"), "{err}");
        let err = run_check_script(
            "python3",
            &repo.join("nope"),
            false,
            false,
            CHECK_TIMEOUT,
            &[],
        )
        .await
        .unwrap_err();
        assert!(err.contains("目录不存在"), "{err}");
        let _ = std::fs::remove_dir_all(&repo);
    }

    /// A fake "python" (a shell script) stands in for the interpreter, so the
    /// test checks argv, cwd and env without needing Python or a network.
    #[cfg(unix)]
    fn fake_python(repo: &Path, body: &str) -> String {
        use std::os::unix::fs::PermissionsExt;
        std::fs::create_dir_all(repo.join("tools")).unwrap();
        std::fs::write(
            repo.join("tools").join("check_providers.py"),
            "# placeholder\n",
        )
        .unwrap();
        let exe = repo.join("fake-python");
        std::fs::write(&exe, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).unwrap();
        exe.to_string_lossy().to_string()
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn runs_the_script_with_flags_cwd_and_utf8() {
        let repo = std::env::temp_dir().join(format!("po_diag_run_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&repo).unwrap();
        let repo = repo.canonicalize().unwrap();
        let py = fake_python(
            &repo,
            r#"echo "starting checks"
printf '{"providers":[],"scholar":[],"argv":"%s","cwd":"%s","utf8":"%s"}\n' "$*" "$(pwd -P)" "$PYTHONUTF8""#,
        );
        let v = run_check_script(&py, &repo, true, true, CHECK_TIMEOUT, &[])
            .await
            .unwrap();
        let argv = v["argv"].as_str().unwrap();
        assert!(
            argv.ends_with("check_providers.py --json --ping --scholar"),
            "{argv}"
        );
        assert_eq!(v["cwd"].as_str().unwrap(), repo.to_string_lossy());
        assert_eq!(v["utf8"], "1");

        let v = run_check_script(&py, &repo, false, false, CHECK_TIMEOUT, &[])
            .await
            .unwrap();
        assert!(v["argv"]
            .as_str()
            .unwrap()
            .ends_with("check_providers.py --json"));

        let v = run_check_script_with(&py, &repo, &list_models_args(), CHECK_TIMEOUT, &[])
            .await
            .unwrap();
        assert!(v["argv"]
            .as_str()
            .unwrap()
            .ends_with("check_providers.py --json --list-models"));
        let _ = std::fs::remove_dir_all(&repo);
    }

    /// Smoke test against the real script (no network needed with a local
    /// fake endpoint in the repo's .env). Run with:
    /// `PO_CHECK_REPO=<repo> PO_CHECK_PYTHON=<python> cargo test real_script -- --ignored --nocapture`
    #[tokio::test]
    #[ignore]
    async fn real_script_smoke() {
        let repo = std::env::var("PO_CHECK_REPO").expect("PO_CHECK_REPO");
        let python = std::env::var("PO_CHECK_PYTHON").expect("PO_CHECK_PYTHON");
        let scholar = std::env::var("PO_CHECK_SCHOLAR").is_ok();
        let secrets = crate::env_config::secret_values(&repo);
        let started = std::time::Instant::now();
        let report = run_check_script(
            &python,
            Path::new(&repo),
            true,
            scholar,
            CHECK_TIMEOUT,
            &secrets,
        )
        .await
        .unwrap();
        let text = serde_json::to_string_pretty(&report).unwrap();
        println!("{text}\n({:?})", started.elapsed());
        assert!(report["providers"]
            .as_array()
            .is_some_and(|p| !p.is_empty()));
        for secret in &secrets {
            assert!(
                !text.contains(secret.as_str()),
                "a secret value leaked into the report"
            );
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn times_out_and_kills_the_process() {
        let repo = std::env::temp_dir().join(format!("po_diag_slow_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&repo).unwrap();
        let marker = repo.join("finished");
        let py = fake_python(&repo, &format!("sleep 3\ntouch '{}'", marker.display()));
        let started = std::time::Instant::now();
        let err = run_check_script(&py, &repo, true, false, Duration::from_millis(300), &[])
            .await
            .unwrap_err();
        assert!(err.contains("超时"), "{err}");
        assert!(
            started.elapsed() < Duration::from_secs(2),
            "{:?}",
            started.elapsed()
        );
        // the killed script never reaches its last line
        tokio::time::sleep(Duration::from_millis(3500)).await;
        assert!(!marker.exists(), "process was not killed");
        let _ = std::fs::remove_dir_all(&repo);
    }

    #[test]
    fn parses_studio_progress_usage_and_masks_secrets() {
        let secret = "sk-secret-value-1234567890abcd";
        let secrets = vec![secret.to_string()];

        let prog_line = format!(
            "{STUDIO_EVENT_PREFIX}{{\"kind\":\"progress\",\"stage\":\"codegen\",\"status\":\"running\",\"message\":\"Using key {secret}\",\"model\":\"gpt-4o\",\"estInputTokens\":1234}}"
        );
        let ev = parse_studio_stderr_line("job1", &prog_line, &secrets).unwrap();
        assert_eq!(ev.job_id, "job1");
        assert_eq!(ev.kind, "progress");
        assert_eq!(ev.stage.as_deref(), Some("codegen"));
        assert_eq!(ev.model.as_deref(), Some("gpt-4o"));
        assert_eq!(ev.est_input_tokens, Some(1234));
        assert!(!ev.message.as_deref().unwrap().contains(secret));

        let usage_line = format!(
            "{STUDIO_EVENT_PREFIX}{{\"kind\":\"usage\",\"usage\":{{\"totals\":{{\"calls\":2,\"errors\":0,\"input_tokens\":500,\"output_tokens\":200}},\"by_model\":[]}}}}"
        );
        let uev = parse_studio_stderr_line("job1", &usage_line, &secrets).unwrap();
        assert_eq!(uev.kind, "usage");
        assert_eq!(uev.usage.as_ref().unwrap()["totals"]["calls"], 2);

        let raw_line = format!("Warning: leak {secret} on retry");
        let lev = parse_studio_stderr_line("job1", &raw_line, &secrets).unwrap();
        assert_eq!(lev.kind, "log");
        assert_eq!(lev.level.as_deref(), Some("warn"));
        assert!(!lev.line.as_deref().unwrap().contains(secret));
    }

    #[tokio::test]
    async fn studio_jobs_state_cancel_removes_and_signals() {
        let state = ActiveStudioJobsState::new();
        let (tx, rx) = oneshot::channel::<()>();
        {
            let mut g = state.jobs.lock().await;
            g.insert("job-xyz".to_string(), tx);
        }
        assert!(state.cancel("job-xyz").await);
        assert!(rx.await.is_ok());
        assert!(!state.cancel("job-xyz").await);
    }

    #[test]
    fn doc_exporter_args_are_allowlisted() {
        assert!(validate_doc_exporter_args(&["google-drive-status".into()]).is_ok());
        assert!(validate_doc_exporter_args(&[
            "export".into(),
            "--input-md".into(),
            "/abs/in.md".into(),
            "--format".into(),
            "docx".into(),
            "--out".into(),
            "/abs/out.docx".into(),
            "--title".into(),
            "报告".into(),
        ])
        .is_ok());
        assert!(validate_doc_exporter_args(&[
            "export".into(),
            "--input-md".into(),
            "relative.md".into(),
        ])
        .is_err());
        assert!(validate_doc_exporter_args(&["unknown".into()]).is_err());
    }
}
