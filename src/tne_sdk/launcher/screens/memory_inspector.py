"""
TNE-SDK Launcher: Memory Inspector Modal

Displays a snapshot of the agent's SQLite memory.
"""
from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static, TabbedContent, TabPane

from ...memory.base import MemoryProvider


class MemoryInspectorModal(ModalScreen[None]):
    """Modal displaying a live snapshot of the agent's SQLite memory."""

    DEFAULT_CSS = """
    MemoryInspectorModal {
        align: center middle;
    }
    #inspector-container {
        width: 90%;
        height: 90%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    DataTable {
        height: 100%;
        width: 100%;
    }
    TabbedContent {
        height: 1fr;
    }
    #btn-close {
        dock: bottom;
        margin-top: 1;
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("ctrl+i", "dismiss", "Close"),
    ]

    def __init__(self, memory: MemoryProvider) -> None:
        super().__init__()
        self._memory = memory

    def compose(self) -> ComposeResult:
        with Static(id="inspector-container"):
            with TabbedContent():
                with TabPane("Knowledge", id="tab-knowledge"):
                    yield DataTable(id="dt-knowledge")
                with TabPane("Active Tasks", id="tab-tasks"):
                    yield DataTable(id="dt-tasks")
                with TabPane("Entities", id="tab-entities"):
                    yield DataTable(id="dt-entities")
                with TabPane("Stats", id="tab-stats"):
                    yield DataTable(id="dt-stats")
            yield Button("Close \\[Esc]", id="btn-close", variant="primary")

    def on_mount(self) -> None:
        # Snapshot the memory database securely within a read transaction.
        with self._memory:
            knowledge = self._memory.get_knowledge_by_prefix("")
            tasks = self._memory.get_active_tasks(limit=100)
            entities = self._memory.get_all_entities(limit=100)
            stats = self._memory.get_db_stats()

        self._populate_knowledge(knowledge)
        self._populate_tasks(tasks)
        self._populate_entities(entities)
        self._populate_stats(stats)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-close":
            self.dismiss(None)

    def _populate_knowledge(self, knowledge: dict[str, Any]) -> None:
        dt = self.query_one("#dt-knowledge", DataTable)
        dt.add_columns("Key", "Value")
        for k, v in sorted(knowledge.items()):
            dt.add_row(k, json.dumps(v) if isinstance(v, (dict, list)) else str(v))

    def _populate_tasks(self, tasks: list[dict]) -> None:
        dt = self.query_one("#dt-tasks", DataTable)
        dt.add_columns("ID", "Parent", "Priority", "Status", "Description")
        for t in tasks:
            dt.add_row(
                str(t.get("task_id", "")),
                str(t.get("parent_id", "")),
                str(t.get("priority", "")),
                str(t.get("status", "")),
                str(t.get("description", "")),
            )

    def _populate_entities(self, entities: list[dict]) -> None:
        dt = self.query_one("#dt-entities", DataTable)
        dt.add_columns("Type", "Name/ID", "Data")
        for e in entities:
            # Guess standard ID keys
            eid = e.get("name") or e.get("entity_id") or e.get("id") or "?"
            etype = e.get("entity_type") or e.get("type") or "unknown"
            
            # Format data nicely
            data_str = json.dumps(e)
            if len(data_str) > 200:
                data_str = data_str[:197] + "..."
            dt.add_row(etype, str(eid), data_str)

    def _populate_stats(self, stats: dict[str, Any]) -> None:
        dt = self.query_one("#dt-stats", DataTable)
        dt.add_columns("Metric", "Value")
        for k, v in stats.items():
            dt.add_row(k, str(v))
