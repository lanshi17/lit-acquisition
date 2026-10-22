//! Bridge between axum and the Python worker processes.
//!
//! Each worker is addressed with one JSON object per line over stdin/stdout.
//! Workers are long-lived, so imports and httpx connection pools are paid once
//! per worker rather than once per request. A worker that misbehaves (timeout,
//! EOF, malformed response) is killed and transparently respawned on the next
//! job.

use std::{process::Stdio, sync::Arc, time::Duration};

use async_channel::{Receiver, Sender};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::{oneshot, Mutex},
    task::JoinHandle,
    time::timeout,
};
use tracing::{debug, warn};

use crate::config::Config;

/// Responses larger than this are treated as a protocol violation.
const MAX_RESPONSE_BYTES: usize = 8 * 1024 * 1024;

/// Errors surfaced by the worker bridge.
#[derive(Debug, thiserror::Error)]
pub enum BridgeError {
    #[error("failed to start python worker: {0}")]
    Spawn(String),
    #[error("worker protocol error: {0}")]
    Protocol(String),
    #[error("worker I/O error: {0}")]
    Io(String),
    #[error("worker request timed out")]
    Timeout,
    #[error("worker returned {code}: {message}")]
    Remote { code: String, message: String },
    #[error("worker pool is shut down")]
    Closed,
}

struct Job {
    method: String,
    params: Value,
    timeout: Duration,
    reply: oneshot::Sender<Result<Value, BridgeError>>,
}

/// A pool of Python worker processes.
pub struct WorkerPool {
    tx: Sender<Job>,
    handles: Mutex<Vec<JoinHandle<()>>>,
    workers: usize,
}

impl WorkerPool {
    /// Spawn `cfg.workers` worker tasks. Worker processes are started lazily on
    /// the first request so the HTTP server can bind even if Python is broken;
    /// `/readyz` then reports the failure instead of the process refusing to boot.
    pub fn start(cfg: Arc<Config>) -> Self {
        let capacity = (cfg.workers * 8).max(8);
        let (tx, rx) = async_channel::bounded::<Job>(capacity);
        let mut handles = Vec::with_capacity(cfg.workers);

        for index in 0..cfg.workers {
            let worker_cfg = Arc::clone(&cfg);
            let worker_rx = rx.clone();
            handles.push(tokio::spawn(async move {
                worker_loop(index, worker_cfg, worker_rx).await;
            }));
        }

        Self {
            tx,
            handles: Mutex::new(handles),
            workers: cfg.workers,
        }
    }

    /// Send one RPC to a free worker.
    pub async fn call(
        &self,
        method: &str,
        params: Value,
        call_timeout: Duration,
    ) -> Result<Value, BridgeError> {
        let (reply, response) = oneshot::channel();
        let job = Job {
            method: method.to_string(),
            params,
            timeout: call_timeout,
            reply,
        };
        self.tx.send(job).await.map_err(|_| BridgeError::Closed)?;
        response.await.map_err(|_| BridgeError::Closed)?
    }

    /// Cheap liveness probe used by `/readyz`.
    pub async fn ping(&self) -> Result<Value, BridgeError> {
        self.call("ping", json!({}), Duration::from_secs(5)).await
    }

    pub fn worker_count(&self) -> usize {
        self.workers
    }

    /// Stop accepting jobs and wait for workers to exit.
    pub async fn shutdown(&self) {
        self.tx.close();
        let mut handles = self.handles.lock().await;
        for handle in handles.drain(..) {
            if timeout(Duration::from_secs(5), handle).await.is_err() {
                warn!("worker task did not stop within the shutdown budget");
            }
        }
    }
}

async fn worker_loop(index: usize, cfg: Arc<Config>, rx: Receiver<Job>) {
    let mut worker: Option<WorkerProcess> = None;

    while let Ok(job) = rx.recv().await {
        let mut outcome: Option<Result<Value, BridgeError>> = None;

        if worker.is_none() {
            match WorkerProcess::spawn(&cfg).await {
                Ok(process) => {
                    debug!(worker = index, python = %cfg.python, module = %cfg.python_module, "worker started");
                    worker = Some(process);
                }
                Err(err) => outcome = Some(Err(err)),
            }
        }

        if outcome.is_none() {
            let process = worker.as_mut().expect("worker was just ensured");
            let call_timeout = job.timeout.min(cfg.request_timeout);
            match process.call(&job.method, &job.params, call_timeout).await {
                Ok(value) => outcome = Some(Ok(value)),
                Err(err) => {
                    warn!(worker = index, error = %err, "worker call failed; recycling worker");
                    process.shutdown().await;
                    worker = None;
                    outcome = Some(Err(err));
                }
            }
        }

        if let Some(result) = outcome {
            let _ = job.reply.send(result);
        }
    }

    if let Some(mut process) = worker {
        process.shutdown().await;
    }
}

