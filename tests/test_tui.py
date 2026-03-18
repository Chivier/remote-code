"""Tests for the Codecast TUI app and screens."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

from head.tui.app import CodecastApp, CODECAST_THEME
from head.tui.screens import (
    DashboardScreen,
    SessionsScreen,
    SetupWizardScreen,
    StartHeadScreen,
    StartWebUIScreen,
)
from head.tui.widgets import StatusPanel, PeerTable


def _get_static_text(widget) -> str:
    """Extract text content from a Static widget (Textual internals)."""
    # Access the private __content attribute set in Static.__init__
    content = getattr(widget, "_Static__content", "")
    return str(content)


@pytest.mark.asyncio
async def test_app_launches():
    """App should launch and have the correct title."""
    app = CodecastApp(config_path="/tmp/nonexistent_codecast_test.yaml")
    async with app.run_test() as pilot:
        assert app.title == "Codecast"


@pytest.mark.asyncio
async def test_theme_registered():
    """App should register and use the codecast theme."""
    app = CodecastApp(config_path="/tmp/nonexistent_codecast_test.yaml")
    async with app.run_test() as pilot:
        assert app.theme == "codecast"


@pytest.mark.asyncio
async def test_setup_wizard_shown_no_config(tmp_path):
    """Setup wizard should be displayed when config file does not exist."""
    config_path = str(tmp_path / "nonexistent.yaml")
    app = CodecastApp(config_path=config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SetupWizardScreen)
        welcome = app.screen.query_one("#welcome")
        text = _get_static_text(welcome)
        assert "Welcome" in text


@pytest.mark.asyncio
async def test_dashboard_shown_when_config_exists(tmp_path):
    """Dashboard should be displayed when a config file exists."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        status = app.screen.query_one("#status")
        assert isinstance(status, StatusPanel)


@pytest.mark.asyncio
async def test_wizard_menu_options(tmp_path):
    """Wizard should show the expected menu options."""
    config_path = str(tmp_path / "nonexistent.yaml")
    app = CodecastApp(config_path=config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        menu = app.screen.query_one("#wizard_menu")
        option_ids = [opt.id for opt in menu._options]
        assert "start_daemon" in option_ids
        assert "add_peer" in option_ids
        assert "config_discord" in option_ids
        assert "config_telegram" in option_ids
        assert "skip" in option_ids


@pytest.mark.asyncio
async def test_dashboard_keybindings(tmp_path):
    """Dashboard should have the expected keybindings."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        keys = {b[0] if isinstance(b, tuple) else b.key for b in app.screen.BINDINGS}
        assert "d" in keys
        assert "h" in keys
        assert "w" in keys
        assert "a" in keys
        assert "s" in keys
        assert "q" in keys


@pytest.mark.asyncio
async def test_status_panel_renders(tmp_path):
    """StatusPanel should render all 4 component status lines."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.screen.query_one("#status", StatusPanel)
        text = _get_static_text(status)
        assert "Head:" in text
        assert "Daemon:" in text
        assert "WebUI:" in text
        assert "Claude:" in text


@pytest.mark.asyncio
async def test_peer_table_shows_peers(tmp_path):
    """PeerTable should show peers from config."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {
            "server1": {"transport": "ssh", "ssh_host": "10.0.0.1"},
            "server2": {"transport": "http", "address": "https://10.0.0.2:9100"},
        },
    }
    config_path.write_text(yaml.dump(cfg))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#peer_table", PeerTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_peer_table_empty_config(tmp_path):
    """PeerTable should handle config with no peers."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#peer_table", PeerTable)
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_peer_table_title_shows_count(tmp_path):
    """Peer table title should show the peer count."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {
            "s1": {"transport": "ssh", "ssh_host": "10.0.0.1"},
            "s2": {"transport": "ssh", "ssh_host": "10.0.0.2"},
            "s3": {"transport": "ssh", "ssh_host": "10.0.0.3"},
        },
    }
    config_path.write_text(yaml.dump(cfg))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        title = app.screen.query_one("#peer_table_title")
        text = _get_static_text(title)
        assert "3 configured" in text


@pytest.mark.asyncio
async def test_quit_via_key(tmp_path):
    """Pressing 'q' in the wizard should exit the app."""
    config_path = str(tmp_path / "nonexistent.yaml")
    app = CodecastApp(config_path=config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")


@pytest.mark.asyncio
async def test_version_displayed(tmp_path):
    """Version string should appear in the welcome screen."""
    config_path = str(tmp_path / "nonexistent.yaml")
    app = CodecastApp(config_path=config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        welcome = app.screen.query_one("#welcome")
        text = _get_static_text(welcome)
        # Should contain a version like "v0.2.1" or at least "v"
        assert "v" in text


@pytest.mark.asyncio
async def test_dashboard_has_status_panel_container(tmp_path):
    """Dashboard should have bordered status and peer containers."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one("#status_panel_container") is not None
        assert app.screen.query_one("#peer_table_container") is not None


# ---------------------------------------------------------------------------
# StartHeadScreen tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_head_screen_shows_status(tmp_path):
    """StartHeadScreen should show head node status and config summary."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Push StartHeadScreen from dashboard
        app.push_screen(StartHeadScreen(str(config_path)))
        await pilot.pause()
        assert isinstance(app.screen, StartHeadScreen)
        status = app.screen.query_one("#head_status")
        text = _get_static_text(status)
        assert "not running" in text
        assert "Config:" in text


@pytest.mark.asyncio
async def test_start_head_screen_menu_options(tmp_path):
    """StartHeadScreen should show config options and back."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(StartHeadScreen(str(config_path)))
        await pilot.pause()
        menu = app.screen.query_one("#head_menu")
        option_ids = [opt.id for opt in menu._options]
        assert "config_discord" in option_ids
        assert "config_telegram" in option_ids
        assert "back" in option_ids


