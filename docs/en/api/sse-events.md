# SSE Stream Events

When the Head Node sends a `session.send` RPC call, the daemon responds with a Server-Sent Events (SSE) stream. This document describes all event types that can appear in the stream.

## SSE Format

Events are sent as `data:` lines with JSON payloads, separated by double newlines:

```
data: {"type":"partial","content":"Hello"}

data: {"type":"text","content":"Hello, world!"}

data: [DONE]
```

The stream ends with `data: [DONE]` (not a JSON payload). The JSON payloads are serialized `StreamEvent` values from `types.rs`, using the `type` field as a tag.

## Terminal Events

Three event types signal the end of a message exchange. The daemon stops sending events after any of these:

| Type | Condition |
|---|---|
| `result` | Claude finished processing successfully |
| `error` | An error occurred (process crash, timeout, etc.) |
| `interrupted` | The operation was interrupted via `session.interrupt` |

---

## Event Types

### `system`

System events provide metadata about the session. The most common is the `init` subtype, sent at the start of each message exchange when Claude starts up.

```json
{
    "type": "system",
    "subtype": "init",
    "session_id": "sdk-session-uuid",
    "model": "claude-sonnet-4-20250514"
}
```

| Field | Type | Description |
|---|---|---|
| `subtype` | string | Event subtype (currently only `"init"`) |
| `session_id` | string | Claude SDK session ID |
| `model` | string | Model name reported by the CLI |
| `raw` | object | Raw CLI JSON message (optional) |

The Head Node uses the `init` event to display a "Connected to **model** | Mode: **mode**" message on the first interaction with a session.

---

### `partial`

Streaming text deltas. These arrive as the CLI generates text, providing real-time output that can be rendered progressively.

```json
{
    "type": "partial",
    "content": "Let me "
}
```

| Field | Type | Description |
|---|---|---|
| `content` | string | Text delta (a few characters to a few words) |

The Head Node accumulates `partial` deltas in a buffer and periodically updates the chat message with the current buffer plus a `▌` cursor indicator. When a complete `text` event arrives, it replaces the accumulated partials.

`partial` events can also carry `partial_json` content during tool use streaming (JSON being assembled incrementally). The Head Node renders these the same way as text partials.

---

### `text`

A complete text block from the CLI. Represents a finished content block in the response.

```json
{
    "type": "text",
    "content": "Here is the complete analysis of your project...",
    "raw": { ... }
}
```

| Field | Type | Description |
|---|---|---|
| `content` | string | Complete text content |
| `raw` | object | Raw CLI message (optional) |

If `partial` events were being accumulated, the `text` event's content replaces the partial buffer. If no partials were sent (e.g., a short response), the text is sent as a new message.

---

### `tool_use`

Indicates the CLI is invoking a tool (file write, bash command, web fetch, etc.).

```json
{
    "type": "tool_use",
    "tool": "Write",
    "input": {
        "file_path": "/home/user/project/README.md",
        "content": "# My Project\n..."
    },
    "raw": { ... }
}
```

With a status message (from tool progress events):

```json
{
    "type": "tool_use",
    "tool": "Bash",
    "message": "Running command...",
    "raw": { ... }
}
```

| Field | Type | Description |
|---|---|---|
| `tool` | string | Tool name (e.g., `Write`, `Bash`, `Read`, `Glob`, `Grep`, `WebFetch`, `AskUserQuestion`) |
| `input` | object | Tool input parameters (optional; present when available) |
| `message` | string | Tool progress status message (optional) |
| `raw` | object | Raw CLI message (optional) |

**Special case: `AskUserQuestion`**

When `tool` is `"AskUserQuestion"`, the `input` field contains a structured question list:

```json
{
    "type": "tool_use",
    "tool": "AskUserQuestion",
    "input": [
        {
            "header": "Which framework should I use?",
            "options": [
                {"description": "FastAPI (async, modern)"},
                {"description": "Flask (simple, synchronous)"}
            ],
            "multiSelect": false
        }
    ]
}
```

The Head Node passes this to `format_ask_user_question()` and then calls `adapter.send_question()` to display platform-native interactive buttons.

---

### `result`

Indicates the CLI has finished processing the message. Contains the SDK session ID needed for conversation continuity.

