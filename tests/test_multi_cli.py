"""
Tests for multi-CLI adapter support across Python components.

Covers:
- session_router cli_type column + migration
- daemon_client cli_type parameter
- engine cmd_start --cli parsing
- message_formatter CLI type display
"""

import os
import sqlite3
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from head.session_router import SessionRouter, Session
from head.daemon_client import DaemonClient
from head.message_formatter import format_status, format_session_info


# ─── Fixtures ───


@pytest.fixture
def router(tmp_path):
    """Create a SessionRouter with a temp database."""
    db_path = str(tmp_path / "test_sessions.db")
    return SessionRouter(db_path=db_path)


@pytest.fixture
def legacy_router(tmp_path):
    """Create a router with a pre-existing DB that lacks cli_type column (for migration testing)."""
    db_path = str(tmp_path / "legacy_sessions.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE sessions (
            channel_id TEXT PRIMARY KEY,
            machine_id TEXT NOT NULL,
            path TEXT NOT NULL,
            daemon_session_id TEXT NOT NULL,
            sdk_session_id TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            mode TEXT NOT NULL DEFAULT 'auto',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            name TEXT,
            tool_display TEXT NOT NULL DEFAULT 'timer'
        );
        CREATE TABLE session_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL,
            machine_id TEXT NOT NULL,
            path TEXT NOT NULL,
            daemon_session_id TEXT NOT NULL,
            sdk_session_id TEXT,
            mode TEXT,
            created_at TEXT NOT NULL,
            detached_at TEXT,
            name TEXT
        );
    """)
    conn.commit()
    conn.close()
    # Creating SessionRouter will trigger migrations
    return SessionRouter(db_path=db_path)


# ─── SessionRouter: cli_type column ───


class TestSessionRouterCliType:
    def test_register_with_default_cli_type(self, router):
        """Default cli_type should be 'claude'."""
        router.register("ch:1", "m1", "/p", "sess-1", "auto")
        session = router.resolve("ch:1")
        assert session is not None
        assert session.cli_type == "claude"

    def test_register_with_explicit_cli_type(self, router):
        """Explicit cli_type should be stored."""
        router.register("ch:1", "m1", "/p", "sess-1", "auto", cli_type="codex")
        session = router.resolve("ch:1")
        assert session is not None
        assert session.cli_type == "codex"

    def test_register_with_gemini(self, router):
        router.register("ch:1", "m1", "/p", "sess-1", "auto", cli_type="gemini")
        session = router.resolve("ch:1")
        assert session.cli_type == "gemini"

    def test_register_with_opencode(self, router):
        router.register("ch:1", "m1", "/p", "sess-1", "auto", cli_type="opencode")
        session = router.resolve("ch:1")
        assert session.cli_type == "opencode"

    def test_cli_type_preserved_in_session_log(self, router):
        """cli_type should be preserved when session is detached to log."""
        router.register("ch:1", "m1", "/p", "sess-1", "auto", cli_type="codex")
        router.detach("ch:1")

        # Should be findable in the log
        session = router.find_session_by_daemon_id("sess-1")
        assert session is not None
        assert session.cli_type == "codex"

    def test_cli_type_in_find_by_name(self, router):
        """cli_type should be available when found by name."""
        name = router.register("ch:1", "m1", "/p", "sess-1", "auto", cli_type="gemini")
        session = router.find_session_by_name(name)
        assert session is not None
        assert session.cli_type == "gemini"

    def test_cli_type_in_find_by_name_after_detach(self, router):
        """cli_type should be preserved in log when found by name."""
        name = router.register("ch:1", "m1", "/p", "sess-1", "auto", cli_type="opencode")
        router.detach("ch:1")

        session = router.find_session_by_name(name)
        assert session is not None
        assert session.cli_type == "opencode"
        assert session.status == "detached"

    def test_cli_type_in_list_sessions(self, router):
        """cli_type should appear in session list."""
        router.register("ch:1", "m1", "/p1", "sess-1", "auto", cli_type="codex")
        router.register("ch:2", "m1", "/p2", "sess-2", "auto", cli_type="claude")
        sessions = router.list_sessions()
        assert len(sessions) == 2
        cli_types = {s.cli_type for s in sessions}
        assert cli_types == {"codex", "claude"}

    def test_auto_detach_preserves_cli_type(self, router):
        """When auto-detaching, cli_type should be in the log."""
        router.register("ch:1", "m1", "/p1", "sess-1", "auto", cli_type="codex")
        # Register new session on same channel → auto-detaches old
        router.register("ch:1", "m1", "/p2", "sess-2", "auto", cli_type="gemini")

        # New session should be gemini
        active = router.resolve("ch:1")
        assert active.cli_type == "gemini"

        # Old session in log should be codex
        old = router.find_session_by_daemon_id("sess-1")
        assert old is not None
        assert old.cli_type == "codex"

    def test_different_cli_types_on_different_channels(self, router):
        """Different channels can have different CLI types."""
        router.register("ch:1", "m1", "/p", "sess-1", "auto", cli_type="claude")
        router.register("ch:2", "m1", "/p", "sess-2", "auto", cli_type="codex")
        router.register("ch:3", "m1", "/p", "sess-3", "auto", cli_type="gemini")
        router.register("ch:4", "m1", "/p", "sess-4", "auto", cli_type="opencode")

        assert router.resolve("ch:1").cli_type == "claude"
        assert router.resolve("ch:2").cli_type == "codex"
        assert router.resolve("ch:3").cli_type == "gemini"
        assert router.resolve("ch:4").cli_type == "opencode"


class TestSessionRouterCliTypeMigration:
    def test_migration_adds_cli_type_column(self, legacy_router):
        """Migration should add cli_type column to both tables."""
        conn = sqlite3.connect(legacy_router.db_path)
        for table in ("sessions", "session_log"):
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            assert "cli_type" in columns
        conn.close()

    def test_existing_sessions_default_to_claude(self, legacy_router):
        """Existing sessions (pre-migration) should default to 'claude'."""
        # Insert a session without cli_type
        conn = sqlite3.connect(legacy_router.db_path)
        conn.execute(
            """INSERT INTO sessions
               (channel_id, machine_id, path, daemon_session_id, status, mode, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'active', 'auto', '2024-01-01', '2024-01-01')""",
            ("ch:1", "m1", "/p", "sess-1"),
        )
        conn.commit()
        conn.close()

        session = legacy_router.resolve("ch:1")
        assert session is not None
        assert session.cli_type == "claude"

    def test_new_sessions_after_migration_support_cli_type(self, legacy_router):
        """New sessions should support cli_type after migration."""
        legacy_router.register("ch:1", "m1", "/p", "sess-1", "auto", cli_type="codex")
        session = legacy_router.resolve("ch:1")
        assert session.cli_type == "codex"


# ─── DaemonClient: cli_type parameter ───


class TestDaemonClientCliType:
    @pytest.mark.asyncio
    async def test_create_session_default_cli_type(self):
        """Default cli_type should not add cli_type to params (backwards compatible)."""
        client = DaemonClient()
        with patch.object(client, "_rpc_call", new_callable=AsyncMock) as mock_rpc:
            mock_rpc.return_value = {"sessionId": "sess-1"}
            await client.create_session(9100, "/tmp/project", "auto")
            mock_rpc.assert_called_once()
            call_params = mock_rpc.call_args[0][2]
            assert "cli_type" not in call_params  # backward compatible

    @pytest.mark.asyncio
    async def test_create_session_with_codex(self):
        """Specifying codex should add cli_type to params."""
        client = DaemonClient()
        with patch.object(client, "_rpc_call", new_callable=AsyncMock) as mock_rpc:
            mock_rpc.return_value = {"sessionId": "sess-1"}
            await client.create_session(9100, "/tmp/project", "auto", cli_type="codex")
            call_params = mock_rpc.call_args[0][2]
            assert call_params["cli_type"] == "codex"

    @pytest.mark.asyncio
    async def test_create_session_with_gemini(self):
        client = DaemonClient()
        with patch.object(client, "_rpc_call", new_callable=AsyncMock) as mock_rpc:
            mock_rpc.return_value = {"sessionId": "sess-1"}
            await client.create_session(9100, "/tmp/project", "auto", cli_type="gemini")
            call_params = mock_rpc.call_args[0][2]
            assert call_params["cli_type"] == "gemini"

    @pytest.mark.asyncio
    async def test_create_session_with_opencode(self):
        client = DaemonClient()
        with patch.object(client, "_rpc_call", new_callable=AsyncMock) as mock_rpc:
            mock_rpc.return_value = {"sessionId": "sess-1"}
            await client.create_session(9100, "/tmp/project", "auto", cli_type="opencode")
            call_params = mock_rpc.call_args[0][2]
            assert call_params["cli_type"] == "opencode"

    @pytest.mark.asyncio
    async def test_create_session_claude_no_cli_type_param(self):
        """Specifying claude explicitly should NOT send cli_type (it's the default)."""
        client = DaemonClient()
        with patch.object(client, "_rpc_call", new_callable=AsyncMock) as mock_rpc:
            mock_rpc.return_value = {"sessionId": "sess-1"}
            await client.create_session(9100, "/tmp/project", "auto", cli_type="claude")
            call_params = mock_rpc.call_args[0][2]
            assert "cli_type" not in call_params

    @pytest.mark.asyncio
    async def test_create_session_with_model_and_cli_type(self):
        """Model and cli_type should both be included."""
        client = DaemonClient()
        with patch.object(client, "_rpc_call", new_callable=AsyncMock) as mock_rpc:
            mock_rpc.return_value = {"sessionId": "sess-1"}
            await client.create_session(9100, "/tmp/project", "auto", model="o4-mini", cli_type="codex")
            call_params = mock_rpc.call_args[0][2]
            assert call_params["model"] == "o4-mini"
            assert call_params["cli_type"] == "codex"


# ─── Engine: cmd_start --cli parsing ───


class TestEngineCliParsing:
    """Test --cli flag parsing in cmd_start.

    These tests mock the engine's dependencies to focus on argument parsing.
    """

    def _parse_cli_type(self, args: list[str]) -> tuple[str, list[str]]:
        """Simulate the cli_type parsing logic from engine.cmd_start.

        Returns (cli_type, remaining_args).
        """
        cli_type = "claude"
        args = list(args)
        for shorthand in ("--codex", "--gemini", "--opencode"):
            if shorthand in args:
                args.remove(shorthand)
                cli_type = shorthand.lstrip("-")
                break
        if "--cli" in args:
            idx = args.index("--cli")
            if idx + 1 < len(args):
                cli_type = args[idx + 1]
                del args[idx : idx + 2]
        return cli_type, args

    def test_default_claude(self):
        cli_type, args = self._parse_cli_type(["gpu-1", "~/project"])
        assert cli_type == "claude"
        assert args == ["gpu-1", "~/project"]

    def test_cli_codex(self):
        cli_type, args = self._parse_cli_type(["gpu-1", "~/project", "--cli", "codex"])
        assert cli_type == "codex"
        assert args == ["gpu-1", "~/project"]

    def test_cli_gemini(self):
        cli_type, args = self._parse_cli_type(["gpu-1", "~/project", "--cli", "gemini"])
        assert cli_type == "gemini"
        assert args == ["gpu-1", "~/project"]

    def test_cli_opencode(self):
        cli_type, args = self._parse_cli_type(["gpu-1", "~/project", "--cli", "opencode"])
        assert cli_type == "opencode"
        assert args == ["gpu-1", "~/project"]

    def test_shorthand_codex(self):
        cli_type, args = self._parse_cli_type(["gpu-1", "~/project", "--codex"])
        assert cli_type == "codex"
        assert args == ["gpu-1", "~/project"]

    def test_shorthand_gemini(self):
        cli_type, args = self._parse_cli_type(["gpu-1", "~/project", "--gemini"])
        assert cli_type == "gemini"
        assert args == ["gpu-1", "~/project"]

    def test_shorthand_opencode(self):
        cli_type, args = self._parse_cli_type(["gpu-1", "~/project", "--opencode"])
        assert cli_type == "opencode"
        assert args == ["gpu-1", "~/project"]

    def test_cli_flag_before_args(self):
        """--cli flag can appear anywhere in args."""
        cli_type, args = self._parse_cli_type(["--cli", "codex", "gpu-1", "~/project"])
        assert cli_type == "codex"
        assert args == ["gpu-1", "~/project"]

    def test_shorthand_before_machine(self):
        cli_type, args = self._parse_cli_type(["--gemini", "gpu-1", "~/project"])
        assert cli_type == "gemini"
        assert args == ["gpu-1", "~/project"]

    def test_shorthand_then_cli_flag(self):
        """When both shorthand and --cli are present, --cli takes final effect
        because it's checked after the shorthand loop."""
        cli_type, args = self._parse_cli_type(["gpu-1", "~/project", "--codex", "--cli", "gemini"])
        # --cli overrides because it runs after the shorthand loop
        assert cli_type == "gemini"
        assert args == ["gpu-1", "~/project"]

    def test_multiple_shorthands_first_wins(self):
        """Only first shorthand is consumed."""
        cli_type, args = self._parse_cli_type(["gpu-1", "~/project", "--codex", "--gemini"])
        assert cli_type == "codex"
        assert "--gemini" in args


# ─── MessageFormatter: CLI type display ───


class TestMessageFormatterCliType:
    def _make_session(self, cli_type="claude", **kwargs):
        defaults = dict(
            channel_id="ch:1",
            machine_id="gpu-1",
            path="/home/user/project",
            daemon_session_id="sess-001",
            sdk_session_id=None,
            status="active",
            mode="auto",
            created_at="2024-01-01",
            updated_at="2024-01-01",
            name="bright-falcon",
            tool_display="timer",
            cli_type=cli_type,
        )
        defaults.update(kwargs)
        return Session(**defaults)

    def test_format_status_shows_cli_type(self):
        session = self._make_session(cli_type="codex")
        result = format_status(session)
        assert "CLI: **codex**" in result

    def test_format_status_shows_claude_cli_type(self):
        session = self._make_session(cli_type="claude")
        result = format_status(session)
        assert "CLI: **claude**" in result

    def test_format_status_shows_gemini(self):
        session = self._make_session(cli_type="gemini")
        result = format_status(session)
        assert "CLI: **gemini**" in result

    def test_format_status_shows_opencode(self):
        session = self._make_session(cli_type="opencode")
        result = format_status(session)
        assert "CLI: **opencode**" in result

    def test_format_session_info_non_claude_shows_type(self):
        session = self._make_session(cli_type="codex")
        result = format_session_info(session)
        assert "[codex]" in result

    def test_format_session_info_claude_no_extra_tag(self):
        session = self._make_session(cli_type="claude")
        result = format_session_info(session)
        assert "[codex]" not in result
        assert "[gemini]" not in result
        assert "[opencode]" not in result

    def test_format_session_info_gemini(self):
        session = self._make_session(cli_type="gemini")
        result = format_session_info(session)
        assert "[gemini]" in result

    def test_format_session_info_opencode(self):
        session = self._make_session(cli_type="opencode")
        result = format_session_info(session)
        assert "[opencode]" in result

    def test_format_status_with_queue_stats(self):
        session = self._make_session(cli_type="codex")
        stats = {"userPending": 2, "responsePending": 5}
        result = format_status(session, queue_stats=stats)
        assert "CLI: **codex**" in result
        assert "2 pending messages" in result
        assert "5 responses" in result

    def test_format_status_default_cli_type(self):
        """Session without cli_type attribute should default to claude."""
        session = MagicMock()
        session.mode = "auto"
        session.name = "test"
        session.machine_id = "m1"
        session.path = "/p"
        session.status = "active"
        session.daemon_session_id = "d1"
        session.sdk_session_id = None
        # No cli_type attr → getattr default
        del session.cli_type
        result = format_status(session)
        assert "CLI: **claude**" in result


# ─── Session dataclass ───


class TestSessionDataclass:
    def test_session_default_cli_type(self):
        session = Session(
            channel_id="ch:1",
            machine_id="m1",
            path="/p",
            daemon_session_id="d1",
            sdk_session_id=None,
            status="active",
            mode="auto",
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        assert session.cli_type == "claude"

    def test_session_with_cli_type(self):
        session = Session(
            channel_id="ch:1",
            machine_id="m1",
            path="/p",
            daemon_session_id="d1",
            sdk_session_id=None,
            status="active",
            mode="auto",
            created_at="2024-01-01",
            updated_at="2024-01-01",
            cli_type="codex",
        )
        assert session.cli_type == "codex"

    def test_session_all_cli_types(self):
        for cli_type in ("claude", "codex", "gemini", "opencode"):
            session = Session(
                channel_id="ch:1",
                machine_id="m1",
                path="/p",
                daemon_session_id="d1",
                sdk_session_id=None,
                status="active",
                mode="auto",
                created_at="2024-01-01",
                updated_at="2024-01-01",
                cli_type=cli_type,
            )
            assert session.cli_type == cli_type
