# 会话池（session-pool.ts）

**文件：** `daemon/src/session-pool.ts`

使用每消息生成进程架构管理 Claude CLI 会话。每条用户消息都会生成一个新的 `claude --print` 进程，通过 `--resume` 保持对话连续性。

## 用途

- 维护会话元数据注册表（路径、模式、状态、SDK 会话 ID）
- 为单条消息生成 Claude CLI 进程
- 将 Claude CLI 的 stdout JSON 行转换为 StreamEvent 对象
- 在 Claude 繁忙时处理消息排队
- 管理进程生命周期（生成、监控、中断、终止）
- 追踪客户端连接状态以进行响应缓冲

## 架构：每消息生成进程

SessionPool 不维护长期运行的 Claude CLI 进程，而是为每条消息生成新进程：

```
claude --print "user message" \
       --output-format stream-json \
       --verbose \
       [--resume <sdkSessionId>] \
       [--dangerously-skip-permissions]
```

**为何采用每消息生成方式？**
- Claude CLI（v2.1.76+）在不使用 `--print` 的情况下不支持 `--input-format stream-json`
- 每个进程只在一次消息交换期间存活
- `--resume` 标志通过引用前一次交互的 SDK 会话 ID 来维持对话上下文

## 内部类型

### InternalSession

用运行时状态扩展 `ManagedSession`：

```typescript
interface InternalSession extends ManagedSession {
    process: ChildProcess | null;  // 当前正在运行的 Claude 进程
    queue: MessageQueue;           // 每会话消息队列
    processing: boolean;           // 是否正在处理消息
    model: string | null;          // 来自 Claude CLI init 的模型名称
}
```

## 关键方法

### `create(path: string, mode: PermissionMode) -> string`

创建新会话。这是**轻量级**操作——只注册会话元数据：

1. 验证项目路径在文件系统上存在
2. 为会话 ID 生成 UUID
3. 创建状态为 `idle`、无进程、新建 `MessageQueue` 的 `InternalSession`
4. 返回会话 ID

此时不会生成 Claude CLI 进程。

### `send(sessionId: string, message: string) -> AsyncGenerator<StreamEvent>`

向会话发送消息。返回产出流事件的异步生成器。

**如果 Claude 繁忙**（正在处理另一条消息）：
- 通过 `MessageQueue.enqueueUser()` 将消息入队
- 产出一个带队列位置的单个 `queued` 事件
- 立即返回

**如果 Claude 空闲：**
- 委托给 `processMessage()`，后者生成 Claude 进程

### `processMessage(session, message) -> AsyncGenerator<StreamEvent>`

生成 Claude CLI 进程并产出事件的内部方法。

**进程生成：**

```typescript
const child = spawn("claude", args, {
    cwd: session.path,
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, TERM: "dumb" },
});
```

`TERM: "dumb"` 环境变量防止 Claude CLI 输出 ANSI 转义码。

**根据会话状态构建 CLI 参数：**
- `--print <message>` -- 用户消息
- `--output-format stream-json` -- JSON 行输出
- `--verbose` -- 包含系统消息
- `--resume <sdkSessionId>` -- 继续之前的对话（如果可用）
- `--dangerously-skip-permissions` -- 仅在 `auto` 模式下

**stdin** 立即关闭（`child.stdin.end()`），因为 `--print` 模式从参数读取提示词。

**事件处理：**

使用 Node.js 的 `readline.createInterface()` 逐行读取 stdout。每行解析为 JSON（`ClaudeStdoutMessage`），并通过 `convertToStreamEvent()` 转换为 `StreamEvent`。

事件被推入内部队列。异步生成器在事件到达时产出，使用基于 Promise 的等待机制处理背压。

**终止事件：** 生成器在 `result`、`error` 或 `interrupted` 事件时停止。

**清理：**
- `session.process` 设为 null
- `session.processing` 设为 false
- `session.status` 设为 `idle`
- 如果进程仍存活，发送 SIGTERM（3 秒后 SIGKILL 兜底）
- 如果有排队消息，通过 `processQueuedMessage()` 自动处理下一条

### `convertToStreamEvent(msg: ClaudeStdoutMessage) -> StreamEvent`

将 Claude CLI 的 stdout JSON 消息映射为内部 StreamEvent 格式：

| Claude CLI 类型 | StreamEvent 类型 | 内容 |
|---|---|---|
| `system`（init） | `system` | 模型名称、会话 ID |
| `assistant`（文本块） | `text` | 拼接的文本内容 |
| `assistant`（工具块） | `tool_use` | 工具名称、输入数据 |
| `stream_event`（content_block_delta, text） | `partial` | 文本增量 |
| `stream_event`（content_block_delta, partial_json） | `partial` | 部分 JSON |
| `stream_event`（content_block_start, tool_use） | `tool_use` | 工具名称 |
| `tool_progress` | `tool_use` | 工具名称、状态消息 |
| `result` | `result` | 会话 ID |

来自 `result` 事件的 `session_id` 字段被捕获并存储为 `session.sdkSessionId`，供后续 `--resume` 调用使用。

### `resume(sessionId, sdkSessionId?) -> { ok, fallback }`

恢复会话。在每消息生成模式下，这只是更新 `sdkSessionId`，使下一次 `send()` 使用 `--resume`。同时调用 `queue.onClientReconnect()`。

### `destroy(sessionId) -> boolean`

销毁会话：
1. 终止所有正在运行的 Claude 进程（SIGTERM，5 秒后 SIGKILL）
2. 将状态设为 `destroyed`
3. 清空消息队列
4. 从池中移除会话

### `setMode(sessionId, mode) -> boolean`

更新会话的权限模式。在下一次 `send()` 时生效（下次进程生成时）。

### `interrupt(sessionId) -> boolean`

中断当前的 Claude 操作：
1. 向正在运行的 Claude CLI 进程发送 SIGTERM
2. 清空消息队列
3. 如果有活跃操作被中断，返回 `true`

### `listSessions() -> SessionInfo[]`

返回所有会话的信息：sessionId、path、status、mode、sdkSessionId、model、createdAt、lastActivityAt。

### `clientDisconnect(sessionId)` / `bufferEvent(sessionId, event)` / `clientReconnect(sessionId)`

MessageQueue 客户端连接状态管理的代理方法。当 SSE 连接断开时由 server.ts 使用。

### `getQueueStats(sessionId) -> { userPending, responsePending, clientConnected }`

返回会话的队列统计信息。

### `destroyAll() -> void`

销毁所有会话。在守护进程关闭时调用。

## 与其他模块的关系

- **server.ts** 创建单一的 `SessionPool` 实例，并为所有会话相关的 RPC 处理器调用其方法
- 使用 **MessageQueue** 进行每会话消息缓冲
- 从 **types.ts** 导入类型（ManagedSession、SessionInfo、StreamEvent、PermissionMode 等）
