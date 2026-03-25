"""
TNE-SDK Launcher: Manage Agents Screen

Add / edit / delete agents.  View memory stats.  Clear memory DB.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Input, Label, Static, TextArea


# ── Prompt field definitions ──────────────────────────────────────────────── #
# Maps the inline-text profile key to (display label, hint text).
_PROMPT_FIELDS: list[tuple[str, str, str]] = [
    (
        "system_prompt_text",
        "Action system prompt",
        "Override the main per-tick decision prompt. Leave empty for default.\n"
        "  Example: \"You are an AI agent playing NULL EPOCH. Choose one action per turn...\"",
    ),
    (
        "reflection_system_prompt_text",
        "Reflection system prompt",
        "Override the reflection system prompt. Leave empty for default.\n"
        "  Example: \"You are a Memory Analyst AI. Distill events into structured JSON.\"",
    ),
    (
        "reflection_user_prompt_text",
        "Reflection user prompt",
        "This is the agent's reflection template.\n"
        "  REQUIRED placeholders: {knowledge_section}, {hypotheses_section}, {event_data}\n"
        "  These placeholders get filled in with the actual game data.\n"
        "  Example: \"KNOWLEDGE:\\n{knowledge_section}\\nEVENTS:\\n{event_data}\\n...\"",
    ),
    (
        "tactical_system_prompt_text",
        "Tactical system prompt",
        "Override the tactical review system prompt. Leave empty for default.\n"
        "  Example: \"You are a Tactical Analyst AI. Prune stale goals, keep the list tight.\"",
    ),
    (
        "tactical_user_prompt_text",
        "Tactical user prompt",
        "This is the agent's strategizing template.\n"
        "  REQUIRED placeholders: {status_section}, {tasks_section}, {events_section}\n"
        "  These placeholders get filled in with the actual game data.\n"
        "  Example: \"STATUS:\\n{status_section}\\nGOALS:\\n{tasks_section}\\n...\"",
    ),
]

# Map of legacy file-path keys to corresponding inline-text keys.
_FILE_TO_TEXT_KEY = {
    "system_prompt_file":            "system_prompt_text",
    "reflection_system_prompt_file": "reflection_system_prompt_text",
    "reflection_user_prompt_file":   "reflection_user_prompt_text",
    "tactical_system_prompt_file":   "tactical_system_prompt_text",
    "tactical_user_prompt_file":     "tactical_user_prompt_text",
}


def _migrate_file_prompts(profile: dict[str, Any]) -> dict[str, Any]:
    """If a profile has legacy *_file paths but no *_text, try to read the
    file and pre-populate the text field so the user sees the content in the
    editor.  Non-destructive: only fills in blanks."""
    for file_key, text_key in _FILE_TO_TEXT_KEY.items():
        if profile.get(text_key):
            continue  # already has inline text, skip
        path_str = profile.get(file_key, "")
        if not path_str:
            continue
        p = Path(path_str).expanduser()
        if p.is_file():
            try:
                profile[text_key] = p.read_text(encoding="utf-8")
            except Exception:
                pass  # unreadable - leave blank, defaults will be used in this case
    return profile


# ── Agent form modal ──────────────────────────────────────────────────────── #

class AgentFormModal(ModalScreen[dict | None]):
    """Add / edit form for a single agent profile."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Save"),
    ]

    # Form layout: tuples of (profile_key, display_label, placeholder).
    # A bare string entry acts as a section separator label.
    _LAYOUT: list[tuple[str, str, str] | str] = [
        # ── Connection ──
        "── Connection ──",
        ("name",        "Agent name",          "Spectre-7"),
        ("api_key",     "Game API key",        "ne_xxxxxxxxxxxx"),
        ("llm_url",     "LLM endpoint URL",    "http://localhost:8000/v1"),
        ("llm_api_key", "LLM API key",         "leave blank for local inference"),
        ("model",       "Model name",          "Qwen/Qwen3-14B"),

        # ── Sampling ──
        "── Sampling ──",
        ("temperature",     "Temperature",        "0.7  (0.0–2.0)"),
        ("top_p",           "Top-P",              "0.8  (0.0–1.0, nucleus sampling)"),
        ("top_k",           "Top-K",              "20  (0 = disabled)"),
        ("presence_penalty","Presence penalty",   "1.5  (0.0–2.0, reduces repetition)"),

        # ── Thinking mode ──
        "── Reasoning / Thinking ──",
        ("enable_thinking",           "Allow reasoning",       "false  (tries to suppress <think> reasoning, experimental). Token budgets auto-scale when on."),
        ("thinking_temperature",      "Thinking temperature",  "1.0  (reflection/tactical when reasoning is allowed)"),
        ("thinking_top_p",            "Thinking top-P",        "0.95"),
        ("thinking_presence_penalty", "Thinking presence pen", "1.5"),

        # ── Token budgets ──
        "── Token Budgets ──",
        ("max_tokens",            "Action max tokens",     "2048"),
        ("max_tokens_reflection", "Reflection max tokens", "6144"),
        ("max_tokens_tactical",   "Tactical max tokens",   "1024"),

        # ── Cognitive cycles ──
        "── Cognitive Cycles ──",
        ("reflection_cooldown_ticks",      "Reflection cooldown",  "200  (ticks between reflections)"),
        ("tactical_review_cooldown_ticks", "Tactical cooldown",    "10  (ticks between reviews)"),
        ("llm_timeout",                    "LLM timeout (sec)",    "120  (raise for slow models)"),

        # ── Misc ──
        "── Misc ──",
        ("meta_directive", "Meta goal",           "e.g. Top the leaderboards, Dominate the markets"),
        ("log_payloads",   "Log LLM payloads",    "false  (true to write debug logs)"),
        ("notes",          "Notes (optional)",     ""),
    ]

    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._existing = _migrate_file_prompts(dict(existing)) if existing else {}
        self._edit_mode = bool(existing)

    def compose(self) -> ComposeResult:
        title = "Edit Agent" if self._edit_mode else "Add Agent"
        yield Static(f" {title} ", id="form-title")
        with VerticalScroll(id="form-container"):
            # ── Standard Input fields ──
            for entry in self._LAYOUT:
                if isinstance(entry, str):
                    yield Static(entry, classes="form-section")
                    continue
                key, label, placeholder = entry
                yield Label(f"{label}:", classes="form-label")
                yield Input(
                    value       = str(self._existing.get(key, "")),
                    placeholder = placeholder,
                    id          = f"field-{key}",
                )

            # ── Custom Prompts (TextArea) ──
            yield Static(
                "── Custom Prompts (paste text, or leave empty for defaults) ──",
                classes="form-section",
            )
            yield Static(
                "Paste your prompt text directly below. Empty = built-in default.",
                classes="prompt-hint",
            )
            for text_key, label, hint in _PROMPT_FIELDS:
                yield Label(f"{label}:", classes="form-label")
                yield Static(hint, classes="prompt-hint")
                yield TextArea(
                    self._existing.get(text_key, ""),
                    id=f"field-{text_key}",
                    classes="prompt-editor",
                    show_line_numbers=False,
                    tab_behavior="indent",
                )

            yield Button("Save  [Ctrl+S]", variant="primary",  id="btn-save")
            yield Button("Cancel [Esc]",   variant="default",  id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            self.action_submit()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        # ── Collect standard Input fields ──
        fields = [e for e in self._LAYOUT if isinstance(e, tuple)]
        result: dict[str, Any] = {}
        for key, _, _ in fields:
            widget = self.query_one(f"#field-{key}", Input)
            result[key] = widget.value.strip()

        # ── Collect prompt TextArea fields ──
        for text_key, _, _ in _PROMPT_FIELDS:
            ta: TextArea = self.query_one(f"#field-{text_key}", TextArea)
            content = ta.text.strip()
            if content:
                result[text_key] = content
            # else: we just omit key entirely and we will use the config defaults

        # ── Remove legacy file-path keys so they don't shadow inline text ──
        for file_key in _FILE_TO_TEXT_KEY:
            result.pop(file_key, None)

        # ── Required field validation ──
        errors: list[str] = []
        if not result.get("name"):
            errors.append("Agent name is required.")
        if not result.get("api_key") or result["api_key"].startswith("YOUR_"):
            errors.append("Game API key is required.")
        if not result.get("llm_url") or not result["llm_url"].startswith("http"):
            errors.append("LLM endpoint URL must start with http:// or https://.")
        if not result.get("model"):
            errors.append("Model name is required.")
        if errors:
            self.notify("  ".join(errors), severity="error")
            return

        # ── Validate user-prompt placeholders if custom text was provided ──
        _REQUIRED_PLACEHOLDERS = {
            "reflection_user_prompt_text": [
                "{knowledge_section}", "{hypotheses_section}", "{event_data}",
            ],
            "tactical_user_prompt_text": [
                "{status_section}", "{tasks_section}", "{events_section}",
            ],
        }
        for tkey, placeholders in _REQUIRED_PLACEHOLDERS.items():
            text = result.get(tkey, "")
            if not text:
                continue
            missing = [p for p in placeholders if p not in text]
            if missing:
                label = tkey.replace("_text", "").replace("_", " ").title()
                errors.append(
                    f"{label}: missing required placeholders: {', '.join(missing)}"
                )
        if errors:
            self.notify("  ".join(errors), severity="error")
            return

        # ── Parse numeric fields (strip hint text after first space) ──
        _FLOAT_FIELDS = {
            "temperature": 0.7,
            "top_p": 0.8,
            "presence_penalty": 1.5,
            "thinking_temperature": 1.0,
            "thinking_top_p": 0.95,
            "thinking_presence_penalty": 1.5,
            "llm_timeout": 120.0,
        }
        for fname, default in _FLOAT_FIELDS.items():
            try:
                result[fname] = float((result.get(fname) or str(default)).split()[0])
            except ValueError:
                result[fname] = default

        _INT_FIELDS = {
            "top_k": 20,
            "max_tokens": 2048,
            "max_tokens_reflection": 6144,
            "max_tokens_tactical": 1024,
            "reflection_cooldown_ticks": 200,
            "tactical_review_cooldown_ticks": 10,
        }
        for fname, default in _INT_FIELDS.items():
            try:
                result[fname] = int((result.get(fname) or str(default)).split()[0])
            except ValueError:
                result[fname] = default

        # ── Parse bool fields ──
        for bool_field in ("enable_thinking", "log_payloads"):
            raw = (result.get(bool_field) or "false").split()[0].lower()
            result[bool_field] = raw in ("true", "1", "yes")

        self.dismiss(result)


# ── Confirm modal ─────────────────────────────────────────────────────────── #

class ConfirmModal(ModalScreen[bool]):
    """Simple yes/no confirmation dialog."""

    BINDINGS = [
        Binding("y", "yes", "Yes"),
        Binding("n", "no",  "No"),
        Binding("escape", "no", "No"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Static(id="form-container"):
            yield Static(self._message)
            yield Button("Yes [Y]", variant="error",   id="btn-yes")
            yield Button("No  [N]", variant="default", id="btn-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


# ── Stats modal ───────────────────────────────────────────────────────────── #

class StatsModal(ModalScreen[None]):
    """Display memory DB stats for an agent."""

    BINDINGS = [Binding("escape", "dismiss_modal", "Close")]

    def __init__(self, name: str, stats: dict) -> None:
        super().__init__()
        self._name  = name
        self._stats = stats

    def compose(self) -> ComposeResult:
        lines = (
            f"Agent         : {self._name}\n"
            f"Events        : {self._stats.get('events', 0)}\n"
            f"Knowledge     : {self._stats.get('knowledge', 0)}\n"
            f"Active tasks  : {self._stats.get('tasks_active', 0)} / {self._stats.get('tasks_total', 0)}\n"
            f"Entities      : {self._stats.get('entities', 0)}\n"
            f"Last refl tick: {self._stats.get('last_reflection_tick', 0)}\n"
            f"DB size       : {self._stats.get('db_size_kb', 0.0):.1f} KB"
        )
        with Static(id="form-container"):
            yield Static(f" Memory Stats: {self._name} ")
            yield Static(lines)
            yield Button("Close [Esc]", id="btn-close")

    def on_button_pressed(self, _: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


# ── Main manage screen ────────────────────────────────────────────────────── #

class ManageAgentsScreen(Screen):
    """CRUD interface for agent profiles."""

    BINDINGS = [
        Binding("a",      "add",    "Add"),
        Binding("e",      "edit",   "Edit"),
        Binding("d",      "delete", "Delete"),
        Binding("s",      "stats",  "Stats"),
        Binding("c",      "clear",  "Clear memory"),
        Binding("escape", "back",   "Back"),
    ]

    def __init__(self, store, log_dir: Path) -> None:
        super().__init__()
        self._store   = store
        self._log_dir = log_dir

    def compose(self) -> ComposeResult:
        yield Static(" ◈  Manage Agents ", id="banner")
        yield DataTable(id="manage-list", cursor_type="row")
        yield Static(
            "  \\[A] Add  \\[E] Edit  \\[D] Delete  \\[S] Stats  \\[C] Clear memory  \\[Esc] Back",
            id="manage-footer",
        )

    def on_mount(self) -> None:
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        table: DataTable = self.query_one("#manage-list", DataTable)
        table.clear(columns=True)
        table.add_columns("Name", "Model", "LLM URL", "Memory")
        for p in self._store.profiles:
            name = p.get("name", "?")
            db   = self._log_dir / f"agent_memory_{name}.db"
            mem  = f"✓ {db.stat().st_size / 1024:.0f} KB" if db.exists() else "-"
            url  = p.get("llm_url", "-")
            url_s = url[:28] + "..." if len(url) > 29 else url
            table.add_row(name, p.get("model", "-"), url_s, mem)

    def _selected_profile(self) -> dict | None:
        table: DataTable = self.query_one("#manage-list", DataTable)
        idx = table.cursor_row
        if idx < 0 or idx >= len(self._store.profiles):
            return None
        return self._store.profiles[idx]

    # ── Actions ──────────────────────────────────────────────────────────── #

    def action_add(self) -> None:
        self.app.push_screen(AgentFormModal(), callback=self._on_add_done)

    def _on_add_done(self, result: dict | None) -> None:
        if not result:
            return
        try:
            self._store.add(result)
            self._store.save()
            self._rebuild_table()
            self.notify(f"Added agent '{result['name']}'.")
        except Exception as exc:
            self.notify(str(exc), severity="error")

    def action_edit(self) -> None:
        profile = self._selected_profile()
        if not profile:
            self.notify("No agent selected.", severity="warning")
            return
        self.app.push_screen(AgentFormModal(existing=profile), callback=self._on_edit_done)

    def _on_edit_done(self, result: dict | None) -> None:
        if not result:
            return
        profile = self._selected_profile()
        if not profile:
            return
        try:
            self._store.update(profile["name"], result)
            self._store.save()
            self._rebuild_table()
            self.notify(f"Updated agent '{result['name']}'.")
        except Exception as exc:
            self.notify(str(exc), severity="error")

    def action_delete(self) -> None:
        profile = self._selected_profile()
        if not profile:
            self.notify("No agent selected.", severity="warning")
            return
        name = profile.get("name", "?")
        self.app.push_screen(
            ConfirmModal(f"Delete agent '{name}'?  This cannot be undone."),
            callback=lambda confirmed: self._do_delete(name, confirmed),
        )

    def _do_delete(self, name: str, confirmed: bool) -> None:
        if not confirmed:
            return
        try:
            self._store.delete(name)
            self._store.save()
            self._rebuild_table()
            self.notify(f"Deleted agent '{name}'.")
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        # Offer to also delete the memory DB if one exists
        db = self._log_dir / f"agent_memory_{name}.db"
        if db.exists():
            size_kb = db.stat().st_size / 1024
            self.app.push_screen(
                ConfirmModal(
                    f"Also delete memory DB for '{name}'?  ({size_kb:.0f} KB, cannot be undone)"
                ),
                callback=lambda ok: self._do_delete_db(name, db, ok),
            )

    def _do_delete_db(self, name: str, db: Path, confirmed: bool) -> None:
        if not confirmed:
            return
        try:
            db.unlink()
            self._rebuild_table()
            self.notify(f"Memory DB deleted for '{name}'.")
        except Exception as exc:
            self.notify(str(exc), severity="error")

    def action_stats(self) -> None:
        profile = self._selected_profile()
        if not profile:
            self.notify("No agent selected.", severity="warning")
            return
        name = profile.get("name", "?")
        db   = self._log_dir / f"agent_memory_{name}.db"
        if not db.exists():
            self.notify(f"No memory DB found for '{name}'.", severity="warning")
            return
        try:
            from ...memory.local_memory import LocalMemory
            mem = LocalMemory(agent_name=name, db_path=self._log_dir)
            mem.open()
            with mem:
                stats = mem.get_db_stats()
            mem.close()
            self.app.push_screen(StatsModal(name, stats))
        except Exception as exc:
            self.notify(str(exc), severity="error")

    def action_clear(self) -> None:
        profile = self._selected_profile()
        if not profile:
            self.notify("No agent selected.", severity="warning")
            return
        name = profile.get("name", "?")
        db   = self._log_dir / f"agent_memory_{name}.db"
        if not db.exists():
            self.notify(f"No memory DB for '{name}'.", severity="warning")
            return
        self.app.push_screen(
            ConfirmModal(f"Clear ALL memory for '{name}'?  Cannot be undone."),
            callback=lambda confirmed: self._do_clear(name, db, confirmed),
        )

    def _do_clear(self, name: str, db: Path, confirmed: bool) -> None:
        if not confirmed:
            return
        try:
            db.unlink()
            self._rebuild_table()
            self.notify(f"Memory cleared for '{name}'.")
        except Exception as exc:
            self.notify(str(exc), severity="error")

    def action_back(self) -> None:
        self.dismiss()
