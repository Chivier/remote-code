"""Tests for head.cli – CLI entry point with subcommand dispatch."""

import pytest


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
