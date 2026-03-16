"""
Telegram adapter implementing PlatformAdapter protocol.

Uses python-telegram-bot (v20+) with async handlers.
Full feature parity: HTML formatting, typing indicator, file transfer,
admin/group support, rate limit handling, command registration.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from telegram import Update, Bot, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter

from head.config import TelegramConfig
from head.message_formatter import split_message
from head.platform.protocol import (
    PlatformAdapter,
    MessageHandle,
    FileAttachment,
    InputHandler,
)
from head.platform.format_utils import markdown_to_telegram_html

logger = logging.getLogger(__name__)

# Telegram Bot API file size limit for regular bots
TELEGRAM_FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB


class TelegramAdapter:
    """Telegram adapter implementing PlatformAdapter protocol."""

    def __init__(self, telegram_config: TelegramConfig):
        self._config = telegram_config
        self._app: Optional[Application] = None  # type: ignore
        self._bot: Optional[Bot] = None
        self._on_input: Optional[InputHandler] = None
        # Cache for last message per channel (for editing)
        self._last_messages: dict[str, int] = {}
        # Active typing indicator tasks per channel
        self._typing_tasks: dict[str, asyncio.Task] = {}

    @property
    def platform_name(self) -> str:
        return "telegram"

    @property
    def max_message_length(self) -> int:
        return 4096

    # --- Message Operations ---

    async def send_message(self, channel_id: str, text: str) -> MessageHandle:
        """Send a message to a Telegram chat using HTML formatting."""
        if not self._bot:
            logger.warning("Telegram bot not initialized")
            return MessageHandle(
                platform="telegram",
                channel_id=channel_id,
                message_id="0",
            )

        chat_id = self._chat_id_from_channel(channel_id)
        chunks = split_message(text, max_len=4096)
        last_msg = None

        for chunk in chunks:
            try:
                # Try sending with HTML formatting
                html_text = markdown_to_telegram_html(chunk)
                last_msg = await self._bot.send_message(
                    chat_id=chat_id,
                    text=html_text,
                    parse_mode=ParseMode.HTML,
                )
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)
                try:
                    html_text = markdown_to_telegram_html(chunk)
                    last_msg = await self._bot.send_message(
                        chat_id=chat_id,
                        text=html_text,
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as e2:
                    logger.error(f"Failed to send Telegram message after retry: {e2}")
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
            return MessageHandle(
                platform="telegram",
                channel_id=channel_id,
                message_id=str(last_msg.message_id),
                raw=last_msg,
            )

        return MessageHandle(
            platform="telegram",
            channel_id=channel_id,
            message_id="0",
        )

    async def edit_message(self, handle: MessageHandle, text: str) -> None:
        """Edit an existing Telegram message."""
        if not self._bot:
            return

        chat_id = self._chat_id_from_channel(handle.channel_id)

        # Get message_id from handle
        if handle.raw and hasattr(handle.raw, "message_id"):
            message_id = handle.raw.message_id
        else:
            try:
                message_id = int(handle.message_id)
            except (ValueError, TypeError):
                return

        if len(text) > 4096:
            text = text[:4093] + "..."

        try:
            html_text = markdown_to_telegram_html(text)
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=html_text,
                parse_mode=ParseMode.HTML,
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass  # Silently ignore identical content
            else:
                try:
                    # Fallback without HTML
                    await self._bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                    )
                except Exception as e2:
                    logger.warning(f"Failed to edit Telegram message: {e2}")
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await self.edit_message(handle, text)
            except Exception:
                pass
        except Exception:
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                )
            except Exception as e:
                logger.warning(f"Failed to edit Telegram message: {e}")

    async def delete_message(self, handle: MessageHandle) -> None:
        """Delete a Telegram message."""
        if not self._bot:
            return

        chat_id = self._chat_id_from_channel(handle.channel_id)
        try:
            message_id = int(handle.message_id)
            await self._bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.warning(f"Failed to delete Telegram message: {e}")

    # --- File Operations ---

    async def download_file(
        self, attachment: FileAttachment, dest: Path
    ) -> Path:
        """Download a Telegram file attachment to local path."""
        if not self._bot:
            raise RuntimeError("Telegram bot not initialized")

        if attachment.size > TELEGRAM_FILE_SIZE_LIMIT:
            raise ValueError(
                f"File {attachment.filename} ({attachment.size} bytes) exceeds "
                f"Telegram's {TELEGRAM_FILE_SIZE_LIMIT // (1024*1024)}MB limit"
            )

        # platform_ref should be a telegram File object
        tg_file = attachment.platform_ref
        if tg_file is None:
            raise ValueError("No Telegram file reference provided")

        # Download using telegram File object
        await tg_file.download_to_drive(str(dest))
        return dest

    async def send_file(
        self, channel_id: str, path: Path, caption: str = ""
    ) -> MessageHandle:
        """Send a file to a Telegram chat."""
        if not self._bot:
            raise RuntimeError("Telegram bot not initialized")

        chat_id = self._chat_id_from_channel(channel_id)

        with open(path, "rb") as f:
            msg = await self._bot.send_document(
                chat_id=chat_id,
                document=f,
                caption=caption[:1024] if caption else None,  # Telegram caption limit
            )

        return MessageHandle(
            platform="telegram",
            channel_id=channel_id,
            message_id=str(msg.message_id),
            raw=msg,
        )

    # --- Interaction State ---

    async def start_typing(self, channel_id: str) -> None:
        """Start showing typing indicator in a channel (loops every 4s)."""
        if not self._bot:
            return

        chat_id = self._chat_id_from_channel(channel_id)

        async def typing_loop() -> None:
            try:
                while True:
                    try:
                        from telegram.constants import ChatAction
                        await self._bot.send_chat_action(chat_id, ChatAction.TYPING)
                    except Exception:
                        pass
                    await asyncio.sleep(4)
            except asyncio.CancelledError:
                pass

        # Cancel any existing task for this channel
        existing = self._typing_tasks.pop(channel_id, None)
        if existing:
            existing.cancel()

        self._typing_tasks[channel_id] = asyncio.create_task(typing_loop())

    async def stop_typing(self, channel_id: str) -> None:
        """Stop the typing indicator for a channel."""
        task = self._typing_tasks.pop(channel_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # --- Capability Queries ---

    def supports_message_edit(self) -> bool:
        return True

    def supports_inline_buttons(self) -> bool:
        return False

    def supports_file_upload(self) -> bool:
        return True

    # --- Input Callback ---

    def set_input_handler(self, handler: InputHandler) -> None:
        """Set the callback invoked when a user message arrives."""
        self._on_input = handler

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the Telegram bot."""
        token = self._config.token
        if not token:
            raise ValueError(
                "Telegram token is empty. Set TELEGRAM_TOKEN environment variable."
            )

        logger.info("Starting Telegram bot...")

        self._app = Application.builder().token(token).build()
        self._bot = self._app.bot

        # Register command handlers
        command_names = [
            "start", "resume", "ls", "list", "exit", "rm", "remove",
            "destroy", "mode", "status", "rename", "interrupt",
            "health", "monitor", "help",
            "add_machine", "remove_machine", "update", "restart",
        ]
        for cmd in command_names:
            self._app.add_handler(
                CommandHandler(cmd, self._handle_telegram_command)
            )

        # Register message handler (non-command messages)
        self._app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._handle_telegram_message,
            )
        )

        # Register command menu
        try:
            await self._bot.set_my_commands([
                BotCommand("start", "Start a new Claude session"),
                BotCommand("resume", "Resume a previous session"),
                BotCommand("ls", "List machines or sessions"),
                BotCommand("exit", "Detach from current session"),
                BotCommand("mode", "Switch permission mode"),
                BotCommand("status", "Show current session info"),
                BotCommand("interrupt", "Interrupt Claude"),
                BotCommand("rename", "Rename current session"),
                BotCommand("health", "Check daemon health"),
                BotCommand("monitor", "Monitor session details"),
                BotCommand("help", "Show available commands"),
            ])
        except Exception as e:
            logger.warning(f"Failed to set Telegram bot commands: {e}")

        self._stop_event = asyncio.Event()

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()  # type: ignore

        logger.info("Telegram bot started")

        # Block until stop() is called (mirrors Discord's start() behavior)
        await self._stop_event.wait()

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if hasattr(self, '_stop_event'):
            self._stop_event.set()
        if self._app:
            logger.info("Stopping Telegram bot...")
            # Cancel all typing tasks
            for task in self._typing_tasks.values():
                task.cancel()
            self._typing_tasks.clear()
            if self._app.updater:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    # --- Internal Helpers ---

    def _is_allowed_user(self, user_id: int) -> bool:
        """Check if a Telegram user is allowed."""
        if not self._config.allowed_users:
            return True
        return user_id in self._config.allowed_users

    def _is_allowed_chat(self, chat_id: int) -> bool:
        """Check if a Telegram chat is allowed (for group filtering)."""
        if not self._config.allowed_chats:
            return True
        return chat_id in self._config.allowed_chats

    def _channel_id(self, chat_id: int) -> str:
        """Build internal channel ID from Telegram chat ID."""
        return f"telegram:{chat_id}"

    def _chat_id_from_channel(self, channel_id: str) -> int:
        """Extract Telegram chat ID from internal channel ID."""
        return int(channel_id.split(":")[1])

    def _strip_bot_mention(self, text: str) -> str:
        """Strip @botname suffix from commands in group chats."""
        # e.g., "/start@MyClaudeBot" -> "/start"
        if "@" in text and text.startswith("/"):
            parts = text.split(None, 1)
            cmd = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            cmd = cmd.split("@")[0]
            return f"{cmd} {rest}".strip() if rest else cmd
        return text

    async def _handle_telegram_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming Telegram messages."""
        if not update.message or not update.message.text:
            return

        user = update.effective_user
        if not user or not self._is_allowed_user(user.id):
            return

        chat = update.effective_chat
        chat_id = chat.id if chat else 0

        # Group chat filtering
        if chat_id < 0 and not self._is_allowed_chat(chat_id):
            return

        channel_id = self._channel_id(chat_id)
        text = update.message.text

        if self._on_input:
            await self._on_input(channel_id, text, user.id, None)

    async def _handle_telegram_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle Telegram slash commands."""
        if not update.message or not update.message.text:
            return

        user = update.effective_user
        if not user or not self._is_allowed_user(user.id):
            return

        chat = update.effective_chat
        chat_id = chat.id if chat else 0

        # Group chat filtering
        if chat_id < 0 and not self._is_allowed_chat(chat_id):
            return

        channel_id = self._channel_id(chat_id)
        text = update.message.text

        # Strip @botname suffix in group chats
        text = self._strip_bot_mention(text)

        # Telegram strips the command prefix in some cases, re-add it
        if not text.startswith("/"):
            text = "/" + text

        # Map underscored commands to hyphenated equivalents
        # (Telegram doesn't support hyphens in command names)
        text = text.replace("/add_machine", "/add-machine")
        text = text.replace("/remove_machine", "/remove-machine")

        if self._on_input:
            await self._on_input(channel_id, text, user.id, None)
