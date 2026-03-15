use serde::{Deserialize, Serialize};
use serde_json::Value;

// ─── RPC Protocol Types ───

#[derive(Debug, Deserialize)]
pub struct RpcRequest {
    pub method: Option<String>,
    pub params: Option<Value>,
    pub id: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct RpcResponse {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<RpcError>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct RpcError {
    pub code: i32,
    pub message: String,
}

impl RpcResponse {
    pub fn success(result: Value, id: Option<String>) -> Self {
        Self {
            result: Some(result),
            error: None,
            id,
        }
    }

    pub fn error(code: i32, message: impl Into<String>, id: Option<String>) -> Self {
        Self {
            result: None,
            error: Some(RpcError {
                code,
                message: message.into(),
            }),
            id,
        }
    }
}

// ─── Session Types ───

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SessionStatus {
    Idle,
    Busy,
    Error,
    Destroyed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PermissionMode {
    Auto,
    Code,
    Plan,
    Ask,
}

impl PermissionMode {
    /// Convert permission mode to Claude CLI flags
    pub fn to_cli_flags(self) -> Vec<&'static str> {
        match self {
            PermissionMode::Auto => vec!["--dangerously-skip-permissions"],
            PermissionMode::Code => vec![],
            PermissionMode::Plan => vec![],
            PermissionMode::Ask => vec![],
        }
    }
}

impl Default for PermissionMode {
    fn default() -> Self {
        PermissionMode::Auto
    }
}

// ─── Stream Event Types ───

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum StreamEvent {
    Text {
        #[serde(skip_serializing_if = "Option::is_none")]
        content: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        raw: Option<Value>,
    },
    #[serde(rename = "tool_use")]
    ToolUse {
        #[serde(skip_serializing_if = "Option::is_none")]
        tool: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        input: Option<Value>,
        #[serde(skip_serializing_if = "Option::is_none")]
        message: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        raw: Option<Value>,
    },
    Result {
        #[serde(skip_serializing_if = "Option::is_none")]
        session_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        raw: Option<Value>,
    },
    Queued {
        position: usize,
    },
    Error {
        message: String,
    },
    System {
        #[serde(skip_serializing_if = "Option::is_none")]
        subtype: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        session_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        model: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        raw: Option<Value>,
    },
    Partial {
        #[serde(skip_serializing_if = "Option::is_none")]
        content: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        raw: Option<Value>,
    },
    Ping {},
    Interrupted {},
}

impl StreamEvent {
    /// Extract session_id from events that carry one
    pub fn session_id(&self) -> Option<&str> {
        match self {
            StreamEvent::System { session_id, .. } => session_id.as_deref(),
            StreamEvent::Result { session_id, .. } => session_id.as_deref(),
            _ => None,
        }
    }

    /// Check if this is a terminal event
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            StreamEvent::Result { .. } | StreamEvent::Error { .. } | StreamEvent::Interrupted {}
        )
    }
}

// ─── Session Info (for list/monitor responses) ───

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionInfo {
    pub session_id: String,
    pub path: String,
    pub status: SessionStatus,
    pub mode: PermissionMode,
    pub sdk_session_id: Option<String>,
    pub model: Option<String>,
    pub created_at: String,
    pub last_activity_at: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QueueStats {
    pub user_pending: usize,
    pub response_pending: usize,
    pub client_connected: bool,
}

// ─── Claude CLI stdout JSON types ───
// These are raw JSON from `claude --output-format stream-json`

/// Convert raw Claude CLI stdout JSON into our StreamEvent
pub fn convert_claude_message(msg: &Value) -> StreamEvent {
    let msg_type = msg.get("type").and_then(|v| v.as_str()).unwrap_or("");

    match msg_type {
        "system" => StreamEvent::System {
            subtype: msg.get("subtype").and_then(|v| v.as_str()).map(String::from),
            session_id: msg
                .get("session_id")
                .and_then(|v| v.as_str())
                .map(String::from),
            model: msg.get("model").and_then(|v| v.as_str()).map(String::from),
            raw: Some(msg.clone()),
        },

        "assistant" => {
            if let Some(content) = msg.get("message").and_then(|m| m.get("content")).and_then(|c| c.as_array()) {
                // Check for tool_use blocks first
                let tool_blocks: Vec<&Value> = content
                    .iter()
                    .filter(|b| b.get("type").and_then(|t| t.as_str()) == Some("tool_use"))
                    .collect();

                if !tool_blocks.is_empty() {
                    return StreamEvent::ToolUse {
                        tool: tool_blocks[0]
                            .get("name")
                            .and_then(|v| v.as_str())
                            .map(String::from),
                        input: tool_blocks[0].get("input").cloned(),
                        message: None,
                        raw: Some(msg.clone()),
                    };
                }

                // Then check for text blocks
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
                    return StreamEvent::Text {
                        content: Some(text),
                        raw: Some(msg.clone()),
                    };
                }
            }

            StreamEvent::Text {
                content: Some(String::new()),
                raw: Some(msg.clone()),
            }
        }

        "stream_event" => {
            if let Some(event) = msg.get("event") {
                let event_type = event.get("type").and_then(|v| v.as_str()).unwrap_or("");

                if event_type == "content_block_delta" {
                    if let Some(delta) = event.get("delta") {
                        if let Some(text) = delta.get("text").and_then(|v| v.as_str()) {
                            return StreamEvent::Partial {
                                content: Some(text.to_string()),
                                raw: None,
                            };
                        }
                        if let Some(pj) = delta.get("partial_json").and_then(|v| v.as_str()) {
                            return StreamEvent::Partial {
                                content: Some(pj.to_string()),
                                raw: None,
                            };
                        }
                    }
                }

                if event_type == "content_block_start" {
                    if let Some(cb) = event.get("content_block") {
                        if cb.get("type").and_then(|v| v.as_str()) == Some("tool_use") {
                            return StreamEvent::ToolUse {
                                tool: cb.get("name").and_then(|v| v.as_str()).map(String::from),
                                input: None,
                                message: None,
                                raw: Some(msg.clone()),
                            };
                        }
                    }
                }
            }

            StreamEvent::Partial {
                content: Some(String::new()),
                raw: Some(msg.clone()),
            }
        }

        "tool_progress" => StreamEvent::ToolUse {
            tool: msg
                .get("tool_name")
                .and_then(|v| v.as_str())
                .map(String::from),
            input: None,
            message: msg
                .get("status")
                .and_then(|v| v.as_str())
                .map(String::from),
            raw: Some(msg.clone()),
        },

        "result" => StreamEvent::Result {
            session_id: msg
                .get("session_id")
                .and_then(|v| v.as_str())
                .map(String::from),
            raw: Some(msg.clone()),
        },

        _ => StreamEvent::System {
            subtype: None,
            session_id: None,
            model: None,
            raw: Some(msg.clone()),
        },
    }
}
