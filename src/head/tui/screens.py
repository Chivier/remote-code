"""Screen classes for the Codecast TUI."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from .widgets import PeerTable, StatusPanel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_claude_cli() -> bool:
    """Return True if the claude CLI binary is on PATH."""
    return shutil.which("claude") is not None


def _check_daemon_running() -> tuple[bool, int | None]:
    """Check if a local daemon is running via port file + health check.

    Uses the same helpers as CLI and StatusPanel for consistency.
    """
    from head.cli import _daemon_healthy, _read_port_file

    port = _read_port_file()
    if port is None:
        return False, None
    return _daemon_healthy(port), port


def _load_config(config_path: str):
    """Try to load ConfigV2; return None on failure."""
    try:
        from head.config_v2 import load_config_v2

        return load_config_v2(config_path)
    except Exception as exc:
        logger.warning("Failed to load config from %s: %s", config_path, exc)
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
                f"Welcome to Codecast! {self.version}\nNo configuration found. Starting setup wizard.\n",
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

    BINDINGS = [
        ("d", "toggle_daemon", "Daemon"),
        ("h", "start_head", "Head"),
        ("w", "start_webui", "WebUI"),
        ("a", "add_peer", "Add Peer"),
        ("s", "sessions", "Sessions"),
        ("q", "quit_app", "Quit"),
    ]

    def __init__(self, config_path: str, version: str = "") -> None:
        super().__init__()
        self.config_path = config_path
        self.version = version

    def compose(self) -> ComposeResult:
        yield Header()

        cfg = _load_config(self.config_path)
        peer_count = len(cfg.peers) if cfg else 0

        yield Vertical(
            Vertical(
                Static(f"[bold]Status[/bold]", id="status_panel_title"),
                StatusPanel(id="status"),
                id="status_panel_container",
            ),
            Vertical(
                Static(
                    f"[bold]Peers ({peer_count} configured)[/bold]",
                    id="peer_table_title",
                ),
                PeerTable(self.config_path, id="peer_table"),
                id="peer_table_container",
            ),
            id="dashboard_container",
        )
        yield Footer()

    def action_toggle_daemon(self) -> None:
        self.app.push_screen(StartDaemonScreen(self.config_path))

    def action_start_head(self) -> None:
        self.app.push_screen(StartHeadScreen(self.config_path))

    def action_start_webui(self) -> None:
        self.app.push_screen(StartWebUIScreen(self.config_path))

    def action_add_peer(self) -> None:
        self.app.push_screen(AddPeerScreen(self.config_path))

    def action_sessions(self) -> None:
        self.app.push_screen(SessionsScreen(self.config_path))

    def on_screen_resume(self) -> None:
        """Refresh status panel when returning from a sub-screen."""
        try:
            self.query_one("#status", StatusPanel).refresh_status()
        except Exception:
            pass

    def action_quit_app(self) -> None:
        self.app.exit()


# ---------------------------------------------------------------------------
# Start / Stop Head Node
# ---------------------------------------------------------------------------


class StartHeadScreen(Screen):
    """Screen for starting or stopping the head node."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config_path: str) -> None:
        super().__init__()
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        from head.cli import _HEAD_PID_FILE, _pid_alive, _read_pid_file

        yield Header()

        head_pid = _read_pid_file(_HEAD_PID_FILE)
        head_running = head_pid is not None and _pid_alive(head_pid)

        cfg = _load_config(self.config_path)

        # Build config summary
        bots_configured: list[str] = []
        if cfg and cfg.bot:
            if cfg.bot.discord and getattr(cfg.bot.discord, "token", None):
                bots_configured.append("Discord")
            if cfg.bot.telegram and getattr(cfg.bot.telegram, "token", None):
                bots_configured.append("Telegram")
            if getattr(cfg.bot, "lark", None) and getattr(cfg.bot.lark, "app_id", None):
                bots_configured.append("Lark")

        peers = getattr(cfg, "peers", {}) or {} if cfg else {}

        summary_lines = []
        if head_running:
            summary_lines.append(f"Head node is [green]running[/green] (pid={head_pid}).")
        else:
            summary_lines.append("Head node is [dim]not running[/dim].")
        summary_lines.append(f"Config:  {self.config_path}")
        summary_lines.append(f"Bots:    {', '.join(bots_configured) if bots_configured else '[dim]none[/dim]'}")
        summary_lines.append(f"Peers:   {len(peers)} configured")

        options: list[Option] = []
        if head_running:
            options.append(Option("Stop head node", id="stop"))
        elif bots_configured:
            options.append(Option("Start head node", id="start"))
        options.append(Option("Configure Discord token", id="config_discord"))
        options.append(Option("Configure Telegram token", id="config_telegram"))
        options.append(Option("Back", id="back"))

        yield Vertical(
            Static("\n".join(summary_lines), id="head_status"),
            OptionList(*options, id="head_menu"),
            id="head_container",
        )
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == "back":
            self.app.pop_screen()
        elif option_id == "start":
            self._start_head()
        elif option_id == "stop":
            self._stop_head()
        elif option_id == "config_discord":
            self.app.push_screen(ConfigBotScreen(self.config_path, "discord"))
        elif option_id == "config_telegram":
            self.app.push_screen(ConfigBotScreen(self.config_path, "telegram"))

    def _start_head(self) -> None:
        try:
            subprocess.Popen(
                [sys.executable, "-m", "head.cli", "head", "start", "-y", "-c", self.config_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.notify("Head node starting...")
        except Exception as exc:
            self.notify(f"Failed to start head: {exc}")
        self.app.pop_screen()

    def _stop_head(self) -> None:
        import signal

        from head.cli import _HEAD_PID_FILE, _pid_alive, _read_pid_file

        pid = _read_pid_file(_HEAD_PID_FILE)
        if pid is not None and _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                self.notify("Head node stopped.")
            except ProcessLookupError:
                self.notify("Head node already stopped.")
            _HEAD_PID_FILE.unlink(missing_ok=True)
        else:
            self.notify("Head node is not running.")
        self.app.pop_screen()

    def action_go_back(self) -> None:
        self.app.pop_screen()


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
        from head.cli import _DAEMON_PID_FILE, _pid_alive, _read_pid_file

        yield Header()
        daemon_running, daemon_port = _check_daemon_running()
        daemon_pid = _read_pid_file(_DAEMON_PID_FILE)
        claude_available = _check_claude_cli()

        if daemon_running:
            pid_part = f" (pid={daemon_pid})" if daemon_pid and _pid_alive(daemon_pid) else ""
            msg = f"Daemon is [green]running[/green] on port {daemon_port}{pid_part}."
        elif not claude_available:
            msg = "Claude CLI not found on PATH.\nInstall Claude CLI first to run the daemon."
        else:
            msg = "Daemon is [dim]not running[/dim]. Claude CLI is available."

        options: list[Option] = []
        if daemon_running:
            options.append(Option("Stop daemon", id="stop"))
            options.append(Option("Restart daemon", id="restart"))
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
        elif option_id == "restart":
            self._stop_daemon_only()
            self._start_daemon()

    def _start_daemon(self) -> None:
        """Start daemon as subprocess (non-blocking)."""
        try:
            cmd = [sys.executable, "-m", "head.cli", "start"]
            if self.config_path:
                cmd.extend(["-c", self.config_path])
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.notify("Daemon starting...")
        except Exception as exc:
            self.notify(f"Failed to start daemon: {exc}")
        self.app.pop_screen()

    def _stop_daemon_only(self) -> None:
        """Stop daemon without popping screen (for restart)."""
        import signal as sig

        from head.cli import _DAEMON_PID_FILE, _PORT_FILE, _pid_alive, _read_pid_file

        daemon_pid = _read_pid_file(_DAEMON_PID_FILE)
        if daemon_pid is not None and _pid_alive(daemon_pid):
            try:
                os.kill(daemon_pid, sig.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            try:
                subprocess.run(["pkill", "-f", "codecast-daemon"], check=False)
            except FileNotFoundError:
                pass
        _DAEMON_PID_FILE.unlink(missing_ok=True)
        _PORT_FILE.unlink(missing_ok=True)

    def _stop_daemon(self) -> None:
        """Stop daemon and return to dashboard."""
        self._stop_daemon_only()
        self.notify("Daemon stopped.")
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
                id=self._peer_name,
                transport="ssh",
                ssh_host=host,
                ssh_user=user,
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
            ConfigV2,
            DiscordBotConfig,
            TelegramBotConfig,
            load_config_v2,
            save_config_v2,
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
    """Screen for viewing sessions from the SessionRouter database."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config_path: str) -> None:
        super().__init__()
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("[bold]Sessions[/bold]\n", id="sessions_title"),
            DataTable(id="sessions_table"),
            Static("", id="sessions_info"),
            OptionList(Option("Back", id="back"), id="sessions_menu"),
            id="sessions_container",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#sessions_table", DataTable)
        table.add_columns("Name", "Machine", "Path", "Mode", "Status")
        table.cursor_type = "row"
        self._populate_sessions(table)

    def _populate_sessions(self, table: DataTable) -> None:
        sessions = self._load_sessions()
        info = self.query_one("#sessions_info", Static)
        if not sessions:
            info.update("[dim]No sessions found.[/dim]")
            return
        for s in sessions:
            table.add_row(
                s.name or s.daemon_session_id[:8],
                s.machine_id,
                s.path if len(s.path) <= 30 else "..." + s.path[-27:],
                s.mode,
                s.status,
            )
        info.update(f"[dim]{len(sessions)} session(s)[/dim]")

    def _load_sessions(self):
        """Load sessions from the SessionRouter SQLite database."""
        try:
            from head.session_router import SessionRouter

            # Try common DB locations
            candidates = [
                Path.home() / ".codecast" / "sessions.db",
                Path(__file__).parent.parent / "sessions.db",
            ]
            for db_path in candidates:
                if db_path.exists():
                    router = SessionRouter(str(db_path))
                    return router.list_sessions()
        except Exception as exc:
            logger.warning("Failed to load sessions: %s", exc)
        return []

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == "back":
            self.app.pop_screen()

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Start / Stop WebUI
# ---------------------------------------------------------------------------


class StartWebUIScreen(Screen):
    """Screen for starting or stopping the WebUI."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config_path: str) -> None:
        super().__init__()
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        from head.cli import _WEBUI_PID_FILE, _WEBUI_PORT_FILE, _pid_alive, _read_pid_file

        yield Header()

        webui_pid = _read_pid_file(_WEBUI_PID_FILE)
        webui_port = _read_pid_file(_WEBUI_PORT_FILE)
        webui_running = webui_pid is not None and _pid_alive(webui_pid)

        if webui_running:
            msg = f"WebUI is [green]running[/green] on http://127.0.0.1:{webui_port} (pid={webui_pid})."
        else:
            msg = "WebUI is [dim]not running[/dim]."

        options: list[Option] = []
        if webui_running:
            options.append(Option("Stop WebUI", id="stop"))
        else:
            options.append(Option("Start WebUI", id="start"))
        options.append(Option("Back", id="back"))

        yield Vertical(
            Static(msg, id="webui_status"),
            OptionList(*options, id="webui_menu"),
            id="webui_container",
        )
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == "back":
            self.app.pop_screen()
        elif option_id == "start":
            self._start_webui()
        elif option_id == "stop":
            self._stop_webui()

    def _start_webui(self) -> None:
        try:
            cmd = [sys.executable, "-m", "head.cli", "webui", "start"]
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.notify("WebUI starting...")
        except Exception as exc:
            self.notify(f"Failed to start WebUI: {exc}")
        self.app.pop_screen()

    def _stop_webui(self) -> None:
        from head.cli import _webui_stop

        try:
            _webui_stop()
            self.notify("WebUI stopped.")
        except Exception as exc:
            self.notify(f"Failed to stop WebUI: {exc}")
        self.app.pop_screen()

    def action_go_back(self) -> None:
        self.app.pop_screen()
