# Head Node Overview

The Head Node is the local orchestrator component of Codecast. It runs on your local machine (or a control server) and manages all user-facing interactions, SSH connections, session state, and communication with remote daemons.

## Technology Stack

- **Language:** Python 3.10+
- **SSH:** [asyncssh](https://asyncssh.readthedocs.io/) for async SSH connections and tunnels
- **HTTP Client:** [aiohttp](https://docs.aiohttp.org/) for JSON-RPC and SSE streaming
- **Discord:** [discord.py](https://discordpy.readthedocs.io/) v2 with slash commands
- **Telegram:** [python-telegram-bot](https://python-telegram-bot.readthedocs.io/) v20+ with async handlers
- **Lark/Feishu:** lark-oapi SDK
- **Database:** SQLite via Python's built-in `sqlite3` module
- **Config:** YAML via `ruamel.yaml` (comment-preserving round-trip editing)

## Module Map

```
src/head/
├── cli.py                   # CLI entry point: argparse, subcommand dispatch
├── main.py                  # Head node entry: loads config, starts adapters, shutdown
├── config.py                # Config dataclasses, YAML loader, env var expansion
├── engine.py                # BotEngine: all command logic and message forwarding
├── ssh_manager.py           # SSH connections, tunnels, daemon deployment
├── session_router.py        # SQLite-backed session registry
├── daemon_client.py         # JSON-RPC + SSE client for daemon communication
├── message_formatter.py     # Output formatting, message splitting, tool batching
├── file_forward.py          # File forwarding: detect and forward file paths to users
├── platform/
│   ├── protocol.py          # PlatformAdapter protocol, MessageHandle, FileAttachment
│   ├── discord_adapter.py   # Discord PlatformAdapter (discord.py v2)
│   ├── telegram_adapter.py  # Telegram PlatformAdapter (python-telegram-bot v20+)
│   ├── lark_adapter.py      # Lark/Feishu PlatformAdapter
│   ├── format_utils.py      # markdown_to_telegram_html() and other format helpers
│   └── __init__.py
├── tui/                     # Interactive TUI (Textual)
└── webui/                   # Web UI (aiohttp)
```

## Module Dependencies

```
main.py
  ├── config.py              (load_config, Config)
  ├── ssh_manager.py         (SSHManager)
  ├── session_router.py      (SessionRouter)
  ├── daemon_client.py       (DaemonClient)
  ├── platform/discord_adapter.py   (DiscordAdapter)
  ├── platform/telegram_adapter.py  (TelegramAdapter)
  └── platform/lark_adapter.py      (LarkAdapter)

engine.py  (BotEngine)
  ├── platform/protocol.py   (PlatformAdapter, MessageHandle, FileAttachment)
  ├── ssh_manager.py         (SSHManager)
  ├── session_router.py      (SessionRouter)
  ├── daemon_client.py       (DaemonClient)
  ├── message_formatter.py   (formatting functions)
  └── file_forward.py        (FileForwardMatcher)

platform/discord_adapter.py  (DiscordAdapter)
  ├── platform/protocol.py   (PlatformAdapter, MessageHandle, FileAttachment)
  └── message_formatter.py   (split_message, format_error, display_mode)

platform/telegram_adapter.py  (TelegramAdapter)
  ├── platform/protocol.py   (PlatformAdapter, MessageHandle, FileAttachment)
  ├── message_formatter.py   (split_message)
  └── platform/format_utils.py (markdown_to_telegram_html)

ssh_manager.py
  └── config.py              (Config, PeerConfig)

session_router.py
  └── (standalone, uses sqlite3)

daemon_client.py
  └── (standalone, uses aiohttp)

message_formatter.py
  └── (standalone, no dependencies)

file_forward.py
  └── (standalone)
```

## Architecture: BotEngine + PlatformAdapter

The Head Node uses a composition pattern rather than inheritance. `BotEngine` (in `engine.py`) contains all command and streaming logic and holds a `PlatformAdapter` instance for platform-specific I/O.

Each platform adapter (Discord, Telegram, Lark) implements the `PlatformAdapter` protocol defined in `platform/protocol.py`. The protocol includes methods for:
- Sending and editing messages (`send_message`, `edit_message`, `delete_message`)
- File operations (`download_file`, `send_file`)
- Interaction state (`start_typing`, `stop_typing`)
- Interactive questions (`send_question`) for AskUserQuestion handling
- Lifecycle (`start`, `stop`)

When a user message arrives, the platform adapter calls the registered `InputHandler` callback on the BotEngine, passing the channel ID, text, optional user ID, and any file attachments.

## Lifecycle

1. **Startup** (`main.py`): Load config, create shared infrastructure instances (SSHManager, SessionRouter, DaemonClient), create one BotEngine per platform adapter, start adapters.
2. **Command handling**: User sends `/start gpu-1 /path` via Discord/Telegram/Lark. The adapter calls `engine.handle_input()`, which routes to `cmd_start()`. This calls SSHManager to set up the tunnel and DaemonClient to create a session.
3. **Message forwarding**: User sends a regular message. BotEngine resolves the active session via SessionRouter, forwards to DaemonClient, streams the SSE response back to chat in real time.
4. **Shutdown**: SIGTERM/SIGINT triggers graceful cleanup — stop adapters, close HTTP sessions, close SSH tunnels.

## Shared Infrastructure

The core infrastructure components are created once in `main.py` and shared across all platform adapters:

- **SSHManager**: One instance manages all SSH connections and tunnels. Thread-safe through asyncio's single-threaded event loop.
- **SessionRouter**: One SQLite database (`sessions.db`) tracks sessions across all platforms (Discord, Telegram, Lark).
- **DaemonClient**: One aiohttp session handles all RPC calls to remote daemons.

Each platform gets its own `BotEngine` instance, but all engines share the same SSHManager, SessionRouter, and DaemonClient.
