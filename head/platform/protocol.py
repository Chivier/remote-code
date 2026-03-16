"""Platform adapter protocol defining the interface each chat platform must implement."""

from typing import Protocol, Any, Optional, runtime_checkable, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MessageHandle:
    """Platform-agnostic message handle for subsequent edit/delete."""

    platform: str  # "discord", "telegram", "lark"
    channel_id: str  # Unified channel ID (includes platform prefix)
    message_id: str  # Platform-native message ID (stringified)
    raw: Any = None  # Platform-native message object (needed for edit/delete)


@dataclass
class FileAttachment:
    """Platform-agnostic file attachment descriptor."""

    filename: str  # Original filename
    size: int  # File size in bytes (0 if unknown)
    mime_type: Optional[str]  # MIME type
    url: Optional[str]  # Direct download URL (Discord has, Telegram doesn't)
    platform_ref: Any  # Platform-native reference (Discord Attachment / Telegram File)


# Type alias for the input handler callback
InputHandler = Callable[
    [str, str, Optional[int], Optional[list[FileAttachment]]],
    Coroutine[Any, Any, None],
]


@runtime_checkable
class PlatformAdapter(Protocol):
    """Interface that each chat platform must implement."""

    @property
    def platform_name(self) -> str:
        """Platform identifier: 'discord', 'telegram', 'lark'."""
        ...

    @property
    def max_message_length(self) -> int:
        """Maximum message length: 2000 (Discord), 4096 (Telegram), etc."""
        ...

    # --- Message Operations ---
    async def send_message(self, channel_id: str, text: str) -> MessageHandle:
        """Send a new message to a channel. Returns a handle for edit/delete."""
        ...

    async def edit_message(self, handle: MessageHandle, text: str) -> None:
        """Edit an existing message using its handle."""
        ...

    async def delete_message(self, handle: MessageHandle) -> None:
        """Delete an existing message using its handle."""
        ...

    # --- File Operations ---
    async def download_file(self, attachment: FileAttachment, dest: Path) -> Path:
        """Download a file attachment to a local path."""
        ...

    async def send_file(
        self, channel_id: str, path: Path, caption: str = ""
    ) -> MessageHandle:
        """Send a file to a channel."""
        ...

    # --- Interaction State ---
    async def start_typing(self, channel_id: str) -> None:
        """Start showing typing indicator in a channel."""
        ...

    async def stop_typing(self, channel_id: str) -> None:
        """Stop showing typing indicator in a channel."""
        ...

    # --- Capability Queries ---
    def supports_message_edit(self) -> bool:
        """Whether this platform supports editing sent messages."""
        ...

    def supports_inline_buttons(self) -> bool:
        """Whether this platform supports inline buttons/actions."""
        ...

    def supports_file_upload(self) -> bool:
        """Whether this platform supports file uploads."""
        ...

    # --- Input Callback ---
    def set_input_handler(self, handler: InputHandler) -> None:
        """Set the callback invoked when a user message arrives."""
        ...

    # --- Lifecycle ---
    async def start(self) -> None:
        """Connect to the platform and begin listening for events."""
        ...

    async def stop(self) -> None:
        """Disconnect from the platform and clean up resources."""
        ...
