//! OpenCode CLI adapter.
//!
//! OpenCode uses `opencode run` subcommand with `--format json` and `--quiet`.
//! Stateful for session_id capture from `step_start` event.
//!
//! Event types: step_start, message.part.updated, text, step_finish, error

use std::cell::Cell;
use std::path::Path;

use serde_json::Value;
use tokio::process::Command;

use super::CliAdapter;
use crate::types::{PermissionMode, StreamEvent};

/// OpenCode CLI adapter.
pub struct OpenCodeAdapter {
    /// Captures sessionID from step_start for the final Result.
    session_id: Cell<Option<String>>,
}

impl OpenCodeAdapter {
    pub fn new() -> Self {
        Self {
            session_id: Cell::new(None),
        }
    }

    /// Normalize OpenCode tool names to canonical names.
    /// OpenCode uses Claude-compatible tool names (Read, Edit, Write, Bash, etc.)
    fn normalize_tool_name(name: &str) -> String {
        match name {
            "Read" => "read".to_string(),
            "Edit" | "Write" | "MultiEdit" => "edit".to_string(),
            "Bash" => "bash".to_string(),
            "Glob" => "glob".to_string(),
            "Grep" => "grep".to_string(),
            "Task" => "subagent".to_string(),
            _ => name.to_lowercase(),
        }
    }
}

// SAFETY: Cell is not Sync, but adapter is created fresh per run_cli_process call
// and only accessed from a single task.
unsafe impl Sync for OpenCodeAdapter {}

impl CliAdapter for OpenCodeAdapter {
    fn name(&self) -> &str {
        "opencode"
    }

