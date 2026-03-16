# Head Node Overview

The Head Node is the local orchestrator component of Remote Code. It runs on your local machine (or a control server) and manages all user-facing interactions, SSH connections, session state, and communication with remote daemons.

## Technology Stack

- **Language:** Python 3.10+
- **SSH:** [asyncssh](https://asyncssh.readthedocs.io/) for async SSH connections and tunnels
- **HTTP Client:** [aiohttp](https://docs.aiohttp.org/) for JSON-RPC and SSE streaming
- **Discord:** [discord.py](https://discordpy.readthedocs.io/) v2 with slash commands
- **Telegram:** [python-telegram-bot](https://python-telegram-bot.readthedocs.io/) v20+ with async handlers
- **Database:** SQLite via Python's built-in `sqlite3` module
- **Config:** YAML via PyYAML

## Module Map

```
head/
├── main.py              # Entry point - initializes and runs everything
├── config.py            # Config dataclasses and YAML loader
├── ssh_manager.py       # SSH connections, tunnels, daemon deployment
├── session_router.py    # SQLite session registry
├── daemon_client.py     # JSON-RPC/SSE client to remote daemons
├── bot_base.py          # Abstract base class for bots
├── bot_discord.py       # Discord bot implementation
├── bot_telegram.py      # Telegram bot implementation
├── message_formatter.py # Output formatting and message splitting
└── __init__.py          # Package marker
```

## Module Dependencies

```
main.py
  ├── config.py          (load_config)
  ├── ssh_manager.py     (SSHManager)
  ├── session_router.py  (SessionRouter)
  ├── daemon_client.py   (DaemonClient)
  ├── bot_discord.py     (DiscordBot)
  └── bot_telegram.py    (TelegramBot)

bot_discord.py / bot_telegram.py
  └── bot_base.py        (BotBase)
        ├── ssh_manager.py
        ├── session_router.py
        ├── daemon_client.py
        └── message_formatter.py

ssh_manager.py
  └── config.py          (Config, MachineConfig)

session_router.py
  └── (standalone, uses sqlite3)

daemon_client.py
  └── (standalone, uses aiohttp)

message_formatter.py
  └── (standalone, no dependencies)
```

## Lifecycle

1. **Startup** (`main.py`): Load config, create shared instances (SSHManager, SessionRouter, DaemonClient), initialize bots, start listening.
2. **Command handling**: User sends `/start gpu-1 /path` via Discord/Telegram. Bot routes through BotBase, which calls SSHManager to set up the tunnel and DaemonClient to create a session.
3. **Message forwarding**: User sends a regular message. BotBase resolves the active session via SessionRouter, forwards to DaemonClient, streams response back to chat.
4. **Shutdown**: SIGTERM/SIGINT triggers graceful cleanup -- stop bots, close HTTP sessions, close SSH tunnels.

## Shared Resources

The three core infrastructure components are created once in `main.py` and shared across all bots:

- **SSHManager**: One instance manages all SSH connections and tunnels. Thread-safe through asyncio's single-threaded event loop.
- **SessionRouter**: One SQLite database (`sessions.db`) tracks sessions across both Discord and Telegram bots.
- **DaemonClient**: One aiohttp session handles all RPC calls to remote daemons.
