"""
TNE-SDK Launcher: Status Panel Widget

Left column of the RunAgent screen.  Reacts to TickSummary updates via
reactive variables for zero-flicker live refresh.  Includes a compact
chronicle feed showing recent world events — damage taken, kills, quests,
and more — so the TUI feels like watching the game live.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import ProgressBar, Static

from ...models import TickSummary


def _esc(text: str) -> str:
    """Escape Rich markup brackets so literal [ ] render safely in Static widgets."""
    return text.replace("[", "\\[")


class StatusPanel(Widget):
    """Displays agent stats derived from the most recent TickSummary."""

    tick:      reactive[int]   = reactive(0)
    territory: reactive[str]   = reactive("—")
    integrity: reactive[int]   = reactive(0)
    max_int:   reactive[int]   = reactive(1)
    power:     reactive[int]   = reactive(0)
    max_power: reactive[int]   = reactive(70)
    context:   reactive[float] = reactive(0.0)
    credits:   reactive[float] = reactive(0.0)
    level:     reactive[int]   = reactive(1)
    faction:   reactive[str]   = reactive("—")
    in_combat: reactive[bool]  = reactive(False)
    last_action: reactive[str] = reactive("—")
    reasoning:   reactive[str] = reactive("")
    elapsed_ms:  reactive[float] = reactive(0.0)
    tasks_active: reactive[int] = reactive(0)
    events_pending: reactive[int] = reactive(0)
    last_refl_tick: reactive[int] = reactive(0)
    total_wealth: reactive[float] = reactive(0.0)

    def compose(self) -> ComposeResult:
        _DIV = "─" * 44
        yield Static("STATUS", classes="section-title")
        yield Static(id="stat-tick")
        yield Static(_DIV, classes="divider")
        yield Static(id="stat-hp-label")
        yield ProgressBar(total=100, show_eta=False, id="hp-bar")
        yield Static(id="stat-pwr-label")
        yield ProgressBar(total=100, show_eta=False, id="pwr-bar")
        yield Static(id="stat-ctx-label")
        yield ProgressBar(total=100, show_eta=False, id="ctx-bar")
        yield Static(id="stat-credits")
        yield Static(id="stat-level")
        yield Static(id="stat-location")
        yield Static(id="stat-wealth")
        yield Static(id="stat-combat")
        yield Static(id="stat-combat-detail")
        yield Static(_DIV, classes="divider")
        yield Static("LAST ACTION", classes="section-title")
        yield Static(id="stat-action")
        yield Static(id="stat-reasoning")
        yield Static(id="stat-elapsed")
        yield Static(_DIV, classes="divider")
        yield Static("LAST RESULT", classes="section-title")
        yield Static(id="stat-result")
        yield Static(_DIV, classes="divider")
        yield Static("CHRONICLE", classes="section-title")
        yield Static(id="stat-chronicle")
        yield Static(_DIV, classes="divider")
        yield Static("MEMORY", classes="section-title")
        yield Static(id="stat-tasks")
        yield Static(id="stat-events")
        yield Static(id="stat-refl")
        yield Static(_DIV, classes="divider")
        yield Static(id="stat-goals")

    def update_from_summary(self, s: TickSummary) -> None:
        self.tick       = s.tick
        self.territory  = s.territory
        self.integrity  = s.integrity
        self.max_int    = max(1, s.max_integrity)
        self.power      = s.power
        self.max_power  = max(1, s.max_power)
        self.context    = s.context
        self.credits    = s.credits
        self.level      = s.level
        self.faction    = s.faction
        self.in_combat  = s.in_combat
        self.last_action = s.last_action
        self.reasoning   = s.reasoning
        self.elapsed_ms  = s.elapsed_ms
        self.total_wealth = s.total_wealth
        if s.memory_stats:
            self.tasks_active   = s.memory_stats.get("tasks_active", 0)
            self.events_pending = s.memory_stats.get("events", 0)
            self.last_refl_tick = s.memory_stats.get("last_reflection_tick", 0)
        self._refresh_widgets(s)

    def _refresh_widgets(self, s: TickSummary) -> None:
        hp_pct  = int(self.integrity  / self.max_int  * 100)
        pwr_pct = int(self.power      / self.max_power * 100)
        ctx_pct = int(self.context * 100)

        # Build action display with parameters
        params = s.action_parameters if hasattr(s, "action_parameters") and s.action_parameters else {}
        params_str = " ".join(f"{k}={v}" for k, v in params.items()) if params else ""
        action_display = f"{self.last_action} {params_str}".strip()

        self.query_one("#stat-tick",     Static).update(f"Tick  {self.tick:,}")
        self.query_one("#stat-hp-label", Static).update(f"INT   {self.integrity}/{self.max_int}")
        self.query_one("#stat-pwr-label",Static).update(f"PWR   {self.power}/{self.max_power}")
        self.query_one("#stat-ctx-label",Static).update(f"CTX   {ctx_pct}%")
        self.query_one("#stat-credits",  Static).update(f"CR    {self.credits:,.0f}cr")
        self.query_one("#stat-level",    Static).update(f"LV    {self.level}")
        self.query_one("#stat-location", Static).update(f"LOC   {self.territory}")
        self.query_one("#stat-wealth",   Static).update(f"WLTH  {self.total_wealth:,.0f}cr")

        # Combat status — show combatants when in combat
        combat_state = s.combat_state if hasattr(s, "combat_state") else None
        if self.in_combat and combat_state:
            self.query_one("#stat-combat", Static).update("⚔ IN COMBAT")
            combatants = combat_state.get("combatants", [])
            if combatants:
                lines = []
                for c in combatants:
                    name = c.get("name", "?")
                    int_pct = c.get("integrity_pct", 0)
                    if isinstance(int_pct, float) and int_pct <= 1.0:
                        int_pct = int(int_pct * 100)
                    bar = _mini_bar(int_pct)
                    lines.append(f"  {bar} {name} ({int_pct}%)")
                self.query_one("#stat-combat-detail", Static).update("\n".join(lines))
            else:
                self.query_one("#stat-combat-detail", Static).update("")
        elif self.in_combat:
            self.query_one("#stat-combat", Static).update("⚔ IN COMBAT")
            self.query_one("#stat-combat-detail", Static).update("")
        else:
            self.query_one("#stat-combat", Static).update("")
            self.query_one("#stat-combat-detail", Static).update("")

        self.query_one("#stat-action",   Static).update(_esc(action_display))
        self.query_one("#stat-reasoning", Static).update(_esc(self.reasoning))
        # Show LLM round-trip time so users can gauge inference speed
        if self.elapsed_ms > 0:
            self.query_one("#stat-elapsed", Static).update(f"⏱ {self.elapsed_ms / 1000:.1f}s")
        else:
            self.query_one("#stat-elapsed", Static).update("")
        self.query_one("#stat-tasks",    Static).update(f"Tasks {self.tasks_active} active")
        self.query_one("#stat-events",   Static).update(f"Evts  {self.events_pending} pend")
        self.query_one("#stat-refl",     Static).update(f"Refl  tick {self.last_refl_tick}")

        # Last action result
        result = s.last_action_result if hasattr(s, "last_action_result") else None
        if result:
            status = result.get("status", "")
            icon = {"success": "✓", "partial": "~"}.get(status, "✗")
            summary_text = result.get("summary", "")
            details = result.get("details")
            result_lines = [f"{icon} {_esc(result.get('action', '?'))} → {_esc(status.upper())}"]
            if summary_text:
                result_lines.append(f"  {_esc(summary_text)}")
            if details:
                result_lines.append("  " + "  ".join(f"{k}={_esc(str(v))}" for k, v in details.items()))
            self.query_one("#stat-result", Static).update("\n".join(result_lines))
        else:
            self.query_one("#stat-result", Static).update("—")

        # Chronicle — compact feed of recent world events
        self.query_one("#stat-chronicle", Static).update(
            _build_chronicle_display(s.recent_events, s.tick)
        )

        # Goals + directives panel
        goal_lines: list[str] = []
        if s.active_directives:
            goal_lines.append("⭐ DIRECTIVES")
            for d in s.active_directives:
                goal_lines.append(f"  {_esc(d['text'])}")
        if s.active_tasks:
            goal_lines.append("🎯 GOALS")
            for t in s.active_tasks:
                prefix = "  ↳" if t.get("parent_id") else "  "
                desc   = _esc(t["description"])
                goal_lines.append(f"{prefix}\\[{t['priority']:3d}] {desc}")
        self.query_one("#stat-goals", Static).update(
            "\n".join(goal_lines) if goal_lines else "🎯 GOALS\n  (none yet)"
        )

        # Update HP bar and color-class
        hp_bar: ProgressBar = self.query_one("#hp-bar", ProgressBar)
        hp_bar.update(progress=hp_pct)
        hp_bar.remove_class("low", "critical")
        if hp_pct < 25:
            hp_bar.add_class("critical")
        elif hp_pct < 50:
            hp_bar.add_class("low")

        # Update power bar
        pwr_bar: ProgressBar = self.query_one("#pwr-bar", ProgressBar)
        pwr_bar.update(progress=pwr_pct)
        pwr_bar.remove_class("low", "critical")
        if pwr_pct < 20:
            pwr_bar.add_class("critical")
        elif pwr_pct < 40:
            pwr_bar.add_class("low")

        # Update context bar (high = bad, inverted color logic)
        ctx_bar: ProgressBar = self.query_one("#ctx-bar", ProgressBar)
        ctx_bar.update(progress=ctx_pct)
        ctx_bar.remove_class("low", "critical")
        if ctx_pct >= 80:
            ctx_bar.add_class("critical")
        elif ctx_pct >= 50:
            ctx_bar.add_class("low")


# ── Helper: mini health bar for combatants ───────────────────────────────── #

def _mini_bar(pct: int, width: int = 10) -> str:
    """Render a compact bar like ████░░░░░░ for use in combat display."""
    filled = max(0, min(width, int(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


# ── Helper: chronicle event icons ────────────────────────────────────────── #

_CHRONICLE_ICONS: dict[str, str] = {
    "damage_taken":       "💥",
    "attack":             "⚔",
    "flee_success":       "↩",
    "flee_failed":        "✘",
    "pvp_kill":           "☠",
    "friendly_fire_kill": "⚠",
    "npc_defeated":       "⚔",
    "agent_defeated":     "💀",
    "respawn":            "◈",
    "apex_damage":        "🔥",
    "apex_defeated":      "🔥",
    "quest_complete":     "✦",
    "quest_accepted":     "✦",
    "level_up":           "⬆",
    "skill_up":           "↑",
    "decoration_awarded": "★",
    "trade_complete":     "⬡",
    "bounty_placed":      "◎",
    "bounty_claimed":     "◎",
    "move":               "→",
    "territory_entered":  "→",
    "alliance_formed":    "⚑",
    "alliance_broken":    "⚑",
    "message_received":   "💬",
}

# Event types that are interesting enough to show in the compact chronicle
_CHRONICLE_HIGHLIGHT_TYPES = frozenset({
    "damage_taken", "attack", "flee_success", "flee_failed",
    "pvp_kill", "friendly_fire_kill", "npc_defeated",
    "agent_defeated", "respawn",
    "apex_damage", "apex_defeated",
    "quest_complete", "level_up", "skill_up", "decoration_awarded",
    "bounty_placed", "bounty_claimed",
    "move", "territory_entered",
})


def _build_chronicle_display(recent_events: list[dict], current_tick: int) -> str:
    """
    Build a compact, reverse-chronological chronicle for the status panel.
    Shows the 8 most recent interesting events with icons and descriptions.
    """
    if not recent_events:
        return "  (no events yet)"

    # Filter to interesting events and take the most recent ones
    interesting = [
        e for e in recent_events
        if e.get("type") in _CHRONICLE_HIGHLIGHT_TYPES
    ]

    # Show the last 8 events, newest first
    display_events = interesting[-8:]
    display_events.reverse()

    if not display_events:
        return "  (no events yet)"

    lines: list[str] = []
    for event in display_events:
        event_type = event.get("type", "")
        icon = _CHRONICLE_ICONS.get(event_type, "·")
        desc = event.get("description", "")
        tick = event.get("tick", "")

        # Truncate long descriptions for the compact panel
        if len(desc) > 60:
            desc = desc[:57] + "..."

        # Show tick offset for context (how many ticks ago)
        if isinstance(tick, int) and isinstance(current_tick, int):
            ago = current_tick - tick
            if ago == 0:
                tick_str = "now"
            elif ago == 1:
                tick_str = "1t ago"
            else:
                tick_str = f"{ago}t ago"
        else:
            tick_str = ""

        line = f"  {icon} {desc}"
        if tick_str:
            line += f"  ({tick_str})"
        lines.append(line)

    return "\n".join(lines).replace("[", "\\[")
