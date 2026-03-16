# Discord Bot (bot_discord.py)

**File:** `head/bot_discord.py`

Discord bot implementation using discord.py v2 with slash commands, autocomplete, typing indicators, and heartbeat status updates.

## Purpose

- Implement the Discord-specific platform layer for Remote Code
- Register slash commands with Discord's application command system
- Provide autocomplete for machine IDs and project paths
- Show typing indicators during Claude processing
- Send periodic heartbeat messages to keep users informed during long operations
- Handle Discord's 2000-character message limit

## Class: DiscordBot

Extends `BotBase` with Discord-specific functionality.

```python
class DiscordBot(BotBase):
    bot: commands.Bot           # discord.py bot instance
    discord_config: DiscordConfig
    _channels: dict[str, Messageable]        # Channel cache
    _typing_tasks: dict[str, Task]           # Active typing indicators
    _heartbeat_msgs: dict[str, Message]      # Current heartbeat messages
    _deferred_interactions: dict[str, Interaction]  # Pending slash command responses
    _init_shown: set[str]                    # Sessions that showed init message
```

## Channel ID Format

Discord channels use the format `discord:{channel_id}` internally (e.g., `discord:123456789012345678`). This distinguishes them from Telegram channels in the shared SessionRouter.

## Slash Commands

All commands are registered as Discord application commands (slash commands) with full autocomplete support:

### `/start <machine> <path>`

- **machine** autocomplete: Lists all configured machines, excluding jump hosts. Filters by the current input text.
- **path** autocomplete: Lists `default_paths` from the selected machine's config. If no machine is selected yet, shows all paths from all machines.
- Sends the initial response directly via `interaction.response.send_message()`, then processes asynchronously.

### `/resume <session_id>`

Takes a session ID string parameter. No autocomplete (session IDs are UUIDs).

### `/ls <target> [machine]`

- **target**: Choice between "machine" and "session" (dropdown).
- **machine**: Autocomplete with configured machine IDs (optional, for filtering sessions).
- Uses deferred response (`interaction.response.defer()`) since listing may take time.

### `/mode <mode>`

- **mode**: Choice dropdown with descriptions:
  - "bypass - Full auto (skip all permissions)" -> `auto`
  - "code - Auto accept edits, confirm bash" -> `code`
  - "plan - Read-only analysis" -> `plan`
  - "ask - Confirm everything" -> `ask`

### `/exit`, `/status`, `/help`

Simple commands with no parameters. All use deferred responses.

### `/rm <machine> <path>`

Machine autocomplete, manual path input. Deferred response.

### `/health [machine]`, `/monitor [machine]`

Optional machine parameter with autocomplete. Deferred response.

## Deferred Interactions

Discord slash commands require a response within 3 seconds. For operations that take longer, the bot uses `interaction.response.defer()` to acknowledge the command, then sends the actual response via `interaction.followup.send()`.

The `_defer_and_register()` method stores the deferred interaction. The next `send_message()` call for that channel automatically uses `followup.send()` instead of `channel.send()`.

## Typing Indicator

When Claude is processing a message, the bot shows "Bot is typing..." in the channel:

```python
async def _start_typing(channel_id):
    # Sends typing() every 8 seconds (Discord indicator lasts ~10s)
```

The typing loop runs as a background task and is cancelled when the response stream completes.

## Heartbeat Status Updates

For long-running Claude operations, the bot sends periodic status messages every 25 seconds to keep users informed. This prevents the perception that the bot has become unresponsive.

```python
async def _heartbeat_loop(channel_id, start_time, event_tracker):
    # Every 25 seconds, sends/updates a status message like:
    # "[1m30s] Claude is working... Using tool: Write"
```

The heartbeat message reflects the current state of processing:
- **tool_name** set: "Using tool: **{tool_name}**"
- **partial** events with content: "Writing response..."
- **tool_use/tool_result** events: "Processing tool results..."
- Default: "Thinking..."

The heartbeat message is deleted when the operation completes.

## Message Forwarding Override

The Discord bot overrides the base class `_forward_message` with `_forward_message_with_heartbeat()`, which adds typing indicators and heartbeat updates on top of the standard streaming logic. The `on_message` event handler calls this method directly for non-command messages.

## Platform Methods

### `send_message(channel_id, text) -> Message`

Sends a message to a Discord channel. Handles:

1. **Deferred interactions**: If a pending deferred interaction exists, uses `interaction.followup.send()` first
2. **Message splitting**: Uses `split_message()` with `max_len=2000` (Discord's limit)
3. **Formatting fallback**: If a message fails to send (e.g., invalid markdown), retries with stripped formatting
4. Returns the last sent `discord.Message` object

### `edit_message(channel_id, message_obj, text) -> None`

Edits an existing Discord message. Truncates to 2000 characters if needed. Falls back to sending a new message if the edit fails.

## Event Handling

### `on_ready`

Logs the bot's username and ID, then syncs slash commands to all guilds.

### `on_message`

Handles regular (non-command) messages:
1. Ignores messages from the bot itself and other bots
2. Ignores messages starting with `/` (handled by slash commands)
3. Checks `allowed_channels` whitelist
4. Forwards to `_forward_message_with_heartbeat()`

## Constants

| Constant | Value | Description |
|---|---|---|
| `HEARTBEAT_INTERVAL` | 25 seconds | Time between heartbeat status messages |
| `STREAM_UPDATE_INTERVAL` | 1.5 seconds | Time between streaming text updates |
| `STREAM_BUFFER_FLUSH_SIZE` | 1800 chars | Force new message at this buffer size |

## Connection to Other Modules

- Extends **BotBase** for command handling and message forwarding logic
- **main.py** creates and starts the DiscordBot
- Uses **message_formatter** for `split_message()`, `format_error()`, and `display_mode()`
