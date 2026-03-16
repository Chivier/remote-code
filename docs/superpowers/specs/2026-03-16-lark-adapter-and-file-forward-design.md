# Lark (Feishu) Adapter & Remote File Forwarding Design

**Date:** 2026-03-16
**Status:** Draft
**Scope:** Add Feishu/Lark platform adapter + remote-to-bot file forwarding with configurable rules

## Problem

### 1. No Feishu/Lark Support

Remote-claude currently supports Discord and Telegram. Many teams use Feishu (飞书/Lark) as their primary workspace. The multi-platform adapter architecture (Protocol + Engine) was designed for exactly this kind of extension, but no Lark adapter exists yet.

### 2. No File Forwarding from Remote to Bot

When Claude CLI generates or references files on the remote machine (e.g., plots, screenshots, build artifacts), users must manually SSH in to retrieve them. The system currently supports user→remote file upload (Discord attachments → SCP → remote), but not the reverse direction: remote→user file delivery.

## Feature 1: Lark Adapter

### Approach

Implement `LarkAdapter` as a new `PlatformAdapter` conforming to the existing protocol in `head/platform/protocol.py`. Uses the `lark-oapi` SDK with **WebSocket long-connection mode** (no public IP needed).

**Why WebSocket over HTTP callback:**
- Remote-claude runs on local machines without public IPs
- WebSocket mode is natively supported by `lark-oapi` SDK with auto-reconnect
- Aligns with how Discord (gateway WS) and Telegram (long-polling) work — no inbound server needed

### Architecture

```
User (Feishu App)
    │
    ▼ (WebSocket long-connection)
┌──────────────────┐
│   LarkAdapter    │  head/platform/lark_adapter.py
│                  │
│  lark-oapi WS   │  - Receives events via WebSocket
│  client          │  - Sends messages via OpenAPI (REST)
│                  │  - Uploads files/images via OpenAPI
│  on_message()    │  - Parses commands from text messages
│  send_message()  │  - Sends rich text (post) or cards
│  send_file()     │  - Uploads file → file_key → send
│  edit_message()  │  - PATCH message content
└────────┬─────────┘
         │ InputHandler callback
         ▼
┌──────────────────┐
│    BotEngine     │  (unchanged - platform agnostic)
└──────────────────┘
```

### Feishu Platform Capabilities

| Capability | Support | Notes |
|------------|---------|-------|
| Message edit | Yes | PATCH `/im/v1/messages/{id}` |
| Message delete | Yes | DELETE `/im/v1/messages/{id}` |
| File upload | Yes | POST `/im/v1/files` → file_key |
| Image upload | Yes | POST `/im/v1/images` → image_key |
| Typing indicator | No | Feishu has no typing indicator API |
| Inline buttons | Yes | Interactive Card with action buttons |
| Max message length | ~30,000 chars | Post (rich text) type |
| File download | Yes | GET `/im/v1/messages/{id}/resources/{file_key}` |

### LarkAdapter Implementation

**File:** `head/platform/lark_adapter.py`

