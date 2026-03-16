# Telegram Bot (bot_telegram.py)

**File:** `head/bot_telegram.py`

Telegram bot implementation using python-telegram-bot v20+ with async handlers.

## Purpose

- Implement the Telegram-specific platform layer for Remote Code
- Register command and message handlers
- Handle user-based access control
- Support Telegram's 4096-character message limit
- Provide Markdown formatting with plain-text fallback

## Class: TelegramBot

Extends `BotBase` with Telegram-specific functionality.

```python
class TelegramBot(BotBase):
    telegram_config: TelegramConfig
    _app: Application               # python-telegram-bot application
    _bot: Bot                        # Telegram Bot API client
    _last_messages: dict[str, int]   # channel_id -> last message_id
```

## Channel ID Format

Telegram channels use the format `telegram:{chat_id}` internally (e.g., `telegram:123456789`). This distinguishes them from Discord channels in the shared SessionRouter.

## Access Control

### `_is_allowed_user(user_id: int) -> bool`

Checks if a Telegram user is permitted to interact with the bot. If `allowed_users` is empty (not configured), all users are allowed. Otherwise, only user IDs in the list are accepted.

## Handlers

### Command Handler

All recognized commands are registered via `CommandHandler`:

```python
command_names = ["start", "resume", "ls", "list", "exit", "rm", "remove",
                 "destroy", "mode", "status", "health", "monitor", "help"]
```

The `_handle_telegram_command()` method:
1. Validates the message and user
2. Checks user permissions
3. Ensures the text starts with `/` (re-adds it if stripped by Telegram)
4. Forwards to `handle_input()` from BotBase

### Message Handler

Non-command text messages are handled by a `MessageHandler` with the filter `filters.TEXT & ~filters.COMMAND`. The `_handle_telegram_message()` method forwards these directly to `handle_input()`.

## Platform Methods

### `send_message(channel_id, text) -> Message`

Sends a message to a Telegram chat. Handles:

1. **Message splitting**: Uses `split_message()` with `max_len=4096` (Telegram's limit)
2. **Markdown formatting**: Attempts to send with `ParseMode.MARKDOWN` first
3. **Fallback**: If Markdown parsing fails, resends without formatting
4. Returns the last sent message object
5. Caches the message ID in `_last_messages` for potential editing

### `edit_message(channel_id, message_obj, text) -> None`

Edits an existing Telegram message. Handles:

1. Extracts `message_id` from the message object
2. Truncates to 4096 characters if needed
3. Attempts edit with Markdown formatting
4. Falls back to plain text if Markdown fails

## Lifecycle

### `start() -> None`

1. Builds the `Application` using the configured token
2. Registers all command handlers
3. Registers the message handler for non-command text
4. Initializes the application
5. Starts polling for updates

### `stop() -> None`

1. Stops the updater (polling)
2. Stops the application
3. Shuts down the application

## Differences from Discord Bot

| Feature | Discord | Telegram |
|---|---|---|
| Message limit | 2000 chars | 4096 chars |
| Command system | Slash commands (app_commands) | CommandHandler (text-based) |
| Autocomplete | Built-in choice/autocomplete | Not available |
| Typing indicator | Custom typing loop | Not implemented |
| Heartbeat updates | Custom heartbeat messages | Not implemented (uses BotBase's streaming) |
| Access control | Channel-based whitelist | User ID-based whitelist |
| Formatting | Discord markdown | Telegram Markdown (ParseMode.MARKDOWN) |
| Command registration | Synced to Discord API | Text-matching via handlers |

## Connection to Other Modules

- Extends **BotBase** for command handling and message forwarding logic
- **main.py** creates and starts the TelegramBot
- Uses **message_formatter** for `split_message()` (with 4096-char limit)
