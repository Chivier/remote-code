# BotEngine (engine.py)

**File:** `src/head/engine.py`

The central command engine for Codecast. `BotEngine` contains all command routing, session management, and message forwarding logic. It uses composition: a `PlatformAdapter` instance handles platform-specific I/O while the engine handles all shared behavior.

This replaces the old `BotBase` ABC inheritance pattern. Instead of subclassing, each platform creates an adapter and passes it to a `BotEngine` instance.

## Purpose

- Route user input to command handlers (`cmd_*` methods) or forward to the active Claude session
- Manage session lifecycle: create, resume, detach, destroy
- Stream responses from the daemon back to chat with configurable display modes
- Handle AskUserQuestion interactive flows
- Manage file forwarding via `FileForwardMatcher`
- Control concurrency: prevent simultaneous streaming to the same channel

## Class: BotEngine

```python
class BotEngine:
    adapter: PlatformAdapter        # Platform-specific I/O
    ssh: SSHManager                 # SSH tunnel management
    router: SessionRouter           # SQLite session registry
    daemon: DaemonClient            # JSON-RPC + SSE client
    config: Config                  # Loaded configuration
    file_pool: Any                  # Optional file pool for file forwarding
    file_forward: FileForwardMatcher | None  # File forwarding rules
    _streaming: set[str]            # Channels currently streaming
    _stop_requested: set[str]       # Channels with a pending stop request
    _init_shown: set[str]           # Sessions that have shown the init message
```

## Command Dispatcher

### `handle_input(channel_id, text, user_id=None, attachments=None)`

Main entry point for all user input from a platform adapter. Logic:

1. If there is a pending interactive flow (SSH import wizard, remove confirmation), route there first.
2. If the text starts with `/`, call `_handle_command()`.
3. Otherwise, call `_forward_message()` to send to the active Claude session.

### `_handle_command(channel_id, text, user_id=None)`

Parses the command name and dispatches to the appropriate handler. Uses `maxsplit=2` to preserve path arguments that may contain spaces, except for variadic commands like `/add-machine`.

**Full command table:**

| Command | Aliases | Handler |
|---|---|---|
| `/start` | | `cmd_start` |
| `/resume` | | `cmd_resume` |
| `/ls` | `/list` | `cmd_ls` |
| `/exit` | | `cmd_exit` |
| `/rm` | `/remove`, `/destroy` | `cmd_rm` |
| `/rm-session` | `/rmsession`, `/remove-session` | `cmd_rm_session` |
| `/mode` | | `cmd_mode` |
| `/model` | | `cmd_model` |
| `/status` | | `cmd_status` |
| `/interrupt` | `/stop` | `cmd_interrupt` |
| `/rename` | | `cmd_rename` |
| `/health` | | `cmd_health` |
| `/monitor` | | `cmd_monitor` |
| `/add-machine` | `/addmachine`, `/add-peer`, `/addpeer` | `cmd_add_machine` |
| `/remove-machine` | `/removemachine`, `/rm-machine`, etc. | `cmd_remove_machine` |
| `/restart` | | `cmd_restart` (admin only) |
| `/update` | | `cmd_update` (admin only) |
| `/tool-display` | `/tooldisplay` | `cmd_tool_display` |
| `/clear` | | `cmd_clear` |
| `/new` | | `cmd_new` |
| `/help` | | `cmd_help` |

Unknown commands (not starting with a recognized prefix) are forwarded to the active Claude session as regular messages, allowing users to send slash-prefixed prompts directly to Claude.

All command handlers are wrapped in error handling that catches `DaemonConnectionError`, `DaemonError`, and generic exceptions, formatting them as error messages back to the user.

## Command Implementations

### `cmd_start(channel_id, args, silent_init=False)`

Creates a new session: `/start <machine_id> <path> [cli_type]`

1. Validates arguments (machine ID and path required)
2. Resolves the path: git URLs are expanded to `{project_path}/{repo_name}`, bare names are expanded to `{project_path}/{name}`
3. Calls `ssh.ensure_tunnel()` to establish the SSH port-forwarding tunnel
4. Calls `ssh.sync_skills()` to copy shared skills to the remote machine
5. Optionally clones a git repo if a URL was provided
6. Calls `daemon.create_session()` to register the session on the daemon
7. Registers the session in the router with `router.register()`
8. Sends a confirmation message with session name, mode, and model

The `silent_init` parameter suppresses the "Starting session..." message (used by Discord slash commands which send their own initial response).

### `cmd_resume(channel_id, args)`

Resumes a detached or previously active session: `/resume <session_id_or_name>`

1. Looks up the session by name or daemon ID in the router
2. Calls `ssh.ensure_tunnel()` for the session's machine
3. Calls `daemon.resume_session()` with the SDK session ID if available
4. Re-registers the session as active via `router.register()`

### `cmd_ls(channel_id, args)`

