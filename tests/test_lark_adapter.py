"""Tests for the Lark (Feishu) platform adapter."""

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest

# Mock lark_oapi before importing the adapter
lark_oapi_mock = MagicMock()
sys.modules["lark_oapi"] = lark_oapi_mock
sys.modules["lark_oapi.api"] = MagicMock()
sys.modules["lark_oapi.api.im"] = MagicMock()
sys.modules["lark_oapi.api.im.v1"] = MagicMock()
sys.modules["lark_oapi.ws"] = MagicMock()

from head.platform.lark_adapter import (  # noqa: E402
    LarkAdapter,
    markdown_to_lark_post,
    _parse_inline_markdown,
)
from head.platform.protocol import MessageHandle, FileAttachment  # noqa: E402
from head.config import LarkConfig  # noqa: E402


def _make_config(**overrides) -> LarkConfig:
    defaults = {
        "app_id": "cli_test123",
        "app_secret": "secret_test456",
        "allowed_chats": [],
        "admin_users": [],
        "use_cards": True,
    }
    defaults.update(overrides)
    return LarkConfig(**defaults)


def _make_adapter(**config_overrides) -> LarkAdapter:
    config = _make_config(**config_overrides)
    return LarkAdapter(config)


def _mock_success_response(message_id="msg_001", **extra):
    resp = MagicMock()
    resp.success.return_value = True
    resp.data.message_id = message_id
    resp.msg = "ok"
    for k, v in extra.items():
        setattr(resp.data, k, v)
    return resp


def _mock_failure_response(msg="error occurred"):
    resp = MagicMock()
    resp.success.return_value = False
    resp.msg = msg
    return resp


# ---------------------------------------------------------------------------
# TestMarkdownToLarkPost
# ---------------------------------------------------------------------------


class TestMarkdownToLarkPost:
    """Tests for markdown_to_lark_post conversion."""

    def test_plain_text(self):
        result = markdown_to_lark_post("Hello world")
        assert result["zh_cn"]["title"] == ""
        content = result["zh_cn"]["content"]
        assert len(content) == 1
        assert content[0] == [{"tag": "text", "text": "Hello world"}]

    def test_bold_text(self):
        result = markdown_to_lark_post("This is **bold** text")
        tags = result["zh_cn"]["content"][0]
        assert tags[0] == {"tag": "text", "text": "This is "}
        assert tags[1] == {"tag": "text", "text": "bold", "style": ["bold"]}
        assert tags[2] == {"tag": "text", "text": " text"}

    def test_inline_code(self):
        result = markdown_to_lark_post("Use `print()` here")
        tags = result["zh_cn"]["content"][0]
        assert tags[0] == {"tag": "text", "text": "Use "}
        assert tags[1] == {"tag": "text", "text": "print()", "style": ["code_block"]}
        assert tags[2] == {"tag": "text", "text": " here"}

    def test_link(self):
        result = markdown_to_lark_post("Visit [Google](https://google.com) now")
        tags = result["zh_cn"]["content"][0]
        assert tags[0] == {"tag": "text", "text": "Visit "}
        assert tags[1] == {"tag": "a", "text": "Google", "href": "https://google.com"}
        assert tags[2] == {"tag": "text", "text": " now"}

    def test_code_block(self):
        text = "```python\nprint('hello')\nprint('world')\n```"
        result = markdown_to_lark_post(text)
        content = result["zh_cn"]["content"]
        # Code block lines should each have code_block style
        code_lines = [
            row for row in content
            if len(row) == 1 and row[0].get("style") == ["code_block"]
        ]
        assert len(code_lines) == 2
        assert code_lines[0][0]["text"] == "print('hello')"
        assert code_lines[1][0]["text"] == "print('world')"

    def test_mixed_content(self):
        text = "**bold** and `code` and [link](http://x.com)"
        result = markdown_to_lark_post(text)
        tags = result["zh_cn"]["content"][0]
        assert tags[0] == {"tag": "text", "text": "bold", "style": ["bold"]}
        assert tags[1] == {"tag": "text", "text": " and "}
        assert tags[2] == {"tag": "text", "text": "code", "style": ["code_block"]}
        assert tags[3] == {"tag": "text", "text": " and "}
        assert tags[4] == {"tag": "a", "text": "link", "href": "http://x.com"}

    def test_multiple_lines(self):
        text = "Line one\nLine two\nLine three"
        result = markdown_to_lark_post(text)
        content = result["zh_cn"]["content"]
        assert len(content) == 3
        assert content[0][0]["text"] == "Line one"
        assert content[1][0]["text"] == "Line two"
        assert content[2][0]["text"] == "Line three"

    def test_empty_text(self):
        result = markdown_to_lark_post("")
        content = result["zh_cn"]["content"]
        # Empty string produces one empty-line entry
        assert len(content) == 1
        assert content[0] == [{"tag": "text", "text": ""}]

    def test_empty_lines_between_content(self):
        text = "Above\n\nBelow"
        result = markdown_to_lark_post(text)
        content = result["zh_cn"]["content"]
        assert len(content) == 3
        assert content[1] == [{"tag": "text", "text": ""}]

    def test_returns_correct_structure(self):
        result = markdown_to_lark_post("test")
        assert "zh_cn" in result
        assert "title" in result["zh_cn"]
        assert "content" in result["zh_cn"]
        assert isinstance(result["zh_cn"]["content"], list)

    def test_only_bold(self):
        result = markdown_to_lark_post("**all bold**")
        tags = result["zh_cn"]["content"][0]
        assert len(tags) == 1
        assert tags[0] == {"tag": "text", "text": "all bold", "style": ["bold"]}

    def test_multiple_bolds(self):
        result = markdown_to_lark_post("**a** and **b**")
        tags = result["zh_cn"]["content"][0]
        assert tags[0] == {"tag": "text", "text": "a", "style": ["bold"]}
        assert tags[1] == {"tag": "text", "text": " and "}
        assert tags[2] == {"tag": "text", "text": "b", "style": ["bold"]}


