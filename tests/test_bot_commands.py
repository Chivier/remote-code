"""
Tests for bot command logic in head/engine.py (BotEngine)
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from pathlib import Path
from typing import Any, Optional

from head.engine import BotEngine
from head.platform.protocol import MessageHandle, FileAttachment, InputHandler
from head.config import Config, MachineConfig
from head.session_router import SessionRouter, Session
from head.daemon_client import DaemonClient, DaemonError, DaemonConnectionError


# ─── MockAdapter: implements PlatformAdapter protocol for testing ───


class MockAdapter:
    """Mock PlatformAdapter that records sent/edited messages."""

    def __init__(self):
        self.sent_messages: list[tuple[str, str]] = []  # (channel_id, text)
        self.edited_messages: list[tuple[str, str]] = []  # (handle_msg_id, text)
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
        return MessageHandle(
            platform="discord",
            channel_id=channel_id,
            message_id=f"msg-{self._msg_counter}",
        )

    async def edit_message(self, handle: MessageHandle, text: str) -> None:
        self.edited_messages.append((handle.message_id, text))

    async def delete_message(self, handle: MessageHandle) -> None:
        pass

    async def download_file(self, attachment: FileAttachment, dest: Path) -> Path:
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
    """BotEngine with convenience test methods for accessing recorded messages."""

    def __init__(self, adapter, ssh_manager, session_router, daemon_client, config):
        super().__init__(adapter, ssh_manager, session_router, daemon_client, config)
        self.adapter: MockAdapter  # type hint for convenience

    @property
    def sent_messages(self) -> list[tuple[str, str]]:
        return self.adapter.sent_messages

    @property
    def edited_messages(self) -> list[tuple[str, str]]:
        return self.adapter.edited_messages

    def get_last_message(self) -> Optional[str]:
        """Get the text of the last sent message."""
        if self.adapter.sent_messages:
            return self.adapter.sent_messages[-1][1]
        return None

    def get_all_message_texts(self) -> list[str]:
        """Get all sent message texts."""
        return [text for _, text in self.adapter.sent_messages]


# ─── Fixtures ───


@pytest.fixture
def mock_ssh():
    ssh = AsyncMock()
    ssh.ensure_tunnel = AsyncMock(return_value=19100)
    ssh.get_local_port = MagicMock(return_value=None)
    ssh.sync_skills = AsyncMock()
    ssh.list_machines = AsyncMock(return_value=[])
    return ssh


@pytest.fixture
def mock_router(tmp_path):
    return SessionRouter(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def mock_daemon():
    daemon = AsyncMock(spec=DaemonClient)
    daemon.health_check = AsyncMock(
        return_value={
            "ok": True,
            "sessions": 0,
            "uptime": 100,
            "sessionsByStatus": {},
            "memory": {},
            "nodeVersion": "v18",
            "pid": 1,
        }
    )
    daemon.monitor_sessions = AsyncMock(
        return_value={
            "sessions": [],
            "uptime": 100,
        }
    )
    daemon.get_queue_stats = AsyncMock(
        return_value={
            "userPending": 0,
            "responsePending": 0,
            "clientConnected": True,
        }
    )
    daemon.set_mode = AsyncMock(return_value=True)
    daemon.create_session = AsyncMock(return_value="new-session-id-123456")
    daemon.destroy_session = AsyncMock(return_value=True)
    return daemon


@pytest.fixture
def mock_config():
    config = Config()
    config.machines = {
        "gpu-1": MachineConfig(id="gpu-1", host="10.0.0.1", user="user"),
        "gpu-2": MachineConfig(id="gpu-2", host="10.0.0.2", user="user"),
    }
    config.default_mode = "auto"
    return config


@pytest.fixture
def bot(mock_ssh, mock_router, mock_daemon, mock_config):
    adapter = MockAdapter()
    engine = MockBotEngine(adapter, mock_ssh, mock_router, mock_daemon, mock_config)
    adapter.set_input_handler(engine.handle_input)
    return engine


def _make_session(**kwargs):
    defaults = {
        "channel_id": "discord:100",
        "machine_id": "gpu-1",
        "path": "/home/user/project",
        "daemon_session_id": "sess-001-abcdef1234567890",
        "sdk_session_id": None,
        "status": "active",
        "mode": "auto",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
        "name": "bright-falcon",
    }
    defaults.update(kwargs)
    return Session(**defaults)


# ─── cmd_health ───


class TestCmdHealth:
    @pytest.mark.asyncio
    async def test_with_machine_arg(self, bot):
        await bot.cmd_health("discord:100", ["gpu-1"])
        msg = bot.get_last_message()
        assert "Daemon Health - gpu-1" in msg
        assert "OK" in msg

    @pytest.mark.asyncio
    async def test_uses_current_session(self, bot, mock_router):
        # Register an active session
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_health("discord:100", [])
        msg = bot.get_last_message()
        assert "Daemon Health - gpu-1" in msg

    @pytest.mark.asyncio
    async def test_no_tunnels(self, bot, mock_config):
        # No active session, no tunnels
        await bot.cmd_health("discord:100", [])
        msg = bot.get_last_message()
        assert "No active tunnels" in msg

    @pytest.mark.asyncio
    async def test_multiple_machines(self, bot, mock_ssh, mock_config):
        # Set up tunnels for both machines
        mock_ssh.get_local_port.side_effect = lambda mid: 19100 if mid == "gpu-1" else 19101
        await bot.cmd_health("discord:100", [])
        msg = bot.get_last_message()
        assert "gpu-1" in msg
        assert "gpu-2" in msg

    @pytest.mark.asyncio
    async def test_health_check_error(self, bot, mock_daemon):
        mock_daemon.health_check.side_effect = Exception("Connection refused")
        await bot.cmd_health("discord:100", ["gpu-1"])
        msg = bot.get_last_message()
        assert "Error" in msg

    @pytest.mark.asyncio
    async def test_health_multiple_some_fail(self, bot, mock_ssh, mock_daemon):
        mock_ssh.get_local_port.side_effect = lambda mid: 19100 if mid == "gpu-1" else 19101

        call_count = 0

        async def health_side_effect(port):
            nonlocal call_count
            call_count += 1
            if port == 19101:
                raise Exception("Connection refused")
            return {
                "ok": True,
                "sessions": 0,
                "uptime": 100,
                "sessionsByStatus": {},
                "memory": {},
                "nodeVersion": "v18",
                "pid": 1,
            }

        mock_daemon.health_check.side_effect = health_side_effect
        await bot.cmd_health("discord:100", [])
        msg = bot.get_last_message()
        assert "gpu-1" in msg
        assert "gpu-2" in msg


# ─── cmd_monitor ───


class TestCmdMonitor:
    @pytest.mark.asyncio
    async def test_with_machine_arg(self, bot):
        await bot.cmd_monitor("discord:100", ["gpu-1"])
        msg = bot.get_last_message()
        assert "Monitor - gpu-1" in msg

    @pytest.mark.asyncio
    async def test_uses_current_session(self, bot, mock_router):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_monitor("discord:100", [])
        msg = bot.get_last_message()
        assert "Monitor - gpu-1" in msg

    @pytest.mark.asyncio
    async def test_no_tunnels(self, bot):
        await bot.cmd_monitor("discord:100", [])
        msg = bot.get_last_message()
        assert "No active tunnels" in msg

    @pytest.mark.asyncio
    async def test_multiple_machines(self, bot, mock_ssh):
        mock_ssh.get_local_port.side_effect = lambda mid: 19100 if mid == "gpu-1" else 19101
        await bot.cmd_monitor("discord:100", [])
        msg = bot.get_last_message()
        assert "gpu-1" in msg
        assert "gpu-2" in msg

    @pytest.mark.asyncio
    async def test_monitor_error(self, bot, mock_daemon):
        mock_daemon.monitor_sessions.side_effect = Exception("Connection refused")
        await bot.cmd_monitor("discord:100", ["gpu-1"])
        msg = bot.get_last_message()
        assert "Error" in msg


# ─── cmd_mode ───


class TestCmdMode:
    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self, bot):
        await bot.cmd_mode("discord:100", [])
        msg = bot.get_last_message()
        assert "Usage:" in msg
        assert "auto" in msg
        assert "code" in msg

    @pytest.mark.asyncio
    async def test_bypass_alias_for_auto(self, bot, mock_router, mock_daemon):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_mode("discord:100", ["bypass"])
        msg = bot.get_last_message()
        assert "bypass" in msg
        # Should have called set_mode with "auto" internally
        mock_daemon.set_mode.assert_called_once()
        call_args = mock_daemon.set_mode.call_args
        assert call_args[0][2] == "auto"  # mode arg

    @pytest.mark.asyncio
    async def test_valid_mode_set(self, bot, mock_router, mock_daemon):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_mode("discord:100", ["code"])
        msg = bot.get_last_message()
        assert "code" in msg

    @pytest.mark.asyncio
    async def test_invalid_mode(self, bot):
        await bot.cmd_mode("discord:100", ["invalid"])
        msg = bot.get_last_message()
        assert "Invalid mode" in msg

    @pytest.mark.asyncio
    async def test_no_active_session(self, bot):
        await bot.cmd_mode("discord:100", ["code"])
        msg = bot.get_last_message()
        assert "No active session" in msg

    @pytest.mark.asyncio
    async def test_display_mode_in_output(self, bot, mock_router, mock_daemon):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_mode("discord:100", ["auto"])
        msg = bot.get_last_message()
        assert "bypass" in msg  # display_mode("auto") -> "bypass"

    @pytest.mark.asyncio
    async def test_set_mode_failure(self, bot, mock_router, mock_daemon):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        mock_daemon.set_mode.return_value = False
        await bot.cmd_mode("discord:100", ["plan"])
        msg = bot.get_last_message()
        assert "Error" in msg or "Failed" in msg


# ─── cmd_status ───


class TestCmdStatus:
    @pytest.mark.asyncio
    async def test_no_session(self, bot):
        await bot.cmd_status("discord:100")
        msg = bot.get_last_message()
        assert "No active session" in msg

    @pytest.mark.asyncio
    async def test_with_session(self, bot, mock_router):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001-abcdef1234")
        await bot.cmd_status("discord:100")
        msg = bot.get_last_message()
        assert "Session Status" in msg
        assert "gpu-1" in msg
        assert "bypass" in msg  # auto -> bypass

    @pytest.mark.asyncio
    async def test_display_mode_in_output(self, bot, mock_router):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001-abcdef1234", mode="code")
        await bot.cmd_status("discord:100")
        msg = bot.get_last_message()
        assert "code" in msg

    @pytest.mark.asyncio
    async def test_queue_stats_shown(self, bot, mock_router, mock_daemon):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001-abcdef1234")
        mock_daemon.get_queue_stats.return_value = {"userPending": 5, "responsePending": 2}
        await bot.cmd_status("discord:100")
        msg = bot.get_last_message()
        assert "5 pending" in msg
        assert "2 response" in msg

    @pytest.mark.asyncio
    async def test_queue_stats_error_graceful(self, bot, mock_router, mock_daemon):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001-abcdef1234")
        mock_daemon.get_queue_stats.side_effect = Exception("Connection lost")
        await bot.cmd_status("discord:100")
        msg = bot.get_last_message()
        # Should still show status even if queue stats fail
        assert "Session Status" in msg

    @pytest.mark.asyncio
    async def test_status_shows_name(self, bot, mock_router):
        name = mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_status("discord:100")
        msg = bot.get_last_message()
        assert f"Name: **{name}**" in msg

    @pytest.mark.asyncio
    async def test_status_shows_full_session_id(self, bot, mock_router):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001-abcdef1234567890")
        await bot.cmd_status("discord:100")
        msg = bot.get_last_message()
        assert "sess-001-abcdef1234567890" in msg  # Full ID


# ─── handle_input ───


class TestHandleInput:
    @pytest.mark.asyncio
    async def test_empty_input(self, bot):
        await bot.handle_input("discord:100", "")
        assert len(bot.sent_messages) == 0

    @pytest.mark.asyncio
    async def test_whitespace_input(self, bot):
        await bot.handle_input("discord:100", "   ")
        assert len(bot.sent_messages) == 0

    @pytest.mark.asyncio
    async def test_command_routing_help(self, bot):
        await bot.handle_input("discord:100", "/help")
        msg = bot.get_last_message()
        assert "Codecast Commands" in msg

    @pytest.mark.asyncio
    async def test_command_routing_status(self, bot):
        await bot.handle_input("discord:100", "/status")
        msg = bot.get_last_message()
        assert "No active session" in msg

    @pytest.mark.asyncio
    async def test_unknown_command(self, bot):
        await bot.handle_input("discord:100", "/foobar")
        msg = bot.get_last_message()
        assert "Unknown command" in msg
        assert "/foobar" in msg

    @pytest.mark.asyncio
    async def test_non_command_forwards_to_session(self, bot, mock_router, mock_daemon):
        # Set up a session
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        # Mock send_message to return an async iterator
        async def mock_send(*args, **kwargs):
            yield {"type": "text", "content": "Hi there!"}
            yield {"type": "result", "session_id": "sdk-123"}

        mock_daemon.send_message = mock_send

        await bot.handle_input("discord:100", "Hello Claude")
        texts = bot.get_all_message_texts()
        assert any("Hi there!" in t for t in texts)

    @pytest.mark.asyncio
    async def test_non_command_no_session(self, bot):
        await bot.handle_input("discord:100", "Hello Claude")
        msg = bot.get_last_message()
        assert "No active session" in msg

    @pytest.mark.asyncio
    async def test_command_routing_ls(self, bot):
        await bot.handle_input("discord:100", "/ls machine")
        msg = bot.get_last_message()
        # Should have called list_machines
        assert msg is not None

    @pytest.mark.asyncio
    async def test_command_routing_exit(self, bot):
        await bot.handle_input("discord:100", "/exit")
        msg = bot.get_last_message()
        assert "No active session" in msg

    @pytest.mark.asyncio
    async def test_command_case_insensitive(self, bot):
        await bot.handle_input("discord:100", "/HELP")
        msg = bot.get_last_message()
        assert "Codecast Commands" in msg

    @pytest.mark.asyncio
    async def test_command_routing_mode(self, bot):
        await bot.handle_input("discord:100", "/mode")
        msg = bot.get_last_message()
        assert "Usage:" in msg

    @pytest.mark.asyncio
    async def test_daemon_connection_error(self, bot, mock_router, mock_daemon):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        mock_daemon.get_queue_stats.side_effect = DaemonConnectionError("No connection")
        # The command itself catches exceptions
        await bot.handle_input("discord:100", "/health gpu-1")
        # Should get some response (error or health data)
        assert len(bot.sent_messages) > 0

    @pytest.mark.asyncio
    async def test_command_routing_list_alias(self, bot):
        await bot.handle_input("discord:100", "/list machine")
        msg = bot.get_last_message()
        assert msg is not None

    @pytest.mark.asyncio
    async def test_command_routing_rm(self, bot):
        await bot.handle_input("discord:100", "/rm")
        msg = bot.get_last_message()
        assert "Usage:" in msg

    @pytest.mark.asyncio
    async def test_command_routing_destroy_alias(self, bot):
        await bot.handle_input("discord:100", "/destroy gpu-1 /path")
        msg = bot.get_last_message()
        assert msg is not None

    @pytest.mark.asyncio
    async def test_command_routing_rename(self, bot):
        await bot.handle_input("discord:100", "/rename")
        msg = bot.get_last_message()
        assert "Usage:" in msg


# ─── cmd_start ───


class TestCmdStart:
    @pytest.mark.asyncio
    async def test_start_no_args(self, bot):
        await bot.cmd_start("discord:100", [])
        msg = bot.get_last_message()
        assert "Usage:" in msg

    @pytest.mark.asyncio
    async def test_start_one_arg(self, bot):
        await bot.cmd_start("discord:100", ["gpu-1"])
        msg = bot.get_last_message()
        assert "Usage:" in msg

    @pytest.mark.asyncio
    async def test_start_success(self, bot, mock_daemon):
        await bot.cmd_start("discord:100", ["gpu-1", "/home/user/project"])
        texts = bot.get_all_message_texts()
        assert any("Session ready" in t for t in texts)
        assert any("bypass" in t.lower() for t in texts)  # default_mode="auto" -> display "bypass"

    @pytest.mark.asyncio
    async def test_start_shows_name(self, bot, mock_daemon):
        await bot.cmd_start("discord:100", ["gpu-1", "/home/user/project"])
        texts = bot.get_all_message_texts()
        assert any("Name:" in t for t in texts)  # Name field present

    @pytest.mark.asyncio
    async def test_start_shows_full_session_id(self, bot, mock_daemon):
        await bot.cmd_start("discord:100", ["gpu-1", "/home/user/project"])
        texts = bot.get_all_message_texts()
        assert any("new-session-id-123456" in t for t in texts)  # Full ID, no truncation


# ─── cmd_exit ───


class TestCmdExit:
    @pytest.mark.asyncio
    async def test_exit_no_session(self, bot):
        await bot.cmd_exit("discord:100")
        msg = bot.get_last_message()
        assert "No active session" in msg

    @pytest.mark.asyncio
    async def test_exit_success(self, bot, mock_router):
        name = mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_exit("discord:100")
        msg = bot.get_last_message()
        assert "Detached" in msg
        assert name in msg

    @pytest.mark.asyncio
    async def test_exit_shows_name_in_resume_hint(self, bot, mock_router):
        name = mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_exit("discord:100")
        msg = bot.get_last_message()
        assert f"/resume {name}" in msg


# ─── cmd_ls ───


class TestCmdLs:
    @pytest.mark.asyncio
    async def test_ls_no_args(self, bot):
        await bot.cmd_ls("discord:100", [])
        msg = bot.get_last_message()
        assert "Usage:" in msg

    @pytest.mark.asyncio
    async def test_ls_machines(self, bot, mock_ssh):
        mock_ssh.list_machines.return_value = [
            {"id": "gpu-1", "host": "10.0.0.1", "status": "online", "daemon": "running"},
        ]
        await bot.cmd_ls("discord:100", ["machine"])
        msg = bot.get_last_message()
        assert "gpu-1" in msg

    @pytest.mark.asyncio
    async def test_ls_sessions(self, bot, mock_router):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001-abcdef1234")
        await bot.cmd_ls("discord:100", ["session"])
        msg = bot.get_last_message()
        assert "Sessions:" in msg

    @pytest.mark.asyncio
    async def test_ls_sessions_shows_full_id(self, bot, mock_router):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001-abcdef1234")
        await bot.cmd_ls("discord:100", ["session"])
        msg = bot.get_last_message()
        assert "sess-001-abcdef1234" in msg  # Full ID

    @pytest.mark.asyncio
    async def test_ls_sessions_shows_name(self, bot, mock_router):
        name = mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_ls("discord:100", ["session"])
        msg = bot.get_last_message()
        assert name in msg

    @pytest.mark.asyncio
    async def test_ls_invalid_subcmd(self, bot):
        await bot.cmd_ls("discord:100", ["foobar"])
        msg = bot.get_last_message()
        assert "Usage:" in msg


# ─── cmd_rename ───


class TestCmdRename:
    @pytest.mark.asyncio
    async def test_rename_no_args(self, bot):
        await bot.cmd_rename("discord:100", [])
        msg = bot.get_last_message()
        assert "Usage:" in msg

    @pytest.mark.asyncio
    async def test_rename_no_session(self, bot):
        await bot.cmd_rename("discord:100", ["my-project"])
        msg = bot.get_last_message()
        assert "No active session" in msg

    @pytest.mark.asyncio
    async def test_rename_success(self, bot, mock_router):
        old_name = mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_rename("discord:100", ["my-project"])
        msg = bot.get_last_message()
        assert "renamed" in msg
        assert "my-project" in msg

    @pytest.mark.asyncio
    async def test_rename_invalid_name(self, bot, mock_router):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_rename("discord:100", ["INVALID"])
        msg = bot.get_last_message()
        assert "Invalid name" in msg

    @pytest.mark.asyncio
    async def test_rename_single_word_invalid(self, bot, mock_router):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_rename("discord:100", ["project"])
        msg = bot.get_last_message()
        assert "Invalid name" in msg

    @pytest.mark.asyncio
    async def test_rename_duplicate_fails(self, bot, mock_router):
        name1 = mock_router.register("discord:100", "gpu-1", "/path1", "sess-001")
        mock_router.register("discord:200", "gpu-1", "/path2", "sess-002")
        await bot.cmd_rename("discord:200", [name1])
        msg = bot.get_last_message()
        assert "already in use" in msg

    @pytest.mark.asyncio
    async def test_rename_updates_session(self, bot, mock_router):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.cmd_rename("discord:100", ["my-project"])

        session = mock_router.resolve("discord:100")
        assert session.name == "my-project"

    @pytest.mark.asyncio
    async def test_rename_via_handle_input(self, bot, mock_router):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        await bot.handle_input("discord:100", "/rename my-project")
        msg = bot.get_last_message()
        assert "renamed" in msg

    @pytest.mark.asyncio
    async def test_help_includes_rename(self, bot):
        await bot.cmd_help("discord:100")
        msg = bot.get_last_message()
        assert "/rename" in msg


# ─── cmd_resume with name ───


class TestCmdResumeWithName:
    @pytest.mark.asyncio
    async def test_resume_by_name(self, bot, mock_router, mock_daemon):
        name = mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        mock_router.detach("discord:100")

        mock_daemon.resume_session = AsyncMock(return_value={"ok": True})
        await bot.cmd_resume("discord:200", [name])
        texts = bot.get_all_message_texts()
        assert any("Resuming session" in t for t in texts)

    @pytest.mark.asyncio
    async def test_resume_by_id(self, bot, mock_router, mock_daemon):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        mock_router.detach("discord:100")

        mock_daemon.resume_session = AsyncMock(return_value={"ok": True})
        await bot.cmd_resume("discord:200", ["sess-001"])
        texts = bot.get_all_message_texts()
        assert any("Resuming session" in t for t in texts)

    @pytest.mark.asyncio
    async def test_resume_not_found(self, bot):
        await bot.cmd_resume("discord:100", ["nonexistent"])
        msg = bot.get_last_message()
        assert "not found" in msg

    @pytest.mark.asyncio
    async def test_resume_shows_name(self, bot, mock_router, mock_daemon):
        name = mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        mock_router.detach("discord:100")

        mock_daemon.resume_session = AsyncMock(return_value={"ok": True})
        await bot.cmd_resume("discord:200", [name])
        texts = bot.get_all_message_texts()
        assert any(f"**{name}**" in t for t in texts)


# ─── cmd_interrupt ───


class TestCmdInterrupt:
    @pytest.mark.asyncio
    async def test_interrupt_no_session(self, bot):
        await bot.cmd_interrupt("discord:100")
        msg = bot.get_last_message()
        assert "No active session" in msg

    @pytest.mark.asyncio
    async def test_interrupt_success(self, bot, mock_router, mock_daemon):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        mock_daemon.interrupt_session.return_value = {"interrupted": True}
        await bot.cmd_interrupt("discord:100")
        msg = bot.get_last_message()
        assert "Interrupted" in msg

    @pytest.mark.asyncio
    async def test_interrupt_not_processing(self, bot, mock_router, mock_daemon):
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")
        mock_daemon.interrupt_session.return_value = {"interrupted": False}
        await bot.cmd_interrupt("discord:100")
        msg = bot.get_last_message()
        assert "not currently processing" in msg


# ─── Full Message Flow Tests ───


class TestFullMessageFlow:
    """Test the complete message processing pipeline:
    handle_input -> resolve session -> ensure_tunnel -> send_message -> stream events -> send response
    """

    @pytest.mark.asyncio
    async def test_text_response_flow(self, bot, mock_router, mock_daemon):
        """User sends text, Claude responds with a text event."""
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_send(*args, **kwargs):
            yield {"type": "text", "content": "Hello! How can I help?"}
            yield {"type": "result", "session_id": "sdk-123"}

        mock_daemon.send_message = mock_send

        await bot.handle_input("discord:100", "hi")
        texts = bot.get_all_message_texts()
        assert any("Hello! How can I help?" in t for t in texts)

    @pytest.mark.asyncio
    async def test_streaming_partial_then_text(self, bot, mock_router, mock_daemon):
        """User sends text, Claude streams partial then sends complete text."""
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_send(*args, **kwargs):
            yield {"type": "partial", "content": "Hel"}
            yield {"type": "partial", "content": "lo!"}
            yield {"type": "text", "content": "Hello!"}
            yield {"type": "result", "session_id": "sdk-123"}

        mock_daemon.send_message = mock_send

        await bot.handle_input("discord:100", "say hello")
        # Should have edited the message with final text "Hello!"
        assert len(bot.edited_messages) > 0 or any("Hello!" in t for _, t in bot.sent_messages)

    @pytest.mark.asyncio
    async def test_tool_use_then_text(self, bot, mock_router, mock_daemon):
        """Claude uses a tool then responds with text."""
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_send(*args, **kwargs):
            yield {"type": "tool_use", "tool": "Read", "message": "Reading file.py"}
            yield {"type": "text", "content": "Here is the file content."}
            yield {"type": "result", "session_id": "sdk-123"}

        mock_daemon.send_message = mock_send

        await bot.handle_input("discord:100", "read file.py")
        texts = bot.get_all_message_texts()
        assert any("Read" in t for t in texts)
        assert any("file content" in t for t in texts)

    @pytest.mark.asyncio
    async def test_error_event(self, bot, mock_router, mock_daemon):
        """Claude returns an error event."""
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_send(*args, **kwargs):
            yield {"type": "error", "message": "Something went wrong"}

        mock_daemon.send_message = mock_send

        await bot.handle_input("discord:100", "do something")
        texts = bot.get_all_message_texts()
        assert any("Something went wrong" in t for t in texts)

    @pytest.mark.asyncio
    async def test_queued_event(self, bot, mock_router, mock_daemon):
        """Claude is busy, message gets queued."""
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_send(*args, **kwargs):
            yield {"type": "queued", "position": 2}

        mock_daemon.send_message = mock_send

        await bot.handle_input("discord:100", "hello")
        texts = bot.get_all_message_texts()
        assert any("queued" in t.lower() or "position" in t.lower() for t in texts)

    @pytest.mark.asyncio
    async def test_system_init_event(self, bot, mock_router, mock_daemon):
        """System init event updates SDK session ID."""
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_send(*args, **kwargs):
            yield {"type": "system", "subtype": "init", "session_id": "sdk-999"}
            yield {"type": "text", "content": "Initialized."}
            yield {"type": "result", "session_id": "sdk-999"}

        mock_daemon.send_message = mock_send

        await bot.handle_input("discord:100", "hello")
        # Verify SDK session ID was updated
        session = mock_router.resolve("discord:100")
        assert session.sdk_session_id == "sdk-999"

    @pytest.mark.asyncio
    async def test_no_session_sends_error(self, bot):
        """Sending a message without an active session shows error."""
        await bot.handle_input("discord:100", "hello")
        texts = bot.get_all_message_texts()
        assert any("No active session" in t for t in texts)

    @pytest.mark.asyncio
    async def test_result_event_does_not_produce_message(self, bot, mock_router, mock_daemon):
        """Result event should not be sent as a visible message."""
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_send(*args, **kwargs):
            yield {"type": "text", "content": "Done."}
            yield {"type": "result", "session_id": "sdk-123"}

        mock_daemon.send_message = mock_send

        await bot.handle_input("discord:100", "do it")
        texts = bot.get_all_message_texts()
        assert not any("result" in t.lower() and "sdk-123" in t for t in texts)
        assert any("Done." in t for t in texts)

    @pytest.mark.asyncio
    async def test_multiple_text_events(self, bot, mock_router, mock_daemon):
        """Multiple text events produce multiple messages."""
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_send(*args, **kwargs):
            yield {"type": "text", "content": "First part."}
            yield {"type": "text", "content": "Second part."}
            yield {"type": "result", "session_id": "sdk-123"}

        mock_daemon.send_message = mock_send

        await bot.handle_input("discord:100", "explain")
        texts = bot.get_all_message_texts()
        assert any("First part." in t for t in texts)
        assert any("Second part." in t for t in texts)

    @pytest.mark.asyncio
    async def test_ping_events_ignored(self, bot, mock_router, mock_daemon):
        """Ping keepalive events should not produce any visible output."""
        mock_router.register("discord:100", "gpu-1", "/path", "sess-001")

        async def mock_send(*args, **kwargs):
            yield {"type": "ping"}
            yield {"type": "ping"}
            yield {"type": "text", "content": "Response."}
            yield {"type": "result", "session_id": "sdk-123"}

        mock_daemon.send_message = mock_send

        await bot.handle_input("discord:100", "test")
        texts = bot.get_all_message_texts()
        assert not any("ping" in t.lower() for t in texts)
        assert any("Response." in t for t in texts)


# ─── Start with ~ Expansion ───


class TestStartTildeExpansion:
    @pytest.mark.asyncio
    async def test_tilde_not_expanded_locally(self, bot, mock_daemon):
        """~/Projects should NOT be expanded locally - daemon handles it on the remote machine."""
        await bot.cmd_start("discord:100", ["gpu-1", "~/Projects/myapp"])
        # Check what path was passed to create_session
        call_args = mock_daemon.create_session.call_args
        if call_args:
            actual_path = call_args[0][1]
            # Path should be passed through as-is for the daemon to expand
            assert actual_path == "~/Projects/myapp"

    @pytest.mark.asyncio
    async def test_absolute_path_unchanged(self, bot, mock_daemon):
        """Absolute paths should not be modified."""
        await bot.cmd_start("discord:100", ["gpu-1", "/home/user/project"])
        call_args = mock_daemon.create_session.call_args
        if call_args:
            actual_path = call_args[0][1]
            assert actual_path == "/home/user/project"


# ─── Add Machine from SSH Config ───


class TestAddMachineSSH:
    @pytest.mark.asyncio
    async def test_add_machine_name_only_not_in_ssh(self, bot):
        """Adding a machine by name that doesn't exist in SSH config shows error."""
        await bot.cmd_add_machine("discord:100", ["nonexistent-host-xyz"])
        texts = bot.get_all_message_texts()
        assert any("not found in SSH config" in t for t in texts)

    @pytest.mark.asyncio
    async def test_add_machine_with_host_user(self, bot, mock_config):
        """Adding machine with explicit host/user should work."""
        await bot.cmd_add_machine("discord:100", ["test-m", "example.com", "testuser"])
        texts = bot.get_all_message_texts()
        assert any("test-m" in t and "added" in t.lower() for t in texts)
        assert "test-m" in bot.config.machines

    @pytest.mark.asyncio
    async def test_add_duplicate_machine(self, bot):
        """Adding a machine that already exists should fail."""
        await bot.cmd_add_machine("discord:100", ["gpu-1", "x.com", "user"])
        texts = bot.get_all_message_texts()
        assert any("already exists" in t for t in texts)

    @pytest.mark.asyncio
    async def test_remove_machine(self, bot, mock_router):
        """Removing a machine should work."""
        await bot.cmd_remove_machine("discord:100", ["gpu-1"])
        texts = bot.get_all_message_texts()
        assert any("removed" in t.lower() for t in texts)
        assert "gpu-1" not in bot.config.machines

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, bot):
        """Removing a machine that doesn't exist should fail."""
        await bot.cmd_remove_machine("discord:100", ["no-such-machine"])
        texts = bot.get_all_message_texts()
        assert any("not found" in t.lower() for t in texts)

    @pytest.mark.asyncio
    async def test_remove_proxy_dependency(self, bot, mock_config):
        """Cannot remove a machine used as proxy_jump by another."""
        mock_config.machines["gpu-2"].proxy_jump = "gpu-1"
        await bot.cmd_remove_machine("discord:100", ["gpu-1"])
        texts = bot.get_all_message_texts()
        assert any("proxy_jump" in t for t in texts)


