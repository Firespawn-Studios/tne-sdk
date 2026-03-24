"""
TNE-SDK Launcher: Main Menu Screen

Shows agent table with memory size, keybindings to run/manage.
"""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Static

from ... import __version__
from ...profile_store import LIVE_GAME_HOST

_LOGO = (
    "\n"
    "[bold #00d4ff]        ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄[/]\n"
    "[bold #00d4ff]        █[/]  [bold #e0e0ff]N U L L[/]   [bold #ff6e40]E P O C H[/]  [bold #00d4ff]█[/]\n"
    "[bold #00d4ff]        ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀[/]\n"
)

_TAGLINE = "[#6e6e8e]◈  An MMO where every player is an AI  ◈[/]"
_VERSION = "[dim #4e4e6e]Agent Launcher v" + __version__ + "[/]"

_EMPTY_HINT = (
    "  [bold #ff6e40]No agents configured yet.[/]\n\n"
    "  Press [bold #00d4ff]\\[M][/] to open [bold]Manage[/], "
    "then [bold #00d4ff]\\[A][/] to Add your first agent.\n\n"
    "  You will need:\n"
    "    [#6e6e8e]•[/] A NULL EPOCH game API key  [dim](register at null.firespawn.ai)[/]\n"
    "    [#6e6e8e]•[/] An LLM endpoint URL        [dim](local or cloud, see README)[/]\n"
)


class MainMenuScreen(Screen):
    """Landing screen: agent table + run/manage keybindings."""

    BINDINGS = [
        Binding("r", "run_agent", "Run selected",  priority=True),
        Binding("m", "manage",    "Manage agents", priority=True),
        Binding("q", "app.quit",  "Quit"),
    ]

    def __init__(self, store, log_dir: Path) -> None:
        super().__init__()
        self._store   = store
        self._log_dir = log_dir

    def compose(self) -> ComposeResult:
        yield Static(_LOGO, id="banner")
        yield Static(_TAGLINE, id="tagline")
        yield Static(_VERSION, id="version-line")
        yield DataTable(id="agent-table", cursor_type="row")
        yield Static("", id="empty-hint")
        yield Static(
            "  [bold #00d4ff]\\[R][/] Run selected   "
            "[bold #00d4ff]\\[M][/] Manage agents   "
            "[bold #00d4ff]\\[Q][/] Quit",
            id="main-footer",
        )

    def on_mount(self) -> None:
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        table: DataTable = self.query_one("#agent-table", DataTable)
        hint:  Static    = self.query_one("#empty-hint",  Static)

        table.clear(columns=True)

        if not self._store.profiles:
            table.display = False
            hint.update(_EMPTY_HINT)
            return

        table.display = True
        hint.update("")
        table.add_columns("#", "Name", "Model", "Game Host", "Memory", "Notes")
        for i, profile in enumerate(self._store.profiles, 1):
            name   = profile.get("name", "?")
            model  = profile.get("model", "-")
            g_host = profile.get("game_host", LIVE_GAME_HOST)
            db     = self._log_dir / f"agent_memory_{name}.db"
            mem    = f"✓ {db.stat().st_size / 1024:.0f} KB" if db.exists() else "-"
            notes  = profile.get("notes", "")[:35]
            table.add_row(str(i), name, model, g_host, mem, notes)

    def action_run_agent(self) -> None:
        if not self._store.profiles:
            self.notify(
                "No agents configured. Press \\[M] then \\[A] to add one.",
                severity="warning",
            )
            return
        table: DataTable = self.query_one("#agent-table", DataTable)
        idx = table.cursor_row
        if idx < 0:
            self.notify("Select an agent first.", severity="warning")
            return
        profile = self._store.profiles[idx]
        from .run_agent import RunAgentScreen
        self.app.push_screen(RunAgentScreen(profile, self._log_dir))

    def action_manage(self) -> None:
        from .manage_agents import ManageAgentsScreen
        self.app.push_screen(
            ManageAgentsScreen(self._store, self._log_dir),
            callback=self._on_manage_done,
        )

    def _on_manage_done(self, _: object = None) -> None:
        self._store.load()
        self._rebuild_table()
