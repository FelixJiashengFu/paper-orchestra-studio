use std::collections::{HashMap, VecDeque};
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::net::Ipv4Addr;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Deserialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Listener, Manager};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Semaphore;
use tokio::time::timeout;
use uuid::Uuid;

use crate::{codex_actions, commands};

const VERSION: u32 = 1;
const DISCOVERY_FILE: &str = "codex-bridge.json";
const MAX_FRAME: usize = 4 * 1024 * 1024;
const MAX_RESULT: usize = 1024 * 1024;
const MAX_JOBS: usize = 64;
const MAX_ACTIVE_JOBS: usize = 16;
const MAX_EVENTS: usize = 24;
const MAX_EVENT_SIZE: usize = 16 * 1024;
const RETAIN_RESULTS: Duration = Duration::from_secs(2 * 60 * 60);

#[derive(Clone)]
struct Bridge {
    app: AppHandle,
    token: Arc<str>,
    jobs: Arc<Mutex<HashMap<String, Job>>>,
}

struct Job {
    command: String,
    cancellable: bool,
    started: Instant,
    finished: Option<(Instant, Result<Value, String>)>,
    events: VecDeque<Value>,
    event_count: usize,
}

impl Job {
    fn status(&self, id: &str, include_result: bool) -> Value {
        let mut status = json!({
            "jobId": id,
            "action": self.command,
            "state": match &self.finished {
                None => "running",
                Some((_, Ok(_))) => "succeeded",
                Some((_, Err(_))) if self.events.iter().any(|event| event["kind"] == "cancelled") => "cancelled",
                Some((_, Err(_))) => "failed",
            },
            "canCancel": self.cancellable && self.finished.is_none(),
            "elapsedSeconds": self.finished.as_ref()
                .map_or_else(|| self.started.elapsed(), |(end, _)| *end - self.started)
                .as_secs(),
            "eventCount": self.event_count,
        });
        if include_result {
            status["events"] = json!(self.events);
            match &self.finished {
                Some((_, Ok(result))) => status["result"] = result.clone(),
                Some((_, Err(error))) => status["error"] = json!(error),
                None => {}
            }
        }
        status
    }
}

#[derive(Deserialize)]
struct Request {
    version: u32,
    token: String,
    #[serde(flatten)]
    action: Action,
}

#[derive(Deserialize)]
#[serde(tag = "method", rename_all = "snake_case", rename_all_fields = "camelCase")]
enum Action {
    Ping,
    Submit { request_id: Uuid, command: String, args: Value },
    Job { job_id: Uuid },
    ListJobs,
    Cancel { job_id: Uuid },
}

pub fn start(app: AppHandle, data_dir: &Path) -> Result<(), String> {
    let listener = std::net::TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
        .map_err(|error| error.to_string())?;
    listener.set_nonblocking(true).map_err(|error| error.to_string())?;
    let port = listener.local_addr().map_err(|error| error.to_string())?.port();
    let token = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
    let bridge = Bridge {
        app,
        token: Arc::from(token),
        jobs: Arc::new(Mutex::new(HashMap::new())),
    };
    publish_endpoint(data_dir, port, &bridge.token).map_err(|error| error.to_string())?;

    let job_events = bridge.jobs.clone();
    bridge.app.listen("studio-event", move |event| {
        let payload = event.payload();
        if payload.len() > MAX_EVENT_SIZE {
            return;
        }
        let Ok(value) = serde_json::from_str::<Value>(payload) else { return };
        if let Some(id) = value.get("jobId").and_then(Value::as_str) {
            if let Some(job) = job_events.lock().unwrap().get_mut(id) {
                job.event_count += 1;
                if job.events.len() == MAX_EVENTS {
                    job.events.pop_front();
                }
                job.events.push_back(value);
            }
        }
    });

    tauri::async_runtime::spawn(async move {
        let Ok(listener) = TcpListener::from_std(listener) else { return };
        let connections = Arc::new(Semaphore::new(32));
        while let Ok((stream, _)) = listener.accept().await {
            if let Ok(permit) = connections.clone().try_acquire_owned() {
                let bridge = bridge.clone();
                tokio::spawn(async move {
                    let _permit = permit;
                    let _ = timeout(Duration::from_secs(8), serve(stream, bridge)).await;
                });
            }
        }
    });
    Ok(())
}

fn publish_endpoint(dir: &Path, port: u16, token: &str) -> io::Result<()> {
    let data = json!({ "version": VERSION, "port": port, "token": token, "pid": std::process::id() });
    let tmp = dir.join(format!(".codex-bridge-{}.tmp", Uuid::new_v4()));
    let result = (|| {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&tmp)?;
        file.write_all(data.to_string().as_bytes())?;
        drop(file);
        fs::rename(&tmp, dir.join(DISCOVERY_FILE))
    })();
    if result.is_err() {
        let _ = fs::remove_file(tmp);
    }
    result
}

