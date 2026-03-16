"""
Tests for file transfer integration in engine.py (BotEngine).

Tests _upload_and_replace_files() and _forward_message() with file_refs parameter.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Optional
from pathlib import Path

from head.engine import BotEngine
from head.platform.protocol import MessageHandle, InputHandler
from head.config import Config, MachineConfig, FilePoolConfig
from head.session_router import SessionRouter
from head.daemon_client import DaemonClient
from head.file_pool import FileEntry


# ─── MockAdapter + MockBotEngine for testing ───


class MockAdapter:
    """Mock PlatformAdapter that records sent/edited messages."""

    def __init__(self):
        self.sent_messages: list[tuple[str, str]] = []
        self.edited_messages: list[tuple[str, str]] = []
        self._msg_counter = 0
        self._on_input: Optional[InputHandler] = None

    @property
    def platform_name(self) -> str:
        return "discord"

    @property
    def max_message_length(self) -> int:
        return 2000

    async def send_message(self, channel_id: str, text: str) -> MessageHandle:
        self._msg_counter += 1
        self.sent_messages.append((channel_id, text))
        return MessageHandle(platform="discord", channel_id=channel_id, message_id=f"msg-{self._msg_counter}")

    async def edit_message(self, handle: MessageHandle, text: str) -> None:
        self.edited_messages.append((handle.message_id, text))

    async def delete_message(self, handle: MessageHandle) -> None:
        pass

    async def download_file(self, attachment, dest: Path) -> Path:
        return dest

    async def send_file(self, channel_id: str, path: Path, caption: str = "") -> MessageHandle:
        return MessageHandle(platform="discord", channel_id=channel_id, message_id="file-1")

    async def start_typing(self, channel_id: str) -> None:
        pass

    async def stop_typing(self, channel_id: str) -> None:
        pass

    def supports_message_edit(self) -> bool:
        return True

    def supports_inline_buttons(self) -> bool:
        return False

    def supports_file_upload(self) -> bool:
        return True

    def set_input_handler(self, handler: InputHandler) -> None:
        self._on_input = handler

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class MockBotEngine(BotEngine):
    """BotEngine with convenience test accessors."""

    def __init__(self, adapter, ssh_manager, session_router, daemon_client, config):
        super().__init__(adapter, ssh_manager, session_router, daemon_client, config)
        self.adapter: MockAdapter

    @property
    def sent_messages(self) -> list[tuple[str, str]]:
        return self.adapter.sent_messages

    def get_last_message(self) -> Optional[str]:
        if self.adapter.sent_messages:
            return self.adapter.sent_messages[-1][1]
        return None


# ─── Fixtures ───


@pytest.fixture
def mock_ssh():
    ssh = AsyncMock()
    ssh.ensure_tunnel = AsyncMock(return_value=19100)
    ssh.upload_files = AsyncMock(return_value={})
    return ssh


@pytest.fixture
def mock_router(tmp_path):
    return SessionRouter(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def mock_daemon():
    daemon = AsyncMock(spec=DaemonClient)
    # send_message returns an async iterator
    daemon.send_message = MagicMock()
    return daemon


@pytest.fixture
def mock_config():
    config = Config()
    config.machines = {
        "gpu-1": MachineConfig(id="gpu-1", host="10.0.0.1", user="user"),
    }
    config.file_pool = FilePoolConfig(remote_dir="/tmp/remote-code/files")
    return config


@pytest.fixture
def bot(mock_ssh, mock_router, mock_daemon, mock_config):
    adapter = MockAdapter()
    engine = MockBotEngine(adapter, mock_ssh, mock_router, mock_daemon, mock_config)
    adapter.set_input_handler(engine.handle_input)
    return engine


@pytest.fixture
def file_entry(tmp_path):
    """Create a FileEntry with a real file."""
    f = tmp_path / "report.pdf"
    f.write_bytes(b"PDF content")
    return FileEntry(
        file_id="sess1234_abcd5678",
        original_name="report.pdf",
        local_path=f,
        size=11,
        mime_type="application/pdf",
        created_at=1000.0,
    )


@pytest.fixture
def registered_session(bot, mock_router):
    """Register a session for testing."""
    mock_router.register(
        "chan-1", "gpu-1", "/home/user/project", "daemon-sess-123", "auto"
    )
    return mock_router.resolve("chan-1")


# ─── _upload_and_replace_files Tests ───


class TestUploadAndReplaceFiles:
    @pytest.mark.asyncio
    async def test_no_file_refs_returns_text_unchanged(self, bot):
        result = await bot._upload_and_replace_files("gpu-1", "hello world")
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_none_file_refs_returns_text_unchanged(self, bot):
        result = await bot._upload_and_replace_files("gpu-1", "hello world", None)
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_empty_list_returns_text_unchanged(self, bot):
        result = await bot._upload_and_replace_files("gpu-1", "hello world", [])
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_single_file_replacement(self, bot, mock_ssh, file_entry):
        mock_ssh.upload_files = AsyncMock(return_value={
            "sess1234_abcd5678": "/tmp/remote-code/files/sess1234_abcd5678_report.pdf"
        })

        text = "Analyze this <discord_file>sess1234_abcd5678</discord_file>"
        result = await bot._upload_and_replace_files("gpu-1", text, [file_entry])

        assert result == "Analyze this /tmp/remote-code/files/sess1234_abcd5678_report.pdf"
        mock_ssh.upload_files.assert_called_once_with("gpu-1", [file_entry])

    @pytest.mark.asyncio
    async def test_multiple_file_replacements(self, bot, mock_ssh, tmp_path):
        f1 = tmp_path / "doc1.pdf"
        f1.write_bytes(b"a")
        f2 = tmp_path / "img.png"
        f2.write_bytes(b"b")

        entries = [
            FileEntry("id_1", "doc1.pdf", f1, 1, "application/pdf", 1000.0),
            FileEntry("id_2", "img.png", f2, 1, "image/png", 1001.0),
        ]

        mock_ssh.upload_files = AsyncMock(return_value={
            "id_1": "/tmp/files/id_1_doc1.pdf",
            "id_2": "/tmp/files/id_2_img.png",
        })

        text = "Look at <discord_file>id_1</discord_file> and <discord_file>id_2</discord_file>"
        result = await bot._upload_and_replace_files("gpu-1", text, entries)

        assert "/tmp/files/id_1_doc1.pdf" in result
        assert "/tmp/files/id_2_img.png" in result
        assert "<discord_file>" not in result

    @pytest.mark.asyncio
    async def test_upload_failure_propagates(self, bot, mock_ssh, file_entry):
        mock_ssh.upload_files = AsyncMock(side_effect=Exception("SCP failed"))

        text = "Analyze <discord_file>sess1234_abcd5678</discord_file>"
        with pytest.raises(Exception, match="SCP failed"):
            await bot._upload_and_replace_files("gpu-1", text, [file_entry])


# ─── _forward_message with file_refs Tests ───


class TestForwardMessageWithFiles:
    @pytest.mark.asyncio
    async def test_forward_with_files_uploads_before_sending(
        self, bot, mock_ssh, mock_daemon, registered_session, file_entry
    ):
        """Files should be uploaded and text replaced before sending to daemon."""
        mock_ssh.upload_files = AsyncMock(return_value={
            "sess1234_abcd5678": "/tmp/remote/sess1234_abcd5678_report.pdf"
        })

        # Make send_message return an empty async iterator
        async def empty_stream(*args, **kwargs):
            return
            yield  # make it an async generator

        mock_daemon.send_message = MagicMock(return_value=empty_stream())

        text = "Analyze <discord_file>sess1234_abcd5678</discord_file>"
        await bot._forward_message("chan-1", text, file_refs=[file_entry])

        # Verify upload was called
        mock_ssh.upload_files.assert_called_once_with("gpu-1", [file_entry])

        # Verify send_message was called with replaced text
        mock_daemon.send_message.assert_called_once()
        call_args = mock_daemon.send_message.call_args
        sent_text = call_args[0][2] if len(call_args[0]) > 2 else call_args.kwargs.get("message", "")
        # The text should have the marker replaced
        assert "<discord_file>" not in sent_text or "report.pdf" in sent_text

    @pytest.mark.asyncio
    async def test_forward_upload_failure_sends_error(
        self, bot, mock_ssh, mock_daemon, registered_session, file_entry
    ):
        """If file upload fails, error message should be sent and message NOT forwarded."""
        mock_ssh.upload_files = AsyncMock(side_effect=Exception("SSH connection lost"))

        text = "Analyze <discord_file>sess1234_abcd5678</discord_file>"
        await bot._forward_message("chan-1", text, file_refs=[file_entry])

        # Error should have been sent
        assert bot.sent_messages
        last_msg = bot.get_last_message()
        assert "File upload failed" in last_msg
        assert "SSH connection lost" in last_msg

        # daemon.send_message should NOT have been called
        mock_daemon.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_forward_without_files_works_normally(
        self, bot, mock_ssh, mock_daemon, registered_session
    ):
        """Normal text forwarding (no files) should still work."""
        async def empty_stream(*args, **kwargs):
            return
            yield

        mock_daemon.send_message = MagicMock(return_value=empty_stream())

        await bot._forward_message("chan-1", "just a text message")

        mock_ssh.upload_files.assert_not_called()
        mock_daemon.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_forward_no_session_with_files(self, bot, file_entry):
        """If no active session, should send error even with file_refs."""
        await bot._forward_message("no-session-chan", "hello", file_refs=[file_entry])

        last_msg = bot.get_last_message()
        assert "No active session" in last_msg