Lists machines or sessions: `/ls machine` or `/ls session [machine]`

### `cmd_exit(channel_id)`

Detaches from the current session. The daemon-side session (and Claude process) is not destroyed. The user can resume with `/resume`.

### `cmd_rm(channel_id, args)`

Destroys all sessions matching a machine/path combination.

### `cmd_mode(channel_id, args)`

Changes the permission mode: `/mode <auto|code|plan|ask>`

Calls `daemon.set_mode()` on the daemon, which takes effect on the next spawned process. Also updates the local session state in the router.

### `cmd_model(channel_id, args)`

Sets the model for the current session: `/model <model_name>`

Calls `daemon.set_model()` on the daemon, which takes effect on the next spawned process.

### `cmd_tool_display(channel_id, args)`

Switches the tool display mode for the current session: `/tool-display <timer|append|batch>`

| Mode | Behavior |
|---|---|
| `timer` | Show a working timer message while tools run; send all results at end (default) |
| `append` | Show each tool call progressively as it arrives |
| `batch` | Accumulate tool calls and show a single summary at the end |

Stored in the session router and applied by `_forward_message()` on the next stream.

### `cmd_interrupt(channel_id)`

Interrupts the current operation: `/interrupt` or `/stop`

1. Adds the channel to `_stop_requested` to signal the active stream loop to exit
2. Calls `daemon.interrupt_session()` to send SIGTERM to the running CLI process

### `cmd_health(channel_id, args)`

Checks daemon health for a specific machine, the current session's machine, or all connected machines.

### `cmd_monitor(channel_id, args)`

Shows detailed per-session monitoring data (status, queue depth, connected state) from the daemon.

## Message Forwarding

### `_forward_message(channel_id, text, attachments=None)`

Forwards a user message to the active Claude session and streams the response back to chat.

**Concurrency control:** The `_streaming` set tracks channels with an active stream. A second message to a streaming channel is rejected with a "Claude is still processing" notice. The `_stop_requested` set signals the loop to exit early when `/interrupt` is called.

**Streaming display flow:**

1. Resolve the session from the router; error if none active
2. Call `ssh.ensure_tunnel()` to confirm the tunnel is up
3. Call `daemon.send_message()`, which returns an async SSE event iterator
4. Handle events according to the session's `tool_display` mode

**Tool display mode logic:**

For `timer` mode (default):
- Show a timer message while tools are running
- Collect all tool events; send them together at the end

For `append` mode:
- Send or edit an activity message immediately for each `tool_use` event
- Show accumulated tool lines plus a partial-text snippet

For `batch` mode:
- Accumulate tool events silently
- Compress them into a single summary at the end via `compress_tool_messages()`

**Per-event handling (all modes):**

- `partial`: Accumulate text in a buffer. Every `STREAM_UPDATE_INTERVAL` seconds (1.5s), send or edit a message with current buffer plus `▌` cursor. If the buffer exceeds `STREAM_BUFFER_FLUSH_SIZE` (1800 chars), finalize the current message and start a new one.
- `text`: Complete text block. Edit the streaming message to its final form, or send as new message(s) if no streaming message exists. Split at platform limits if needed.
- `tool_use` with `tool == "AskUserQuestion"`: Parse the question input using `format_ask_user_question()`, then call `adapter.send_question()` for each question to display platform-native interactive buttons/keyboard.
- `tool_use` (other tools): Route to the active tool display mode handler.
- `result`: Capture the SDK session ID and update the router with `router.update_sdk_session_id()`.
- `system` (subtype `init`): On the first `system` event for a session, display the connected model and current mode.
- `queued`: Notify the user that their message is queued, with its position number.
- `error`: Display the error message.
- `interrupted`: Display an interruption notice.
- `ping`: Ignored (daemon keepalive).

5. After the stream ends, flush any remaining buffer content as a final message.

## AskUserQuestion Handling

When Claude invokes the `AskUserQuestion` tool, the stream emits a `tool_use` event with `tool == "AskUserQuestion"` and structured `input` containing a list of question dicts.

The engine calls `format_ask_user_question()` to parse the input, then calls `adapter.send_question()` for each question. Platform adapters that support inline buttons (Discord, Telegram) render them as clickable buttons or an inline keyboard. The user's button click is forwarded back to the engine as a regular input event, which gets sent to Claude as the response.

## File Forwarding

If `config.file_forward.enabled` is true, the engine initializes a `FileForwardMatcher`. After each streamed response, the engine scans the output for file paths matching the forwarding rules. Matched files are downloaded from the remote machine via SSH and sent to the chat channel via `adapter.send_file()`.

## Constants

| Constant | Value | Description |
|---|---|---|
| `STREAM_UPDATE_INTERVAL` | 1.5 seconds | How often to update the streaming message |
| `STREAM_BUFFER_FLUSH_SIZE` | 1800 chars | Force a new message when buffer exceeds this |