#[derive(Debug, Deserialize)]
struct RpcResponse {
    id: String,
    #[serde(default)]
    ok: bool,
    #[serde(default)]
    result: Option<Value>,
    #[serde(default)]
    error: Option<RpcErrorBody>,
}

#[derive(Debug, Default, Deserialize)]
struct RpcErrorBody {
    #[serde(default)]
    code: String,
    #[serde(default)]
    message: String,
}

struct WorkerProcess {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    next_id: u64,
}

impl WorkerProcess {
    async fn spawn(cfg: &Config) -> Result<Self, BridgeError> {
        let mut command = Command::new(&cfg.python);
        command
            .arg("-u")
            .arg("-m")
            .arg(&cfg.python_module)
            .env("PYTHONUNBUFFERED", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true);

        if let Some(path) = &cfg.python_path {
            let combined = match std::env::var("PYTHONPATH") {
                Ok(existing) if !existing.is_empty() => format!("{path}:{existing}"),
                _ => path.clone(),
            };
            command.env("PYTHONPATH", combined);
        }

        let mut child = command.spawn().map_err(|err| {
            BridgeError::Spawn(format!("{} -m {}: {err}", cfg.python, cfg.python_module))
        })?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| BridgeError::Spawn("worker stdin unavailable".to_string()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| BridgeError::Spawn("worker stdout unavailable".to_string()))?;

        Ok(Self {
            child,
            stdin,
            stdout: BufReader::new(stdout),
            next_id: 1,
        })
    }

    async fn call(
        &mut self,
        method: &str,
        params: &Value,
        call_timeout: Duration,
    ) -> Result<Value, BridgeError> {
        let id = self.next_id.to_string();
        self.next_id += 1;

        let mut payload = serde_json::to_vec(&json!({
            "id": id,
            "method": method,
            "params": params,
        }))
        .map_err(|err| BridgeError::Protocol(err.to_string()))?;
        payload.push(b'\n');

        self.stdin
            .write_all(&payload)
            .await
            .map_err(|err| BridgeError::Io(format!("write: {err}")))?;
        self.stdin
            .flush()
            .await
            .map_err(|err| BridgeError::Io(format!("flush: {err}")))?;

        let read = async {
            let mut buffer = Vec::new();
            let read = self
                .stdout
                .read_until(b'\n', &mut buffer)
                .await
                .map_err(|err| BridgeError::Io(format!("read: {err}")))?;
            if read == 0 {
                return Err(BridgeError::Io("worker closed stdout".to_string()));
            }
            if buffer.len() > MAX_RESPONSE_BYTES {
                return Err(BridgeError::Protocol(format!(
                    "worker response exceeded {MAX_RESPONSE_BYTES} bytes"
                )));
            }
            Ok::<Vec<u8>, BridgeError>(buffer)
        };

        let buffer = match timeout(call_timeout, read).await {
            Ok(result) => result?,
            Err(_) => return Err(BridgeError::Timeout),
        };

        let response: RpcResponse = serde_json::from_slice(&buffer)
            .map_err(|err| BridgeError::Protocol(format!("invalid JSON from worker: {err}")))?;
        if response.id != id {
            return Err(BridgeError::Protocol(format!(
                "response id mismatch: expected {id}, got {}",
                response.id
            )));
        }

        if response.ok {
            Ok(response.result.unwrap_or(Value::Null))
        } else {
            let error = response.error.unwrap_or_default();
            Err(BridgeError::Remote {
                code: if error.code.is_empty() {
                    "worker_error".to_string()
                } else {
                    error.code
                },
                message: error.message,
            })
        }
    }

    async fn shutdown(&mut self) {
        let _ = self.stdin.shutdown().await;
        match timeout(Duration::from_secs(3), self.child.wait()).await {
            Ok(Ok(_)) => {}
            _ => {
                let _ = self.child.start_kill();
                let _ = self.child.wait().await;
            }
        }
    }
}

impl Drop for WorkerProcess {
    fn drop(&mut self) {
        // `kill_on_drop(true)` covers the normal path; this is a belt-and-braces
        // guard for early returns while a child is still running.
        let _ = self.child.start_kill();
    }
}
