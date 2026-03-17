"""Main Textual app for the Codecast TUI."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from .screens import DashboardScreen, SetupWizardScreen


_DEFAULT_CONFIG = str(Path.home() / ".codecast" / "config.yaml")


class CodecastApp(App):
    """Codecast interactive terminal UI.

    Shows a setup wizard when no config exists, otherwise shows the dashboard.
    """

    TITLE = "Codecast"
    CSS = """
    #wizard_container, #dashboard_container {
        padding: 1 2;
    }
    #welcome, #status {
        margin-bottom: 1;
    }
    """

    def __init__(self, config_path: str | None = None) -> None:
        super().__init__()
        self.config_path: str = config_path or _DEFAULT_CONFIG
        self._version = self._get_version()

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
