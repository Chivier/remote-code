"""Screen classes for the Codecast TUI."""

from __future__ import annotations

import shutil
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_claude_cli() -> bool:
    """Return True if the claude CLI binary is on PATH."""
    return shutil.which("claude") is not None


def _check_daemon_running() -> tuple[bool, int | None]:
    """Check if a local daemon is running via the port file + health check."""
    port_file = Path.home() / ".codecast" / "daemon.port"
    try:
        port = int(port_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        return False, None

    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200, port
    except Exception:
        return False, port


def _load_config(config_path: str):
    """Try to load ConfigV2; return None on failure."""
    try:
        from head.config_v2 import load_config_v2
        return load_config_v2(config_path)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Setup Wizard
# ---------------------------------------------------------------------------

_WIZARD_OPTIONS = [
    Option("Start local daemon", id="start_daemon"),
    Option("Add a remote peer", id="add_peer"),
    Option("Configure Discord bot", id="config_discord"),
    Option("Configure Telegram bot", id="config_telegram"),
    Option("Skip setup", id="skip"),
]


class SetupWizardScreen(Screen):
    """First-run setup wizard shown when no config exists."""

    BINDINGS = [("q", "quit_app", "Quit")]

    def __init__(self, config_path: str, version: str = "") -> None:
        super().__init__()
        self.config_path = config_path
        self.version = version

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static(
                f"Welcome to Codecast! {self.version}\n"
                "No configuration found. Starting setup wizard.\n",
                id="welcome",
            ),
            Static("What would you like to set up?", id="wizard_prompt"),
            OptionList(*_WIZARD_OPTIONS, id="wizard_menu"),
            id="wizard_container",
        )
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == "skip":
            self.app.exit()
        elif option_id == "start_daemon":
            self.app.push_screen(StartDaemonScreen(self.config_path))
        elif option_id == "add_peer":
            self.app.push_screen(AddPeerScreen(self.config_path))
        elif option_id == "config_discord":
            self.app.push_screen(ConfigBotScreen(self.config_path, "discord"))
        elif option_id == "config_telegram":
            self.app.push_screen(ConfigBotScreen(self.config_path, "telegram"))

    def action_quit_app(self) -> None:
        self.app.exit()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardScreen(Screen):
    """Main dashboard shown when a config already exists."""

    BINDINGS = [("q", "quit_app", "Quit")]

    def __init__(self, config_path: str, version: str = "") -> None:
        super().__init__()
        self.config_path = config_path
        self.version = version

    def compose(self) -> ComposeResult:
        yield Header()

        # Gather status
        daemon_running, daemon_port = _check_daemon_running()
        cfg = _load_config(self.config_path)
        peer_count = len(cfg.peers) if cfg else 0

        discord_status = "not configured"
        telegram_status = "not configured"
        if cfg:
            if cfg.bot.discord and cfg.bot.discord.token:
                discord_status = "configured"
            if cfg.bot.telegram and cfg.bot.telegram.token:
                telegram_status = "configured"

        daemon_label = (
            f"running (port {daemon_port})" if daemon_running else "not running"
        )

        status_text = (
            f"Codecast {self.version}\n\n"
            f"Local daemon: {daemon_label}\n"
            f"Peers: {peer_count} configured\n"
            f"Bots: Discord {discord_status}, Telegram {telegram_status}\n"
        )

        toggle_label = "Stop local daemon" if daemon_running else "Start local daemon"

        options = [
            Option("Manage sessions", id="sessions"),
            Option(toggle_label, id="toggle_daemon"),
            Option("Add a remote peer", id="add_peer"),
            Option("Configure bots", id="config_bots"),
            Option("Settings", id="settings"),
            Option("Quit", id="quit"),
        ]

        yield Vertical(
            Static(status_text, id="status"),
            Static("What would you like to do?", id="dashboard_prompt"),
            OptionList(*options, id="dashboard_menu"),
            id="dashboard_container",
        )
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == "quit":
            self.app.exit()
        elif option_id == "toggle_daemon":
            self.app.push_screen(StartDaemonScreen(self.config_path))
        elif option_id == "add_peer":
            self.app.push_screen(AddPeerScreen(self.config_path))
        elif option_id == "config_bots":
            self.app.push_screen(ConfigBotScreen(self.config_path, "discord"))
        elif option_id == "sessions":
            self.app.push_screen(SessionsScreen(self.config_path))
        elif option_id == "settings":
            self.notify("Settings not yet implemented.")

    def action_quit_app(self) -> None:
        self.app.exit()


# ---------------------------------------------------------------------------
# Start / Stop Daemon
# ---------------------------------------------------------------------------

