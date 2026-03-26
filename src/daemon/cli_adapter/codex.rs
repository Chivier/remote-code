//! Codex CLI adapter.
//!
//! Codex uses `codex exec` with `--json` for structured output.
//! Stateful adapter: tracks cumulative text for delta computation and thread_id.
//!
//! Event hierarchy: thread > turn > item
//! - `thread.started` → System init
//! - `item.updated` (agent_message) → Partial (cumulative → delta)
//! - `item.completed` (agent_message) → Text
//! - `item.started/completed` (command_execution) → ToolUse (bash)
//! - `item.started/completed` (file_change) → ToolUse (edit)
//! - `item.*` (mcp_tool_call) → ToolUse
//! - `turn.completed` → Result
//! - `turn.failed` / `error` → Error

use std::cell::Cell;
use std::path::Path;

use serde_json::Value;
use tokio::process::Command;

use super::CliAdapter;
use crate::types::{PermissionMode, StreamEvent};

/// Codex CLI adapter with cumulative text tracking.
pub struct CodexAdapter {
    /// Tracks cumulative text length for delta computation.
    last_text_len: Cell<usize>,
    /// Captures thread_id from thread.started for the Result event.
    thread_id: Cell<Option<String>>,
}

impl CodexAdapter {
    pub fn new() -> Self {
        Self {
            last_text_len: Cell::new(0),
            thread_id: Cell::new(None),
        }
    }
}

// SAFETY: Cell is not Sync, but we only access from a single task within run_cli_process.
// The adapter is created fresh per call and never shared across tasks.
unsafe impl Sync for CodexAdapter {}

impl CliAdapter for CodexAdapter {
    fn name(&self) -> &str {
        "codex"
    }

    fn build_command(
        &self,
        message: &str,
        mode: PermissionMode,
        cwd: &Path,
        model: Option<&str>,
    ) -> Command {
        let mut cmd = Command::new("codex");
        cmd.arg("exec");
        cmd.arg("--json");

        match mode {
            PermissionMode::Auto | PermissionMode::Code => {
                cmd.arg("--full-auto");
            }
            PermissionMode::Plan => {
                cmd.args(["--sandbox", "read-only"]);
            }
            PermissionMode::Ask => {
                cmd.args(["--sandbox", "read-only", "--approval-policy", "on-failure"]);
            }
        }

        if let Some(m) = model {
            cmd.args(["--model", m]);
        }

        cmd.args(["--cd", &cwd.to_string_lossy()]);
        cmd.arg(message);

        cmd.env("TERM", "dumb")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());