# ---------------------------------------------------------------------------
# TestParseInlineMarkdown
# ---------------------------------------------------------------------------


class TestParseInlineMarkdown:
    """Tests for _parse_inline_markdown helper."""

    def test_plain_text_only(self):
        tags = _parse_inline_markdown("no formatting here")
        assert len(tags) == 1
        assert tags[0] == {"tag": "text", "text": "no formatting here"}

    def test_bold_only(self):
        tags = _parse_inline_markdown("**bold**")
        assert tags == [{"tag": "text", "text": "bold", "style": ["bold"]}]

    def test_code_only(self):
        tags = _parse_inline_markdown("`code`")
        assert tags == [{"tag": "text", "text": "code", "style": ["code_block"]}]

    def test_link_only(self):
        tags = _parse_inline_markdown("[click](http://example.com)")
        assert tags == [{"tag": "a", "text": "click", "href": "http://example.com"}]

    def test_empty_string(self):
        tags = _parse_inline_markdown("")
        assert tags == []

    def test_trailing_text_after_match(self):
        tags = _parse_inline_markdown("**x** end")
        assert len(tags) == 2
        assert tags[0] == {"tag": "text", "text": "x", "style": ["bold"]}
        assert tags[1] == {"tag": "text", "text": " end"}

    def test_leading_text_before_match(self):
        tags = _parse_inline_markdown("start **x**")
        assert len(tags) == 2
        assert tags[0] == {"tag": "text", "text": "start "}
        assert tags[1] == {"tag": "text", "text": "x", "style": ["bold"]}


# ---------------------------------------------------------------------------
# TestLarkAdapterBasic
# ---------------------------------------------------------------------------


