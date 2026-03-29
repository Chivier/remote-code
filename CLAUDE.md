# Codecast - Project Guide

## Project Overview

Codecast is a bot-based system that lets users interact with Claude CLI on remote machines through Discord, Telegram, and Lark. It follows a **Head Node + Daemon** architecture: the Head Node (Python) runs locally as a chat bot, connects to remote machines via SSH, and communicates with a Daemon (Rust) running on each remote machine that manages Claude CLI processes.

## Project Structure

```
codecast/
├── CLAUDE.md                  # This file - project overview
├── .claude/
│   ├── rules/                 # Detailed development rules
│   │   ├── core.md            # Code conventions, testing, known pitfalls
│   │   ├── config.md          # Configuration format and rules
│   │   └── release.md         # Version management and release process
│   ├── skills/                # Operational skills
│   │   ├── runtime-diagnosis/ # Log collection, status checks, dependency health
│   │   ├── config-migration/  # Config migration with rollback protection
│   │   ├── release-check/     # Pre-release validation and smoke testing
│   │   └── incident-triage/   # Online fault diagnosis workflow
│   ├── agents/
│   │   ├── reviewer.md        # PR review focus areas and checklist
│   │   └── explorer.md        # Codebase navigation guide
│   └── settings.json          # Claude Code settings
│
├── src/
│   ├── head/                  # Head Node (Python) - local bot + SSH orchestrator
│   │   ├── cli.py             # CLI entry point: argparse, subcommand dispatch
│   │   ├── main.py            # Head node entry: loads config, starts bots, shutdown
│   │   ├── config.py          # Config loader: YAML parsing, env var expansion
│   │   ├── engine.py          # Core command engine: session lifecycle, message routing
│   │   ├── ssh_manager.py     # SSH connections, tunnels, daemon deployment
│   │   ├── session_router.py  # SQLite-backed session registry
│   │   ├── daemon_client.py   # JSON-RPC + SSE client for daemon communication
│   │   ├── message_formatter.py # Message splitting, tool_use batching
│   │   ├── tui/               # Interactive TUI (Textual)
│   │   └── webui/             # Web UI (aiohttp)
│   │
│   └── daemon/                # Remote Agent Daemon (Rust)
│       ├── main.rs            # Axum HTTP server, port allocation
│       ├── server.rs          # JSON-RPC router, SSE streaming
│       ├── session_pool.rs    # Claude CLI process management
│       ├── message_queue.rs   # Per-session message buffering
│       └── types.rs           # All type definitions
│
├── scripts/
│   ├── bump-version.sh        # Version bump across all files
│   ├── lint.sh                # Lint checker/fixer (ruff + clippy + cargo fmt)
│   └── install.sh             # Installation script
│
├── docs/                      # Documentation (mdbook, bilingual en/zh)
│   ├── book.toml              # mdbook configuration
│   ├── build-docs.sh          # Multi-language doc build script
│   ├── en/                    # English documentation
│   ├── zh/                    # Chinese documentation
│   └── ai/                    # AI-readable architecture and runbooks
│
├── skills/                    # Shared skills synced to remote project dirs
│
└── tests/                     # Python tests (812+ tests, pytest + pytest-asyncio)
```

## Architecture

```
User (Discord/Telegram/Lark)
      │
      ▼
┌──────────────┐
│  Head Node   │  Python (local machine)
│  (bot_*.py)  │  - Bot adapters (Discord, Telegram, Lark)
│  engine.py   │  - Command dispatch, session lifecycle
└──────┬───────┘
       │ SSH tunnel (asyncssh)  OR  Direct localhost
       ▼
┌──────────────┐
│   Daemon     │  Rust (remote machine)
│  server.rs   │  - Axum HTTP on 127.0.0.1:9100
│  session-pool│  - Claude CLI long-lived processes (stdin/stdout JSON-lines)
│  msg-queue   │  - Message buffering for reconnect
└──────┬───────┘
       │ stdin/stdout (JSON-lines)
       ▼
┌──────────────┐
│  Claude CLI  │  Long-lived subprocess
└──────────────┘
```

## Quick Reference

### Setup & Run

```bash
pip install codecast
cp config.example.yaml ~/.codecast/config.yaml
codecast                    # Start head node
```

### Development

```bash
python -m pytest tests/ -v          # Run tests
./scripts/lint.sh --fix             # Lint + auto-fix
cargo build --release               # Build daemon
./scripts/deploy-test.sh            # Deploy to test env
```

### Release

```bash
./scripts/bump-version.sh X.Y.Z    # Bump all version files
# See .claude/rules/release.md for full release flow
```

## Rules & Conventions

Detailed rules are in `.claude/rules/`:
- **[core.md](.claude/rules/core.md)** — Code conventions, testing, known pitfalls
- **[config.md](.claude/rules/config.md)** — Configuration format, permission modes
- **[release.md](.claude/rules/release.md)** — Version management, CI build matrix, release flow

## Key Design Decisions

1. **Long-lived Claude CLI processes** — stdin kept open for bidirectional JSON-lines, avoids ~50K token context reload per message
2. **SSH tunnels** — daemon binds `127.0.0.1` only, SSH provides authentication
3. **Localhost mode** — auto-detected, skips SSH entirely for local machines
4. **SSE streaming** — `session.send` streams via SSE, other RPCs use standard JSON
5. **Session lifecycle** — `active → detached → destroyed`
6. **Message queue** — buffers during busy/disconnect, enables reconnect without data loss
7. **Tool call batching** — consecutive tool_use events compressed into single summary (default 15)

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start <machine> <path>` | Create new session on remote machine |
| `/resume <name>` | Resume a detached session |
| `/exit` | Detach (process keeps running) |
| `/ls machine\|session` | List machines or sessions |
| `/mode <auto\|code\|plan\|ask>` | Switch permission mode |
| `/status` | Current session info |
| `/health [machine]` | Daemon health check |
| `/update` | Git pull + restart (admin only) |

## RPC Methods (Daemon API)

| Method | Response | Description |
|--------|----------|-------------|
| `session.create` | JSON | Spawn new Claude CLI process |
| `session.send` | SSE stream | Send message, stream response |
| `session.resume` | JSON | Resume dead session |
| `session.destroy` | JSON | Kill Claude process |
| `session.list` | JSON | List all sessions |
| `health.check` | JSON | Health + uptime |
