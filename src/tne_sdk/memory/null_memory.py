"""
TNE-SDK: Null Memory Provider

Stateless memory implementation where every write is a no-op and every read
returns empty / None.  Use with ``--no-memory`` / stateless mode.

Agents work transparently through the same MemoryProvider interface.
"""
from __future__ import annotations

from typing import Any

from .base import MemoryProvider


class NullMemory(MemoryProvider):
    """No-op memory provider for stateless / testing use."""

    # ── Lifecycle ──────────────────────────────────────────────────────────── #

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> NullMemory:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    # ── Core state ─────────────────────────────────────────────────────────── #

    def update(self, state: dict) -> None:
        pass

    def get_knowledge(self, key: str) -> Any | None:
        return None

    def set_knowledge(self, key: str, value: Any) -> None:
        pass

    def get_knowledge_by_prefix(self, prefix: str) -> dict[str, Any]:
        return {}

    def get_knowledge_summary_text(self) -> str:
        return ""

    # ── Directives ─────────────────────────────────────────────────────────── #

    def add_directive(self, text: str) -> int:
        return 0

    def update_directive_status(self, directive_id: int, status: str) -> None:
        pass

    def get_active_directives(self, limit: int = 5) -> list[dict]:
        return []

    # ── Tasks ─────────────────────────────────────────────────────────────── #

    def add_task(
        self,
        description: str,
        priority: int = 10,
        parent_id: int | None = None,
        depends_on_ids: list[int] | None = None,
    ) -> int:
        return 0

    def update_task_status(self, task_id: int, status: str) -> None:
        pass

    def get_task(self, task_id: int) -> dict | None:
        return None

    def get_active_tasks(self, limit: int = 15) -> list[dict]:
        return []

    def add_tasks_hierarchical(self, tasks: list[dict]) -> list[int]:
        return []

    def reset_active_tasks(self) -> int:
        return 0

    # ── Events ─────────────────────────────────────────────────────────────── #

    def get_events_since(self, tick: int, limit: int = 2000) -> list[dict]:
        return []

    def prune_reflected_events(self, up_to_tick: int) -> int:
        return 0

    # ── Entities ───────────────────────────────────────────────────────────── #

    def set_entity(self, entity_id: str, entity_type: str, data: dict) -> None:
        pass

    def get_entity(self, entity_id: str) -> dict | None:
        return None

    # ── Stats ──────────────────────────────────────────────────────────────── #

    def get_db_stats(self) -> dict:
        return {
            "events":               0,
            "knowledge":            0,
            "tasks_active":         0,
            "tasks_total":          0,
            "entities":             0,
            "last_reflection_tick": 0,
            "db_size_kb":           0.0,
        }