        cmd
    }

    fn build_resume_command(
        &self,
        message: &str,
        mode: PermissionMode,
        cwd: &Path,
        session_id: &str,
        model: Option<&str>,
    ) -> Command {
        let mut cmd = Command::new("codex");
        cmd.args(["exec", "resume", session_id]);
        cmd.arg("--json");

        match mode {
            PermissionMode::Auto | PermissionMode::Code => {
                cmd.arg("--full-auto");
            }
            PermissionMode::Plan => {
                cmd.args(["--sandbox", "read-only"]);
            }
            PermissionMode::Ask => {
                cmd.args(["--sandbox", "read-only", "--approval-policy", "on-failure"]);
            }
        }

        if let Some(m) = model {
            cmd.args(["--model", m]);
        }

        cmd.args(["--cd", &cwd.to_string_lossy()]);
        cmd.arg(message);

        cmd.env("TERM", "dumb")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());

        cmd
    }

    fn parse_output_line(&self, line: &str) -> Vec<StreamEvent> {
        let msg: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => return vec![],
        };

        let event_type = msg.get("type").and_then(|v| v.as_str()).unwrap_or("");

        match event_type {
            "thread.started" => {
                // Capture thread_id for later Result event
                if let Some(tid) = msg.get("thread_id").and_then(|v| v.as_str()) {
                    self.thread_id.set(Some(tid.to_string()));
                }
                vec![StreamEvent::System {
                    subtype: Some("init".to_string()),
                    session_id: msg
                        .get("thread_id")
                        .and_then(|v| v.as_str())
                        .map(String::from),
                    model: msg.get("model").and_then(|v| v.as_str()).map(String::from),
                    raw: Some(msg),
                }]
            }

            "turn.started" => vec![], // Ignored

            "item.started" => {
                let item_type = msg.get("item_type").and_then(|v| v.as_str()).unwrap_or("");
                match item_type {
                    "command_execution" => vec![StreamEvent::ToolUse {
                        tool: Some("bash".to_string()),
                        input: msg.get("command").cloned(),
                        message: None,
                        raw: None,
                    }],
                    "file_change" => vec![StreamEvent::ToolUse {
                        tool: Some("edit".to_string()),
                        input: msg.get("file").cloned(),
                        message: None,
                        raw: None,
                    }],
                    "mcp_tool_call" => vec![StreamEvent::ToolUse {
                        tool: msg
                            .get("tool_name")
                            .and_then(|v| v.as_str())
                            .map(String::from),
                        input: msg.get("parameters").cloned(),
                        message: None,
                        raw: None,
                    }],
                    "agent_message" => {
                        // Reset cumulative text tracker for new message
                        self.last_text_len.set(0);
                        vec![]
                    }
                    _ => vec![],
                }
            }

            "item.updated" => {
                let item_type = msg.get("item_type").and_then(|v| v.as_str()).unwrap_or("");
                if item_type == "agent_message" {
                    // Cumulative text → compute delta
                    if let Some(text) = msg.get("text").and_then(|v| v.as_str()) {
                        let prev_len = self.last_text_len.get();
                        if text.len() > prev_len {
                            let delta = &text[prev_len..];
                            self.last_text_len.set(text.len());
                            return vec![StreamEvent::Partial {
                                content: Some(delta.to_string()),
                                raw: None,
                            }];
                        }
                    }
                }
                vec![]
            }

            "item.completed" => {
                let item_type = msg.get("item_type").and_then(|v| v.as_str()).unwrap_or("");
                match item_type {
                    "agent_message" => {
                        // Reset tracker for next message
                        self.last_text_len.set(0);
                        vec![StreamEvent::Text {
                            content: msg.get("text").and_then(|v| v.as_str()).map(String::from),
                            raw: None,
                        }]
                    }
                    "command_execution" => vec![StreamEvent::ToolUse {
                        tool: Some("bash".to_string()),
                        input: None,
                        message: msg.get("output").and_then(|v| v.as_str()).map(String::from),
                        raw: None,
                    }],
                    "file_change" => vec![StreamEvent::ToolUse {
                        tool: Some("edit".to_string()),
                        input: None,
                        message: msg
                            .get("summary")
                            .and_then(|v| v.as_str())
                            .map(String::from),
                        raw: None,
                    }],
                    "mcp_tool_call" => vec![StreamEvent::ToolUse {
                        tool: msg
                            .get("tool_name")
                            .and_then(|v| v.as_str())
                            .map(String::from),
                        input: None,
                        message: msg.get("result").and_then(|v| v.as_str()).map(String::from),
                        raw: None,
                    }],
                    _ => vec![],
                }
            }

            "turn.completed" => {
                let tid = self.thread_id.take();
                vec![StreamEvent::Result {
                    session_id: tid,
                    raw: Some(msg),
                }]
            }

            "turn.failed" => vec![StreamEvent::Error {
                message: msg
                    .get("error")
                    .and_then(|v| v.as_str())
                    .unwrap_or("Turn failed")
                    .to_string(),
            }],

            "error" => vec![StreamEvent::Error {
                message: msg
                    .get("message")
                    .and_then(|v| v.as_str())
                    .unwrap_or("Unknown error")
                    .to_string(),
            }],

            _ => vec![],
        }
    }

    fn extract_session_id(&self, line: &str) -> Option<String> {
        let msg: Value = serde_json::from_str(line).ok()?;
        if msg.get("type")?.as_str()? == "thread.started" {
            return msg.get("thread_id")?.as_str().map(String::from);
        }
        None
    }

    fn instructions_file(&self) -> &str {
        "AGENTS.md"
    }

    fn skills_dir(&self) -> Option<&str> {
        None
    }

    fn stderr_log_level(&self) -> tracing::Level {
        tracing::Level::INFO
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn adapter() -> CodexAdapter {
        CodexAdapter::new()
    }

    #[test]
    fn test_codex_thread_started() {
        let a = adapter();
        let line = r#"{"type":"thread.started","thread_id":"thread_abc123","model":"o4-mini"}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        match &events[0] {
            StreamEvent::System {
                subtype,
                session_id,
                model,
                ..
            } => {
                assert_eq!(subtype.as_deref(), Some("init"));
                assert_eq!(session_id.as_deref(), Some("thread_abc123"));
                assert_eq!(model.as_deref(), Some("o4-mini"));
            }
            _ => panic!("Expected System event"),
        }
    }

    #[test]
    fn test_codex_cumulative_text_delta() {
        let a = adapter();

        // First update: "Hello"
        let line1 = r#"{"type":"item.updated","item_type":"agent_message","text":"Hello"}"#;
        let events1 = a.parse_output_line(line1);
        assert_eq!(events1.len(), 1);
        assert!(
            matches!(&events1[0], StreamEvent::Partial { content, .. } if content.as_deref() == Some("Hello"))
        );

        // Second update: "Hello world" → delta is " world"
        let line2 = r#"{"type":"item.updated","item_type":"agent_message","text":"Hello world"}"#;
        let events2 = a.parse_output_line(line2);
        assert_eq!(events2.len(), 1);
        assert!(
            matches!(&events2[0], StreamEvent::Partial { content, .. } if content.as_deref() == Some(" world"))
        );

        // Third update with same length → no delta
        let line3 = r#"{"type":"item.updated","item_type":"agent_message","text":"Hello world"}"#;
        let events3 = a.parse_output_line(line3);
        assert!(events3.is_empty());
    }

    #[test]
    fn test_codex_item_completed_message() {
        let a = adapter();
        let line =
            r#"{"type":"item.completed","item_type":"agent_message","text":"Final answer."}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Text { content, .. } if content.as_deref() == Some("Final answer."))
        );
    }

    #[test]
    fn test_codex_command_execution() {
        let a = adapter();

        // Start
        let line = r#"{"type":"item.started","item_type":"command_execution","command":"ls -la"}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, .. } if tool.as_deref() == Some("bash"))
        );

        // Complete
        let line =
            r#"{"type":"item.completed","item_type":"command_execution","output":"total 42\n..."}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, message, .. }
            if tool.as_deref() == Some("bash") && message.as_deref() == Some("total 42\n..."))
        );
    }

    #[test]
    fn test_codex_file_change() {
        let a = adapter();

        let line = r#"{"type":"item.started","item_type":"file_change","file":"src/main.rs"}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, .. } if tool.as_deref() == Some("edit"))
        );

        let line =
            r#"{"type":"item.completed","item_type":"file_change","summary":"Added function"}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, message, .. }
            if tool.as_deref() == Some("edit") && message.as_deref() == Some("Added function"))
        );
    }

    #[test]
    fn test_codex_mcp_tool_call() {
        let a = adapter();

        let line = r#"{"type":"item.started","item_type":"mcp_tool_call","tool_name":"web_search","parameters":{"query":"rust async"}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, .. } if tool.as_deref() == Some("web_search"))
        );
    }

    #[test]
    fn test_codex_turn_completed() {
        let a = adapter();

        // First set thread_id via thread.started
        let _ = a.parse_output_line(
            r#"{"type":"thread.started","thread_id":"thread_xyz","model":"o4-mini"}"#,
        );

        let line = r#"{"type":"turn.completed"}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Result { session_id, .. } if session_id.as_deref() == Some("thread_xyz"))
        );
    }

    #[test]
    fn test_codex_turn_failed() {
        let a = adapter();
        let line = r#"{"type":"turn.failed","error":"Rate limit exceeded"}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Error { message } if message == "Rate limit exceeded")
        );
    }

    #[test]
    fn test_codex_error() {
        let a = adapter();
        let line = r#"{"type":"error","message":"Authentication failed"}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Error { message } if message == "Authentication failed")
        );
    }

    #[test]
    fn test_codex_turn_started_ignored() {
        let a = adapter();
        let line = r#"{"type":"turn.started"}"#;
        let events = a.parse_output_line(line);
        assert!(events.is_empty());
    }

    #[test]
    fn test_codex_cumulative_reset_on_item_started() {
        let a = adapter();

        // First message
        let _ = a.parse_output_line(r#"{"type":"item.started","item_type":"agent_message"}"#);
        let _ = a.parse_output_line(
            r#"{"type":"item.updated","item_type":"agent_message","text":"Hello"}"#,
        );
        assert_eq!(a.last_text_len.get(), 5);

        // Complete first
        let _ = a.parse_output_line(
            r#"{"type":"item.completed","item_type":"agent_message","text":"Hello"}"#,
        );

        // New message starts → cumulative tracker should reset on item.started
        let _ = a.parse_output_line(r#"{"type":"item.started","item_type":"agent_message"}"#);
        assert_eq!(a.last_text_len.get(), 0);

        // New update from scratch
        let events = a.parse_output_line(
            r#"{"type":"item.updated","item_type":"agent_message","text":"New"}"#,
        );
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Partial { content, .. } if content.as_deref() == Some("New"))
        );
    }

    #[test]
    fn test_codex_extract_session_id() {
        let a = adapter();
        assert_eq!(
            a.extract_session_id(r#"{"type":"thread.started","thread_id":"thread_123"}"#),
            Some("thread_123".to_string())
        );
        assert_eq!(a.extract_session_id(r#"{"type":"turn.completed"}"#), None);
    }

    #[test]
    fn test_codex_build_command_full_auto() {
        let a = adapter();
        let cmd = a.build_command("hello", PermissionMode::Auto, Path::new("/tmp/proj"), None);
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert_eq!(args[0], "exec");
        assert!(args.contains(&"--json"));
        assert!(args.contains(&"--full-auto"));
        assert!(args.contains(&"hello"));
    }

    #[test]
    fn test_codex_build_command_plan() {
        let a = adapter();
        let cmd = a.build_command("analyze", PermissionMode::Plan, Path::new("/tmp"), None);
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(args.contains(&"--sandbox"));
        assert!(args.contains(&"read-only"));
        assert!(!args.contains(&"--full-auto"));
    }

    #[test]
    fn test_codex_build_command_ask() {
        let a = adapter();
        let cmd = a.build_command("check", PermissionMode::Ask, Path::new("/tmp"), None);
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(args.contains(&"--sandbox"));
        assert!(args.contains(&"read-only"));
        assert!(args.contains(&"--approval-policy"));
        assert!(args.contains(&"on-failure"));
    }

    #[test]
    fn test_codex_build_resume_command() {
        let a = adapter();
        let cmd = a.build_resume_command(
            "continue",
            PermissionMode::Auto,
            Path::new("/tmp"),
            "thread_abc",
            Some("o4-mini"),
        );
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert_eq!(args[0], "exec");
        assert_eq!(args[1], "resume");
        assert_eq!(args[2], "thread_abc");
        assert!(args.contains(&"--model"));
        assert!(args.contains(&"o4-mini"));
    }

    #[test]
    fn test_codex_invalid_json() {
        let a = adapter();
        let events = a.parse_output_line("not json");
        assert!(events.is_empty());
    }

    #[test]
    fn test_codex_unknown_event_type() {
        let a = adapter();
        let events = a.parse_output_line(r#"{"type":"unknown.event"}"#);
        assert!(events.is_empty());
    }
}
