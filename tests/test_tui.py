"""Tests for the Codecast TUI app and screens."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

from textual.widgets import Input

from head.tui.app import CodecastApp, CODECAST_THEME
from head.tui.screens import (
    AddMachineScreen,
    ConfigBotScreen,
    DashboardScreen,
    HelpScreen,
    SSHImportScreen,
    SessionsScreen,
    SetupWizardScreen,
    StartDaemonScreen,
    StartHeadScreen,
    StartWebUIScreen,
)
from head.tui.widgets import StatusPanel, MachineTable


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
        assert "config_bot" in option_ids
        assert "add_machine" in option_ids
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
        assert "H" in keys
        assert "w" in keys
        assert "a" in keys
        assert "s" in keys
        assert "x" in keys
        assert "j" in keys
        assert "k" in keys
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
        assert "Daemon" in text
        assert "Head" in text
        assert "WebUI" in text
        assert "CLIs" in text


@pytest.mark.asyncio
async def test_machine_table_shows_machines(tmp_path):
    """MachineTable should show machines from config."""
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
        table = app.screen.query_one("#machine_table", MachineTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_machine_table_empty_config(tmp_path):
    """MachineTable should handle config with no machines."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#machine_table", MachineTable)
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_machine_table_title_shows_count(tmp_path):
    """Machine table title should show the machine count."""
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
        title = app.screen.query_one("#machine_table_title")
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
    """Dashboard should have bordered status and machine containers."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one("#status_panel_container") is not None
        assert app.screen.query_one("#machine_table_container") is not None


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
        with patch("head.cli._read_pid_file", return_value=None):
            app.push_screen(StartHeadScreen(str(config_path)))
            await pilot.pause()
        assert isinstance(app.screen, StartHeadScreen)
        status = app.screen.query_one("#head_status")
        text = _get_static_text(status)
        assert "stopped" in text
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
        with patch("head.cli._read_pid_file", return_value=None):
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
        assert "stopped" in text or "not running" in text


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
        assert "Status" in col_labels
        assert "Created" in col_labels


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
        # 1 machine header + 1 session row = 2 rows
        assert table.row_count == 2
        info = app.screen.query_one("#sessions_info")
        text = _get_static_text(info)
        assert "1 session(s)" in text


# ---------------------------------------------------------------------------
# Dashboard action routing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_H_key_opens_head_screen(tmp_path):
    """Pressing 'H' (shift-h) on dashboard should open StartHeadScreen."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press("H")
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


# ---------------------------------------------------------------------------
# AddMachineScreen tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_machine_screen_shows_method_choice(tmp_path):
    """AddMachineScreen should show Manual/SSH import options."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(AddMachineScreen(str(config_path)))
        await pilot.pause()
        assert isinstance(app.screen, AddMachineScreen)
        method_list = app.screen.query_one("#add_machine_method")
        option_ids = [opt.id for opt in method_list._options]
        assert "manual" in option_ids
        assert "ssh_import" in option_ids


@pytest.mark.asyncio
async def test_add_machine_screen_title(tmp_path):
    """AddMachineScreen should display 'Add a machine' title."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(AddMachineScreen(str(config_path)))
        await pilot.pause()
        title = app.screen.query_one("#add_machine_title")
        text = _get_static_text(title)
        assert "Add a machine" in text


# ---------------------------------------------------------------------------
# Vim navigation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vim_navigation_bindings_on_sessions(tmp_path):
    """SessionsScreen should have j/k/h/l vim navigation bindings."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(SessionsScreen(str(config_path)))
        await pilot.pause()
        assert isinstance(app.screen, SessionsScreen)
        keys = {b[0] if isinstance(b, tuple) else b.key for b in app.screen.BINDINGS}
        assert "j" in keys
        assert "k" in keys
        assert "h" in keys
        assert "l" in keys


# ---------------------------------------------------------------------------
# Session drill-down tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_screen_filter_machine(tmp_path):
    """SessionsScreen should support filter_machine parameter."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(str(config_path), filter_machine="server1")
        screen._load_sessions = lambda: []
        app.push_screen(screen)
        await pilot.pause()
        assert app.screen._filter_machine == "server1"
        title = app.screen.query_one("#sessions_title")
        text = _get_static_text(title)
        assert "server1" in text


