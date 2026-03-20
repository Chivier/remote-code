"""Tests for head.cli – CLI entry point with subcommand dispatch."""

import os
import shutil
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

    def test_head_start(self):
        from head.cli import parse_args

        args = parse_args(["head", "start"])
        assert args.command == "head"

    def test_bot_alias(self):
        from head.cli import parse_args

        args = parse_args(["bot", "start"])
        assert args.command == "bot"

    def test_head_with_yes_flag(self):
        from head.cli import parse_args

        args = parse_args(["head", "--yes"])
        assert args.command == "head"
        assert args.yes is True

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

    def test_version_flag(self):
        from head.cli import parse_args

        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--version"])
        assert exc_info.value.code == 0

    def test_version_output(self, capsys):
        from head.cli import parse_args
        from head.__version__ import __version__

        with pytest.raises(SystemExit):
            parse_args(["--version"])
        output = capsys.readouterr().out
        assert __version__ in output
        assert "codecast" in output

    def test_uninstall_command(self):
        from head.cli import parse_args

        args = parse_args(["uninstall"])
        assert args.command == "uninstall"
        assert args.keep_config is False
        assert args.yes is False

    def test_uninstall_with_keep_config(self):
        from head.cli import parse_args

        args = parse_args(["uninstall", "--keep-config"])
        assert args.command == "uninstall"
        assert args.keep_config is True

    def test_uninstall_with_yes(self):
        from head.cli import parse_args

        args = parse_args(["uninstall", "--yes"])
        assert args.command == "uninstall"
        assert args.yes is True

    def test_completion_bash(self):
        from head.cli import parse_args

        args = parse_args(["completion", "bash"])
        assert args.command == "completion"
        assert args.shell == "bash"

    def test_completion_zsh(self):
        from head.cli import parse_args

        args = parse_args(["completion", "zsh"])
        assert args.command == "completion"
        assert args.shell == "zsh"

    def test_completion_fish(self):
        from head.cli import parse_args

        args = parse_args(["completion", "fish"])
        assert args.command == "completion"
        assert args.shell == "fish"

    def test_completion_invalid_shell(self):
        from head.cli import parse_args

        with pytest.raises(SystemExit):
            parse_args(["completion", "powershell"])


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


class TestCmdPeers:
    def test_peers_lists_configured_peers(self, tmp_path, capsys):
        """_cmd_peers iterates dict items and prints peer_id + host."""
        from head.cli import _cmd_peers, parse_args

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "peers:\n"
            "  gpu-box:\n"
            "    transport: ssh\n"
            "    ssh_host: 10.0.0.5\n"
            "    ssh_user: alice\n"
            "  local-dev:\n"
            "    transport: local\n"
        )
        args = parse_args(["--config", str(cfg_file), "peers"])
        _cmd_peers(args)

        output = capsys.readouterr().out
        assert "gpu-box" in output
        assert "10.0.0.5" in output
        assert "local-dev" in output
        # local-dev has no ssh_host, should fall back to transport
        assert "local" in output

    def test_peers_empty(self, tmp_path, capsys):
        """_cmd_peers prints message when no peers configured."""
        from head.cli import _cmd_peers, parse_args

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("peers: {}\n")
        args = parse_args(["--config", str(cfg_file), "peers"])
        _cmd_peers(args)

        output = capsys.readouterr().out
        assert "No peers configured" in output

    def test_peers_missing_config(self, capsys):
        """_cmd_peers handles missing config file."""
        from head.cli import _cmd_peers, parse_args

        args = parse_args(["--config", "/tmp/nonexistent_codecast_cfg.yaml", "peers"])
        _cmd_peers(args)

        output = capsys.readouterr().out
        assert "Config file not found" in output


