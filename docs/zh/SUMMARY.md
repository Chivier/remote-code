# 目录

# 用户手册

- [简介](./README.md)
- [快速开始](./getting-started.md)
- [配置指南](./configuration.md)
- [Bot 命令参考](./commands.md)

# 开发者手册

- [架构概览](./architecture.md)
- [Head Node (Python)]()
  - [概览](./head/overview.md)
  - [主入口 (main.py)](./head/main.md)
  - [配置加载 (config.py)](./head/config.md)
  - [命令引擎 (engine.py)](./head/bot-base.md)
  - [SSH 管理 (ssh_manager.py)](./head/ssh-manager.md)
  - [会话路由 (session_router.py)](./head/session-router.md)
  - [Daemon 客户端 (daemon_client.py)](./head/daemon-client.md)
  - [Discord 适配器](./head/bot-discord.md)
  - [Telegram 适配器](./head/bot-telegram.md)
  - [消息格式化](./head/message-formatter.md)
- [Daemon (Rust)]()
  - [概览](./daemon/overview.md)
  - [RPC 服务器 (server.rs)](./daemon/server.md)
  - [会话池 (session_pool.rs)](./daemon/session-pool.md)
  - [消息队列 (message_queue.rs)](./daemon/message-queue.md)
  - [技能同步](./daemon/skill-manager.md)
  - [类型定义 (types.rs)](./daemon/types.md)
- [API 参考]()
  - [JSON-RPC 协议](./api/rpc-protocol.md)
  - [SSE 流事件](./api/sse-events.md)
