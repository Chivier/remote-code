# 消息格式化器（message_formatter.py）

**文件：** `head/message_formatter.py`

处理消息拆分以适应平台字符限制，以及将各种输出类型格式化以便在 Discord 和 Telegram 中显示。

## 用途

- 将长消息拆分为符合平台限制的块（Discord：2000，Telegram：4096）
- 智能拆分，避免打断代码块，优先在自然边界处分割
- 格式化工具调用事件、机器列表、会话列表、状态报告、健康检查和监控数据
- 将内部模式名称映射为用户可见的显示名称

## 模式显示名称

内部模式名称映射到用户可见名称：

| 内部名称 | 显示名称 |
|---|---|
| `auto` | `bypass` |
| `code` | `code` |
| `plan` | `plan` |
| `ask` | `ask` |

`auto` 模式显示为 `bypass`，以明确表示权限被完全绕过。

```python
def display_mode(mode: str) -> str
```

## 消息拆分

### `split_message(text: str, max_len: int = 2000) -> list[str]`

将长消息拆分为符合平台字符限制的块。

**拆分优先级（按顺序）：**

1. **代码块感知**：如果拆分会发生在代码块内部（奇数个 ` ``` ` 标记），则将拆分点移到代码块开始之前。
2. **段落边界**（`\n\n`）：优先拆分点，要求至少位于文本 30% 处。
3. **行边界**（`\n`）：次优选项，同样要求 30% 最小位置。
4. **句子边界**（`. `、`! `、`? `、`; `）：要求 50% 最小位置。
5. **词边界**（空格）：要求 50% 最小位置。
6. **强制拆分**：如果找不到自然边界，则在 `max_len` 处强制拆分。

结果中会过滤掉空块。

## 格式化函数

### `format_tool_use(event: dict) -> str`

为聊天显示格式化 `tool_use` 事件。

```
**[Tool: Write]** Creating file at /path/to/file
```

或带输入数据：

```
**[Tool: Bash]**
\`\`\`
{"command": "ls -la"}
\`\`\`
```

输入数据截断至 500 字符。

### `format_machine_list(machines: list[dict]) -> str`

为 `/ls machine` 格式化机器列表：

```
**Machines:**
🟢 **gpu-1** (gpu1.example.com) ⚡
  Paths: `/home/user/project-a`, `/home/user/project-b`
🔴 **gpu-2** (gpu2.lab.internal) 💤
```

图标含义：
- 🟢 在线 / 🔴 离线
- ⚡ 守护进程运行中 / 💤 守护进程已停止

### `format_session_list(sessions: list) -> str`

为 `/ls session` 格式化会话列表：

```
**Sessions:**
● `a1b2c3d4...` **gpu-1**:`/home/user/project` [bypass] (active)
○ `e5f6g7h8...` **gpu-1**:`/home/user/other` [code] (detached)
```

状态图标：● 活跃，○ 已分离，✕ 已销毁，◉ 繁忙

### `format_session_info(session) -> str`

格式化单个会话的显示内容。同时处理 `Session` 对象（来自 SessionRouter）和字典对象（来自守护进程 API）。

### `format_error(error: str) -> str`

格式化错误消息：

```
**Error:** message text
```

### `format_status(session, queue_stats=None) -> str`

格式化 `/status` 输出：

```
**Session Status**
Machine: **gpu-1**
Path: `/home/user/project`
Mode: **bypass**
Status: **active**
Session ID: `a1b2c3d4e5f6...`
SDK Session: `x9y8z7w6v5u4...`
Queue: 0 pending messages
Buffered: 0 responses
```

### `format_health(machine_id, health) -> str`

格式化 `/health` 输出：

```
**Daemon Health - gpu-1**
Status: OK
Uptime: 2h15m30s
Sessions: 3 (idle: 2, busy: 1)
Memory: 45MB RSS, 20/30MB heap
Node: v20.11.0 (PID: 12345)
```

运行时间格式化为时/分/秒。

### `format_monitor(machine_id, monitor) -> str`

格式化 `/monitor` 输出，包含每个会话的详细信息：

```
**Monitor - gpu-1** (uptime: 2h15m30s, 2 session(s))

● `a1b2c3d4...` **idle** [bypass | claude-sonnet-4-20250514]
  Path: `/home/user/project`
  Client: connected | Queue: 0 pending, 0 buffered

◉ `e5f6g7h8...` **busy** [code | claude-sonnet-4-20250514]
  Path: `/home/user/other`
  Client: **disconnected** | Queue: 1 pending, 5 buffered
```

## 与其他模块的关系

- **bot_base.py** 导入 `split_message`、`format_tool_use`、`format_machine_list`、`format_session_list`、`format_error`、`format_status`、`format_health`、`format_monitor` 和 `display_mode`
- **bot_discord.py** 导入 `split_message`、`format_error` 和 `display_mode`
- **bot_telegram.py** 导入 `split_message`
