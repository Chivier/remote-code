# Type Definitions (types.rs)

**File:** `src/daemon/types.rs`

Central type definitions for the daemon's RPC protocol, session management, and stream events. All types use serde for JSON serialization/deserialization.

## RPC Protocol Types

### RpcRequest

```rust
#[derive(Debug, Deserialize)]
pub struct RpcRequest {
    pub method: Option<String>,
    pub params: Option<Value>,  // serde_json::Value
    pub id: Option<String>,
}
```

### RpcResponse

```rust
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
```

Constructors:

```rust
RpcResponse::success(result: Value, id: Option<String>) -> RpcResponse
RpcResponse::error(code: i32, message: impl Into<String>, id: Option<String>) -> RpcResponse
```

## Session Types

### SessionStatus

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SessionStatus {
    Idle,
    Busy,
    Error,
    Destroyed,
}
```

| Variant | JSON | Description |
|---|---|---|
| `Idle` | `"idle"` | No CLI process running; ready for messages |
| `Busy` | `"busy"` | A CLI process is currently handling a message |
| `Error` | `"error"` | Session encountered an unrecoverable error |
| `Destroyed` | `"destroyed"` | Session has been destroyed and removed |

### PermissionMode

```rust
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PermissionMode {
    #[default]
    Auto,
    Code,
    Plan,
    Ask,
}
```

| Variant | JSON | Description |
|---|---|---|
| `Auto` | `"auto"` | Full automation; maps to `--dangerously-skip-permissions` for Claude |
| `Code` | `"code"` | Auto-accept file edits; confirm bash (SDK-level, no extra CLI flag) |
| `Plan` | `"plan"` | Read-only analysis (SDK-level, no extra CLI flag) |
| `Ask` | `"ask"` | Confirm all actions (default Claude CLI behavior) |

`PermissionMode` implements `Default` as `Auto`.

#### `to_claude_flags(self) -> Vec<&'static str>`

Maps a `PermissionMode` to Claude CLI flags. Currently only `Auto` has a corresponding flag:

```rust
impl PermissionMode {
    pub fn to_claude_flags(self) -> Vec<&'static str> {
        match self {
            PermissionMode::Auto => vec!["--dangerously-skip-permissions"],
            _ => vec![],
        }
    }
}
```

Each `CliAdapter` implementation decides how to use the mode value — the `ClaudeAdapter` calls `to_claude_flags()`, while other adapters may map modes differently.

## Stream Event Types

`StreamEvent` is a tagged enum serialized with `#[serde(tag = "type", rename_all = "lowercase")]`. The `type` field in JSON identifies the variant.

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum StreamEvent {
    Text {
        content: Option<String>,
        raw: Option<Value>,
    },
    #[serde(rename = "tool_use")]
    ToolUse {
        tool: Option<String>,
        input: Option<Value>,
        message: Option<String>,
        raw: Option<Value>,
    },
    Result {
        session_id: Option<String>,
        raw: Option<Value>,
    },
    Queued {
        position: usize,
    },
    Error {
        message: String,
    },
    System {
        subtype: Option<String>,
        session_id: Option<String>,
        model: Option<String>,
        raw: Option<Value>,
    },
    Partial {
        content: Option<String>,
        raw: Option<Value>,
    },
    Ping {},
    Interrupted {},
}
```

All `Option` fields are skipped in JSON serialization when `None` (`#[serde(skip_serializing_if = "Option::is_none")]`).

### Variant Reference

| Variant | JSON `type` | Terminal? | Description |
|---|---|---|---|
| `Text` | `"text"` | no | Complete text block from Claude |
| `ToolUse` | `"tool_use"` | no | Tool invocation (includes `AskUserQuestion`) |
| `Result` | `"result"` | yes | Message complete; carries SDK session ID |
| `Queued` | `"queued"` | no | Message queued (session busy) |
| `Error` | `"error"` | yes | Error during processing |
| `System` | `"system"` | no | System event (init with model name) |
| `Partial` | `"partial"` | no | Streaming text delta |
| `Ping` | `"ping"` | no | Keepalive (ignored by client) |
| `Interrupted` | `"interrupted"` | yes | Operation interrupted |

#### `is_terminal() -> bool`

```rust
pub fn is_terminal(&self) -> bool {
    matches!(
        self,
        StreamEvent::Result { .. } | StreamEvent::Error { .. } | StreamEvent::Interrupted {}
    )
}
```

#### `session_id() -> Option<&str>`

Extracts the `session_id` field from `System` or `Result` variants. Returns `None` for all other variants.

## Session Info Types

### SessionInfo

Serializable session snapshot used in `session.list` and `monitor.sessions` responses. All `DateTime` fields are serialized as ISO 8601 strings. Uses `camelCase` field names in JSON.

```rust
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionInfo {
    pub session_id: String,
    pub path: String,
    pub status: SessionStatus,
    pub mode: PermissionMode,
    pub cli_type: String,          // "claude", "codex", "gemini", "opencode"
    pub sdk_session_id: Option<String>,
    pub model: Option<String>,
    pub created_at: String,        // ISO 8601
    pub last_activity_at: String,  // ISO 8601
}
```

### QueueStats

```rust
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QueueStats {
    pub user_pending: usize,
    pub response_pending: usize,
    pub client_connected: bool,
}
```

| Field | Description |
|---|---|
| `user_pending` | User messages waiting to be processed (Claude busy) |
| `response_pending` | Response events buffered for SSH reconnect |
| `client_connected` | Whether the Head Node SSE client is currently connected |

## Connection to Other Modules

- **All daemon modules** import types from this file
- **server.rs** uses `RpcRequest`, `RpcResponse`, `PermissionMode`
- **session_pool.rs** uses `SessionStatus`, `PermissionMode`, `StreamEvent`, `SessionInfo`, `QueueStats`
- **message_queue.rs** uses `StreamEvent`, `QueueStats`
- **cli_adapter/mod.rs** and each adapter module use `PermissionMode`, `StreamEvent`
