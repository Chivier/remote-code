# Remote Code

Remote Code 是一个分布式系统，用于通过 Discord 和 Telegram 聊天机器人远程控制部署在远程服务器（如 GPU 服务器）上的 Claude CLI。

## 项目概述

在日常开发中，我们经常需要在远程 GPU 服务器上使用 Claude CLI 进行代码编写、调试和分析。但直接 SSH 到远程机器使用 CLI 存在诸多不便：终端会话易断、多机器切换繁琐、无法在移动设备上操作等。

Remote Code 解决了这些问题。它在本地运行一个 Head Node（控制节点），通过 SSH 隧道连接到远程机器上的 Daemon（守护进程），并将 Discord/Telegram 消息转发给 Claude CLI，再将 Claude 的流式响应实时推送回聊天窗口。

## 核心特性

- **SSH 隧道管理** — 自动建立和维护到远程机器的 SSH 隧道，支持跳板机（ProxyJump）和密码认证
- **自动部署** — 自动将 Daemon 代码部署到远程机器并启动，无需手动安装
- **会话路由** — SQLite 持久化存储会话状态，支持会话的创建、分离、恢复和销毁
- **消息队列** — 当 Claude 正在处理时自动排队用户消息，SSH 断连时缓存响应数据
- **技能同步** — 将本地的 CLAUDE.md 和 .claude/skills/ 同步到远程项目目录
- **多平台 Bot 支持** — 同时支持 Discord（带斜杠命令和自动补全）和 Telegram
- **流式响应** — 通过 SSE（Server-Sent Events）实时流式传输 Claude 的回复，含打字指示器和心跳状态更新
- **权限模式** — 四种权限模式可选：auto（完全自动）、code（自动接受编辑）、plan（只读分析）、ask（全部确认）

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| Head Node | Python 3.11+ | 本地编排器，运行 Bot 和管理 SSH |
| Daemon | TypeScript / Node.js | 远程代理，管理 Claude CLI 进程 |
| 通信协议 | JSON-RPC over HTTP | Head ↔ Daemon 通信 |
| 流式传输 | SSE (Server-Sent Events) | Claude 响应的实时推送 |
| 会话存储 | SQLite | Head Node 端的会话状态持久化 |
| SSH | asyncssh | 异步 SSH 连接和隧道管理 |

## 项目结构

```
remote-claude/
├── head/                    # Head Node (Python)
│   ├── main.py              # 主入口
│   ├── config.py            # 配置加载
│   ├── ssh_manager.py       # SSH 隧道管理
│   ├── session_router.py    # 会话路由（SQLite）
│   ├── daemon_client.py     # Daemon RPC 客户端
│   ├── bot_base.py          # Bot 抽象基类
│   ├── bot_discord.py       # Discord Bot 实现
│   ├── bot_telegram.py      # Telegram Bot 实现
│   └── message_formatter.py # 消息格式化
├── daemon/                  # Daemon (TypeScript)
│   └── src/
│       ├── server.ts        # Express RPC 服务器
│       ├── session-pool.ts  # 会话池管理
│       ├── message-queue.ts # 消息队列
│       ├── skill-manager.ts # 技能管理
│       └── types.ts         # 类型定义
├── config.example.yaml      # 示例配置文件
└── docs/                    # 文档
```

## 快速链接

- [架构概览](./architecture.md) — 了解系统的整体设计
- [快速开始](./getting-started.md) — 开始使用 Remote Code
- [配置指南](./configuration.md) — 详细的配置说明
- [Bot 命令参考](./commands.md) — 所有可用的聊天命令
- [API 参考](./api/rpc-protocol.md) — JSON-RPC 协议文档
