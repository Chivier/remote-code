# SSE 流事件

当 Head Node 调用 `session.send` RPC 时，守护进程以 Server-Sent Events（SSE）流的形式响应。本文档描述流中可能出现的所有事件类型。

## SSE 格式

事件以 `data:` 行发送，携带 JSON 负载，以双换行符分隔：

```
data: {"type":"partial","content":"Hello"}

data: {"type":"text","content":"Hello, world!"}

data: [DONE]
```

流以 `data: [DONE]` 结束（不是 JSON 负载）。JSON 负载是来自 `types.rs` 的 `StreamEvent` 值序列化结果，以 `type` 字段作为标签。

## 终端事件

三种事件类型表示一次消息交换的结束。守护进程在发送以下任一事件后停止继续发送事件：

| 类型 | 触发条件 |
|---|---|
| `result` | Claude 成功完成处理 |
| `error` | 发生错误（进程崩溃、超时等） |
| `interrupted` | 通过 `session.interrupt` 中断了操作 |

---

## 事件类型

### `system`

系统事件，提供会话相关的元数据。最常见的是 `init` 子类型，在每次消息交换开始时（Claude 启动时）发送。

```json
{
    "type": "system",
    "subtype": "init",
    "session_id": "sdk-session-uuid",
    "model": "claude-sonnet-4-20250514"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `subtype` | string | 事件子类型（目前仅有 `"init"`） |
| `session_id` | string | Claude SDK 会话 ID |
| `model` | string | CLI 报告的模型名称 |
| `raw` | object | 原始 CLI JSON 消息（可选） |

Head Node 使用 `init` 事件在首次与会话交互时显示 "Connected to **model** | Mode: **mode**" 消息。

---

### `partial`

流式文本增量。这些事件在 CLI 生成文本时逐步到达，提供可实时渲染的输出。

```json
{
    "type": "partial",
    "content": "Let me "
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `content` | string | 文本增量（几个字符到几个词） |

Head Node 将 `partial` 增量累积到缓冲区中，并定期用当前缓冲区内容加 `▌` 光标指示符更新聊天消息。当完整的 `text` 事件到达时，它会替换累积的增量内容。

`partial` 事件在工具使用流式传输期间也可携带 `partial_json` 内容（JSON 被增量组装）。Head Node 以与文本增量相同的方式渲染这些内容。

---

### `text`

来自 CLI 的完整文本块，表示响应中一个已完成的内容块。

```json
{
    "type": "text",
    "content": "Here is the complete analysis of your project...",
    "raw": { ... }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `content` | string | 完整文本内容 |
| `raw` | object | 原始 CLI 消息（可选） |

如果之前一直在累积 `partial` 事件，`text` 事件的内容会替换增量缓冲区。如果没有发送过增量（例如简短的回复），文本将作为新消息发送。

---

### `tool_use`

表示 CLI 正在调用工具（文件写入、bash 命令、网络请求等）。

```json
{
    "type": "tool_use",
    "tool": "Write",
    "input": {
        "file_path": "/home/user/project/README.md",
        "content": "# My Project\n..."
    },
    "raw": { ... }
}
```

带状态消息（来自工具进度事件）：

```json
{
    "type": "tool_use",
    "tool": "Bash",
    "message": "Running command...",
    "raw": { ... }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `tool` | string | 工具名称（如 `Write`、`Bash`、`Read`、`Glob`、`Grep`、`WebFetch`、`AskUserQuestion`） |
| `input` | object | 工具输入参数（可选；有数据时存在） |
| `message` | string | 工具执行进度状态消息（可选） |
| `raw` | object | 原始 CLI 消息（可选） |

**特殊情况：`AskUserQuestion`**

当 `tool` 为 `"AskUserQuestion"` 时，`input` 字段包含结构化的问题列表：

```json
{
    "type": "tool_use",
    "tool": "AskUserQuestion",
    "input": [
        {
            "header": "Which framework should I use?",
            "options": [
                {"description": "FastAPI (async, modern)"},
                {"description": "Flask (simple, synchronous)"}
            ],
            "multiSelect": false
        }
    ]
}
```

Head Node 将此传递给 `format_ask_user_question()`，然后调用 `adapter.send_question()` 以显示平台原生的交互按钮。

---

### `result`

表示 CLI 已完成处理该消息，包含对话连续性所需的 SDK 会话 ID。

```json
{
    "type": "result",
    "session_id": "sdk-session-uuid-here",
    "raw": { ... }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | 供下次消息 `--resume` 使用的 SDK 会话 ID |
| `raw` | object | 原始结果，包含 `duration_ms` 和 `usage`（可选） |

Head Node 捕获 `session_id` 并通过 `router.update_sdk_session_id()` 保存，供后续 `--resume` 调用使用。

这是一个**终端事件**。

---

### `queued`

当会话繁忙且新消息已被排队时立即发送。

```json
{
    "type": "queued",
    "position": 2
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `position` | number | 在队列中的位置（从 1 开始） |

Head Node 显示："Message queued (position: 2). Claude is busy with a previous request."

当排队的消息最终被处理时，其事件将通过一个新的 SSE 流流出（由守护进程在上一条消息完成后自动发起下一次 `session.send` 调用）。如果客户端在此时已断连，事件将被缓冲以供 `session.reconnect` 使用。

---

### `error`

处理过程中发生错误。

```json
{
    "type": "error",
    "message": "Claude process exited abnormally (code=1)"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `message` | string | 人类可读的错误描述 |

常见错误来源：
- CLI 进程以非零退出码退出
- CLI 进程启动失败（找不到二进制文件、权限被拒绝）
- 流空闲超时（长时间无事件）
- 守护进程检测到 SSH 连接断开

这是一个**终端事件**。

---

### `ping`

保活事件，每 30 秒发送一次，防止空闲 SSH 隧道超时。

```json
{
    "type": "ping"
}
```

Head Node 忽略这些事件。它们存在的唯一目的是通过会关闭空闲连接的 SSH 隧道和代理保持 HTTP 连接活跃。

---

### `interrupted`

当操作被中断时发送，可由 `session.interrupt` 触发，也可由向 CLI 进程发送外部 SIGTERM 触发。

```json
{
    "type": "interrupted"
}
```

这是一个**终端事件**。

---

## 事件流示例

### 简单文本回复

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514","session_id":"sdk-123"}
data: {"type":"partial","content":"The "}
data: {"type":"partial","content":"answer "}
data: {"type":"partial","content":"is 42."}
data: {"type":"text","content":"The answer is 42."}
data: {"type":"result","session_id":"sdk-123"}
data: [DONE]
```

### 包含工具调用的回复

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514"}
data: {"type":"partial","content":"Let me check..."}
data: {"type":"tool_use","tool":"Bash","input":{"command":"ls -la"}}
data: {"type":"tool_use","tool":"Bash","message":"Running command..."}
data: {"type":"partial","content":"Here are the files:\n"}
data: {"type":"partial","content":"- src/\n- Cargo.toml"}
data: {"type":"text","content":"Here are the files:\n- src/\n- Cargo.toml"}
data: {"type":"result","session_id":"sdk-456"}
data: [DONE]
```

### AskUserQuestion

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514"}
data: {"type":"partial","content":"I need to clarify a few things."}
data: {"type":"tool_use","tool":"AskUserQuestion","input":[{"header":"Which approach?","options":[{"description":"Option A"},{"description":"Option B"}],"multiSelect":false}]}
data: {"type":"result","session_id":"sdk-789"}
data: [DONE]
```

### Claude 忙时排队

```
data: {"type":"queued","position":1}
data: [DONE]
```

### 处理中发生错误

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514"}
data: {"type":"partial","content":"Let me "}
data: {"type":"error","message":"Claude process exited abnormally (code=1)"}
data: [DONE]
```

### 长时间操作期间的保活

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514"}
data: {"type":"partial","content":"Analyzing..."}
data: {"type":"ping"}
data: {"type":"partial","content":" the codebase structure"}
data: {"type":"ping"}
data: {"type":"tool_use","tool":"Glob","input":{"pattern":"**/*.rs"}}
data: {"type":"text","content":"I found 15 Rust files..."}
data: {"type":"result","session_id":"sdk-789"}
data: [DONE]
```

### 操作被中断

```
data: {"type":"system","subtype":"init","model":"claude-sonnet-4-20250514"}
data: {"type":"partial","content":"Let me analyze this large codebase..."}
data: {"type":"tool_use","tool":"Glob","input":{"pattern":"**/*"}}
data: {"type":"interrupted"}
data: [DONE]
```
