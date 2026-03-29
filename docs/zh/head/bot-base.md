# Bot Base（bot_base.py）

**文件：** `head/bot_base.py`

Discord 和 Telegram 机器人实现的抽象基类。包含所有共享的命令处理逻辑、消息转发和流式显示功能。

## 用途

- 定义平台特定机器人必须实现的抽象接口
- 实现将 `/命令` 路由到处理方法的命令分发器
- 处理向 Claude 会话转发消息并实时流式显示响应
- 管理并发（防止同时向同一频道进行流式传输）

## 类：BotBase（ABC）

```python
class BotBase(ABC):
    ssh: SSHManager
    router: SessionRouter
    daemon: DaemonClient
    config: Config
    _streaming: set[str]  # 当前正在流式传输的频道
```

## 抽象方法

子类（DiscordBot、TelegramBot）必须实现以下方法：

| 方法 | 说明 |
|---|---|
| `send_message(channel_id, text) -> Any` | 向频道发送新消息，返回平台消息对象。 |
| `edit_message(channel_id, message_obj, text) -> None` | 编辑已有消息（用于流式更新）。 |
| `start() -> None` | 连接到平台并开始监听。 |
| `stop() -> None` | 断开与平台的连接。 |

## 命令分发器

### `handle_input(channel_id: str, text: str) -> None`

所有用户输入的主入口。如果文本以 `/` 开头，路由到命令分发器；否则转发到活跃的 Claude 会话。

### `_handle_command(channel_id: str, text: str) -> None`

解析命令并分发到对应的处理器：

| 命令 | 别名 | 处理器 |
|---|---|---|
| `/start` | | `cmd_start` |
| `/resume` | | `cmd_resume` |
| `/ls` | `/list` | `cmd_ls` |
| `/exit` | | `cmd_exit` |
| `/rm` | `/remove`、`/destroy` | `cmd_rm` |
| `/mode` | | `cmd_mode` |
| `/status` | | `cmd_status` |
| `/interrupt` | | `cmd_interrupt` |
| `/health` | | `cmd_health` |
| `/monitor` | | `cmd_monitor` |
| `/help` | | `cmd_help` |

所有命令都包含错误处理，捕获 `DaemonConnectionError`、`DaemonError` 和通用异常，将其格式化为错误消息返回给用户。

## 命令实现

### `cmd_start(channel_id, args, silent_init=False)`

创建新会话：`/start <machine_id> <path>`

1. 验证提供了两个参数
2. 通过 `ssh.ensure_tunnel()` 建立 SSH 隧道
3. 通过 `ssh.sync_skills()` 同步技能文件
4. 通过 `daemon.create_session()` 创建守护进程会话
5. 在路由器中注册会话
6. 发送确认消息，包含会话 ID 和当前模式

`silent_init` 参数用于抑制初始的"Starting session..."消息（Discord 斜杠命令有自己的初始响应，因此使用此参数）。

### `cmd_resume(channel_id, args)`

恢复会话：`/resume <session_id>`

1. 在路由器中按名称或 ID 查找会话（同时检查活跃会话和日志中的会话）
2. 确保 SSH 隧道存在
3. 如果有 SDK 会话 ID，调用 `daemon.resume_session()` 传入
4. 重新将会话注册为当前频道的活跃会话

### `cmd_ls(channel_id, args)`

列出机器或会话：`/ls machine` 或 `/ls session [machine]`

### `cmd_exit(channel_id)`

分离当前会话而不销毁它。会话稍后可以恢复。

### `cmd_rm(channel_id, args)`

销毁会话：`/rm <machine_id> <path>`

查找匹配机器/路径组合的所有会话，在守护进程和本地路由器中分别销毁。

### `cmd_mode(channel_id, args)`

更改权限模式：`/mode <auto|code|plan|ask>`

同时接受内部名称（`auto`）和显示名称（`bypass`）。同时更新守护进程和本地会话状态。

### `cmd_status(channel_id)`

显示当前会话状态，包括队列统计信息。

### `cmd_interrupt(channel_id)`

通过向正在运行的 CLI 进程发送 SIGTERM 来中断 Claude 当前的操作。

### `cmd_health(channel_id, args)`

检查守护进程健康状态。如果未指定机器，则检查当前会话的机器或所有已连接的机器。

### `cmd_monitor(channel_id, args)`

显示某台机器上会话的详细监控信息。

### `cmd_help(channel_id)`

显示列出所有可用命令的帮助消息。

## 消息转发

### `_forward_message(channel_id: str, text: str) -> None`

将用户消息转发到活跃的 Claude 会话并流式回传响应。

**并发控制：** `_streaming` 集合追踪哪些频道当前有活跃的流。如果某个频道正在流式传输，用户会收到"Claude is still processing"的消息。

**流式显示流程：**

1. 从路由器解析会话
2. 确保 SSH 隧道存在
3. 调用 `daemon.send_message()`，返回事件的异步迭代器
4. 对每个事件：
   - `partial`：将文本累积到缓冲区。定期（每 1.5 秒）发送或编辑包含缓冲内容加光标指示器（`▌`）的消息。如果缓冲区超过 1800 个字符，完成当前消息并开始新消息。
   - `text`：完整文本块。如果正在流式传输的消息存在，编辑为最终内容；否则发送为新消息（必要时拆分）。
   - `tool_use`：格式化并发送为新消息，显示工具名称和输入。
   - `result`：捕获 SDK 会话 ID 供后续 `--resume` 使用。
   - `system`（init）：首次交互时显示已连接的模型和当前模式。
   - `queued`：通知用户消息已排队及其位置。
   - `error`：显示错误消息。
   - `ping`：忽略（守护进程的保活信号）。
5. 流结束后，刷新缓冲区中剩余的内容。

## 常量

| 常量 | 值 | 说明 |
|---|---|---|
| `STREAM_UPDATE_INTERVAL` | 1.5 秒 | 流式消息的更新频率 |
| `STREAM_BUFFER_FLUSH_SIZE` | 1800 字符 | 缓冲区超过此大小时强制开始新消息 |

## 与其他模块的关系

- **bot_discord.py** 和 **bot_telegram.py** 继承此类
- 使用 **SSHManager** 进行隧道管理和技能同步
- 使用 **SessionRouter** 管理会话状态
- 使用 **DaemonClient** 进行所有守护进程通信
- 使用 **message_formatter** 进行输出格式化