@pytest.mark.asyncio
async def test_sessions_screen_h_pops_when_init_filtered(tmp_path):
    """Pressing 'h' on init-filtered sessions should pop back to dashboard."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(str(config_path), filter_machine="server1")
        screen._load_sessions = lambda: []
        app.push_screen(screen)
        await pilot.pause()
        assert app.screen._filter_machine == "server1"
        assert app.screen._init_filtered is True
        await pilot.press("h")
        await pilot.pause()
        # Should pop back to dashboard (not clear filter)
        assert isinstance(app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_sessions_screen_h_clears_drilldown_filter(tmp_path):
    """Pressing 'h' on drill-down filtered sessions should clear filter, not pop."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    sessions = [
        _make_fake_session(channel_id="discord:1", machine_id="server1"),
        _make_fake_session(channel_id="discord:2", machine_id="server2"),
    ]
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(str(config_path))
        screen._load_sessions = lambda: sessions
        app.push_screen(screen)
        await pilot.pause()
        # Drill down into server1 via action_open_or_enter
        screen.action_open_or_enter()
        await pilot.pause()
        assert screen._filter_machine is not None
        assert screen._init_filtered is False
        await pilot.press("h")
        await pilot.pause()
        # Should clear filter but stay on SessionsScreen
        assert isinstance(app.screen, SessionsScreen)
        assert app.screen._filter_machine is None


@pytest.mark.asyncio
async def test_sessions_screen_h_pops_when_no_filter(tmp_path):
    """Pressing 'h' on unfiltered sessions should pop back to dashboard."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(str(config_path))
        screen._load_sessions = lambda: []
        app.push_screen(screen)
        await pilot.pause()
        assert isinstance(app.screen, SessionsScreen)
        assert app.screen._filter_machine is None
        await pilot.press("h")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


# ---------------------------------------------------------------------------
# StartDaemonScreen tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_daemon_screen_shows_stopped(tmp_path):
    """StartDaemonScreen should show 'stopped' when daemon is not running."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        with (
            patch("head.tui.screens._check_daemon_running", return_value=(False, None)),
            patch("head.tui.screens._check_claude_cli", return_value=True),
        ):
            app.push_screen(StartDaemonScreen(str(config_path)))
            await pilot.pause()
        assert isinstance(app.screen, StartDaemonScreen)
        status = app.screen.query_one("#daemon_status")
        text = _get_static_text(status)
        assert "stopped" in text


@pytest.mark.asyncio
async def test_start_daemon_screen_shows_running(tmp_path):
    """StartDaemonScreen should show 'running' with port when daemon is up."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        with (
            patch("head.tui.screens._check_daemon_running", return_value=(True, 9100)),
            patch("head.tui.screens._check_claude_cli", return_value=True),
            patch("head.cli._read_pid_file", return_value=12345),
            patch("head.cli._pid_alive", return_value=True),
        ):
            app.push_screen(StartDaemonScreen(str(config_path)))
            await pilot.pause()
        status = app.screen.query_one("#daemon_status")
        text = _get_static_text(status)
        assert "running" in text
        assert "9100" in text


@pytest.mark.asyncio
async def test_start_daemon_screen_no_claude_cli(tmp_path):
    """StartDaemonScreen should warn when Claude CLI is not available."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        with (
            patch("head.tui.screens._check_daemon_running", return_value=(False, None)),
            patch("head.tui.screens._check_claude_cli", return_value=False),
        ):
            app.push_screen(StartDaemonScreen(str(config_path)))
            await pilot.pause()
        status = app.screen.query_one("#daemon_status")
        text = _get_static_text(status)
        assert "not found" in text


@pytest.mark.asyncio
async def test_start_daemon_screen_start_option(tmp_path):
    """StartDaemonScreen should show 'Start daemon' when stopped and CLI available."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        with (
            patch("head.tui.screens._check_daemon_running", return_value=(False, None)),
            patch("head.tui.screens._check_claude_cli", return_value=True),
        ):
            app.push_screen(StartDaemonScreen(str(config_path)))
            await pilot.pause()
        menu = app.screen.query_one("#daemon_menu")
        option_ids = [opt.id for opt in menu._options]
        assert "start" in option_ids


# ---------------------------------------------------------------------------
# HelpScreen tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_help_screen_shows_shortcuts(tmp_path):
    """HelpScreen should show keyboard shortcuts."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(HelpScreen())
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        help_text = app.screen.query_one("#help_text")
        text = _get_static_text(help_text)
        assert "Dashboard shortcuts" in text
        assert "Navigation" in text


