# Bot Command Reference

This page documents all commands available across the Discord, Telegram, and Lark bots.

## Command Summary

| Command | Arguments | Description |
|---|---|---|
| `/start` | `<machine> <path> [--cli <type>]` | Start a new AI session |
| `/resume` | `<session_name_or_id>` | Resume a previously detached session |
| `/new` | *(none)* | Start a new session in the same directory |
| `/clear` | *(none)* | Destroy current session and restart in same directory |
| `/exit` | *(none)* | Detach from current session |
| `/stop` | *(none)* | Interrupt the AI's current operation |
| `/interrupt` | *(none)* | Interrupt the AI's current operation (alias for /stop) |
| `/ls` | `machine` or `session [machine]` | List machines or sessions |
| `/rm-session` | `<name_or_id>` | Destroy a specific session by name or ID |
| `/rm` | `<machine> <path>` | Destroy all sessions on a machine/path |
| `/mode` | `<auto\|code\|plan\|ask>` | Switch permission mode |
| `/model` | `<model_name>` | Switch the AI model for the current session |
| `/tool-display` | `<timer\|append\|batch>` | Switch how tool calls are displayed |
| `/rename` | `<new_name>` | Rename the current session |
| `/status` | *(none)* | Show current session info |
| `/health` | `[machine]` | Check daemon health |
| `/monitor` | `[machine]` | Monitor session details and queues |
| `/add-machine` | `<name> [host] [user]` | Add a remote machine |
| `/remove-machine` | `<machine>` | Remove a machine |
| `/update` | *(none)* | Git pull + restart (admin only) |
| `/restart` | *(none)* | Restart head node (admin only) |
| `/help` | *(none)* | Show available commands |

---

## `/start`

Start a new AI session on a remote machine.

**Usage:**

```
/start <machine_id> <path> [--cli <type>]
```

**Arguments:**

| Argument | Description |
|---|---|
| `machine_id` | ID of the remote machine as defined in config.yaml |
| `path` | Absolute path to the project directory on the remote machine |
| `--cli <type>` | AI CLI to use: `claude`, `codex`, `gemini`, or `opencode` (default: `claude`) |

**Shorthand flags:** `--codex`, `--gemini`, `--opencode` can be used instead of `--cli <type>`.

**Examples:**

```
/start gpu-1 /home/user/my-project
/start gpu-1 /home/user/my-project --cli codex
/start gpu-1 /home/user/my-project --gemini
```

**What happens:**

1. An SSH tunnel is established to the machine (if not already active).
2. The daemon is deployed and started if not already running.
3. Skills files are synced to the project directory if configured.
4. A new AI session is created on the daemon.
5. The session is registered in the local database.
6. A confirmation message shows the session name and current mode.

Session names are auto-assigned in adjective-noun format, for example `bright-falcon` or `smooth-dove`. You can rename a session with `/rename`.

**Discord:** Slash command with autocomplete for `machine` (from configured machines) and `path` (from `default_paths` in config).

---

## `/resume`

Resume a previously detached session.

**Usage:**

```
/resume <session_name_or_id>
```

**Arguments:**

| Argument | Description |
|---|---|
| `session_name_or_id` | Session name (e.g. `bright-falcon`) or daemon UUID |

**Examples:**