class TestUninstall:
    def test_uninstall_removes_files(self, tmp_path, capsys):
        """_cmd_uninstall removes expected paths from codecast dir."""
        from head.cli import _cmd_uninstall, parse_args

        codecast_dir = tmp_path / ".codecast"
        codecast_dir.mkdir()
        (codecast_dir / "daemon").mkdir()
        (codecast_dir / "daemon" / "codecast-daemon").write_text("binary")
        (codecast_dir / "daemon.log").write_text("logs")
        (codecast_dir / "sessions.db").write_text("db")
        (codecast_dir / "file-pool").mkdir()
        (codecast_dir / "downloads").mkdir()
        (codecast_dir / "skills").mkdir()
        (codecast_dir / "daemon.pid").write_text("99999")
        (codecast_dir / "daemon.port").write_text("9100")
        (codecast_dir / "config.yaml").write_text("peers: {}")
        (codecast_dir / "tokens.yaml").write_text("tokens: []")

        args = parse_args(["uninstall", "--yes"])

        with patch("head.cli.Path.home", return_value=tmp_path):
            with patch("head.cli._stop_all_processes"):
                _cmd_uninstall(args)

        # Everything should be gone
        assert not codecast_dir.exists()

    def test_uninstall_keep_config(self, tmp_path, capsys):
        """_cmd_uninstall --keep-config preserves config files."""
        from head.cli import _cmd_uninstall, parse_args

        codecast_dir = tmp_path / ".codecast"
        codecast_dir.mkdir()
        (codecast_dir / "daemon").mkdir()
        (codecast_dir / "daemon" / "codecast-daemon").write_text("binary")
        (codecast_dir / "sessions.db").write_text("db")
        (codecast_dir / "config.yaml").write_text("peers: {}")
        (codecast_dir / "tokens.yaml").write_text("tokens: []")
        (codecast_dir / "daemon.yaml").write_text("port: 9100")

        args = parse_args(["uninstall", "--keep-config", "--yes"])

        with patch("head.cli.Path.home", return_value=tmp_path):
            with patch("head.cli._stop_all_processes"):
                _cmd_uninstall(args)

        # Config files should remain
        assert (codecast_dir / "config.yaml").exists()
        assert (codecast_dir / "tokens.yaml").exists()
        assert (codecast_dir / "daemon.yaml").exists()
        # Non-config files should be gone
        assert not (codecast_dir / "daemon").exists()
        assert not (codecast_dir / "sessions.db").exists()

    def test_uninstall_nonexistent_dir(self, tmp_path, capsys):
        """_cmd_uninstall handles missing ~/.codecast/ gracefully."""
        from head.cli import _cmd_uninstall, parse_args

        args = parse_args(["uninstall", "--yes"])

        with patch("head.cli.Path.home", return_value=tmp_path):
            _cmd_uninstall(args)

        output = capsys.readouterr().out
        assert "Nothing to remove" in output


class TestCompletion:
    def test_bash_completion_output(self, capsys):
        """_cmd_completion bash produces valid bash script."""
        from head.cli import _cmd_completion, parse_args

        args = parse_args(["completion", "bash"])
        _cmd_completion(args)

        output = capsys.readouterr().out
        assert "_codecast()" in output
        assert "complete -F _codecast codecast" in output
        assert "COMPREPLY" in output

    def test_zsh_completion_output(self, capsys):
        """_cmd_completion zsh produces valid zsh script."""
        from head.cli import _cmd_completion, parse_args

        args = parse_args(["completion", "zsh"])
        _cmd_completion(args)

        output = capsys.readouterr().out
        assert "#compdef codecast" in output
        assert "_codecast" in output
        assert "_arguments" in output

    def test_fish_completion_output(self, capsys):
        """_cmd_completion fish produces valid fish script."""
        from head.cli import _cmd_completion, parse_args

        args = parse_args(["completion", "fish"])
        _cmd_completion(args)

        output = capsys.readouterr().out
        assert "complete -c codecast" in output
        assert "__fish_seen_subcommand_from" in output

    def test_completion_includes_all_subcommands(self, capsys):
        """Completion scripts include all subcommands."""
        from head.cli import _cmd_completion, parse_args

        expected_commands = [
            "start",
            "stop",
            "restart",
            "status",
            "peers",
            "sessions",
            "token",
            "head",
            "webui",
            "update",
            "uninstall",
            "completion",
        ]

        for shell in ["bash", "zsh", "fish"]:
            args = parse_args(["completion", shell])
            _cmd_completion(args)
            output = capsys.readouterr().out
            for cmd in expected_commands:
                assert cmd in output, f"'{cmd}' missing from {shell} completion"