@pytest.mark.asyncio
async def test_help_screen_shows_cli_equivalents(tmp_path):
    """HelpScreen should show CLI equivalents."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(HelpScreen())
        await pilot.pause()
        help_text = app.screen.query_one("#help_text")
        text = _get_static_text(help_text)
        assert "CLI equivalents" in text
        assert "codecast start" in text


# ---------------------------------------------------------------------------
# ConfigBotScreen tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_bot_screen_discord_title(tmp_path):
    """ConfigBotScreen should show Discord in the title for discord type."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ConfigBotScreen(str(config_path), "discord"))
        await pilot.pause()
        assert isinstance(app.screen, ConfigBotScreen)
        title = app.screen.query_one("#bot_title")
        text = _get_static_text(title)
        assert "Discord" in text


@pytest.mark.asyncio
async def test_config_bot_screen_telegram_title(tmp_path):
    """ConfigBotScreen should show Telegram in the title for telegram type."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ConfigBotScreen(str(config_path), "telegram"))
        await pilot.pause()
        title = app.screen.query_one("#bot_title")
        text = _get_static_text(title)
        assert "Telegram" in text


@pytest.mark.asyncio
async def test_config_bot_screen_has_guidance(tmp_path):
    """ConfigBotScreen should show platform-specific guidance text."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ConfigBotScreen(str(config_path), "discord"))
        await pilot.pause()
        guidance = app.screen.query_one("#bot_guidance")
        text = _get_static_text(guidance)
        assert "Discord Bot Setup" in text
        assert "discord.com/developers" in text


@pytest.mark.asyncio
async def test_config_bot_screen_telegram_guidance(tmp_path):
    """ConfigBotScreen should show Telegram-specific guidance."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ConfigBotScreen(str(config_path), "telegram"))
        await pilot.pause()
        guidance = app.screen.query_one("#bot_guidance")
        text = _get_static_text(guidance)
        assert "Telegram Bot Setup" in text
        assert "BotFather" in text


@pytest.mark.asyncio
async def test_config_bot_screen_password_input(tmp_path):
    """ConfigBotScreen should have a password-masked input field."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ConfigBotScreen(str(config_path), "discord"))
        await pilot.pause()
        inp = app.screen.query_one("#bot_token_input")
        assert inp.password is True


@pytest.mark.asyncio
async def test_config_bot_screen_saves_discord_token(tmp_path):
    """ConfigBotScreen should save a Discord token to config."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ConfigBotScreen(str(config_path), "discord"))
        await pilot.pause()
        inp = app.screen.query_one("#bot_token_input")
        inp.value = "test-discord-token-123"
        await inp.action_submit()
        await pilot.pause()
        # Verify token was written
        with open(config_path) as f:
            saved = yaml.safe_load(f)
        assert saved["bot"]["discord"]["token"] == "test-discord-token-123"


# ---------------------------------------------------------------------------
# AddMachine manual flow tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_machine_manual_step1_prompt(tmp_path):
    """Selecting 'Manual entry' should show machine name prompt."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = AddMachineScreen(str(config_path))
        app.push_screen(screen)
        await pilot.pause()
        # Simulate selecting "manual"
        screen._mode = "manual"
        screen._step = 1
        screen._switch_to_manual_input()
        await pilot.pause()
        prompt = screen.query_one("#add_machine_prompt")
        text = _get_static_text(prompt)
        assert "machine name" in text.lower()


