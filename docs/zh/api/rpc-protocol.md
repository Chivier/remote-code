# JSON-RPC 协议

Remote Code 使用 JSON-RPC over HTTP 协议进行 Head Node 和 Daemon 之间的通信。所有请求发送到 `POST /rpc` 端点。

## 协议格式

### 请求

```json
{
    "method": "method.name",
    "params": { ... },
    "id": "optional-request-id"
}
```

### 成功响应

```json
{
    "result": { ... },
    "id": "optional-request-id"
}
```

### 错误响应

```json
{
    "error": {
        "code": -32000,
        "message": "Error description"
    },
    "id": "optional-request-id"
}
```

### 标准错误码

| 错误码 | 含义 |
|--------|------|
| `-32600` | 无效请求（缺少 method 字段） |
| `-32601` | 方法不存在 |
| `-32602` | 无效参数（缺少必需参数） |
| `-32000` | 内部错误 |

---

## 方法列表

### session.create

创建一个新的 Claude 会话。

**请求**：
```json
{
    "method": "session.create",
    "params": {
        "path": "/home/user/project",
        "mode": "auto"
    }
}
```

| 参数 | 类型 | 必需 | 说明 |
|------|------|:----:|------|
| `path` | string | 是 | 远程项目路径，必须存在 |
| `mode` | string | 否 | 权限模式：`auto`/`code`/`plan`/`ask`，默认 `auto` |

**成功响应**：
```json
{
    "result": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
}
```

**行为**：
1. 验证路径是否存在
2. 同步技能文件到项目目录
3. 注册会话状态（状态为 idle，不启动进程）
4. 返回会话 ID

---

### session.send

发送消息到 Claude 会话。**此方法返回 SSE 流而非 JSON 响应。**

**请求**：
```json
{
    "method": "session.send",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "message": "请帮我分析这段代码"
    }
}
```

| 参数 | 类型 | 必需 | 说明 |
|------|------|:----:|------|
| `sessionId` | string | 是 | 会话 ID |
| `message` | string | 是 | 用户消息 |

**响应**：SSE 流（`Content-Type: text/event-stream`），详见 [SSE 流事件](./sse-events.md)。

**行为**：
1. 如果 Claude 空闲，启动 `claude --print` 进程
2. 如果 Claude 正忙，将消息排队并返回 `queued` 事件
3. 流式推送 Claude 的处理结果
4. 流结束时发送 `data: [DONE]`

---

### session.resume

恢复之前的会话。

**请求**：
```json
{
    "method": "session.resume",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "sdkSessionId": "sdk-session-uuid"
    }
}
```

| 参数 | 类型 | 必需 | 说明 |
|------|------|:----:|------|
| `sessionId` | string | 是 | Daemon 会话 ID |
| `sdkSessionId` | string | 否 | Claude SDK 会话 ID，用于 `--resume` |

**成功响应**：
```json
{
    "result": {
        "ok": true,
        "fallback": false
    }
}
```

| 字段 | 说明 |
|------|------|
| `ok` | 是否成功 |
| `fallback` | 是否使用了降级策略（如注入历史而非直接恢复） |

**行为**：
- 如果会话存在，更新 `sdkSessionId` 并标记客户端重连
- 下次 `session.send` 时会使用 `--resume <sdkSessionId>` 恢复上下文

---

### session.destroy

销毁会话，终止关联的 Claude 进程。

**请求**：
```json
{
    "method": "session.destroy",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
}
```

| 参数 | 类型 | 必需 | 说明 |
|------|------|:----:|------|
| `sessionId` | string | 是 | 会话 ID |

**成功响应**：
```json
{
    "result": {
        "ok": true
    }
}
```

**行为**：
1. 向 Claude 进程发送 SIGTERM
2. 5 秒后未终止则发送 SIGKILL
3. 清空消息队列
4. 从会话池中移除

---

### session.list

列出 Daemon 上的所有会话。

**请求**：
```json
{
    "method": "session.list"
}
```

不需要参数。

