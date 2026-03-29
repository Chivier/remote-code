# Summary

# User Guide

- [Introduction](./README.md)
- [Getting Started](./getting-started.md)
- [Configuration Guide](./configuration.md)
- [Bot Command Reference](./commands.md)

# Developer Guide

- [Architecture Overview](./architecture.md)
- [Head Node (Python)]()
  - [Overview](./head/overview.md)
  - [Entry Point (main.py)](./head/main.md)
  - [Config Loader (config.py)](./head/config.md)
  - [Command Engine (engine.py)](./head/bot-base.md)
  - [SSH Manager (ssh_manager.py)](./head/ssh-manager.md)
  - [Session Router (session_router.py)](./head/session-router.md)
  - [Daemon Client (daemon_client.py)](./head/daemon-client.md)
  - [Discord Adapter](./head/bot-discord.md)
  - [Telegram Adapter](./head/bot-telegram.md)
  - [Message Formatter](./head/message-formatter.md)
- [Daemon (Rust)]()
  - [Overview](./daemon/overview.md)
  - [RPC Server (server.rs)](./daemon/server.md)
  - [Session Pool (session_pool.rs)](./daemon/session-pool.md)
  - [Message Queue (message_queue.rs)](./daemon/message-queue.md)
  - [Skills Sync](./daemon/skill-manager.md)
  - [Type Definitions (types.rs)](./daemon/types.md)
- [API Reference]()
  - [JSON-RPC Protocol](./api/rpc-protocol.md)
  - [SSE Stream Events](./api/sse-events.md)