class StartDaemonScreen(Screen):
    """Screen for starting or stopping the local daemon."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config_path: str) -> None:
        super().__init__()
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        yield Header()
        daemon_running, daemon_port = _check_daemon_running()
        claude_available = _check_claude_cli()

        if daemon_running:
            msg = f"Daemon is running on port {daemon_port}."
        elif not claude_available:
            msg = (
                "Claude CLI not found on PATH.\n"
                "Install Claude CLI first to run the daemon."
            )
        else:
            msg = "Daemon is not running. Claude CLI is available."

        options = []
        if daemon_running:
            options.append(Option("Stop daemon", id="stop"))
        elif claude_available:
            options.append(Option("Start daemon", id="start"))
        options.append(Option("Back", id="back"))

        yield Vertical(
            Static(msg, id="daemon_status"),
            OptionList(*options, id="daemon_menu"),
            id="daemon_container",
        )
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == "back":
            self.app.pop_screen()
        elif option_id == "start":
            self._start_daemon()
        elif option_id == "stop":
            self._stop_daemon()

    def _start_daemon(self) -> None:
        import argparse
        from head.cli import _cmd_start
        try:
            ns = argparse.Namespace(config=self.config_path)
            _cmd_start(ns)
            self.notify("Daemon started.")
        except Exception as exc:
            self.notify(f"Failed to start daemon: {exc}")
        self.app.pop_screen()

    def _stop_daemon(self) -> None:
        import argparse
        from head.cli import _cmd_stop
        try:
            ns = argparse.Namespace()
            _cmd_stop(ns)
            self.notify("Daemon stopped.")
        except Exception as exc:
            self.notify(f"Failed to stop daemon: {exc}")
        self.app.pop_screen()

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Add Peer
# ---------------------------------------------------------------------------

class AddPeerScreen(Screen):
    """Screen for adding a new remote peer."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config_path: str) -> None:
        super().__init__()
        self.config_path = config_path
        self._step = 0
        self._peer_name = ""
        self._transport = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Add a remote peer\n", id="add_peer_title"),
            Static("Enter peer name:", id="add_peer_prompt"),
            Input(placeholder="e.g. my-server", id="peer_input"),
            id="add_peer_container",
        )
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return

        if self._step == 0:
            self._peer_name = value
            self._step = 1
            prompt = self.query_one("#add_peer_prompt", Static)
            prompt.update("Transport (http / ssh):")
            inp = self.query_one("#peer_input", Input)
            inp.value = ""
            inp.placeholder = "ssh"
        elif self._step == 1:
            self._transport = value if value in ("http", "ssh") else "ssh"
            self._step = 2
            prompt = self.query_one("#add_peer_prompt", Static)
            if self._transport == "http":
                prompt.update("Address (e.g. https://host:9100):")
            else:
                prompt.update("SSH host (e.g. user@host):")
            inp = self.query_one("#peer_input", Input)
            inp.value = ""
            inp.placeholder = ""
        elif self._step == 2:
            self._save_peer(value)
            self.notify(f"Peer '{self._peer_name}' added.")
            self.app.pop_screen()

    def _save_peer(self, address: str) -> None:
        from head.config_v2 import PeerConfig, ConfigV2, load_config_v2, save_config_v2
        try:
            cfg = load_config_v2(self.config_path)
        except FileNotFoundError:
            cfg = ConfigV2()

        if self._transport == "http":
            peer = PeerConfig(id=self._peer_name, transport="http", address=address)
        else:
            parts = address.split("@", 1)
            if len(parts) == 2:
                user, host = parts
            else:
                user, host = None, parts[0]
            peer = PeerConfig(
                id=self._peer_name, transport="ssh",
                ssh_host=host, ssh_user=user,
            )
        cfg.peers[self._peer_name] = peer
        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        save_config_v2(cfg, self.config_path)

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Configure Bot
# ---------------------------------------------------------------------------

class ConfigBotScreen(Screen):
    """Screen for configuring a bot (Discord or Telegram)."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config_path: str, bot_type: str = "discord") -> None:
        super().__init__()
        self.config_path = config_path
        self.bot_type = bot_type

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static(f"Configure {self.bot_type.capitalize()} bot\n", id="bot_title"),
            Static(f"Enter {self.bot_type} bot token:", id="bot_prompt"),
            Input(placeholder="Bot token", password=True, id="bot_token_input"),
            id="bot_config_container",
        )
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        token = event.value.strip()
        if not token:
            return
        self._save_bot_token(token)
        self.notify(f"{self.bot_type.capitalize()} bot configured.")
        self.app.pop_screen()

    def _save_bot_token(self, token: str) -> None:
        from head.config_v2 import (
            ConfigV2, DiscordBotConfig, TelegramBotConfig,
            load_config_v2, save_config_v2,
        )
        try:
            cfg = load_config_v2(self.config_path)
        except FileNotFoundError:
            cfg = ConfigV2()

        if self.bot_type == "discord":
            cfg.bot.discord = DiscordBotConfig(token=token)
        else:
            cfg.bot.telegram = TelegramBotConfig(token=token)

        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        save_config_v2(cfg, self.config_path)

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class SessionsScreen(Screen):
    """Screen for managing active sessions (placeholder)."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config_path: str) -> None:
        super().__init__()
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Active Sessions\n", id="sessions_title"),
            Static("No active sessions.", id="sessions_list"),
            OptionList(Option("Back", id="back"), id="sessions_menu"),
            id="sessions_container",
        )
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == "back":
            self.app.pop_screen()

    def action_go_back(self) -> None:
        self.app.pop_screen()
