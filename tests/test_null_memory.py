"""Tests for NullMemory: verifies the no-op contract."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tne_sdk.memory.null_memory import NullMemory


class TestNullMemory:
    def test_lifecycle(self):
        mem = NullMemory()
        mem.open()
        mem.close()

    def test_context_manager(self):
        mem = NullMemory()
        mem.open()
        with mem:
            pass
        mem.close()

    def test_knowledge_returns_none(self):
        mem = NullMemory()
        mem.open()
        with mem:
            mem.set_knowledge("key", "value")
            assert mem.get_knowledge("key") is None
            assert mem.get_knowledge_by_prefix("k") == {}
            assert mem.get_knowledge_summary_text() == ""
        mem.close()

    def test_directives_noop(self):
        mem = NullMemory()
        mem.open()
        with mem:
            did = mem.add_directive("test directive")
            assert did == 0
            mem.update_directive_status(did, "completed")
            assert mem.get_active_directives() == []
        mem.close()

    def test_tasks_noop(self):
        mem = NullMemory()
        mem.open()
        with mem:
            tid = mem.add_task("test task", priority=50)
            assert tid == 0
            assert mem.get_task(tid) is None
            assert mem.get_active_tasks() == []
            assert mem.add_tasks_hierarchical([{"description": "x"}]) == []
            assert mem.reset_active_tasks() == 0
        mem.close()

    def test_events_noop(self):
        mem = NullMemory()
        mem.open()
        with mem:
            assert mem.get_events_since(0) == []
            assert mem.prune_reflected_events(100) == 0
        mem.close()

    def test_entities_noop(self):
        mem = NullMemory()
        mem.open()
        with mem:
            mem.set_entity("npc:1", "npc", {"name": "test"})
            assert mem.get_entity("npc:1") is None
        mem.close()

    def test_stats(self):
        mem = NullMemory()
        mem.open()
        with mem:
            stats = mem.get_db_stats()
            assert stats["events"] == 0
            assert stats["knowledge"] == 0
            assert stats["tasks_active"] == 0
            assert stats["tasks_total"] == 0
            assert stats["entities"] == 0
            assert stats["db_size_kb"] == 0.0
        mem.close()

    def test_update_noop(self):
        mem = NullMemory()
        mem.open()
        with mem:
            mem.update({"tick_info": {"current_tick": 1}})
            assert mem.get_knowledge("last_known_state") is None
        mem.close()
