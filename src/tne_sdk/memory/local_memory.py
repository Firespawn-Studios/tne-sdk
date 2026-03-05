"""
TNE-SDK: Local Memory

SQLite-backed persistent memory for a Null Epoch agent.

Lifecycle
---------
Call ``memory.open()`` once at startup and ``memory.close()`` in a finally
block.  Use ``with memory:`` as a lightweight transaction fence (BEGIN /
COMMIT / ROLLBACK) around any group of reads or writes.

Schema
------
knowledge  : key-value store for distilled facts, summaries, and strategies
tasks      : strategic goals with priority and status
entities   : NPC / item / location records, updated on each sighting
events     : raw game event log consumed by the reflection cycle and pruned
             after each successful reflection
directives : high-priority instructions injected by a user or coach
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from .base import MemoryProvider

logger = logging.getLogger(__name__)


class LocalMemory(MemoryProvider):
    """
    Agent long-term memory backed by a local SQLite database.

    Call ``open()`` before use and ``close()`` when done.  Wrap reads and
    writes in ``with memory:`` blocks to batch them into a single transaction:

        memory.open()
        try:
            with memory:
                memory.update(state)
                prompt = build_prompt(state, memory)
            action = llm.call(prompt)
        finally:
            memory.close()
    """

    def __init__(self, agent_name: str, db_path: Path | str = "logs") -> None:
        db_dir = Path(db_path)
        db_dir.mkdir(parents=True, exist_ok=True)
        self.db_file = db_dir / f"agent_memory_{agent_name}.db"
        self._conn: sqlite3.Connection | None = None
        self._depth: int = 0
        logger.debug("Memory for '%s' at %s", agent_name, self.db_file)

    # ── Lifecycle ──────────────────────────────────────────────────────────── #

    def open(self) -> None:
        """Open a persistent connection and initialise the schema (idempotent)."""
        if self._conn is not None:
            return  # already open
        self._conn = sqlite3.connect(self.db_file, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._initialize_db()
        self._conn.commit()
        logger.debug("Memory connection opened: %s", self.db_file)

    def close(self) -> None:
        """Commit any pending transaction and close the connection."""
        if self._conn is not None:
            try:
                self._conn.commit()
            finally:
                self._conn.close()
                self._conn = None
            logger.debug("Memory connection closed: %s", self.db_file)

    # ── Transaction context manager (lightweight) ──────────────────────────── #

    def __enter__(self) -> LocalMemory:
        if self._conn is None:
            raise RuntimeError(
                "LocalMemory connection is not open.  Call memory.open() first."
            )
        if self._depth == 0:
            self._conn.execute("BEGIN")
        self._depth += 1
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._conn is None:
            return
        self._depth -= 1
        if self._depth == 0:
            if exc_type is None:
                self._conn.execute("COMMIT")
            else:
                self._conn.execute("ROLLBACK")

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError(
                "Memory must be used inside a 'with' block after open()."
            )
        return self._conn

    # ── Schema ────────────────────────────────────────────────────────────── #

    def _initialize_db(self) -> None:
        c = self._get_conn().cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS knowledge (
                key          TEXT PRIMARY KEY,
                value_json   TEXT NOT NULL,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id    INTEGER,
                description  TEXT    NOT NULL,
                status       TEXT    NOT NULL DEFAULT 'pending',
                priority     INTEGER DEFAULT 10,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (parent_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS task_dependencies (
                task_id       INTEGER NOT NULL,
                depends_on_id INTEGER NOT NULL,
                PRIMARY KEY (task_id, depends_on_id),
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
                FOREIGN KEY (depends_on_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                entity_id   TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                data_json   TEXT NOT NULL,
                last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                tick       INTEGER NOT NULL,
                event_type TEXT    NOT NULL,
                data_json  TEXT    NOT NULL,
                source_key TEXT    UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_tick ON events (tick)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_task_dependencies ON task_dependencies (depends_on_id)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS directives (
                directive_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                text            TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'active',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Schema migrations (safe for existing DBs)
        for migration in (
            "ALTER TABLE events ADD COLUMN source_key TEXT UNIQUE",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source ON events (source_key) WHERE source_key IS NOT NULL",
            "ALTER TABLE tasks ADD COLUMN parent_id INTEGER REFERENCES tasks(task_id) ON DELETE CASCADE",
        ):
            try:
                c.execute(migration)
            except sqlite3.OperationalError:
                pass

    # ── Knowledge ─────────────────────────────────────────────────────────── #

    def set_knowledge(self, key: str, value: Any) -> None:
        if value is None:
            self._get_conn().execute("DELETE FROM knowledge WHERE key = ?", (key,))
            return
        self._get_conn().execute("""
            INSERT INTO knowledge (key, value_json) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json   = excluded.value_json,
                last_updated = CURRENT_TIMESTAMP
        """, (key, json.dumps(value)))

    def get_knowledge(self, key: str) -> Any | None:
        row = self._get_conn().execute(
            "SELECT value_json FROM knowledge WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row["value_json"]) if row else None

    def get_knowledge_by_prefix(self, prefix: str) -> dict[str, Any]:
        rows = self._get_conn().execute(
            "SELECT key, value_json FROM knowledge WHERE key LIKE ?", (prefix + "%",)
        ).fetchall()
        return {r["key"]: json.loads(r["value_json"]) for r in rows}

    def get_knowledge_summary_text(self) -> str:
        narrative  = self.get_knowledge("summary:narrative") or ""
        economic   = self.get_knowledge("summary:economic")  or ""
        strategies = self.get_knowledge_by_prefix("strategy:combat:")
        tasks      = self.get_active_tasks(limit=10)

        parts: list[str] = []
        if narrative:
            parts.append(f"Narrative: {narrative}")
        if economic:
            parts.append(f"Economy: {economic}")
        if strategies:
            strat_lines = "; ".join(
                f"{k.split(':')[-1]}: {str(v)[:100]}" for k, v in strategies.items()
            )
            parts.append(f"Combat strategies: {strat_lines}")
        if tasks:
            task_lines = "; ".join(
                f"[pri={t['priority']}] {t['description'][:80]} ({t['status']})"
                for t in tasks
            )
            parts.append(f"Active tasks: {task_lines}")

        return "\n".join(parts)

    # ── Directives ─────────────────────────────────────────────────────────── #

    def add_directive(self, text: str) -> int:
        cur = self._get_conn().execute(
            "INSERT INTO directives (text) VALUES (?)", (text,)
        )
        new_id = cur.lastrowid
        if not new_id:
            raise RuntimeError("Failed to create directive.")
        return new_id

    def update_directive_status(self, directive_id: int, status: str) -> None:
        self._get_conn().execute(
            "UPDATE directives SET status = ? WHERE directive_id = ?", (status, directive_id)
        )

    def get_active_directives(self, limit: int = 5) -> list[dict]:
        rows = self._get_conn().execute(
            """SELECT directive_id, text, created_at FROM directives
               WHERE status = 'active'
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Tasks ─────────────────────────────────────────────────────────────── #

    def add_task(
        self,
        description: str,
        priority: int = 10,
        parent_id: int | None = None,
        depends_on_ids: list[int] | None = None,
    ) -> int:
        status = "blocked" if depends_on_ids else "pending"
        cur = self._get_conn().execute(
            "INSERT INTO tasks (description, priority, parent_id, status) VALUES (?, ?, ?, ?)",
            (description, priority, parent_id, status),
        )
        new_task_id = cur.lastrowid
        if not new_task_id:
            raise RuntimeError("Failed to retrieve new task ID.")

        if depends_on_ids:
            self._get_conn().executemany(
                "INSERT INTO task_dependencies (task_id, depends_on_id) VALUES (?, ?)",
                [(new_task_id, dep_id) for dep_id in depends_on_ids],
            )
        return new_task_id

    def update_task_status(self, task_id: int, status: str) -> None:
        self._get_conn().execute(
            """UPDATE tasks
               SET status = ?,
                   completed_at = CASE WHEN ? IN ('completed', 'failed')
                                       THEN CURRENT_TIMESTAMP ELSE NULL END
               WHERE task_id = ?""",
            (status, status, task_id),
        )
        if status == "completed":
            self._unblock_dependent_tasks(task_id)

    def _unblock_dependent_tasks(self, completed_task_id: int) -> None:
        dependent_tasks = self._get_conn().execute(
            "SELECT task_id FROM task_dependencies WHERE depends_on_id = ?",
            (completed_task_id,),
        ).fetchall()

        for row in dependent_tasks:
            task_to_check = row["task_id"]
            unmet_deps = self._get_conn().execute(
                """SELECT COUNT(*) FROM task_dependencies dep
                   JOIN   tasks t ON dep.depends_on_id = t.task_id
                   WHERE  dep.task_id = ? AND t.status != 'completed'""",
                (task_to_check,),
            ).fetchone()
            if unmet_deps and unmet_deps[0] == 0:
                self.update_task_status(task_to_check, "pending")

    def get_task(self, task_id: int) -> dict | None:
        row = self._get_conn().execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_active_tasks(self, limit: int = 15) -> list[dict]:
        rows = self._get_conn().execute(
            """SELECT task_id, parent_id, description, status, priority
               FROM tasks
               WHERE status IN ('pending', 'in_progress')
               ORDER BY priority DESC, created_at ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_tasks_hierarchical(self, tasks: list[dict]) -> list[int]:
        if not tasks:
            return []

        conn   = self._get_conn()
        cursor = conn.cursor()
        desc_to_id_map: dict[str, int] = {}
        new_task_ids: list[int] = []

        for task_data in tasks:
            cursor.execute(
                "INSERT INTO tasks (description, priority, status) VALUES (?, ?, ?)",
                (task_data["description"], task_data.get("priority", 10), "pending"),
            )
            new_id = cursor.lastrowid
            if not new_id:
                raise RuntimeError("Failed to create a task and get its ID.")
            desc_to_id_map[task_data["description"]] = new_id
            new_task_ids.append(new_id)

        for task_data in tasks:
            current_task_id = desc_to_id_map[task_data["description"]]
            has_deps = False

            if parent_desc := task_data.get("parent_description"):
                if parent_id := desc_to_id_map.get(parent_desc):
                    cursor.execute(
                        "UPDATE tasks SET parent_id = ? WHERE task_id = ?",
                        (parent_id, current_task_id),
                    )

            if depends_on_descs := task_data.get("depends_on"):
                dep_ids = [desc_to_id_map[d] for d in depends_on_descs if d in desc_to_id_map]
                if dep_ids:
                    has_deps = True
                    cursor.executemany(
                        "INSERT INTO task_dependencies (task_id, depends_on_id) VALUES (?, ?)",
                        [(current_task_id, dep_id) for dep_id in dep_ids],
                    )

            if has_deps:
                cursor.execute(
                    "UPDATE tasks SET status = 'blocked' WHERE task_id = ?",
                    (current_task_id,),
                )

        return new_task_ids

    def reset_active_tasks(self) -> int:
        cur = self._get_conn().execute(
            """UPDATE tasks
               SET status = 'failed',
                   completed_at = CURRENT_TIMESTAMP
               WHERE status IN ('pending', 'in_progress')"""
        )
        updated_count: int = cur.rowcount or 0
        if updated_count:
            logger.info("Reset %d active tasks to 'failed' status.", updated_count)
        return updated_count

    # ── Events ────────────────────────────────────────────────────────────── #

    def add_event(self, tick: int, event: dict) -> None:
        event_type = event.get("event_type", "unknown")
        desc       = event.get("description", "")
        source_key = f"{tick}:{event_type}:{desc[:120]}"
        self._get_conn().execute(
            "INSERT OR IGNORE INTO events (tick, event_type, data_json, source_key) VALUES (?, ?, ?, ?)",
            (tick, event_type, json.dumps(event), source_key),
        )

    def get_events_since(self, tick: int, limit: int = 2000) -> list[dict]:
        rows = self._get_conn().execute("""
            SELECT data_json FROM events
            WHERE  tick > ?
            ORDER  BY tick ASC, event_id ASC
            LIMIT  ?
        """, (tick, limit)).fetchall()
        return [json.loads(r["data_json"]) for r in rows]

    def prune_reflected_events(self, up_to_tick: int) -> int:
        cur = self._get_conn().execute(
            "DELETE FROM events WHERE tick <= ?", (up_to_tick,)
        )
        return cur.rowcount  # type: ignore[return-value]

    # ── Entities ──────────────────────────────────────────────────────────── #

    def set_entity(self, entity_id: str, entity_type: str, data: dict) -> None:
        self._get_conn().execute("""
            INSERT INTO entities (entity_id, entity_type, data_json) VALUES (?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                data_json = excluded.data_json,
                last_seen = CURRENT_TIMESTAMP
        """, (entity_id, entity_type, json.dumps(data)))

    def get_entity(self, entity_id: str) -> dict | None:
        row = self._get_conn().execute(
            "SELECT data_json FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        return json.loads(row["data_json"]) if row else None

    # ── Stats ─────────────────────────────────────────────────────────────── #

    def get_db_stats(self) -> dict:
        conn = self._get_conn()
        events    = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        knowledge = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        tasks_act = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('pending','in_progress')"
        ).fetchone()[0]
        tasks_all = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        entities  = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        last_refl = self.get_knowledge("last_reflection_tick") or 0
        db_size   = self.db_file.stat().st_size / 1024 if self.db_file.exists() else 0.0

        return {
            "events":               events,
            "knowledge":            knowledge,
            "tasks_active":         tasks_act,
            "tasks_total":          tasks_all,
            "entities":             entities,
            "last_reflection_tick": last_refl,
            "db_size_kb":           db_size,
        }

    def vacuum(self) -> None:
        """Run SQLite VACUUM to reclaim space and defragment the database.

        VACUUM cannot run inside a transaction, so this must be called
        outside of any ``with memory:`` block.
        """
        if self._conn is None:
            return
        if self._depth > 0:
            logger.warning("vacuum() called inside a transaction — skipping.")
            return
        try:
            self._conn.execute("VACUUM")
            logger.debug("Memory database vacuumed: %s", self.db_file)
        except sqlite3.OperationalError as e:
            logger.warning("VACUUM failed: %s", e)

    # ── State ingestion ───────────────────────────────────────────────────── #

    def update(self, state: dict) -> None:
        if not state:
            return

        tick = state.get("tick_info", {}).get("current_tick")
        if not tick:
            return

        self.set_knowledge("last_known_state", {
            "tick":      tick,
            "territory": state.get("current_territory"),
            "integrity": state.get("integrity"),
            "power":     state.get("power"),
            "credits":   state.get("credits"),
            "level":     state.get("level"),
            "faction":   state.get("faction"),
        })
        self.set_knowledge("inventory", state.get("inventory", {}))

        for npc in state.get("nearby_npcs", []):
            self.set_entity(f"npc:{npc['npc_id']}", "npc", npc)
        for agent in state.get("nearby_agents", []):
            self.set_entity(f"agent:{agent['agent_id']}", "agent", agent)

        territory_id = state.get("current_territory")
        if territory_id:
            gather_action = next(
                (a for a in state.get("available_actions", []) if a.get("action") == "gather"),
                None,
            )
            nodes: list[str] = []
            if gather_action:
                nodes = [
                    n["node_id"]
                    for n in gather_action.get("parameters", {})
                                          .get("node_id", {})
                                          .get("nodes", [])
                ]
            self.set_entity(f"location:{territory_id}", "location", {
                "lore":   state.get("world_context"),
                "market": state.get("local_market", {}),
                "shop":   state.get("shop_inventory", {}),
                "nodes":  nodes,
            })

        for event in state.get("recent_events", []):
            event_tick = event.get("tick", tick)
            self.add_event(event_tick, event)
