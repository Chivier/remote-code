# Architecture Overview

Codecast uses a two-tier architecture consisting of a **Head Node** (local orchestrator) and one or more **Daemons** (remote agents). The Head Node handles user interaction and SSH connections. Each remote machine runs a Daemon that spawns CLI processes and streams results back.

## System Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Devices                             │
│                                                                 │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐                  │
│   │ Discord  │    │ Telegram │    │   Lark   │                  │
│   │ Client   │    │ Client   │    │  Client  │                  │
│   └────┬─────┘    └────┬─────┘    └────┬─────┘                  │
└────────┼──────────────┼───────────────┼──────────────────────────┘
         │              │               │
         │  Platform APIs               │
         │              │               │
┌────────▼──────────────▼───────────────▼──────────────────────────┐
│                  HEAD NODE  (Python, asyncio)                    │
│                                                                  │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │  PlatformAdapter protocol                                │   │
│   │  discord_adapter.py  telegram_adapter.py  lark_adapter.py│   │
│   └────────────────────────┬─────────────────────────────────┘   │
│                            │  set_input_handler / send_message   │
│                            ▼                                     │
│   ┌─────────────────────────────────────┐  ┌──────────────────┐  │
│   │  BotEngine  (engine.py)             │──│  SessionRouter   │  │
│   │  cmd_* handlers, _forward_message   │  │  (SQLite)        │  │
│   └──────────────┬──────────────────────┘  └──────────────────┘  │
│                  │                                               │
│   ┌──────────────▼──────────────┐  ┌──────────────────────────┐  │
│   │  DaemonClient               │  │  SSHManager              │  │
│   │  JSON-RPC + SSE client      │  │  asyncssh tunnels        │  │
│   └──────────────┬──────────────┘  └────────────┬─────────────┘  │
└──────────────────┼──────────────────────────────┼────────────────┘
                   │                              │
                   │  JSON-RPC over SSH tunnel    │  SSH port forwarding
                   │                              │
┌──────────────────▼──────────────────────────────▼────────────────┐
│                  DAEMON  (Rust, tokio)           REMOTE MACHINE  │
│                                                                  │
│   ┌─────────────────────────────────────┐                        │
│   │  Axum RPC Server  (server.rs)       │ ◄── 127.0.0.1:9100     │
│   │  POST /rpc  (JSON + SSE)            │                        │
│   └──────────────┬──────────────────────┘                        │
│                  │                                               │
│   ┌──────────────▼──────────────┐  ┌──────────────────────────┐  │
│   │  SessionPool                │──│  MessageQueue            │  │
│   │  (session_pool.rs)          │  │  (message_queue.rs)      │  │
│   └──────────────┬──────────────┘  └──────────────────────────┘  │
│                  │  spawn per message                            │
│                  ▼                                               │
│   ┌─────────────────────────────────────┐                        │
│   │  CliAdapter trait  (cli_adapter/)   │                        │
│   │  claude.rs / codex.rs / gemini.rs   │                        │
│   │  opencode.rs                        │                        │
│   └──────────────┬──────────────────────┘                        │
│                  │  spawn subprocess                             │
│                  ▼                                               │
│   ┌─────────────────────────────────────┐                        │
│   │  claude --print --output-format     │                        │
│   │         stream-json [--resume ...]  │                        │
│   └─────────────────────────────────────┘                        │
└──────────────────────────────────────────────────────────────────┘
```

## Data Flow

A typical user interaction follows this path:

1. **User** sends a message or command via Discord, Telegram, or Lark.
2. The **PlatformAdapter** receives the event and calls the registered `InputHandler` callback on the BotEngine.
3. **BotEngine.handle_input()** routes the input: commands go to `cmd_*` handlers; regular messages go to `_forward_message()`.
4. For message forwarding, the **SessionRouter** resolves the active session for this channel.
5. **SSHManager.ensure_tunnel()** establishes (or reuses) an SSH port-forwarding tunnel to the remote machine.
6. **DaemonClient.send_message()** sends a `session.send` JSON-RPC request over the tunnel and returns an async SSE event iterator.
7. The **Daemon** receives the request, selects a **CliAdapter** for the session's CLI type, and spawns a subprocess (e.g., `claude --print <message> --output-format stream-json --resume <sdkSessionId>`).
8. The CLI process writes JSON-lines to stdout. The daemon parses each line via `CliAdapter.parse_output_line()` and converts it to a **StreamEvent**.
9. Each **StreamEvent** is serialized and sent back to the Head Node as an SSE `data:` frame.
10. **BotEngine._forward_message()** handles each event: accumulating `partial` deltas for streaming display, forwarding `tool_use` notifications, and capturing the SDK session ID from the `result` event.
11. When Claude finishes (emits a `result` event), the SDK session ID is stored in the **SessionRouter** for future `--resume` calls.

## Key Design Decisions

### Per-Message Spawn

The daemon spawns a fresh CLI process for each user message rather than keeping a long-running process with stdin open. The command pattern is:

```
claude --print "user message" --output-format stream-json --verbose \
       [--resume <sdkSessionId>] [--dangerously-skip-permissions]