class TestLarkAdapterBasic:
    """Tests for basic adapter properties and helpers."""

    def test_platform_name(self):
        adapter = _make_adapter()
        assert adapter.platform_name == "lark"

    def test_max_message_length(self):
        adapter = _make_adapter()
        assert adapter.max_message_length == 30000

    def test_supports_message_edit(self):
        adapter = _make_adapter()
        assert adapter.supports_message_edit() is True

    def test_supports_inline_buttons(self):
        adapter = _make_adapter()
        assert adapter.supports_inline_buttons() is True

    def test_supports_file_upload(self):
        adapter = _make_adapter()
        assert adapter.supports_file_upload() is True

    @pytest.mark.asyncio
    async def test_start_typing_noop(self):
        adapter = _make_adapter()
        # Should not raise
        await adapter.start_typing("lark:chat_123")

    @pytest.mark.asyncio
    async def test_stop_typing_noop(self):
        adapter = _make_adapter()
        await adapter.stop_typing("lark:chat_123")

    def test_set_input_handler(self):
        adapter = _make_adapter()
        handler = AsyncMock()
        adapter.set_input_handler(handler)
        assert adapter._on_input is handler

    def test_channel_id(self):
        adapter = _make_adapter()
        assert adapter._channel_id("oc_abc123") == "lark:oc_abc123"

    def test_chat_id_from_channel(self):
        adapter = _make_adapter()
        assert adapter._chat_id_from_channel("lark:oc_abc123") == "oc_abc123"

    def test_chat_id_from_channel_colon_in_id(self):
        adapter = _make_adapter()
        assert adapter._chat_id_from_channel("lark:oc:special") == "oc:special"

    def test_is_allowed_chat_empty_allows_all(self):
        adapter = _make_adapter(allowed_chats=[])
        assert adapter._is_allowed_chat("any_chat") is True

    def test_is_allowed_chat_match(self):
        adapter = _make_adapter(allowed_chats=["oc_abc"])
        assert adapter._is_allowed_chat("oc_abc") is True

    def test_is_allowed_chat_no_match(self):
        adapter = _make_adapter(allowed_chats=["oc_abc"])
        assert adapter._is_allowed_chat("oc_other") is False


# ---------------------------------------------------------------------------
# TestLarkEventHandling
# ---------------------------------------------------------------------------


