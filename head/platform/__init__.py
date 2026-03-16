"""Platform adapter layer for multi-platform chat bot support."""

from head.platform.protocol import (
    FileAttachment,
    InputHandler,
    MessageHandle,
    PlatformAdapter,
)
from head.platform.discord_adapter import DiscordAdapter
from head.platform.telegram_adapter import TelegramAdapter

__all__ = [
    "PlatformAdapter",
    "MessageHandle",
    "FileAttachment",
    "InputHandler",
    "DiscordAdapter",
    "TelegramAdapter",
]
