# Head Node 概览

Head Node 是 Codecast 的本地编排组件。它运行在你的本地机器（或控制服务器）上，负责管理所有面向用户的交互、SSH 连接、会话状态以及与远程守护进程的通信。

## 技术栈

- **语言：** Python 3.10+
- **SSH：** [asyncssh](https://asyncssh.readthedocs.io/)，用于异步 SSH 连接和隧道
- **HTTP 客户端：** [aiohttp](https://docs.aiohttp.org/)，用于 JSON-RPC 和 SSE 流
- **Discord：** [discord.py](https://discordpy.readthedocs.io/) v2，带斜杠命令支持
- **Telegram：** [python-telegram-bot](https://python-telegram-bot.readthedocs.io/) v20+，异步处理器
- **数据库：** SQLite，通过 Python 内置的 `sqlite3` 模块
- **配置：** YAML，通过 PyYAML

## 模块结构

```
head/
├── main.py              # 入口点——初始化并运行所有组件
├── config.py            # 配置数据类和 YAML 加载器
├── ssh_manager.py       # SSH 连接、隧道、守护进程部署
├── session_router.py    # SQLite 会话注册表
├── daemon_client.py     # 到远程守护进程的 JSON-RPC/SSE 客户端
├── bot_base.py          # 机器人抽象基类
├── bot_discord.py       # Discord 机器人实现
├── bot_telegram.py      # Telegram 机器人实现
├── message_formatter.py # 输出格式化和消息拆分
└── __init__.py          # 包标记
```

## 模块依赖关系

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
  └── （独立模块，使用 sqlite3）

daemon_client.py
  └── （独立模块，使用 aiohttp）

message_formatter.py
  └── （独立模块，无外部依赖）
```

## 生命周期

1. **启动**（`main.py`）：加载配置，创建共享实例（SSHManager、SessionRouter、DaemonClient），初始化机器人，开始监听。
2. **命令处理**：用户通过 Discord/Telegram 发送 `/start gpu-1 /path`。机器人通过 BotBase 路由，后者调用 SSHManager 建立隧道，调用 DaemonClient 创建会话。
3. **消息转发**：用户发送普通消息。BotBase 通过 SessionRouter 解析活跃会话，转发给 DaemonClient，将响应流式回传到聊天。
4. **关闭**：SIGTERM/SIGINT 触发优雅清理——停止机器人、关闭 HTTP 会话、关闭 SSH 隧道。

## 共享资源

三个核心基础设施组件在 `main.py` 中只创建一次，并在所有机器人之间共享：

- **SSHManager**：一个实例管理所有 SSH 连接和隧道。通过 asyncio 的单线程事件循环保证线程安全。
- **SessionRouter**：一个 SQLite 数据库（`sessions.db`）追踪 Discord 和 Telegram 机器人中的所有会话。
- **DaemonClient**：一个 aiohttp 会话处理对远程守护进程的所有 RPC 调用。