```
/resume bright-falcon
/resume a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**What happens:**

1. The session is looked up in the local database by name or ID.
2. An SSH tunnel is established to the session's machine.
3. The daemon is notified to resume the session.
4. The session is re-registered as active on the current channel.
5. Future messages continue the conversation context.

---

## `/new`

Start a new AI session in the same directory as the current session, automatically detaching the current one.

**Usage:**

```
/new
```

Equivalent to `/exit` followed by `/start` with the same machine, path, and CLI type. Useful for getting a clean context without re-entering connection details.

---

## `/clear`

Destroy the current session and immediately start a fresh one in the same directory.

**Usage:**

```
/clear
```

Unlike `/new`, the old session is fully destroyed rather than detached.

---

## `/exit`

Detach from the current session without destroying it.

**Usage:**

```
/exit
```

The AI process on the remote machine keeps running. Use `/resume` with the session name to reconnect later.

**Example output:**

```
Detached from session on gpu-1:/home/user/project
Use /resume bright-falcon to reconnect.
```

---

## `/stop` and `/interrupt`

Interrupt the AI's current operation.

**Usage:**

```
/stop
/interrupt
```

Both commands are equivalent. They:

1. Send an interrupt signal to the running AI process.
2. Clear the message queue.
3. Leave the session active for future messages.

**Output:**

- If the AI was processing: "Interrupted current operation."
- If the AI was idle: "No active operation to interrupt."

---

## `/ls`

List machines or sessions.

**Usage:**

```
/ls machine
/ls session [machine_id]
```

**Examples:**

```
/ls machine
/ls session
/ls session gpu-1
```

**Machine listing output:**

```
Machines:
  gpu-1 (gpu1.example.com) [online, daemon running]
    Paths: /home/user/project-a, /home/user/project-b
  gpu-2 (gpu2.lab.internal) [offline]
```

**Session listing output:**

```
Sessions:
  bright-falcon  gpu-1:/home/user/project  [bypass] active
  smooth-dove    gpu-1:/home/user/other    [code]   detached
```

---

## `/rm-session`

Destroy a specific session by name or ID.

**Usage:**

```
/rm-session <name_or_id>
```

**Examples:**

```
/rm-session bright-falcon
/rm-session a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

This kills the AI process for that session and marks it as destroyed in the database.

---

## `/rm`

Destroy all sessions matching a machine and path.

**Usage:**

```
/rm <machine_id> <path>
```

**Example:**

```
/rm gpu-1 /home/user/project
```

All active and detached sessions on the given machine/path combination are destroyed.

---

## `/mode`

Switch the permission mode for the current session.

**Usage:**

```
/mode <auto|code|plan|ask>
```

| Mode | Description |
|---|---|
| `auto` | Full automation. The AI can read, write, and execute anything without asking. Displayed as "bypass" in bot output. |
| `code` | Auto-accept file edits. The AI asks before running shell commands. |
| `plan` | Read-only analysis. The AI can read files but cannot make changes. |
| `ask` | Confirm everything. Every tool invocation requires approval. |

**Example:**

```
/mode plan
```

**Discord:** Dropdown choice with descriptions for each mode.

---

## `/model`

Switch the AI model for the current session.

**Usage:**

```
/model <model_name>
```

**Examples:**

```
/model claude-sonnet-4-20250514
/model claude-opus-4-20250514
```

The model change takes effect for the next message sent to the session. Use `/status` to confirm the active model.

---

## `/tool-display`

Switch how tool calls (file reads, shell commands, etc.) are displayed while the AI is working.

**Usage:**

```
/tool-display <timer|append|batch>
```

| Mode | Description |
|---|---|
| `timer` | Shows a "Working Xs" timer while the AI works. All results are sent together at the end. This is the default. |
| `append` | Shows each tool call progressively as it happens. |
| `batch` | Accumulates all tool calls and sends a single summary at the end. |

**Example:**

```
/tool-display timer
```

---

## `/rename`

Rename the current session.

**Usage:**

```
/rename <new_name>
```

**Arguments:**

| Argument | Description |
|---|---|
| `new_name` | New name in `word-word` format (e.g. `fast-hawk`, `smooth-dove`) |

**Example:**

```
/rename fast-hawk
```

The new name is stored in the session registry and can be used with `/resume`.

---

## `/status`

Show the current session's status and queue statistics.

**Usage:**

```
/status
```

**Example output:**

```
Session: bright-falcon
Machine: gpu-1
Path: /home/user/project
Mode: bypass
Status: active
CLI: claude
Model: claude-sonnet-4-20250514
Queue: 0 pending messages
Buffered: 0 responses
```