# ─── Restart / Update Admin Check ───


class TestAdminCommands:
    @pytest.mark.asyncio
    async def test_restart_no_admin(self, bot):
        """Restart without admin privileges should fail."""
        await bot.cmd_restart("discord:100", user_id=12345)
        msg = bot.get_last_message()
        assert "admin" in msg.lower()

    @pytest.mark.asyncio
    async def test_update_no_admin(self, bot):
        """Update without admin privileges should fail."""
        await bot.cmd_update("discord:100", user_id=12345)
        msg = bot.get_last_message()
        assert "admin" in msg.lower()

    @pytest.mark.asyncio
    async def test_restart_no_user_id(self, bot):
        """Restart with no user_id should fail."""
        await bot.cmd_restart("discord:100", user_id=None)
        msg = bot.get_last_message()
        assert "admin" in msg.lower()


# ─── Peer Command Aliases (v2) ───


class TestPeerAliases:
    """Test that v2 peer command aliases route to the same handlers as machine commands."""

    @pytest.mark.asyncio
    async def test_add_peer_alias(self, bot):
        """add-peer routes to same handler as add-machine."""
        await bot.handle_input("discord:100", "/add-peer")
        texts = bot.get_all_message_texts()
        # Should show usage or prompt (same as add-machine with no args)
        assert len(texts) > 0

    @pytest.mark.asyncio
    async def test_addpeer_alias(self, bot):
        """addpeer (no dash) routes to same handler as add-machine."""
        await bot.handle_input("discord:100", "/addpeer")
        texts = bot.get_all_message_texts()
        assert len(texts) > 0

    @pytest.mark.asyncio
    async def test_remove_peer_alias(self, bot):
        """remove-peer routes to same handler as remove-machine."""
        await bot.handle_input("discord:100", "/remove-peer no-such-machine")
        texts = bot.get_all_message_texts()
        assert any("not found" in t.lower() for t in texts)

    @pytest.mark.asyncio
    async def test_removepeer_alias(self, bot):
        """removepeer (no dash) routes to same handler as remove-machine."""
        await bot.handle_input("discord:100", "/removepeer no-such-machine")
        texts = bot.get_all_message_texts()
        assert any("not found" in t.lower() for t in texts)

    @pytest.mark.asyncio
    async def test_rm_peer_alias(self, bot):
        """rm-peer routes to same handler as remove-machine."""
        await bot.handle_input("discord:100", "/rm-peer no-such-machine")
        texts = bot.get_all_message_texts()
        assert any("not found" in t.lower() for t in texts)

    @pytest.mark.asyncio
    async def test_rmpeer_alias(self, bot):
        """rmpeer (no dash) routes to same handler as remove-machine."""
        await bot.handle_input("discord:100", "/rmpeer no-such-machine")
        texts = bot.get_all_message_texts()
        assert any("not found" in t.lower() for t in texts)

    @pytest.mark.asyncio
    async def test_add_peer_variadic(self, bot, mock_config):
        """add-peer should be in variadic commands and handle multiple args."""
        await bot.handle_input("discord:100", "/add-peer test-gpu gpu.example.com testuser")
        texts = bot.get_all_message_texts()
        assert any("test-gpu" in t and "added" in t.lower() for t in texts)
        assert "test-gpu" in bot.config.machines

    @pytest.mark.asyncio
    async def test_remove_peer_existing_machine(self, bot, mock_router):
        """remove-peer can remove an existing machine."""
        await bot.handle_input("discord:100", "/remove-peer gpu-1")
        texts = bot.get_all_message_texts()
        assert any("removed" in t.lower() for t in texts)
        assert "gpu-1" not in bot.config.machines

    @pytest.mark.asyncio
    async def test_help_shows_peer_aliases(self, bot):
        """Help text should mention peer aliases."""
        await bot.cmd_help("discord:100")
        msg = bot.get_last_message()
        assert "/add-peer" in msg
        assert "/remove-peer" in msg
