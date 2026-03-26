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

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PermissionMode {
    #[default]
    Auto,
    Code,
    Plan,
    Ask,
}

impl PermissionMode {
    /// Convert permission mode to Claude CLI flags (used by ClaudeAdapter)
    pub fn to_claude_flags(self) -> Vec<&'static str> {
        match self {
            PermissionMode::Auto => vec!["--dangerously-skip-permissions"],
            PermissionMode::Code => vec![],
            PermissionMode::Plan => vec![],
            PermissionMode::Ask => vec![],
        }
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
    pub cli_type: String,
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

// Note: convert_claude_message() has been moved to cli_adapter::claude module.
// Each CLI adapter now implements its own parse_output_line() method.
