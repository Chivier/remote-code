//! Gemini CLI adapter.
//!
//! Gemini CLI uses `-p` for non-interactive prompt and `--output-format stream-json`.
//! Delta messages are pure deltas (not cumulative), so no cumulative tracking needed.
//! Stateful only for session_id capture from `init` event.
//!
//! Event types: init, message, tool_use, tool_result, error, result

use std::cell::Cell;
use std::path::Path;

use serde_json::Value;
use tokio::process::Command;

use super::CliAdapter;
use crate::types::{PermissionMode, StreamEvent};

/// Gemini CLI adapter.
pub struct GeminiAdapter {
    /// Captures session_id from init event for the final Result.
    session_id: Cell<Option<String>>,
}

impl GeminiAdapter {
    pub fn new() -> Self {
        Self {
            session_id: Cell::new(None),
        }
    }

    /// Normalize Gemini tool names to canonical names.
    fn normalize_tool_name(name: &str) -> &str {
        match name {
            "run_shell_command" => "bash",
            "write_file" | "replace" => "edit",
            "read_file" => "read",
            _ => name,
        }
    }
}

// SAFETY: Cell is not Sync, but adapter is created fresh per run_cli_process call
// and only accessed from a single task.
unsafe impl Sync for GeminiAdapter {}

impl CliAdapter for GeminiAdapter {
    fn name(&self) -> &str {
        "gemini"
    }

    fn build_command(
        &self,
        message: &str,
        mode: PermissionMode,
        cwd: &Path,
        model: Option<&str>,
    ) -> Command {
        let mut cmd = Command::new("gemini");
        cmd.args(["-p", message]);
        cmd.args(["--output-format", "stream-json"]);

        match mode {
            PermissionMode::Auto => {
                cmd.args(["--approval-mode", "yolo"]);
            }
            PermissionMode::Code => {
                cmd.args(["--approval-mode", "auto_edit"]);
            }
            PermissionMode::Plan => {
                cmd.arg("--sandbox");
            }
            PermissionMode::Ask => {
                // Default approval mode — no extra flags
            }
        }

        if let Some(m) = model {
            cmd.args(["--model", m]);
        }

        cmd.current_dir(cwd)
            .env("TERM", "dumb")
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
        let mut cmd = self.build_command(message, mode, cwd, model);
        cmd.args(["--resume", session_id]);
        cmd
    }

    fn parse_output_line(&self, line: &str) -> Vec<StreamEvent> {
        let msg: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => return vec![],
        };

        let event_type = msg.get("type").and_then(|v| v.as_str()).unwrap_or("");

