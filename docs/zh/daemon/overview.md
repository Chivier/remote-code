# Daemon 概览

Daemon 是 Remote Code 系统的远程代理端，运行在远程 GPU 服务器上，使用 TypeScript/Node.js 编写。它管理 Claude CLI 进程的生命周期，提供 JSON-RPC 接口供 Head Node 调用。

## 模块架构

```
daemon/src/
├── server.ts        # Express RPC 服务器 — HTTP 端点和请求路由
├── session-pool.ts  # 会话池 — Claude CLI 进程管理
├── message-queue.ts # 消息队列 — 用户消息和响应缓冲
├── skill-manager.ts # 技能管理 — 技能文件同步到项目目录
└── types.ts         # 类型定义 — RPC 协议、会话、事件类型
```

## 设计理念

### 仅绑定本地回环

Daemon 只绑定到 `127.0.0.1`，不对外暴露端口。所有访问必须通过 SSH 隧道进行。这简化了安全模型——不需要任何认证或加密，因为 SSH 隧道已经提供了这些保护。

### 每消息生成进程（Per-Message Spawn）

这是 Daemon 最核心的设计决策。系统不维护长期运行的 Claude CLI 进程，而是为每条用户消息生成一个新的 `claude --print` 进程：

```
用户消息 → spawn("claude", ["--print", message, "--output-format", "stream-json", ...])
                                    ↓
                              进程输出 JSON-lines
                                    ↓
                              转换为 StreamEvent
                                    ↓
                              通过 SSE 推送给客户端
                                    ↓
                              进程退出 (exit code 0)
```

**为什么这样设计**：Claude CLI 2.1.76 不支持在非 `--print` 模式下使用 `--input-format stream-json`，因此无法通过 stdin 持续发送消息给一个长期运行的进程。

**会话连续性**：通过 `--resume <sdkSessionId>` 参数实现。Claude CLI 在每次输出结果时会包含一个 `session_id`，Daemon 保存这个 ID 并在下次调用时传递给 `--resume`。

### 消息队列

每个会话有独立的消息队列，负责：
1. 当 Claude 正忙时缓冲用户消息
2. 当 SSH 断连时缓冲响应事件
3. 断连重连后自动回放缓冲事件

## 模块依赖关系

```
              types.ts
                 │
    ┌────────────┼────────────┐
    ▼            ▼            ▼
server.ts  session-pool.ts  skill-manager.ts
    │            │
    │            ▼
    │      message-queue.ts
    │            │
    └────────────┘
         ↕
    HTTP 请求/响应
```

- **server.ts** → 依赖 `session-pool`、`skill-manager`、`types`
- **session-pool.ts** → 依赖 `message-queue`、`types`
- **message-queue.ts** → 依赖 `types`
- **skill-manager.ts** → 独立模块，仅依赖 Node.js 标准库
- **types.ts** → 被所有模块依赖

## 进程模型

```
Daemon (Node.js 主进程)
  │
  ├── Express HTTP Server (127.0.0.1:9100)
  │
  ├── Session A
  │   ├── 空闲状态 (无子进程)
  │   └── 处理消息时：
  │       └── claude --print "message" --output-format stream-json --verbose --resume <id>
  │
  └── Session B
      ├── 空闲状态 (无子进程)
      └── 处理消息时：
          └── claude --print "message" --output-format stream-json --verbose
```

## 通信协议

Daemon 使用 JSON-RPC over HTTP 协议：

- **普通请求** — POST `/rpc`，请求体为 JSON-RPC 格式，响应为 JSON
- **流式请求** — POST `/rpc` with `session.send` 方法，响应为 SSE (Server-Sent Events)
- **心跳** — SSE 流中每 30 秒发送一个 `ping` 事件

详细协议文档参见 [JSON-RPC 协议](../api/rpc-protocol.md) 和 [SSE 流事件](../api/sse-events.md)。
