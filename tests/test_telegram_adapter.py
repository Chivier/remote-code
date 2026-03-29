"""
Tests for TelegramAdapter (head/platform/telegram_adapter.py)
and format_utils (head/platform/format_utils.py).
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from pathlib import Path
from typing import Any, Optional

from head.config import TelegramConfig
from head.platform.protocol import MessageHandle, FileAttachment, InputHandler
from head.platform.format_utils import markdown_to_telegram_html
from head.platform.telegram_adapter import TelegramAdapter, TELEGRAM_FILE_SIZE_LIMIT


# ─── Helpers ───


def make_config(**overrides) -> TelegramConfig:
    defaults = dict(
        token="test-token-123",
        allowed_users=[],
        admin_users=[],
        allowed_chats=[],
    )
    defaults.update(overrides)
    return TelegramConfig(**defaults)


def make_adapter(**config_overrides) -> TelegramAdapter:
    """Create a TelegramAdapter with a mocked bot."""
    adapter = TelegramAdapter(make_config(**config_overrides))
    adapter._bot = AsyncMock()
    return adapter


def make_message(message_id: int = 42, text: str = "hello"):
    """Create a mock Telegram Message object."""
    msg = MagicMock()
    msg.message_id = message_id
    msg.text = text
    return msg


def make_update(
    text: str = "/start",
    user_id: int = 100,
    chat_id: int = 200,
    username: str = "testuser",
):
    """Create a mock Telegram Update object."""
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update


# ─── Tests: Properties & Capabilities ───


class TestTelegramAdapterProperties:
    def test_platform_name(self):
        adapter = TelegramAdapter(make_config())
        assert adapter.platform_name == "telegram"

    def test_max_message_length(self):
        adapter = TelegramAdapter(make_config())
        assert adapter.max_message_length == 4096

    def test_supports_message_edit(self):
        adapter = TelegramAdapter(make_config())
        assert adapter.supports_message_edit() is True

    def test_supports_inline_buttons(self):
        adapter = TelegramAdapter(make_config())
        assert adapter.supports_inline_buttons() is True

    def test_supports_file_upload(self):
        adapter = TelegramAdapter(make_config())
        assert adapter.supports_file_upload() is True


# ─── Tests: Channel ID Mapping ───


class TestChannelIdMapping:
    def test_channel_id_from_chat_id(self):
        adapter = TelegramAdapter(make_config())
        assert adapter._channel_id(12345) == "telegram:12345"

    def test_chat_id_from_channel(self):
        adapter = TelegramAdapter(make_config())
        assert adapter._chat_id_from_channel("telegram:12345") == 12345

    def test_negative_chat_id_group(self):
        adapter = TelegramAdapter(make_config())
        assert adapter._channel_id(-100123456) == "telegram:-100123456"
        assert adapter._chat_id_from_channel("telegram:-100123456") == -100123456


# ─── Tests: Access Control ───


class TestAccessControl:
    def test_allowed_users_empty_allows_all(self):
        adapter = TelegramAdapter(make_config(allowed_users=[]))
        assert adapter._is_allowed_user(999) is True

    def test_allowed_users_restricts(self):
        adapter = TelegramAdapter(make_config(allowed_users=[100, 200]))
        assert adapter._is_allowed_user(100) is True
        assert adapter._is_allowed_user(200) is True
        assert adapter._is_allowed_user(300) is False

    def test_allowed_chats_empty_allows_all(self):
        adapter = TelegramAdapter(make_config(allowed_chats=[]))
        assert adapter._is_allowed_chat(-100123) is True

    def test_allowed_chats_restricts(self):
        adapter = TelegramAdapter(make_config(allowed_chats=[-100123]))
        assert adapter._is_allowed_chat(-100123) is True
        assert adapter._is_allowed_chat(-100999) is False


# ─── Tests: Bot Mention Stripping ───


class TestStripBotMention:
    def test_no_mention(self):
        adapter = TelegramAdapter(make_config())
        assert adapter._strip_bot_mention("/start") == "/start"

    def test_mention_no_args(self):
        adapter = TelegramAdapter(make_config())
        assert adapter._strip_bot_mention("/start@MyBot") == "/start"

    def test_mention_with_args(self):
        adapter = TelegramAdapter(make_config())
        result = adapter._strip_bot_mention("/start@MyBot machine1 /path")
        assert result == "/start machine1 /path"

    def test_non_command_with_at(self):
        adapter = TelegramAdapter(make_config())
        result = adapter._strip_bot_mention("hello @someone")
        assert result == "hello @someone"


# ─── Tests: Send Message ───


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_basic(self):
        adapter = make_adapter()
        msg = make_message(42)
        adapter._bot.send_message = AsyncMock(return_value=msg)

        handle = await adapter.send_message("telegram:200", "Hello world")

        assert handle.platform == "telegram"
        assert handle.channel_id == "telegram:200"
        assert handle.message_id == "42"
        assert handle.raw == msg

    @pytest.mark.asyncio
    async def test_send_message_html_format(self):
        adapter = make_adapter()
        msg = make_message(42)
        adapter._bot.send_message = AsyncMock(return_value=msg)

        await adapter.send_message("telegram:200", "**bold** text")

        call_kwargs = adapter._bot.send_message.call_args
        assert call_kwargs.kwargs.get("parse_mode") == "HTML"

    @pytest.mark.asyncio
    async def test_send_message_html_fallback_on_error(self):
        """When HTML send fails, should fallback to plain text."""
        adapter = make_adapter()
        msg = make_message(42)
        # First call (HTML) fails, second call (plain) succeeds
        adapter._bot.send_message = AsyncMock(side_effect=[Exception("parse error"), msg])

        handle = await adapter.send_message("telegram:200", "bad **markdown")

        assert handle.message_id == "42"
        # Second call should be plain text (no parse_mode)
        second_call = adapter._bot.send_message.call_args_list[1]
        assert "parse_mode" not in second_call.kwargs

    @pytest.mark.asyncio
    async def test_send_message_no_bot_returns_zero_id(self):
        adapter = TelegramAdapter(make_config())
        # _bot is None
        handle = await adapter.send_message("telegram:200", "Hello")
        assert handle.message_id == "0"

    @pytest.mark.asyncio
    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    async def test_send_message_retry_after(self):
        """Should sleep and retry on RetryAfter error."""
        from telegram.error import RetryAfter

        adapter = make_adapter()
        msg = make_message(42)
        retry_err = RetryAfter(0)
        adapter._bot.send_message = AsyncMock(side_effect=[retry_err, msg])

        handle = await adapter.send_message("telegram:200", "Hello")
        assert handle.message_id == "42"
        assert adapter._bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_send_message_caches_last_message_id(self):
        adapter = make_adapter()
        msg = make_message(42)
        adapter._bot.send_message = AsyncMock(return_value=msg)

        await adapter.send_message("telegram:200", "Hello")

        assert adapter._last_messages["telegram:200"] == 42


# ─── Tests: Edit Message ───


class TestEditMessage:
    @pytest.mark.asyncio
    async def test_edit_message_basic(self):
        adapter = make_adapter()
        adapter._bot.edit_message_text = AsyncMock()

        handle = MessageHandle(
            platform="telegram",
            channel_id="telegram:200",
            message_id="42",
        )
        await adapter.edit_message(handle, "Updated text")

        adapter._bot.edit_message_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_message_with_raw_object(self):
        adapter = make_adapter()
        adapter._bot.edit_message_text = AsyncMock()

        raw = MagicMock()
        raw.message_id = 99
        handle = MessageHandle(
            platform="telegram",
            channel_id="telegram:200",
            message_id="42",
            raw=raw,
        )
        await adapter.edit_message(handle, "Updated")

        call_kwargs = adapter._bot.edit_message_text.call_args.kwargs
        assert call_kwargs["message_id"] == 99

    @pytest.mark.asyncio
    async def test_edit_message_truncates_long_text(self):
        adapter = make_adapter()
        adapter._bot.edit_message_text = AsyncMock()

        handle = MessageHandle(
            platform="telegram",
            channel_id="telegram:200",
            message_id="42",
        )
        long_text = "x" * 5000
        await adapter.edit_message(handle, long_text)

        call_kwargs = adapter._bot.edit_message_text.call_args.kwargs
        assert len(call_kwargs["text"]) == 4096

    @pytest.mark.asyncio
    async def test_edit_message_not_modified_ignored(self):
        """BadRequest 'not modified' should be silently ignored."""
        from telegram.error import BadRequest

        adapter = make_adapter()
        adapter._bot.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified"))

        handle = MessageHandle(
            platform="telegram",
            channel_id="telegram:200",
            message_id="42",
        )
        # Should not raise
        await adapter.edit_message(handle, "same text")

    @pytest.mark.asyncio
    async def test_edit_message_bad_html_falls_back(self):
        """BadRequest with other reasons should try plain text fallback."""
        from telegram.error import BadRequest

        adapter = make_adapter()
        # First call fails, second (plain text) succeeds
        adapter._bot.edit_message_text = AsyncMock(side_effect=[BadRequest("can't parse entities"), None])

        handle = MessageHandle(
            platform="telegram",
            channel_id="telegram:200",
            message_id="42",
        )
        await adapter.edit_message(handle, "<bad html>")

        assert adapter._bot.edit_message_text.call_count == 2

    @pytest.mark.asyncio
    async def test_edit_no_bot_noop(self):
        adapter = TelegramAdapter(make_config())
        handle = MessageHandle(
            platform="telegram",
            channel_id="telegram:200",
            message_id="42",
        )
        # Should not raise
        await adapter.edit_message(handle, "text")

    @pytest.mark.asyncio
    async def test_edit_invalid_message_id_noop(self):
        adapter = make_adapter()
        handle = MessageHandle(
            platform="telegram",
            channel_id="telegram:200",
            message_id="not-a-number",
        )
        # Should not raise or call API
        await adapter.edit_message(handle, "text")
        adapter._bot.edit_message_text.assert_not_called()


# ─── Tests: Delete Message ───


class TestDeleteMessage:
    @pytest.mark.asyncio
    async def test_delete_message(self):
        adapter = make_adapter()
        adapter._bot.delete_message = AsyncMock()

        handle = MessageHandle(
            platform="telegram",
            channel_id="telegram:200",
            message_id="42",
        )
        await adapter.delete_message(handle)

        adapter._bot.delete_message.assert_called_once_with(chat_id=200, message_id=42)

    @pytest.mark.asyncio
    async def test_delete_no_bot_noop(self):
        adapter = TelegramAdapter(make_config())
        handle = MessageHandle(
            platform="telegram",
            channel_id="telegram:200",
            message_id="42",
        )
        await adapter.delete_message(handle)  # Should not raise


# ─── Tests: File Operations ───


class TestFileOperations:
    @pytest.mark.asyncio
    async def test_download_file(self):
        adapter = make_adapter()
        tg_file = AsyncMock()
        attachment = FileAttachment(
            filename="test.txt",
            size=100,
            mime_type="text/plain",
            url=None,
            platform_ref=tg_file,
        )
        dest = Path("/tmp/test.txt")
        result = await adapter.download_file(attachment, dest)

        tg_file.download_to_drive.assert_called_once_with(str(dest))
        assert result == dest

    @pytest.mark.asyncio
    async def test_download_file_too_large(self):
        adapter = make_adapter()
        attachment = FileAttachment(
            filename="big.bin",
            size=TELEGRAM_FILE_SIZE_LIMIT + 1,
            mime_type="application/octet-stream",
            url=None,
            platform_ref=MagicMock(),
        )
        with pytest.raises(ValueError, match="exceeds"):
            await adapter.download_file(attachment, Path("/tmp/big.bin"))

    @pytest.mark.asyncio
    async def test_download_file_no_ref(self):
        adapter = make_adapter()
        attachment = FileAttachment(
            filename="test.txt",
            size=100,
            mime_type="text/plain",
            url=None,
            platform_ref=None,
        )
        with pytest.raises(ValueError, match="No Telegram file reference"):
            await adapter.download_file(attachment, Path("/tmp/test.txt"))

    @pytest.mark.asyncio
    async def test_download_file_no_bot(self):
        adapter = TelegramAdapter(make_config())
        attachment = FileAttachment(
            filename="test.txt",
            size=100,
            mime_type="text/plain",
            url=None,
            platform_ref=MagicMock(),
        )
        with pytest.raises(RuntimeError, match="not initialized"):
            await adapter.download_file(attachment, Path("/tmp/test.txt"))

    @pytest.mark.asyncio
    async def test_send_file(self, tmp_path):
        adapter = make_adapter()
        msg = make_message(99)
        adapter._bot.send_document = AsyncMock(return_value=msg)

        test_file = tmp_path / "doc.pdf"
        test_file.write_text("content")

        handle = await adapter.send_file("telegram:200", test_file, "My doc")

        assert handle.platform == "telegram"
        assert handle.message_id == "99"
        adapter._bot.send_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_file_no_bot(self):
        adapter = TelegramAdapter(make_config())
        with pytest.raises(RuntimeError, match="not initialized"):
            await adapter.send_file("telegram:200", Path("/tmp/f.txt"))

    @pytest.mark.asyncio
    async def test_send_file_caption_truncated(self, tmp_path):
        adapter = make_adapter()
        msg = make_message(99)
        adapter._bot.send_document = AsyncMock(return_value=msg)

        test_file = tmp_path / "doc.pdf"
        test_file.write_text("content")

        long_caption = "x" * 2000
        await adapter.send_file("telegram:200", test_file, long_caption)

        call_kwargs = adapter._bot.send_document.call_args.kwargs
        assert len(call_kwargs["caption"]) <= 1024


# ─── Tests: Typing Indicator ───


class TestTypingIndicator:
    @pytest.mark.asyncio
    async def test_start_typing_creates_task(self):
        adapter = make_adapter()
        adapter._bot.send_chat_action = AsyncMock()

        await adapter.start_typing("telegram:200")

        assert "telegram:200" in adapter._typing_tasks
        task = adapter._typing_tasks["telegram:200"]
        assert not task.done()

        # Cleanup
        await adapter.stop_typing("telegram:200")

    @pytest.mark.asyncio
    async def test_stop_typing_cancels_task(self):
        adapter = make_adapter()
        adapter._bot.send_chat_action = AsyncMock()

        await adapter.start_typing("telegram:200")
        task = adapter._typing_tasks["telegram:200"]

        await adapter.stop_typing("telegram:200")

        assert "telegram:200" not in adapter._typing_tasks
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_start_typing_replaces_existing(self):
        adapter = make_adapter()
        adapter._bot.send_chat_action = AsyncMock()

        await adapter.start_typing("telegram:200")
        first_task = adapter._typing_tasks["telegram:200"]

        await adapter.start_typing("telegram:200")
        second_task = adapter._typing_tasks["telegram:200"]

        assert first_task != second_task
        # Give the event loop a chance to process the cancellation
        await asyncio.sleep(0)
        assert first_task.cancelled() or first_task.done()

        # Cleanup
        await adapter.stop_typing("telegram:200")

    @pytest.mark.asyncio
    async def test_stop_typing_nonexistent_noop(self):
        adapter = make_adapter()
        # Should not raise
        await adapter.stop_typing("telegram:999")


# ─── Tests: Input Handler ───


class TestInputHandler:
    def test_set_input_handler(self):
        adapter = TelegramAdapter(make_config())
        handler = AsyncMock()
        adapter.set_input_handler(handler)
        assert adapter._on_input is handler


# ─── Tests: Command Handling ───


class TestCommandHandling:
    @pytest.mark.asyncio
    async def test_command_dispatched_to_handler(self):
        adapter = TelegramAdapter(make_config())
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        update = make_update(text="/help", user_id=100, chat_id=200)
        context = MagicMock()

        await adapter._handle_telegram_command(update, context)

        handler.assert_called_once_with("telegram:200", "/help", 100, None)

    @pytest.mark.asyncio
    async def test_command_strips_bot_mention(self):
        adapter = TelegramAdapter(make_config())
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        update = make_update(text="/help@MyBot", user_id=100, chat_id=200)
        context = MagicMock()

        await adapter._handle_telegram_command(update, context)

        handler.assert_called_once_with("telegram:200", "/help", 100, None)

    @pytest.mark.asyncio
    async def test_command_maps_underscores_to_hyphens(self):
        adapter = TelegramAdapter(make_config())
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        update = make_update(text="/add_machine", user_id=100, chat_id=200)
        context = MagicMock()

        await adapter._handle_telegram_command(update, context)

        handler.assert_called_once_with("telegram:200", "/add-machine", 100, None)

    @pytest.mark.asyncio
    async def test_command_remove_machine_mapped(self):
        adapter = TelegramAdapter(make_config())
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        update = make_update(text="/remove_machine", user_id=100, chat_id=200)
        context = MagicMock()

        await adapter._handle_telegram_command(update, context)

        handler.assert_called_once_with("telegram:200", "/remove-machine", 100, None)

    @pytest.mark.asyncio
    async def test_command_blocked_user_ignored(self):
        adapter = TelegramAdapter(make_config(allowed_users=[100]))
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        update = make_update(text="/help", user_id=999, chat_id=200)
        context = MagicMock()

        await adapter._handle_telegram_command(update, context)

        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_command_no_message_ignored(self):
        adapter = TelegramAdapter(make_config())
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        update = MagicMock()
        update.message = None
        context = MagicMock()

        await adapter._handle_telegram_command(update, context)

        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_command_no_text_ignored(self):
        adapter = TelegramAdapter(make_config())
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        update = MagicMock()
        update.message = MagicMock()
        update.message.text = None
        context = MagicMock()

        await adapter._handle_telegram_command(update, context)

        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_command_group_chat_filtering(self):
        """Negative chat IDs (groups) should be filtered by allowed_chats."""
        adapter = TelegramAdapter(make_config(allowed_chats=[-100111]))
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        # Allowed group
        update = make_update(text="/help", user_id=100, chat_id=-100111)
        await adapter._handle_telegram_command(update, MagicMock())
        handler.assert_called_once()

        handler.reset_mock()

        # Blocked group
        update = make_update(text="/help", user_id=100, chat_id=-100999)
        await adapter._handle_telegram_command(update, MagicMock())
        handler.assert_not_called()


# ─── Tests: Message Handling ───


class TestMessageHandling:
    @pytest.mark.asyncio
    async def test_text_message_dispatched(self):
        adapter = TelegramAdapter(make_config())
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        update = make_update(text="Hello Claude", user_id=100, chat_id=200)
        context = MagicMock()

        await adapter._handle_telegram_message(update, context)

        handler.assert_called_once_with("telegram:200", "Hello Claude", 100, None)

    @pytest.mark.asyncio
    async def test_message_blocked_user_ignored(self):
        adapter = TelegramAdapter(make_config(allowed_users=[100]))
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        update = make_update(text="Hello", user_id=999, chat_id=200)
        context = MagicMock()

        await adapter._handle_telegram_message(update, context)

        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_no_handler_noop(self):
        adapter = TelegramAdapter(make_config())
        # No handler set

        update = make_update(text="Hello", user_id=100, chat_id=200)
        context = MagicMock()

        # Should not raise
        await adapter._handle_telegram_message(update, context)

    @pytest.mark.asyncio
    async def test_message_no_user_ignored(self):
        adapter = TelegramAdapter(make_config())
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        update = MagicMock()
        update.message = MagicMock()
        update.message.text = "Hello"
        update.effective_user = None
        context = MagicMock()

        await adapter._handle_telegram_message(update, context)

        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_group_chat_filtering(self):
        adapter = TelegramAdapter(make_config(allowed_chats=[-100111]))
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        # Blocked group
        update = make_update(text="Hello", user_id=100, chat_id=-100999)
        await adapter._handle_telegram_message(update, MagicMock())
        handler.assert_not_called()


# ─── Tests: Lifecycle ───


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_stop_cancels_typing_tasks(self):
        adapter = make_adapter()
        adapter._bot.send_chat_action = AsyncMock()

        await adapter.start_typing("telegram:100")
        await adapter.start_typing("telegram:200")
        assert len(adapter._typing_tasks) == 2

        # Mock app for stop
        adapter._app = AsyncMock()
        adapter._app.updater = AsyncMock()

        await adapter.stop()

        assert len(adapter._typing_tasks) == 0

    @pytest.mark.asyncio
    async def test_stop_no_app_noop(self):
        adapter = TelegramAdapter(make_config())
        # _app is None
        await adapter.stop()  # Should not raise


# ─── Tests: Format Utils (markdown_to_telegram_html) ───


class TestMarkdownToTelegramHtml:
    def test_plain_text(self):
        assert markdown_to_telegram_html("Hello world") == "Hello world"

    def test_html_entities_escaped(self):
        result = markdown_to_telegram_html("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_bold(self):
        result = markdown_to_telegram_html("**bold text**")
        assert "<b>bold text</b>" in result

    def test_italic(self):
        result = markdown_to_telegram_html("*italic text*")
        assert "<i>italic text</i>" in result

    def test_inline_code(self):
        result = markdown_to_telegram_html("`code here`")
        assert "<code>code here</code>" in result

    def test_code_block(self):
        result = markdown_to_telegram_html("```python\nprint('hi')\n```")
        assert "<pre>" in result
        assert "print" in result

    def test_code_block_no_lang(self):
        result = markdown_to_telegram_html("```\nhello\n```")
        assert "<pre>" in result

    def test_strikethrough(self):
        result = markdown_to_telegram_html("~~deleted~~")
        assert "<s>deleted</s>" in result

    def test_bold_and_italic_together(self):
        result = markdown_to_telegram_html("**bold** and *italic*")
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result

    def test_empty_string(self):
        assert markdown_to_telegram_html("") == ""

    def test_ampersand_escaped(self):
        result = markdown_to_telegram_html("a & b")
        assert "&amp;" in result

    def test_nested_formatting(self):
        result = markdown_to_telegram_html("**bold with `code`**")
        assert "<b>" in result
        assert "<code>" in result


# ─── Tests: Protocol Compliance ───


class TestProtocolCompliance:
    def test_adapter_satisfies_protocol(self):
        """TelegramAdapter should satisfy PlatformAdapter protocol."""
        from head.platform.protocol import PlatformAdapter

        adapter = TelegramAdapter(make_config())
        assert isinstance(adapter, PlatformAdapter)
