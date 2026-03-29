# 消息队列（message-queue.ts）

**文件：** `daemon/src/message-queue.ts`

每会话消息队列，承担三项职责：在 Claude 繁忙时缓冲用户消息、在 SSH 连接断开时缓冲响应，以及追踪客户端连接状态。

## 用途

- **用户消息缓冲**：当 Claude 正在处理消息时，额外的用户消息会被排队，并在当前消息完成后按顺序处理。
- **响应缓冲**：当 SSH 连接（以及 SSE 流）在响应过程中断开时，事件会被缓冲，客户端重连时可以重放。
- **客户端连接追踪**：追踪 Head Node 客户端当前是否已连接，以便系统决定是否需要缓冲响应。

## 类：MessageQueue

```typescript
class MessageQueue {
    private userPending: QueuedUserMessage[];   // 排队的用户消息
    private responsePending: QueuedResponse[];  // 缓冲的响应事件
    private _clientConnected: boolean;          // 客户端连接状态
}
```

每个会话有自己的 MessageQueue 实例，在会话创建时建立。

## 用户消息缓冲

### `enqueueUser(message: string) -> number`

将用户消息加入队列。返回队列位置（从 1 开始）。当 Claude 已经在处理消息时，由 `SessionPool.send()` 调用。

### `dequeueUser() -> QueuedUserMessage | null`

从队列中移除并返回下一条用户消息。队列为空时返回 `null`。消息处理完成后，由 `SessionPool.processMessage()` 调用，检查是否有下一条消息需要处理。

### `hasUserPending() -> boolean`

如果有排队等待处理的用户消息，返回 `true`。

### `userQueueLength`（getter）

返回待处理用户消息的数量。

## 响应缓冲

### `bufferResponse(event: StreamEvent, force: boolean = false) -> void`

缓冲响应事件。默认情况下，只有当 `_clientConnected` 为 `false` 时才会缓冲事件。`force` 参数绕过此检查——当 server.ts 检测到 SSE 客户端已断连但 session pool 尚未收到通知时使用。

### `replayResponses() -> StreamEvent[]`

返回所有缓冲的响应事件并清空缓冲区。在客户端重连时调用，重放在断连期间生成的所有事件。

### `hasResponsesPending() -> boolean`

如果有缓冲的响应事件，返回 `true`。

## 客户端连接状态

### `clientConnected`（getter）

返回当前的客户端连接状态。

### `onClientDisconnect() -> void`

将客户端标记为已断连。此调用之后，响应事件将被缓冲而非假定已送达。当 SSE 响应流的 `close` 事件触发时，由服务器调用。

### `onClientReconnect() -> StreamEvent[]`

将客户端标记为已重连，并返回所有缓冲的响应事件（将重连通知与响应重放合并）。由 `session.reconnect` RPC 处理器调用。

## 清理

### `clear() -> void`

清空用户消息队列和响应缓冲区。当会话被销毁或中断时调用。

### `stats() -> { userPending, responsePending, clientConnected }`

返回队列统计信息，用于调试和监控。供 `/status` 和 `/monitor` 命令使用。

## 数据类型

### QueuedUserMessage

```typescript
interface QueuedUserMessage {
    message: string;    // 用户消息文本
    timestamp: number;  // 入队时的 Date.now()
}
```

### QueuedResponse

```typescript
interface QueuedResponse {
    event: StreamEvent;  // 响应事件
    timestamp: number;   // 缓冲时的 Date.now()
}
```

## 流程示例

```
用户发送 msg1 -> Claude 开始处理
用户发送 msg2 -> enqueueUser("msg2")，position=1
用户发送 msg3 -> enqueueUser("msg3")，position=2
Claude 完成 msg1 -> dequeueUser() 返回 msg2
              -> Claude 开始处理 msg2
SSH 在流式传输中断开 -> onClientDisconnect()
              -> 后续事件通过 bufferResponse() 缓冲
Claude 完成 msg2 -> dequeueUser() 返回 msg3
              -> Claude 开始处理 msg3
SSH 重连 -> session.reconnect RPC
              -> onClientReconnect() 返回缓冲的事件
```

## 与其他模块的关系

- **session-pool.ts** 为每个会话创建一个 MessageQueue，并调用其方法进行消息排队和客户端状态管理
- 从 **types.ts** 导入 **StreamEvent**、**QueuedUserMessage** 和 **QueuedResponse**
