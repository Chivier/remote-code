"""Main Textual app for the Codecast TUI."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.theme import Theme

from .screens import DashboardScreen, SetupWizardScreen


_DEFAULT_CONFIG = str(Path.home() / ".codecast" / "config.yaml")

CODECAST_THEME = Theme(
    name="codecast",
    primary="#5c9cf5",
    secondary="#fab283",
    accent="#a3be8c",
    warning="#ebcb8b",
    error="#bf616a",
    success="#a3be8c",
    dark=True,
)


class CodecastApp(App):
    """Codecast interactive terminal UI.

    Shows a setup wizard when no config exists, otherwise shows the dashboard.
    """

    TITLE = "Codecast"
    CSS = """
    #wizard_container, #dashboard_container {
        padding: 1 2;
    }
    #welcome {
        margin-bottom: 1;
    }
    #status_panel_container {
        border: solid $primary;
        padding: 1 2;
        margin: 1 0;
        height: auto;
    }
    #status_panel_container > Static {
        color: $text;
    }
    #peer_table_container {
        border: solid $primary;
        padding: 1 2;
        margin: 1 0;
        height: auto;
        max-height: 20;
    }
    #status_panel_title, #peer_table_title {
        text-style: bold;
        margin-bottom: 1;
    }
    DataTable {
        height: auto;
        max-height: 14;
    }
    """

    def __init__(self, config_path: str | None = None) -> None:
        super().__init__()
        self.config_path: str = config_path or _DEFAULT_CONFIG
        self._version = self._get_version()
        self.register_theme(CODECAST_THEME)
        self.theme = "codecast"

    def on_mount(self) -> None:
        """Decide which screen to show based on config existence."""
        if Path(self.config_path).exists():
            self.push_screen(DashboardScreen(self.config_path, self._version))
        else:
            self.push_screen(SetupWizardScreen(self.config_path, self._version))

    @staticmethod
    def _get_version() -> str:
        try:
            from head.__version__ import __version__

            return f"v{__version__}"
        except Exception:
            return ""