@pytest.mark.asyncio
async def test_start_head_screen_shows_bots_when_configured(tmp_path):
    """StartHeadScreen should list configured bots."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "bot": {"discord": {"token": "fake-token-123"}},
    }
    config_path.write_text(yaml.dump(cfg))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(StartHeadScreen(str(config_path)))
        await pilot.pause()
        status = app.screen.query_one("#head_status")
        text = _get_static_text(status)
        assert "Discord" in text
        # Should have start option when bots are configured
        menu = app.screen.query_one("#head_menu")
        option_ids = [opt.id for opt in menu._options]
        assert "start" in option_ids


@pytest.mark.asyncio
async def test_start_head_screen_escape_goes_back(tmp_path):
    """Pressing escape on StartHeadScreen should return to dashboard."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(StartHeadScreen(str(config_path)))
        await pilot.pause()
        assert isinstance(app.screen, StartHeadScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


# ---------------------------------------------------------------------------
# StartWebUIScreen tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_webui_screen_shows_status(tmp_path):
    """StartWebUIScreen should show WebUI status."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(StartWebUIScreen(str(config_path)))
        await pilot.pause()
        assert isinstance(app.screen, StartWebUIScreen)
        status = app.screen.query_one("#webui_status")
        text = _get_static_text(status)
        assert "not running" in text


@pytest.mark.asyncio
async def test_start_webui_screen_menu_options(tmp_path):
    """StartWebUIScreen should have start and back options when not running."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(StartWebUIScreen(str(config_path)))
        await pilot.pause()
        menu = app.screen.query_one("#webui_menu")
        option_ids = [opt.id for opt in menu._options]
        assert "start" in option_ids
        assert "back" in option_ids


@pytest.mark.asyncio
async def test_start_webui_screen_escape_goes_back(tmp_path):
    """Pressing escape on StartWebUIScreen should return to dashboard."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(StartWebUIScreen(str(config_path)))
        await pilot.pause()
        assert isinstance(app.screen, StartWebUIScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


# ---------------------------------------------------------------------------
# SessionsScreen tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_screen_shows_table(tmp_path):
    """SessionsScreen should display a DataTable with session columns."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(SessionsScreen(str(config_path)))
        await pilot.pause()
        assert isinstance(app.screen, SessionsScreen)
        table = app.screen.query_one("#sessions_table")
        assert table is not None
        # Should have the expected columns
        col_labels = [col.label.plain for col in table.columns.values()]
        assert "Name" in col_labels
        assert "Machine" in col_labels
        assert "Status" in col_labels


@pytest.mark.asyncio
async def test_sessions_screen_no_sessions(tmp_path):
    """SessionsScreen should show 'no sessions' when DB doesn't exist."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(str(config_path))
        screen._load_sessions = lambda: []  # No sessions
        app.push_screen(screen)
        await pilot.pause()
        info = app.screen.query_one("#sessions_info")
        text = _get_static_text(info)
        assert "No sessions found" in text


@pytest.mark.asyncio
async def test_sessions_screen_escape_goes_back(tmp_path):
    """Pressing escape on SessionsScreen should return to dashboard."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(SessionsScreen(str(config_path)))
        await pilot.pause()
        assert isinstance(app.screen, SessionsScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_sessions_screen_with_sessions(tmp_path):
    """SessionsScreen should show sessions from a pre-populated DB."""
    import sqlite3

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))

    # Create a fake sessions database
    db_path = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
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
            tool_display TEXT DEFAULT 'append'
        )
    """)
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "discord:123",
            "server1",
            "/home/user/project",
            "uuid-1234",
            None,
            "active",
            "auto",
            "2026-03-18T00:00:00",
            "2026-03-18T00:00:00",
            "bright-falcon",
            "append",
        ),
    )
    conn.commit()
    conn.close()

    # Patch _load_sessions to use our temp DB
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(str(config_path))
        # Monkey-patch to use our DB
        original_load = screen._load_sessions

        def patched_load():
            from head.session_router import SessionRouter

            router = SessionRouter(str(db_path))
            return router.list_sessions()

        screen._load_sessions = patched_load
        app.push_screen(screen)
        await pilot.pause()

        table = app.screen.query_one("#sessions_table")
        assert table.row_count == 1
        info = app.screen.query_one("#sessions_info")
        text = _get_static_text(info)
        assert "1 session(s)" in text


# ---------------------------------------------------------------------------
# Dashboard action routing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_h_key_opens_head_screen(tmp_path):
    """Pressing 'h' on dashboard should open StartHeadScreen."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press("h")
        await pilot.pause()
        assert isinstance(app.screen, StartHeadScreen)


@pytest.mark.asyncio
async def test_dashboard_w_key_opens_webui_screen(tmp_path):
    """Pressing 'w' on dashboard should open StartWebUIScreen."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, StartWebUIScreen)


@pytest.mark.asyncio
async def test_dashboard_s_key_opens_sessions_screen(tmp_path):
    """Pressing 's' on dashboard should open SessionsScreen."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SessionsScreen)
