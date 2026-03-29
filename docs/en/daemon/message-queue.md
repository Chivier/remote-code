# Message Queue (message_queue.rs)

**File:** `src/daemon/message_queue.rs`

Per-session message queue with three responsibilities: buffering user messages when the CLI is busy, buffering response events when the SSH connection drops, and tracking client connection state.

## Purpose

- **User message buffering**: When Claude is processing a message, additional user messages are queued and processed in order after the current message completes.
- **Response buffering**: When the SSH connection (and thus the SSE stream) drops mid-response, events are buffered and can be replayed when the client reconnects via `session.reconnect`.
- **Client connection tracking**: Tracks whether the Head Node client is currently connected so the system knows whether to buffer response events.

## Struct: MessageQueue

```rust
pub struct MessageQueue {
    user_pending: VecDeque<QueuedUserMessage>,
    response_pending: VecDeque<QueuedResponse>,
    client_connected: bool,  // Starts as true
}
```

Each session has its own `MessageQueue` instance, created when the session is created in `SessionPool::create()`.

## Data Types

### QueuedUserMessage

```rust
pub struct QueuedUserMessage {
    pub message: String,
    pub timestamp: u64,  // Unix milliseconds
}
```

### QueuedResponse (private)

```rust
struct QueuedResponse {
    event: StreamEvent,
    timestamp: u64,  // Unix milliseconds
}
```

## User Message Buffering

### `enqueue_user(message: String) -> usize`

Adds a user message to the back of the queue. Returns the new queue length (used as the queue position in the `Queued` event sent back to the user). Called by `SessionPool::send()` when the session is busy.

### `dequeue_user() -> Option<QueuedUserMessage>`

Removes and returns the next user message from the front of the queue. Returns `None` if empty. Called by `SessionPool` after completing a message to auto-process the next one.

### `has_user_pending() -> bool`

Returns `true` if there are queued user messages. Used by `SessionPool` to decide whether to call `process_queued_message()` after a message completes.

## Response Buffering

### `buffer_response(event: StreamEvent, force: bool)`

Buffers a response event. When `force` is `false` (normal path), events are only buffered when `client_connected` is `false`. When `force` is `true`, the event is always buffered — used by `server.rs` when it detects an SSE client disconnect before the session pool has been notified.

### `on_client_reconnect() -> Vec<StreamEvent>`

Marks the client as reconnected and returns all buffered response events (draining the queue). Called by the `session.reconnect` RPC handler. The caller sends the returned events back to the newly reconnected client.

## Client Connection State

### `is_client_connected() -> bool`

Returns the current connection state.

### `on_client_disconnect()`

Sets `client_connected = false`. After this call, response events will be buffered via `buffer_response()` rather than discarded.

## Cleanup

### `clear()`

Clears both the user message queue and the response buffer. Called when a session is destroyed or interrupted.

### `stats() -> QueueStats`

Returns a `QueueStats` snapshot for monitoring:

```rust
pub struct QueueStats {
    pub user_pending: usize,
    pub response_pending: usize,
    pub client_connected: bool,
}
```

Used by the `/status` and `/monitor` commands to report queue depth.

## Flow Example

```
User sends msg1  →  SessionPool: start processing msg1

User sends msg2  →  queue.enqueue_user("msg2") → returns position 1
                    SessionPool: yields Queued { position: 1 } to client
                    (msg2 sits in queue while msg1 processes)

User sends msg3  →  queue.enqueue_user("msg3") → returns position 2
                    SessionPool: yields Queued { position: 2 } to client

SSH drops        →  server.rs detects SSE close event
                    queue.on_client_disconnect()
                    (subsequent response events buffered via buffer_response(event, false))

msg1 completes   →  SessionPool: dequeue_user() → returns msg2
                    SessionPool: start processing msg2 (events buffered since disconnected)

msg2 completes   →  SessionPool: dequeue_user() → returns msg3
                    SessionPool: start processing msg3

SSH reconnects   →  client sends session.reconnect RPC
                    queue.on_client_reconnect() → returns buffered events for msg2
                    server.rs sends buffered events to client
                    client is now back in sync; msg3 is still processing
```

## Connection to Other Modules

- **session_pool.rs** creates one `MessageQueue` per session and calls its methods for message queuing and client state management
- **server.rs** calls `queue.on_client_disconnect()` when the SSE connection closes, and uses the `session.reconnect` handler to call `queue.on_client_reconnect()`
- Imports `StreamEvent` and `QueueStats` from **types.rs**
