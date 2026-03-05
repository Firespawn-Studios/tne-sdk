"""
TNE-SDK Example: Custom MemoryProvider

Shows how to implement MemoryProvider for an alternative backend.
This example uses a simple in-memory dict (not persistent, for illustration only).
"""
from __future__ import annotations

import json
from typing import Any

from tne_sdk import Agent, AgentConfig, TNEClient, MemoryProvider
from tne_sdk.llm.providers import OpenAICompatibleProvider


class DictMemory(MemoryProvider):
    """
    Minimal in-memory MemoryProvider backed by plain Python dicts.

    Not persistent across restarts. For testing / demonstration only.
    """

    def __init__(self) -> None:
        self._knowledge: dict[str, Any]  = {}
        self._directives: list[dict]     = []
        self._tasks: list[dict]          = []
        self._events: list[dict]         = []
        self._entities: dict[str, dict]  = {}
        self._next_id = 1

    # ── Lifecycle ──────────────────────────────────────────────────────────── #
    def open(self)  -> None: pass
    def close(self) -> None: pass
    def __enter__(self) -> DictMemory: return self
    def __exit__(self, *_: Any) -> None: pass

    # ── Core ──────────────────────────────────────────────────────────────── #
    def update(self, state: dict) -> None:
        tick = state.get("tick_info", {}).get("current_tick")
        if tick:
            for e in state.get("recent_events", []):
                self._events.append(e)

    def get_knowledge(self, key: str) -> Any | None:
        return self._knowledge.get(key)

    def set_knowledge(self, key: str, value: Any) -> None:
        if value is None:
            self._knowledge.pop(key, None)
        else:
            self._knowledge[key] = value

    def get_knowledge_by_prefix(self, prefix: str) -> dict[str, Any]:
        return {k: v for k, v in self._knowledge.items() if k.startswith(prefix)}

    def get_knowledge_summary_text(self) -> str:
        return json.dumps({k: v for k, v in self._knowledge.items()
                           if k.startswith("summary:")}, indent=2)

    # ── Directives ─────────────────────────────────────────────────────────── #
    def add_directive(self, text: str) -> int:
        did = self._next_id; self._next_id += 1
        self._directives.append({"directive_id": did, "text": text, "status": "active"})
        return did

    def update_directive_status(self, directive_id: int, status: str) -> None:
        for d in self._directives:
            if d["directive_id"] == directive_id:
                d["status"] = status

    def get_active_directives(self, limit: int = 5) -> list[dict]:
        return [d for d in self._directives if d["status"] == "active"][-limit:]

    # ── Tasks ──────────────────────────────────────────────────────────────── #
    def add_task(self, description: str, priority: int = 10,
                 parent_id: int | None = None,
                 depends_on_ids: list[int] | None = None) -> int:
        tid = self._next_id; self._next_id += 1
        self._tasks.append({
            "task_id": tid, "description": description, "priority": priority,
            "status": "pending", "parent_id": parent_id,
        })
        return tid

    def update_task_status(self, task_id: int, status: str) -> None:
        for t in self._tasks:
            if t["task_id"] == task_id:
                t["status"] = status

    def get_task(self, task_id: int) -> dict | None:
        return next((t for t in self._tasks if t["task_id"] == task_id), None)

    def get_active_tasks(self, limit: int = 15) -> list[dict]:
        active = [t for t in self._tasks if t["status"] in ("pending", "in_progress")]
        return sorted(active, key=lambda t: t["priority"], reverse=True)[:limit]

    def add_tasks_hierarchical(self, tasks: list[dict]) -> list[int]:
        return [self.add_task(t["description"], t.get("priority", 10)) for t in tasks]

    def reset_active_tasks(self) -> int:
        count = 0
        for t in self._tasks:
            if t["status"] in ("pending", "in_progress"):
                t["status"] = "failed"; count += 1
        return count

    # ── Events ─────────────────────────────────────────────────────────────── #
    def get_events_since(self, tick: int, limit: int = 2000) -> list[dict]:
        return [e for e in self._events if e.get("tick", 0) > tick][-limit:]

    def prune_reflected_events(self, up_to_tick: int) -> int:
        before = len(self._events)
        self._events = [e for e in self._events if e.get("tick", 0) > up_to_tick]
        return before - len(self._events)

    # ── Entities ───────────────────────────────────────────────────────────── #
    def set_entity(self, entity_id: str, entity_type: str, data: dict) -> None:
        self._entities[entity_id] = data

    def get_entity(self, entity_id: str) -> dict | None:
        return self._entities.get(entity_id)

    # ── Stats ──────────────────────────────────────────────────────────────── #
    def get_db_stats(self) -> dict:
        active = sum(1 for t in self._tasks if t["status"] in ("pending", "in_progress"))
        return {
            "events": len(self._events),
            "knowledge": len(self._knowledge),
            "tasks_active": active,
            "tasks_total": len(self._tasks),
            "entities": len(self._entities),
            "last_reflection_tick": self._knowledge.get("last_reflection_tick", 0),
            "db_size_kb": 0.0,
        }


# ── Wire it up ────────────────────────────────────────────────────────────── #
if __name__ == "__main__":
    import asyncio, logging
    logging.basicConfig(level=logging.INFO)

    cfg    = AgentConfig(model="local-model", temperature=0.7)
    client = TNEClient(api_key="YOUR_KEY")
    memory = DictMemory()
    llm    = OpenAICompatibleProvider(base_url="http://localhost:8000/v1")

    agent = Agent(config=cfg, client=client, memory=memory, llm_provider=llm,
                  name="custom-mem-demo")
    asyncio.run(agent.run())
