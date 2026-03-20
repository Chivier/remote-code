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
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    OptionList,
    SelectionList,
    Static,
)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from .widgets import MachineTable, StatusPanel

logger = logging.getLogger(__name__)

LOGO = r"""[bold cyan]
   ___          _                    _
  / __\___   __| | ___  ___ __ _ ___| |_
 / /  / _ \ / _` |/ _ \/ __/ _` / __| __|
/ /__| (_) | (_| |  __/ (_| (_| \__ \ |_
\____/\___/ \__,_|\___|\___\__,_|___/\__|[/bold cyan]"""


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
    """Try to load config; return None on failure."""
    try:
        from head.config import load_config

        return load_config(config_path)
    except Exception as exc:
        logger.warning("Failed to load config from %s: %s", config_path, exc)
        return None


# ---------------------------------------------------------------------------
# Setup Wizard
# ---------------------------------------------------------------------------


class SetupWizardScreen(Screen):
    """First-run setup wizard — clean checkbox UI with one-click actions."""

    BINDINGS = [
        ("q", "quit_app", "Quit"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    def __init__(self, config_path: str, version: str = "") -> None:
        super().__init__()
        self.config_path = config_path
        self.version = version

    def _check_steps(self) -> dict[str, bool]:
        """Check which setup steps are already completed."""
        steps = {
            "daemon": False,
            "bot": False,
            "machine": False,
        }
        daemon_running, _ = _check_daemon_running()
        steps["daemon"] = daemon_running

        try:
            from head.config import load_config

            cfg = load_config(self.config_path)
            if cfg.bot:
                if (
                    (cfg.bot.discord and getattr(cfg.bot.discord, "token", None))
                    or (cfg.bot.telegram and getattr(cfg.bot.telegram, "token", None))
                    or (getattr(cfg.bot, "lark", None) and getattr(cfg.bot.lark, "app_id", None))
                ):
                    steps["bot"] = True
            if cfg.peers:
                steps["machine"] = True
        except Exception:
            pass

        return steps

    def _build_step_label(self, done: bool, text: str, desc: str, optional: bool = False) -> str:
        """Build a checkbox-style step label."""
        if done:
            return f"  [bold green][x][/bold green] [dim strikethrough]{text}[/dim strikethrough]  [green]done[/green]"
        tag = "[dim](optional)[/dim] " if optional else ""
        return f"  [ ] {tag}{text}  [dim]— {desc}[/dim]"

    def _build_options(self, steps: dict[str, bool]) -> list[Option]:
        """Build the option list from current step state."""
        options: list[Option] = []

        options.append(
            Option(
                self._build_step_label(steps["daemon"], "Install & start daemon", "manages CLI agent processes"),
                id="start_daemon",
            )
        )
        options.append(
            Option(
                self._build_step_label(steps["bot"], "Configure a chat bot", "Discord, Telegram, or Lark"),
                id="config_bot",
            )
        )
        options.append(
            Option(
                self._build_step_label(steps["machine"], "Add a remote machine", "connect via SSH", optional=True),
                id="add_machine",
            )
        )

        required_done = sum([steps["daemon"], steps["bot"]])
        if required_done >= 2:
            options.append(Option("[bold green]  -> Open Dashboard[/bold green]", id="dashboard"))
        else:
            options.append(Option("  Skip setup and exit", id="skip"))

        return options

    def _first_incomplete_index(self, steps: dict[str, bool]) -> int:
        """Return the index of the first incomplete required step."""
        if not steps["daemon"]:
            return 0
        if not steps["bot"]:
            return 1
        return 0

    def compose(self) -> ComposeResult:
        yield Header()

        steps = self._check_steps()
        required_done = sum([steps["daemon"], steps["bot"]])

        if required_done >= 2:
            progress = "[bold green]All required steps complete![/bold green] Press [cyan]q[/cyan] to exit.\n"
        else:
            progress = f"[bold]Setup: {required_done}/2 required steps[/bold]\n"

        options = self._build_options(steps)

        yield Vertical(
            Static(LOGO, id="logo"),
            Static(progress, id="welcome"),
            OptionList(*options, id="wizard_menu"),
            id="wizard_container",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Set cursor to the first incomplete step."""
        steps = self._check_steps()
        idx = self._first_incomplete_index(steps)
        try:
            menu = self.query_one("#wizard_menu", OptionList)
            menu.highlighted = idx
        except Exception:
            pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == "skip":
            self.app.exit()
        elif option_id == "dashboard":
            self.app.pop_screen()
            self.app.push_screen(DashboardScreen(self.config_path, self.version))
        elif option_id == "start_daemon":
            self.app.push_screen(StartDaemonScreen(self.config_path))
        elif option_id == "config_bot":
            self.app.push_screen(_BotPickerScreen(self.config_path))
        elif option_id == "add_machine":
            self.app.push_screen(AddMachineScreen(self.config_path))

    def on_screen_resume(self) -> None:
        """Refresh the wizard when returning from a sub-screen."""
        steps = self._check_steps()
        required_done = sum([steps["daemon"], steps["bot"]])

        try:
            welcome = self.query_one("#welcome", Static)
            if required_done >= 2:
                welcome.update("[bold green]All required steps complete![/bold green] Press [cyan]q[/cyan] to exit.\n")
            else:
                welcome.update(f"[bold]Setup: {required_done}/2 required steps[/bold]\n")
        except Exception:
            pass

        try:
            menu = self.query_one("#wizard_menu", OptionList)
            menu.clear_options()
            for opt in self._build_options(steps):
                menu.add_option(opt)
        except Exception:
            pass

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#wizard_menu", OptionList).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#wizard_menu", OptionList).action_cursor_up()
        except Exception:
            pass

    def action_quit_app(self) -> None:
        self.app.exit()


class _BotPickerScreen(Screen):
    """Intermediate screen to choose which bot platform to configure."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    def __init__(self, config_path: str) -> None:
        super().__init__()
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static(
                "[bold]Configure a chat bot[/bold]\n\n"
                "Choose a platform. You need at least one bot to interact with your agents.\n"
                "You can configure more later from the dashboard.\n",
            ),
            OptionList(
                Option("Discord  —  create a bot at discord.com/developers", id="discord"),
                Option("Telegram  —  create a bot via @BotFather", id="telegram"),
                Option("Back", id="back"),
                id="bot_picker_menu",
            ),
            id="head_container",
        )
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == "back":
            self.app.pop_screen()
        elif option_id in ("discord", "telegram"):
            self.app.push_screen(ConfigBotScreen(self.config_path, option_id))

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#bot_picker_menu", OptionList).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#bot_picker_menu", OptionList).action_cursor_up()
        except Exception:
            pass

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class DashboardScreen(Screen):
    """Main dashboard shown when a config already exists."""

    BINDINGS = [
        ("d", "toggle_daemon", "Daemon"),
        ("H", "start_head", "Head"),
        ("w", "start_webui", "WebUI"),
        ("a", "add_machine", "Add Machine"),
        ("s", "sessions", "Sessions"),
        ("x", "remove_machine", "Remove Machine"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("up", "cursor_up", "Up"),
        ("l", "open_machine", "Enter"),
        ("right", "open_machine", "Enter"),
        ("enter", "open_machine", "Enter"),
        ("escape", "quit_app", "Quit"),
        ("question_mark", "show_help", "Help"),
        ("q", "quit_app", "Quit"),
    ]

    def __init__(self, config_path: str, version: str = "") -> None:
        super().__init__()
        self.config_path = config_path
        self.version = version

    def compose(self) -> ComposeResult:
        yield Header()

        cfg = _load_config(self.config_path)
        machine_count = len(cfg.peers) if cfg else 0

        yield Vertical(
            Static(LOGO, id="logo"),
            Vertical(
                Static("[bold cyan]Status[/bold cyan]", id="status_panel_title"),
                StatusPanel(config_path=self.config_path, id="status"),
                id="status_panel_container",
            ),
            Vertical(
                Static(
                    f"[bold cyan]Machines[/bold cyan] [bold white]({machine_count} configured)[/bold white]",
                    id="machine_table_title",
                ),
                MachineTable(self.config_path, id="machine_table"),
                id="machine_table_container",
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

    def action_add_machine(self) -> None:
        self.app.push_screen(AddMachineScreen(self.config_path))

    def action_sessions(self) -> None:
        self.app.push_screen(SessionsScreen(self.config_path))

    def _get_router(self):
        """Get a SessionRouter instance, or None."""
        try:
            from head.session_router import SessionRouter

            candidates = [
                Path.home() / ".codecast" / "sessions.db",
                Path(__file__).parent.parent / "sessions.db",
            ]
            for db_path in candidates:
                if db_path.exists():
                    return SessionRouter(str(db_path))
        except Exception:
            pass
        return None

    def action_remove_machine(self) -> None:
        try:
            table = self.query_one("#machine_table", MachineTable)
        except Exception:
            return
        name = table.get_selected_machine_name()
        if not name:
            self.notify("No machine selected.", severity="warning")
            return

        is_unknown = table.is_selected_unknown()
        try:
            if not is_unknown:
                from head.config import load_config, save_config

                cfg = load_config(self.config_path)
                if name in cfg.peers:
                    del cfg.peers[name]
                    save_config(cfg, self.config_path)
                else:
                    self.notify(f"Machine '{name}' not found in config.", severity="warning")
                    return

            # Clean up sessions referencing this machine
            sessions_cleaned = self._cleanup_machine_sessions(name)

            table.refresh_machines()
            self._refresh_unknown_machines()
            title = self.query_one("#machine_table_title", Static)
            title.update(f"[bold cyan]Machines[/bold cyan] [bold white]({table.machine_count} configured)[/bold white]")
            if sessions_cleaned:
                self.notify(f"Removed machine: {name} ({sessions_cleaned} session(s) cleaned up)")
            else:
                self.notify(f"Removed machine: {name}")
        except Exception as exc:
            self.notify(f"Failed to remove machine: {exc}", severity="error")

    def _cleanup_machine_sessions(self, machine_id: str) -> int:
        """Destroy and delete all sessions for a machine. Returns count cleaned."""
        router = self._get_router()
        if not router:
            return 0
        machine_sessions = router.list_sessions(machine_id=machine_id)
        for s in machine_sessions:
            router.destroy(s.channel_id)
        if machine_sessions:
            try:
                conn = router._connect()
                conn.execute(
                    "DELETE FROM sessions WHERE machine_id = ? AND status = 'destroyed'",
                    (machine_id,),
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
        return len(machine_sessions)

    def action_cursor_down(self) -> None:
        try:
            table = self.query_one("#machine_table", MachineTable)
            table.action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            table = self.query_one("#machine_table", MachineTable)
            table.action_cursor_up()
        except Exception:
            pass

    def action_open_machine(self) -> None:
        """Open sessions screen filtered to the selected machine, or resolve unknown machines."""
        try:
            table = self.query_one("#machine_table", MachineTable)
        except Exception:
            return

        if table.is_selected_unknown():
            self._resolve_unknown_machine(table)
            return

        name = table.get_selected_machine_name()
        if name:
            self.app.push_screen(SessionsScreen(self.config_path, filter_machine=name))
        else:
            self.app.push_screen(SessionsScreen(self.config_path))

    def _resolve_unknown_machine(self, table: MachineTable) -> None:
        """Try to resolve an unknown machine by importing from SSH config or adding manually."""
        name = table.get_selected_machine_name()
        if not name:
            return

        try:
            from head.config import parse_ssh_config

            ssh_entries = parse_ssh_config()
            match = next((e for e in ssh_entries if e.name == name), None)
        except Exception:
            match = None

        if match:
            self._import_ssh_host(name, match)
        else:
            self.app.push_screen(AddMachineScreen(self.config_path))

    def _import_ssh_host(self, name: str, entry) -> None:
        """Import a single SSH host as a machine."""
        from head.config import Config, PeerConfig, load_config, save_config

        try:
            cfg = load_config(self.config_path)
        except FileNotFoundError:
            cfg = Config()

        hostname = entry.hostname or entry.name
        try:
            from head.config import _is_localhost

            is_local = _is_localhost(hostname)
        except Exception:
            is_local = hostname in ("localhost", "127.0.0.1", "::1")

        if is_local:
            peer = PeerConfig(id=name, transport="local")
        else:
            peer = PeerConfig(
                id=name,
                transport="ssh",
                ssh_host=hostname,
                ssh_user=entry.user,
                proxy_jump=getattr(entry, "proxy_jump", None),
            )

        cfg.peers[name] = peer
        save_config(cfg, self.config_path)
        self.notify(f"Imported '{name}' from SSH config.")

        # Refresh the dashboard
        try:
            table = self.query_one("#machine_table", MachineTable)
            table.refresh_machines()
            self._refresh_unknown_machines()
            title = self.query_one("#machine_table_title", Static)
            title.update(f"[bold cyan]Machines[/bold cyan] [bold white]({table.machine_count} configured)[/bold white]")
        except Exception:
            pass

    def _refresh_unknown_machines(self) -> None:
        """Find machines referenced in sessions but not in config, and display them."""
        try:
            table = self.query_one("#machine_table", MachineTable)
            cfg = _load_config(self.config_path)
            existing_peers = set((cfg.peers or {}).keys()) if cfg else set()

            router = self._get_router()
            if not router:
                table.set_unknown_machines([])
                return

            all_sessions = router.list_sessions()
            unknown = set()
            for s in all_sessions:
                if s.machine_id not in existing_peers:
                    unknown.add(s.machine_id)
            table.set_unknown_machines(sorted(unknown))
        except Exception:
            pass

    def on_screen_resume(self) -> None:
        """Refresh status panel and machine table when returning from a sub-screen."""
        try:
            self.query_one("#status", StatusPanel).refresh_status()
        except Exception:
            pass
        try:
            table = self.query_one("#machine_table", MachineTable)
            table.refresh_machines()
            title = self.query_one("#machine_table_title", Static)
            title.update(f"[bold cyan]Machines[/bold cyan] [bold white]({table.machine_count} configured)[/bold white]")
        except Exception:
            pass

    def action_show_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_quit_app(self) -> None:
        self.app.exit()


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------


class HelpScreen(Screen):
    """Help screen showing available keyboard shortcuts and commands."""

    BINDINGS = [("escape", "go_back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        help_text = (
            "[bold]Codecast TUI — Help[/bold]\n"
            "\n"
            "[bold]What is Codecast?[/bold]\n"
            "  Codecast lets you control AI coding agents (Claude CLI, Codex CLI) on\n"
            "  local or remote machines through Discord, Telegram, or the web UI.\n"
            "\n"
            "[bold]Components:[/bold]\n"
            "  [cyan]Daemon[/cyan]     Runs on each machine. Spawns and manages CLI agent processes\n"
            "             (Claude, Codex). Streams responses back via JSON-RPC.\n"
            "  [cyan]Head[/cyan]       Runs here. Connects chat bots (Discord/Telegram/Lark) to\n"
            "             daemons via SSH tunnels. Routes messages between users and agents.\n"
            "  [cyan]WebUI[/cyan]      Optional web dashboard for monitoring sessions and machines.\n"
            "  [cyan]Machines[/cyan]   Remote servers connected via SSH where agents run.\n"
            "\n"
            "[bold]Dashboard shortcuts:[/bold]\n"
            "  [cyan]d[/cyan]  Daemon   — start / stop the agent process manager\n"
            "  [cyan]H[/cyan]  Head     — start / stop the chat bot bridge\n"
            "  [cyan]w[/cyan]  WebUI    — start / stop the web dashboard\n"
            "  [cyan]a[/cyan]  Add      — add a new remote machine\n"
            "  [cyan]x[/cyan]  Remove   — remove selected machine\n"
            "  [cyan]s[/cyan]  Sessions — view active agent sessions\n"
            "  [cyan]?[/cyan]  Help     — show this screen\n"
            "  [cyan]q[/cyan]  Quit\n"
            "\n"
            "[bold]Navigation:[/bold]\n"
            "  [cyan]j / ↓[/cyan]     Move cursor down\n"
            "  [cyan]k / ↑[/cyan]     Move cursor up\n"
            "  [cyan]l / → / Enter[/cyan]  Open machine sessions\n"
            "  [cyan]h / ← / Esc[/cyan]   Go back\n"
            "\n"
            "[bold]Sessions view:[/bold]\n"
            "  [cyan]t[/cyan]         Toggle sort (newest/oldest)\n"
            "  [cyan]r / Del[/cyan]   Remove selected session\n"
            "  [yellow]\u26a0[/yellow]          Unknown machine (not in config)\n"
            "\n"
            "[bold]CLI equivalents:[/bold]\n"
            "  codecast start       Start the daemon\n"
            "  codecast stop        Stop the daemon\n"
            "  codecast head start  Start the head node (chat bots)\n"
            "  codecast status      Show component status\n"
            "  codecast peers       List configured machines\n"
            "  codecast sessions    List active sessions\n"
        )
        yield Vertical(
            Static(help_text, id="help_text"),
            id="head_container",
        )
        yield Footer()

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Start / Stop Head Node
# ---------------------------------------------------------------------------


class StartHeadScreen(Screen):
    """Screen for starting or stopping the head node."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

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
            summary_lines.append(f"Head node is [bold green]● running[/bold green] [dim](pid={head_pid})[/dim]")
        else:
            summary_lines.append("Head node is [bold red]○ stopped[/bold red]")
        summary_lines.append(f"[bold]Config:[/bold]   {self.config_path}")
        summary_lines.append(
            f"[bold]Bots:[/bold]     {', '.join(bots_configured) if bots_configured else '[dim]none[/dim]'}"
        )
        summary_lines.append(f"[bold]Machines:[/bold] {len(peers)} configured")

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

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#head_menu", OptionList).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#head_menu", OptionList).action_cursor_up()
        except Exception:
            pass

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Start / Stop Daemon
# ---------------------------------------------------------------------------


class StartDaemonScreen(Screen):
    """Screen for starting, stopping, or installing the local daemon."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    def __init__(self, config_path: str) -> None:
        super().__init__()
        self.config_path = config_path
        self._installing = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("", id="daemon_status"),
            OptionList(id="daemon_menu"),
            Static("", id="daemon_log"),
            id="daemon_container",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        """Rebuild status text and menu options based on current state."""
        from head.cli import _DAEMON_PID_FILE, _pid_alive, _read_pid_file
        from head.peer_manager import resolve_daemon_binary

        daemon_running, daemon_port = _check_daemon_running()
        daemon_pid = _read_pid_file(_DAEMON_PID_FILE)
        claude_available = _check_claude_cli()
        daemon_binary = resolve_daemon_binary()

        explanation = (
            "[bold cyan]Daemon[/bold cyan] — the agent process manager\n"
            "[dim]Manages Claude/Codex CLI processes on this machine.[/dim]\n"
        )

        if daemon_running:
            pid_part = f" [dim](pid={daemon_pid})[/dim]" if daemon_pid and _pid_alive(daemon_pid) else ""
            status = (
                f"Status: [bold green]● running[/bold green] on port [bold white]{daemon_port}[/bold white]{pid_part}"
            )
        elif daemon_binary is None:
            from head.daemon_installer import get_expected_asset_name

            asset = get_expected_asset_name()
            if asset:
                status = (
                    f"Status: [bold yellow]⚠ daemon binary not found[/bold yellow]\n[dim]Will download: {asset}[/dim]"
                )
            else:
                status = (
                    "Status: [bold yellow]⚠ daemon binary not found[/bold yellow]\n"
                    "[dim]No pre-built binary for this platform — will build from source[/dim]"
                )
        elif not claude_available:
            status = (
                "Status: [bold red]○ stopped[/bold red]\n\n"
                "[bold yellow]⚠ Claude CLI not found on PATH[/bold yellow]\n"
                "[dim]Install from: https://docs.anthropic.com/en/docs/claude-cli[/dim]"
            )
        else:
            status = (
                "Status: [bold red]○ stopped[/bold red] — Claude CLI is [green]available[/green]\n"
                f"[dim]Binary: {daemon_binary}[/dim]"
            )

        status_widget = self.query_one("#daemon_status", Static)
        status_widget.update(explanation + status)

        menu = self.query_one("#daemon_menu", OptionList)
        menu.clear_options()

        if self._installing:
            menu.add_option(Option("[dim]Installing...[/dim]", id="noop"))
        elif daemon_running:
            menu.add_option(Option("Stop daemon", id="stop"))
            menu.add_option(Option("Restart daemon", id="restart"))
        elif daemon_binary is None:
            menu.add_option(Option("[bold]Install daemon[/bold]", id="install"))
        elif claude_available:
            menu.add_option(Option("Start daemon", id="start"))
        menu.add_option(Option("Back", id="back"))

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
        elif option_id == "install":
            self._install_daemon()

    def _install_daemon(self) -> None:
        """Run daemon installation in a background thread."""
        self._installing = True
        self._refresh_ui()

        log_widget = self.query_one("#daemon_log", Static)
        log_lines: list[str] = []

        def on_progress(msg: str) -> None:
            log_lines.append(msg)
            # Keep last 12 lines
            display = log_lines[-12:]
            try:
                self.app.call_from_thread(log_widget.update, "\n".join(f"[dim]{l}[/dim]" for l in display))
            except Exception:
                pass  # Screen may be gone

        import threading

        def _run() -> None:
            try:
                from head.daemon_installer import install_daemon

                result = install_daemon(on_progress=on_progress)
            except Exception as exc:
                on_progress(f"Error: {exc}")
                result = False

            def _finish() -> None:
                self._installing = False
                if result:
                    self.notify("Daemon installed successfully!")
                    log_widget.update("[green]Installation complete.[/green]")
                else:
                    self.notify("Daemon installation failed.", severity="error")
                    log_widget.update("[red]Installation failed. Check log above.[/red]")
                self._refresh_ui()

            try:
                self.app.call_from_thread(_finish)
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

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

    def on_screen_resume(self) -> None:
        """Refresh when returning from a sub-screen."""
        self._refresh_ui()

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#daemon_menu", OptionList).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#daemon_menu", OptionList).action_cursor_up()
        except Exception:
            pass

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Add Machine
# ---------------------------------------------------------------------------


class AddMachineScreen(Screen):
    """Screen for adding a new machine (manual or SSH import)."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    def __init__(self, config_path: str) -> None:
        super().__init__()
        self.config_path = config_path
        self._step = 0
        self._machine_name = ""
        self._transport = ""
        self._mode = ""  # "manual" or "ssh_import"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Add a machine\n", id="add_machine_title"),
            Static("Choose method:", id="add_machine_prompt"),
            OptionList(
                Option("Manual entry", id="manual"),
                Option("Import from SSH config", id="ssh_import"),
                id="add_machine_method",
            ),
            id="add_machine_container",
        )
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == "manual":
            self._mode = "manual"
            self._step = 1
            self._switch_to_manual_input()
        elif option_id == "ssh_import":
            self._mode = "ssh_import"
            self.app.push_screen(SSHImportScreen(self.config_path))

    def _switch_to_manual_input(self) -> None:
        """Replace option list with manual input fields."""
        prompt = self.query_one("#add_machine_prompt", Static)
        prompt.update("Enter machine name:")
        try:
            method_list = self.query_one("#add_machine_method", OptionList)
            method_list.remove()
        except Exception:
            pass
        container = self.query_one("#add_machine_container", Vertical)
        container.mount(Input(placeholder="e.g. my-server", id="machine_input"))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return

        if self._step == 1:
            self._machine_name = value
            self._step = 2
            prompt = self.query_one("#add_machine_prompt", Static)
            prompt.update("Transport (http / ssh):")
            inp = self.query_one("#machine_input", Input)
            inp.value = ""
            inp.placeholder = "ssh"
        elif self._step == 2:
            self._transport = value if value in ("http", "ssh") else "ssh"
            self._step = 3
            prompt = self.query_one("#add_machine_prompt", Static)
            if self._transport == "http":
                prompt.update("Address (e.g. https://host:9100):")
            else:
                prompt.update("SSH host (e.g. user@host):")
            inp = self.query_one("#machine_input", Input)
            inp.value = ""
            inp.placeholder = ""
        elif self._step == 3:
            self._save_machine(value)
            self.notify(f"Machine '{self._machine_name}' added.")
            self.app.pop_screen()

    def _save_machine(self, address: str) -> None:
        from head.config import Config, PeerConfig, load_config, save_config

        try:
            cfg = load_config(self.config_path)
        except FileNotFoundError:
            cfg = Config()

        if self._transport == "http":
            peer = PeerConfig(id=self._machine_name, transport="http", address=address)
        else:
            parts = address.split("@", 1)
            if len(parts) == 2:
                user, host = parts
            else:
                user, host = None, parts[0]
            peer = PeerConfig(
                id=self._machine_name,
                transport="ssh",
                ssh_host=host,
                ssh_user=user,
            )
        cfg.peers[self._machine_name] = peer
        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        save_config(cfg, self.config_path)

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#add_machine_method", OptionList).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#add_machine_method", OptionList).action_cursor_up()
        except Exception:
            pass

    def action_go_back(self) -> None:
        self.app.pop_screen()


class SSHImportScreen(Screen):
    """Screen for importing machines from SSH config with search and multi-select."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("slash", "focus_search", "Search"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    def __init__(self, config_path: str) -> None:
        super().__init__()
        self.config_path = config_path
        self._available: list = []  # list of ssh entries
        self._entry_map: dict[str, object] = {}  # name -> ssh entry
        self._selected: set[str] = set()  # track selections across filters

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Import from SSH config\n", id="ssh_import_title"),
            Input(placeholder="Type to filter, Enter to jump to list, Esc to clear", id="ssh_search"),
            Static("Loading...", id="ssh_status"),
            SelectionList[str](id="ssh_host_list"),
            Static(
                "[bold]Space[/bold] toggle  "
                "[bold]Enter[/bold] import selected  "
                "[bold]/[/bold] search  "
                "[bold]Tab[/bold] switch focus  "
                "[bold]j/k[/bold] navigate  "
                "[bold]Esc[/bold] clear/back",
                id="ssh_help",
            ),
            id="ssh_import_container",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._load_ssh_hosts()

    def _load_ssh_hosts(self) -> None:
        """Load SSH hosts and populate the selection list."""
        try:
            from head.config import load_config, parse_ssh_config

            ssh_entries = parse_ssh_config()
            cfg = load_config(self.config_path) if Path(self.config_path).exists() else None
            existing = set((cfg.peers or {}).keys()) if cfg else set()
        except Exception:
            ssh_entries = []
            existing = set()

        seen: set[str] = set()
        self._available = []
        for e in ssh_entries:
            if e.name not in existing and e.name not in seen:
                seen.add(e.name)
                self._available.append(e)
                self._entry_map[e.name] = e

        status = self.query_one("#ssh_status", Static)
        sel_list = self.query_one("#ssh_host_list", SelectionList)

        if not self._available:
            status.update("No new SSH hosts found in ~/.ssh/config.")
            return

        self._update_status()
        self._populate_list(self._available)
        sel_list.focus()

    def _format_entry(self, entry) -> str:
        """Format an SSH entry for display."""
        host_info = entry.hostname or entry.name
        if entry.user:
            host_info = f"{entry.user}@{host_info}"
        label = f"{entry.name} ({host_info})"
        if getattr(entry, "proxy_jump", None):
            label += f" via {entry.proxy_jump}"
        return label

    def _get_filtered(self) -> list:
        """Get entries matching current search query."""
        search = self.query_one("#ssh_search", Input)
        query = search.value.strip().lower()
        if not query:
            return list(self._available)
        return [
            e
            for e in self._available
            if query in e.name.lower() or query in (e.hostname or "").lower() or query in (e.user or "").lower()
        ]

    def _populate_list(self, entries: list) -> None:
        """Populate the selection list, preserving selection state."""
        sel_list = self.query_one("#ssh_host_list", SelectionList)
        sel_list.clear_options()
        for entry in entries:
            selected = entry.name in self._selected
            sel_list.add_option(Selection(self._format_entry(entry), entry.name, selected))

    def _update_status(self) -> None:
        """Update status line with current counts."""
        filtered = self._get_filtered()
        count = len(self._selected)
        search = self.query_one("#ssh_search", Input)
        has_query = bool(search.value.strip())
        status = self.query_one("#ssh_status", Static)
        word = "shown" if has_query else "available"
        status.update(f"{len(filtered)} hosts {word} ({count} selected):")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the host list as the user types."""
        if event.input.id != "ssh_search":
            return
        filtered = self._get_filtered()
        self._populate_list(filtered)
        self._update_status()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in search box moves focus to the selection list."""
        if event.input.id != "ssh_search":
            return
        self.query_one("#ssh_host_list", SelectionList).focus()

    def on_key(self, event) -> None:
        """Handle Escape and Enter context-sensitively."""
        search = self.query_one("#ssh_search", Input)
        sel_list = self.query_one("#ssh_host_list", SelectionList)

        if event.key == "escape":
            if search.has_focus and search.value:
                # Clear search and refocus list
                search.value = ""
                sel_list.focus()
                event.prevent_default()
                event.stop()
            elif search.value:
                # List focused but search has text — clear search first
                search.value = ""
                event.prevent_default()
                event.stop()
            # else: let binding handle go_back

        elif event.key == "enter":
            if search.has_focus:
                # Enter in search box -> move focus to list
                sel_list.focus()
                event.prevent_default()
                event.stop()
            elif sel_list.has_focus:
                # Enter on list -> confirm import
                event.prevent_default()
                event.stop()
                self.action_confirm_import()

    def on_selection_list_selection_toggled(self, event: SelectionList.SelectionToggled) -> None:
        """Track selections persistently across filter changes."""
        value = event.selection_list.get_option_at_index(event.selection_index).value
        if value in self._selected:
            self._selected.discard(value)
        else:
            self._selected.add(value)
        self._update_status()

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#ssh_host_list", SelectionList).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#ssh_host_list", SelectionList).action_cursor_up()
        except Exception:
            pass

    def action_focus_search(self) -> None:
        self.query_one("#ssh_search", Input).focus()

    def action_confirm_import(self) -> None:
        """Import all selected hosts."""
        if not self._selected:
            self.notify("No hosts selected.", severity="warning")
            return
        self._import_hosts(list(self._selected))

    def _import_hosts(self, host_names: list[str]) -> None:
        """Import multiple SSH hosts as machines."""
        from head.config import Config, PeerConfig, load_config, save_config

        try:
            cfg = load_config(self.config_path)
        except FileNotFoundError:
            cfg = Config()

        imported = 0
        for name in host_names:
            entry = self._entry_map.get(name)
            if not entry:
                continue

            hostname = entry.hostname or entry.name
            try:
                from head.config import _is_localhost

                is_local = _is_localhost(hostname)
            except Exception:
                is_local = hostname in ("localhost", "127.0.0.1", "::1")

            if is_local:
                peer = PeerConfig(id=name, transport="local")
            else:
                peer = PeerConfig(
                    id=name,
                    transport="ssh",
                    ssh_host=hostname,
                    ssh_user=entry.user,
                    proxy_jump=getattr(entry, "proxy_jump", None),
                )

            cfg.peers[name] = peer
            imported += 1

        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        save_config(cfg, self.config_path)
        self.notify(f"Imported {imported} machine(s) from SSH config.")
        # Pop both SSHImportScreen and AddMachineScreen
        self.app.pop_screen()
        self.app.pop_screen()

    def action_go_back(self) -> None:
        self.app.pop_screen()


# Keep backward-compatible alias
AddPeerScreen = AddMachineScreen


# ---------------------------------------------------------------------------
# Configure Bot
# ---------------------------------------------------------------------------


_DISCORD_GUIDANCE = (
    "[bold]Discord Bot Setup[/bold]\n"
    "\n"
    "1. Go to [cyan]https://discord.com/developers/applications[/cyan]\n"
    "2. Click [bold]New Application[/bold] → name it (e.g. Codecast)\n"
    "3. Go to [bold]Bot[/bold] tab → click [bold]Reset Token[/bold] → copy the token\n"
    "4. Enable [bold]Message Content Intent[/bold] under Privileged Intents\n"
    "5. Go to [bold]OAuth2 → URL Generator[/bold] → select [cyan]bot[/cyan] scope\n"
    "6. Select permissions: Send Messages, Read Message History, Use Slash Commands\n"
    "7. Copy the generated URL and open it to invite the bot to your server\n"
)

_TELEGRAM_GUIDANCE = (
    "[bold]Telegram Bot Setup[/bold]\n"
    "\n"
    "1. Open Telegram and message [cyan]@BotFather[/cyan]\n"
    "2. Send [bold]/newbot[/bold] and follow the prompts to name your bot\n"
    "3. Copy the API token BotFather gives you (format: [dim]123456:ABC-DEF...[/dim])\n"
    "4. Optionally send [bold]/setcommands[/bold] to register bot commands\n"
)


class ConfigBotScreen(Screen):
    """Screen for configuring a bot (Discord or Telegram)."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, config_path: str, bot_type: str = "discord") -> None:
        super().__init__()
        self.config_path = config_path
        self.bot_type = bot_type

    def compose(self) -> ComposeResult:
        yield Header()
        guidance = _DISCORD_GUIDANCE if self.bot_type == "discord" else _TELEGRAM_GUIDANCE
        if self.bot_type == "discord":
            placeholder = "Paste Discord bot token (e.g. MTIz...)"
        else:
            placeholder = "Paste Telegram bot token (e.g. 123456:ABC-DEF...)"
        yield Vertical(
            Static(f"Configure {self.bot_type.capitalize()} bot\n", id="bot_title"),
            Static(guidance, id="bot_guidance"),
            Static(f"Enter {self.bot_type} bot token:", id="bot_prompt"),
            Input(placeholder=placeholder, password=True, id="bot_token_input"),
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
        from head.config import (
            Config,
            DiscordConfig,
            TelegramConfig,
            load_config,
            save_config,
        )

        try:
            cfg = load_config(self.config_path)
        except FileNotFoundError:
            cfg = Config()

        if self.bot_type == "discord":
            cfg.bot.discord = DiscordConfig(token=token)
        else:
            cfg.bot.telegram = TelegramConfig(token=token)

        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        save_config(cfg, self.config_path)

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionsScreen(Screen):
    """Screen for viewing sessions from the SessionRouter database."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("h", "go_back", "Back"),
        ("left", "go_back", "Back"),
        ("t", "toggle_sort", "Toggle sort"),
        ("r", "remove_session", "Remove"),
        ("delete", "remove_session", "Remove"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("up", "cursor_up", "Up"),
        ("l", "open_or_enter", "Enter"),
        ("right", "open_or_enter", "Enter"),
        ("enter", "open_or_enter", "Enter"),
    ]

    def __init__(self, config_path: str, filter_machine: str | None = None) -> None:
        super().__init__()
        self.config_path = config_path
        self._sort_descending = True  # newest first by default
        self._sessions: list = []
        self._row_session_map: dict[int, object] = {}  # row index -> Session
        self._row_machine_map: dict[int, str] = {}  # row index -> machine_id (header rows)
        self._filter_machine: str | None = filter_machine
        self._init_filtered: bool = filter_machine is not None  # opened pre-filtered from dashboard

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("[bold cyan]Sessions[/bold cyan]\n", id="sessions_title"),
            DataTable(id="sessions_table"),
            Static("", id="sessions_info"),
            id="sessions_container",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#sessions_table", DataTable)
        table.add_columns("Name", "Path", "Mode", "Status", "Created")
        table.cursor_type = "row"
        self._sessions = self._load_sessions()
        self._populate_sessions(table)

    def _populate_sessions(self, table: DataTable) -> None:
        table.clear()
        self._row_session_map.clear()
        self._row_machine_map.clear()
        info = self.query_one("#sessions_info", Static)
        title = self.query_one("#sessions_title", Static)

        sessions = self._sessions
        if self._filter_machine:
            sessions = [s for s in sessions if s.machine_id == self._filter_machine]
            title.update(
                f"[bold cyan]Sessions[/bold cyan] — [bold white]{self._filter_machine}[/bold white]"
                " [dim](← back)[/dim]\n"
            )
        else:
            title.update("[bold cyan]Sessions[/bold cyan]\n")

        if not sessions:
            info.update("[dim]No sessions found.[/dim]")
            return

        # Sort by created_at
        sessions_sorted = sorted(
            sessions,
            key=lambda s: s.created_at or "",
            reverse=self._sort_descending,
        )

        # Group by machine (sorted alphabetically)
        from collections import OrderedDict

        raw_grouped: dict[str, list] = {}
        for s in sessions_sorted:
            raw_grouped.setdefault(s.machine_id, []).append(s)
        grouped: OrderedDict[str, list] = OrderedDict(sorted(raw_grouped.items()))

        # Determine which machines exist in config
        cfg = _load_config(self.config_path)
        existing_peers = set((cfg.peers or {}).keys()) if cfg else set()

        row_idx = 0
        for machine_id, machine_sessions in grouped.items():
            is_unknown = machine_id not in existing_peers
            if not self._filter_machine:
                # Machine header row (only in all-machines view)
                warning = " [yellow]⚠[/yellow]" if is_unknown else ""
                table.add_row(
                    f"[bold cyan]▸ {machine_id}[/bold cyan]{warning}",
                    "",
                    "",
                    "",
                    "",
                    key=f"header_{machine_id}",
                )
                self._row_machine_map[row_idx] = machine_id
                row_idx += 1

            for s in machine_sessions:
                created = s.created_at[:16].replace("T", " ") if s.created_at else ""
                path_display = s.path if len(s.path) <= 30 else "..." + s.path[-27:]
                indent = "  " if not self._filter_machine else ""
                # Color-code status
                status = s.status
                if status == "active":
                    status_display = "[bold green]active[/bold green]"
                elif status == "detached":
                    status_display = "[yellow]detached[/yellow]"
                elif status == "destroyed":
                    status_display = "[red]destroyed[/red]"
                else:
                    status_display = f"[dim]{status}[/dim]"
                table.add_row(
                    f"{indent}[bold]{s.name or s.daemon_session_id[:8]}[/bold]",
                    f"[dim]{path_display}[/dim]",
                    f"[cyan]{s.mode}[/cyan]",
                    status_display,
                    f"[dim]{created}[/dim]",
                    key=f"session_{s.channel_id}",
                )
                self._row_session_map[row_idx] = s
                row_idx += 1

        sort_label = "newest first" if self._sort_descending else "oldest first"
        filter_info = f" | Machine: {self._filter_machine}" if self._filter_machine else ""
        info.update(f"[dim]{len(sessions)} session(s) | Sort: {sort_label} (t) | Remove (r/del){filter_info}[/dim]")

    def _load_sessions(self):
        """Load sessions from the SessionRouter SQLite database."""
        try:
            from head.session_router import SessionRouter

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

    def _get_router(self):
        """Get a SessionRouter instance, or None."""
        try:
            from head.session_router import SessionRouter

            candidates = [
                Path.home() / ".codecast" / "sessions.db",
                Path(__file__).parent.parent / "sessions.db",
            ]
            for db_path in candidates:
                if db_path.exists():
                    return SessionRouter(str(db_path))
        except Exception:
            pass
        return None

    def action_toggle_sort(self) -> None:
        self._sort_descending = not self._sort_descending
        table = self.query_one("#sessions_table", DataTable)
        self._populate_sessions(table)

    def action_remove_session(self) -> None:
        table = self.query_one("#sessions_table", DataTable)
        if table.row_count == 0:
            return
        cursor_row = table.cursor_row
        session = self._row_session_map.get(cursor_row)
        if session is None:
            # Cursor is on a machine header row
            self.notify("Select a session row to remove.", severity="warning")
            return
        router = self._get_router()
        if router:
            router.destroy(session.channel_id)
            self._sessions = [s for s in self._sessions if s.channel_id != session.channel_id]
            self._populate_sessions(table)
            name = session.name or session.daemon_session_id[:8]
            self.notify(f"Removed session: {name}")
        else:
            self.notify("Cannot find session database.", severity="error")

    def action_cursor_down(self) -> None:
        try:
            table = self.query_one("#sessions_table", DataTable)
            table.action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            table = self.query_one("#sessions_table", DataTable)
            table.action_cursor_up()
        except Exception:
            pass

    def action_open_or_enter(self) -> None:
        """Open a machine's sessions when on a header row."""
        if self._filter_machine:
            return
        table = self.query_one("#sessions_table", DataTable)
        if table.row_count == 0:
            return
        cursor_row = table.cursor_row
        machine_id = self._row_machine_map.get(cursor_row)
        if machine_id:
            self._filter_machine = machine_id
            self._init_filtered = False  # drilled down within screen, back clears filter
            self._populate_sessions(table)

    def action_go_back(self) -> None:
        if self._filter_machine and not self._init_filtered:
            # Drilled down within sessions view — go back to all-machines view
            self._filter_machine = None
            table = self.query_one("#sessions_table", DataTable)
            self._populate_sessions(table)
        else:
            # Either unfiltered or opened pre-filtered from dashboard — pop screen
            self.app.pop_screen()


# ---------------------------------------------------------------------------
# Start / Stop WebUI
# ---------------------------------------------------------------------------


class StartWebUIScreen(Screen):
    """Screen for starting or stopping the WebUI."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

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
            msg = (
                f"WebUI is [bold green]● running[/bold green]"
                f" on [bold white]http://127.0.0.1:{webui_port}[/bold white]"
                f" [dim](pid={webui_pid})[/dim]"
            )
        else:
            msg = "WebUI is [bold red]○ stopped[/bold red]"

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

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#webui_menu", OptionList).action_cursor_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#webui_menu", OptionList).action_cursor_up()
        except Exception:
            pass

    def action_go_back(self) -> None:
        self.app.pop_screen()
