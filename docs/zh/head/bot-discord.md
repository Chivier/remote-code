# Discord Bot（bot_discord.py）

**文件：** `head/bot_discord.py`

使用 discord.py v2 实现的 Discord 机器人，支持斜杠命令、自动补全、打字指示器和心跳状态更新。

## 用途

- 实现 Codecast 的 Discord 平台层
- 向 Discord 应用命令系统注册斜杠命令
- 为机器 ID 和项目路径提供自动补全
- 在 Claude 处理期间显示打字指示器
- 在长时间操作期间定期发送心跳消息，让用户随时了解进度
- 处理 Discord 的 2000 字符消息限制

## 类：DiscordBot

继承 `BotBase`，增加 Discord 特定功能。

```python
class DiscordBot(BotBase):
    bot: commands.Bot           # discord.py bot 实例
    discord_config: DiscordConfig
    _channels: dict[str, Messageable]        # 频道缓存
    _typing_tasks: dict[str, Task]           # 活跃的打字指示器
    _heartbeat_msgs: dict[str, Message]      # 当前心跳消息
    _deferred_interactions: dict[str, Interaction]  # 待处理的斜杠命令响应
    _init_shown: set[str]                    # 已显示初始化消息的会话
```

## 频道 ID 格式

Discord 频道在内部使用 `discord:{channel_id}` 格式（如 `discord:123456789012345678`），以便在共享的 SessionRouter 中与 Telegram 频道区分。

## 斜杠命令

所有命令都注册为 Discord 应用命令（斜杠命令），并带有完整的自动补全支持：

### `/start <machine> <path>`

- **machine** 自动补全：列出所有已配置的机器，排除跳板机，根据当前输入文本过滤。
- **path** 自动补全：列出所选机器配置中的 `default_paths`。如果尚未选择机器，则显示所有机器的路径。
- 通过 `interaction.response.send_message()` 直接发送初始响应，然后异步处理。

### `/resume <session_id>`

接受会话 ID 字符串参数。无自动补全（会话 ID 为 UUID）。

### `/ls <target> [machine]`

- **target**：在"machine"和"session"之间选择（下拉菜单）。
- **machine**：已配置机器 ID 的自动补全（可选，用于过滤会话）。
- 使用延迟响应（`interaction.response.defer()`），因为列表操作可能需要时间。

### `/mode <mode>`

- **mode**：带描述的下拉选择：
  - "bypass - Full auto (skip all permissions)" -> `auto`
  - "code - Auto accept edits, confirm bash" -> `code`
  - "plan - Read-only analysis" -> `plan`
  - "ask - Confirm everything" -> `ask`

### `/exit`、`/status`、`/help`

无参数的简单命令。全部使用延迟响应。

### `/rm <machine> <path>`

machine 参数有自动补全，path 手动输入。使用延迟响应。

### `/health [machine]`、`/monitor [machine]`

可选的 machine 参数带自动补全。使用延迟响应。

## 延迟交互

Discord 斜杠命令要求在 3 秒内响应。对于耗时较长的操作，机器人使用 `interaction.response.defer()` 确认命令，然后通过 `interaction.followup.send()` 发送实际响应。

`_defer_and_register()` 方法存储延迟的交互。该频道的下一次 `send_message()` 调用会自动使用 `followup.send()` 而非 `channel.send()`。

## 打字指示器

当 Claude 正在处理消息时，机器人在频道中显示"Bot is typing..."：

```python
async def _start_typing(channel_id):
    # 每 8 秒发送 typing()（Discord 打字指示器持续约 10 秒）
```

打字循环作为后台任务运行，在响应流完成时取消。

## 心跳状态更新

对于长时间运行的 Claude 操作，机器人每 25 秒发送一次定期状态消息，让用户及时了解进度，避免产生机器人已无响应的错觉。

```python
async def _heartbeat_loop(channel_id, start_time, event_tracker):
    # 每 25 秒发送/更新一条状态消息，例如：
    # "[1m30s] Claude is working... Using tool: Write"
```

心跳消息反映处理的当前状态：
- **tool_name** 已设置："Using tool: **{tool_name}**"
- 有内容的 **partial** 事件："Writing response..."
- **tool_use/tool_result** 事件："Processing tool results..."
- 默认："Thinking..."

操作完成时心跳消息会被删除。

## 消息转发覆盖

Discord 机器人用 `_forward_message_with_heartbeat()` 覆盖基类的 `_forward_message`，在标准流式逻辑之上增加打字指示器和心跳更新。`on_message` 事件处理器对非命令消息直接调用此方法。

## 平台方法

### `send_message(channel_id, text) -> Message`

向 Discord 频道发送消息。处理以下情况：

1. **延迟交互**：如果存在待处理的延迟交互，优先使用 `interaction.followup.send()`
2. **消息拆分**：使用 `split_message()`，`max_len=2000`（Discord 的限制）
3. **格式化回退**：如果消息发送失败（如无效的 Markdown），去除格式化后重试
4. 返回最后发送的 `discord.Message` 对象

### `edit_message(channel_id, message_obj, text) -> None`

编辑已有的 Discord 消息。如有必要截断至 2000 字符。编辑失败时回退为发送新消息。

## 事件处理

### `on_ready`

记录机器人的用户名和 ID，然后将斜杠命令同步到所有服务器。

### `on_message`

处理普通（非命令）消息：
1. 忽略机器人自身和其他机器人的消息
2. 忽略以 `/` 开头的消息（由斜杠命令处理）
3. 检查 `allowed_channels` 白名单
4. 转发到 `_forward_message_with_heartbeat()`

## 常量

| 常量 | 值 | 说明 |
|---|---|---|
| `HEARTBEAT_INTERVAL` | 25 秒 | 心跳状态消息的发送间隔 |
| `STREAM_UPDATE_INTERVAL` | 1.5 秒 | 流式文本更新的时间间隔 |
| `STREAM_BUFFER_FLUSH_SIZE` | 1800 字符 | 缓冲区达到此大小时强制发送新消息 |

## 与其他模块的关系

- 继承 **BotBase** 的命令处理和消息转发逻辑
- **main.py** 创建并启动 DiscordBot
- 使用 **message_formatter** 的 `split_message()`、`format_error()` 和 `display_mode()`
