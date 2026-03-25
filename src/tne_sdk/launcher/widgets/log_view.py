"""
TNE-SDK Launcher: Log View Widget

MMO-style scrolling combat/activity log with visually distinct entry types.

Each log record is classified into a category and rendered with unique
styling - icons, colors, borders, and spacing - so the feed reads like
a game chronicle rather than a diagnostic dump.

Categories:
  action      → Agent decisions (move, attack, gather, rest, etc.)
  result      → Server outcomes for previous actions
  reflection  → Episodic memory consolidation cycles
  tactical    → Goal/task review cycles
  system      → Connection, auth, server messages
  combat      → Combat-specific events
  warning     → Warnings
  error       → Errors
  debug       → Low-level detail (dim)
  payload     → LLM request/response (collapsible)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Static

# Custom log level below DEBUG for full payload inspection
VERBOSE = 5
logging.addLevelName(VERBOSE, "VERBOSE")

_MAX_ENTRIES = 400

# Marker prefix used by Agent._log_payload to tag payload records
PAYLOAD_MARKER = "@@PAYLOAD@@"

# ── Message classification ───────────────────────────────────────────────── #

# Patterns matched against the log message (case-insensitive)
_CATEGORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("reflection_start", re.compile(r"^=== REFLECTION CYCLE", re.I)),
    ("reflection_end",   re.compile(r"^=== REFLECTION COMPLETE", re.I)),
    ("tactical_start",   re.compile(r"^--- TACTICAL REVIEW \(", re.I)),
    ("tactical_end",     re.compile(r"^--- TACTICAL REVIEW COMPLETE", re.I)),
    ("reflection_skip",  re.compile(r"No new events since tick.*skipping reflection", re.I)),
    ("tactical_skip",    re.compile(r"No active tasks or new events.*skipping tactical", re.I)),
    ("reflection_info",  re.compile(r"^(Processing \d+ events|Compacted \d+ raw events)", re.I)),
    ("result_success",   re.compile(r"^Result \(tick \d+\):.*✓", re.I)),
    ("result_partial",   re.compile(r"^Result \(tick \d+\):.*~", re.I)),
    ("result_fail",      re.compile(r"^Result \(tick \d+\):.*✗", re.I)),
    # Chronicle events - matched before generic action/queued patterns
    ("chronicle_damage",    re.compile(r"^Chronicle \[DAMAGE\]", re.I)),
    ("chronicle_attack",    re.compile(r"^Chronicle \[ATTACK\]", re.I)),
    ("chronicle_flee",      re.compile(r"^Chronicle \[FLEE\b", re.I)),
    ("chronicle_flee_fail", re.compile(r"^Chronicle \[FLEE FAILED\]", re.I)),
    ("chronicle_pvp_kill",  re.compile(r"^Chronicle \[PVP KILL\]", re.I)),
    ("chronicle_ff_kill",   re.compile(r"^Chronicle \[FRIENDLY FIRE\]", re.I)),
    ("chronicle_npc_kill",  re.compile(r"^Chronicle \[NPC KILL\]", re.I)),
    ("chronicle_defeated",  re.compile(r"^Chronicle \[DEFEATED\]", re.I)),
    ("chronicle_respawn",   re.compile(r"^Chronicle \[RESPAWN\]", re.I)),
    ("chronicle_apex",      re.compile(r"^Chronicle \[APEX", re.I)),
    ("chronicle_quest",     re.compile(r"^Chronicle \[QUEST\]", re.I)),
    ("chronicle_level",     re.compile(r"^Chronicle \[LEVEL UP\]", re.I)),
    ("chronicle_skill",     re.compile(r"^Chronicle \[SKILL UP\]", re.I)),
    ("chronicle_deco",      re.compile(r"^Chronicle \[DECORATION\]", re.I)),
    ("chronicle_trade",     re.compile(r"^Chronicle \[TRADE\]", re.I)),
    ("chronicle_bounty",    re.compile(r"^Chronicle \[BOUNTY\]", re.I)),
    ("chronicle_move",      re.compile(r"^Chronicle \[MOVE\]", re.I)),
    ("chronicle_alliance",  re.compile(r"^Chronicle \[ALLIANCE\]", re.I)),
    ("chronicle_message",   re.compile(r"^Chronicle \[MESSAGE\]", re.I)),
    ("action",           re.compile(r"^Action: ", re.I)),
    ("queued",           re.compile(r"^Server confirmed:", re.I)),
    ("goal_reset",       re.compile(r"(Goal reset requested|Cleared \d+ stale goal)", re.I)),
    ("directive",        re.compile(r"^(Directive added|Hotloaded \d+ directive)", re.I)),
    ("auth",             re.compile(r"^Authenticated\b", re.I)),
    ("connecting",       re.compile(r"^Connecting to ", re.I)),
    ("waiting_shard",    re.compile(r"^Waiting for shard:", re.I)),
    ("agent_start",      re.compile(r"^Starting agent ", re.I)),
    ("agent_stop",       re.compile(r"^Agent .* stopped\.", re.I)),
]

# ── Category → visual config ────────────────────────────────────────────── #

_CATEGORY_CONFIG: dict[str, dict] = {
    # Actions - the agent's decisions
    "action": {
        "icon": "⚔",
        "css": "log-action",
        "label": "",
    },
    # Results
    "result_success": {
        "icon": "✦",
        "css": "log-result-ok",
        "label": "",
    },
    "result_partial": {
        "icon": "◐",
        "css": "log-result-partial",
        "label": "",
    },
    "result_fail": {
        "icon": "✘",
        "css": "log-result-fail",
        "label": "",
    },
    # ── Chronicle: combat events ─────────────────────────────────────────── #
    "chronicle_damage": {
        "icon": "💥",
        "css": "log-chronicle-damage",
        "label": "",
    },
    "chronicle_attack": {
        "icon": "⚔",
        "css": "log-chronicle-attack",
        "label": "",
    },
    "chronicle_flee": {
        "icon": "↩",
        "css": "log-chronicle-flee",
        "label": "",
    },
    "chronicle_flee_fail": {
        "icon": "✘",
        "css": "log-chronicle-damage",
        "label": "",
    },
    "chronicle_pvp_kill": {
        "icon": "☠",
        "css": "log-chronicle-pvp",
        "label": "",
    },
    "chronicle_ff_kill": {
        "icon": "⚠",
        "css": "log-chronicle-pvp",
        "label": "",
    },
    "chronicle_npc_kill": {
        "icon": "⚔",
        "css": "log-chronicle-npc-kill",
        "label": "",
    },
    "chronicle_defeated": {
        "icon": "💀",
        "css": "log-chronicle-death",
        "label": "",
    },
    "chronicle_respawn": {
        "icon": "◈",
        "css": "log-chronicle-respawn",
        "label": "",
    },
    # ── Chronicle: apex processes ────────────────────────────────────────── #
    "chronicle_apex": {
        "icon": "🔥",
        "css": "log-chronicle-apex",
        "label": "",
    },
    # ── Chronicle: progression ───────────────────────────────────────────── #
    "chronicle_quest": {
        "icon": "✦",
        "css": "log-chronicle-quest",
        "label": "",
    },
    "chronicle_level": {
        "icon": "⬆",
        "css": "log-chronicle-level",
        "label": "",
    },
    "chronicle_skill": {
        "icon": "↑",
        "css": "log-chronicle-level",
        "label": "",
    },
    "chronicle_deco": {
        "icon": "★",
        "css": "log-chronicle-level",
        "label": "",
    },
    # ── Chronicle: economy & social ──────────────────────────────────────── #
    "chronicle_trade": {
        "icon": "⬡",
        "css": "log-chronicle-trade",
        "label": "",
    },
    "chronicle_bounty": {
        "icon": "◎",
        "css": "log-chronicle-bounty",
        "label": "",
    },
    "chronicle_move": {
        "icon": "→",
        "css": "log-chronicle-move",
        "label": "",
    },
    "chronicle_alliance": {
        "icon": "⚑",
        "css": "log-chronicle-alliance",
        "label": "",
    },
    "chronicle_message": {
        "icon": "💬",
        "css": "log-chronicle-message",
        "label": "",
    },
    # Cognitive cycles
    "reflection_start": {
        "icon": "◈",
        "css": "log-reflection",
        "label": "",
    },
    "reflection_end": {
        "icon": "◈",
        "css": "log-reflection",
        "label": "",
    },
    "reflection_skip": {
        "icon": "◇",
        "css": "log-reflection-dim",
        "label": "",
    },
    "reflection_info": {
        "icon": "◇",
        "css": "log-reflection-dim",
        "label": "",
    },
    "tactical_start": {
        "icon": "⚑",
        "css": "log-tactical",
        "label": "",
    },
    "tactical_end": {
        "icon": "⚑",
        "css": "log-tactical",
        "label": "",
    },
    "tactical_skip": {
        "icon": "⚐",
        "css": "log-tactical-dim",
        "label": "",
    },
    # Server confirmations
    "queued": {
        "icon": "›",
        "css": "log-queued",
        "label": "",
    },
    # Goal / directive management
    "goal_reset": {
        "icon": "⟳",
        "css": "log-goal",
        "label": "",
    },
    "directive": {
        "icon": "★",
        "css": "log-directive",
        "label": "",
    },
    # System / connection
    "auth": {
        "icon": "◉",
        "css": "log-system",
        "label": "",
    },
    "connecting": {
        "icon": "◌",
        "css": "log-system-dim",
        "label": "",
    },
    "waiting_shard": {
        "icon": "◌",
        "css": "log-system-dim",
        "label": "",
    },
    "agent_start": {
        "icon": "▶",
        "css": "log-system",
        "label": "",
    },
    "agent_stop": {
        "icon": "■",
        "css": "log-system",
        "label": "",
    },
}


def _classify(msg: str, level: int) -> str:
    """Return a category key for the log message."""
    for cat, pattern in _CATEGORY_PATTERNS:
        if pattern.search(msg):
            return cat
    if level >= logging.ERROR:
        return "error"
    if level >= logging.WARNING:
        return "warning"
    if level <= logging.DEBUG:
        return "debug"
    return "info"


# ── Action-specific icons ────────────────────────────────────────────────── #

_ACTION_ICONS: dict[str, str] = {
    "move":           "→",
    "attack":         "⚔",
    "gather":         "⛏",
    "rest":           "☽",
    "wait":           "◦",
    "accept_quest":   "✦",
    "complete_quest": "★",
    "abandon_quest":  "✕",
    "buy":            "⬡",
    "sell":           "⬡",
    "equip":          "◈",
    "unequip":        "◇",
    "use_item":       "◆",
    "craft":          "⚒",
    "train":          "↑",
    "inspect":        "◎",
    "talk":           "💬",
    "flee":           "↩",
    "defend":         "◉",
    "scan":           "◎",
    "reset_goals":    "⟳",
}


def _icon_for_action(msg: str) -> str:
    """Pick an icon based on the action verb in an Action: line."""
    body = msg[len("Action: "):] if msg.startswith("Action: ") else msg
    verb = body.split(None, 1)[0].lower() if body else ""
    return _ACTION_ICONS.get(verb, "·")


# ── Formatting helpers ───────────────────────────────────────────────────── #

def _format_action(msg: str) -> str:
    """Parse 'Action: move territory=deep_loop | reasoning text' into rich parts."""
    # Strip the "Action: " prefix
    body = msg[len("Action: "):] if msg.startswith("Action: ") else msg
    # Split on the pipe separator between action+params and reasoning
    parts = body.split(" | ", 1)
    cmd = parts[0].strip()
    reasoning = parts[1].strip() if len(parts) > 1 else ""

    # Split command into action name and parameters
    tokens = cmd.split(None, 1)
    action_name = tokens[0] if tokens else cmd
    params = tokens[1] if len(tokens) > 1 else ""

    formatted = action_name.upper()
    if params:
        formatted += f"  {params}"
    if reasoning:
        formatted += f"\n    {reasoning}"
    return formatted


def _format_result(msg: str) -> str:
    """Parse 'Result (tick N): ✓ action → STATUS summary | details' into rich parts."""
    # Strip "Result (tick N): " prefix
    m = re.match(r"^Result \(tick (\d+)\):\s*[✓~✗]\s*(.+)$", msg)
    if not m:
        return msg
    tick = m.group(1)
    body = m.group(2)
    # body is like: "move → SUCCESS Moved to deep_loop ... | territory=deep_loop ..."
    arrow_parts = body.split("→", 1)
    action_name = arrow_parts[0].strip() if arrow_parts else "?"
    rest = arrow_parts[1].strip() if len(arrow_parts) > 1 else body

    # Split off key=value details after pipe
    pipe_parts = rest.split(" | ", 1)
    summary = pipe_parts[0].strip()
    details = pipe_parts[1].strip() if len(pipe_parts) > 1 else ""

    line = f"tick {tick}  {action_name} → {summary}"
    if details:
        line += f"\n    {details}"
    return line


# ── Chronicle event prefix → tag label ──────────────────────────────────── #

_CHRONICLE_PREFIX = re.compile(r"^Chronicle \[([^\]]+)\]:\s*")


def _format_chronicle(msg: str) -> str:
    """Strip the Chronicle prefix and return a clean, readable event line."""
    m = _CHRONICLE_PREFIX.match(msg)
    if not m:
        return msg
    tag = m.group(1)
    body = msg[m.end():].strip()

    # Extract territory tag from end if present: "... [territory_name]"
    territory = ""
    t_match = re.search(r"\s*\[([a-z_]+)\]\s*$", body)
    if t_match:
        territory = t_match.group(1).replace("_", " ")
        body = body[:t_match.start()].strip()

    line = f"{tag}  {body}"
    if territory:
        line += f"  ⌁ {territory}"
    return line


def _format_entry(category: str, ts: str, msg: str) -> str:
    """Build the final display string for a log entry."""
    cfg = _CATEGORY_CONFIG.get(category, {})
    icon = cfg.get("icon", "·")

    # Action lines get a verb-specific icon instead of the generic ⚔
    if category == "action":
        icon = _icon_for_action(msg)
        body = _format_action(msg)
    elif category.startswith("result_"):
        body = _format_result(msg)
    elif category.startswith("chronicle_"):
        body = _format_chronicle(msg)
    elif category in ("reflection_start", "reflection_end", "tactical_start", "tactical_end"):
        # These are section headers - keep them short and punchy
        body = msg.replace("===", "").replace("---", "").strip()
    elif category == "queued":
        # "Server confirmed: move queued for tick 12984" → compact
        body = msg.replace("Server confirmed: ", "").strip()
    else:
        body = msg

    # Escape Rich markup brackets so literal [ ] render safely in Static widgets
    body = body.replace("[", "\\[")

    return f" {icon}  {ts}  {body}"


class LogView(VerticalScroll):
    """MMO-style scrolling log with categorized, styled entries."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._count = 0
        self._records: list[dict] = []  # raw log data for export
        self._last_category: str = ""   # track for visual grouping
        self._last_msg: str = ""        # dedup consecutive identical messages
        self._last_ts: str = ""         # dedup window (same second)

    def write_record(self, record: logging.LogRecord) -> None:
        """Append a log record as a styled entry or collapsible payload."""
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        msg = record.getMessage()

        # Payload records get a collapsible block
        if msg.startswith(PAYLOAD_MARKER):
            raw = msg[len(PAYLOAD_MARKER):]
            parts = raw.split("\n", 1)
            self._records.append({
                "time": ts,
                "level": record.levelname,
                "type": "payload",
                "title": parts[0].strip() if parts else "payload",
                "body": parts[1] if len(parts) > 1 else "",
            })
            self._write_payload(ts, raw)
            return

        # Deduplicate consecutive identical messages within the same second
        if msg == self._last_msg and ts == self._last_ts:
            return
        self._last_msg = msg
        self._last_ts = ts

        # Classify the message
        category = _classify(msg, record.levelno)

        # Store raw record
        self._records.append({
            "time": ts,
            "level": record.levelname,
            "type": "log",
            "category": category,
            "message": msg,
        })

        # Build display text
        display = _format_entry(category, ts, msg)

        # Determine CSS class
        cfg = _CATEGORY_CONFIG.get(category, {})
        css_class = cfg.get("css", "")

        # Fallback styling for uncategorized
        if not css_class:
            if category == "error":
                css_class = "log-error"
            elif category == "warning":
                css_class = "log-warning"
            elif category == "debug":
                css_class = "log-debug"
            else:
                css_class = "log-info"

        # Add a spacer before major section changes for visual breathing room
        needs_spacer = category in (
            "action", "reflection_start", "tactical_start",
            "result_success", "result_partial", "result_fail",
            "chronicle_damage", "chronicle_defeated", "chronicle_pvp_kill",
            "chronicle_level", "chronicle_apex",
        ) and self._last_category not in (
            "reflection_start", "tactical_start",
        )

        if needs_spacer and self._count > 0:
            spacer = Static(" ", classes="log-spacer")
            self.mount(spacer)
            self._count += 1

        widget = Static(display, classes=f"log-entry {css_class}")
        self.mount(widget)
        self._count += 1
        self._last_category = category
        self._prune()
        widget.scroll_visible()

    def _write_payload(self, ts: str, raw: str) -> None:
        """Render a payload as a collapsed block with a header line."""
        parts = raw.split("\n", 1)
        title = parts[0].strip() if parts else "payload"
        body = parts[1] if len(parts) > 1 else "(empty)"

        header = f" ◆  {ts}  {title}  [dim](click to unfurl full payload)[/dim]"
        # Escape Rich markup brackets in payload body
        safe_body = body.replace("[", "\\[")
        widget = Collapsible(
            Static(safe_body, classes="payload-body"),
            title=header,
            collapsed=True,
        )
        self.mount(widget)
        self._count += 1
        self._prune()
        widget.scroll_visible()

    def _prune(self) -> None:
        """Remove oldest entries when the log exceeds the cap."""
        while self._count > _MAX_ENTRIES:
            children = list(self.children)
            if not children:
                break
            children[0].remove()
            self._count -= 1

    def export_text(self, include_payloads: bool = False) -> str:
        """Return all log records as plain text."""
        lines: list[str] = []
        for r in self._records:
            if r["type"] == "payload":
                if include_payloads:
                    lines.append(f"[{r['time']}] {r['title']}")
                    lines.append(r["body"])
            else:
                lines.append(f"[{r['time']}] [{r['level']}] {r['message']}")
        return "\n".join(lines)

    def export_json(self, include_payloads: bool = False) -> str:
        """Return all log records as a JSON string."""
        if include_payloads:
            data = self._records
        else:
            data = [r for r in self._records if r["type"] != "payload"]
        return json.dumps(data, indent=2, default=str)


class WidgetLogHandler(logging.Handler):
    """Routes Python log records to a LogView widget."""

    def __init__(self, log_view: LogView) -> None:
        super().__init__()
        self._log_view = log_view

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._log_view.write_record(record)
        except Exception:
            self.handleError(record)
