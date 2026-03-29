# Message Formatter (message_formatter.py)

**File:** `src/head/message_formatter.py`

Handles message splitting for platform character limits and formatting of various output types for display in Discord, Telegram, and Lark.

## Purpose

- Split long messages into chunks that respect platform limits (Discord: 2000, Telegram: 4096)
- Smart splitting that avoids breaking code blocks and prefers natural boundaries
- Format tool use events in multiple styles (full, line, activity, compressed batch)
- Handle AskUserQuestion parsing and text rendering
- Format machine lists, session lists, status reports, health checks, and monitoring data
- Map internal mode names to user-facing display names

## Mode Display Names

Internal mode names are mapped to user-facing names:

| Internal | Display |
|---|---|
| `auto` | `bypass` |
| `code` | `code` |
| `plan` | `plan` |
| `ask` | `ask` |

The `auto` mode is displayed as `bypass` to make it clear that all permission prompts are skipped.

```python
def display_mode(mode: str) -> str
```

## Message Splitting

### `split_message(text: str, max_len: int = 2000) -> list[str]`

Splits a long message into chunks that fit within the platform's character limit.

**Splitting priority (highest to lowest):**

1. **Code block awareness**: If a split would land inside a code block (odd number of ` ``` ` markers before the split point), the split is moved to before the opening ` ``` `. This prevents sending a message with an unclosed code block.
2. **Paragraph boundary** (`\n\n`): Preferred split point; must be at least 30% into the text.
3. **Line boundary** (`\n`): Next best option; also requires 30% minimum position.
4. **Sentence boundary** (`. `, `! `, `? `, `; `): Requires 50% minimum position.
5. **Word boundary** (space): Requires 50% minimum position.
6. **Forced split**: At exactly `max_len` if no natural boundary is found.

Empty chunks are filtered out of the result.

## Tool Formatting Functions

### `format_tool_use(event: dict) -> str`

Formats a single `tool_use` event for full display. Used in `append` display mode and for single-tool responses.

With a status message:
```
**[Tool: Bash]** Running command...
```

With structured input (truncated to 500 chars):
```
**[Tool: Write]**
```
{"file_path": "/path/to/file", "content": "..."}
```
```

With no message or input:
```
**[Tool: Glob]**
```

### `format_tool_line(event: dict) -> str`

Formats a single `tool_use` event as a compact one-liner for activity messages. Used when building the accumulated tool call list in `timer` and `append` display modes.

```
  `WebFetch` — https://api.github.com/repos/...
  `Write` — {"file_path": "/home/user/..."}
  `Bash`
```

Input/message text is truncated to 120 characters.

### `compress_tool_messages(events: list[dict]) -> str`

Compresses multiple `tool_use` events into a single summary message. Used in `batch` display mode when there is more than one tool call in a response.

For a single event, delegates to `format_tool_use()`. For multiple events:

```
**[Tools: 3 calls]**
  `Read` — /home/user/project/main.py
  `Bash` — {"command": "pytest tests/ -v"}
  `Write` — {"file_path": "/home/user/project/..."}
```

Each line is truncated to 120 characters.

### `format_activity_message(tool_lines: list[str], thinking: str = "", cursor: bool = True) -> str`

Builds a live activity message showing accumulated tool calls and an optional thinking snippet. Used in `timer` and `append` display modes to provide a continuously updated status message.

```
**[Tools: 2 calls]**
  `Read` — /home/user/project/main.py
  `Bash` — {"command": "pytest"}
> *...running test suite...*
▌
```

Parameters:
- `tool_lines`: One line per tool call from `format_tool_line()`
- `thinking`: Current partial-text snippet (last 200 chars shown)
- `cursor`: Whether to append the `▌` cursor indicator

## AskUserQuestion Functions

### `format_ask_user_question(questions: list[dict]) -> list[tuple[str, list[str], bool]]`

Parses the structured input from a Claude `AskUserQuestion` tool invocation into a list of `(header, options, multi_select)` tuples.

Input format (from Claude's tool input JSON):
```json
[
    {
        "header": "Which framework should I use?",
        "options": [
            {"description": "FastAPI (async, modern)"},
            {"description": "Flask (simple, synchronous)"}
        ],
        "multiSelect": false
    }
]
```

Output:
```python
[("Which framework should I use?", ["FastAPI (async, modern)", "Flask (simple, synchronous)"], False)]
```

The adapter then calls `adapter.send_question()` for each tuple in the list.

### `format_question_text(header: str, options: list[str], multi_select: bool = False) -> str`

Formats a question with numbered options as plain text. Used as a fallback for platforms that do not support inline buttons, and for logging.

```
**Which framework should I use?**
  1. FastAPI (async, modern)
  2. Flask (simple, synchronous)
```

For multi-select:
```
**Which components need updating?**
_(Select one or more — reply with numbers separated by commas)_
  1. Authentication module
  2. Database layer
  3. API endpoints
```

## List and Status Formatting

### `format_machine_list(machines: list[dict]) -> str`

Formats the machine list for `/ls machine`:

```
**Machines:**
🟢 **gpu-1** (gpu1.example.com) ⚡
  Paths: `/home/user/project-a`, `/home/user/project-b`
🔴 **gpu-2** (gpu2.lab.internal) 💤
  Paths: `/home/user/experiments`
```

Icons: online (🟢) / offline (🔴), daemon running (⚡) / stopped (💤). Localhost machines are tagged with `[localhost]`.

### `format_session_list(sessions: list) -> str`

Formats the session list for `/ls session`. Delegates to `format_session_info()` for each session.

### `format_session_info(session) -> str`

Formats a single session. Handles both `Session` objects from the SessionRouter and dict objects from the daemon API.

For router sessions:
```
● **smooth-dove** `a1b2c3d4...` **gpu-1**:`/home/user/project` [bypass] (active)
```

For daemon API dicts:
```
◉ `e5f6g7h8...` **/home/user/other** [code | claude-sonnet-4-20250514] (busy)
```

Status icons: `●` active/idle, `◉` busy, `○` detached, `✕` destroyed/error, `?` unknown.

### `format_error(error: str) -> str`

```
**Error:** message text
```

### `format_status(session, queue_stats=None) -> str`

Formats the `/status` output, including session name, machine, path, CLI type, mode, tool display mode, status, session IDs, and queue statistics.

### `format_health(machine_id, health) -> str`

Formats the `/health` output with uptime, session counts by status, memory usage, and process info.

### `format_monitor(machine_id, monitor) -> str`

Formats the `/monitor` output with detailed per-session information including queue depth and client connection state.

## Connection to Other Modules

- **BotEngine** (`engine.py`) imports all formatting functions
- **discord_adapter.py** imports `split_message`, `format_error`, `display_mode`
- **telegram_adapter.py** imports `split_message`
