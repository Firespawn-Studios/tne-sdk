"""
TNE-SDK: Memory Provider Base

Abstract base class for all memory providers.  Any object that implements
this interface can be used as the memory backend for an Agent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MemoryProvider(ABC):
    """Abstract base class for agent memory systems."""

    # ── Lifecycle ──────────────────────────────────────────────────────────── #

    @abstractmethod
    def open(self) -> None:
        """Open the persistent backing store.  Called once by Agent.run()."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close the backing store.  Called in Agent.run() finally block."""
        ...

    @abstractmethod
    def __enter__(self) -> MemoryProvider:
        """Enter a transaction context (BEGIN).  Connection must already be open."""
        ...

    @abstractmethod
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """COMMIT on success, ROLLBACK on exception."""
        ...

    # ── Core state ─────────────────────────────────────────────────────────── #

    @abstractmethod
    def update(self, state: dict) -> None:
        """Ingest a full game state snapshot."""
        ...

    @abstractmethod
    def get_knowledge(self, key: str) -> Any | None:
        """Retrieve a single value from the knowledge store."""
        ...

    @abstractmethod
    def set_knowledge(self, key: str, value: Any) -> None:
        """Upsert a key-value pair into the knowledge store."""
        ...

    @abstractmethod
    def get_knowledge_by_prefix(self, prefix: str) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_knowledge_summary_text(self) -> str:
        ...

    # ── Directives ─────────────────────────────────────────────────────────── #

    @abstractmethod
    def add_directive(self, text: str) -> int:
        ...

    @abstractmethod
    def update_directive_status(self, directive_id: int, status: str) -> None:
        ...

    @abstractmethod
    def get_active_directives(self, limit: int = 5) -> list[dict]:
        ...

    # ── Tasks ──────────────────────────────────────────────────────────────── #

    @abstractmethod
    def add_task(
        self,
        description: str,
        priority: int = 10,
        parent_id: int | None = None,
        depends_on_ids: list[int] | None = None,
    ) -> int:
        ...

    @abstractmethod
    def update_task_status(self, task_id: int, status: str) -> None:
        ...

    @abstractmethod
    def get_task(self, task_id: int) -> dict | None:
        ...

    @abstractmethod
    def get_active_tasks(self, limit: int = 15) -> list[dict]:
        ...

    @abstractmethod
    def add_tasks_hierarchical(self, tasks: list[dict]) -> list[int]:
        ...

    @abstractmethod
    def reset_active_tasks(self) -> int:
        ...

    # ── Events ─────────────────────────────────────────────────────────────── #

    @abstractmethod
    def get_events_since(self, tick: int, limit: int = 2000) -> list[dict]:
        ...

    @abstractmethod
    def prune_reflected_events(self, up_to_tick: int) -> int:
        ...

    # ── Entities ───────────────────────────────────────────────────────────── #

    @abstractmethod
    def set_entity(self, entity_id: str, entity_type: str, data: dict) -> None:
        ...

    @abstractmethod
    def get_entity(self, entity_id: str) -> dict | None:
        ...

    # ── Stats ──────────────────────────────────────────────────────────────── #

    @abstractmethod
    def get_db_stats(self) -> dict:
        """Return a dict with keys: events, knowledge, tasks_active, tasks_total,
        entities, last_reflection_tick, db_size_kb."""
        ...

    def vacuum(self) -> None:
        """Reclaim space in the backing store.  Optional - default is a no-op."""
        pass
