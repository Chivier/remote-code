# Telegram Adapter (telegram_adapter.py)

**File:** `src/head/platform/telegram_adapter.py`

Telegram platform adapter implementing the `PlatformAdapter` protocol. Uses python-telegram-bot v20+ with async handlers, HTML formatting (with Markdown-to-HTML conversion), inline keyboard buttons for AskUserQuestion, and rate limit handling.

## Purpose

- Implement the Telegram-specific platform layer for Codecast
- Register command and message handlers with the python-telegram-bot Application
- Handle Telegram's 4096-character message limit with smart splitting
- Present AskUserQuestion prompts as Telegram inline keyboards
- Handle rate limiting (`RetryAfter` exceptions) gracefully
- Convert Discord-style Markdown output to Telegram HTML format

## Class: TelegramAdapter

Implements `PlatformAdapter`.

```python
class TelegramAdapter:
    platform_name: str = "telegram"
    max_message_length: int = 4096
    _config: TelegramConfig
    _app: Application | None
    _bot: Bot | None
    _on_input: InputHandler | None
    _last_messages: dict[str, int]     # channel_id -> last message_id
    _typing_tasks: dict[str, Task]     # active typing indicators
```

## Channel ID Format

Telegram channels use the prefix `telegram:` in the unified channel ID (e.g., `telegram:123456789`). Chat IDs are extracted from this prefix when making Bot API calls.

## Access Control

Access is controlled by two config lists:

- `allowed_users`: If non-empty, only these user IDs may interact with the bot
- `admin_users`: User IDs allowed to run admin commands (`/restart`, `/update`)

## Handlers

The adapter registers two handler types with the python-telegram-bot `Application`:

### Command Handler

Registered via `CommandHandler` for each recognized command name:

```python
command_names = [
    "start", "resume", "ls", "list", "exit", "rm", "remove",
    "destroy", "mode", "model", "status", "health", "monitor",
    "interrupt", "stop", "tool_display", "help", "clear", "new",
    "add_machine", "remove_machine", "rename", "restart", "update",
]
```

The `_handle_telegram_command()` method:
1. Validates that the message and user exist
2. Checks `allowed_users` permission
3. Re-prefixes the command text with `/` if Telegram stripped it
4. Calls `_on_input(channel_id, text, user_id)` which routes to BotEngine

### Message Handler

A `MessageHandler` with filter `filters.TEXT & ~filters.COMMAND` handles non-command messages. The `_handle_telegram_message()` method also checks `allowed_users` before forwarding.

### Callback Query Handler

A `CallbackQueryHandler` handles inline keyboard button presses from AskUserQuestion views. When the user presses a button:
1. The handler acknowledges the callback query
2. Edits the original message to show the selected choice
3. Calls `_on_input(channel_id, selected_option, user_id)` which feeds back to BotEngine

## Platform Methods

### `send_message(channel_id, text) -> MessageHandle`

1. Splits the text with `split_message(max_len=4096)`
2. Converts Markdown to Telegram HTML via `markdown_to_telegram_html()`
3. Sends with `parse_mode=ParseMode.HTML`
4. On `BadRequest` (parse error), retries with plain text
5. On `RetryAfter` (rate limit), waits the specified number of seconds and retries
6. Returns a `MessageHandle` wrapping the last sent message; caches the message ID in `_last_messages`

### `edit_message(handle, text) -> None`

1. Extracts the message ID from the handle
2. Converts Markdown to HTML
3. Calls `bot.edit_message_text()` with `parse_mode=ParseMode.HTML`
4. On `BadRequest`, retries with plain text
5. On `RetryAfter`, waits and retries

### `delete_message(handle) -> None`

Calls `bot.delete_message()` using the message ID from the handle.

### `download_file(attachment, dest) -> Path`

For Telegram file attachments (which provide a file ID rather than a URL), calls `bot.get_file()` then `file.download_to_drive()` to write to the destination path.

### `send_file(channel_id, path, caption="") -> MessageHandle`

Checks the file size against `TELEGRAM_FILE_SIZE_LIMIT` (20 MB). If within limit, sends as a document with `bot.send_document()`. If oversized, sends an error message instead.

### `start_typing(channel_id) -> None` / `stop_typing(channel_id) -> None`

Starts or cancels a background asyncio task that calls `bot.send_chat_action(action="typing")` every 5 seconds (Telegram's typing indicator lasts about 5 seconds).

### `send_question(channel_id, header, options, multi_select=False) -> MessageHandle`

Builds a Telegram `InlineKeyboardMarkup` with one button per option. Button `callback_data` is set to the option text (truncated to 64 bytes, Telegram's limit for callback data).

For `multi_select=True`, options are prefixed with a checkbox indicator and the header notes that multiple selections are accepted. (Multi-select is handled by collecting multiple button presses until the user sends a confirmation message.)

## HTML Formatting

Telegram uses HTML formatting rather than Markdown for safe message delivery. The `markdown_to_telegram_html()` function (in `platform/format_utils.py`) converts:

- `**bold**` → `<b>bold</b>`
- `*italic*` or `_italic_` → `<i>italic</i>`
- `` `code` `` → `<code>code</code>`
- ```` ```lang\n...\n``` ```` → `<pre><code class="language-lang">...</code></pre>`
- Escaped HTML characters in non-code sections

## Lifecycle

### `start() -> None`

1. Builds the `Application` using the configured token
2. Registers all command handlers
3. Registers the message handler and callback query handler
4. Sets bot commands in the Telegram UI via `bot.set_my_commands()`
5. Initializes and starts the Application
6. Starts polling for updates

### `stop() -> None`

1. Stops the updater (polling)
2. Stops and shuts down the Application

## Differences from Discord Adapter

| Feature | Discord | Telegram |
|---|---|---|
| Message limit | 2000 chars | 4096 chars |
| Command system | Slash commands (app_commands) | CommandHandler (text-based `/cmd`) |
| Autocomplete | Built-in choice/autocomplete popups | Not available |
| Typing indicator | Loop every 8s (10s Discord TTL) | Loop every 5s (5s Telegram TTL) |
| Heartbeat updates | Dedicated heartbeat messages | Not implemented |
| Access control | Channel-based whitelist | User ID-based whitelist |
| Text formatting | Discord Markdown | Telegram HTML (converted from Markdown) |
| Interactive questions | discord.ui.Button / SelectMenu | InlineKeyboardButton |
| File size limit | 25 MB | 20 MB |

## Connection to Other Modules

- Implements **PlatformAdapter** from `platform/protocol.py`
- **main.py** creates and starts the TelegramAdapter, then passes it to BotEngine
- Uses **message_formatter** for `split_message()`
- Uses **platform/format_utils.py** for `markdown_to_telegram_html()`
- BotEngine drives all command and streaming logic via the registered `InputHandler`