class TestLarkEventHandling:
    """Tests for _handle_message_event and _extract_attachments."""

    def _make_event_data(
        self,
        chat_id="oc_test",
        sender_type="user",
        open_id="ou_user1",
        content_json='{"text": "hello"}',
        message_type="text",
        message_id="msg_100",
    ):
        data = MagicMock()
        data.event.message.chat_id = chat_id
        data.event.message.content = content_json
        data.event.message.message_type = message_type
        data.event.message.message_id = message_id
        data.event.sender.sender_type = sender_type
        data.event.sender.sender_id.open_id = open_id
        return data

    def test_ignores_bot_messages(self):
        adapter = _make_adapter()
        handler = AsyncMock()
        adapter.set_input_handler(handler)
        data = self._make_event_data(sender_type="app")
        adapter._handle_message_event(data)
        handler.assert_not_called()

    def test_filters_by_allowed_chats(self):
        adapter = _make_adapter(allowed_chats=["oc_allowed"])
        handler = AsyncMock()
        adapter.set_input_handler(handler)
        data = self._make_event_data(chat_id="oc_blocked")
        adapter._handle_message_event(data)
        handler.assert_not_called()

    @patch("head.platform.lark_adapter.asyncio.run_coroutine_threadsafe")
    @patch("head.platform.lark_adapter.asyncio.get_event_loop")
    def test_dispatches_to_handler(self, mock_get_loop, mock_run_coro):
        mock_loop = MagicMock()
        mock_get_loop.return_value = mock_loop

        adapter = _make_adapter()
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        data = self._make_event_data(
            chat_id="oc_test",
            content_json='{"text": "/start machine ~/proj"}',
            open_id="ou_user1",
        )
        adapter._handle_message_event(data)

        mock_run_coro.assert_called_once()
        call_args = mock_run_coro.call_args
        # Second positional arg is the loop
        assert call_args[0][1] is mock_loop

    @patch("head.platform.lark_adapter.asyncio.run_coroutine_threadsafe")
    @patch("head.platform.lark_adapter.asyncio.get_event_loop")
    def test_extracts_sender_id(self, mock_get_loop, mock_run_coro):
        mock_loop = MagicMock()
        mock_get_loop.return_value = mock_loop

        adapter = _make_adapter()
        handler = AsyncMock()
        adapter.set_input_handler(handler)

        data = self._make_event_data(open_id="ou_user42")
        adapter._handle_message_event(data)

        # Verify the coroutine was scheduled (sender_id passed through)
        assert mock_run_coro.called

    def test_ignores_empty_text(self):
        adapter = _make_adapter()
        handler = AsyncMock()
        adapter.set_input_handler(handler)
        data = self._make_event_data(content_json='{"text": ""}')
        adapter._handle_message_event(data)
        handler.assert_not_called()

    def test_ignores_invalid_json(self):
        adapter = _make_adapter()
        handler = AsyncMock()
        adapter.set_input_handler(handler)
        data = self._make_event_data(content_json="not json")
        adapter._handle_message_event(data)
        handler.assert_not_called()

    def test_no_handler_set(self):
        adapter = _make_adapter()
        # No handler set - should not raise
        data = self._make_event_data()
        adapter._handle_message_event(data)

    def test_exception_in_handler_does_not_raise(self):
        adapter = _make_adapter()
        # Simulate an event that will cause AttributeError
        data = MagicMock()
        data.event.message.chat_id = "oc_test"
        data.event.sender.sender_type = "user"
        data.event.sender.sender_id = None  # will cause error accessing open_id
        data.event.message.content = '{"text": "hi"}'
        # Should not raise
        adapter._handle_message_event(data)

    # --- _extract_attachments ---

    def test_extract_image_attachment(self):
        adapter = _make_adapter()
        msg = MagicMock()
        msg.message_type = "image"
        msg.message_id = "msg_img_1"
        msg.content = json.dumps({"image_key": "img_key_abc"})

        attachments = adapter._extract_attachments(msg)
        assert len(attachments) == 1
        att = attachments[0]
        assert att.filename == "img_key_abc.png"
        assert att.mime_type == "image/png"
        assert att.platform_ref["message_id"] == "msg_img_1"
        assert att.platform_ref["file_key"] == "img_key_abc"
        assert att.platform_ref["type"] == "image"

    def test_extract_file_attachment(self):
        adapter = _make_adapter()
        msg = MagicMock()
        msg.message_type = "file"
        msg.message_id = "msg_file_1"
        msg.content = json.dumps({"file_key": "file_key_xyz", "file_name": "report.pdf"})

        attachments = adapter._extract_attachments(msg)
        assert len(attachments) == 1
        att = attachments[0]
        assert att.filename == "report.pdf"
        assert att.mime_type is None
        assert att.platform_ref["file_key"] == "file_key_xyz"
        assert att.platform_ref["type"] == "file"

    def test_extract_text_message_no_attachments(self):
        adapter = _make_adapter()
        msg = MagicMock()
        msg.message_type = "text"
        attachments = adapter._extract_attachments(msg)
        assert attachments == []

    def test_extract_file_no_file_key(self):
        adapter = _make_adapter()
        msg = MagicMock()
        msg.message_type = "file"
        msg.message_id = "msg_1"
        msg.content = json.dumps({"file_name": "test.txt"})

        attachments = adapter._extract_attachments(msg)
        assert attachments == []

    def test_extract_image_no_image_key(self):
        adapter = _make_adapter()
        msg = MagicMock()
        msg.message_type = "image"
        msg.message_id = "msg_1"
        msg.content = json.dumps({})

        attachments = adapter._extract_attachments(msg)
        assert attachments == []


# ---------------------------------------------------------------------------
# TestLarkAdapterSendMessage
# ---------------------------------------------------------------------------


