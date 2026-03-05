"""
TNE-SDK Launcher: Root Textual Application
"""
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult

from .. import __version__
from ..profile_store import ProfileStore


class TNELauncherApp(App):
    """NULL EPOCH agent launcher, Textual TUI."""

    CSS_PATH = Path(__file__).parent / "tne_launcher.tcss"
    TITLE    = f"NULL EPOCH - Agent Launcher  v{__version__}"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, data_dir: Path | None = None) -> None:
        super().__init__()
        profiles_path = (data_dir / "agents.json") if data_dir else None
        self.store    = ProfileStore(path=profiles_path)
        self.store.load()

        self.log_dir: Path = (
            data_dir / "logs" if data_dir
            else Path.home() / ".tne_sdk" / "logs"
        )
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def on_mount(self) -> None:
        from .screens.main_menu import MainMenuScreen
        self.push_screen(MainMenuScreen(self.store, self.log_dir))

    def compose(self) -> ComposeResult:
        return iter([])
