# Bot Command Reference

This page documents all commands available in the Discord and Telegram bots.

## Command Summary

| Command | Arguments | Description |
|---|---|---|
| `/start` | `<machine> <path>` | Start a new Claude session |
| `/resume` | `<session_id>` | Resume a previously detached session |
| `/new` | *(none)* | Start a new session in same directory (detaches current) |
| `/clear` | *(none)* | Destroy current session and restart in same directory |
| `/ls` | `machine` or `session [machine]` | List machines or sessions |
| `/exit` | *(none)* | Detach from current session |
| `/rm` | `<machine> <path>` | Destroy a session |
| `/mode` | `<auto\|code\|plan\|ask>` | Switch permission mode |
| `/rename` | `<new_name>` | Rename current session (word-word format) |
| `/status` | *(none)* | Show current session info |
| `/interrupt` | *(none)* | Interrupt Claude's current operation |
| `/health` | `[machine]` | Check daemon health |
| `/monitor` | `[machine]` | Monitor session details & queues |
| `/add-machine` | `<name> [host] [user] [opts]` | Add a remote machine |
| `/remove-machine` | `<machine>` | Remove a machine |
| `/update` | *(none)* | Git pull + restart (admin only) |
| `/restart` | *(none)* | Restart head node (admin only) |
| `/help` | *(none)* | Show available commands |

---

## `/start`

Start a new Claude session on a remote machine.

**Usage:**

```
/start <machine_id> <path>
```

**Arguments:**

| Argument | Description |
|---|---|
| `machine_id` | ID of the remote machine (as defined in config.yaml) |
| `path` | Absolute path to the project directory on the remote machine |

**Example:**

```
/start gpu-1 /home/user/my-project
```

**What happens:**

1. An SSH tunnel is established to the machine (if not already active)
2. The daemon is deployed and started if needed (auto-deploy)
3. Skills files are synced to the project directory (if configured)
4. A new Claude session is created on the daemon
5. The session is registered in the local database
6. Confirmation message shows session ID and current mode

**Discord features:** Slash command with autocomplete for both `machine` (from configured machines) and `path` (from `default_paths` in config).

---

## `/resume`

Resume a previously detached session.

**Usage:**

```
/resume <session_id>
```

**Arguments:**

| Argument | Description |
|---|---|
| `session_id` | The daemon session UUID (shown when session was created or listed) |

**Example:**

```
/resume a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**What happens:**

1. The session is looked up in the local database (both active and logged sessions)
2. An SSH tunnel is established to the session's machine
3. The daemon is notified to resume the session with the stored SDK session ID
4. The session is re-registered as active on the current channel
5. Future messages use `--resume` to continue the conversation context

---

## `/new`

Start a new Claude session in the same directory as the current session, automatically detaching the current one.

**Usage:**

```
/new
```

**What happens:**

1. The current session is detached (not destroyed)
2. A new Claude session is created on the same machine and path
3. The new session is bound to the current channel

Equivalent to `/exit` followed by `/start` with the same machine and path. Useful for getting a clean context without re-entering the connection details.

---

## `/clear`

Destroy the current session and immediately start a fresh one in the same directory.

**Usage:**

```
/clear
```

**What happens:**

1. The current session's Claude process is killed
2. The session record is marked destroyed
3. A new Claude session is spawned in the same machine and path
4. The new session is bound to the current channel

Unlike `/new`, the old session is fully destroyed rather than detached.

---

## `/ls`

List machines or sessions.

**Usage:**

```
/ls machine
/ls session [machine_id]
```

**Subcommands:**

| Subcommand | Description |
|---|---|
| `machine` / `machines` | List all configured machines with online/daemon status |
| `session` / `sessions` | List all sessions, optionally filtered by machine |

**Examples:**

```
/ls machine
/ls session
/ls session gpu-1
```

**Machine listing output:**

```
Machines:
🟢 gpu-1 (gpu1.example.com) ⚡
  Paths: /home/user/project-a, /home/user/project-b