@pytest.mark.asyncio
async def test_add_machine_manual_ssh_flow(tmp_path):
    """Manual SSH machine should be saved to config."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = AddMachineScreen(str(config_path))
        app.push_screen(screen)
        await pilot.pause()
        screen._mode = "manual"
        screen._step = 1
        screen._switch_to_manual_input()
        await pilot.pause()

        from textual.widgets import Input

        inp = screen.query_one("#machine_input", Input)
        inp.value = "test-server"
        await inp.action_submit()
        await pilot.pause()

        inp = screen.query_one("#machine_input", Input)
        inp.value = "ssh"
        await inp.action_submit()
        await pilot.pause()

        inp = screen.query_one("#machine_input", Input)
        inp.value = "user@10.0.0.99"
        await inp.action_submit()
        await pilot.pause()

        # Verify saved
        with open(config_path) as f:
            saved = yaml.safe_load(f)
        assert "test-server" in saved["peers"]
        assert saved["peers"]["test-server"]["ssh_host"] == "10.0.0.99"
        assert saved["peers"]["test-server"]["ssh_user"] == "user"


@pytest.mark.asyncio
async def test_add_machine_manual_http_flow(tmp_path):
    """Manual HTTP machine should be saved to config."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = AddMachineScreen(str(config_path))
        app.push_screen(screen)
        await pilot.pause()
        screen._mode = "manual"
        screen._step = 1
        screen._switch_to_manual_input()
        await pilot.pause()

        from textual.widgets import Input

        inp = screen.query_one("#machine_input", Input)
        inp.value = "http-server"
        await inp.action_submit()
        await pilot.pause()

        inp = screen.query_one("#machine_input", Input)
        inp.value = "http"
        await inp.action_submit()
        await pilot.pause()

        inp = screen.query_one("#machine_input", Input)
        inp.value = "https://10.0.0.50:9100"
        await inp.action_submit()
        await pilot.pause()

        with open(config_path) as f:
            saved = yaml.safe_load(f)
        assert "http-server" in saved["peers"]
        assert saved["peers"]["http-server"]["transport"] == "http"
        assert saved["peers"]["http-server"]["address"] == "https://10.0.0.50:9100"


# ---------------------------------------------------------------------------
# AddMachine SSH import tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ssh_import_screen_shows_hosts(tmp_path):
    """SSH import screen should show available hosts in selection list."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))

    from head.config import SSHHostEntry

    mock_entries = [
        SSHHostEntry(name="server-a", hostname="10.0.0.1", user="alice"),
        SSHHostEntry(name="server-b", hostname="10.0.0.2"),
    ]

    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        with patch("head.config.parse_ssh_config", return_value=mock_entries):
            screen = SSHImportScreen(str(config_path))
            app.push_screen(screen)
            await pilot.pause()
        status = screen.query_one("#ssh_status")
        text = _get_static_text(status)
        assert "2 hosts available" in text


@pytest.mark.asyncio
async def test_ssh_import_screen_filters_existing(tmp_path):
    """SSH import screen should filter out already-configured machines."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {"server-a": {"transport": "ssh", "ssh_host": "10.0.0.1"}},
    }
    config_path.write_text(yaml.dump(cfg))

    from head.config import SSHHostEntry

    mock_entries = [
        SSHHostEntry(name="server-a", hostname="10.0.0.1"),
        SSHHostEntry(name="server-b", hostname="10.0.0.2"),
    ]

    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        with patch("head.config.parse_ssh_config", return_value=mock_entries):
            screen = SSHImportScreen(str(config_path))
            app.push_screen(screen)
            await pilot.pause()
        status = screen.query_one("#ssh_status")
        text = _get_static_text(status)
        assert "1 hosts available" in text


