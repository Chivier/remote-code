"""
Tests for bot command logic in head/bot_base.py
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from typing import Any, Optional

from head.bot_base import BotBase
from head.config import Config, MachineConfig
from head.session_router import SessionRouter, Session
from head.daemon_client import DaemonClient, DaemonError, DaemonConnectionError


# ─── MockBot: concrete subclass of BotBase for testing ───


class MockBot(BotBase):
    """Concrete BotBase subclass that records sent/edited messages."""

    def __init__(self, ssh_manager, session_router, daemon_client, config):
        super().__init__(ssh_manager, session_router, daemon_client, config)
        self.sent_messages: list[tuple[str, str]] = []  # (channel_id, text)
        self.edited_messages: list[tuple[str, Any, str]] = []  # (channel_id, msg_obj, text)
        self._msg_counter = 0

    async def send_message(self, channel_id: str, text: str) -> Any:
        self._msg_counter += 1
        self.sent_messages.append((channel_id, text))
        return f"msg-{self._msg_counter}"

    async def edit_message(self, channel_id: str, message_obj: Any, text: str) -> None:
        self.edited_messages.append((channel_id, message_obj, text))

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def get_last_message(self) -> Optional[str]:
        """Get the text of the last sent message."""
        if self.sent_messages:
            return self.sent_messages[-1][1]
        return None

    def get_all_message_texts(self) -> list[str]:
        """Get all sent message texts."""
        return [text for _, text in self.sent_messages]


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
    daemon.health_check = AsyncMock(return_value={
        "ok": True, "sessions": 0, "uptime": 100,
        "sessionsByStatus": {}, "memory": {}, "nodeVersion": "v18", "pid": 1,
    })
    daemon.monitor_sessions = AsyncMock(return_value={
        "sessions": [], "uptime": 100,
    })
    daemon.get_queue_stats = AsyncMock(return_value={
        "userPending": 0, "responsePending": 0, "clientConnected": True,
    })
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
    return MockBot(mock_ssh, mock_router, mock_daemon, mock_config)


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
            return {"ok": True, "sessions": 0, "uptime": 100, "sessionsByStatus": {}, "memory": {}, "nodeVersion": "v18", "pid": 1}

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
        assert "Remote Claude Commands" in msg

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
        assert "Remote Claude Commands" in msg

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
        assert any("Session started" in t for t in texts)
        assert any("bypass" in t for t in texts)  # default_mode="auto" -> display "bypass"


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
    async def test_ls_invalid_subcmd(self, bot):
        await bot.cmd_ls("discord:100", ["foobar"])
        msg = bot.get_last_message()
        assert "Usage:" in msg


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