```json
{
    "type": "result",
    "session_id": "sdk-session-uuid-here",
    "raw": { ... }
}
```

| Field | Type | Description |
|---|---|---|
| `session_id` | string | SDK session ID for `--resume` on next message |
| `raw` | object | Raw result including `duration_ms` and `usage` (optional) |

The Head Node captures `session_id` and stores it via `router.update_sdk_session_id()` for future `--resume` calls.

This is a **terminal event**.

---

### `queued`

Sent immediately when the session is busy and the new message has been queued.

```json
{
    "type": "queued",
    "position": 2
}
```

| Field | Type | Description |
|---|---|---|
| `position` | number | Position in the queue (1-based) |

The Head Node displays: "Message queued (position: 2). Claude is busy with a previous request."

When the queued message is eventually processed, its events will flow through a new SSE stream from the implicit next `session.send` call (initiated by the daemon after the previous message completes). If the client is disconnected at that point, the events are buffered for `session.reconnect`.

---

### `error`

An error occurred during processing.

```json
{
    "type": "error",
    "message": "Claude process exited abnormally (code=1)"
}
```

| Field | Type | Description |
|---|---|---|
| `message` | string | Human-readable error description |

Common error sources:
- CLI process exiting with non-zero code
- CLI process spawn failure (binary not found, permission denied)
- Stream idle timeout (no events for an extended period)
- SSH connection loss detected by the daemon

This is a **terminal event**.

---

### `ping`

Keepalive event sent every 30 seconds to prevent idle SSH tunnel timeouts.

```json
{
    "type": "ping"
}
```

The Head Node ignores these events. They exist solely to keep the HTTP connection alive through SSH tunnels and proxies that close idle connections.

---

### `interrupted`

Sent when the operation was interrupted, either via `session.interrupt` or by an external SIGTERM to the CLI process.

```json
{
    "type": "interrupted"
}
```

This is a **terminal event**.

---

## Event Flow Examples

### Simple Text Response

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514","session_id":"sdk-123"}
data: {"type":"partial","content":"The "}
data: {"type":"partial","content":"answer "}
data: {"type":"partial","content":"is 42."}
data: {"type":"text","content":"The answer is 42."}
data: {"type":"result","session_id":"sdk-123"}
data: [DONE]
```

### Tool Use with Text

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514"}
data: {"type":"partial","content":"Let me check..."}
data: {"type":"tool_use","tool":"Bash","input":{"command":"ls -la"}}
data: {"type":"tool_use","tool":"Bash","message":"Running command..."}
data: {"type":"partial","content":"Here are the files:\n"}
data: {"type":"partial","content":"- src/\n- Cargo.toml"}
data: {"type":"text","content":"Here are the files:\n- src/\n- Cargo.toml"}
data: {"type":"result","session_id":"sdk-456"}
data: [DONE]
```

### AskUserQuestion

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514"}
data: {"type":"partial","content":"I need to clarify a few things."}
data: {"type":"tool_use","tool":"AskUserQuestion","input":[{"header":"Which approach?","options":[{"description":"Option A"},{"description":"Option B"}],"multiSelect":false}]}
data: {"type":"result","session_id":"sdk-789"}
data: [DONE]
```

### Queued Message

```
data: {"type":"queued","position":1}
data: [DONE]
```

### Error During Processing

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514"}
data: {"type":"partial","content":"Let me "}
data: {"type":"error","message":"Claude process exited abnormally (code=1)"}
data: [DONE]
```

### Keepalive During Long Operation

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514"}
data: {"type":"partial","content":"Analyzing..."}
data: {"type":"ping"}
data: {"type":"partial","content":" the codebase structure"}
data: {"type":"ping"}
data: {"type":"tool_use","tool":"Glob","input":{"pattern":"**/*.rs"}}
data: {"type":"text","content":"I found 15 Rust files..."}
data: {"type":"result","session_id":"sdk-789"}
data: [DONE]
```

### Interrupted Operation

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514"}
data: {"type":"partial","content":"Let me analyze this large codebase..."}
data: {"type":"tool_use","tool":"Glob","input":{"pattern":"**/*"}}
data: {"type":"interrupted"}
data: [DONE]
```