🔴 gpu-2 (gpu2.lab.internal) 💤
```

- 🟢 = online, 🔴 = offline
- ⚡ = daemon running, 💤 = daemon stopped

**Session listing output:**

```
Sessions:
● a1b2c3d4... gpu-1:/home/user/project [bypass] (active)
○ e5f6g7h8... gpu-1:/home/user/other [code] (detached)
```

**Discord features:** Dropdown choice for `machine`/`session` target, with autocomplete on the optional machine filter.

---

## `/exit`

Detach from the current session without destroying it.

**Usage:**

```
/exit
```

**What happens:**

1. The active session on the current channel is detached
2. The session is logged in the history table for future resume
3. The daemon session is NOT destroyed -- Claude processes can continue
4. A message shows the session ID for later `/resume`

**Example output:**

```
Detached from session on gpu-1:/home/user/project
Use /resume a1b2c3d4-e5f6-7890-abcd-ef1234567890 to reconnect.
```

---

## `/rm`

Destroy a session by machine and path.

**Usage:**

```
/rm <machine_id> <path>
```

**Arguments:**

| Argument | Description |
|---|---|
| `machine_id` | Machine the session runs on |
| `path` | Project path of the session |

**Example:**

```
/rm gpu-1 /home/user/project
```

**What happens:**

1. All sessions matching the machine/path combination are found
2. For each active or detached session:
   - The daemon session is destroyed (Claude process killed)
   - The local session record is marked as destroyed
3. Confirmation shows the number of sessions destroyed

**Discord features:** Autocomplete for `machine`.

---

## `/mode`

Switch the permission mode for the current session.

**Usage:**

```
/mode <auto|code|plan|ask>
```

**Arguments:**

| Mode | Display Name | CLI Flag | Description |
|---|---|---|---|
| `auto` | bypass | `--dangerously-skip-permissions` | Full automation. Claude can read, write, and execute anything without asking for permission. |
| `code` | code | *(none)* | Auto-accept file edits. Claude asks before running bash commands. |
| `plan` | plan | *(none)* | Read-only analysis. Claude can read files but cannot make changes. |
| `ask` | ask | *(none)* | Confirm everything. Every tool invocation requires approval. |

The display name `bypass` is used for `auto` mode in bot output to make the behavior explicit.

**Example:**

```
/mode plan
```

**Discord features:** Dropdown choice with mode descriptions:
- "bypass - Full auto (skip all permissions)"
- "code - Auto accept edits, confirm bash"
- "plan - Read-only analysis"
- "ask - Confirm everything"

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

The new name is stored in the session registry and can be used with `/resume` instead of the UUID.

---

## `/status`

Show the current session's status and queue statistics.

**Usage:**

```
/status
```

**Example output:**

```
Session Status
Machine: gpu-1
Path: /home/user/project
Mode: bypass
Status: active
Session ID: a1b2c3d4e5f6...
SDK Session: x9y8z7w6v5u4...
Queue: 0 pending messages
Buffered: 0 responses
```

---

## `/interrupt`

Interrupt Claude's current operation.

**Usage:**

```
/interrupt
```

**What happens:**

1. Sends SIGTERM to the running Claude CLI process
2. Clears the message queue
3. The session remains active for future messages

**Output:**

- If Claude was processing: "Interrupted Claude's current operation."
- If Claude was idle: "Claude is not currently processing any request."

---

## `/health`

Check daemon health on a remote machine.

**Usage:**

```
/health [machine_id]
```

**Arguments:**

| Argument | Required | Description |
|---|---|---|
| `machine_id` | no | Machine to check. Defaults to current session's machine, or checks all connected machines. |

**Example output:**

```
Daemon Health - gpu-1
Status: OK
Uptime: 2h15m30s
Sessions: 3 (idle: 2, busy: 1)
Memory: 45MB RSS, 20/30MB heap
Node: v20.11.0 (PID: 12345)
```

**Discord features:** Autocomplete for `machine`.

---

## `/monitor`

Monitor session details and queue state on a remote machine.

**Usage:**

```
/monitor [machine_id]
```

**Arguments:**

| Argument | Required | Description |
|---|---|---|
| `machine_id` | no | Machine to monitor. Defaults to current session's machine, or monitors all connected machines. |

**Example output:**

```
Monitor - gpu-1 (uptime: 2h15m30s, 2 session(s))

