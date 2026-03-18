# Codecast - Project Guide

## Project Overview

Codecast is a bot-based system that lets users interact with Claude CLI on remote machines through Discord and Telegram. It follows a **Head Node + Daemon** architecture: the Head Node (Python) runs locally as a chat bot, connects to remote machines via SSH, and communicates with a Daemon (Rust) running on each remote machine that manages Claude CLI processes.

## Project Structure

```
codecast/
├── CLAUDE.md                  # This file - project conventions and guide
├── config.yaml                # Runtime config (gitignored, contains tokens)
├── config.example.yaml        # Config template
├── requirements.txt           # Python dependencies for Head Node
├── claude-cli-communication.md # Design reference: Claude CLI communication patterns
│
├── src/
│   ├── head/                  # Head Node (Python) - local bot + SSH orchestrator
│   │   ├── __init__.py
│   │   ├── main.py            # Entry point: loads config, starts bots, graceful shutdown, restart support
│   │   ├── config.py          # Config loader: YAML parsing, env var expansion, dataclasses, SSH config parser
│   │   ├── ssh_manager.py     # SSH connections, tunnels, daemon deployment, skills sync, localhost mode
│   │   ├── session_router.py  # SQLite-backed session registry (channel -> session mapping)
│   │   ├── daemon_client.py   # JSON-RPC + SSE client for communicating with remote daemon
│   │   ├── bot_base.py        # Abstract base: command dispatch, message forwarding, streaming, admin commands
│   │   ├── bot_discord.py     # Discord implementation: 17 slash commands, typing, heartbeat, restart notify
│   │   ├── bot_telegram.py    # Telegram implementation: command handlers, polling
│   │   ├── message_formatter.py # Message splitting, tool_use batching, status display
│   │   ├── file_pool.py       # Discord attachment download, file type validation, LRU eviction
│   │   └── name_generator.py  # Human-friendly session names (adjective-noun)
│   │
│   └── daemon/                # Remote Agent Daemon (Rust) - runs on remote machines
│       ├── main.rs            # Entry point, Axum HTTP server
│       ├── server.rs          # JSON-RPC router, SSE streaming
│       ├── session_pool.rs    # Claude CLI process management, per-message spawn with --resume
│       ├── message_queue.rs   # Per-session message queue: user buffering, response buffering
│       ├── skill_manager.rs   # Skills sync: CLAUDE.md + .claude/skills/ to project dirs
│       └── types.rs           # All type definitions: RPC, session, stream events, CLI protocol
│
├── docs/                      # Documentation (mdbook)
│   ├── book.toml              # mdbook configuration
│   ├── build-docs.sh          # Multi-language doc build script
│   ├── theme/                 # mdbook theme overrides
│   ├── en/                    # English documentation
│   └── zh/                    # Chinese documentation
│
├── skills/                    # Shared skills synced to remote project directories
│   ├── CLAUDE.md              # Global CLAUDE.md synced to projects
│   └── .claude/
│       └── skills/            # Skill files synced to projects' .claude/skills/
│
└── tests/                     # Python tests (368 tests, 0 warnings)
    ├── test_bot_commands.py   # 92 tests: commands, full message flow, admin, add/remove machine
    ├── test_daemon_client.py  # RPC client tests: call, health, sessions, streaming
    ├── test_file_pool.py      # File pool: sanitize, MIME types, add/get, eviction, download
    ├── test_file_transfer.py  # File upload and replacement in messages
    ├── test_message_formatter.py  # Message splitting, formatting, truncation
    ├── test_name_generator.py # Session name generation and validation
    ├── test_session_router.py # SQLite session CRUD, lifecycle, migration
    ├── test_ssh_upload.py     # SSH file upload tests
    └── test_tool_batching.py  # Tool message compression and batching
```

## Architecture Design

### Overall Architecture

