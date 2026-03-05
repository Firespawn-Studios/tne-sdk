# tne_sdk/tests/test_local_memory.py
import tempfile
import importlib.util
import sys
from pathlib import Path

# Load LocalMemory directly without triggering tne_sdk.__init__
# (which imports LLM providers that may not be installed in test envs)
_src = Path(__file__).parent.parent / "src"

def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_base = _load_module("tne_sdk.memory.base", _src / "tne_sdk" / "memory" / "base.py")
_lm   = _load_module("tne_sdk.memory.local_memory", _src / "tne_sdk" / "memory" / "local_memory.py")
LocalMemory = _lm.LocalMemory


def _mem(name: str, tmp: str) -> LocalMemory:
    """Helper: return an open LocalMemory instance."""
    m = LocalMemory(name, tmp)
    m.open()
    return m


def test_memory_full_cycle():
    """
    Tests the full lifecycle of the LocalMemory class.
    Uses the new open()/close() + with-block-as-transaction pattern.
    """
    with tempfile.TemporaryDirectory() as tmp:

        # Basic CRUD
        mem = _mem("test", tmp)
        with mem:
            tid = mem.add_task("Conquer the Grid", priority=99)
            mem.update_task_status(tid, "completed")
            assert mem.get_active_tasks() == [], "Expected no active tasks after completion"
        mem.close()

        # Persistence
        mem = _mem("test", tmp)
        with mem:
            mem.set_knowledge("foo", {"bar": 42})
        mem.close()

        mem = _mem("test", tmp)
        with mem:
            assert mem.get_knowledge("foo") == {"bar": 42}, "Knowledge not persisted"
        mem.close()

        # Event deduplication
        mem = _mem("test", tmp)
        with mem:
            state = {
                "tick_info": {"current_tick": 10},
                "recent_events": [
                    {"tick": 9,  "event_type": "combat",   "description": "Dealt 40 damage"},
                    {"tick": 10, "event_type": "dialogue", "description": "NPC said hello"},
                    # Duplicate: same tick/type/description
                    {"tick": 9,  "event_type": "combat",   "description": "Dealt 40 damage"},
                ],
            }
            mem.update(state)
            events = mem.get_events_since(0)
            assert len(events) == 2, f"Expected 2 deduplicated events, got {len(events)}"
        mem.close()

        # Compaction
        mem = _mem("test", tmp)
        with mem:
            pruned = mem.prune_reflected_events(up_to_tick=9)
            assert pruned == 1, f"Expected 1 pruned event, got {pruned}"
            events = mem.get_events_since(0)
            assert len(events) == 1, f"Expected 1 event after compaction, got {len(events)}"
        mem.close()

        # Knowledge summary
        mem = _mem("test", tmp)
        with mem:
            mem.set_knowledge("summary:narrative", "The war for Sector 7 begins.")
            mem.set_knowledge("strategy:combat:rust_reaver", "Kite + use_item repair before engaging")
            text = mem.get_knowledge_summary_text()
            assert "Sector 7" in text
            assert "rust_reaver" in text
        mem.close()

        # DB stats
        mem = _mem("test", tmp)
        with mem:
            stats = mem.get_db_stats()
            assert "events" in stats and "db_size_kb" in stats
        mem.close()

        # Hierarchical tasks + dependency unblocking
        mem = _mem("test", tmp)
        with mem:
            p_id  = mem.add_task("Parent Task", priority=100)
            c1_id = mem.add_task("Child 1 (Prereq)", parent_id=p_id)
            c2_id = mem.add_task("Child 2 (Prereq)", parent_id=p_id)
            c3_id = mem.add_task("Child 3 (Dependent)", parent_id=p_id, depends_on_ids=[c1_id, c2_id])

            c3 = mem.get_task(c3_id)
            assert c3 and c3["status"] == "blocked", "Dependent task should be blocked initially"

            mem.update_task_status(c1_id, "completed")
            c3 = mem.get_task(c3_id)
            assert c3 and c3["status"] == "blocked", "Still blocked after one prereq"

            mem.update_task_status(c2_id, "completed")
            c3 = mem.get_task(c3_id)
            assert c3 and c3["status"] == "pending", "Should be pending after all prereqs met"
        mem.close()
