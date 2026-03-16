# Commands Reference

Complete reference for all bot commands available in Remote Code. All commands work identically in Discord (as slash commands) and Telegram (as text commands with `/` prefix).

---

## Session Management

### `/start <machine> <path>`

Start a new Claude session on a remote machine at the given project path.

```
/start gpu-server /home/alice/myproject
```

- **`machine`** — the machine ID from your `config.yaml`
- **`path`** — absolute path to the project directory on the remote machine

What happens:
1. Opens an SSH tunnel to the machine (or reuses an existing one)
2. Syncs skills directory if `skills.sync_on_start: true`
3. Spawns a new Claude CLI process on the remote
4. Registers the session mapped to this chat channel

After `/start`, any plain text message in the channel is forwarded to Claude.

> In Discord, machine and path have autocomplete — machine names come from `config.yaml`, paths come from `default_paths` for the selected machine.

---

### `/resume <session_id>`

Resume a previously detached session.

```
/resume abc123
/resume my-project-session
```

- **`session_id`** — the session name (e.g. `my-project`) or the UUID shown by `/ls session`

The daemon attempts to resume the Claude CLI process using `--resume <sdk_session_id>`. If the process is no longer alive, it starts a fresh session with a warning.

---

### `/exit`

Detach from the current session without destroying it.

```
/exit
```

The Claude process continues running on the remote machine. You can reconnect later with `/resume`. Use this when you want to leave a long-running task running and check back later.

---

### `/rm <machine> <path>`

Destroy all sessions matching a machine and path.

```
/rm gpu-server /home/alice/myproject
```

Sends `SIGTERM` to the Claude CLI process, waits 5 seconds, then sends `SIGKILL`. The session is removed from the registry.

> **Irreversible.** Any in-progress work by Claude is lost. Use `/exit` if you just want to detach temporarily.

---

## Information Commands

### `/ls machine`

List all configured machines with their current SSH and daemon status.

```
/ls machine
```

Output example:
```
Machines (2)
  gpu-server    10.0.1.50    online    daemon: running
  gpu-node-2    10.0.1.51    offline   daemon: unknown
```

Machines used only as jump hosts (with no `default_paths`) are hidden from this list.

---

### `/ls session [machine]`

List all sessions (active and detached).

```
/ls session
/ls session gpu-server
```

- Without `machine`: lists all sessions across all machines
- With `machine`: filters to sessions on that machine

Output example:
```
Sessions (2)
  my-project   gpu-server   /home/alice/myproject   active
  old-task     gpu-server   /home/alice/oldtask     detached
```

---

### `/status`

Show detailed information about the current active session.

```
/status
```

Output includes:
- Machine and project path
- Permission mode
- Session ID
- Queue stats (pending messages, buffered responses)

---

### `/health [machine]`

Check the daemon health on a machine.

```
/health
/health gpu-server
```

- Without `machine`: checks all machines with active SSH tunnels
- With `machine`: checks that specific machine (opens a tunnel if needed)

Output includes: daemon uptime, number of active sessions, and overall health status.

---

### `/monitor [machine]`

Show detailed session and queue stats for a machine.

```
/monitor
/monitor gpu-server
```

More detailed than `/health` — shows per-session information including:
- Processing state (busy/idle)
- User message queue depth
- Response buffer depth
- SSH client connection state

---

## Mode Control

### `/mode <auto|code|plan|ask>`

Switch the permission mode for the current Claude session. The daemon restarts the Claude CLI process with the new flags.

```
/mode auto
/mode code
/mode plan
/mode ask
```

| Mode | Claude CLI Flag | Behavior |
|------|-----------------|----------|
| `auto` | `--dangerously-skip-permissions` | Full autonomous — no confirmations at all |
| `code` | `acceptEdits` | Auto-accepts file edits; confirms bash commands |
| `plan` | *(read-only)* | Analysis and planning only; no file writes |
| `ask` | *(default)* | Confirms every action |

In Discord, the `/mode` slash command shows descriptive labels in the dropdown:
- `bypass - Full auto (skip all permissions)`
- `code - Auto accept edits, confirm bash`
- `plan - Read-only analysis`
- `ask - Confirm everything`

---

## Utility

### `/help`

Show the list of available commands.

```
/help
```

---

### `/interrupt`

Interrupt Claude's currently running operation.

```
/interrupt
```

Sends an interrupt signal to the Claude CLI process. Useful when Claude is stuck in a long loop or you want to stop an operation mid-way.

---

## Sending Messages to Claude

After starting or resuming a session, send any plain text message in the channel — no command prefix needed.

```
Fix the type errors in src/utils.ts
```

```
Explain how the authentication flow works
```

```
Add unit tests for the UserService class
```

The bot streams Claude's response back in real time, updating a single message as text arrives. Tool invocations (file reads, writes, bash commands) appear as separate messages showing the tool name and input.

---

## Discord-specific Behavior

- **Typing indicator** — the bot shows "typing..." while Claude is processing
- **Heartbeat messages** — for long responses, a status message appears every ~25 seconds showing elapsed time and what Claude is doing (e.g. "Using tool: Bash"). It's deleted when the response completes.
- **Message splitting** — responses over 2000 characters are automatically split at paragraph/sentence boundaries
- **Autocomplete** — `/start`, `/resume`, `/rm`, `/health`, `/monitor` have autocomplete for machine and path fields

## Telegram-specific Behavior

- **Polling mode** — the bot uses long polling (no webhook required)
- **Allowed users** — set `bot.telegram.allowed_users` to restrict the bot to specific Telegram user IDs
- **Message splitting** — responses over 4096 characters are split at paragraph/sentence boundaries

---

## Session Lifecycle

```
           /start
              │
              ▼
           active  ◄────── /resume ──────┐
              │                          │
           /exit                         │
              │                          │
              ▼                          │
          detached ─────────────────────►┘
              │
             /rm
              │
              ▼
          destroyed
```

- **active** — session is bound to a chat channel; messages are forwarded to Claude
- **detached** — process still running on remote; not bound to any channel
- **destroyed** — process killed; session record kept in history only