```
  User (Discord/Telegram)
        │
        ▼
  ┌──────────────┐
  │  Head Node   │  Python (local machine)
  │  (bot_*.py)  │  - Discord: 17 slash commands + message listener
  │              │  - Telegram: command/message handlers
  │  bot_base.py │  - Command dispatch: /start, /resume, /ls, /exit, /rm, /mode, /status,
  │              │    /rename, /interrupt, /health, /monitor, /add-machine, /remove-machine,
  │              │    /update, /restart, /help
  │              │  - Stream response forwarding with periodic message updates
  │              │  - Tool call batching (configurable batch_size)
  └──────┬───────┘
         │ SSH tunnel (asyncssh)       OR        Direct localhost (no SSH)
         │ localhost:19100+ -> remote:9100        127.0.0.1:daemon_port
         ▼
  ┌──────────────┐
  │   Daemon     │  Rust (remote machine or localhost)
  │  server.rs   │  - Actix-web HTTP on 127.0.0.1:9100 (SSH tunnel only)
  │              │  - JSON-RPC request routing
  │              │  - SSE streaming for session.send
  │              │  - ~ path expansion (homedir)
  │              │
  │ session-pool │  - Claude CLI long-lived processes (stdin/stdout JSON-lines)
  │              │  - --input-format stream-json --output-format stream-json
  │              │  - One process per session, stdin kept OPEN
  │              │
  │ message-queue│  - User message buffering when Claude is busy
  │              │  - Response buffering when SSH is disconnected
  │              │
  │ skill-manager│  - Sync CLAUDE.md + .claude/skills/ to project dirs
  └──────┬───────┘
         │ stdin/stdout (JSON-lines)
         ▼
  ┌──────────────┐
  │  Claude CLI  │  Long-lived subprocess
  │              │  --input-format stream-json
  │              │  --output-format stream-json
  │              │  --include-partial-messages
  └──────────────┘
```

### Key Design Decisions

1. **Long-lived Claude CLI processes**: The daemon keeps Claude CLI as persistent subprocesses with stdin open for bidirectional JSON-lines communication. This avoids the per-message spawn overhead (~50K token context reload).

2. **SSH tunnel approach**: All daemon communication goes through SSH port forwarding (`localhost:localPort -> remote:9100`). The daemon binds to `127.0.0.1` only, no auth needed since SSH provides it.

3. **Localhost mode**: When the head node itself is also a target machine, SSH is skipped entirely. The daemon is spawned as a local subprocess and accessed directly via `127.0.0.1:daemon_port`. Localhost is auto-detected by checking the target host against all local IPs, hostname, and FQDN.

4. **SSE for streaming**: The `session.send` RPC method uses Server-Sent Events to stream Claude's response in real-time. Other RPC methods use standard JSON responses.

5. **Session lifecycle**: `active -> detached -> destroyed`. Detaching preserves the daemon process; destroying kills it. Sessions are tracked in SQLite on the Head Node and mapped by chat channel ID.

6. **Permission modes**: Four modes mapped to Claude CLI flags:
   - `auto` -> `--dangerously-skip-permissions` (full auto)
   - `code` -> acceptEdits (auto-accept file edits)
   - `plan` -> read-only analysis
   - `ask` -> confirm everything

7. **Resume with fallback**: When resuming a dead session, the daemon tries `--resume <sdkSessionId>`. If that fails, it starts a fresh session (CodePilot-style degradation).

8. **Message queue**: Per-session queue buffers user messages when Claude is busy and buffers responses when the SSH connection drops, enabling reconnection without data loss.

9. **Tool call batching**: Consecutive tool_use events are accumulated and compressed into a single summary message. Configurable via `tool_batch_size` (default 15).

10. **Config persistence**: Machine additions/removals persist to config.yaml via `ruamel.yaml` (preserves comments and formatting). SSH config import parses `~/.ssh/config` recursively.

11. **Self-restart**: `/update` and `/restart` commands use `os.execv()` to replace the running process in-place, preserving the PID. A `.restart_notify` file bridges the restart gap to send a confirmation message.

### Component Responsibilities

| Component | Language | Role |
|-----------|----------|------|
| `src/head/main.py` | Python | Entry point, config loading, bot lifecycle, graceful shutdown, restart support |
| `src/head/config.py` | Python | YAML config parsing, `${ENV_VAR}` expansion, dataclass models, SSH config parser, config persistence via ruamel.yaml |
| `src/head/ssh_manager.py` | Python | asyncssh connections, port forwarding, daemon deploy via SCP, skills sync, localhost mode (local daemon spawn) |
| `src/head/session_router.py` | Python | SQLite session registry, channel->session mapping, lifecycle tracking, session naming |
| `src/head/daemon_client.py` | Python | aiohttp-based JSON-RPC client, SSE stream parsing |
| `src/head/bot_base.py` | Python | Abstract bot: command routing, message forwarding, stream buffering, tool batching, admin commands, machine management |
| `src/head/bot_discord.py` | Python | Discord: 17 slash commands with autocomplete, typing indicator, 25s heartbeat status, restart notification |
| `src/head/bot_telegram.py` | Python | Telegram command/message handlers, polling |
| `src/head/message_formatter.py` | Python | Smart message splitting (code blocks, paragraphs), tool/status formatting, tool batch compression |
| `src/head/file_pool.py` | Python | Discord attachment download, MIME validation, LRU eviction pool |
| `src/head/name_generator.py` | Python | Human-friendly session names (adjective-noun format) |
| `src/daemon/server.rs` | Rust | Axum RPC server, method dispatch, SSE response streaming, ~ path expansion |
| `src/daemon/session_pool.rs` | Rust | Claude CLI process management, per-message spawn with --resume, ~ path expansion |
| `src/daemon/message_queue.rs` | Rust | User message buffering, response buffering, client reconnect state |
| `src/daemon/skill_manager.rs` | Rust | Copy CLAUDE.md + .claude/skills/ to project dirs (skip existing) |
| `src/daemon/types.rs` | Rust | All types: RPC protocol, session, stream events, CLI protocol |