    fn build_command(
        &self,
        message: &str,
        mode: PermissionMode,
        cwd: &Path,
        model: Option<&str>,
    ) -> Command {
        let mut cmd = Command::new("opencode");
        cmd.args(["run", message]);
        cmd.args(["--format", "json"]);
        cmd.arg("--quiet");

        match mode {
            PermissionMode::Auto | PermissionMode::Code => {
                cmd.arg("--yolo");
            }
            PermissionMode::Plan => {
                cmd.args(["--agent", "plan"]);
            }
            PermissionMode::Ask => {
                // Default non-interactive behavior
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
        cmd.args(["--session", session_id]);
        cmd
    }

    fn parse_output_line(&self, line: &str) -> Vec<StreamEvent> {
        let msg: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => return vec![],
        };

        let event_type = msg.get("type").and_then(|v| v.as_str()).unwrap_or("");

        match event_type {
            "step_start" => {
                // Capture sessionID
                if let Some(sid) = msg.get("sessionID").and_then(|v| v.as_str()) {
                    self.session_id.set(Some(sid.to_string()));
                }
                vec![StreamEvent::System {
                    subtype: Some("init".to_string()),
                    session_id: msg
                        .get("sessionID")
                        .and_then(|v| v.as_str())
                        .map(String::from),
                    model: msg.get("model").and_then(|v| v.as_str()).map(String::from),
                    raw: Some(msg),
                }]
            }

            "message.part.updated" => {
                let part_type = msg
                    .get("part")
                    .and_then(|p| p.get("type"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");

                match part_type {
                    "text" | "thinking" => {
                        let content = msg
                            .get("part")
                            .and_then(|p| p.get("content"))
                            .and_then(|v| v.as_str())
                            .map(String::from);
                        vec![StreamEvent::Partial { content, raw: None }]
                    }
                    "tool" => {
                        let state = msg
                            .get("part")
                            .and_then(|p| p.get("state"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        let tool_name = msg
                            .get("part")
                            .and_then(|p| p.get("name"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("unknown");

                        match state {
                            "running" => vec![StreamEvent::ToolUse {
                                tool: Some(Self::normalize_tool_name(tool_name)),
                                input: msg.get("part").and_then(|p| p.get("input")).cloned(),
                                message: None,
                                raw: None,
                            }],
                            "completed" => vec![StreamEvent::ToolUse {
                                tool: Some(Self::normalize_tool_name(tool_name)),
                                input: None,
                                message: msg
                                    .get("part")
                                    .and_then(|p| p.get("output"))
                                    .and_then(|v| v.as_str())
                                    .map(String::from),
                                raw: None,
                            }],
                            "error" => vec![StreamEvent::Error {
                                message: msg
                                    .get("part")
                                    .and_then(|p| p.get("error"))
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("Tool execution failed")
                                    .to_string(),
                            }],
                            _ => vec![],
                        }
                    }
                    _ => vec![],
                }
            }

            "text" => {
                let content = msg
                    .get("content")
                    .and_then(|v| v.as_str())
                    .map(String::from);
                vec![StreamEvent::Text { content, raw: None }]
            }

            "step_finish" => {
                let sid = self.session_id.take();
                vec![StreamEvent::Result {
                    session_id: sid,
                    raw: Some(msg),
                }]
            }

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
        if msg.get("type")?.as_str()? == "step_start" {
            return msg.get("sessionID")?.as_str().map(String::from);
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

    fn adapter() -> OpenCodeAdapter {
        OpenCodeAdapter::new()
    }

    #[test]
    fn test_opencode_step_start() {
        let a = adapter();
        let line =
            r#"{"type":"step_start","sessionID":"ses_abc123","model":"claude-sonnet-4-20250514"}"#;
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
                assert_eq!(session_id.as_deref(), Some("ses_abc123"));
                assert_eq!(model.as_deref(), Some("claude-sonnet-4-20250514"));
            }
            _ => panic!("Expected System event"),
        }
    }

    #[test]
    fn test_opencode_text_partial() {
        let a = adapter();
        let line =
            r#"{"type":"message.part.updated","part":{"type":"text","content":"Hello world"}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Partial { content, .. } if content.as_deref() == Some("Hello world"))
        );
    }

    #[test]
    fn test_opencode_thinking_partial() {
        let a = adapter();
        let line = r#"{"type":"message.part.updated","part":{"type":"thinking","content":"Let me consider..."}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Partial { content, .. } if content.as_deref() == Some("Let me consider..."))
        );
    }

    #[test]
    fn test_opencode_tool_running() {
        let a = adapter();
        let line = r#"{"type":"message.part.updated","part":{"type":"tool","name":"Bash","state":"running","input":{"command":"ls"}}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, .. } if tool.as_deref() == Some("bash"))
        );
    }

    #[test]
    fn test_opencode_tool_completed() {
        let a = adapter();
        let line = r#"{"type":"message.part.updated","part":{"type":"tool","name":"Read","state":"completed","output":"file contents here"}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, message, .. }
            if tool.as_deref() == Some("read") && message.as_deref() == Some("file contents here"))
        );
    }

    #[test]
    fn test_opencode_tool_error() {
        let a = adapter();
        let line = r#"{"type":"message.part.updated","part":{"type":"tool","name":"Bash","state":"error","error":"Command not found"}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Error { message } if message == "Command not found")
        );
    }

    #[test]
    fn test_opencode_text_complete() {
        let a = adapter();
        let line = r#"{"type":"text","content":"Final response text."}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Text { content, .. } if content.as_deref() == Some("Final response text."))
        );
    }

    #[test]
    fn test_opencode_step_finish() {
        let a = adapter();

        // Set up session_id via step_start
        let _ = a.parse_output_line(r#"{"type":"step_start","sessionID":"ses_xyz"}"#);

        let line = r#"{"type":"step_finish","tokens":{"input":500,"output":200}}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Result { session_id, .. } if session_id.as_deref() == Some("ses_xyz"))
        );
    }

    #[test]
    fn test_opencode_error_event() {
        let a = adapter();
        let line = r#"{"type":"error","message":"Session not found"}"#;
        let events = a.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Error { message } if message == "Session not found")
        );
    }

    #[test]
    fn test_opencode_tool_name_normalization() {
        assert_eq!(OpenCodeAdapter::normalize_tool_name("Read"), "read");
        assert_eq!(OpenCodeAdapter::normalize_tool_name("Edit"), "edit");
        assert_eq!(OpenCodeAdapter::normalize_tool_name("Write"), "edit");
        assert_eq!(OpenCodeAdapter::normalize_tool_name("MultiEdit"), "edit");
        assert_eq!(OpenCodeAdapter::normalize_tool_name("Bash"), "bash");
        assert_eq!(OpenCodeAdapter::normalize_tool_name("Glob"), "glob");
        assert_eq!(OpenCodeAdapter::normalize_tool_name("Grep"), "grep");
        assert_eq!(OpenCodeAdapter::normalize_tool_name("Task"), "subagent");
        assert_eq!(
            OpenCodeAdapter::normalize_tool_name("CustomTool"),
            "customtool"
        );
    }

    #[test]
    fn test_opencode_extract_session_id() {
        let a = adapter();
        assert_eq!(
            a.extract_session_id(r#"{"type":"step_start","sessionID":"ses_abc"}"#),
            Some("ses_abc".to_string())
        );
        assert_eq!(a.extract_session_id(r#"{"type":"step_finish"}"#), None);
    }

    #[test]
    fn test_opencode_build_command_yolo() {
        let a = adapter();
        let cmd = a.build_command("hello", PermissionMode::Auto, Path::new("/tmp"), None);
        let program = cmd.as_std().get_program().to_str().unwrap();
        assert_eq!(program, "opencode");

        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert_eq!(args[0], "run");
        assert_eq!(args[1], "hello");
        assert!(args.contains(&"--format"));
        assert!(args.contains(&"json"));
        assert!(args.contains(&"--quiet"));
        assert!(args.contains(&"--yolo"));
    }

    #[test]
    fn test_opencode_build_command_plan() {
        let a = adapter();
        let cmd = a.build_command("analyze", PermissionMode::Plan, Path::new("/tmp"), None);
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(args.contains(&"--agent"));
        assert!(args.contains(&"plan"));
        assert!(!args.contains(&"--yolo"));
    }

    #[test]
    fn test_opencode_build_command_ask() {
        let a = adapter();
        let cmd = a.build_command("check", PermissionMode::Ask, Path::new("/tmp"), None);
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(!args.contains(&"--yolo"));
        assert!(!args.contains(&"--agent"));
    }

    #[test]
    fn test_opencode_build_resume_command() {
        let a = adapter();
        let cmd = a.build_resume_command(
            "continue",
            PermissionMode::Auto,
            Path::new("/tmp"),
            "ses_abc123",
            Some("claude-sonnet-4-20250514"),
        );
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(args.contains(&"--session"));
        assert!(args.contains(&"ses_abc123"));
        assert!(args.contains(&"--model"));
        assert!(args.contains(&"claude-sonnet-4-20250514"));
    }

    #[test]
    fn test_opencode_invalid_json() {
        let a = adapter();
        let events = a.parse_output_line("not valid json");
        assert!(events.is_empty());
    }

    #[test]
    fn test_opencode_unknown_event() {
        let a = adapter();
        let events = a.parse_output_line(r#"{"type":"custom.event"}"#);
        assert!(events.is_empty());
    }

    #[test]
    fn test_opencode_unknown_part_type() {
        let a = adapter();
        let line = r#"{"type":"message.part.updated","part":{"type":"image","content":"..."}}"#;
        let events = a.parse_output_line(line);
        assert!(events.is_empty());
    }

    #[test]
    fn test_opencode_tool_unknown_state() {
        let a = adapter();
        let line = r#"{"type":"message.part.updated","part":{"type":"tool","name":"Bash","state":"pending"}}"#;
        let events = a.parse_output_line(line);
        assert!(events.is_empty());
    }
}
