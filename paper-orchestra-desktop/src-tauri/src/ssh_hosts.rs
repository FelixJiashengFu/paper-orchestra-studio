use std::fs;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use tokio::io::AsyncReadExt;
use tokio::process::Command;

use crate::diagnostics::mask_secrets;
use crate::models::{SshExecResult, SshGpuInfo, SshHost, SshTestResult};

const SSH_TEST_TIMEOUT: Duration = Duration::from_secs(20);
const SSH_EXEC_TIMEOUT: Duration = Duration::from_secs(120);

pub fn validate_host_fields(alias: &str, host: &str, port: i64, username: &str) -> Result<(), String> {
    let bad_chars = |s: &str| s.chars().any(|c| c.is_control() || c.is_whitespace() || c == ';');
    if alias.trim().is_empty() {
        return Err("请填写主机别名。".to_string());
    }
    let h = host.trim();
    if h.is_empty() || bad_chars(h) || h.starts_with('-') {
        return Err("主机地址格式无效（不能包含空格或控制字符）。".to_string());
    }
    if !(1..=65535).contains(&port) {
        return Err("SSH 端口必须在 1 到 65535 之间。".to_string());
    }
    let u = username.trim();
    if u.is_empty() || bad_chars(u) || u.starts_with('-') {
        return Err("用户名格式无效。".to_string());
    }
    Ok(())
}

pub fn save_ssh_private_key(app_data_dir: &Path, host_id: &str, pem_content: &str) -> Result<PathBuf, String> {
    let cleaned = pem_content.trim();
    if cleaned.is_empty() {
        return Err("私钥内容不能为空。".to_string());
    }
    let dir = app_data_dir.join("ssh_keys");
    fs::create_dir_all(&dir).map_err(|e| format!("创建 SSH 密钥目录失败：{e}"))?;
    let safe_id: String = host_id
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_'))
        .collect();
    if safe_id.is_empty() {
        return Err("无效的主机 ID。".to_string());
    }
    let path = dir.join(format!("{safe_id}.key"));
    let body = if cleaned.ends_with('\n') {
        cleaned.to_string()
    } else {
        format!("{cleaned}\n")
    };
    fs::write(&path, body.as_bytes()).map_err(|e| format!("保存私钥文件失败：{e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))
            .map_err(|e| format!("设置私钥文件权限 (0600) 失败：{e}"))?;
    }
    Ok(path)
}

fn build_ssh_base_args(host: &SshHost) -> Result<(Vec<String>, String), String> {
    validate_host_fields(&host.alias, &host.host, host.port, &host.username)?;
    let mut args = vec![
        "-p".to_string(),
        host.port.to_string(),
        "-o".to_string(),
        "BatchMode=yes".to_string(),
        "-o".to_string(),
        "ConnectTimeout=10".to_string(),
        "-o".to_string(),
        "StrictHostKeyChecking=accept-new".to_string(),
    ];
    let key = host.key_path.trim();
    if !key.is_empty() {
        if !Path::new(key).is_file() {
            return Err(format!("找不到 SSH 私钥文件：{key}"));
        }
        args.push("-i".to_string());
        args.push(key.to_string());
        args.push("-o".to_string());
        args.push("IdentitiesOnly=yes".to_string());
    }
    let target = format!("{}@{}", host.username.trim(), host.host.trim());
    Ok((args, target))
}

pub fn parse_nvidia_smi_csv(raw: &str) -> Vec<SshGpuInfo> {
    let mut out = Vec::new();
    for line in raw.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed == "NO_GPU" || trimmed.starts_with("NVIDIA-SMI has failed") {
            continue;
        }
        let parts: Vec<&str> = trimmed.split(',').map(str::trim).collect();
        if parts.len() < 4 {
            continue;
        }
        let num = |s: &str| -> f64 {
            s.split_whitespace()
                .next()
                .and_then(|tok| tok.trim_end_matches('%').parse::<f64>().ok())
                .unwrap_or(0.0)
        };
        out.push(SshGpuInfo {
            name: parts[0].to_string(),
            utilization_pct: num(parts[1]),
            mem_used_mb: num(parts[2]),
            mem_total_mb: num(parts[3]),
        });
    }
    out
}