        match event_type {
            "init" => {
                // Capture session_id for Result event
                if let Some(sid) = msg.get("session_id").and_then(|v| v.as_str()) {
                    self.session_id.set(Some(sid.to_string()));
                }
                vec![StreamEvent::System {
                    subtype: Some("init".to_string()),
                    session_id: msg
                        .get("session_id")
                        .and_then(|v| v.as_str())
                        .map(String::from),
                    model: msg.get("model").and_then(|v| v.as_str()).map(String::from),
                    raw: Some(msg),
                }]
            }

            "message" => {
                let role = msg.get("role").and_then(|v| v.as_str()).unwrap_or("");
                match role {
                    "user" => vec![], // Echo of user input — ignore
                    "assistant" => {
                        let is_delta = msg.get("delta").and_then(|v| v.as_bool()).unwrap_or(false);
                        let content = msg
                            .get("content")
                            .and_then(|v| v.as_str())
                            .map(String::from);

                        if is_delta {
                            vec![StreamEvent::Partial { content, raw: None }]
                        } else {
                            vec![StreamEvent::Text { content, raw: None }]
                        }
                    }
                    _ => vec![],
                }
            }

            "tool_use" => {
                let tool_name = msg
                    .get("tool_name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown");
                vec![StreamEvent::ToolUse {
                    tool: Some(Self::normalize_tool_name(tool_name).to_string()),
                    input: msg.get("parameters").cloned(),
                    message: None,
                    raw: None,
                }]
            }

            "tool_result" => {
                let status = msg.get("status").and_then(|v| v.as_str()).unwrap_or("");
                if status == "error" {
                    let err_msg = msg
                        .get("error")
                        .and_then(|v| v.get("message"))
                        .and_then(|v| v.as_str())
                        .or_else(|| msg.get("output").and_then(|v| v.as_str()))
                        .unwrap_or("Tool execution failed");
                    vec![StreamEvent::Error {
                        message: err_msg.to_string(),
                    }]
                } else {
                    // Extract tool name from tool_id if possible
                    let tool_id = msg
                        .get("tool_id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("tool");
                    // tool_id often contains the tool name as prefix
                    let tool_name = tool_id.split('_').next().unwrap_or(tool_id);
                    vec![StreamEvent::ToolUse {
                        tool: Some(Self::normalize_tool_name(tool_name).to_string()),
                        input: None,
                        message: msg.get("output").and_then(|v| v.as_str()).map(String::from),
                        raw: None,
                    }]
                }
            }

            "error" => vec![StreamEvent::Error {
                message: msg
                    .get("message")
                    .and_then(|v| v.as_str())
                    .unwrap_or("Unknown error")
                    .to_string(),
            }],

            "result" => {
                let status = msg.get("status").and_then(|v| v.as_str()).unwrap_or("");
                if status == "error" {
                    let err_msg = msg
                        .get("error")
                        .and_then(|v| v.get("message"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("Session ended with error");
                    vec![StreamEvent::Error {
                        message: err_msg.to_string(),
                    }]
                } else {
                    let sid = self.session_id.take();
                    vec![StreamEvent::Result {
                        session_id: sid,
                        raw: Some(msg),
                    }]
                }
            }

            _ => vec![], // Unknown event types → drop
        }
    }

    fn extract_session_id(&self, line: &str) -> Option<String> {
        let msg: Value = serde_json::from_str(line).ok()?;
        if msg.get("type")?.as_str()? == "init" {
            return msg.get("session_id")?.as_str().map(String::from);
        }
        None
    }

    fn instructions_file(&self) -> &str {
        "GEMINI.md"
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

    fn adapter() -> GeminiAdapter {
        GeminiAdapter::new()
    }

    #[test]
    fn test_gemini_init_event() {
        let a = adapter();
        let line = r#"{"type":"init","timestamp":"2026-02-21T00:51:38.138Z","session_id":"70272ea8-4083-4590-ba02-242d377fa77b","model":"auto-gemini-3"}"#;
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
                assert_eq!(
                    session_id.as_deref(),
                    Some("70272ea8-4083-4590-ba02-242d377fa77b")
                );
                assert_eq!(model.as_deref(), Some("auto-gemini-3"));
            }
            _ => panic!("Expected System event"),
        }
    }

    #[test]
    fn test_gemini_user_message_ignored() {
        let a = adapter();
        let line =
            r#"{"type":"message","timestamp":"...","role":"user","content":"create a folder"}"#;
        let events = a.parse_output_line(line);
        assert!(events.is_empty());
    }

    #[test]
    fn test_gemini_assistant_delta() {
        let a = adapter();
        let line = r#"{"type":"message","timestamp":"...","role":"assistant","content":"I will create","delta":true}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Partial { content, .. } if content.as_deref() == Some("I will create"))
        );
    }

