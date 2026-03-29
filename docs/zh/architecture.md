# 架构概述

Codecast 采用双层架构，由一个 **Head Node**（本地编排器）和一个或多个 **Daemon**（远程代理）组成。这一设计将用户交互、连接管理和 Claude CLI 执行等关注点分离开来。

## 系统图

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户设备                                  │
│                                                                 │
│   ┌──────────┐          ┌──────────┐                            │
│   │ Discord  │          │ Telegram │                            │
│   │  客户端  │          │  客户端  │                            │
│   └────┬─────┘          └────┬─────┘                            │
└────────┼─────────────────────┼──────────────────────────────────┘
         │                     │
         │  Discord API        │  Telegram API
         │                     │
┌────────┼─────────────────────┼──────────────────────────────────┐
│        ▼                     ▼         HEAD NODE（Python）       │
│   ┌──────────┐          ┌──────────┐                            │
│   │ Discord  │          │ Telegram │                            │
│   │  Bot     │          │  Bot     │                            │
│   └────┬─────┘          └────┬─────┘                            │
│        │                     │                                  │
│        └──────────┬──────────┘                                  │
│                   ▼                                             │
│            ┌─────────────┐     ┌─────────────────┐              │
│            │  Bot Base   │────▶│  Session Router  │              │
│            │  （命令处理） │     │  （SQLite）       │              │
│            └──────┬──────┘     └─────────────────┘              │
│                   │                                             │
│                   ▼                                             │
│            ┌─────────────┐     ┌─────────────────┐              │
│            │   Daemon    │────▶│   SSH Manager    │              │
│            │   Client    │     │（隧道、部署）      │              │
│            └──────┬──────┘     └────────┬────────┘              │
└───────────────────┼─────────────────────┼───────────────────────┘
                    │                     │
                    │  JSON-RPC/SSE       │  SSH 隧道
                    │  经由 SSH 隧道      │  （端口转发）
                    │                     │
┌───────────────────┼─────────────────────┼───────────────────────┐
│                   ▼                     ▼    远程机器            │
│            ┌─────────────┐                                      │
│            │  Express RPC│ ◄── 127.0.0.1:9100                   │
│            │    Server   │                                      │
│            └──────┬──────┘                                      │
│                   │                                             │
│                   ▼                                             │
│            ┌─────────────┐     ┌─────────────────┐              │
│            │ Session Pool│────▶│  Message Queue   │              │
│            │             │     └─────────────────┘              │
│            └──────┬──────┘                                      │
│                   │                                             │
│                   ▼  每条消息生成一个进程                         │
│            ┌─────────────┐                                      │
│            │ claude      │                                      │
│            │ --print     │                                      │
│            │ --stream    │                                      │
│            └─────────────┘                                      │
└─────────────────────────────────────────────────────────────────┘
```

## 数据流

一次典型的用户交互按以下路径执行：

1. **用户**通过 Discord/Telegram 发送消息或命令。
2. **Bot**（Discord 或 Telegram）接收消息，通过 **Bot Base** 命令分发器进行路由。
3. 对于普通消息（非命令），Bot Base 在 **Session Router** 中查找映射到当前聊天频道的活跃会话。
4. **Daemon Client** 通过 SSH 隧道以 JSON-RPC 方式将消息发送到远程 **Daemon**。
5. **Daemon** 生成一个 `claude --print <message> --output-format stream-json` 进程。
6. Claude CLI 处理消息并向 stdout 输出 JSON-lines 格式的内容。
7. Daemon 将这些内容转换为 **StreamEvent** 对象，并以 **SSE（Server-Sent Events）** 方式回传。
8. **Daemon Client** 将每个事件传递给 Bot Base，后者对内容进行格式化并将部分更新实时发送到聊天频道。
9. 当 Claude 完成（发出 `result` 事件）时，SDK 会话 ID 会被捕获，供后续的 `--resume` 调用使用。

## 关键设计决策

### 每消息生成进程（`claude --print`）

Codecast 不维护带有 stdin/stdout 的长期运行 Claude CLI 进程，而是为每条用户消息生成一个新进程：

```
claude --print "user message" --output-format stream-json --verbose \
       [--resume <sdkSessionId>] [--dangerously-skip-permissions]
```

采用这一方式是因为 Claude CLI（v2.1.76 及以上）在不使用 `--print` 的情况下不支持 `--input-format stream-json`。`--resume` 标志通过传递前一次交互的 SDK 会话 ID 来保持对话连续性。每个进程只在一次消息交换期间存活。

**优势：**
- 无需管理僵尸进程
- 每次交互都有干净的进程状态
- 从崩溃中自然恢复（重新生成进程即可）
- 消息之间释放内存

### SSH 隧道安全性

守护进程只绑定到 `127.0.0.1`——无法从网络直接访问。所有访问都通过 SSH 端口转发进行：

```
localhost:19100 ──SSH 隧道──▶ remote:127.0.0.1:9100
```

这意味着：
- 无需在远程机器上开放防火墙端口
- SSH 负责认证和加密
- 支持跳板机链（ProxyJump）访问堡垒机后的机器
- 守护进程永远不会暴露到网络上

### SQLite 会话状态持久化

Head Node 使用 SQLite（`sessions.db`）持久化聊天频道与远程 Claude 会话之间的映射关系。这确保了：

- 会话在 Head Node 重启后依然存在
- 多个机器人（Discord + Telegram）共享同一个会话注册表
- 会话历史记录用于审计和恢复
- `session_log` 表追踪已分离的会话，供后续 `--resume` 使用

### SSE 流式响应

向 Claude 发送消息时，守护进程以 SSE（Server-Sent Events）流而非单条 JSON 响应来回传结果。这允许：

- Claude 生成文本时实时流式传输输出
- 在聊天中逐步渲染（带光标指示器 `▌` 的部分文本更新）
- 每 30 秒发送一次保活 ping，防止空闲超时
- 优雅处理客户端断连，通过响应缓冲实现重连

## 组件职责

| 组件 | 运行时 | 职责 |
|---|---|---|
| **Discord Bot** | Python（discord.py） | 斜杠命令、打字指示器、心跳更新、2000 字符消息拆分 |
| **Telegram Bot** | Python（python-telegram-bot） | 命令处理器、消息处理器、4096 字符拆分、Markdown 格式化 |
| **Bot Base** | Python（抽象基类） | 命令分发、会话解析、带流式显示的消息转发 |
| **Session Router** | Python（sqlite3） | 频道到会话的映射、生命周期追踪（active/detached/destroyed） |
| **SSH Manager** | Python（asyncssh） | 连接池、端口转发、通过 SCP 部署守护进程、技能同步 |
| **Daemon Client** | Python（aiohttp） | JSON-RPC 调用、SSE 流解析、错误处理 |
| **RPC Server** | TypeScript（Express） | JSON-RPC 的 HTTP 端点、SSE 流、健康检查 |
| **Session Pool** | TypeScript | Claude CLI 生命周期、每消息生成进程、事件转换 |
| **Message Queue** | TypeScript | 用户消息缓冲、SSH 重连的响应缓冲 |
| **Skill Manager** | TypeScript | 将 CLAUDE.md 和技能目录同步到项目路径 |
