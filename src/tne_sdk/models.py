"""
TNE-SDK: Shared Data Models

Lightweight dataclasses with zero SDK dependencies so launcher/ can import
them without creating circular imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TickSummary:
    """Emitted by Agent after every action tick for external consumers (TUI, logging)."""

    tick: int
    territory: str
    integrity: int
    max_integrity: int
    power: int
    max_power: int
    credits: float
    level: int
    faction: str
    in_combat: bool
    last_action: str
    action_parameters: dict
    reasoning: str
    elapsed_ms: float
    last_action_result: dict | None = field(default=None)
    context:           float        = 0.0
    memory_stats:      dict | None  = field(default=None)
    active_tasks:      list[dict]   = field(default_factory=list)
    active_directives: list[dict]   = field(default_factory=list)
    recent_events:     list[dict]   = field(default_factory=list)
    combat_state:      dict | None  = field(default=None)
    nearby_agents:     list[dict]   = field(default_factory=list)
    warnings:          list[str]    = field(default_factory=list)
    kills:             int          = 0
    deaths:            int          = 0
    npc_kills:         int          = 0
    equipped_weapon:   str | None   = None
    alliance_id:       str | None   = None
    total_wealth:      float        = 0.0