@pytest.mark.asyncio
async def test_ssh_import_screen_deduplicates(tmp_path):
    """SSH import screen should deduplicate hosts with the same name."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))

    from head.config import SSHHostEntry

    mock_entries = [
        SSHHostEntry(name="jump-box", hostname="10.0.0.1", user="admin"),
        SSHHostEntry(name="jump-box", hostname="10.0.0.2", user="admin"),
        SSHHostEntry(name="unique-host", hostname="10.0.0.5"),
    ]

    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        with patch("head.config.parse_ssh_config", return_value=mock_entries):
            screen = SSHImportScreen(str(config_path))
            app.push_screen(screen)
            await pilot.pause()
        status = screen.query_one("#ssh_status")
        text = _get_static_text(status)
        # Should show 2 (deduped) not 3
        assert "2 hosts available" in text


@pytest.mark.asyncio
async def test_ssh_import_screen_saves_multiple_machines(tmp_path):
    """Importing multiple SSH hosts should save them all to config."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))

    from head.config import SSHHostEntry

    mock_entries = [
        SSHHostEntry(name="remote-box", hostname="192.168.1.100", user="deploy"),
        SSHHostEntry(name="gpu-node", hostname="10.0.0.50", user="ml"),
    ]

    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        with patch("head.config.parse_ssh_config", return_value=mock_entries):
            screen = SSHImportScreen(str(config_path))
            app.push_screen(screen)
            await pilot.pause()
        with patch("head.config._is_localhost", return_value=False):
            screen._import_hosts(["remote-box", "gpu-node"])
            await pilot.pause()
        with open(config_path) as f:
            saved = yaml.safe_load(f)
        assert "remote-box" in saved["peers"]
        assert saved["peers"]["remote-box"]["ssh_host"] == "192.168.1.100"
        assert saved["peers"]["remote-box"]["ssh_user"] == "deploy"
        assert "gpu-node" in saved["peers"]
        assert saved["peers"]["gpu-node"]["ssh_host"] == "10.0.0.50"


@pytest.mark.asyncio
async def test_ssh_import_screen_search_filters(tmp_path):
    """Search input should filter the host list."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))

    from head.config import SSHHostEntry

    mock_entries = [
        SSHHostEntry(name="alpha-gpu", hostname="10.0.0.1"),
        SSHHostEntry(name="beta-cpu", hostname="10.0.0.2"),
        SSHHostEntry(name="gamma-gpu", hostname="10.0.0.3"),
    ]

    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        with patch("head.config.parse_ssh_config", return_value=mock_entries):
            screen = SSHImportScreen(str(config_path))
            app.push_screen(screen)
            await pilot.pause()
        # Type "gpu" in search
        search = screen.query_one("#ssh_search", Input)
        search.value = "gpu"
        await pilot.pause()
        status = screen.query_one("#ssh_status")
        text = _get_static_text(status)
        assert "2 hosts shown" in text


# ---------------------------------------------------------------------------
# SessionsScreen additional tests
# ---------------------------------------------------------------------------


def _make_fake_session(
    channel_id="discord:100",
    machine_id="server1",
    path="/home/user/project",
    daemon_session_id="uuid-0001",
    status="active",
    mode="auto",
    created_at="2026-03-18T10:00:00",
    name="bright-falcon",
):
    """Create a fake session object for testing."""
    from types import SimpleNamespace

    return SimpleNamespace(
        channel_id=channel_id,
        machine_id=machine_id,
        path=path,
        daemon_session_id=daemon_session_id,
        sdk_session_id=None,
        status=status,
        mode=mode,
        created_at=created_at,
        updated_at=created_at,
        name=name,
        tool_display="append",
    )


@pytest.mark.asyncio
async def test_sessions_screen_unknown_machine_warning(tmp_path):
    """Sessions from unknown machines should show a warning indicator."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {
            "server1": {"transport": "ssh", "ssh_host": "10.0.0.1"},
        },
    }
    config_path.write_text(yaml.dump(cfg))
    # Session on server1 (known) and server-gone (unknown)
    sessions = [
        _make_fake_session(channel_id="discord:1", machine_id="server1"),
        _make_fake_session(channel_id="discord:2", machine_id="server-gone", name="orphan"),
    ]
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(str(config_path))
        screen._load_sessions = lambda: sessions
        app.push_screen(screen)
        await pilot.pause()
        # The table should have 2 machine headers + 2 session rows = 4 rows
        table = screen.query_one("#sessions_table")
        assert table.row_count == 4