```

The `--resume` flag passes the SDK session ID from the previous `result` event, maintaining conversation continuity across process boundaries.

**Benefits:**
- Clean process state and memory for every message
- No zombie process management
- Natural recovery from crashes: just spawn a new process on the next send
- The pattern generalizes across all CLI backends (Claude, Codex, Gemini, OpenCode)

### SSH Tunnels for Security

The daemon binds exclusively to `127.0.0.1`. It is never network-reachable. All Head Node access passes through SSH port forwarding:

```
localhost:1xxxx  ──SSH tunnel──▶  remote:127.0.0.1:9100
```

This means:
- No firewall changes are needed on remote machines
- SSH handles authentication and encryption
- ProxyJump chains are supported for machines behind bastion hosts
- Localhost machines skip SSH entirely (auto-detected)

### BotEngine + PlatformAdapter Composition

The Head Node uses composition rather than inheritance. `BotEngine` holds a `PlatformAdapter` instance and contains all command and streaming logic. Each platform (Discord, Telegram, Lark) implements the `PlatformAdapter` protocol independently, with no shared base class.

This means:
- Platform adapters are independently testable
- New platforms can be added without touching BotEngine
- The engine can be driven by a test adapter for integration tests

### CliAdapter Trait for Multi-CLI Support

The daemon uses a `CliAdapter` trait to abstract over different CLI backends. A fresh adapter instance is created per `run_cli_process()` call via `create_adapter()`. Each adapter implements:
- `build_command()` and `build_resume_command()` for constructing the subprocess invocation
- `parse_output_line()` for parsing JSON-lines output into `StreamEvent` values
- `instructions_file()` and `skills_dir()` for skill sync

Currently supported CLI types: `claude`, `codex`, `gemini`, `opencode`.

### SQLite for Session State

The Head Node uses SQLite (`sessions.db`) to persist session mappings between chat channels and remote daemon sessions. This ensures:
- Sessions survive Head Node restarts
- Multiple platform adapters (Discord, Telegram, Lark) share the same registry
- Session history enables `/resume` after detach
- The `session_log` table records detached sessions with their SDK session IDs

### SSE for Streaming Responses

The `session.send` RPC method responds with an SSE stream (`Content-Type: text/event-stream`) instead of a single JSON body. This enables:
- Real-time streaming of Claude's output as it is generated
- Progressive rendering in chat (partial text updates with a `▌` cursor indicator)
- Keepalive pings every 30 seconds to prevent idle tunnel timeouts
- Graceful buffering of events when the client disconnects mid-stream

## Component Responsibilities

| Component | Runtime | Responsibility |
|---|---|---|
| **discord_adapter.py** | Python (discord.py v2) | Slash commands, autocomplete, typing indicator, heartbeat, AskUserQuestion buttons |
| **telegram_adapter.py** | Python (python-telegram-bot v20+) | Command handlers, HTML formatting, inline keyboard for AskUserQuestion |
| **lark_adapter.py** | Python (lark-oapi) | Lark/Feishu message handling and card interactions |
| **BotEngine** | Python | Command dispatch, session lifecycle, streaming display modes |
| **SessionRouter** | Python (sqlite3) | Channel-to-session mapping, lifecycle tracking (active/detached/destroyed) |
| **SSHManager** | Python (asyncssh) | SSH connection pool, port forwarding, daemon deployment via SCP, skills sync |
| **DaemonClient** | Python (aiohttp) | JSON-RPC calls, SSE stream parsing, error handling |
| **Axum RPC Server** | Rust (axum) | `POST /rpc` endpoint, SSE streaming, auth middleware |
| **SessionPool** | Rust | CLI session registry, per-message spawn, CliAdapter dispatch |
| **MessageQueue** | Rust | User message buffering, response buffering for SSH reconnect |
| **CliAdapter** | Rust (trait) | CLI-specific command building, output parsing, skill file names |
| **SkillManager** | Rust | Skills sync from `~/.codecast/skills` to project directories |
