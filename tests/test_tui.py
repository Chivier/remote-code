"""Tests for the Codecast TUI app and screens."""

from __future__ import annotations

import pytest
import yaml

from head.tui.app import CodecastApp
from head.tui.screens import SetupWizardScreen, DashboardScreen


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
        text = _get_static_text(status)
        assert "Codecast" in text


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
async def test_dashboard_menu_options(tmp_path):
    """Dashboard should show the expected menu options."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"default_mode": "auto"}))
    app = CodecastApp(config_path=str(config_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        menu = app.screen.query_one("#dashboard_menu")
        option_ids = [opt.id for opt in menu._options]
        assert "sessions" in option_ids
        assert "toggle_daemon" in option_ids
        assert "add_peer" in option_ids
        assert "quit" in option_ids


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
async def test_dashboard_shows_peer_count(tmp_path):
    """Dashboard should show the number of configured peers."""
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
        status = app.screen.query_one("#status")
        text = _get_static_text(status)
        assert "2 configured" in text