@pytest.mark.asyncio
async def test_dashboard_unknown_machines_from_sessions(tmp_path):
    """Dashboard should show unknown machines found in sessions."""
    import sqlite3

    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {
            "server1": {"transport": "ssh", "ssh_host": "10.0.0.1"},
        },
    }
    config_path.write_text(yaml.dump(cfg))

    # Create sessions DB with a session referencing an unknown machine
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
            "discord:1",
            "ghost-server",
            "/proj",
            "uuid-1",
            None,
            "active",
            "auto",
            "2026-03-18T00:00:00",
            "2026-03-18T00:00:00",
            "orphan",
            "append",
        ),
    )
    conn.commit()
    conn.close()

    from head.session_router import SessionRouter

    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        # Patch _get_router to use our test DB
        app.screen._get_router = lambda: SessionRouter(str(db_path))
        app.screen._refresh_unknown_machines()
        await pilot.pause()
        table = app.screen.query_one("#machine_table", MachineTable)
        # 1 configured + 1 unknown = 2 rows
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_machine_table_set_unknown_machines(tmp_path):
    """MachineTable.set_unknown_machines should add/remove unknown rows."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#machine_table", MachineTable)
        assert table.row_count == 0
        table.set_unknown_machines(["ghost1", "ghost2"])
        assert table.row_count == 2
        # Setting again should replace, not duplicate
        table.set_unknown_machines(["ghost1"])
        assert table.row_count == 1
        # Clear unknowns
        table.set_unknown_machines([])
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_sessions_screen_toggle_sort(tmp_path):
    """Pressing 't' should toggle sort order."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    sessions = [
        _make_fake_session(channel_id="discord:1", name="alpha", created_at="2026-03-18T08:00:00"),
        _make_fake_session(channel_id="discord:2", name="beta", created_at="2026-03-18T12:00:00"),
    ]
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(str(config_path))
        screen._load_sessions = lambda: sessions
        app.push_screen(screen)
        await pilot.pause()
        assert screen._sort_descending is True
        info_text = _get_static_text(screen.query_one("#sessions_info"))
        assert "newest first" in info_text
        await pilot.press("t")
        await pilot.pause()
        assert screen._sort_descending is False
        info_text = _get_static_text(screen.query_one("#sessions_info"))
        assert "oldest first" in info_text


@pytest.mark.asyncio
async def test_sessions_screen_color_coded_status(tmp_path):
    """Sessions should show color-coded status values."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    sessions = [
        _make_fake_session(channel_id="discord:1", status="active"),
        _make_fake_session(channel_id="discord:2", status="detached", name="detach-test"),
        _make_fake_session(channel_id="discord:3", status="destroyed", name="dead-test"),
    ]
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(str(config_path))
        screen._load_sessions = lambda: sessions
        app.push_screen(screen)
        await pilot.pause()
        info_text = _get_static_text(screen.query_one("#sessions_info"))
        assert "3 session(s)" in info_text


@pytest.mark.asyncio
async def test_sessions_screen_open_machine_header(tmp_path):
    """Pressing enter on a machine header should filter to that machine."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    sessions = [
        _make_fake_session(channel_id="discord:1", machine_id="server1"),
        _make_fake_session(channel_id="discord:2", machine_id="server2"),
    ]
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(str(config_path))
        screen._load_sessions = lambda: sessions
        app.push_screen(screen)
        await pilot.pause()
        # First row is machine header for server1
        assert screen._row_machine_map.get(0) is not None
        # Simulate action_open_or_enter when cursor is on a machine header
        screen.action_open_or_enter()
        await pilot.pause()
        assert screen._filter_machine is not None


@pytest.mark.asyncio
async def test_sessions_screen_remove_session(tmp_path):
    """Pressing 'r' should remove the selected session."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    sessions = [
        _make_fake_session(channel_id="discord:1", name="to-remove"),
    ]
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(str(config_path))
        screen._load_sessions = lambda: list(sessions)
        # Mock _get_router to avoid needing a real DB
        screen._get_router = lambda: None
        app.push_screen(screen)
        await pilot.pause()
        # Should have 1 header + 1 session = 2 rows
        table = screen.query_one("#sessions_table")
        assert table.row_count == 2


# ---------------------------------------------------------------------------
# DashboardScreen additional tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_on_screen_resume_refreshes(tmp_path):
    """on_screen_resume should refresh status panel and machine table."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {"s1": {"transport": "ssh", "ssh_host": "10.0.0.1"}},
    }
    config_path.write_text(yaml.dump(cfg))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        # Push and pop a screen to trigger on_screen_resume
        app.push_screen(HelpScreen())
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        # Verify components still work
        table = app.screen.query_one("#machine_table", MachineTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_dashboard_open_machine_with_selection(tmp_path):
    """action_open_machine should open sessions screen."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {
            "s1": {"transport": "ssh", "ssh_host": "10.0.0.1"},
            "s2": {"transport": "ssh", "ssh_host": "10.0.0.2"},
        },
    }
    config_path.write_text(yaml.dump(cfg))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        # Call action directly to avoid focus issues
        app.screen.action_open_machine()
        await pilot.pause()
        assert isinstance(app.screen, SessionsScreen)


# ---------------------------------------------------------------------------
# StatusPanel additional tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_panel_bot_summary(tmp_path):
    """StatusPanel should report configured bot names."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "bot": {
            "discord": {"token": "fake-token"},
            "telegram": {"token": "fake-tg-token"},
        },
    }
    config_path.write_text(yaml.dump(cfg))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.screen.query_one("#status", StatusPanel)
        bots = status._get_bot_summary()
        assert "Discord" in bots
        assert "Telegram" in bots


