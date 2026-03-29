# Telegram Bot（bot_telegram.py）

**文件：** `head/bot_telegram.py`

使用 python-telegram-bot v20+ 异步处理器实现的 Telegram 机器人。

## 用途

- 实现 Codecast 的 Telegram 平台层
- 注册命令和消息处理器
- 处理基于用户的访问控制
- 支持 Telegram 的 4096 字符消息限制
- 提供 Markdown 格式化及纯文本回退

## 类：TelegramBot

继承 `BotBase`，增加 Telegram 特定功能。

```python
class TelegramBot(BotBase):
    telegram_config: TelegramConfig
    _app: Application               # python-telegram-bot 应用
    _bot: Bot                        # Telegram Bot API 客户端
    _last_messages: dict[str, int]   # channel_id -> 最后消息 ID
```

## 频道 ID 格式

Telegram 频道在内部使用 `telegram:{chat_id}` 格式（如 `telegram:123456789`），以便在共享的 SessionRouter 中与 Discord 频道区分。

## 访问控制

### `_is_allowed_user(user_id: int) -> bool`

检查 Telegram 用户是否有权限与机器人交互。如果 `allowed_users` 为空（未配置），则允许所有用户；否则只接受列表中的用户 ID。

## 处理器

### 命令处理器

所有已识别的命令都通过 `CommandHandler` 注册：

```python
command_names = ["start", "resume", "ls", "list", "exit", "rm", "remove",
                 "destroy", "mode", "status", "health", "monitor", "help"]
```

`_handle_telegram_command()` 方法：
1. 验证消息和用户
2. 检查用户权限
3. 确保文本以 `/` 开头（如果 Telegram 去除了前缀则重新添加）
4. 转发到来自 BotBase 的 `handle_input()`

### 消息处理器

非命令文本消息通过带有过滤器 `filters.TEXT & ~filters.COMMAND` 的 `MessageHandler` 处理。`_handle_telegram_message()` 方法将这些消息直接转发到 `handle_input()`。

## 平台方法

### `send_message(channel_id, text) -> Message`

向 Telegram 聊天发送消息。处理以下情况：

1. **消息拆分**：使用 `split_message()`，`max_len=4096`（Telegram 的限制）
2. **Markdown 格式化**：先尝试使用 `ParseMode.MARKDOWN` 发送
3. **回退**：如果 Markdown 解析失败，改为不带格式发送
4. 返回最后发送的消息对象
5. 在 `_last_messages` 中缓存消息 ID，以备后续编辑使用

### `edit_message(channel_id, message_obj, text) -> None`

编辑已有的 Telegram 消息。处理以下情况：

1. 从消息对象中提取 `message_id`
2. 如有必要截断至 4096 字符
3. 尝试使用 Markdown 格式编辑
4. 如果 Markdown 失败则回退为纯文本

## 生命周期

### `start() -> None`

1. 使用配置的 token 构建 `Application`
2. 注册所有命令处理器
3. 注册非命令文本的消息处理器
4. 初始化应用
5. 开始轮询更新

### `stop() -> None`

1. 停止更新器（轮询）
2. 停止应用
3. 关闭应用

## 与 Discord Bot 的差异

| 功能 | Discord | Telegram |
|---|---|---|
| 消息长度限制 | 2000 字符 | 4096 字符 |
| 命令系统 | 斜杠命令（app_commands） | CommandHandler（文本方式） |
| 自动补全 | 内置选择/自动补全 | 不可用 |
| 打字指示器 | 自定义打字循环 | 未实现 |
| 心跳更新 | 自定义心跳消息 | 未实现（使用 BotBase 的流式处理） |
| 访问控制 | 基于频道的白名单 | 基于用户 ID 的白名单 |
| 格式化 | Discord Markdown | Telegram Markdown（ParseMode.MARKDOWN） |
| 命令注册 | 同步到 Discord API | 通过处理器进行文本匹配 |

## 与其他模块的关系

- 继承 **BotBase** 的命令处理和消息转发逻辑
- **main.py** 创建并启动 TelegramBot
- 使用 **message_formatter** 的 `split_message()`（4096 字符限制）
