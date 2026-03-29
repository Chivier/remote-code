# RPC Server (server.rs)

**File:** `src/daemon/server.rs`

Axum-based JSON-RPC server that provides the HTTP endpoint for all daemon operations. Handles method routing, SSE streaming for `session.send`, keepalive pings, auth middleware, and graceful shutdown.

## Purpose

- Provide a single `POST /rpc` endpoint for all JSON-RPC methods
- Route requests to method-specific handlers based on the `method` field
- Stream responses for `session.send` via SSE (Server-Sent Events)
- Send keepalive pings to prevent idle SSH tunnel timeouts
- Apply Bearer token auth middleware when the daemon is in auth mode
- Handle graceful shutdown on SIGTERM/SIGINT

## AppState

Shared application state injected into every request handler via Axum's `State` extractor:

```rust
pub struct AppState {
    pub session_pool: SessionPool,
    pub skill_manager: SkillManager,
    pub start_time: Instant,
    pub shutdown: Arc<Notify>,
    pub config: DaemonConfig,
    pub token_store: TokenStore,
}
```

## Router

The router is built by `build_router(state)`:

```rust
pub fn build_router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/rpc", post(handle_rpc))
        .layer(axum::middleware::from_fn_with_state(
            state.clone(),
            crate::auth::auth_middleware,
        ))
        .with_state(state)
}
```

The auth middleware runs on every request. In plain HTTP mode (default for SSH tunnel access), the middleware is a no-op pass-through. In auth mode (HTTPS), it validates the `Authorization: Bearer <token>` header against the token store.

## Method Routing

All requests arrive at `POST /rpc`. The `method` field in the JSON body determines the handler:

| Method | Response Type | Handler |
|---|---|---|
| `session.create` | JSON | `handle_create_session` |
| `session.send` | SSE stream | `handle_send_message` |
| `session.resume` | JSON | `handle_resume_session` |
| `session.destroy` | JSON | `handle_destroy_session` |
| `session.list` | JSON | `handle_list_sessions` |
| `session.set_mode` | JSON | `handle_set_mode` |
| `session.set_model` | JSON | `handle_set_model` |
| `session.interrupt` | JSON | `handle_interrupt_session` |
| `session.queue_stats` | JSON | `handle_queue_stats` |
| `session.reconnect` | JSON | `handle_reconnect` |
| `health.check` | JSON | `handle_health_check` |
| `monitor.sessions` | JSON | `handle_monitor_sessions` |

Missing `method` field returns error `-32600` (Invalid request). Unknown method returns `-32601` (Method not found).

## SSE Streaming (session.send)

`handle_send_message` is the only handler that returns an SSE stream rather than a JSON body. It uses Axum's `Sse` response type:

```rust
// SSE response headers (set by axum::response::sse::Sse):
// Content-Type: text/event-stream
// Cache-Control: no-cache
// Connection: keep-alive
// X-Accel-Buffering: no   (disables nginx response buffering)
```

The handler spawns a `session_pool.send()` task. Events from the task are forwarded onto an mpsc channel. A `ReceiverStream` wraps the channel receiver and drives the SSE response.

### Keepalive Pings

A keepalive task runs alongside the stream, sending a `Ping {}` event every 30 seconds:

```rust
// Every 30 seconds:
StreamEvent::Ping {}
// Serializes to: data: {"type":"ping"}\n\n
```

These prevent idle SSH tunnel timeouts and proxy connection closures.

### Client Disconnect Handling

Axum detects client disconnection when the SSE stream's `Sink` reports an error. The keepalive task is cancelled. If a disconnect is detected while the CLI is still running, the session pool's `client_disconnect()` method is called to start buffering subsequent events. These buffered events can be retrieved later via `session.reconnect`.

### Stream Termination

The stream ends with `data: [DONE]\n\n` after the terminal event (`result`, `error`, or `interrupted`) has been sent.

## Method Handlers

### `handle_create_session`

1. Validates the required `path` param
2. Optionally reads `mode`, `model`, and `cli_type` params
3. Calls `skill_manager.sync_to_project(path, cli_type)` to copy shared skills
4. Calls `session_pool.create(path, mode, model, cli_type)`
5. Returns `{ "sessionId": "uuid" }`

### `handle_send_message`

See SSE Streaming section above. Params: `sessionId` (required), `message` (required).

### `handle_resume_session`

Delegates to `session_pool.resume(session_id, sdk_session_id)`. Returns `{ "ok": true, "fallback": false }`.

### `handle_destroy_session`

Delegates to `session_pool.destroy(session_id)`. Returns `{ "ok": true }`.

### `handle_list_sessions`

Returns `{ "sessions": [...] }` with `SessionInfo` for all sessions.

### `handle_set_mode`

Delegates to `session_pool.set_mode(session_id, mode)`. Returns `{ "ok": true }`.

### `handle_set_model`

Delegates to `session_pool.set_model(session_id, model)`. Returns `{ "ok": true }`.

### `handle_interrupt_session`

Delegates to `session_pool.interrupt(session_id)`. Returns `{ "ok": true, "interrupted": bool }`.

### `handle_queue_stats`

Returns `QueueStats` for a session: `{ "userPending": N, "responsePending": N, "clientConnected": bool }`.

### `handle_reconnect`

Calls `session_pool.client_reconnect(session_id)` and returns any buffered events. Returns `{ "bufferedEvents": [...] }`.

### `handle_health_check`

Returns daemon health info:

```json
{
    "ok": true,
    "sessions": 3,
    "sessionsByStatus": { "idle": 2, "busy": 1 },
    "uptime": 3600,
    "memory": { "rss": 45, "heapUsed": 20, "heapTotal": 30 },
    "rustVersion": "1.78.0",
    "pid": 12345
}
```

Memory values are in megabytes. `rustVersion` reports the Rust toolchain version.

### `handle_monitor_sessions`

Returns detailed session info including per-session queue stats:

```json
{
    "sessions": [
        {
            "sessionId": "...",
            "path": "/home/user/project",
            "status": "busy",
            "mode": "auto",
            "cliType": "claude",
            "model": "claude-sonnet-4-20250514",
            "sdkSessionId": "sdk-uuid",
            "createdAt": "2026-03-29T10:00:00Z",
            "lastActivityAt": "2026-03-29T10:05:00Z",
            "queue": {
                "userPending": 1,
                "responsePending": 0,
                "clientConnected": true
            }
        }
    ],
    "totalSessions": 1,
    "uptime": 3600
}
```

## JSON-RPC Helpers

`RpcResponse` is defined in `types.rs` with two constructors:

```rust
RpcResponse::success(result: Value, id: Option<String>) -> RpcResponse
RpcResponse::error(code: i32, message: impl Into<String>, id: Option<String>) -> RpcResponse
```

Standard error codes:
- `-32600`: Invalid request (missing `method`)
- `-32601`: Method not found
- `-32602`: Invalid params (missing required params)
- `-32000`: Internal/application error (session not found, path invalid, etc.)

## Connection to Other Modules

- Uses **SessionPool** for all session lifecycle operations
- Uses **SkillManager** for skills sync on `session.create`
- Uses **TokenStore** via `auth_middleware` for request authentication
- Imports types from **types.rs** for request/response typing