```python
class LarkAdapter:
    """Feishu/Lark platform adapter using WebSocket long-connection."""

    platform_name = "lark"
    max_message_length = 30000

    def __init__(self, config: LarkConfig):
        self.config = config
        self._client: lark.Client          # REST API client
        self._ws_client: lark.ws.Client    # WebSocket event client
        self._input_handler: InputHandler | None = None

    # --- Input Callback ---
    def set_input_handler(self, handler: InputHandler) -> None:
        """Set the callback invoked when a user message arrives."""
        self._input_handler = handler

    # --- Lifecycle ---
    async def start(self) -> None:
        """Initialize lark clients, register event handlers, start WS."""

    async def stop(self) -> None:
        """Close WS connection, cleanup."""

    # --- Message Operations ---
    async def send_message(self, channel_id: str, text: str) -> MessageHandle:
        """Send rich text (post) message to chat.

        Uses post message type for code block support.
        Falls back to plain text for simple messages.
        """

    async def send_card(self, channel_id: str, card: dict) -> MessageHandle:
        """Send interactive card message (for status, lists, etc).

        Not part of PlatformAdapter protocol - Lark-specific extension
        called by engine via supports_inline_buttons() capability check.
        """

    async def edit_message(self, handle: MessageHandle, text: str) -> None:
        """Edit message content via PATCH API."""

    async def delete_message(self, handle: MessageHandle) -> None:
        """Delete message via DELETE API."""

    # --- File Operations ---
    async def download_file(self, attachment: FileAttachment, dest: Path) -> Path:
        """Download file attachment from Feishu message."""

    async def send_file(self, channel_id: str, path: Path, caption: str = "") -> MessageHandle:
        """Upload file to Feishu, then send as file/image message.

        Auto-detects image types → uses image API (im/v1/images).
        Other types → uses file API (im/v1/files).
        """

    # --- Interaction State ---
    async def start_typing(self, channel_id: str) -> None:
        """No-op. Feishu has no typing indicator API."""

    async def stop_typing(self, channel_id: str) -> None:
        """No-op."""

    # --- Capability ---
    def supports_message_edit(self) -> bool: return True
    def supports_inline_buttons(self) -> bool: return True
    def supports_file_upload(self) -> bool: return True
```

### Event Handling

**Incoming messages** are received via WebSocket event subscription:

```python
# Event: im.message.receive_v1
def _on_message(self, event: lark.im.v1.P2ImMessageReceiveV1):
    msg = event.event.message
    chat_id = msg.chat_id           # Group or P2P chat ID
    sender_id = event.event.sender.sender_id.open_id
    content = json.loads(msg.content)  # {"text": "/start gpu-1 ~/project"}

    # Filter: allowed_chats check
    # Filter: ignore bot's own messages
    # Extract file attachments if present

    channel_id = f"lark:{chat_id}"
    await self._input_handler(channel_id, text, sender_id, attachments)
```

**Message format mapping:**

| Engine output | Feishu format |
|---------------|---------------|
| Plain text with markdown | `post` rich text (with code blocks, bold, links) |
| Status / health / machine list | Interactive Card (structured layout with fields) |
| Error message | `post` with red text styling |
| File attachment | `image` (for images) or `file` (for others) |

### Markdown to Feishu Post Conversion

Feishu's `post` message type uses a structured JSON format, not raw markdown. A converter is needed:

```python
def markdown_to_lark_post(text: str) -> dict:
    """Convert markdown text to Feishu post message structure.

    Supports:
    - **bold** → bold tag
    - `code` → code inline
    - ```code blocks``` → code block element
    - [link](url) → link tag
    - Plain text → text tag

    Returns: {"zh_cn": {"title": "", "content": [[...tags...]]}}
    """
```

This is similar to the existing `markdown_to_telegram_html()` in `telegram_adapter.py` but targets Feishu's JSON structure instead of HTML.

### Configuration

**File:** `config.example.yaml` addition:

```yaml
bot:
  lark:
    app_id: ${LARK_APP_ID}           # Feishu app ID (from open.feishu.cn)
    app_secret: ${LARK_APP_SECRET}   # Feishu app secret
    allowed_chats: []                # Chat IDs to respond in (empty = all)
    admin_users: []                  # Admin open_ids for /restart, /update
    use_cards: true                  # Use interactive cards for status display
```

**Config dataclass:**

```python
@dataclass
class LarkConfig:
    app_id: str
    app_secret: str
    allowed_chats: list[str] = field(default_factory=list)
    admin_users: list[str] = field(default_factory=list)  # Feishu open_id strings
    use_cards: bool = True
```

**BotConfig extension** (in `config.py`):

```python
@dataclass
class BotConfig:
    discord: Optional[DiscordConfig] = None
    telegram: Optional[TelegramConfig] = None
    lark: Optional[LarkConfig] = None            # NEW
```

### user_id Type Widening