---

## `/health`

Check daemon health on a remote machine.

**Usage:**

```
/health [machine_id]
```

If no machine is specified, checks the machine of the current session, or checks all connected machines.

**Example output:**

```
Daemon Health - gpu-1
Status: OK
Uptime: 2h 15m 30s
Sessions: 3 (idle: 2, busy: 1)
```

---

## `/monitor`

Monitor session details and queue state on a remote machine.

**Usage:**

```
/monitor [machine_id]
```

**Example output:**

```
Monitor - gpu-1 (uptime: 2h 15m 30s, 2 session(s))

  bright-falcon  idle [bypass | claude-sonnet-4-20250514]
    Path: /home/user/project
    Client: connected | Queue: 0 pending, 0 buffered

  smooth-dove  busy [code | claude-sonnet-4-20250514]
    Path: /home/user/other
    Client: disconnected | Queue: 1 pending, 5 buffered
```

---

## `/add-machine`

Add a new remote machine to the configuration.

**Usage:**

```
/add-machine <name> [host] [user]
/add-machine --from-ssh
```

**Examples:**

```
/add-machine gpu-3 10.0.1.52 alice
/add-machine gpu-3 --from-ssh
```

The `--from-ssh` option reads `~/.ssh/config` and presents an interactive selection of hosts to import. The machine is persisted to `config.yaml` immediately. The daemon is deployed on first `/start`.

---

## `/remove-machine`

Remove a machine from the configuration.

**Usage:**

```
/remove-machine <machine_id>
```

If active or detached sessions exist on the machine, you are asked to confirm. The machine entry is deleted from `config.yaml`.

---

## `/update`

Pull the latest code and restart the Head Node. Admin only.

**Usage:**

```
/update
```

Runs `git pull` in the project directory, then replaces the running process. Requires your user ID in `admin_users` in the config.

---

## `/restart`

Restart the Head Node without pulling new code. Admin only.

**Usage:**

```
/restart
```

Useful for picking up config changes or recovering from a degraded state. Requires your user ID in `admin_users` in the config.

---

## `/help`

Show the list of available commands.

**Usage:**

```
/help
```

---

## Sending Messages

After starting or resuming a session, any message sent in the channel that is not a recognized command is forwarded to the AI. If you type something that starts with `/` but is not a known bot command, it is also forwarded to the AI directly -- useful for passing slash commands to the AI CLI itself.

Responses stream back in real-time. While the AI is processing, a cursor indicator or timer shows progress. On Discord, a "bot is typing..." indicator and periodic status updates keep you informed during long operations.

If you send a message while the AI is still processing the previous one, the new message is queued and processed automatically in order.

## Interactive Questions (AskUserQuestion)

When the AI uses the `AskUserQuestion` tool, Codecast presents the question with interactive controls rather than as plain text:

- **Discord** -- Buttons below the message. Click to select.
- **Telegram** -- An inline keyboard. Tap to select.
- **Lark** -- An interactive card. Tap to select.

For multiple-choice questions, each option appears as a separate button or key. Your selection is sent back to the AI as the response.

## File Forwarding

When the AI response contains a file path that matches a configured forwarding rule, Codecast can automatically download the file from the remote machine and send it to your chat. This happens without any manual command.

File forwarding is configured in `config.yaml` under `file_forward`. See the [Configuration Guide](./configuration.md) for setup details.

## Platform Differences

| Feature | Discord | Telegram | Lark |
|---|---|---|---|
| Command style | Slash commands with popups | Text commands | Text commands |
| Autocomplete | Machine IDs, paths, modes | Not available | Not available |
| Message limit | 2000 characters | 4096 characters | Platform limit |
| Interactive questions | Buttons | Inline keyboard | Interactive cards |
| Access control | Channel whitelist | User ID or chat whitelist | Chat ID whitelist |
| Admin commands | User ID in `admin_users` | User ID in `admin_users` | User ID in `admin_users` |