fn shell_quote_single(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\"'\"'"))
}

async fn run_command_with_timeout(mut cmd: Command, timeout: Duration) -> Result<(bool, Option<i32>, String, String), String> {
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let mut child = cmd.spawn().map_err(|e| format!("无法启动 SSH/SCP 进程：{e}"))?;
    let mut stdout = child.stdout.take();
    let mut stderr = child.stderr.take();
    let all = async {
        let read_out = async {
            let mut b = Vec::new();
            if let Some(s) = stdout.as_mut() {
                let _ = s.read_to_end(&mut b).await;
            }
            b
        };
        let read_err = async {
            let mut b = Vec::new();
            if let Some(s) = stderr.as_mut() {
                let _ = s.read_to_end(&mut b).await;
            }
            b
        };
        tokio::join!(read_out, read_err, child.wait())
    };
    let (out_b, err_b, status_res) = tokio::time::timeout(timeout, all)
        .await
        .map_err(|_| format!("SSH 操作超时（超过 {} 秒）。", timeout.as_secs()))?;
    let status = status_res.map_err(|e| format!("等待 SSH 进程失败：{e}"))?;
    Ok((
        status.success(),
        status.code(),
        String::from_utf8_lossy(&out_b).into_owned(),
        String::from_utf8_lossy(&err_b).into_owned(),
    ))
}

pub async fn test_ssh_host(host: &SshHost, secrets: &[String]) -> SshTestResult {
    let (mut args, target) = match build_ssh_base_args(host) {
        Ok(v) => v,
        Err(e) => {
            return SshTestResult {
                ok: false,
                system_info: String::new(),
                gpus: Vec::new(),
                raw_output: String::new(),
                error: Some(e),
            }
        }
    };
    let probe = "uname -snrm && (nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null || echo NO_GPU)";
    args.push(target);
    args.push(probe.to_string());

    let mut cmd = Command::new("ssh");
    cmd.args(&args);
    match run_command_with_timeout(cmd, SSH_TEST_TIMEOUT).await {
        Ok((ok, _code, stdout, stderr)) => {
            let out_masked = mask_secrets(&stdout, secrets);
            let err_masked = mask_secrets(&stderr, secrets);
            if !ok {
                return SshTestResult {
                    ok: false,
                    system_info: String::new(),
                    gpus: Vec::new(),
                    raw_output: out_masked,
                    error: Some(if err_masked.trim().is_empty() {
                        "SSH 连接失败（请检查主机地址、端口与私钥权限）".to_string()
                    } else {
                        err_masked.trim().to_string()
                    }),
                };
            }
            let mut lines = out_masked.lines();
            let sys_info = lines.next().unwrap_or("").trim().to_string();
            let gpu_body: String = lines.collect::<Vec<_>>().join("\n");
            let gpus = parse_nvidia_smi_csv(&gpu_body);
            SshTestResult {
                ok: true,
                system_info: sys_info,
                gpus,
                raw_output: out_masked.trim().to_string(),
                error: None,
            }
        }
        Err(e) => SshTestResult {
            ok: false,
            system_info: String::new(),
            gpus: Vec::new(),
            raw_output: String::new(),
            error: Some(mask_secrets(&e, secrets)),
        },
    }
}