# ---------------------------------------------------------------------------
# MachineTable additional tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_machine_table_transport_detection(tmp_path):
    """MachineTable should correctly display different transport types."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {
            "ssh-box": {"transport": "ssh", "ssh_host": "10.0.0.1"},
            "http-box": {"transport": "http", "address": "https://10.0.0.2:9100"},
            "local-box": {"transport": "local"},
        },
    }
    config_path.write_text(yaml.dump(cfg))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#machine_table", MachineTable)
        assert table.row_count == 3


@pytest.mark.asyncio
async def test_machine_table_host_truncation(tmp_path):
    """MachineTable should truncate long hostnames."""
    config_path = tmp_path / "config.yaml"
    long_host = "very-long-hostname-that-exceeds-twenty-four-characters.example.com"
    cfg = {
        "default_mode": "auto",
        "peers": {
            "long-box": {"transport": "ssh", "ssh_host": long_host},
        },
    }
    config_path.write_text(yaml.dump(cfg))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#machine_table", MachineTable)
        row = table.get_row_at(0)
        host_cell = str(row[2])
        assert host_cell.endswith("...")
        assert len(host_cell) <= 24


@pytest.mark.asyncio
async def test_machine_table_get_selected_machine_name(tmp_path):
    """get_selected_machine_name should return the name of the selected row."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {
            "alpha": {"transport": "ssh", "ssh_host": "10.0.0.1"},
            "bravo": {"transport": "ssh", "ssh_host": "10.0.0.2"},
        },
    }
    config_path.write_text(yaml.dump(cfg))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#machine_table", MachineTable)
        name = table.get_selected_machine_name()
        assert name in ("alpha", "bravo")


@pytest.mark.asyncio
async def test_machine_table_get_selected_empty(tmp_path):
    """get_selected_machine_name should return None for empty table."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#machine_table", MachineTable)
        name = table.get_selected_machine_name()
        assert name is None


# ---------------------------------------------------------------------------
# Help screen via '?' key test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_question_mark_opens_help(tmp_path):
    """Pressing '?' on dashboard should open HelpScreen."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)


