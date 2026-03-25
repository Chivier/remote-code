"""Custom widgets for the Codecast TUI dashboard."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

from textual.widgets import DataTable, Static

from head.process_monitor import (
    DAEMON_PID_FILE,
    HEAD_PID_FILE,
    WEBUI_PID_FILE,
    WEBUI_PORT_FILE,
    daemon_healthy,
    find_process,
    pid_alive,
    read_pid_file,
    read_port_file,
)


def _gather_status(config_path: str) -> dict:
    """Gather all status info (may block on HTTP/subprocess). Run off main thread."""
    # Daemon
    port = read_port_file()
    daemon_pid = read_pid_file(DAEMON_PID_FILE) or find_process("codecast-daemon")
    daemon_running = port is not None and daemon_healthy(port)

    # Head
    head_pid = read_pid_file(HEAD_PID_FILE)
    head_running = head_pid is not None and pid_alive(head_pid)

    # WebUI
    webui_pid = read_pid_file(WEBUI_PID_FILE)
    webui_port = read_pid_file(WEBUI_PORT_FILE)
    webui_running = webui_pid is not None and pid_alive(webui_pid)

    # CLIs
    claude_path = shutil.which("claude")
    codex_path = shutil.which("codex")

    # Daemon version
    daemon_version = ""
    codecast_version = ""
    try:
        from head.daemon_installer import get_current_version, get_daemon_version
        from head.peer_manager import resolve_daemon_binary

        codecast_version = get_current_version()
        binary = resolve_daemon_binary()
        daemon_version = get_daemon_version(binary) if binary else ""
    except Exception:
        pass

    version_mismatch = bool(daemon_version and codecast_version and daemon_version != codecast_version)

    # Bots
    bots: list[str] = []
    if config_path:
        try:
            from head.config import load_config

            cfg = load_config(config_path)
            if cfg.bot:
                if cfg.bot.discord and getattr(cfg.bot.discord, "token", None):
                    bots.append("Discord")
                if cfg.bot.telegram and getattr(cfg.bot.telegram, "token", None):
                    bots.append("Telegram")
                if getattr(cfg.bot, "lark", None) and getattr(cfg.bot.lark, "app_id", None):
                    bots.append("Lark")
        except Exception:
            pass

    return dict(
        port=port,
        daemon_pid=daemon_pid,
        daemon_running=daemon_running,
        head_pid=head_pid,
        head_running=head_running,
        webui_pid=webui_pid,
        webui_port=webui_port,
        webui_running=webui_running,
        claude_path=claude_path,
        codex_path=codex_path,
        bots=bots,
        daemon_version=daemon_version,
        codecast_version=codecast_version,
        version_mismatch=version_mismatch,
    )


def _render_status(info: dict) -> str:
    """Build Rich markup status text from gathered info (no I/O)."""
    lines: list[str] = []

    # Daemon
    if info["daemon_running"]:
        pid_part = f" [dim](pid={info['daemon_pid']})[/dim]" if info["daemon_pid"] else ""
        lines.append(
            f"[bold]Daemon[/bold]  [dim](agent manager)[/dim]  "
            f"[bold green]● running[/bold green] on port [bold white]{info['port']}[/bold white]{pid_part}"
        )
    else:
        lines.append("[bold]Daemon[/bold]  [dim](agent manager)[/dim]  [bold red]○ stopped[/bold red]")

    if info.get("version_mismatch"):
        lines.append(
            f"         [bold yellow]⚠ Version mismatch![/bold yellow] "
            f"daemon [bold]{info['daemon_version']}[/bold] ≠ "
            f"codecast [bold]{info['codecast_version']}[/bold]  "
            "[dim]Press [bold]d[/bold] to update[/dim]"
        )

    # Head
    if info["head_running"]:
        bots = info["bots"]
        bot_info = f" | bots: [bold white]{', '.join(bots)}[/bold white]" if bots else ""
        lines.append(
            f"[bold]Head[/bold]    [dim](chat bots)[/dim]      "
            f"[bold green]● running[/bold green] [dim](pid={info['head_pid']})[/dim]{bot_info}"
        )
    else:
        lines.append("[bold]Head[/bold]    [dim](chat bots)[/dim]      [bold red]○ stopped[/bold red]")

    # WebUI
    if info["webui_running"]:
        lines.append(
            f"[bold]WebUI[/bold]   [dim](dashboard)[/dim]      "
            f"[bold green]● running[/bold green] on [bold white]http://127.0.0.1:{info['webui_port']}[/bold white]"
            f" [dim](pid={info['webui_pid']})[/dim]"
        )
    else:
        lines.append("[bold]WebUI[/bold]   [dim](dashboard)[/dim]      [bold red]○ stopped[/bold red]")

    # CLIs
    cli_parts: list[str] = []
    if info["claude_path"]:
        cli_parts.append("Claude [green]✓[/green]")
    if info["codex_path"]:
        cli_parts.append("Codex [green]✓[/green]")
    if cli_parts:
        lines.append(f"[bold]CLIs[/bold]    [dim](AI agents)[/dim]       {' | '.join(cli_parts)}")
    else:
        lines.append(
            "[bold]CLIs[/bold]    [dim](AI agents)[/dim]       [bold red]✗ none found[/bold red] [dim](install claude or codex)[/dim]"
        )

    return "\n".join(lines)


class StatusPanel(Static):
    """Displays component status with colored indicators."""

    DEFAULT_CSS = """
    StatusPanel {
        padding: 0 1;
    }
    """

    REFRESH_INTERVAL = 2.0  # seconds between auto-refresh

    def __init__(self, config_path: str = "", **kwargs) -> None:
        super().__init__("", **kwargs)
        self.config_path = config_path
        self._refresh_pending = False

    def on_mount(self) -> None:
        self.update("[dim]Loading status...[/dim]")
        self._async_refresh()
        self.set_interval(self.REFRESH_INTERVAL, self._async_refresh)

    def _async_refresh(self) -> None:
        """Kick off a background thread to gather status, then update UI."""
        if self._refresh_pending:
            return  # Don't stack up concurrent refreshes
        self._refresh_pending = True

        def _run() -> None:
            try:
                info = _gather_status(self.config_path)
                text = _render_status(info)
            except Exception:
                text = "[bold red]Error checking status[/bold red]"
            try:
                self.app.call_from_thread(self._apply_status, text)
            except Exception:
                pass  # Widget may be gone

        threading.Thread(target=_run, daemon=True).start()

    def _apply_status(self, text: str) -> None:
        """Apply status text on the main thread."""
        self._refresh_pending = False
        self.update(text)

    def refresh_status(self) -> None:
        """Re-check and update all status indicators (non-blocking)."""
        self._async_refresh()


class MachineTable(DataTable):
    """DataTable showing configured machines from config."""

    DEFAULT_CSS = """
    MachineTable {
        height: auto;
        max-height: 16;
    }
    """

    def __init__(self, config_path: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.config_path = config_path

    def on_mount(self) -> None:
        self.add_columns("Name", "Transport", "Host", "Port", "Projects")
        self.cursor_type = "row"
        self._populate()

    def _populate(self) -> None:
        try:
            from head.config import load_config

            cfg = load_config(self.config_path)
            machines = getattr(cfg, "peers", {}) or {}
        except Exception:
            machines = {}

        for name, machine in sorted(machines.items()):
            transport = getattr(machine, "transport", "?")
            if transport == "ssh":
                host = getattr(machine, "ssh_host", "") or ""
            elif transport == "http":
                host = getattr(machine, "address", "") or ""
            else:
                host = "localhost"

            # Truncate long hostnames
            if len(host) > 24:
                host = host[:21] + "..."

            port = str(getattr(machine, "port", 9100) or 9100)
            project_path = getattr(machine, "project_path", "~/Projects")
            self.add_row(name, transport, host, port, project_path, key=name)

    @property
    def machine_count(self) -> int:
        return self.row_count

    def refresh_machines(self) -> None:
        """Clear and re-populate from config."""
        self.clear()
        self._populate()

    def get_selected_machine_name(self) -> str | None:
        """Return the name of the currently selected machine, or None.

        Uses the row key which is the clean machine name (no Rich markup).
        For unknown machines, the key is ``unknown_<name>`` — strip the prefix.
        """
        if self.row_count == 0:
            return None
        try:
            row_key = list(self.rows.keys())[self.cursor_row]
            key_str = str(row_key.value)
            if key_str.startswith("unknown_"):
                return key_str[len("unknown_") :]
            return key_str
        except Exception:
            return None

    def is_selected_unknown(self) -> bool:
        """Return True if the currently selected row is an unknown machine."""
        if self.row_count == 0:
            return False
        try:
            row_key = list(self.rows.keys())[self.cursor_row]
            return str(row_key.value).startswith("unknown_")
        except Exception:
            return False

    def set_unknown_machines(self, names: list[str]) -> None:
        """Append rows for machines found in sessions but missing from config."""
        # Remove existing unknown rows first
        keys_to_remove = [k for k in self.rows if str(k.value).startswith("unknown_")]
        for key in keys_to_remove:
            self.remove_row(key)

        for name in names:
            self.add_row(
                f"[yellow]{name}[/yellow]",
                "[yellow]\u26a0 unknown[/yellow]",
                "[dim]—[/dim]",
                "[dim]—[/dim]",
                "[dim]—[/dim]",
                key=f"unknown_{name}",
            )
