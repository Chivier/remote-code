"""Tests for head.cli – CLI entry point with subcommand dispatch."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch


class TestCLIParsing:
    def test_no_args_returns_tui(self):
        from head.cli import parse_args

        args = parse_args([])
        assert args.command is None

    def test_start_command(self):
        from head.cli import parse_args

        args = parse_args(["start"])
        assert args.command == "start"

    def test_stop_command(self):
        from head.cli import parse_args

        args = parse_args(["stop"])
        assert args.command == "stop"

    def test_status_command(self):
        from head.cli import parse_args

        args = parse_args(["status"])
        assert args.command == "status"

    def test_peers_command(self):
        from head.cli import parse_args

        args = parse_args(["peers"])
        assert args.command == "peers"

    def test_token_generate(self):
        from head.cli import parse_args

        args = parse_args(["token", "generate"])
        assert args.command == "token"
        assert args.token_action == "generate"

    def test_token_revoke(self):
        from head.cli import parse_args

        args = parse_args(["token", "revoke", "ccast_abc"])
        assert args.command == "token"
        assert args.token_action == "revoke"
        assert args.token_value == "ccast_abc"

    def test_bot_start(self):
        from head.cli import parse_args

        args = parse_args(["bot", "start"])
        assert args.command == "bot"

    def test_webui_command(self):
        from head.cli import parse_args

        args = parse_args(["webui"])
        assert args.command == "webui"
        assert args.webui_action == "start"

    def test_webui_start(self):
        from head.cli import parse_args

        args = parse_args(["webui", "start"])
        assert args.command == "webui"
        assert args.webui_action == "start"

    def test_webui_stop(self):
        from head.cli import parse_args

        args = parse_args(["webui", "stop"])
        assert args.command == "webui"
        assert args.webui_action == "stop"

    def test_webui_status(self):
        from head.cli import parse_args

        args = parse_args(["webui", "status"])
        assert args.command == "webui"
        assert args.webui_action == "status"

    def test_webui_with_port(self):
        from head.cli import parse_args

        args = parse_args(["webui", "start", "--port", "8080"])
        assert args.command == "webui"
        assert args.port == 8080

    def test_webui_with_bind(self):
        from head.cli import parse_args

        args = parse_args(["webui", "start", "--bind", "0.0.0.0"])
        assert args.command == "webui"
        assert args.bind == "0.0.0.0"

    def test_restart_command(self):
        from head.cli import parse_args

        args = parse_args(["restart"])
        assert args.command == "restart"

    def test_update_command(self):
        from head.cli import parse_args

        args = parse_args(["update"])
        assert args.command == "update"

    def test_sessions_command(self):
        from head.cli import parse_args

        args = parse_args(["sessions"])
        assert args.command == "sessions"

    def test_token_list(self):
        from head.cli import parse_args

        args = parse_args(["token", "list"])
        assert args.command == "token"
        assert args.token_action == "list"

    def test_start_with_config(self):
        from head.cli import parse_args

        args = parse_args(["start", "--config", "/tmp/cfg.yaml"])
        assert args.command == "start"
        assert args.config == "/tmp/cfg.yaml"

    def test_global_config_flag(self):
        from head.cli import parse_args

        args = parse_args(["--config", "/tmp/cfg.yaml", "status"])
        assert args.command == "status"
        assert args.config == "/tmp/cfg.yaml"


class TestPortHelper:
    def test_port_available_on_free_port(self):
        from head.cli import _port_available

        # Port 0 lets the OS pick a free port — but _port_available
        # binds to the specified port, so use a high unlikely port.
        assert _port_available(59123, "127.0.0.1") is True

    def test_port_unavailable_on_used_port(self):
        import socket
        from head.cli import _port_available

        # Bind a port, then check it's unavailable
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        try:
            assert _port_available(port, "127.0.0.1") is False
        finally:
            s.close()


class TestPidHelpers:
    def test_pid_alive_current_process(self):
        from head.cli import _pid_alive

        assert _pid_alive(os.getpid()) is True

    def test_pid_alive_nonexistent(self):
        from head.cli import _pid_alive

        # PID 4000000 is almost certainly unused
        assert _pid_alive(4000000) is False

    def test_read_pid_file_missing(self):
        from head.cli import _read_pid_file

        assert _read_pid_file(Path("/tmp/nonexistent_codecast_pid")) is None

    def test_read_pid_file_valid(self, tmp_path):
        from head.cli import _read_pid_file

        pf = tmp_path / "test.pid"
        pf.write_text("12345\n")
        assert _read_pid_file(pf) == 12345

    def test_read_pid_file_invalid(self, tmp_path):
        from head.cli import _read_pid_file

        pf = tmp_path / "test.pid"
        pf.write_text("not-a-number\n")
        assert _read_pid_file(pf) is None


class TestStatusOutput:
    def test_status_prints_components(self, capsys):
        """_cmd_status prints all component sections."""
        from head.cli import _cmd_status, parse_args

        args = parse_args(["status"])
        # Mock external dependencies
        with (
            patch("head.cli._read_port_file", return_value=None),
            patch("head.cli._find_process", return_value=None),
            patch("head.cli._read_pid_file", return_value=None),
            patch("subprocess.run") as mock_run,
        ):
            # Mock 'which claude' to return not found
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            _cmd_status(args)

        output = capsys.readouterr().out
        assert "Head Node:" in output
        assert "Daemon:" in output
        assert "WebUI:" in output
        assert "Claude CLI:" in output