class TestHelpOutput:
    def test_help_shows_grouped_commands(self, capsys):
        """--help output includes grouped command sections."""
        from head.cli import parse_args

        with pytest.raises(SystemExit):
            parse_args(["--help"])
        output = capsys.readouterr().out
        assert "daemon commands:" in output
        assert "service commands:" in output
        assert "info commands:" in output
        assert "management:" in output


class TestStartVersionMismatch:
    def test_start_restarts_on_version_mismatch(self, capsys):
        """_cmd_start auto-restarts when daemon version != package version."""
        from head.cli import _cmd_start, parse_args

        args = parse_args(["start"])
        with (
            patch("head.cli._read_port_file", return_value=9100),
            patch("head.cli._daemon_healthy", return_value=True),
            patch("head.daemon_installer.get_current_version", return_value="0.2.16"),
            patch("head.daemon_installer.get_daemon_version", return_value="0.2.15"),
            patch("head.cli._cmd_stop") as mock_stop,
            patch("head.peer_manager.resolve_daemon_binary", return_value="/fake/daemon"),
            patch("subprocess.Popen") as mock_popen,
            patch("head.cli._CODECAST_DIR"),
            patch("head.cli._DAEMON_PID_FILE"),
        ):
            mock_popen.return_value.pid = 42
            _cmd_start(args)
            mock_stop.assert_called_once_with(args)

        output = capsys.readouterr().out
        assert "version mismatch" in output.lower()
        assert "0.2.15" in output
        assert "0.2.16" in output

    def test_start_no_restart_when_versions_match(self, capsys):
        """_cmd_start does NOT restart when versions match."""
        from head.cli import _cmd_start, parse_args

        args = parse_args(["start"])
        with (
            patch("head.cli._read_port_file", return_value=9100),
            patch("head.cli._daemon_healthy", return_value=True),
            patch("head.daemon_installer.get_current_version", return_value="0.2.16"),
            patch("head.daemon_installer.get_daemon_version", return_value="0.2.16"),
            patch("head.cli._cmd_stop") as mock_stop,
        ):
            _cmd_start(args)
            mock_stop.assert_not_called()

        output = capsys.readouterr().out
        assert "already running" in output.lower()

    def test_start_skips_check_when_version_unknown(self, capsys):
        """_cmd_start skips version check if either version is empty."""
        from head.cli import _cmd_start, parse_args

        args = parse_args(["start"])
        with (
            patch("head.cli._read_port_file", return_value=9100),
            patch("head.cli._daemon_healthy", return_value=True),
            patch("head.daemon_installer.get_current_version", return_value="0.2.16"),
            patch("head.daemon_installer.get_daemon_version", return_value=""),
            patch("head.cli._cmd_stop") as mock_stop,
        ):
            _cmd_start(args)
            mock_stop.assert_not_called()

        output = capsys.readouterr().out
        assert "already running" in output.lower()


class TestUpdateStopsDaemon:
    def test_update_stops_daemon_before_pull(self):
        """_cmd_update stops daemon before git pull."""
        from head.cli import _cmd_update, parse_args

        args = parse_args(["update"])
        call_order = []

        def mock_stop(a):
            call_order.append("stop")

        with (
            patch("head.cli._cmd_stop", side_effect=mock_stop),
            patch("subprocess.run") as mock_run,
            patch("os.execv") as mock_execv,
        ):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Already up to date.\n"
            _cmd_update(args)
            call_order.append("pull")

        assert call_order[0] == "stop"


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
