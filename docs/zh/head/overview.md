# Head Node 概览

Head Node 是 Remote Code 系统的本地控制端，使用 Python 编写，负责编排所有组件：管理 SSH 连接、路由会话、与远程 Daemon 通信以及运行聊天 Bot。

## 模块架构

```
head/
├── main.py              # 主入口 — 加载配置、初始化组件、启动 Bot
├── config.py            # 配置加载 — YAML 解析 + 环境变量展开
├── ssh_manager.py       # SSH 管理 — 连接池、隧道、Daemon 部署、技能同步
├── session_router.py    # 会话路由 — SQLite 持久化会话状态
├── daemon_client.py     # Daemon 客户端 — JSON-RPC + SSE 流式通信
├── bot_base.py          # Bot 基类 — 命令分发 + 消息转发 + 流式显示
├── bot_discord.py       # Discord Bot — 斜杠命令、自动补全、心跳
├── bot_telegram.py      # Telegram Bot — 命令处理器 + 消息处理器
└── message_formatter.py # 消息格式化 — 消息分割 + 格式化输出
```

## 模块职责和依赖关系

```
                config.py
                    │
                    ▼
              ┌──────────┐
              │ main.py  │
              └────┬─────┘
                   │
         ┌─────────┼──────────┐
         ▼         ▼          ▼
   ssh_manager  session_   daemon_
      .py       router.py  client.py
         │                    │
         └────────┬───────────┘
                  ▼
            ┌──────────┐
            │ bot_base │
            │   .py    │
            └────┬─────┘
                 │
          ┌──────┴──────┐
          ▼             ▼
    bot_discord    bot_telegram
       .py            .py
          │             │
          └──────┬──────┘
                 ▼
         message_formatter
               .py
```

### 依赖关系说明

- **main.py** → 依赖所有其他模块，负责实例化和协调
- **bot_base.py** → 依赖 `ssh_manager`、`session_router`、`daemon_client`、`message_formatter`
- **bot_discord.py** / **bot_telegram.py** → 继承 `bot_base`，依赖 `message_formatter`
- **ssh_manager.py** → 依赖 `config`
- **session_router.py** → 独立模块，仅依赖标准库
- **daemon_client.py** → 独立模块，依赖 `aiohttp`
- **message_formatter.py** → 独立模块，纯工具函数

## 数据流

### 命令处理流

```
用户发送 /start gpu-1 /path
        │
        ▼
  bot_discord/telegram (平台适配)
        │
        ▼
  bot_base._handle_command()  (命令分发)
        │
        ▼
  bot_base.cmd_start()
        │
        ├─→ ssh_manager.ensure_tunnel()     → 建立 SSH 隧道
        ├─→ ssh_manager.sync_skills()       → 同步技能文件
        ├─→ daemon_client.create_session()  → 创建远程会话
        └─→ session_router.register()       → 注册本地会话状态
```

### 消息转发流

```
用户发送普通文本消息
        │
        ▼
  bot_base._forward_message() / bot_discord._forward_message_with_heartbeat()
        │
        ├─→ session_router.resolve()         → 查找活跃会话
        ├─→ ssh_manager.ensure_tunnel()      → 确保隧道存活
        ├─→ daemon_client.send_message()     → 发送消息 (SSE 流)
        │       │
        │       ▼ (async for event in stream)
        │   ┌─ partial → 累积到 buffer，定期更新消息
        │   ├─ text    → 完整文本块，发送新消息
        │   ├─ tool_use → 格式化工具使用信息
        │   ├─ result  → 保存 sdk_session_id
        │   ├─ error   → 显示错误信息
        │   └─ queued  → 通知用户消息已排队
        │
        └─→ message_formatter.split_message() → 长消息自动分割
```

## 关键设计

### 异步架构

Head Node 全面使用 Python `asyncio`，所有 I/O 操作都是异步的：
- SSH 连接和隧道使用 `asyncssh`
- HTTP 请求使用 `aiohttp`
- Discord Bot 使用 `discord.py` 的异步接口
- Telegram Bot 使用 `python-telegram-bot` v20+ 的异步接口

### 平台抽象

`BotBase` 抽象基类定义了统一的接口：
- `send_message()` / `edit_message()` — 平台无关的消息操作
- `start()` / `stop()` — 生命周期管理
- 所有命令逻辑在基类中实现，子类只需实现平台特定的消息发送

### 优雅关闭

通过信号处理器（SIGTERM/SIGINT）实现优雅关闭：
1. 设置 shutdown_event
2. 停止所有 Bot
3. 关闭 HTTP 客户端
4. 关闭所有 SSH 隧道
5. 取消残余异步任务
