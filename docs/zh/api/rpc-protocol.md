# JSON-RPC 协议

守护进程在 `POST /rpc` 暴露单一 HTTP 端点，接受 JSON-RPC 请求。Head Node 与守护进程之间的所有通信均使用此协议。

## 端点

```
POST http://127.0.0.1:{port}/rpc
Content-Type: application/json
```

守护进程只绑定到 `127.0.0.1`。访问通过 Head Node 管理的 SSH 端口转发进行。

## 请求格式

```json
{
    "method": "session.create",
    "params": { "path": "/home/user/project", "mode": "auto" },
    "id": "optional-request-id"
}
```

## 响应格式

**成功：**

```json
{
    "result": { "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890" },
    "id": "optional-request-id"
}
```

**错误：**

```json
{
    "error": { "code": -32602, "message": "Missing required param: path" },
    "id": "optional-request-id"
}
```

## 错误码

| 码 | 含义 |
|---|---|
| `-32600` | 无效请求（缺少 method） |
| `-32601` | 方法不存在 |
| `-32602` | 无效参数（缺少必要参数） |
| `-32000` | 内部/应用程序错误（会话不存在等） |

---

## 方法

### `session.create`

创建新的 Claude 会话。这是轻量级操作——在发送消息之前不会生成 Claude 进程。

**请求：**

```json
{
    "method": "session.create",
    "params": {
        "path": "/home/user/project",
        "mode": "auto"
    }
}
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `path` | string | 是 | 远程机器上项目目录的绝对路径。必须存在。 |
| `mode` | string | 否 | 权限模式：`auto`、`code`、`plan`、`ask`。默认为 `auto`。 |

**响应：**

```json
{
    "result": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
}
```

**副作用：**
- 会话创建前将技能同步到项目目录
- 验证路径在文件系统上存在

---

### `session.send`

向 Claude 会话发送消息。与其他方法不同，此方法返回 **SSE 流**而非 JSON 响应。

**请求：**

```json
{
    "method": "session.send",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "message": "What files are in this project?"
    }
}
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionId` | string | 是 | 来自 `session.create` 的会话 UUID。 |
| `message` | string | 是 | 要发送给 Claude 的用户消息。 |

**响应：** SSE 流（Content-Type：`text/event-stream`）

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514"}

data: {"type":"partial","content":"Let me "}

data: {"type":"partial","content":"look at "}

data: {"type":"partial","content":"the files..."}

data: {"type":"tool_use","tool":"Bash","input":{"command":"ls -la"}}

data: {"type":"text","content":"Here are the files in this project:\n\n- src/\n- package.json\n- README.md"}

data: {"type":"result","session_id":"sdk-session-uuid-here"}

data: [DONE]
```

如果 Claude 正在处理另一条消息：

```
data: {"type":"queued","position":1}

data: [DONE]
```

完整的事件类型文档请参阅 [SSE 流事件](./sse-events.md)。

**副作用：**
- 在消息处理期间生成 `claude --print` 进程
- 从结果中捕获 SDK 会话 ID，供后续 `--resume` 使用
- 完成后自动处理下一条排队消息（如果有）

---

### `session.resume`

恢复之前分离的会话。更新 SDK 会话 ID，使下一次 `send()` 使用 `--resume`。

**请求：**

```json
{
    "method": "session.resume",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "sdkSessionId": "sdk-session-uuid-here"
    }
}
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionId` | string | 是 | 守护进程会话 UUID。 |
| `sdkSessionId` | string | 否 | 用于 `--resume` 的 Claude SDK 会话 ID。 |

**响应：**

