# Session Pool (session_pool.rs)

**File:** `src/daemon/session_pool.rs`

Manages CLI sessions using a per-message spawn architecture. Each user message spawns a fresh CLI subprocess via the appropriate `CliAdapter`, maintaining conversation continuity via session resume flags (`--resume` for Claude, equivalent for other CLIs).

## Purpose

- Maintain a registry of session metadata (path, mode, CLI type, status, SDK session ID)
- Spawn CLI subprocesses for individual messages via the `CliAdapter` trait
- Convert CLI stdout JSON-lines to `StreamEvent` values
- Handle message queuing when the CLI is busy
- Manage process lifecycle (spawn, monitor, interrupt, kill)
- Track client connection state for response buffering

## Architecture: Per-Message Spawn

Rather than keeping a long-running CLI process with stdin open, the SessionPool spawns a fresh process for each message:

```
# First message (no session ID yet):
claude --print "user message" --output-format stream-json --verbose \
       [--dangerously-skip-permissions]

# Subsequent messages (using --resume to continue conversation):
claude --print "user message" --output-format stream-json --verbose \
       --resume <sdkSessionId> \
       [--dangerously-skip-permissions]
```

The `CliAdapter` trait abstracts this pattern across all supported CLIs. Each adapter implements `build_command()` for the first message and `build_resume_command()` for subsequent messages.

A **fresh adapter instance** is created for each `run_cli_process()` call via `create_adapter()`. This ensures any per-run state (such as cumulative text tracking) is reset cleanly between message turns.

## Internal Session State

```rust
struct InternalSession {
    session_id: String,
    path: String,
    mode: PermissionMode,
    cli_type: String,
    status: SessionStatus,
    sdk_session_id: Option<String>,
    created_at: DateTime<Utc>,
    last_activity_at: DateTime<Utc>,
    process: Option<Child>,       // Running CLI process (only during processing)
    queue: MessageQueue,          // Per-session message + response queue
    processing: bool,             // Whether a message is currently being processed
    model: Option<String>,        // Model name reported by CLI init event
}
```

The `sessions` map is wrapped in `Arc<Mutex<HashMap<String, InternalSession>>>` for async-safe access.

## Key Methods

### `create(path, mode, model, cli_type) -> Result<String, String>`

Creates a new session entry. **Lightweight — no CLI process is spawned.**

1. Resolves the path: expands `~`, and expands bare project names to `~/Projects/<name>`
2. Validates that the resolved path exists on the filesystem
3. Generates a UUID for the session ID
4. Inserts an `InternalSession` with `status: Idle`, no process, and a fresh `MessageQueue`
5. Returns the session ID

### `send(session_id, message) -> mpsc::Receiver<StreamEvent>`

Sends a message to a session. Returns a channel receiver that yields stream events.

**If the session is busy** (another message in flight):
- Enqueues the message via `queue.enqueue_user()`
- Sends a single `Queued { position }` event on the receiver
- Returns immediately

**If the session is idle:**
- Sets `status: Busy` and `processing: true`
- Spawns a tokio task that calls `run_cli_process()`
- The task forwards events onto the mpsc channel

### `run_cli_process(session_id, message) -> impl Stream<Item = StreamEvent>`

Internal method that spawns the CLI subprocess and yields events.

**Adapter selection and command building:**

```rust
let adapter = create_adapter(&session.cli_type);
let command = if let Some(sdk_id) = &session.sdk_session_id {
    adapter.build_resume_command(message, mode, cwd, sdk_id, model)
} else {
    adapter.build_command(message, mode, cwd, model)
};
```

**Process spawn:**

```rust
let mut child = command
    .current_dir(&session.path)
    .stdin(Stdio::null())       // --print mode: stdin not needed
    .stdout(Stdio::piped())
    .stderr(Stdio::piped())
    .spawn()?;
```

Stdin is set to null because `--print` mode reads the prompt from CLI arguments.

**Output processing:**

stdout is read line-by-line using `tokio::io::BufReader` and `AsyncBufReadExt::lines()`. Each line is passed to `adapter.parse_output_line()`, which returns zero or more `StreamEvent` values. Events are forwarded onto the mpsc channel.

stderr is logged at the level specified by `adapter.stderr_log_level()` (typically `debug` for Claude, `warn` for others).

**Session ID extraction:**

On the first line of output, `adapter.extract_session_id()` is called. If an ID is found, it is stored as `session.sdk_session_id` for future `build_resume_command()` calls.

**Model name capture:**

`System { subtype: Some("init"), model, .. }` events are used to update `session.model`.

**Terminal events:**

The generator stops forwarding events after receiving a `Result`, `Error`, or `Interrupted` event. The subprocess is awaited and then cleaned up.

**Cleanup after process exit:**

1. `session.process` is set to `None`
2. `session.processing` is set to `false`
3. `session.status` is set to `Idle`
4. If the process exited with a non-zero code, an `Error` event is emitted
5. If there are queued user messages, `process_queued_message()` is called to auto-process the next one

### `resume(session_id, sdk_session_id?) -> Result<ResumeResult, String>`

In per-message spawn mode, this simply updates `sdk_session_id` so the next `send()` uses the resume command. Also calls `queue.on_client_reconnect()` to mark the client as reconnected.

Returns `{ ok: true, fallback: false }`.

### `destroy(session_id) -> bool`

1. Sends SIGTERM to any running CLI process
2. Waits up to 5 seconds for the process to exit; sends SIGKILL if it does not
3. Sets `status: Destroyed`
4. Clears the message queue
5. Removes the session from the pool

### `set_mode(session_id, mode) -> bool`

Updates `session.mode`. Takes effect on the next `send()` call since the mode is passed to `adapter.build_command()`.

### `set_model(session_id, model) -> bool`

Updates `session.model`. Takes effect on the next `send()` call.

### `interrupt(session_id) -> Result<bool, String>`

1. Sends SIGTERM to the running CLI process
2. Clears the message queue (cancels any pending messages)
3. Returns `true` if there was an active process to interrupt, `false` if the session was idle

### `list_sessions() -> Vec<SessionInfo>`

Returns `SessionInfo` for all non-destroyed sessions. `SessionInfo` is a serializable snapshot (dates as ISO 8601 strings, no runtime-only fields).

### `client_disconnect(session_id)` / `client_reconnect(session_id) -> Vec<StreamEvent>`

Proxy methods for `MessageQueue` client state management. Called by `server.rs` when the SSE connection drops or is re-established.

### `get_queue_stats(session_id) -> QueueStats`

Returns `QueueStats { user_pending, response_pending, client_connected }` for a session.

### `destroy_all()`

Destroys all sessions. Called during daemon shutdown.

## Connection to Other Modules

- **server.rs** creates a single `SessionPool` instance (inside `AppState`) and calls its methods for all session-related RPC handlers
- Uses **cli_adapter** via `create_adapter()` to build and parse CLI subprocesses
- Uses **MessageQueue** for per-session message buffering and client state tracking
- Imports types from **types.rs** (`SessionStatus`, `PermissionMode`, `StreamEvent`, `SessionInfo`, `QueueStats`)
