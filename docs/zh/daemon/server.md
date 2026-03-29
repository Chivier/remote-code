# RPC 服务器（server.ts）

**文件：** `daemon/src/server.ts`

基于 Express 的 JSON-RPC 服务器，为所有守护进程操作提供 HTTP 端点。负责方法路由、SSE 流、保活 ping 和优雅关闭。

## 用途

- 为所有 JSON-RPC 方法提供单一的 `POST /rpc` 端点
- 根据 `method` 字段将请求路由到对应的处理器
- 通过 SSE（Server-Sent Events）为 `session.send` 流式传输响应
- 发送保活 ping，防止空闲超时
- 处理 SIGTERM/SIGINT 时的优雅关闭

## 服务器配置

```typescript
const PORT = parseInt(process.env.DAEMON_PORT || "9100", 10);
const HOST = "127.0.0.1"; // 只能通过 SSH 隧道访问
```

服务器只绑定到 localhost。端口可通过 `DAEMON_PORT` 环境变量配置。

## 组件

服务器在启动时创建两个单例实例：

```typescript
const sessionPool = new SessionPool();
const skillManager = new SkillManager();
```

同时记录 `startTime` 用于健康检查中的运行时间计算。

## 方法路由

所有请求通过 `POST /rpc` 传入。请求体中的 `method` 字段决定调用哪个处理器：

| 方法 | 处理器 | 响应类型 |
|---|---|---|
| `session.create` | `handleCreateSession` | JSON |
| `session.send` | `handleSendMessage` | SSE 流 |
| `session.resume` | `handleResumeSession` | JSON |
| `session.destroy` | `handleDestroySession` | JSON |
| `session.list` | `handleListSessions` | JSON |
| `session.set_mode` | `handleSetMode` | JSON |
| `session.interrupt` | `handleInterruptSession` | JSON |
| `session.queue_stats` | `handleQueueStats` | JSON |
| `session.reconnect` | `handleReconnect` | JSON |
| `health.check` | `handleHealthCheck` | JSON |
| `monitor.sessions` | `handleMonitorSessions` | JSON |

未知方法返回错误码 `-32601`（方法不存在）。

## SSE 流（session.send）

`handleSendMessage` 处理器特殊——它以 SSE 流而非 JSON 正文响应：

```typescript
res.setHeader("Content-Type", "text/event-stream");
res.setHeader("Cache-Control", "no-cache");
res.setHeader("Connection", "keep-alive");
res.setHeader("X-Accel-Buffering", "no"); // 禁用 nginx 缓冲
```

### 客户端断连处理

服务器监听响应上的 `close` 事件以检测客户端断连：

```typescript
res.on("close", () => {
    clientDisconnected = true;
    sessionPool.clientDisconnect(params.sessionId);
});
```

如果客户端在流式传输过程中断连，剩余事件通过 `sessionPool.bufferEvent()` 缓冲，供后续使用 `session.reconnect` 检索。

### 保活 Ping

保活定时器每 30 秒发送一个 `ping` 事件，防止空闲 SSH 隧道超时：

```typescript
const keepaliveInterval = setInterval(() => {
    res.write(`data: ${JSON.stringify({ type: "ping" })}\n\n`);
}, 30000);
```

### 流终止

所有事件发送完毕后，流以 `data: [DONE]\n\n` 结束。如果发生错误，错误事件在 `[DONE]` 之前发送。

## 方法处理器

### `handleCreateSession`

1. 验证 `path` 参数
2. 通过 `skillManager.syncToProject()` 将技能同步到项目目录
3. 在池中创建会话（轻量级——不生成进程）
4. 返回 `{ sessionId }`

### `handleSendMessage`

参见上方的 SSE 流部分。

### `handleResumeSession`

委托给 `sessionPool.resume()`。返回 `{ ok, fallback }`。

### `handleDestroySession`

委托给 `sessionPool.destroy()`。返回 `{ ok }`。

### `handleListSessions`

返回 `{ sessions: [...] }`，包含所有会话信息。

### `handleSetMode`

委托给 `sessionPool.setMode()`。返回 `{ ok }`。

### `handleInterruptSession`

委托给 `sessionPool.interrupt()`。返回 `{ ok, interrupted }`。

### `handleQueueStats`

返回特定会话的队列统计：`{ userPending, responsePending, clientConnected }`。

### `handleReconnect`

调用 `sessionPool.clientReconnect()` 将客户端标记为已重连，并检索缓冲的事件。返回 `{ bufferedEvents: [...] }`。

### `handleHealthCheck`

返回守护进程健康信息：

```json
{
    "ok": true,
    "sessions": 3,
    "sessionsByStatus": { "idle": 2, "busy": 1 },
    "uptime": 3600,
    "memory": {
        "rss": 45,
        "heapUsed": 20,
        "heapTotal": 30
    },
    "nodeVersion": "v20.11.0",
    "pid": 12345
}
```

内存值以兆字节为单位。

### `handleMonitorSessions`

返回每个会话的详细信息，包括队列统计。

## JSON-RPC 辅助函数

```typescript
function rpcSuccess(result: unknown, id?: string): RpcResponse
function rpcError(code: number, message: string, id?: string): RpcResponse
```

使用的标准错误码：
- `-32600`：无效请求（缺少 method）
- `-32601`：方法不存在
- `-32602`：无效参数（缺少必要参数）
- `-32000`：内部/应用程序错误

## 优雅关闭

```typescript
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
```

`shutdown()` 函数调用 `sessionPool.destroyAll()` 终止所有正在运行的 Claude 进程并清理，然后退出进程。

## 与其他模块的关系

- 使用 **SessionPool** 进行所有会话生命周期操作
- 使用 **SkillManager** 在会话创建时同步技能
- 从 **types.ts** 导入请求/响应的类型定义
