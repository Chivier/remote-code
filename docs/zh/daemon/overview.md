# 守护进程概览

守护进程是 Codecast 的远程代理组件。它运行在每台远程机器（GPU 服务器、云虚拟机等）上，提供用于管理 Claude CLI 会话的 JSON-RPC 接口。

## 技术栈

- **语言：** TypeScript
- **运行时：** Node.js 18+
- **HTTP 服务器：** Express.js
- **进程管理：** Node.js `child_process.spawn()`
- **UUID 生成：** `uuid` 包（v4）

## 模块结构

```
daemon/src/
├── server.ts         # Express JSON-RPC 服务器和 HTTP 端点
├── session-pool.ts   # Claude CLI 进程生命周期管理
├── message-queue.ts  # 每会话消息和响应缓冲
├── skill-manager.ts  # 技能文件同步到项目目录
└── types.ts          # RPC 协议和事件的 TypeScript 类型
```

## 模块依赖关系

```
server.ts
  ├── session-pool.ts  (SessionPool)
  ├── skill-manager.ts (SkillManager)
  └── types.ts         (RPC 类型)

session-pool.ts
  ├── message-queue.ts (MessageQueue)
  └── types.ts         (会话类型、流事件)

message-queue.ts
  └── types.ts         (QueuedUserMessage, QueuedResponse, StreamEvent)

skill-manager.ts
  └── （独立模块，使用 fs）

types.ts
  └── （独立模块，仅类型定义）
```

## 架构

守护进程使用**每消息生成进程**架构，而非维护长期运行的 Claude CLI 进程：

1. `session.create` 调用注册会话元数据（路径、模式），但**不**生成进程。
2. `session.send` 调用在单次消息交换期间生成一个 `claude --print <message> --output-format stream-json` 进程。
3. 进程在产生输出后退出。SDK 会话 ID 从 `result` 事件中捕获。
4. 下一次 `session.send` 调用以 `--resume <sdkSessionId>` 生成新进程，延续对话。

此设计具有以下优势：
- 每条消息的进程隔离
- 消息间自动清理内存
- 无需管理僵尸进程
- 从崩溃中自然恢复

## 安全性

守护进程只绑定到 `127.0.0.1`（localhost）。它无法从网络直接访问。所有访问都通过 Head Node 建立的 SSH 端口转发进行。

```typescript
const HOST = "127.0.0.1";
app.listen(PORT, HOST, () => { ... });
```

## 生命周期

1. **启动**：守护进程由 Head Node 的 SSHManager 通过 `nohup node dist/server.js` 启动。`DAEMON_PORT` 环境变量控制监听端口（默认：9100）。
2. **运行**：守护进程在 `POST /rpc` 上接受 JSON-RPC 请求。每个请求被路由到对应的处理器。
3. **关闭**：收到 SIGTERM/SIGINT 时，所有会话被销毁（进程终止、队列清空），然后进程退出。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DAEMON_PORT` | `9100` | 监听端口 |
| `HOME` | 系统默认值 | 用于定位技能源目录（`~/.codecast/skills`） |
