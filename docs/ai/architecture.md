# Codecast Architecture (AI Reference)

This document provides a machine-readable architecture overview for AI agents working on the codecast codebase.

## System Overview

Codecast is a **Head Node + Daemon** distributed system:
- **Head Node** (Python): Local bot process connecting Discord/Telegram/Lark users to remote machines
- **Daemon** (Rust): Remote agent managing Claude CLI processes on target machines

## Data Flow

```
User Message → Bot Adapter → Engine → SSH Manager → Daemon Client
                                                        ↓ (JSON-RPC)
                                                    Daemon Server
                                                        ↓
                                                    Session Pool
                                                        ↓ (stdin JSON-lines)
                                                    Claude CLI
                                                        ↓ (stdout JSON-lines)
                                                    Stream Events
                                                        ↓ (SSE)
User ← Bot Adapter ← Message Formatter ← Engine ← Daemon Client
```

## Component Map

### Head Node (src/head/)

| Component | File | Responsibility |
|-----------|------|----------------|
| CLI | `cli.py` | Entry point, argparse, subcommand dispatch |
| Main | `main.py` | Config loading, bot lifecycle, shutdown |
| Engine | `engine.py` | Command dispatch, session lifecycle, message routing |
| SSH Manager | `ssh_manager.py` | SSH connections, tunnels, daemon deploy, localhost mode |
| Session Router | `session_router.py` | SQLite session registry, channel→session mapping |
| Daemon Client | `daemon_client.py` | JSON-RPC client, SSE stream parsing |
| Message Formatter | `message_formatter.py` | Message splitting, tool batching, status display |
| Bot Adapters | `bot_discord.py`, `bot_telegram.py`, `bot_lark.py` | Platform-specific handlers |
| File Pool | `file_pool.py` | Attachment download, MIME validation, LRU cache |
| Config | `config.py` | YAML parsing, env var expansion, SSH config import |

### Daemon (src/daemon/)

| Component | File | Responsibility |
|-----------|------|----------------|
| Server | `main.rs` + `server.rs` | Axum HTTP, JSON-RPC routing, SSE streaming |
| Session Pool | `session_pool.rs` | Claude CLI process management |
| Message Queue | `message_queue.rs` | User/response buffering for reconnect |
| Skill Manager | `skill_manager.rs` | CLAUDE.md + skills sync to project dirs |
| Types | `types.rs` | All type definitions |
| Auth | `auth.rs` | Token-based middleware |
| TLS | `tls.rs` | Self-signed certificate generation |

## Key Design Decisions

1. **Long-lived Claude CLI processes** — stdin kept open for bidirectional JSON-lines, avoids ~50K token context reload per message
2. **SSH tunnels** — daemon binds 127.0.0.1 only, SSH provides auth
3. **Localhost mode** — auto-detected, skips SSH entirely
4. **SSE streaming** — `session.send` streams via SSE, other RPCs use standard JSON
5. **Session lifecycle** — active → detached → destroyed
6. **Message queue** — buffers during busy/disconnect, enables reconnect without data loss
7. **Tool call batching** — consecutive tool_use events compressed into single summary

## RPC Protocol

All methods use JSON-RPC 2.0 over HTTP POST to `http://127.0.0.1:{port}`.

| Method | Response Type | Purpose |
|--------|--------------|---------|
| `session.create` | JSON | Spawn Claude CLI |
| `session.send` | SSE stream | Send message, stream response |
| `session.resume` | JSON | Resume dead session |
| `session.destroy` | JSON | Kill process |
| `session.list` | JSON | List sessions |
| `session.set_mode` | JSON | Change permission mode |
| `health.check` | JSON | Health + uptime |
