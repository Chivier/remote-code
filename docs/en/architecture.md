# Architecture Overview

Remote Code uses a two-tier architecture consisting of a **Head Node** (local orchestrator) and one or more **Daemons** (remote agents). This design separates the concerns of user interaction, connection management, and Claude CLI execution.

## System Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Devices                             │
│                                                                 │
│   ┌──────────┐          ┌──────────┐                            │
│   │ Discord  │          │ Telegram │                            │
│   │ Client   │          │ Client   │                            │
│   └────┬─────┘          └────┬─────┘                            │
└────────┼─────────────────────┼──────────────────────────────────┘
         │                     │
         │  Discord API        │  Telegram API
         │                     │
┌────────┼─────────────────────┼──────────────────────────────────┐
│        ▼                     ▼         HEAD NODE (Python)       │
│   ┌──────────┐          ┌──────────┐                            │
│   │ Discord  │          │ Telegram │                            │
│   │ Bot      │          │ Bot      │                            │
│   └────┬─────┘          └────┬─────┘                            │
│        │                     │                                  │
│        └──────────┬──────────┘                                  │
│                   ▼                                             │
│            ┌─────────────┐     ┌─────────────────┐              │
│            │  Bot Base   │────▶│ Session Router   │              │
│            │ (commands)  │     │ (SQLite)         │              │
│            └──────┬──────┘     └─────────────────┘              │
│                   │                                             │
│                   ▼                                             │
│            ┌─────────────┐     ┌─────────────────┐              │
│            │   Daemon    │────▶│  SSH Manager     │              │
│            │   Client    │     │ (tunnels, deploy)│              │
│            └──────┬──────┘     └────────┬────────┘              │
└───────────────────┼─────────────────────┼───────────────────────┘
                    │                     │
                    │  JSON-RPC/SSE       │  SSH Tunnel
                    │  over SSH Tunnel    │  (port forwarding)
                    │                     │
┌───────────────────┼─────────────────────┼───────────────────────┐
│                   ▼                     ▼    REMOTE MACHINE     │
│            ┌─────────────┐                                      │
│            │ Express RPC │ ◄── 127.0.0.1:9100                   │
│            │   Server    │                                      │
│            └──────┬──────┘                                      │
│                   │                                             │
│                   ▼                                             │
│            ┌─────────────┐     ┌─────────────────┐              │
│            │Session Pool │────▶│ Message Queue    │              │
│            │             │     └─────────────────┘              │
│            └──────┬──────┘                                      │
│                   │                                             │
│                   ▼  spawn per message                          │
│            ┌─────────────┐                                      │
│            │ claude      │                                      │
│            │ --print     │                                      │
│            │ --stream    │                                      │
│            └─────────────┘                                      │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

A typical user interaction follows this path:

1. **User** sends a message or command via Discord/Telegram.
2. The **Bot** (Discord or Telegram) receives the message and routes it through the **Bot Base** command dispatcher.
3. For regular messages (non-commands), the Bot Base checks the **Session Router** for an active session mapped to this chat channel.
4. The **Daemon Client** sends the message via JSON-RPC over the SSH tunnel to the remote **Daemon**.
5. The **Daemon** spawns a `claude --print <message> --output-format stream-json` process.
6. Claude CLI processes the message and outputs JSON-lines to stdout.
7. The Daemon converts these to **StreamEvent** objects and sends them back as **SSE (Server-Sent Events)**.
8. The **Daemon Client** yields each event to the Bot Base, which formats and sends partial updates to the chat channel in real-time.
9. When Claude finishes (emits a `result` event), the SDK session ID is captured for future `--resume` calls.

## Key Design Decisions

### Per-Message Spawn (`claude --print`)

Rather than maintaining a long-running Claude CLI process with stdin/stdout, Remote Code spawns a fresh process for each user message:

```
claude --print "user message" --output-format stream-json --verbose \
       [--resume <sdkSessionId>] [--dangerously-skip-permissions]
```

This approach was adopted because Claude CLI (v2.1.76+) does not support `--input-format stream-json` without `--print`. The `--resume` flag maintains conversation continuity by passing the SDK session ID from previous interactions. Each process lives only for the duration of one message exchange.

**Benefits:**
- No zombie process management needed
- Clean process state for every interaction
- Natural recovery from crashes (just spawn a new process)
- Memory is freed between messages

### SSH Tunnels for Security

The daemon binds exclusively to `127.0.0.1` -- it is not accessible from the network. All access is through SSH port forwarding:

```
localhost:19100 ──SSH tunnel──▶ remote:127.0.0.1:9100
```

This means:
- No need to open firewall ports on remote machines
- Authentication and encryption are handled by SSH
- ProxyJump chains are supported for machines behind bastion hosts
- The daemon never exposes itself to the network

### SQLite for Session State

The Head Node uses SQLite (`sessions.db`) to persist session mappings between chat channels and remote Claude sessions. This ensures:

- Sessions survive Head Node restarts
- Multiple bots (Discord + Telegram) share the same session registry
- Session history is logged for auditing and resume capabilities
- The `session_log` table tracks detached sessions for later `--resume`

### SSE for Streaming Responses

When a message is sent to Claude, the daemon responds with an SSE (Server-Sent Events) stream rather than a single JSON response. This allows:

- Real-time streaming of Claude's output as it generates text
- Progressive rendering in chat (partial text updates with a cursor indicator `▌`)
- Keepalive pings every 30 seconds to prevent idle timeouts
- Graceful handling of client disconnections with response buffering

## Component Responsibilities

| Component | Runtime | Responsibility |
|---|---|---|
| **Discord Bot** | Python (discord.py) | Slash commands, typing indicators, heartbeat updates, 2000-char message splitting |
| **Telegram Bot** | Python (python-telegram-bot) | Command handlers, message handlers, 4096-char splitting, Markdown formatting |
| **Bot Base** | Python (abstract) | Command dispatch, session resolution, message forwarding with streaming display |
| **Session Router** | Python (sqlite3) | Channel-to-session mapping, lifecycle tracking (active/detached/destroyed) |
| **SSH Manager** | Python (asyncssh) | Connection pool, port forwarding, daemon deployment via SCP, skills sync |
| **Daemon Client** | Python (aiohttp) | JSON-RPC calls, SSE stream parsing, error handling |
| **RPC Server** | TypeScript (Express) | HTTP endpoint for JSON-RPC, SSE streaming, health checks |
| **Session Pool** | TypeScript | Claude CLI lifecycle, per-message spawn, event conversion |
| **Message Queue** | TypeScript | User message buffering, response buffering for SSH reconnect |
| **Skill Manager** | TypeScript | CLAUDE.md and skills directory sync to project paths |
