# Discord Adapter (discord_adapter.py)

**File:** `src/head/platform/discord_adapter.py`

Discord platform adapter implementing the `PlatformAdapter` protocol. Uses discord.py v2 with slash commands, autocomplete, typing indicators, heartbeat status updates, and interactive button views for AskUserQuestion.

## Purpose

- Implement the Discord-specific platform layer for Codecast
- Register slash commands with Discord's application command system and provide autocomplete
- Show typing indicators and heartbeat messages during long Claude operations
- Present AskUserQuestion prompts as Discord UI buttons or select menus
- Handle Discord's 2000-character message limit with smart splitting

## Class: DiscordAdapter

Implements `PlatformAdapter`.

```python
class DiscordAdapter:
    platform_name: str = "discord"
    max_message_length: int = 2000
    _config: DiscordConfig
    _bot: commands.Bot
    _on_input: InputHandler | None
    _channels: dict[str, Messageable]
    _typing_tasks: dict[str, Task]
    _heartbeat_msgs: dict[str, Message]
    _deferred_interactions: dict[str, Interaction]
    _engine_ref: EngineType | None    # back-reference for heartbeat
```

## Channel ID Format

Discord channels use the prefix `discord:` in the unified channel ID (e.g., `discord:123456789012345678`). The BotEngine and SessionRouter always use these prefixed IDs.

## Slash Commands

All commands are registered as Discord application commands (slash commands) with full autocomplete support. Commands are synced to all guilds on `on_ready`.

### `/start <machine> <path> [cli_type]`

- **machine** autocomplete: Lists all configured machines, excluding jump hosts. Filters by the current input text.
- **path** autocomplete: Lists `default_paths` from the selected machine's config. Falls back to paths from all machines if no machine is selected yet.
- **cli_type**: Optional choice from `claude`, `codex`, `gemini`, `opencode`.
- Sends the initial "Starting..." response via `interaction.response.send_message()`, then processes asynchronously.

### `/resume <session_id>`

Takes a session name or UUID string. No autocomplete.

### `/ls <target> [machine]`

- **target**: Choice dropdown between "machine" and "session".
- **machine**: Autocomplete with configured machine IDs (optional, for session filtering).
- Uses deferred response since listing may take time.

### `/mode <mode>`

Choice dropdown with descriptions:
- "bypass - Full auto (skip all permissions)" → `auto`
- "code - Auto accept edits, confirm bash" → `code`
- "plan - Read-only analysis" → `plan`
- "ask - Confirm everything" → `ask`

### `/exit`, `/status`, `/help`, `/interrupt`

Simple commands with no parameters. All use deferred responses.

### `/rm <machine> <path>`

Machine autocomplete, manual path input. Deferred response.

### `/health [machine]`, `/monitor [machine]`

Optional machine parameter with autocomplete. Deferred response.

### `/tool-display <mode>`

Choice dropdown for `timer`, `append`, `batch`.

### `/model <model_name>`

Text input for the model identifier.

## Deferred Interactions

Discord requires a response within 3 seconds. For operations that may take longer, the adapter calls `interaction.response.defer()` to acknowledge the command immediately. The next `send_message()` call for that channel uses `interaction.followup.send()` instead of `channel.send()`.

The `_defer_and_register()` method stores the pending interaction. It is consumed on first use and then cleared.

## Typing Indicator

While Claude processes a message, the adapter shows "Bot is typing..." in the channel:

```python
async def _start_typing(channel_id: str) -> None:
    # Sends typing() context manager every 8s (Discord indicator lasts ~10s)
```

The typing loop runs as a background asyncio task. It is cancelled by `stop_typing()` when the stream completes.

## Heartbeat Status Updates

For long-running operations, the adapter sends periodic status messages every `HEARTBEAT_INTERVAL` (30 seconds) to keep users informed. These prevent the perception of a frozen bot.

```python
async def _heartbeat_loop(channel_id: str, start_time: float, event_tracker: dict) -> None:
    # Every 30 seconds, sends/updates a message like:
    # "[1m30s] Claude is working... Using tool: Write"
```

The heartbeat message reflects the current processing state:
- Tool name set: "Using tool: **{tool_name}**"
- Partial events with content: "Writing response..."
- Tool use/result events: "Processing tool results..."
- Default: "Thinking..."

The heartbeat message is deleted when the operation completes.

The `_forward_message_with_heartbeat()` method wraps the BotEngine's streaming loop with typing and heartbeat management. The Discord adapter sets up this wrapper by storing a back-reference to the engine.

## AskUserQuestion: Interactive View

When BotEngine calls `adapter.send_question()`, the Discord adapter presents options as interactive UI elements using `_AskUserQuestionView`:

```python
class _AskUserQuestionView(discord.ui.View):
    timeout = 300  # 5 minutes
```

- For 5 or fewer options: renders as `discord.ui.Button` instances (one per option, secondary style)
- For more than 5 options: renders as a `discord.SelectMenu` with up to 25 options

When the user clicks a button or selects from the menu, the view calls the `on_input` callback with the selected option text. This feeds back into `engine.handle_input()` as a normal message.

## Platform Methods

### `send_message(channel_id, text) -> MessageHandle`

1. If a pending deferred interaction exists for this channel, uses `interaction.followup.send()` first
2. Splits the text with `split_message(max_len=2000)`
3. Sends each chunk; if a chunk fails (e.g., invalid Discord markdown), retries with formatting stripped
4. Returns a `MessageHandle` wrapping the last sent `discord.Message` object

### `edit_message(handle, text) -> None`

Edits an existing Discord message using the handle's `raw` field (the original `discord.Message`). Truncates to 2000 characters. Falls back to sending a new message if the edit fails (e.g., message deleted).

### `delete_message(handle) -> None`

Deletes the message referenced by the handle.

### `download_file(attachment, dest) -> Path`

Downloads the file from the Discord CDN URL to the local path using aiohttp.

### `send_file(channel_id, path, caption="") -> MessageHandle`

Uploads a file to the Discord channel as a `discord.File` attachment.

### `start_typing(channel_id) -> None` / `stop_typing(channel_id) -> None`

Starts or cancels the background typing loop for the channel.

### `send_question(channel_id, header, options, multi_select=False) -> MessageHandle`

Sends the question header text and an `_AskUserQuestionView` with the given options. The view expires after 300 seconds.

## Event Handling

### `on_ready`

Logs the bot username and ID, then calls `bot.tree.sync()` to sync slash commands to all guilds.

### `on_message`

Handles regular (non-slash-command) messages:
1. Ignores messages from the bot itself and other bots
2. Ignores messages starting with `/` (handled by slash commands)
3. Checks the `allowed_channels` whitelist (if configured)
4. Calls `_on_input(channel_id, text, user_id, attachments)` which routes to BotEngine

## Constants

| Constant | Value | Description |
|---|---|---|
| `HEARTBEAT_INTERVAL` | 30 seconds | Time between heartbeat status messages |
| `STREAM_BUFFER_FLUSH_SIZE` | 1800 chars | Force new message at this buffer size |

## Connection to Other Modules

- Implements **PlatformAdapter** from `platform/protocol.py`
- **main.py** creates and starts the DiscordAdapter, then passes it to BotEngine
- Uses **message_formatter** for `split_message()`, `format_error()`, and `display_mode()`
- BotEngine drives all command and streaming logic via the registered `InputHandler`
