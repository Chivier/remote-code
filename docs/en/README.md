# Codecast

Codecast is a distributed system for controlling AI CLI tools on remote machines through chat bots. It supports Discord, Telegram, and Lark (Feishu), and works with Claude CLI, Codex (OpenAI), Gemini CLI, and OpenCode.

The system lets you start, manage, and interact with AI sessions on GPU servers, cloud VMs, or any SSH-accessible machine directly from your phone or desktop chat client.

## Why Codecast?

When working with remote development servers -- GPU nodes behind firewalls, lab machines accessible only via jump hosts, cloud instances without a GUI -- you often need to run AI CLI tools in those environments. Codecast bridges the gap by letting you manage those sessions through familiar chat interfaces without opening a terminal.

## Key Features

**SSH Tunnel Management** -- Automatic SSH connections with ProxyJump support, port forwarding, and connection health monitoring. The daemon only binds to `127.0.0.1`, so all communication is secured through SSH tunnels.

**Auto-Deployment** -- The daemon is a single static Rust binary. It is automatically deployed to remote machines via SCP on first connection. No Node.js, npm, or manual setup is needed on the remote side.

**Multi-CLI Support** -- Start sessions using Claude CLI, Codex (OpenAI), Gemini CLI, or OpenCode. Choose the CLI per session with the `--cli` flag on `/start`.

**Session Routing** -- A SQLite-backed session registry maps chat channels to active AI sessions across multiple machines. Sessions can be detached, resumed, and destroyed independently.

**Message Queuing** -- When the AI is busy processing a request, additional messages are queued and processed in order. If the SSH connection drops mid-stream, responses are buffered and replayed on reconnect.

**Streaming Responses** -- Responses stream back in real-time via Server-Sent Events (SSE), with partial text updates rendered progressively in chat. Long responses are automatically split to fit platform message limits.

**Tool Display Modes** -- Three modes control how tool calls are displayed during a response: `timer` (shows elapsed time, sends all results at the end), `append` (shows each tool call progressively), and `batch` (accumulates tool calls into a summary at the end).

**Interactive Questions** -- When the AI uses `AskUserQuestion`, each platform presents the question with interactive controls: buttons on Discord, an inline keyboard on Telegram, and interactive cards on Lark.

**File Forwarding** -- When AI responses reference file paths, Codecast can automatically download matching files from the remote machine and send them to your chat.

**Permission Modes** -- Four modes control AI autonomy: `auto` (bypass all permissions), `code` (auto-accept edits, confirm bash), `plan` (read-only analysis), and `ask` (confirm everything). Switch modes at any time during a session.

**Skills Sync** -- Share `CLAUDE.md` and `.claude/skills/` files across projects on remote machines. Skills are synced from a local directory to remote project paths on session creation, without overwriting existing files.

**Web UI and TUI** -- A browser-based Web UI and an interactive terminal UI (TUI) are available in addition to the chat bot interface.

**Model Switching** -- Switch the AI model mid-session with `/model` without restarting the session.

## Supported Platforms

| Platform | Access Control | Interactive Questions | File Sharing |
|---|---|---|---|
| Discord | Channel whitelist | Buttons | Attachments |
| Telegram | User ID whitelist | Inline keyboard | File messages |
| Lark (Feishu) | Chat ID whitelist | Interactive cards | File messages |

## Project Structure

```
codecast/
├── src/
│   ├── head/                        # Head Node (Python)
│   │   ├── cli.py                   # CLI entry point
│   │   ├── main.py                  # Head node entry
│   │   ├── config.py                # Config loader
│   │   ├── engine.py                # Core command engine
│   │   ├── ssh_manager.py           # SSH connections & tunnels
│   │   ├── session_router.py        # SQLite session registry
│   │   ├── daemon_client.py         # JSON-RPC + SSE client
│   │   ├── message_formatter.py     # Message formatting
│   │   ├── file_forward.py          # File forwarding
│   │   ├── platform/                # Bot adapters
│   │   │   ├── protocol.py          # Platform adapter interface
│   │   │   ├── discord_adapter.py
│   │   │   ├── telegram_adapter.py
│   │   │   └── lark_adapter.py
│   │   ├── tui/                     # Terminal UI (Textual)
│   │   └── webui/                   # Web UI (aiohttp)
│   └── daemon/                      # Daemon (Rust)
│       ├── main.rs                  # Axum HTTP server
│       ├── server.rs                # JSON-RPC router, SSE streaming
│       ├── session_pool.rs          # CLI process management
│       ├── message_queue.rs         # Message buffering
│       ├── cli_adapter/             # Multi-CLI adapters
│       │   ├── claude.rs
│       │   ├── codex.rs
│       │   ├── gemini.rs
│       │   └── opencode.rs
│       └── types.rs                 # Type definitions
├── docs/                            # This documentation
└── tests/                           # Python tests (855+ tests)
```

## Quick Links

- [Getting Started](./getting-started.md) -- Install and run Codecast
- [Configuration Guide](./configuration.md) -- All config.yaml options
- [Bot Command Reference](./commands.md) -- Every chat command explained
- [Architecture Overview](./architecture.md) -- Understand the two-tier design