```json
{
    "result": {
        "ok": true,
        "fallback": false
    }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | boolean | 会话是否找到并恢复 |
| `fallback` | boolean | 是否创建了注入历史的新会话（而非真正恢复） |

---

### `session.destroy`

销毁会话并终止任何正在运行的 Claude 进程。

**请求：**

```json
{
    "method": "session.destroy",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
}
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionId` | string | 是 | 要销毁的会话 UUID。 |

**响应：**

```json
{
    "result": {
        "ok": true
    }
}
```

**副作用：**
- 向任何正在运行的 Claude 进程发送 SIGTERM（5 秒后 SIGKILL）
- 清空消息队列
- 从池中移除会话

---

### `session.list`

列出守护进程上的所有会话。

**请求：**

```json
{
    "method": "session.list",
    "params": {}
}
```

无需参数。

**响应：**

```json
{
    "result": {
        "sessions": [
            {
                "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "path": "/home/user/project",
                "status": "idle",
                "mode": "auto",
                "sdkSessionId": "sdk-uuid",
                "model": "claude-sonnet-4-20250514",
                "createdAt": "2026-03-14T10:00:00.000Z",
                "lastActivityAt": "2026-03-14T10:05:00.000Z"
            }
        ]
    }
}
```

---

### `session.set_mode`

更改会话的权限模式。在下一条消息时生效。

**请求：**

```json
{
    "method": "session.set_mode",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "mode": "code"
    }
}
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionId` | string | 是 | 会话 UUID。 |
| `mode` | string | 是 | 新模式：`auto`、`code`、`plan`、`ask`。 |

**响应：**

```json
{
    "result": {
        "ok": true
    }
}
```

---

### `session.interrupt`

中断 Claude 当前的操作。向正在运行的 Claude CLI 进程发送 SIGTERM。

**请求：**

```json
{
    "method": "session.interrupt",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
}
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionId` | string | 是 | 会话 UUID。 |

**响应：**

```json
{
    "result": {
        "ok": true,
        "interrupted": true
    }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | boolean | 如果会话存在，始终为 `true` |
| `interrupted` | boolean | 如果有活跃操作被中断则为 `true`，如果 Claude 处于空闲则为 `false` |

**副作用：**
- 向 Claude CLI 进程发送 SIGTERM
- 清空消息队列

---

### `session.queue_stats`

获取会话的消息队列统计信息。

**请求：**

```json
{
    "method": "session.queue_stats",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
}
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionId` | string | 是 | 会话 UUID。 |

**响应：**

```json
{
    "result": {
        "userPending": 2,
        "responsePending": 0,
        "clientConnected": true
    }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `userPending` | number | 等待处理的用户消息数量 |
| `responsePending` | number | 缓冲的响应事件数量（用于 SSH 重连） |
| `clientConnected` | boolean | Head Node 客户端当前是否已连接 |

---

### `session.reconnect`

重连到会话并检索任何缓冲的响应事件。

**请求：**

```json
{
    "method": "session.reconnect",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
}
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionId` | string | 是 | 会话 UUID。 |

**响应：**

```json
{
    "result": {
        "bufferedEvents": [
            {"type": "partial", "content": "Here is "},
            {"type": "text", "content": "Here is the answer to your question."},
            {"type": "result", "session_id": "sdk-uuid"}
        ]
    }
}
```

**副作用：**
- 将客户端标记为已重连
- 重放后清空响应缓冲区

---

### `health.check`

检查守护进程健康状态和系统信息。

**请求：**

```json
{
    "method": "health.check",
    "params": {}
}
```

无需参数。

**响应：**

```json
{
    "result": {
        "ok": true,
        "sessions": 3,
        "sessionsByStatus": {
            "idle": 2,
            "busy": 1
        },
        "uptime": 3600,
        "memory": {
            "rss": 45,
            "heapUsed": 20,
            "heapTotal": 30
        },
        "nodeVersion": "v20.11.0",
        "pid": 12345
    }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | boolean | 守护进程响应时始终为 `true` |
| `sessions` | number | 会话总数 |
| `sessionsByStatus` | object | 按状态（idle、busy、error、destroyed）统计的会话数量 |
| `uptime` | number | 守护进程运行时间（秒） |
| `memory.rss` | number | 常驻内存大小（MB） |
| `memory.heapUsed` | number | V8 已用堆内存（MB） |
| `memory.heapTotal` | number | V8 总堆内存（MB） |
| `nodeVersion` | string | Node.js 版本字符串 |
| `pid` | number | 守护进程 PID |

---

### `monitor.sessions`

获取所有会话的详细监控信息，包括队列统计。

**请求：**

```json
{
    "method": "monitor.sessions",
    "params": {}
}
```

无需参数。

**响应：**

```json
{
    "result": {
        "sessions": [
            {
                "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "path": "/home/user/project",
                "status": "busy",
                "mode": "auto",
                "model": "claude-sonnet-4-20250514",
                "sdkSessionId": "sdk-uuid",
                "createdAt": "2026-03-14T10:00:00.000Z",
                "lastActivityAt": "2026-03-14T10:05:00.000Z",
                "queue": {
                    "userPending": 1,
                    "responsePending": 0,
                    "clientConnected": true
                }
            }
        ],
        "totalSessions": 1,
        "uptime": 3600
    }
}
```