    #[test]
    fn test_gemini_assistant_non_delta() {
        let a = adapter();
        let line = r#"{"type":"message","timestamp":"...","role":"assistant","content":"Complete response here."}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Text { content, .. } if content.as_deref() == Some("Complete response here."))
        );
    }

    #[test]
    fn test_gemini_tool_use() {
        let a = adapter();
        let line = r#"{"type":"tool_use","timestamp":"...","tool_name":"run_shell_command","tool_id":"run_shell_command_171635102963_0","parameters":{"command":"mkdir -p temp"}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, .. } if tool.as_deref() == Some("bash"))
        );
    }

    #[test]
    fn test_gemini_tool_result_success() {
        let a = adapter();
        let line = r#"{"type":"tool_result","timestamp":"...","tool_id":"run_shell_command_171635102963_0","status":"success","output":"created directory"}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        match &events[0] {
            StreamEvent::ToolUse { tool, message, .. } => {
                // tool_id prefix "run" → normalized; we get the first segment
                assert!(tool.is_some());
                assert_eq!(message.as_deref(), Some("created directory"));
            }
            _ => panic!("Expected ToolUse event"),
        }
    }

    #[test]
    fn test_gemini_tool_result_error() {
        let a = adapter();
        let line = r#"{"type":"tool_result","timestamp":"...","tool_id":"t1","status":"error","error":{"message":"permission denied"}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Error { message } if message == "permission denied")
        );
    }

    #[test]
    fn test_gemini_error_event() {
        let a = adapter();
        let line = r#"{"type":"error","message":"API key invalid"}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Error { message } if message == "API key invalid")
        );
    }

    #[test]
    fn test_gemini_result_success() {
        let a = adapter();

        // Set up session_id via init
        let _ = a.parse_output_line(r#"{"type":"init","session_id":"ses_abc","model":"gemini-3"}"#);

        let line = r#"{"type":"result","timestamp":"...","status":"success","stats":{"total_tokens":1234}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Result { session_id, .. } if session_id.as_deref() == Some("ses_abc"))
        );
    }

    #[test]
    fn test_gemini_result_error() {
        let a = adapter();
        let line =
            r#"{"type":"result","status":"error","error":{"message":"Token limit exceeded"}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Error { message } if message == "Token limit exceeded")
        );
    }

    #[test]
    fn test_gemini_tool_name_normalization() {
        assert_eq!(
            GeminiAdapter::normalize_tool_name("run_shell_command"),
            "bash"
        );
        assert_eq!(GeminiAdapter::normalize_tool_name("write_file"), "edit");
        assert_eq!(GeminiAdapter::normalize_tool_name("replace"), "edit");
        assert_eq!(GeminiAdapter::normalize_tool_name("read_file"), "read");
        assert_eq!(
            GeminiAdapter::normalize_tool_name("custom_tool"),
            "custom_tool"
        );
    }

    #[test]
    fn test_gemini_extract_session_id() {
        let a = adapter();
        assert_eq!(
            a.extract_session_id(r#"{"type":"init","session_id":"ses_123","model":"gemini-3"}"#),
            Some("ses_123".to_string())
        );
        assert_eq!(
            a.extract_session_id(r#"{"type":"message","role":"assistant","content":"hi"}"#),
            None
        );
    }

    #[test]
    fn test_gemini_build_command_yolo() {
        let a = adapter();
        let cmd = a.build_command("hello", PermissionMode::Auto, Path::new("/tmp"), None);
        let program = cmd.as_std().get_program().to_str().unwrap();
        assert_eq!(program, "gemini");

        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(args.contains(&"-p"));
        assert!(args.contains(&"hello"));
        assert!(args.contains(&"--output-format"));
        assert!(args.contains(&"stream-json"));
        assert!(args.contains(&"--approval-mode"));
        assert!(args.contains(&"yolo"));
    }

    #[test]
    fn test_gemini_build_command_auto_edit() {
        let a = adapter();
        let cmd = a.build_command("edit", PermissionMode::Code, Path::new("/tmp"), None);
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(args.contains(&"--approval-mode"));
        assert!(args.contains(&"auto_edit"));
    }

    #[test]
    fn test_gemini_build_command_sandbox() {
        let a = adapter();
        let cmd = a.build_command("plan", PermissionMode::Plan, Path::new("/tmp"), None);
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(args.contains(&"--sandbox"));
        assert!(!args.contains(&"--approval-mode"));
    }

    #[test]
    fn test_gemini_build_command_ask() {
        let a = adapter();
        let cmd = a.build_command("ask", PermissionMode::Ask, Path::new("/tmp"), None);
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        // Ask mode = default, no extra flags
        assert!(!args.contains(&"--sandbox"));
        assert!(!args.contains(&"--approval-mode"));
    }

    #[test]
    fn test_gemini_build_resume_command() {
        let a = adapter();
        let cmd = a.build_resume_command(
            "continue",
            PermissionMode::Auto,
            Path::new("/tmp"),
            "ses_abc",
            Some("gemini-3-pro"),
        );
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(args.contains(&"--resume"));
        assert!(args.contains(&"ses_abc"));
        assert!(args.contains(&"--model"));
        assert!(args.contains(&"gemini-3-pro"));
    }

    #[test]
    fn test_gemini_invalid_json() {
        let a = adapter();
        let events = a.parse_output_line("garbage");
        assert!(events.is_empty());
    }

    #[test]
    fn test_gemini_unknown_event() {
        let a = adapter();
        let events = a.parse_output_line(r#"{"type":"unknown_type"}"#);
        assert!(events.is_empty());
    }

    #[test]
    fn test_gemini_assistant_delta_false() {
        let a = adapter();
        let line = r#"{"type":"message","role":"assistant","content":"Full text","delta":false}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Text { content, .. } if content.as_deref() == Some("Full text"))
        );
    }

    #[test]
    fn test_gemini_write_file_normalization() {
        let a = adapter();
        let line = r#"{"type":"tool_use","tool_name":"write_file","tool_id":"wf_1","parameters":{"path":"test.txt"}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, .. } if tool.as_deref() == Some("edit"))
        );
    }
}
