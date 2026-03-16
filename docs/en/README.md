# Remote Code

Remote Code is a distributed system for controlling [Claude CLI](https://docs.anthropic.com/en/docs/claude-cli) on remote machines through chat bots (Discord and Telegram). It enables you to interact with Claude on GPU servers, cloud VMs, or any SSH-accessible machine directly from your phone or desktop chat client.

## Why Remote Code?

When working with remote development servers -- GPU nodes behind firewalls, lab machines accessible only via jump hosts, cloud instances without a GUI -- you often need to run Claude CLI in those environments. Remote Code bridges the gap by letting you start, manage, and interact with Claude sessions on those machines through familiar chat interfaces.

## Key Features

- **SSH Tunnel Management** -- Automatic SSH connections with ProxyJump support, port forwarding, and connection health monitoring. The daemon only binds to `127.0.0.1`, so all communication is secured through SSH tunnels.

- **Auto-Deployment** -- The daemon (TypeScript/Node.js) is automatically built locally, deployed to remote machines via SCP, and started when you first connect. No manual setup needed on the remote side.

- **Session Routing** -- SQLite-backed session registry that maps chat channels to active Claude sessions across multiple machines. Sessions can be detached, resumed, and destroyed independently.

- **Message Queuing** -- When Claude is busy processing a request, additional messages are queued and processed in order. If the SSH connection drops mid-stream, responses are buffered and replayed on reconnect.

- **Skills Sync** -- Share `CLAUDE.md` and `.claude/skills/` files across projects on remote machines. Skills are synced from a local directory to remote project paths on session creation, without overwriting existing project-specific files.

- **Multi-Platform Bot Support** -- Both Discord (with slash commands, autocomplete, typing indicators, and heartbeat status) and Telegram (with Markdown formatting and user-based access control) are supported. Run one or both simultaneously.

- **Streaming Responses** -- Claude's responses are streamed back in real-time via SSE (Server-Sent Events), with partial text updates rendered progressively in chat. Long responses are automatically split to fit platform message limits.

- **Permission Modes** -- Four modes control Claude's autonomy: `auto` (bypass all permissions), `code` (auto-accept edits, confirm bash), `plan` (read-only analysis), and `ask` (confirm everything). Switch modes at any time during a session.

## Project Structure

```
happy-moon/
├── head/                    # Head Node (Python) - local orchestrator
│   ├── main.py              # Entry point
│   ├── config.py            # Configuration loader
│   ├── ssh_manager.py       # SSH tunnels & daemon lifecycle
│   ├── session_router.py    # SQLite session registry
│   ├── daemon_client.py     # JSON-RPC client
│   ├── bot_base.py          # Abstract bot base class
│   ├── bot_discord.py       # Discord bot implementation
│   ├── bot_telegram.py      # Telegram bot implementation
│   └── message_formatter.py # Output formatting & message splitting
├── daemon/                  # Daemon (TypeScript) - runs on remote machines
│   └── src/
│       ├── server.ts        # Express JSON-RPC server
│       ├── session-pool.ts  # Claude CLI process management
│       ├── message-queue.ts # Message & response buffering
│       ├── skill-manager.ts # Skills file sync
│       └── types.ts         # Type definitions & RPC protocol
├── config.example.yaml      # Example configuration
├── requirements.txt         # Python dependencies
└── docs/                    # This documentation
```

## Quick Links

- [Architecture Overview](./architecture.md) -- Understand the two-tier design
- [Getting Started](./getting-started.md) -- Install and run Remote Code
- [Configuration Guide](./configuration.md) -- All config.yaml options
- [Bot Command Reference](./commands.md) -- Every chat command explained
- [JSON-RPC Protocol](./api/rpc-protocol.md) -- Daemon API reference
