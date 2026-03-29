# Codecast

Codecast 是一个分布式系统，通过聊天机器人远程控制 AI 命令行工具。支持 Discord、Telegram 和飞书（Lark），可与 Claude CLI、Codex（OpenAI）、Gemini CLI 以及 OpenCode 协同工作。

该系统让你能够直接在手机或桌面聊天客户端中，启动、管理并与 GPU 服务器、云虚拟机或任何可通过 SSH 访问的机器上的 AI 会话进行交互。

## 为什么选择 Codecast？

在使用远程开发服务器时——例如防火墙后的 GPU 节点、只能通过跳板机访问的实验室机器、没有图形界面的云实例——往往需要在这些环境中运行 AI 命令行工具。Codecast 弥补了这一不足，让你无需打开终端，就能通过熟悉的聊天界面管理这些会话。

## 核心功能

**SSH 隧道管理** -- 自动建立 SSH 连接，支持 ProxyJump、端口转发和连接健康监测。守护进程只绑定到 `127.0.0.1`，所有通信都通过 SSH 隧道进行加密和认证。

**自动部署** -- 守护进程是一个单一的静态 Rust 二进制文件，首次连接时通过 SCP 自动部署到远程机器。远程端不需要 Node.js、npm 或任何手动配置。

**多 CLI 支持** -- 可以使用 Claude CLI、Codex（OpenAI）、Gemini CLI 或 OpenCode 启动会话。在 `/start` 命令中通过 `--cli` 标志为每个会话选择 CLI。

**会话路由** -- 基于 SQLite 的会话注册表将聊天频道映射到跨多台机器的活跃 AI 会话。会话可以独立地分离、恢复和销毁。

**消息队列** -- 当 AI 正在处理请求时，后续消息会被排队并按顺序处理。如果 SSH 连接在流式响应中途断开，响应会被缓存，并在重连后重新播放。

**流式响应** -- 响应通过 Server-Sent Events（SSE）实时回传，在聊天界面中逐步渲染局部文本更新。过长的响应会自动拆分以适应各平台的消息长度限制。

**工具显示模式** -- 三种模式控制响应过程中工具调用的显示方式：`timer`（显示已用时间，最终一并发送所有结果）、`append`（逐步展示每次工具调用）和 `batch`（将工具调用汇总后在最后一次性发送）。

**交互式问答** -- 当 AI 使用 `AskUserQuestion` 时，各平台以交互控件呈现问题：Discord 显示按钮，Telegram 显示内联键盘，飞书显示交互卡片。

**文件转发** -- 当 AI 响应中包含文件路径时，Codecast 可以自动从远程机器下载对应文件并发送到聊天中。

**权限模式** -- 四种模式控制 AI 自主程度：`auto`（绕过所有权限）、`code`（自动接受编辑，确认 bash 命令）、`plan`（只读分析）和 `ask`（确认所有操作）。会话期间可随时切换模式。

**技能同步** -- 跨远程机器的项目共享 `CLAUDE.md` 和 `.claude/skills/` 文件。技能文件在会话创建时从本地目录同步到远程项目路径，不会覆盖已有文件。

**Web UI 和 TUI** -- 除聊天机器人界面外，还提供基于浏览器的 Web UI 和交互式终端 UI（TUI）。

**模型切换** -- 使用 `/model` 命令在不重启会话的情况下切换 AI 模型。

## 支持平台

| 平台 | 访问控制 | 交互式问答 | 文件共享 |
|---|---|---|---|
| Discord | 频道白名单 | 按钮 | 附件 |
| Telegram | 用户 ID 白名单 | 内联键盘 | 文件消息 |
| 飞书（Lark） | 会话 ID 白名单 | 交互卡片 | 文件消息 |

## 项目结构

```
codecast/
├── src/
│   ├── head/                        # Head Node（Python）
│   │   ├── cli.py                   # CLI 入口
│   │   ├── main.py                  # Head Node 入口
│   │   ├── config.py                # 配置加载器
│   │   ├── engine.py                # 核心命令引擎
│   │   ├── ssh_manager.py           # SSH 连接与隧道
│   │   ├── session_router.py        # SQLite 会话注册表
│   │   ├── daemon_client.py         # JSON-RPC + SSE 客户端
│   │   ├── message_formatter.py     # 消息格式化
│   │   ├── file_forward.py          # 文件转发
│   │   ├── platform/                # 机器人适配器
│   │   │   ├── protocol.py          # 平台适配器接口
│   │   │   ├── discord_adapter.py
│   │   │   ├── telegram_adapter.py
│   │   │   └── lark_adapter.py
│   │   ├── tui/                     # 终端 UI（Textual）
│   │   └── webui/                   # Web UI（aiohttp）
│   └── daemon/                      # 守护进程（Rust）
│       ├── main.rs                  # Axum HTTP 服务器
│       ├── server.rs                # JSON-RPC 路由，SSE 流
│       ├── session_pool.rs          # CLI 进程管理
│       ├── message_queue.rs         # 消息缓冲
│       ├── cli_adapter/             # 多 CLI 适配器
│       │   ├── claude.rs
│       │   ├── codex.rs
│       │   ├── gemini.rs
│       │   └── opencode.rs
│       └── types.rs                 # 类型定义
├── docs/                            # 本文档
└── tests/                           # Python 测试（855+ 条）
```

## 快速导航

- [快速开始](./getting-started.md) -- 安装并运行 Codecast
- [配置指南](./configuration.md) -- 所有 config.yaml 选项说明
- [机器人命令参考](./commands.md) -- 每条聊天命令的详细说明
- [架构概述](./architecture.md) -- 了解双层设计
