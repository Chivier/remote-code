//! Claude CLI adapter — extracted from the original session_pool.rs + types.rs.
//!
//! Stateless adapter. Claude CLI outputs stream-json with distinct event types
//! (system, assistant, stream_event, tool_progress, result, user).

use std::path::Path;

use serde_json::Value;
use tokio::process::Command;

use super::CliAdapter;
use crate::types::{PermissionMode, StreamEvent};

/// Claude CLI adapter (stateless — no interior mutability needed).
pub struct ClaudeAdapter;

impl CliAdapter for ClaudeAdapter {
    fn name(&self) -> &str {
        "claude"
    }

    fn build_command(
        &self,
        message: &str,
        mode: PermissionMode,
        cwd: &Path,
        model: Option<&str>,
    ) -> Command {
        let mut cmd = Command::new("claude");
        cmd.args([
            "--print",
            message,
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--verbose",
        ]);

        for flag in mode.to_claude_flags() {
            cmd.arg(flag);
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
        convert_claude_message(&msg)
    }

    fn extract_session_id(&self, line: &str) -> Option<String> {
        let msg: Value = serde_json::from_str(line).ok()?;
        if msg.get("type")?.as_str()? == "system" {
            return msg.get("session_id")?.as_str().map(String::from);
        }
        None
    }

    fn instructions_file(&self) -> &str {
        "CLAUDE.md"
    }

    fn skills_dir(&self) -> Option<&str> {
        Some(".claude/skills/")
    }

    fn stderr_log_level(&self) -> tracing::Level {
        tracing::Level::ERROR
    }
}

/// Convert raw Claude CLI stdout JSON into our StreamEvent(s).
///
/// Returns a Vec because an `assistant` message may contain multiple tool_use
/// blocks that each need their own event, plus a text block.
pub fn convert_claude_message(msg: &Value) -> Vec<StreamEvent> {
    let msg_type = msg.get("type").and_then(|v| v.as_str()).unwrap_or("");

    match msg_type {
        "system" => vec![StreamEvent::System {
            subtype: msg
                .get("subtype")
                .and_then(|v| v.as_str())
                .map(String::from),
            session_id: msg
                .get("session_id")
                .and_then(|v| v.as_str())
                .map(String::from),
            model: msg.get("model").and_then(|v| v.as_str()).map(String::from),
            raw: Some(msg.clone()),
        }],

        "assistant" => {
            let mut events = Vec::new();

            if let Some(content) = msg
                .get("message")
                .and_then(|m| m.get("content"))
                .and_then(|c| c.as_array())
            {
                // Emit ALL tool_use blocks (not just the first)
                for block in content.iter() {
                    if block.get("type").and_then(|t| t.as_str()) == Some("tool_use") {
                        events.push(StreamEvent::ToolUse {
                            tool: block.get("name").and_then(|v| v.as_str()).map(String::from),
                            input: block.get("input").cloned(),
                            message: None,
                            raw: None,
                        });
                    }
                }

                // Then emit text blocks
                let text_blocks: Vec<&Value> = content
                    .iter()
                    .filter(|b| b.get("type").and_then(|t| t.as_str()) == Some("text"))
                    .collect();

                if !text_blocks.is_empty() {
                    let text: String = text_blocks
                        .iter()
                        .filter_map(|b| b.get("text").and_then(|t| t.as_str()))
                        .collect::<Vec<_>>()
                        .join("");
                    events.push(StreamEvent::Text {
                        content: Some(text),
                        raw: None,
                    });
                }
            }

            if events.is_empty() {
                // Fallback: empty text event
                events.push(StreamEvent::Text {
                    content: Some(String::new()),
                    raw: None,
                });
            }

            events
        }

        "stream_event" => {
            if let Some(event) = msg.get("event") {
                let event_type = event.get("type").and_then(|v| v.as_str()).unwrap_or("");

                match event_type {
                    "content_block_delta" => {
                        if let Some(delta) = event.get("delta") {
                            // Text streaming deltas → forward as Partial
                            if let Some(text) = delta.get("text").and_then(|v| v.as_str()) {
                                return vec![StreamEvent::Partial {
                                    content: Some(text.to_string()),
                                    raw: None,
                                }];
                            }
                            // partial_json (tool input streaming) → drop, it's noise
                            if delta.get("partial_json").is_some() {
                                return vec![];
                            }
                        }
                        vec![]
                    }

                    "content_block_start" => {
                        if let Some(cb) = event.get("content_block") {
                            if cb.get("type").and_then(|v| v.as_str()) == Some("tool_use") {
                                return vec![StreamEvent::ToolUse {
                                    tool: cb.get("name").and_then(|v| v.as_str()).map(String::from),
                                    input: None,
                                    message: None,
                                    raw: None,
                                }];
                            }
                        }
                        // content_block_start for text → ignore (text comes via deltas)
                        vec![]
                    }

                    // Internal lifecycle events → drop
                    "content_block_stop" | "message_start" | "message_stop" | "message_delta" => {
                        vec![]
                    }

                    _ => vec![], // Unknown stream_event subtypes → drop
                }
            } else {
                vec![]
            }
        }

        // Tool results from user messages are internal — don't forward
        "user" => vec![],

        "tool_progress" => vec![StreamEvent::ToolUse {
            tool: msg
                .get("tool_name")
                .and_then(|v| v.as_str())
                .map(String::from),
            input: None,
            message: msg.get("status").and_then(|v| v.as_str()).map(String::from),
            raw: Some(msg.clone()),
        }],

        "result" => vec![StreamEvent::Result {
            session_id: msg
                .get("session_id")
                .and_then(|v| v.as_str())
                .map(String::from),
            raw: Some(msg.clone()),
        }],

        // Unknown types → drop
        _ => vec![],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_claude_system_event() {
        let adapter = ClaudeAdapter;
        let line = r#"{"type":"system","subtype":"init","session_id":"ses_abc123","model":"claude-sonnet-4-20250514"}"#;
        let events = adapter.parse_output_line(line);
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
    fn test_claude_assistant_text_and_tool() {
        let msg = json!({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"path": "/tmp/test.rs"}},
                    {"type": "text", "text": "Here is the file content."}
                ]
            }
        });
        let events = convert_claude_message(&msg);
        assert_eq!(events.len(), 2);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, .. } if tool.as_deref() == Some("Read"))
        );
        assert!(
            matches!(&events[1], StreamEvent::Text { content, .. } if content.as_deref() == Some("Here is the file content."))
        );
    }

    #[test]
    fn test_claude_stream_event_delta() {
        let line = r#"{"type":"stream_event","event":{"type":"content_block_delta","delta":{"text":"Hello "}}}"#;
        let adapter = ClaudeAdapter;
        let events = adapter.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Partial { content, .. } if content.as_deref() == Some("Hello "))
        );
    }

    #[test]
    fn test_claude_stream_event_tool_start() {
        let line = r#"{"type":"stream_event","event":{"type":"content_block_start","content_block":{"type":"tool_use","name":"Bash"}}}"#;
        let adapter = ClaudeAdapter;
        let events = adapter.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, .. } if tool.as_deref() == Some("Bash"))
        );
    }

    #[test]
    fn test_claude_result_event() {
        let line = r#"{"type":"result","session_id":"ses_xyz789"}"#;
        let adapter = ClaudeAdapter;
        let events = adapter.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Result { session_id, .. } if session_id.as_deref() == Some("ses_xyz789"))
        );
    }

    #[test]
    fn test_claude_user_event_ignored() {
        let line = r#"{"type":"user","message":{"content":[{"type":"text","text":"hi"}]}}"#;
        let adapter = ClaudeAdapter;
        let events = adapter.parse_output_line(line);
        assert!(events.is_empty());
    }

    #[test]
    fn test_claude_tool_progress() {
        let line = r#"{"type":"tool_progress","tool_name":"Bash","status":"Running command..."}"#;
        let adapter = ClaudeAdapter;
        let events = adapter.parse_output_line(line);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, message, .. }
            if tool.as_deref() == Some("Bash") && message.as_deref() == Some("Running command..."))
        );
    }

    #[test]
    fn test_claude_extract_session_id() {
        let adapter = ClaudeAdapter;
        let line = r#"{"type":"system","subtype":"init","session_id":"ses_abc123","model":"claude-sonnet-4-20250514"}"#;
        assert_eq!(
            adapter.extract_session_id(line),
            Some("ses_abc123".to_string())
        );

        // Non-system line
        let line2 = r#"{"type":"result","session_id":"ses_abc123"}"#;
        assert_eq!(adapter.extract_session_id(line2), None);
    }

    #[test]
    fn test_claude_partial_json_dropped() {
        let line = r#"{"type":"stream_event","event":{"type":"content_block_delta","delta":{"partial_json":"{\"path\":"}}}"#;
        let adapter = ClaudeAdapter;
        let events = adapter.parse_output_line(line);
        assert!(events.is_empty());
    }

    #[test]
    fn test_claude_lifecycle_events_dropped() {
        for event_type in &[
            "content_block_stop",
            "message_start",
            "message_stop",
            "message_delta",
        ] {
            let line = format!(
                r#"{{"type":"stream_event","event":{{"type":"{}"}}}}"#,
                event_type
            );
            let adapter = ClaudeAdapter;
            let events = adapter.parse_output_line(&line);
            assert!(events.is_empty(), "Expected {} to be dropped", event_type);
        }
    }

    #[test]
    fn test_claude_invalid_json() {
        let adapter = ClaudeAdapter;
        let events = adapter.parse_output_line("not json at all");
        assert!(events.is_empty());
    }

    #[test]
    fn test_claude_empty_assistant() {
        let msg = json!({
            "type": "assistant",
            "message": {
                "content": []
            }
        });
        let events = convert_claude_message(&msg);
        assert_eq!(events.len(), 1);
        assert!(
            matches!(&events[0], StreamEvent::Text { content, .. } if content.as_deref() == Some(""))
        );
    }

    #[test]
    fn test_claude_multiple_tool_use() {
        let msg = json!({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"path": "a.rs"}},
                    {"type": "tool_use", "name": "Edit", "input": {"path": "b.rs"}},
                    {"type": "text", "text": "Done editing."}
                ]
            }
        });
        let events = convert_claude_message(&msg);
        assert_eq!(events.len(), 3);
        assert!(
            matches!(&events[0], StreamEvent::ToolUse { tool, .. } if tool.as_deref() == Some("Read"))
        );
        assert!(
            matches!(&events[1], StreamEvent::ToolUse { tool, .. } if tool.as_deref() == Some("Edit"))
        );
        assert!(
            matches!(&events[2], StreamEvent::Text { content, .. } if content.as_deref() == Some("Done editing."))
        );
    }

    #[test]
    fn test_claude_build_command() {
        let adapter = ClaudeAdapter;
        let cmd = adapter.build_command("hello", PermissionMode::Auto, Path::new("/tmp"), None);
        let program = cmd.as_std().get_program().to_str().unwrap();
        assert_eq!(program, "claude");

        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(args.contains(&"--print"));
        assert!(args.contains(&"hello"));
        assert!(args.contains(&"--output-format"));
        assert!(args.contains(&"stream-json"));
        assert!(args.contains(&"--dangerously-skip-permissions"));
    }

    #[test]
    fn test_claude_build_resume_command() {
        let adapter = ClaudeAdapter;
        let cmd = adapter.build_resume_command(
            "follow up",
            PermissionMode::Code,
            Path::new("/tmp"),
            "ses_abc",
            Some("claude-sonnet-4-20250514"),
        );
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(args.contains(&"--resume"));
        assert!(args.contains(&"ses_abc"));
        assert!(args.contains(&"--model"));
        assert!(args.contains(&"claude-sonnet-4-20250514"));
    }

    #[test]
    fn test_claude_permission_modes() {
        let adapter = ClaudeAdapter;

        // Auto mode
        let cmd = adapter.build_command("msg", PermissionMode::Auto, Path::new("/tmp"), None);
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(args.contains(&"--dangerously-skip-permissions"));

        // Code mode — no special flag
        let cmd = adapter.build_command("msg", PermissionMode::Code, Path::new("/tmp"), None);
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(!args.contains(&"--dangerously-skip-permissions"));

        // Plan mode — no special flag
        let cmd = adapter.build_command("msg", PermissionMode::Plan, Path::new("/tmp"), None);
        let args: Vec<&str> = cmd
            .as_std()
            .get_args()
            .map(|a| a.to_str().unwrap())
            .collect();
        assert!(!args.contains(&"--dangerously-skip-permissions"));
    }
}