### RPC Methods (Daemon API)

| Method | Params | Response | Description |
|--------|--------|----------|-------------|
| `session.create` | `{path, mode?}` | `{sessionId}` | Spawn new Claude CLI process |
| `session.send` | `{sessionId, message}` | SSE stream | Send message, stream response |
| `session.resume` | `{sessionId, sdkSessionId?}` | `{ok, fallback}` | Resume dead session |
| `session.destroy` | `{sessionId}` | `{ok}` | Kill Claude process |
| `session.list` | - | `{sessions[]}` | List all sessions |
| `session.set_mode` | `{sessionId, mode}` | `{ok}` | Change permission mode (restarts process) |
| `session.queue_stats` | `{sessionId}` | `{userPending, responsePending, clientConnected}` | Queue stats |
| `session.reconnect` | `{sessionId}` | `{bufferedEvents[]}` | Get buffered events after reconnect |
| `health.check` | - | `{ok, sessions, uptime}` | Health check |

### Bot Commands

| Command | Args | Description |
|---------|------|-------------|
| `/start` | `<machine> <path>` | Create new session on remote machine (~ expanded) |
| `/resume` | `<session_id_or_name>` | Resume a detached session (by name or ID) |
| `/ls` | `machine` or `session [machine]` | List machines or sessions |
| `/exit` | - | Detach from current session (process keeps running) |
| `/rm` | `<machine> <path>` | Destroy session(s) |
| `/mode` | `<auto\|code\|plan\|ask>` | Switch permission mode |
| `/rename` | `<new_name>` | Rename current session |
| `/interrupt` | - | Interrupt Claude's current operation |
| `/status` | - | Show session info and queue stats |
| `/health` | `[machine]` | Check daemon health |
| `/monitor` | `[machine]` | Monitor session details & queues |
| `/add-machine` | `<name> [host] [user] [opts]` | Add machine (auto-resolves from SSH config) |
| `/add-machine` | `--from-ssh` | Browse and import from SSH config |
| `/remove-machine` | `<machine>` | Remove machine (with session confirmation) |
| `/update` | - | Git pull + restart (admin only) |
| `/restart` | - | Restart head node (admin only) |
| `/help` | - | Show available commands |

### Stream Event Types (daemon -> head)

| Type | Fields | Description |
|------|--------|-------------|
| `partial` | `content` | Streaming text delta (real-time) |
| `text` | `content` | Complete text block |
| `tool_use` | `tool, input?, message?` | Tool invocation (batched) |
| `result` | `session_id` | Claude finished processing |
| `queued` | `position` | Message was queued (Claude busy) |
| `error` | `message` | Error occurred |
| `system` | `subtype, session_id` | System event (init, etc.) |
| `ping` | - | Keepalive (ignored by head) |

## Development

### Prerequisites

- Python 3.11+ with `asyncssh`, `aiohttp`, `discord.py`, `python-telegram-bot`, `pyyaml`, `ruamel.yaml`
- Rust toolchain (cargo) for daemon
- SSH access to remote machines with Claude CLI installed

### Setup

```bash
# Head Node (Python)
pip install -r requirements.txt
cp config.example.yaml config.yaml
# Edit config.yaml with machine IPs, bot tokens, etc.

# Daemon (Rust) - built locally, deployed via SCP
cargo build --release
```

### Running

```bash
# Start head node (runs bots)
python -m head.main

# Or with custom config
python -m head.main /path/to/config.yaml

# Background (production)
nohup python -m head.main > /tmp/codecast-head.log 2>&1 &
```

The daemon is auto-deployed and auto-started on remote machines when `daemon.auto_deploy: true` in config.

For localhost machines, the daemon is spawned as a local subprocess automatically.

### Building Daemon

```bash
cargo build --release    # Compile Rust daemon binary
```

**Important:** After building, the daemon binary must be redeployed to `~/.codecast/daemon/` for localhost machines. Remote machines are redeployed automatically on next `/start` when `auto_deploy: true`.

### Updating in Production

From Discord (admin only):
- `/update` — pulls latest code via `git pull --ff-only` and restarts
- `/restart` — restarts the head node without pulling code

Both commands use `os.execv()` to replace the process in-place and send a confirmation message after restart.