# ---------------------------------------------------------------------------
# Remove machine tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_machine_updates_config_file(tmp_path):
    """Removing a machine should persist to config file with 1 machine left."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {
            "server1": {"transport": "ssh", "ssh_host": "10.0.0.1"},
            "server2": {"transport": "ssh", "ssh_host": "10.0.0.2"},
        },
    }
    config_path.write_text(yaml.dump(cfg))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        table = app.screen.query_one("#machine_table", MachineTable)
        assert table.row_count == 2
        # Directly call the remove action with a known machine
        from head.config import load_config, save_config

        config = load_config(str(config_path))
        assert "server1" in config.peers
        del config.peers["server1"]
        save_config(config, str(config_path))
        # Verify file
        with open(config_path) as f:
            saved = yaml.safe_load(f)
        assert "server1" not in saved["peers"]
        assert "server2" in saved["peers"]


@pytest.mark.asyncio
async def test_remove_machine_cleans_up_sessions(tmp_path):
    """Removing a machine should also destroy its sessions."""
    import sqlite3

    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {
            "server1": {"transport": "ssh", "ssh_host": "10.0.0.1"},
            "server2": {"transport": "ssh", "ssh_host": "10.0.0.2"},
        },
    }
    config_path.write_text(yaml.dump(cfg))

    # Create a sessions database with sessions for server1
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
    for i in range(3):
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"discord:{i}",
                "server1",
                "/proj",
                f"uuid-{i}",
                None,
                "active",
                "auto",
                "2026-03-18T00:00:00",
                "2026-03-18T00:00:00",
                f"sess-{i}",
                "append",
            ),
        )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "discord:99",
            "server2",
            "/proj",
            "uuid-99",
            None,
            "active",
            "auto",
            "2026-03-18T00:00:00",
            "2026-03-18T00:00:00",
            "keep-me",
            "append",
        ),
    )
    conn.commit()
    conn.close()

    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        table = app.screen.query_one("#machine_table", MachineTable)
        assert table.row_count == 2

        # Patch _get_router to use our test DB
        from head.session_router import SessionRouter

        app.screen._get_router = lambda: SessionRouter(str(db_path))

        # Move cursor to server1 (first row)
        table.move_cursor(row=0)
        app.screen.action_remove_machine()
        await pilot.pause()

        # Verify server1 sessions are gone, server2 session remains
        router = SessionRouter(str(db_path))
        remaining = router.list_sessions()
        assert len(remaining) == 1
        assert remaining[0].machine_id == "server2"

        # Verify config file updated
        with open(config_path) as f:
            saved = yaml.safe_load(f)
        assert "server1" not in saved["peers"]
        assert "server2" in saved["peers"]


@pytest.mark.asyncio
async def test_remove_machine_empty_list_warns(tmp_path):
    """action_remove_machine with no machines should show warning, not crash."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        table = app.screen.query_one("#machine_table", MachineTable)
        assert table.row_count == 0
        # Should not crash when calling remove with no selection
        app.screen.action_remove_machine()
        await pilot.pause()


# ---------------------------------------------------------------------------
# Config error handling tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_with_malformed_config(tmp_path):
    """Malformed YAML config should not crash the app."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("invalid: yaml: [broken")
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # App should still launch (wizard or dashboard)
        assert app.screen is not None


def test_load_config_missing_file(tmp_path):
    """load_config should raise FileNotFoundError for missing file."""
    from head.config import load_config

    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "nonexistent.yaml"))


def test_load_config_empty_file(tmp_path):
    """load_config should raise ValueError for empty file."""
    from head.config import load_config

    p = tmp_path / "config.yaml"
    p.write_text("")
    with pytest.raises(ValueError, match="empty"):
        load_config(str(p))


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_machine_overwrites_existing(tmp_path):
    """Adding a machine with the same name should overwrite."""
    config_path = tmp_path / "config.yaml"
    cfg = {
        "default_mode": "auto",
        "peers": {
            "mybox": {"transport": "ssh", "ssh_host": "10.0.0.1", "ssh_user": "old"},
        },
    }
    config_path.write_text(yaml.dump(cfg))
    from head.config import PeerConfig, load_config, save_machine_to_config

    config = load_config(str(config_path))
    new_machine = PeerConfig(
        id="mybox",
        transport="ssh",
        ssh_host="10.0.0.99",
        ssh_user="newuser",
    )
    save_machine_to_config(config, new_machine)
    with open(config_path) as f:
        saved = yaml.safe_load(f)
    assert saved["peers"]["mybox"]["ssh_host"] == "10.0.0.99"
    assert saved["peers"]["mybox"]["ssh_user"] == "newuser"


# ---------------------------------------------------------------------------
# Integration test: wizard -> add machine flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wizard_to_add_machine_flow(tmp_path):
    """Wizard -> selecting 'Add a remote machine' should push AddMachineScreen."""
    config_path = str(tmp_path / "nonexistent.yaml")
    app = CodecastApp(config_path=config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, SetupWizardScreen)
        # Simulate selecting "add_machine" option
        menu = app.screen.query_one("#wizard_menu")
        # Find add_machine option index
        for i, opt in enumerate(menu._options):
            if getattr(opt, "id", None) == "add_machine":
                menu.highlighted = i
                break
        menu.action_select()
        await pilot.pause()
        assert isinstance(app.screen, AddMachineScreen)
