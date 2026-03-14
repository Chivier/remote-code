"""
Telegram Bot implementation for Remote Claude.

Uses python-telegram-bot (v20+) with async handlers.
"""

import asyncio
import logging
from typing import Any, Optional

from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

from .config import Config, TelegramConfig
from .ssh_manager import SSHManager
from .session_router import SessionRouter
from .daemon_client import DaemonClient
from .bot_base import BotBase
from .message_formatter import split_message

logger = logging.getLogger(__name__)


class TelegramBot(BotBase):
    """Telegram bot implementation."""

    def __init__(
        self,
        ssh_manager: SSHManager,
        session_router: SessionRouter,
        daemon_client: DaemonClient,
        config: Config,
    ):
        super().__init__(ssh_manager, session_router, daemon_client, config)
        self.telegram_config: Optional[TelegramConfig] = config.bot.telegram

        if not self.telegram_config:
            raise ValueError("Telegram config not found in config.yaml")

        self._app: Optional[Application] = None  # type: ignore
        self._bot: Optional[Bot] = None
        # Cache for message objects (telegram message_id -> message)
        self._last_messages: dict[str, int] = {}  # channel_id -> last message_id

    def _is_allowed_user(self, user_id: int) -> bool:
        """Check if a Telegram user is allowed."""
        if not self.telegram_config or not self.telegram_config.allowed_users:
            return True  # No restrictions
        return user_id in self.telegram_config.allowed_users

    def _channel_id(self, chat_id: int) -> str:
        """Build internal channel ID from Telegram chat ID."""
        return f"telegram:{chat_id}"

    def _chat_id_from_channel(self, channel_id: str) -> int:
        """Extract Telegram chat ID from internal channel ID."""
        return int(channel_id.split(":")[1])

    async def send_message(self, channel_id: str, text: str) -> Any:
        """Send a message to a Telegram chat."""
        if not self._bot:
            logger.warning("Telegram bot not initialized")
            return None

        chat_id = self._chat_id_from_channel(channel_id)

        # Telegram supports 4096 chars per message
        chunks = split_message(text, max_len=4096)
        last_msg = None

        for chunk in chunks:
            try:
                # Try sending with Markdown formatting
                last_msg = await self._bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                try:
                    # Fallback: send without formatting
                    last_msg = await self._bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                    )
                except Exception as e:
                    logger.error(f"Failed to send Telegram message: {e}")

        if last_msg:
            self._last_messages[channel_id] = last_msg.message_id

        return last_msg

    async def edit_message(self, channel_id: str, message_obj: Any, text: str) -> None:
        """Edit an existing Telegram message."""
        if not self._bot:
            return

        chat_id = self._chat_id_from_channel(channel_id)

        # Get message_id from the message object
        if hasattr(message_obj, "message_id"):
            message_id = message_obj.message_id
        else:
            return

        # Telegram supports up to 4096 chars
        if len(text) > 4096:
            text = text[:4093] + "..."

        try:
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            try:
                # Fallback without markdown
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                )
            except Exception as e:
                logger.warning(f"Failed to edit Telegram message: {e}")

    async def _handle_telegram_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming Telegram messages."""
        if not update.message or not update.message.text:
            return

        user = update.effective_user
        if not user or not self._is_allowed_user(user.id):
            return

        chat_id = update.effective_chat.id if update.effective_chat else 0
        channel_id = self._channel_id(chat_id)
        text = update.message.text

        await self.handle_input(channel_id, text)

    async def _handle_telegram_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle Telegram slash commands (they arrive without the / in some cases)."""
        if not update.message or not update.message.text:
            return

        user = update.effective_user
        if not user or not self._is_allowed_user(user.id):
            return

        chat_id = update.effective_chat.id if update.effective_chat else 0
        channel_id = self._channel_id(chat_id)
        text = update.message.text

        # Telegram strips the command prefix, re-add it
        if not text.startswith("/"):
            text = "/" + text

        await self.handle_input(channel_id, text)

    async def start(self) -> None:
        """Start the Telegram bot."""
        if not self.telegram_config:
            raise ValueError("Telegram config not set")

        token = self.telegram_config.token
        if not token:
            raise ValueError("Telegram token is empty. Set TELEGRAM_TOKEN environment variable.")

        logger.info("Starting Telegram bot...")

        # Build application
        self._app = Application.builder().token(token).build()
        self._bot = self._app.bot

        # Register command handlers
        command_names = ["start", "resume", "ls", "list", "exit", "rm", "remove",
                         "destroy", "mode", "status", "rename", "health", "monitor", "help"]
        for cmd in command_names:
            self._app.add_handler(CommandHandler(cmd, self._handle_telegram_command))

        # Register message handler (non-command messages)
        self._app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_telegram_message,
        ))

        # Start polling
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()  # type: ignore

        logger.info("Telegram bot started")

        # Keep running
        # The caller (main.py) manages the event loop

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self._app:
            logger.info("Stopping Telegram bot...")
            if self._app.updater:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