## Linting

**Always run lint before committing.** CI will reject PRs that fail lint.

```bash
# Check only (same checks as CI)
./scripts/lint.sh

# Auto-fix formatting + lint issues
./scripts/lint.sh --fix
```

The lint script runs:
- **Python:** `ruff check` + `ruff format` on `src/head/` and `tests/`
- **Rust:** `cargo clippy` (with `-D warnings`) + `cargo fmt`

## Testing

### Test Suite Overview

368 tests, 0 warnings. All tests use `pytest` with `pytest-asyncio`.

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_bot_commands.py` | 92 | Commands, full message flow (text/partial/tool/error/queued/system/ping), tilde expansion, add/remove machine, admin checks |
| `test_daemon_client.py` | 21 | RPC calls, health check, session management, SSE streaming, connection errors |
| `test_file_pool.py` | 27 | Filename sanitization, MIME types, file pool CRUD, eviction, Discord download |
| `test_file_transfer.py` | 4 | File upload/replace in messages, forward with files |
| `test_message_formatter.py` | 38 | Message splitting, formatting (tool/session/machine/health/monitor), truncation |
| `test_name_generator.py` | 14 | Session name generation, validation, uniqueness |
| `test_session_router.py` | 35 | SQLite CRUD, lifecycle, find by name/ID, rename, migration |
| `test_ssh_upload.py` | 7 | SSH file upload with tunnel verification |
| `test_tool_batching.py` | 29 | Tool message compression, batch flushing, interleaving |

### Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Specific test file
python -m pytest tests/test_bot_commands.py -v

# Specific test class
python -m pytest tests/test_bot_commands.py::TestFullMessageFlow -v
```

## Code Conventions

- **Always lint before committing:** run `./scripts/lint.sh --fix` before every commit
- Python: dataclasses for config models, `async`/`await` throughout, `logging` module
- Rust: strong typing, async with tokio, structured error handling
- Channel IDs are prefixed with platform: `discord:<id>` or `telegram:<id>`
- Session IDs are UUIDs (daemon-side), SDK session IDs come from Claude CLI
- Session names are human-friendly: `adjective-noun` format (e.g., `smooth-dove`)
- Config supports `${ENV_VAR}` expansion and `file:/path` for passwords
- Config persistence uses `ruamel.yaml` for comment-preserving round-trip editing
- All daemon communication is JSON-RPC over HTTP through SSH tunnels (or direct for localhost)
- Bot responses use streaming with periodic message edits (1.5s interval, 1800 char buffer flush)
- Tool calls are batched (default 15) into compressed summary messages
- Paths with `~` are expanded at both head (Python `Path.home()`) and daemon (Rust `dirs::home_dir()`)
- Datetime uses timezone-aware `datetime.now(timezone.utc)` (not deprecated `utcnow()`)
- Admin commands (`/update`, `/restart`) require user IDs in `admin_users` config list
- Localhost detection checks all local IPs, hostname, and FQDN (not just literal `localhost`)

## Known Pitfalls & Lessons

### SSH ProxyCommand vs ProxyJump
- Many SSH configs use `ProxyCommand sshpass ... ssh jumphost -W %h:%p` instead of `ProxyJump`
- `asyncssh` does **not** support `ProxyCommand` — it needs a `tunnel=` connection object
- When importing machines from SSH config, the engine extracts the jump host from `ProxyCommand` patterns and converts it to `proxy_jump` for asyncssh compatibility
- Always verify that `proxy_jump` is set after SSH config import; a missing proxy means direct connection attempts that will time out silently on firewalled hosts

### SSH Connection Timeouts
- `_connect_ssh` uses a 30s timeout via `asyncio.wait_for` — both for the jump host and the final target
- Without this timeout, failed SSH connections hang indefinitely and the Discord user sees no error feedback
- When adding new SSH connection paths, always wrap `asyncssh.connect()` with `asyncio.wait_for()`

### Discord Slash Commands: Avoid Duplicate Messages
- Discord slash commands send an immediate `interaction.response.send_message()` reply
- If the engine's command handler ALSO sends a similar message, the user sees duplicates
- Solution: Discord adapter calls `engine.cmd_*()` directly with `silent_init=True` instead of routing through text-based `handle_input()` — never inject flags like `--silent` into the command text, as the argument parser's `maxsplit` will break them

### Config Path Awareness
- The bot loads config from `./config.yaml` (project root) at startup
- When machines are added at runtime via `/add-machine`, they are saved to the config file tracked by `config._config_path`
- If the config was migrated (e.g., to `~/.codecast/config.yaml`), runtime additions go there while the bot originally loaded from the project dir
- The machine IS added to the in-memory config, so it works for the current session, but a restart may load from a different file
