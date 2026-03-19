"""Custom widgets for the Codecast TUI dashboard."""

from __future__ import annotations

import os
import shutil
import subprocess
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

    def on_mount(self) -> None:
        self.update(self._build_status())
        self.set_interval(self.REFRESH_INTERVAL, self.refresh_status)

    def _get_bot_summary(self) -> list[str]:
        """Return list of configured bot descriptions from config."""
        if not self.config_path:
            return []
        try:
            from head.config import load_config

            cfg = load_config(self.config_path)
        except Exception:
            return []
        bots: list[str] = []
        if cfg.bot:
            if cfg.bot.discord and getattr(cfg.bot.discord, "token", None):
                bots.append("Discord")
            if cfg.bot.telegram and getattr(cfg.bot.telegram, "token", None):
                bots.append("Telegram")
            if getattr(cfg.bot, "lark", None) and getattr(cfg.bot.lark, "app_id", None):
                bots.append("Lark")
        return bots

    def _build_status(self) -> str:
        lines: list[str] = []

        # Daemon (agent process manager)
        port = read_port_file()
        daemon_pid = read_pid_file(DAEMON_PID_FILE) or find_process("codecast-daemon")
        if port is not None and daemon_healthy(port):
            pid_part = f" [dim](pid={daemon_pid})[/dim]" if daemon_pid else ""
            lines.append(
                f"[bold]Daemon[/bold]  [dim](agent manager)[/dim]  "
                f"[bold green]● running[/bold green] on port [bold white]{port}[/bold white]{pid_part}"
            )
        else:
            lines.append("[bold]Daemon[/bold]  [dim](agent manager)[/dim]  [bold red]○ stopped[/bold red]")

        # Head Node (chat bot bridge)
        head_pid = read_pid_file(HEAD_PID_FILE)
        head_running = head_pid is not None and pid_alive(head_pid)
        if head_running:
            bots = self._get_bot_summary()
            bot_info = f" | bots: [bold white]{', '.join(bots)}[/bold white]" if bots else ""
            lines.append(
                f"[bold]Head[/bold]    [dim](chat bots)[/dim]      "
                f"[bold green]● running[/bold green] [dim](pid={head_pid})[/dim]{bot_info}"
            )
        else:
            lines.append("[bold]Head[/bold]    [dim](chat bots)[/dim]      [bold red]○ stopped[/bold red]")

        # WebUI (web dashboard)
        webui_pid = read_pid_file(WEBUI_PID_FILE)
        webui_port = read_pid_file(WEBUI_PORT_FILE)
        if webui_pid is not None and pid_alive(webui_pid):
            lines.append(
                f"[bold]WebUI[/bold]   [dim](dashboard)[/dim]      "
                f"[bold green]● running[/bold green] on [bold white]http://127.0.0.1:{webui_port}[/bold white]"
                f" [dim](pid={webui_pid})[/dim]"
            )
        else:
            lines.append("[bold]WebUI[/bold]   [dim](dashboard)[/dim]      [bold red]○ stopped[/bold red]")

        # Claude CLI
        claude_path = shutil.which("claude")
        codex_path = shutil.which("codex")
        cli_parts: list[str] = []
        if claude_path:
            cli_parts.append(f"Claude [green]✓[/green]")
        if codex_path:
            cli_parts.append(f"Codex [green]✓[/green]")
        if cli_parts:
            lines.append(f"[bold]CLIs[/bold]    [dim](AI agents)[/dim]       {' | '.join(cli_parts)}")
        else:
            lines.append("[bold]CLIs[/bold]    [dim](AI agents)[/dim]       [bold red]✗ none found[/bold red] [dim](install claude or codex)[/dim]")

        return "\n".join(lines)

    def refresh_status(self) -> None:
        """Re-check and update all status indicators."""
        self.update(self._build_status())


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
