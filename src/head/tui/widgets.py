"""Custom widgets for the Codecast TUI dashboard."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from textual.widgets import DataTable, Static

from head.cli import (
    _HEAD_PID_FILE,
    _WEBUI_PID_FILE,
    _WEBUI_PORT_FILE,
    _daemon_healthy,
    _pid_alive,
    _read_pid_file,
    _read_port_file,
    _find_process,
)


class StatusPanel(Static):
    """Displays component status with colored indicators."""

    DEFAULT_CSS = """
    StatusPanel {
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)

    def on_mount(self) -> None:
        self.update(self._build_status())

    def _build_status(self) -> str:
        lines: list[str] = []

        # Head Node
        head_pid = _read_pid_file(_HEAD_PID_FILE)
        if head_pid is not None and _pid_alive(head_pid):
            lines.append(f"Head:   [green]●[/green] running (pid={head_pid})")
        else:
            lines.append("Head:   [dim]○[/dim] not running")

        # Daemon
        port = _read_port_file()
        daemon_pid = _find_process("codecast-daemon")
        if port is not None and _daemon_healthy(port):
            pid_part = f" (pid={daemon_pid})" if daemon_pid else ""
            lines.append(f"Daemon: [green]●[/green] running on port {port}{pid_part}")
        else:
            lines.append("Daemon: [dim]○[/dim] not running")

        # WebUI
        webui_pid = _read_pid_file(_WEBUI_PID_FILE)
        webui_port = _read_pid_file(_WEBUI_PORT_FILE)
        if webui_pid is not None and _pid_alive(webui_pid):
            lines.append(f"WebUI:  [green]●[/green] running on http://127.0.0.1:{webui_port} (pid={webui_pid})")
        else:
            lines.append("WebUI:  [dim]○[/dim] not running")

        # Claude CLI
        claude_path = shutil.which("claude")
        if claude_path:
            lines.append(f"Claude: [green]✓[/green] available ({claude_path})")
        else:
            lines.append("Claude: [red]✗[/red] not found")

        return "\n".join(lines)

    def refresh_status(self) -> None:
        """Re-check and update all status indicators."""
        self.update(self._build_status())


class PeerTable(DataTable):
    """DataTable showing configured peers from config."""

    DEFAULT_CSS = """
    PeerTable {
        height: auto;
        max-height: 16;
    }
    """

    def __init__(self, config_path: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.config_path = config_path

    def on_mount(self) -> None:
        self.add_columns("Name", "Transport", "Host", "Port")
        self.cursor_type = "row"
        self._populate()

    def _populate(self) -> None:
        try:
            from head.config_v2 import load_config_v2

            cfg = load_config_v2(self.config_path)
            peers = getattr(cfg, "peers", {}) or {}
        except Exception:
            peers = {}

        for name, peer in peers.items():
            transport = getattr(peer, "transport", "?")
            if transport == "ssh":
                host = getattr(peer, "ssh_host", "") or ""
            elif transport == "http":
                host = getattr(peer, "address", "") or ""
            else:
                host = "localhost"

            # Truncate long hostnames
            if len(host) > 24:
                host = host[:21] + "..."

            port = str(getattr(peer, "port", 9100) or 9100)
            self.add_row(name, transport, host, port)

    @property
    def peer_count(self) -> int:
        return self.row_count
