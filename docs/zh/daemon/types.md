# 类型定义（types.ts）

**文件：** `daemon/src/types.ts`

守护进程 RPC 协议、会话管理、流事件和 Claude CLI JSON 行格式的核心类型定义。

## 用途

- 定义 JSON-RPC 请求/响应的线协议格式
- 定义会话状态和权限模式枚举
- 将权限模式映射到 Claude CLI 标志
- 定义 SSE 通信的流事件类型
- 定义 Claude CLI stdout 消息格式
- 为每个 RPC 方法定义有类型的参数和结果接口

## RPC 协议类型

### RpcRequest

```typescript
interface RpcRequest {
    method: string;                    // 如 "session.create"
    params?: Record<string, unknown>;  // 方法参数
    id?: string;                       // 可选请求 ID
}
```

### RpcResponse

```typescript
interface RpcResponse {
    result?: unknown;                           // 成功结果
    error?: { code: number; message: string; data?: unknown };  // 错误
    id?: string;                                // 回显的请求 ID
}
```

## 会话类型

### SessionStatus

```typescript
type SessionStatus = "idle" | "busy" | "error" | "destroyed";
```

- **idle**：无 Claude 进程运行，准备好接收消息
- **busy**：Claude 进程正在处理消息
- **error**：会话遇到错误
- **destroyed**：会话已销毁和清理

### PermissionMode

```typescript
type PermissionMode = "auto" | "code" | "plan" | "ask";
```

### `modeToCliFlag(mode: PermissionMode) -> string[]`

将内部模式名称映射为 Claude CLI 标志：

| 模式 | CLI 标志 | 效果 |
|---|---|---|
| `auto` | `["--dangerously-skip-permissions"]` | 完全自动化，无确认提示 |
| `code` | `[]` | 无特定 CLI 标志（SDK 层面） |
| `plan` | `[]` | 无特定 CLI 标志（SDK 层面） |
| `ask` | `[]` | 默认行为（所有工具需要确认） |

目前只有 `auto` 模式有对应的 CLI 标志。`code` 和 `plan` 模式是 SDK 层面的概念，在 Claude CLI 的 `--print` 模式下没有直接对应的标志。

### ManagedSession

基础会话接口：

```typescript
interface ManagedSession {
    sessionId: string;
    path: string;
    mode: PermissionMode;
    status: SessionStatus;
    sdkSessionId: string | null;
    createdAt: Date;
    lastActivityAt: Date;
}
```

### SessionInfo

可序列化的会话信息（日期以 ISO 字符串表示）：

```typescript
interface SessionInfo {
    sessionId: string;
    path: string;
    status: SessionStatus;
    mode: PermissionMode;
    sdkSessionId: string | null;
    model: string | null;
    createdAt: string;      // ISO 8601
    lastActivityAt: string;  // ISO 8601
}
```

## 流事件类型

### StreamEventType

```typescript
type StreamEventType =
    | "text"        // 完整文本块
    | "tool_use"    // 工具调用
    | "tool_result" // 工具执行结果
    | "result"      // 带 session_id 的最终结果
    | "queued"      // 消息已排队（Claude 繁忙）
    | "error"       // 错误消息
    | "system"      // 系统事件（init 等）
    | "partial"     // 流式文本增量
    | "ping"        // 保活
    | "interrupted"; // 操作被中断
```

### StreamEvent

```typescript
interface StreamEvent {
    type: StreamEventType;
    content?: string;       // 文本内容（用于 text/partial）
    tool?: string;          // 工具名称（用于 tool_use）
    input?: unknown;        // 工具输入（用于 tool_use）
    output?: unknown;       // 工具输出（用于 tool_result）
    session_id?: string;    // SDK 会话 ID（用于 result/system）
    position?: number;      // 队列位置（用于 queued）
    message?: string;       // 错误/状态消息
    subtype?: string;       // 事件子类型（system 的 "init"）
    model?: string;         // 模型名称（用于 system init）
    raw?: unknown;          // 原始 Claude CLI 数据（直接传递）
}
```

## 消息队列类型

### QueuedUserMessage

```typescript
interface QueuedUserMessage {
    message: string;    // 用户消息文本
    timestamp: number;  // Date.now()
}
```

### QueuedResponse

```typescript
interface QueuedResponse {
    event: StreamEvent;  // 缓冲的响应事件
    timestamp: number;   // Date.now()
}
```

## RPC 方法参数和结果

### 会话方法

```typescript
interface CreateSessionParams {
    path: string;
    mode?: PermissionMode;
}

interface CreateSessionResult {
    sessionId: string;
}

interface SendMessageParams {
    sessionId: string;
    message: string;
}

interface ResumeSessionParams {
    sessionId: string;
    sdkSessionId?: string;
}

interface ResumeSessionResult {
    ok: boolean;
    fallback?: boolean;
    newSdkSessionId?: string;
}

interface DestroySessionParams {
    sessionId: string;
}

interface SetModeParams {
    sessionId: string;
    mode: PermissionMode;
}

interface InterruptSessionParams {
    sessionId: string;
}
```

### 健康检查和监控

```typescript
interface HealthCheckResult {
    ok: boolean;
    sessions: number;
    sessionsByStatus: Record<string, number>;
    uptime: number;        // 秒
    memory: {
        rss: number;       // MB
        heapUsed: number;  // MB
        heapTotal: number; // MB
    };
    nodeVersion: string;
    pid: number;
}

interface MonitorSessionDetail {
    sessionId: string;
    path: string;
    status: SessionStatus;
    mode: PermissionMode;
    model: string | null;
    sdkSessionId: string | null;
    createdAt: string;
    lastActivityAt: string;
    queue: {
        userPending: number;
        responsePending: number;
        clientConnected: boolean;
    };
}

interface MonitorSessionsResult {
    sessions: MonitorSessionDetail[];
    totalSessions: number;
    uptime: number;  // 秒
}
```

## Claude CLI JSON 行协议

### ClaudeStdoutMessage

Claude CLI 的 `--output-format stream-json` 输出的原始消息格式：

```typescript
interface ClaudeStdoutMessage {
    type: string;           // "system", "assistant", "stream_event", "result", "tool_progress"
    subtype?: string;       // system 消息的 "init"
    session_id?: string;    // SDK 会话 ID

    // assistant 消息内容
    message?: {
        role: string;
        content: Array<{
            type: string;    // "text" 或 "tool_use"
            text?: string;
            name?: string;   // 工具名称
            input?: unknown; // 工具输入
            id?: string;
        }>;
    };

    // 流式事件
    event?: {
        type: string;        // "content_block_delta", "content_block_start"
        index?: number;
        delta?: {
            type?: string;
            text?: string;
            partial_json?: string;
        };
        content_block?: {
            type: string;    // "text" 或 "tool_use"
            text?: string;
            name?: string;
            id?: string;
        };
    };

    // 结果元数据
    duration_ms?: number;
    usage?: {
        input_tokens: number;
        output_tokens: number;
    };

    // 工具进度
    tool_name?: string;
    status?: string;
}
```

注：`ClaudeStdinMessage` 已从类型定义中移除，因为守护进程使用 `--print` 模式（每消息生成进程）而非 stdin JSON 行。

## 与其他模块的关系

- **所有守护进程模块**都从此文件导入类型
- **server.ts** 使用 RPC 请求/响应类型和参数接口
- **session-pool.ts** 使用会话类型、流事件和模式到标志的映射
- **message-queue.ts** 使用队列类型和 StreamEvent