Feishu uses string `open_id` (e.g., `"ou_xxxxxxxxxxxx"`) as user identifiers, unlike Discord/Telegram which use integer IDs. This requires:

1. **Widen `InputHandler` type** in `protocol.py`: Change `Optional[int]` to `Optional[str | int]` for the `user_id` parameter
2. **Update `is_admin()` in `engine.py`**: Add a `"lark"` branch, and ensure comparison works with string IDs:

```python
def is_admin(self, platform: str, user_id: str | int | None) -> bool:
    # ... existing discord/telegram branches ...
    if platform == "lark" and self.config.bot.lark:
        return str(user_id) in [str(u) for u in (self.config.bot.lark.admin_users or [])]
    return False
```

### Channel ID Convention

Following the existing pattern (`discord:<id>`, `telegram:<id>`):
- Lark channels: `lark:<chat_id>`
- The `chat_id` is Feishu's native chat identifier (group or P2P)

### Card Messages for Structured Output

For status-type commands (`/status`, `/ls`, `/health`, `/monitor`), the adapter uses Feishu Interactive Cards instead of plain text. This requires a small extension to the engine:

The engine already calls `self.adapter.supports_inline_buttons()` for capability detection. When this returns `True`, the engine can pass structured data that the adapter renders as a card. The mechanism:

1. Engine formats structured output as it does today (text with formatting)
2. LarkAdapter's `send_message()` detects structured patterns (e.g., machine lists, session tables) and optionally wraps them in card JSON
3. Simple text remains as post messages

This keeps the engine unchanged — the intelligence is in the adapter's rendering layer.

### Dependencies

- `lark-oapi>=1.4.0` — Official Feishu/Lark SDK (supports WebSocket mode)
- Add to `requirements.txt`

---

## Feature 2: Remote File Forwarding

### Problem

Claude CLI on remote machines frequently generates or references files (images, PDFs, code artifacts). Users currently have no way to see these files without manually SSH-ing to the remote machine.

### Approach

Add a **file path detection + download + forwarding** pipeline in `BotEngine._forward_message()`. When Claude's response text contains file paths matching user-configured rules, the engine:

1. Detects file paths in completed text blocks
2. Checks against configurable rules (extension, size limit, auto-send policy)
3. Downloads the file from remote via SSH
4. Sends to the chat channel via `adapter.send_file()`

### Detection Strategy

**When to detect:** On `text` events (complete text blocks) in `_forward_message()`. NOT on `partial` events (incomplete, would cause false matches and duplicate detections).

**What to detect:** Absolute and tilde-prefixed file paths in the text content. Regex pattern:

```python
# Match absolute paths (/...) and tilde paths (~/...) with file extensions
FILE_PATH_PATTERN = re.compile(
    r'(?<![`\w])((?:/|~/)(?:[\w.~-]+/)*[\w.-]+\.(\w+))(?![`\w])'
)
```

This matches paths like `/home/user/output.png`, `/tmp/chart.pdf`, `~/project/result.png`, but excludes:
- Paths inside backtick code spans (`` `/not/this.png` ``)
- Paths that are part of longer words
- Relative paths without leading `/` or `~/` (too ambiguous)

**Tilde expansion:** Detected `~/...` paths are passed as-is to the SSH download command. The remote shell expands `~` to the correct home directory on the remote machine. This is consistent with how the existing codebase handles tilde paths (deferred to remote).

**Deduplication:** Track forwarded paths per-session to avoid sending the same file twice in one response.

### Rule Engine

**File:** `head/file_forward.py` (new module)

```python
@dataclass
class FileForwardRule:
    """A single file forwarding rule."""
    pattern: str          # Glob pattern: "*.png", "*.pdf", "output.*"
    max_size: int         # Max file size in bytes (0 = no limit)
    auto: bool            # True = auto-forward; False = notify only

@dataclass
class FileForwardConfig:
    """File forwarding configuration."""
    enabled: bool = False
    rules: list[FileForwardRule] = field(default_factory=list)
    default_max_size: int = 5 * 1024 * 1024   # 5MB default
    default_auto: bool = False                  # Default: notify only
    download_dir: str = "~/.remote-code/downloads"  # Temp download location


class FileForwardMatcher:
    """Matches file paths against configured forwarding rules."""

    def __init__(self, config: FileForwardConfig):
        self.config = config
        # Per-channel dedup state. Multiple channels can stream concurrently,
        # so each channel gets its own set of forwarded paths.
        self._forwarded: dict[str, set[str]] = {}

    def reset(self, channel_id: str) -> None:
        """Reset dedup tracker for a channel (call at start of each _forward_message)."""
        self._forwarded[channel_id] = set()

    def cleanup(self, channel_id: str) -> None:
        """Remove dedup state for a channel (call when stream ends)."""
        self._forwarded.pop(channel_id, None)

    def detect_paths(self, text: str) -> list[str]:
        """Extract file paths from text that match any rule's pattern."""

    def match_rule(self, path: str) -> tuple[FileForwardRule | None, bool]:
        """Find the best matching rule for a path.

        Returns: (rule, is_default)
        - rule: matching FileForwardRule, or a synthetic default rule
        - is_default: True if no explicit rule matched (using defaults)

        Returns (None, _) if no rule matches and no default applies.
        """

    def should_forward(self, path: str, file_size: int) -> ForwardDecision:
        """Decide what to do with a detected file.

        Returns ForwardDecision with action and reason.
        """

@dataclass
class ForwardDecision:
    action: str      # "auto_send", "notify", "skip"
    reason: str      # Human-readable reason
    rule: FileForwardRule | None
```

### Rule Matching Logic

```
For each detected file path:
  1. Extract extension from path
  2. Iterate rules in order, find first matching glob pattern
  3. If rule found:
     - Check file size vs rule.max_size
     - If size <= max_size and rule.auto → action = "auto_send"
     - If size > max_size → action = "notify" (tell user, don't auto-send)
     - If not rule.auto → action = "notify"
  4. If no rule matches:
     - Use default_max_size and default_auto
     - Same size check logic
  5. Dedup: skip if path already forwarded in this stream
```

### Configuration

```yaml
# Remote file forwarding settings
file_forward:
  enabled: true
  download_dir: ~/.remote-code/downloads   # Temp local storage
  default_max_size: 5242880                  # 5MB - default max for unmatched files
  default_auto: false                        # Unmatched files: notify only (don't auto-send)
  rules:
    # Auto-send images up to 5MB
    - pattern: "*.png"
      max_size: 5242880
      auto: true
    - pattern: "*.jpg"
      max_size: 5242880
      auto: true
    - pattern: "*.jpeg"
      max_size: 5242880
      auto: true
    - pattern: "*.gif"
      max_size: 5242880
      auto: true
    - pattern: "*.svg"
      max_size: 1048576       # 1MB for SVG
      auto: true
    - pattern: "*.webp"
      max_size: 5242880
      auto: true

    # Auto-send PDFs up to 10MB
    - pattern: "*.pdf"
      max_size: 10485760
      auto: true

    # Notify for archives (don't auto-send)
    - pattern: "*.zip"
      max_size: 52428800      # 50MB
      auto: false
    - pattern: "*.tar.gz"
      max_size: 52428800
      auto: false
```

### Download Pipeline

The download uses the existing SSH infrastructure:

```
Engine._forward_message()
    │
    │ text event with "/home/user/chart.png"
    ▼
FileForwardMatcher.detect_paths(text)
    │
    │ ["/home/user/chart.png"]
    ▼
FileForwardMatcher.should_forward(path, size=0)  # Intent check only, no size validation
    │
    │ ForwardDecision(action="auto_send", rule=*.png)
    ▼
ssh_manager.download_file(machine_id, remote_path, local_dest)
    │
    │ SCP via existing SSH tunnel
    ▼
Check actual file size against rule.max_size
    │
    ├─ OK → adapter.send_file(channel_id, local_path, caption)
    │
    └─ Too large → send_message("File chart.png (15MB) exceeds limit. Use /download to fetch manually.")
```

**Size check happens twice:**
1. **Pre-download** (optional): If the daemon can report file size via a new lightweight RPC call (`file.stat`), check before downloading. This is an optimization — skip large files without transferring them.
2. **Post-download**: Always check the actual downloaded file size. This is the authoritative check.

### Engine Integration

Changes to `BotEngine._forward_message()`:

```python
async def _forward_message(self, channel_id, text, file_refs=None):
    # ... existing setup ...

    # NEW: Initialize file forward matcher for this stream
    if self.file_forward and self.file_forward.config.enabled:
        self.file_forward.reset(channel_id)

    async for event in self.daemon.send_message(...):
        # ... existing event handling ...

        if event_type == "text":
            content = event.get("content", "")
            # ... existing text handling (send/edit message) ...

            # NEW: Detect and forward files from completed text
            if self.file_forward and self.file_forward.config.enabled:
                await self._detect_and_forward_files(
                    channel_id, session.machine_id, content
                )

    # ... existing flush logic ...


async def _detect_and_forward_files(
    self, channel_id: str, machine_id: str, text: str
) -> None:
    """Detect file paths in text and forward matching files."""
    paths = self.file_forward.detect_paths(text, channel_id)
    for path in paths:
        # Pre-download intent check (file_size=0 means "ignore size, just check rule match + auto flag")
        decision = self.file_forward.should_forward(path, file_size=0)

        if decision.action == "auto_send":
            local_path = None
            try:
                local_path = await self.ssh.download_file(
                    machine_id, path, self.file_forward.config.download_dir
                )
                actual_size = local_path.stat().st_size
                # Authoritative size check with actual file
                decision = self.file_forward.should_forward(path, actual_size)
                if decision.action == "auto_send":
                    filename = Path(path).name
                    await self.adapter.send_file(
                        channel_id, local_path, caption=f"📎 {filename}"
                    )
                else:
                    await self.send_message(
                        channel_id,
                        f"File `{Path(path).name}` ({actual_size // 1024}KB) "
                        f"exceeds size limit. {decision.reason}"
                    )
            except Exception as e:
                logger.warning(f"Failed to forward file {path}: {e}")
            finally:
                # Always cleanup temp file regardless of outcome
                if local_path and local_path.exists():
                    local_path.unlink(missing_ok=True)

        elif decision.action == "notify":
            await self.send_message(
                channel_id,
                f"Detected file: `{path}` — {decision.reason}"
            )
```

### SSH Download Extension

Add to `ssh_manager.py`:

```python
async def download_file(
    self, machine_id: str, remote_path: str, local_dir: str
) -> Path:
    """Download a file from remote machine via SCP.

    Returns the local path of the downloaded file.
    Raises FileNotFoundError if remote file doesn't exist.
    Raises PermissionError if no read access.
    """
```

This is the inverse of the existing `upload_files()` method, using the same SSH connection and tunnel infrastructure.

---

## Testing Strategy

### Lark Adapter Tests

**File:** `tests/test_lark_adapter.py`

Test structure mirrors `test_bot_commands.py` with a `MockLarkClient` that simulates `lark-oapi` SDK responses.

| Test Group | Tests | Description |
|------------|-------|-------------|
| `TestLarkAdapterBasic` | 8 | Lifecycle (start/stop), send_message, edit_message, delete_message, send_file, capabilities |
| `TestLarkMessageFormat` | 10 | markdown_to_lark_post conversion: bold, code, code blocks, links, mixed content, edge cases |
| `TestLarkCardRendering` | 6 | Status card, machine list card, health card, monitor card formatting |
| `TestLarkEventHandling` | 8 | on_message parsing, command extraction, allowed_chats filter, admin check, file attachment extraction, bot self-message ignore, group vs P2P |
| `TestLarkCommandIntegration` | 12 | All 17 commands via LarkAdapter+BotEngine (same test matrix as Discord/Telegram) |

**Mock strategy:**
- Mock `lark.Client` for REST API calls (send/edit/delete message, upload file)
- Mock `lark.ws.Client` for WebSocket lifecycle
- Use callback injection to simulate incoming messages

### File Forward Tests

**File:** `tests/test_file_forward.py`

| Test Group | Tests | Description |
|------------|-------|-------------|
| `TestFilePathDetection` | 12 | Regex detection: absolute paths, various extensions, paths in backticks (skip), paths in URLs (skip), multiple paths in one block, deduplication, no false positives on non-path text |
| `TestRuleMatching` | 10 | Glob matching, rule priority (first match wins), max_size check, auto/notify decision, default fallback, no matching rule, empty ruleset |
| `TestForwardDecision` | 8 | auto_send when under size, notify when over size, notify when auto=false, skip when no match and default_auto=false, dedup across calls |
| `TestForwardPipeline` | 10 | Integration: detect → decide → download → send, SSH download failure handling, file cleanup after send, size re-check after download, multiple files in one text block, concurrent forward safety |
| `TestForwardConfig` | 5 | Config parsing, default values, empty config (disabled), rule validation |

**Mock strategy:**
- Mock `ssh_manager.download_file()` to return temp files
- Mock `adapter.send_file()` to verify correct calls
- Use real `FileForwardMatcher` with test configs

### Estimated Test Count

| File | New Tests |
|------|-----------|
| `test_lark_adapter.py` | ~44 |
| `test_file_forward.py` | ~45 |
| **Total new** | **~89** |

Combined with existing 368 tests → **~457 total**.

---

## File Summary

### New Files

| File | Purpose |
|------|---------|
| `head/platform/lark_adapter.py` | Feishu adapter implementing PlatformAdapter |
| `head/file_forward.py` | File forwarding rule engine (detection, matching, decisions) |
| `tests/test_lark_adapter.py` | Lark adapter tests |
| `tests/test_file_forward.py` | File forwarding tests |

### Modified Files

| File | Changes |
|------|---------|
| `head/config.py` | Add `LarkConfig`, `FileForwardConfig`, `FileForwardRule` dataclasses; add `lark` field to `BotConfig`; add `file_forward` field to `Config`; parse new config sections |
| `head/platform/protocol.py` | Widen `InputHandler` user_id type from `Optional[int]` to `Optional[str \| int]` for Feishu string open_id compatibility |
| `head/engine.py` | Add `"lark"` branch to `is_admin()`; add `_detect_and_forward_files()` method; integrate file forwarding into `_forward_message()` event loop |
| `head/ssh_manager.py` | Add `download_file()` method (SCP download, inverse of upload) |
| `head/platform/__init__.py` | Export `LarkAdapter` |
| `head/main.py` | Add Lark adapter initialization when `config.bot.lark` is present |
| `config.example.yaml` | Add `lark` and `file_forward` sections |
| `requirements.txt` | Add `lark-oapi>=1.4.0` |

### Unchanged Files
- `head/platform/discord_adapter.py` — Unaffected
- `head/platform/telegram_adapter.py` — Unaffected
- `head/daemon_client.py` — Unaffected (file download uses SSH, not daemon RPC)
- `head/file_pool.py` — Existing file pool is for user→remote uploads; file forward uses its own download dir
- All existing test files — No changes

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| False positive path detection | Sends wrong files to chat | Conservative regex (absolute paths only, exclude backticks/URLs), dedup |
| Large file download blocks streaming | Delays response delivery | Download in background task (`asyncio.create_task`), don't block event loop |
| Feishu API rate limits | Message send failures | Retry with backoff (lark-oapi SDK handles this) |
| Feishu WS disconnection | Missed messages | SDK auto-reconnect; log warnings |
| File download via SSH timeout | Stale temp files | Timeout on SCP (30s default), cleanup temp files on error |
| Remote file permissions | Download failure | Catch PermissionError, notify user |
