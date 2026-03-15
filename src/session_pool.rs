use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use chrono::Utc;
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{mpsc, Mutex};
use tracing::{error, info};
use uuid::Uuid;

use crate::message_queue::MessageQueue;
use crate::types::{
    convert_claude_message, PermissionMode, QueueStats, SessionInfo, SessionStatus, StreamEvent,
};

/// Internal session state
struct InternalSession {
    session_id: String,
    path: String,
    mode: PermissionMode,
    status: SessionStatus,
    sdk_session_id: Option<String>,
    created_at: chrono::DateTime<Utc>,
    last_activity_at: chrono::DateTime<Utc>,
    /// Currently running Claude process (only during message processing)
    process: Option<Child>,
    queue: MessageQueue,
    /// Whether we're currently processing a message
    processing: bool,
    /// Model name reported by Claude CLI
    model: Option<String>,
}

/// SessionPool manages Claude CLI sessions using per-message spawn.
///
/// Each call to send() spawns a fresh `claude --print <msg> --output-format stream-json`
/// process. Session continuity is maintained via `--resume <sdkSessionId>`.
pub struct SessionPool {
    sessions: Arc<Mutex<HashMap<String, InternalSession>>>,
}

impl SessionPool {
    pub fn new() -> Self {
        Self {
            sessions: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Create a new session (lightweight — just registers session state).
    /// No Claude CLI process is spawned until a message is sent.
    pub async fn create(&self, path: &str, mode: PermissionMode) -> Result<String, String> {
        // Expand ~ to home directory
        let expanded_path = expand_tilde(path);

        // Validate path exists on the remote machine
        if !Path::new(&expanded_path).exists() {
            return Err(format!("Path does not exist on remote machine: {}", expanded_path));
        }

        let session_id = Uuid::new_v4().to_string();
        let now = Utc::now();

        info!(
            "[SessionPool] Creating session {} at {} (mode={:?})",
            session_id, expanded_path, mode
        );

        let session = InternalSession {
            session_id: session_id.clone(),
            path: expanded_path,
            mode,
            status: SessionStatus::Idle,
            sdk_session_id: None,
            created_at: now,
            last_activity_at: now,
            process: None,
            queue: MessageQueue::new(),
            processing: false,
            model: None,
        };

        self.sessions.lock().await.insert(session_id.clone(), session);
        Ok(session_id)
    }

    /// Send a message to a session.
    /// Returns a receiver of stream events.
    pub async fn send(
        &self,
        session_id: &str,
        message: &str,
    ) -> Result<mpsc::Receiver<StreamEvent>, String> {
        let mut sessions = self.sessions.lock().await;
        let session = sessions
            .get_mut(session_id)
            .ok_or_else(|| format!("Session not found: {}", session_id))?;

        // If Claude is busy, queue the message
        if session.processing {
            let position = session.queue.enqueue_user(message.to_string());
            let (tx, rx) = mpsc::channel(1);
            let _ = tx.send(StreamEvent::Queued { position }).await;
            return Ok(rx);
        }

        // Start processing
        let (tx, rx) = mpsc::channel(256);
        let path = session.path.clone();
        let mode = session.mode;
        let sdk_session_id = session.sdk_session_id.clone();

        session.processing = true;
        session.status = SessionStatus::Busy;
        session.last_activity_at = Utc::now();

        let sessions_ref = self.sessions.clone();
        let session_id_owned = session_id.to_string();
        let message_owned = message.to_string();

        // Drop the lock before spawning the task
        drop(sessions);

        // Spawn the processing task
        tokio::spawn(process_message_loop(
            sessions_ref,
            session_id_owned,
            message_owned,
            path,
            mode,
            sdk_session_id,
            tx,
        ));

        Ok(rx)
    }

    /// Resume a session — update the sdkSessionId
    pub async fn resume(
        &self,
        session_id: &str,
        sdk_session_id: Option<String>,
    ) -> serde_json::Value {
        let mut sessions = self.sessions.lock().await;

        if let Some(session) = sessions.get_mut(session_id) {
            if let Some(sid) = sdk_session_id {
                session.sdk_session_id = Some(sid);
            }
            session.queue.on_client_reconnect();
            serde_json::json!({ "ok": true, "fallback": false })
        } else {
            serde_json::json!({ "ok": false, "fallback": false })
        }
    }

    /// Destroy a session: kill any running Claude process and clean up.
    /// Sends SIGTERM first, then SIGKILL after 5 seconds (matching TypeScript behavior).
    pub async fn destroy(&self, session_id: &str) -> bool {
        let mut sessions = self.sessions.lock().await;

        if let Some(mut session) = sessions.remove(session_id) {
            // Kill any running process with SIGTERM + SIGKILL escalation
            if let Some(ref mut child) = session.process {
                send_sigterm_then_sigkill(child, 5000).await;
            }
            session.status = SessionStatus::Destroyed;
            session.queue.clear();
            info!("[SessionPool] Destroyed session {}", session_id);
            true
        } else {
            false
        }
    }

    /// Set the permission mode for a session
    pub async fn set_mode(&self, session_id: &str, mode: PermissionMode) -> Result<bool, String> {
        let mut sessions = self.sessions.lock().await;
        let session = sessions
            .get_mut(session_id)
            .ok_or_else(|| format!("Session not found: {}", session_id))?;
        session.mode = mode;
        info!(
            "[SessionPool] Mode changed to {:?} for session {}",
            mode, session_id
        );
        Ok(true)
    }

    /// Interrupt the current Claude operation for a session.
    /// Sends SIGTERM to the running Claude CLI process (matching TypeScript behavior).
    pub async fn interrupt(&self, session_id: &str) -> Result<bool, String> {
        let mut sessions = self.sessions.lock().await;
        let session = sessions
            .get_mut(session_id)
            .ok_or_else(|| format!("Session not found: {}", session_id))?;

        if !session.processing {
            return Ok(false);
        }

        if let Some(ref child) = session.process {
            info!("[SessionPool] Interrupting session {}", session_id);
            // Send SIGTERM (not SIGKILL) — matches TypeScript behavior
            send_sigterm(child);
            session.queue.clear();
            Ok(true)
        } else {
            Ok(false)
        }
    }

    /// List all sessions
    pub async fn list_sessions(&self) -> Vec<SessionInfo> {
        let sessions = self.sessions.lock().await;
        sessions
            .values()
            .map(|s| SessionInfo {
                session_id: s.session_id.clone(),
                path: s.path.clone(),
                status: s.status,
                mode: s.mode,
                sdk_session_id: s.sdk_session_id.clone(),
                model: s.model.clone(),
                created_at: s.created_at.to_rfc3339(),
                last_activity_at: s.last_activity_at.to_rfc3339(),
            })
            .collect()
    }

    /// Mark client as disconnected for a session
    pub async fn client_disconnect(&self, session_id: &str) {
        let mut sessions = self.sessions.lock().await;
        if let Some(session) = sessions.get_mut(session_id) {
            session.queue.on_client_disconnect();
        }
    }

    /// Buffer a single event for a session
    pub async fn buffer_event(&self, session_id: &str, event: StreamEvent) {
        let mut sessions = self.sessions.lock().await;
        if let Some(session) = sessions.get_mut(session_id) {
            session.queue.buffer_response(event, true);
        }
    }

    /// Mark client as reconnected, return buffered events
    pub async fn client_reconnect(&self, session_id: &str) -> Vec<StreamEvent> {
        let mut sessions = self.sessions.lock().await;
        if let Some(session) = sessions.get_mut(session_id) {
            session.queue.on_client_reconnect()
        } else {
            Vec::new()
        }
    }

    /// Get queue stats for a session
    pub async fn get_queue_stats(&self, session_id: &str) -> Option<QueueStats> {
        let sessions = self.sessions.lock().await;
        sessions.get(session_id).map(|s| s.queue.stats())
    }

    /// Destroy all sessions (cleanup on shutdown)
    pub async fn destroy_all(&self) {
        let mut sessions = self.sessions.lock().await;
        for (id, session) in sessions.iter_mut() {
            if let Some(ref mut child) = session.process {
                send_sigterm_then_sigkill(child, 5000).await;
            }
            info!("[SessionPool] Destroyed session {} (shutdown)", id);
        }
        sessions.clear();
    }
}

// ─── Signal Helpers ───

/// Send SIGTERM to a child process (Unix only).
fn send_sigterm(child: &Child) {
    if let Some(pid) = child.id() {
        #[cfg(unix)]
        {
            use nix::sys::signal::{kill, Signal};
            use nix::unistd::Pid;
            let _ = kill(Pid::from_raw(pid as i32), Signal::SIGTERM);
        }
        #[cfg(not(unix))]
        {
            let _ = pid; // suppress unused warning
        }
    }
}

/// Send SIGTERM, wait `timeout_ms`, then SIGKILL if still alive.
/// Matches TypeScript's `kill("SIGTERM"); setTimeout(() => kill("SIGKILL"), timeout)` pattern.
async fn send_sigterm_then_sigkill(child: &mut Child, timeout_ms: u64) {
    send_sigterm(child);
    tokio::select! {
        _ = child.wait() => {
            // Process exited gracefully after SIGTERM
        }
        _ = tokio::time::sleep(std::time::Duration::from_millis(timeout_ms)) => {
            // Timeout — escalate to SIGKILL
            let _ = child.kill().await;
        }
    }
}

/// Run a single Claude CLI invocation and stream events to `tx`.
/// Returns `true` if the process completed successfully.
async fn run_claude_process(
    sessions: &Arc<Mutex<HashMap<String, InternalSession>>>,
    session_id: &str,
    message: &str,
    path: &str,
    mode: PermissionMode,
    sdk_session_id: Option<&str>,
    tx: &mpsc::Sender<StreamEvent>,
) -> bool {
    // Build CLI arguments
    let mut args: Vec<String> = vec![
        "--print".to_string(),
        message.to_string(),
        "--output-format".to_string(),
        "stream-json".to_string(),
        "--verbose".to_string(),
    ];

    for flag in mode.to_cli_flags() {
        args.push(flag.to_string());
    }

    if let Some(sid) = sdk_session_id {
        args.push("--resume".to_string());
        args.push(sid.to_string());
    }

    info!("[SessionPool] Spawning claude for session {}", session_id);
    info!(
        "[SessionPool] Command: claude {}",
        args.iter()
            .map(|a| if a.contains(' ') {
                format!("\"{}\"", a)
            } else {
                a.clone()
            })
            .collect::<Vec<_>>()
            .join(" ")
    );
    info!("[SessionPool] CWD: {}", path);

    // Spawn Claude CLI process
    let child_result = Command::new("claude")
        .args(&args)
        .current_dir(path)
        .env("TERM", "dumb")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn();

    let mut child = match child_result {
        Ok(child) => child,
        Err(e) => {
            error!("[Session {}] Failed to spawn claude: {}", session_id, e);
            let _ = tx
                .send(StreamEvent::Error {
                    message: format!("Failed to spawn claude: {}", e),
                })
                .await;
            return false;
        }
    };

    // Take stdout and stderr before storing child
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();

    // Store the child process in the session
    {
        let mut sessions_guard = sessions.lock().await;
        if let Some(session) = sessions_guard.get_mut(session_id) {
            session.process = Some(child);
        }
    }

    // Read stderr in a separate task (just logging)
    if let Some(stderr) = stderr {
        let sid = session_id.to_string();
        tokio::spawn(async move {
            let reader = BufReader::new(stderr);
            let mut lines = reader.lines();
            while let Ok(Some(line)) = lines.next_line().await {
                error!("[Session {}] stderr: {}", sid, line);
            }
        });
    }

    // Read stdout line-by-line (JSON-lines from Claude CLI)
    if let Some(stdout) = stdout {
        let reader = BufReader::new(stdout);
        let mut lines = reader.lines();

        while let Ok(Some(line)) = lines.next_line().await {
            let parsed: Value = match serde_json::from_str(&line) {
                Ok(v) => v,
                Err(_) => {
                    info!("[Session {}] non-JSON stdout: {}", session_id, line);
                    continue;
                }
            };

            // Extract model name from system init message
            if parsed.get("type").and_then(|v| v.as_str()) == Some("system")
                && parsed.get("subtype").and_then(|v| v.as_str()) == Some("init")
            {
                if let Some(model) = parsed.get("model").and_then(|v| v.as_str()) {
                    let mut sessions_guard = sessions.lock().await;
                    if let Some(session) = sessions_guard.get_mut(session_id) {
                        session.model = Some(model.to_string());
                    }
                    info!("[Session {}] Model: {}", session_id, model);
                }
            }

            // Convert to StreamEvent
            let event = convert_claude_message(&parsed);

            // Capture SDK session ID
            if let Some(sid) = event.session_id() {
                let mut sessions_guard = sessions.lock().await;
                if let Some(session) = sessions_guard.get_mut(session_id) {
                    session.sdk_session_id = Some(sid.to_string());
                }
            }

            // Send event to the channel
            if tx.send(event).await.is_err() {
                break;
            }
        }
    }

    // Wait for the process to exit
    let exit_status = {
        let mut sessions_guard = sessions.lock().await;
        if let Some(session) = sessions_guard.get_mut(session_id) {
            if let Some(ref mut child) = session.process {
                child.wait().await.ok()
            } else {
                None
            }
        } else {
            None
        }
    };

    // Check exit code — match TypeScript format: (code=N, signal=SIGNAME)
    if let Some(status) = exit_status {
        let code = status.code();

        #[cfg(unix)]
        let signal_str = {
            use std::os::unix::process::ExitStatusExt;
            status.signal().map(|s| format!("{}", s))
        };
        #[cfg(not(unix))]
        let signal_str: Option<String> = None;

        info!(
            "[Session {}] Process exited: code={}, signal={}",
            session_id,
            code.map_or("null".to_string(), |c| c.to_string()),
            signal_str.as_deref().unwrap_or("null"),
        );

        if code != Some(0) && code.is_some() {
            let _ = tx
                .send(StreamEvent::Error {
                    message: format!(
                        "Claude process exited abnormally (code={}, signal={})",
                        code.map_or("null".to_string(), |c| c.to_string()),
                        signal_str.as_deref().unwrap_or("null"),
                    ),
                })
                .await;
        }
    }

    // Kill process if still alive (e.g. on error/interrupt) — SIGTERM then SIGKILL after 3s
    {
        let mut sessions_guard = sessions.lock().await;
        if let Some(session) = sessions_guard.get_mut(session_id) {
            if let Some(ref mut child) = session.process {
                // Check if process is still alive by trying wait with timeout 0
                match child.try_wait() {
                    Ok(None) => {
                        // Still alive — send SIGTERM, then SIGKILL after 3s
                        send_sigterm_then_sigkill(child, 3000).await;
                    }
                    _ => {} // Already exited or error
                }
            }
        }
    }

    true
}

/// Process a message and then drain the queue (loop-based, no recursion).
/// This runs as a background tokio task.
async fn process_message_loop(
    sessions: Arc<Mutex<HashMap<String, InternalSession>>>,
    session_id: String,
    initial_message: String,
    initial_path: String,
    initial_mode: PermissionMode,
    initial_sdk_session_id: Option<String>,
    tx: mpsc::Sender<StreamEvent>,
) {
    // Process the initial message
    let success = run_claude_process(
        &sessions,
        &session_id,
        &initial_message,
        &initial_path,
        initial_mode,
        initial_sdk_session_id.as_deref(),
        &tx,
    )
    .await;

    if !success {
        let mut sessions_guard = sessions.lock().await;
        if let Some(session) = sessions_guard.get_mut(&session_id) {
            session.processing = false;
            session.status = SessionStatus::Idle;
            session.process = None;
        }
        return;
    }

    // Loop: reset state, check for queued messages, process them
    loop {
        let next_message = {
            let mut sessions_guard = sessions.lock().await;
            if let Some(session) = sessions_guard.get_mut(&session_id) {
                session.process = None;
                session.processing = false;
                session.status = SessionStatus::Idle;

                if session.queue.has_user_pending() {
                    session.queue.dequeue_user()
                } else {
                    None
                }
            } else {
                None
            }
        };

        let queued = match next_message {
            Some(q) => q,
            None => break, // No more queued messages
        };

        // Get current session state for the next message
        let (path, mode, sdk_sid) = {
            let sessions_guard = sessions.lock().await;
            if let Some(session) = sessions_guard.get(&session_id) {
                (
                    session.path.clone(),
                    session.mode,
                    session.sdk_session_id.clone(),
                )
            } else {
                break;
            }
        };

        // Mark as processing again
        {
            let mut sessions_guard = sessions.lock().await;
            if let Some(session) = sessions_guard.get_mut(&session_id) {
                session.processing = true;
                session.status = SessionStatus::Busy;
                session.last_activity_at = Utc::now();
            }
        }

        // For background queued messages, create a buffering channel
        let sessions_for_buffer = sessions.clone();
        let sid_for_buffer = session_id.clone();
        let (buf_tx, mut buf_rx) = mpsc::channel::<StreamEvent>(256);

        let buffer_task = tokio::spawn(async move {
            while let Some(event) = buf_rx.recv().await {
                let mut sessions_guard = sessions_for_buffer.lock().await;
                if let Some(session) = sessions_guard.get_mut(&sid_for_buffer) {
                    if !session.queue.is_client_connected() {
                        session.queue.buffer_response(event, false);
                    }
                }
            }
        });

        run_claude_process(
            &sessions,
            &session_id,
            &queued.message,
            &path,
            mode,
            sdk_sid.as_deref(),
            &buf_tx,
        )
        .await;

        drop(buf_tx);
        let _ = buffer_task.await;
    }
}

/// Expand ~ to home directory
fn expand_tilde(path: &str) -> String {
    if path.starts_with("~/") || path == "~" {
        if let Some(home) = dirs::home_dir() {
            return path.replacen('~', &home.to_string_lossy(), 1);
        }
    }
    path.to_string()
}