class TestLarkAdapterSendMessage:
    """Tests for send_message, edit_message, delete_message."""

    @pytest.mark.asyncio
    async def test_send_message_no_client(self):
        adapter = _make_adapter()
        # _client is None by default
        handle = await adapter.send_message("lark:oc_chat", "Hello")
        assert handle.platform == "lark"
        assert handle.message_id == "0"

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        resp = _mock_success_response(message_id="msg_sent_1")
        mock_client.im.v1.message.create.return_value = resp

        handle = await adapter.send_message("lark:oc_chat1", "Hello there")
        assert handle.platform == "lark"
        assert handle.message_id == "msg_sent_1"
        assert handle.channel_id == "lark:oc_chat1"
        mock_client.im.v1.message.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_failure(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        resp = _mock_failure_response("rate limited")
        mock_client.im.v1.message.create.return_value = resp

        handle = await adapter.send_message("lark:oc_chat1", "Hello")
        # On failure, still returns handle with message_id "0" (last_handle default)
        assert handle.message_id == "0"

    @pytest.mark.asyncio
    async def test_send_message_exception(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client
        mock_client.im.v1.message.create.side_effect = Exception("network error")

        handle = await adapter.send_message("lark:oc_chat1", "Hello")
        assert handle.message_id == "0"

    @pytest.mark.asyncio
    async def test_edit_message_no_client(self):
        adapter = _make_adapter()
        handle = MessageHandle(platform="lark", channel_id="lark:oc_1", message_id="m1")
        # Should not raise
        await adapter.edit_message(handle, "Updated")

    @pytest.mark.asyncio
    async def test_edit_message_success(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        resp = _mock_success_response()
        mock_client.im.v1.message.patch.return_value = resp

        handle = MessageHandle(platform="lark", channel_id="lark:oc_1", message_id="msg_edit")
        await adapter.edit_message(handle, "Updated text")
        mock_client.im.v1.message.patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_message_failure(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        resp = _mock_failure_response("not found")
        mock_client.im.v1.message.patch.return_value = resp

        handle = MessageHandle(platform="lark", channel_id="lark:oc_1", message_id="msg_edit")
        # Should not raise, just logs warning
        await adapter.edit_message(handle, "Updated text")

    @pytest.mark.asyncio
    async def test_edit_message_exception(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client
        mock_client.im.v1.message.patch.side_effect = Exception("oops")

        handle = MessageHandle(platform="lark", channel_id="lark:oc_1", message_id="m1")
        # Should not raise
        await adapter.edit_message(handle, "Updated")

    @pytest.mark.asyncio
    async def test_delete_message_no_client(self):
        adapter = _make_adapter()
        handle = MessageHandle(platform="lark", channel_id="lark:oc_1", message_id="m1")
        await adapter.delete_message(handle)

    @pytest.mark.asyncio
    async def test_delete_message_success(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        resp = _mock_success_response()
        mock_client.im.v1.message.delete.return_value = resp

        handle = MessageHandle(platform="lark", channel_id="lark:oc_1", message_id="msg_del")
        await adapter.delete_message(handle)
        mock_client.im.v1.message.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_message_failure(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        resp = _mock_failure_response("forbidden")
        mock_client.im.v1.message.delete.return_value = resp

        handle = MessageHandle(platform="lark", channel_id="lark:oc_1", message_id="msg_del")
        # Should not raise
        await adapter.delete_message(handle)

    @pytest.mark.asyncio
    async def test_delete_message_exception(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client
        mock_client.im.v1.message.delete.side_effect = Exception("err")

        handle = MessageHandle(platform="lark", channel_id="lark:oc_1", message_id="m1")
        await adapter.delete_message(handle)


# ---------------------------------------------------------------------------
# TestLarkAdapterFileOps
# ---------------------------------------------------------------------------


class TestLarkAdapterFileOps:
    """Tests for send_file and download_file."""

    @pytest.mark.asyncio
    async def test_send_file_no_client(self):
        adapter = _make_adapter()
        with pytest.raises(RuntimeError, match="not initialized"):
            await adapter.send_file("lark:oc_1", Path("/tmp/test.txt"))

    @pytest.mark.asyncio
    async def test_send_file_image(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        # Image upload response
        upload_resp = MagicMock()
        upload_resp.success.return_value = True
        upload_resp.data.image_key = "img_uploaded_key"
        mock_client.im.v1.image.create.return_value = upload_resp

        # Message send response
        send_resp = _mock_success_response(message_id="msg_img_sent")
        mock_client.im.v1.message.create.return_value = send_resp

        with patch("builtins.open", mock_open(read_data=b"\x89PNG")):
            handle = await adapter.send_file("lark:oc_1", Path("/tmp/photo.png"))

        assert handle.message_id == "msg_img_sent"
        mock_client.im.v1.image.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_file_regular(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        # File upload response
        upload_resp = MagicMock()
        upload_resp.success.return_value = True
        upload_resp.data.file_key = "file_uploaded_key"
        mock_client.im.v1.file.create.return_value = upload_resp

        # Message send response
        send_resp = _mock_success_response(message_id="msg_file_sent")
        mock_client.im.v1.message.create.return_value = send_resp

        with patch("builtins.open", mock_open(read_data=b"data")):
            handle = await adapter.send_file("lark:oc_1", Path("/tmp/report.pdf"))

        assert handle.message_id == "msg_file_sent"
        mock_client.im.v1.file.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_file_image_upload_fails(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        upload_resp = _mock_failure_response("too large")
        mock_client.im.v1.image.create.return_value = upload_resp

        with patch("builtins.open", mock_open(read_data=b"\x89PNG")):
            with pytest.raises(RuntimeError, match="Image upload failed"):
                await adapter.send_file("lark:oc_1", Path("/tmp/big.jpg"))

    @pytest.mark.asyncio
    async def test_send_file_file_upload_fails(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        upload_resp = _mock_failure_response("quota exceeded")
        mock_client.im.v1.file.create.return_value = upload_resp

        with patch("builtins.open", mock_open(read_data=b"data")):
            with pytest.raises(RuntimeError, match="File upload failed"):
                await adapter.send_file("lark:oc_1", Path("/tmp/doc.pdf"))

    @pytest.mark.asyncio
    async def test_send_file_send_message_fails(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        upload_resp = MagicMock()
        upload_resp.success.return_value = True
        upload_resp.data.file_key = "fk_1"
        mock_client.im.v1.file.create.return_value = upload_resp

        send_resp = _mock_failure_response("send error")
        mock_client.im.v1.message.create.return_value = send_resp

        with patch("builtins.open", mock_open(read_data=b"data")):
            with pytest.raises(RuntimeError, match="Failed to send file message"):
                await adapter.send_file("lark:oc_1", Path("/tmp/doc.txt"))

    @pytest.mark.asyncio
    async def test_send_file_image_types_detected(self):
        """Verify all image extensions are detected as images."""
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        upload_resp = MagicMock()
        upload_resp.success.return_value = True
        upload_resp.data.image_key = "ik"
        mock_client.im.v1.image.create.return_value = upload_resp

        send_resp = _mock_success_response()
        mock_client.im.v1.message.create.return_value = send_resp

        for ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]:
            mock_client.im.v1.image.create.reset_mock()
            with patch("builtins.open", mock_open(read_data=b"img")):
                await adapter.send_file("lark:oc_1", Path(f"/tmp/pic{ext}"))
            mock_client.im.v1.image.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_download_file_no_client(self):
        adapter = _make_adapter()
        att = FileAttachment(
            filename="f.txt", size=0, mime_type=None, url=None,
            platform_ref={"message_id": "m1", "file_key": "fk1", "type": "file"},
        )
        with pytest.raises(RuntimeError, match="not initialized"):
            await adapter.download_file(att, Path("/tmp/f.txt"))

    @pytest.mark.asyncio
    async def test_download_file_success(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        resp = MagicMock()
        resp.success.return_value = True
        resp.file.read.return_value = b"file contents"
        mock_client.im.v1.message_resource.get.return_value = resp

        att = FileAttachment(
            filename="f.txt", size=0, mime_type=None, url=None,
            platform_ref={"message_id": "m1", "file_key": "fk1", "type": "file"},
        )
        dest = MagicMock(spec=Path)
        result = await adapter.download_file(att, dest)
        dest.write_bytes.assert_called_once_with(b"file contents")
        assert result is dest

    @pytest.mark.asyncio
    async def test_download_file_failure(self):
        adapter = _make_adapter()
        mock_client = MagicMock()
        adapter._client = mock_client

        resp = _mock_failure_response("not found")
        mock_client.im.v1.message_resource.get.return_value = resp

        att = FileAttachment(
            filename="f.txt", size=0, mime_type=None, url=None,
            platform_ref={"message_id": "m1", "file_key": "fk1", "type": "file"},
        )
        with pytest.raises(RuntimeError, match="Failed to download"):
            await adapter.download_file(att, Path("/tmp/f.txt"))