**成功响应**：
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
                "lastActivityAt": "2026-03-14T10:30:00.000Z"
            }
        ]
    }
}
```

---

### session.set_mode

设置会话的权限模式。

**请求**：
```json
{
    "method": "session.set_mode",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "mode": "code"
    }
}
```

| 参数 | 类型 | 必需 | 说明 |
|------|------|:----:|------|
| `sessionId` | string | 是 | 会话 ID |
| `mode` | string | 是 | 新模式：`auto`/`code`/`plan`/`ask` |

**成功响应**：
```json
{
    "result": {
        "ok": true
    }
}
```

**行为**：更新会话模式，下次启动 Claude 进程时生效。

---

### session.interrupt

中断 Claude 当前的操作。

**请求**：
```json
{
    "method": "session.interrupt",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
}
```

| 参数 | 类型 | 必需 | 说明 |
|------|------|:----:|------|
| `sessionId` | string | 是 | 会话 ID |

**成功响应**：
```json
{
    "result": {
        "ok": true,
        "interrupted": true
    }
}
```

| 字段 | 说明 |
|------|------|
| `ok` | 请求成功 |
| `interrupted` | `true` 表示有操作被中断，`false` 表示 Claude 当前空闲 |

**行为**：
- 向 Claude CLI 进程发送 SIGTERM
- 清空消息队列

---

### session.queue_stats

获取会话的消息队列统计。

**请求**：
```json
{
    "method": "session.queue_stats",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
}
```

| 参数 | 类型 | 必需 | 说明 |
|------|------|:----:|------|
| `sessionId` | string | 是 | 会话 ID |

**成功响应**：
```json
{
    "result": {
        "userPending": 2,
        "responsePending": 0,
        "clientConnected": true
    }
}
```

| 字段 | 说明 |
|------|------|
| `userPending` | 排队等待处理的用户消息数 |
| `responsePending` | 因客户端断连而缓冲的响应事件数 |
| `clientConnected` | 客户端是否处于连接状态 |

---

### session.reconnect

重新连接到会话，获取断连期间缓冲的事件。

**请求**：
```json
{
    "method": "session.reconnect",
    "params": {
        "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    }
}
```

| 参数 | 类型 | 必需 | 说明 |
|------|------|:----:|------|
| `sessionId` | string | 是 | 会话 ID |

**成功响应**：
```json
{
    "result": {
        "bufferedEvents": [
            { "type": "partial", "content": "Hello " },
            { "type": "partial", "content": "world!" },
            { "type": "result", "session_id": "sdk-uuid" }
        ]
    }
}
```

**行为**：
1. 标记客户端为已连接
2. 返回所有缓冲的事件
3. 清空响应缓冲区

---

### health.check

检查 Daemon 健康状态。

**请求**：
```json
{
    "method": "health.check"
}
```

不需要参数。

**成功响应**：
```json
{
    "result": {
        "ok": true,
        "sessions": 3,
        "sessionsByStatus": {
            "idle": 2,
            "busy": 1
        },
        "uptime": 7200,
        "memory": {
            "rss": 85,
            "heapUsed": 42,
            "heapTotal": 65
        },
        "nodeVersion": "v20.11.0",
        "pid": 12345
    }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `ok` | boolean | 服务是否正常 |
| `sessions` | number | 活跃会话总数 |
| `sessionsByStatus` | object | 按状态分类的会话数 |
| `uptime` | number | 运行时间（秒） |
| `memory.rss` | number | 常驻内存（MB） |
| `memory.heapUsed` | number | 已用堆内存（MB） |
| `memory.heapTotal` | number | 总堆内存（MB） |
| `nodeVersion` | string | Node.js 版本 |
| `pid` | number | Daemon 进程 ID |

---

### monitor.sessions

获取所有会话的详细监控信息。

**请求**：
```json
{
    "method": "monitor.sessions"
}
```

不需要参数。

**成功响应**：
```json
{
    "result": {
        "sessions": [
            {
                "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "path": "/home/user/project",
                "status": "idle",
                "mode": "auto",
                "model": "claude-sonnet-4-20250514",
                "sdkSessionId": "sdk-uuid",
                "createdAt": "2026-03-14T10:00:00.000Z",
                "lastActivityAt": "2026-03-14T10:30:00.000Z",
                "queue": {
                    "userPending": 0,
                    "responsePending": 0,
                    "clientConnected": true
                }
            }
        ],
        "totalSessions": 1,
        "uptime": 7200
    }
}
```

| 字段 | 说明 |
|------|------|
| `sessions` | 所有会话的详细信息数组 |
| `totalSessions` | 会话总数 |
| `uptime` | Daemon 运行时间（秒） |

每个会话包含基本信息加上消息队列状态。