pub async fn exec_ssh_command(
    host: &SshHost,
    command: &str,
    work_dir: Option<&str>,
    secrets: &[String],
) -> Result<SshExecResult, String> {
    let trimmed = command.trim();
    if trimmed.is_empty() {
        return Err("请输入要在远程主机执行的命令。".to_string());
    }
    let (mut args, target) = build_ssh_base_args(host)?;
    let remote_cmd = match work_dir.map(str::trim).filter(|s| !s.is_empty() && *s != "~") {
        Some(dir) => format!("cd {} && {}", shell_quote_single(dir), trimmed),
        None => trimmed.to_string(),
    };
    args.push(target);
    args.push(remote_cmd);

    let t0 = Instant::now();
    let mut cmd = Command::new("ssh");
    cmd.args(&args);
    let (ok, code, stdout, stderr) = run_command_with_timeout(cmd, SSH_EXEC_TIMEOUT).await?;
    Ok(SshExecResult {
        ok,
        exit_code: code,
        stdout: mask_secrets(&stdout, secrets),
        stderr: mask_secrets(&stderr, secrets),
        duration_ms: t0.elapsed().as_millis() as u64,
    })
}

pub async fn scp_transfer(
    host: &SshHost,
    direction: &str,
    local_path: &str,
    remote_path: &str,
) -> Result<String, String> {
    validate_host_fields(&host.alias, &host.host, host.port, &host.username)?;
    let local = local_path.trim();
    let remote = remote_path.trim();
    if local.is_empty() || remote.is_empty() {
        return Err("本地路径与远程路径均不能为空。".to_string());
    }
    if remote.chars().any(char::is_control) {
        return Err("远程路径包含非法字符。".to_string());
    }
    let mut args = vec![
        "-r".to_string(),
        "-P".to_string(),
        host.port.to_string(),
        "-o".to_string(),
        "BatchMode=yes".to_string(),
        "-o".to_string(),
        "ConnectTimeout=15".to_string(),
        "-o".to_string(),
        "StrictHostKeyChecking=accept-new".to_string(),
    ];
    let key = host.key_path.trim();
    if !key.is_empty() {
        if !Path::new(key).is_file() {
            return Err(format!("找不到 SSH 私钥文件：{key}"));
        }
        args.push("-i".to_string());
        args.push(key.to_string());
        args.push("-o".to_string());
        args.push("IdentitiesOnly=yes".to_string());
    }
    let spec = format!("{}@{}:{}", host.username.trim(), host.host.trim(), remote);
    match direction {
        "upload" => {
            if !Path::new(local).exists() {
                return Err(format!("本地路径不存在：{local}"));
            }
            args.push(local.to_string());
            args.push(spec);
        }
        "download" => {
            args.push(spec);
            args.push(local.to_string());
        }
        _ => return Err(format!("不支持的传输方向：{direction}")),
    }

    let mut cmd = Command::new("scp");
    cmd.args(&args);
    let (ok, _code, _out, err) = run_command_with_timeout(cmd, SSH_EXEC_TIMEOUT).await?;
    if !ok {
        return Err(if err.trim().is_empty() {
            "SCP 文件传输失败。".to_string()
        } else {
            format!("SCP 传输失败：{}", err.trim())
        });
    }
    Ok(if direction == "download" {
        format!("已拉取到本地：{local}")
    } else {
        format!("已上传到远程：{remote}")
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_nvidia_smi_csv_rows() {
        let raw = "NVIDIA RTX 4090, 52 %, 12450 MiB, 24564 MiB\nNVIDIA RTX 4090, 0 %, 12 MiB, 24564 MiB\n";
        let gpus = parse_nvidia_smi_csv(raw);
        assert_eq!(gpus.len(), 2);
        assert_eq!(gpus[0].name, "NVIDIA RTX 4090");
        assert_eq!(gpus[0].utilization_pct, 52.0);
        assert_eq!(gpus[0].mem_used_mb, 12450.0);
        assert_eq!(gpus[0].mem_total_mb, 24564.0);
        assert!(parse_nvidia_smi_csv("NO_GPU\n").is_empty());
    }

    #[test]
    fn validates_ssh_host_fields() {
        assert!(validate_host_fields("555", "region-1.autodl.pro", 31245, "root").is_ok());
        assert!(validate_host_fields("bad", "host with space", 22, "root").is_err());
        assert!(validate_host_fields("bad", "example.com", 70000, "root").is_err());
        assert!(validate_host_fields("bad", "example.com", 22, "-evil").is_err());
    }
}