fn authenticated(expected: &str, token: &str) -> bool {
    expected.len() == token.len()
        && expected.bytes().zip(token.bytes()).fold(0, |diff, (a, b)| diff | (a ^ b)) == 0
}

async fn serve(mut stream: TcpStream, bridge: Bridge) -> io::Result<()> {
    let mut input = Vec::new();
    let mut reader = BufReader::new((&mut stream).take(MAX_FRAME as u64 + 1));
    reader.read_until(b'\n', &mut input).await?;
    drop(reader);
    if input.len() > MAX_FRAME || input.last() != Some(&b'\n') {
        return Ok(());
    }
    let Ok(request) = serde_json::from_slice::<Request>(&input) else { return Ok(()) };
    if request.version != VERSION || !authenticated(&bridge.token, &request.token) {
        return Ok(());
    }
    let response = match bridge.handle(request.action).await {
        Ok(result) => json!({ "result": result }),
        Err(error) => json!({ "error": error }),
    };
    stream.write_all(response.to_string().as_bytes()).await?;
    stream.write_all(b"\n").await
}

impl Bridge {
    async fn handle(&self, action: Action) -> Result<Value, String> {
        match action {
            Action::Ping => Ok(json!({"connected": true, "bridgeVersion": VERSION})),
            Action::Submit { request_id, command, args } => self.submit(request_id, command, args),
            Action::Job { job_id } => {
                let id = job_id.to_string();
                self.jobs.lock().unwrap().get(&id)
                    .map(|job| job.status(&id, true))
                    .ok_or_else(|| "Studio job not found. Its history is kept while Studio is open.".into())
            }
            Action::ListJobs => {
                let jobs = self.jobs.lock().unwrap();
                let mut ordered: Vec<_> = jobs.iter().collect();
                ordered.sort_unstable_by_key(|(_, job)| std::cmp::Reverse(job.started));
                Ok(json!(ordered.iter().map(|(id, job)| job.status(id, false)).collect::<Vec<_>>()))
            }
            Action::Cancel { job_id } => {
                let id = job_id.to_string();
                {
                    let jobs = self.jobs.lock().unwrap();
                    let job = jobs.get(&id).ok_or("Studio job not found.")?;
                    if job.finished.is_some() || !job.cancellable {
                        return Err("This Studio job no longer has an available cancellation action.".into());
                    }
                }
                let cancelled = commands::cancel_studio_job(self.app.state(), id.clone()).await?;
                Ok(json!({"jobId": id, "cancelRequested": cancelled}))
            }
        }
    }

    fn submit(&self, request_id: Uuid, command: String, mut args: Value) -> Result<Value, String> {
        if !args.is_object() {
            return Err("Studio tool arguments must be an object.".into());
        }
        let id = request_id.to_string();
        {
            let mut jobs = self.jobs.lock().unwrap();
            if let Some(job) = jobs.get(&id) {
                return Ok(job.status(&id, false));
            }
            jobs.retain(|_, job| job.finished.as_ref().is_none_or(|(end, _)| end.elapsed() < RETAIN_RESULTS));
            if jobs.values().filter(|job| job.finished.is_none()).count() >= MAX_ACTIVE_JOBS {
                return Err("Studio has too many active plugin jobs. Wait for one to finish.".into());
            }
            if jobs.len() >= MAX_JOBS {
                let oldest = jobs.iter()
                    .filter_map(|(id, job)| job.finished.as_ref().map(|(end, _)| (id.clone(), *end)))
                    .min_by_key(|(_, end)| *end);
                if let Some((old_id, _)) = oldest {
                    jobs.remove(&old_id);
                }
            }
            let cancellable = codex_actions::streams(&command, &args);
            if cancellable {
                args["jobId"] = json!(&id);
            }
            jobs.insert(id.clone(), Job {
                command: command.clone(), cancellable, started: Instant::now(), finished: None,
                events: VecDeque::new(), event_count: 0,
            });
        }
        let response = self.jobs.lock().unwrap()[&id].status(&id, false);
        let bridge = self.clone();
        tokio::spawn(async move {
            let task_app = bridge.app.clone();
            let result = tokio::spawn(async move {
                let mut result = codex_actions::dispatch(&task_app, &command, args)
                    .await.map_err(Value::String);
                match &mut result {
                    Ok(value) | Err(value) => codex_actions::redact(&task_app, value),
                }
                match result {
                    Ok(value) if value.to_string().len() <= MAX_RESULT => Ok(value),
                    Ok(_) => Err("The result exceeds 1 MiB. Check the output files in Studio before retrying.".into()),
                    Err(value) => Err(value.as_str().unwrap_or("Studio action failed.").chars().take(8192).collect()),
                }
            }).await.unwrap_or_else(|_| Err("The Studio action terminated unexpectedly.".into()));
            if let Some(job) = bridge.jobs.lock().unwrap().get_mut(&id) {
                job.finished = Some((Instant::now(), result));
            }
        });
        Ok(response)
    }
}