● a1b2c3d4... idle [bypass | claude-sonnet-4-20250514]
  Path: /home/user/project
  Client: connected | Queue: 0 pending, 0 buffered

◉ e5f6g7h8... busy [code | claude-sonnet-4-20250514]
  Path: /home/user/other
  Client: disconnected | Queue: 1 pending, 5 buffered
```

**Discord features:** Autocomplete for `machine`.

---

## `/add-machine`

Add a new remote machine to the configuration.

**Usage:**

```
/add-machine <name> [host] [user] [opts]
/add-machine --from-ssh
```

**Arguments:**

| Argument | Required | Description |
|---|---|---|
| `name` | yes | Short identifier for the machine (used in other commands) |
| `host` | no | IP address or hostname (can be resolved from SSH config) |
| `user` | no | SSH username (can be resolved from SSH config) |
| `opts` | no | Additional SSH options (port, proxy jump, etc.) |
| `--from-ssh` | — | Browse and import from `~/.ssh/config` interactively |

**Examples:**

```
/add-machine gpu-3 10.0.1.52 alice
/add-machine gpu-3 --from-ssh
```

The machine is persisted to `config.yaml` immediately. The daemon is deployed on first `/start`.

---

## `/remove-machine`

Remove a machine from the configuration.

**Usage:**

```
/remove-machine <machine_id>
```

**Arguments:**

| Argument | Description |
|---|---|
| `machine_id` | The machine to remove |

**Example:**

```
/remove-machine gpu-3
```

If active or detached sessions exist on the machine, the command asks for confirmation. The machine entry is deleted from `config.yaml`.

---

## `/update`

Pull the latest code and restart the Head Node. **Admin only.**

**Usage:**

```
/update
```

**What happens:**

1. Runs `git pull --ff-only` in the project directory
2. Replaces the running process via `os.execv()` (same PID)
3. Sends a confirmation message after the restart completes

Requires your user ID to be in `admin_users` in the config.

---

## `/restart`

Restart the Head Node without pulling new code. **Admin only.**

**Usage:**

```
/restart
```

Replaces the running process via `os.execv()`. Useful for picking up config changes or recovering from a degraded state. Requires your user ID to be in `admin_users` in the config.

---

## `/help`

Show the list of available commands.

**Usage:**

```
/help
```

---

## Sending Messages

After starting or resuming a session, any non-command message sent in the channel is forwarded to Claude. The response is streamed back in real-time.

While Claude is processing, a cursor indicator (`▌`) is shown at the end of the streaming text. On Discord, a "bot is typing..." indicator and periodic heartbeat status messages keep you informed of progress during long operations.

If you send a message while Claude is still processing the previous one, the new message is queued and processed automatically after the current one completes. You'll see a notification with your position in the queue.

## Platform Differences

| Feature | Discord | Telegram |
|---|---|---|
| Command style | Slash commands with popups | Text commands (e.g., `/start gpu-1 /path`) |
| Autocomplete | Machine IDs, paths, mode choices | Not available |
| Message limit | 2000 characters | 4096 characters |
| Typing indicator | "Bot is typing..." loop | Not implemented |
| Heartbeat updates | Status messages every 25s | Not implemented |
| Access control | Channel whitelist | User ID whitelist |
| Command aliases | Not applicable (registered commands) | `/list`, `/remove`, `/destroy` also work |
