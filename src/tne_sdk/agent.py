"""
TNE-SDK: Core Agent

Orchestrates the full cognitive loop:
  - WebSocket connection via TNEClient
  - Long-term memory via MemoryProvider
  - LLM decision-making via LLMProvider
  - Reflection and tactical review cycles
  - TickSummary callbacks for external consumers (TUI, scripts)
  - Hot-inject directives from a watched text file
  - Optional full request/response payload logging
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .client import TNEClient
from .config import AgentConfig
from .llm.providers import LLMProvider, Message
from .memory.base import MemoryProvider
from .models import TickSummary
from . import prompts

logger = logging.getLogger(__name__)


# ── JSON repair utility ──────────────────────────────────────────────────── #

def _repair_json(text: str) -> dict[str, Any]:
    """
    Parse JSON from an LLM response, with progressive repair fallbacks.

    It's messy but we try to handle <think> blocks, code fences, trailing commas, single quotes,
    prose before/after the JSON object, and truncated output with unclosed
    braces.

    Raises ``json.JSONDecodeError`` if all strategies fail.
    """
    if not text or not text.strip():
        raise json.JSONDecodeError("Empty response", text, 0)

    cleaned = text.strip()

    # 0. Strip <think>...</think> blocks (reasoning models like Qwen3/3.5).
    #    Some servers put thinking in the content field instead of
    #    reasoning_content, especially without --reasoning-format auto.
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    # Also handle unclosed <think> (model hit token limit mid-thought)
    if "<think>" in cleaned and "</think>" not in cleaned:
        think_start = cleaned.find("<think>")
        cleaned = cleaned[:think_start].strip()

    # 1. Strip code fences: ```json ... ``` or ``` ... ```
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # 2. Try direct parse first
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 3. Extract first { ... } block (skip prose before/after)
    brace_start = cleaned.find("{")
    if brace_start != -1:
        depth = 0
        brace_end = -1
        in_string = False
        escape = False
        for i in range(brace_start, len(cleaned)):
            c = cleaned[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    brace_end = i
                    break
        if brace_end != -1:
            candidate = cleaned[brace_start:brace_end + 1]
            try:
                result = json.loads(candidate)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

            # 4. Fix trailing commas before } or ]
            fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                result = json.loads(fixed)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

            # 5. Replace single quotes with double quotes (crude but effective)
            sq_fixed = candidate.replace("'", '"')
            try:
                result = json.loads(sq_fixed)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        # 6. Truncated JSON — try closing open braces
        if brace_end == -1 and depth > 0:
            truncated = cleaned[brace_start:]
            # Remove any trailing partial string/key
            truncated = re.sub(r',\s*"[^"]*$', "", truncated)
            truncated += "}" * depth
            try:
                result = json.loads(truncated)
                if isinstance(result, dict):
                    logger.warning("Repaired truncated JSON (closed %d braces).", depth)
                    return result
            except json.JSONDecodeError:
                pass

    raise json.JSONDecodeError(
        f"Could not parse or repair JSON from LLM response ({len(text)} chars)",
        text[:200], 0,
    )


class Agent:
    """
    The core TNE SDK Agent.

    Parameters
    ----------
    config              : AgentConfig
    client              : TNEClient
    memory              : MemoryProvider  (LocalMemory or NullMemory)
    llm_provider        : LLMProvider
    name                : str             agent name, used for directive file watch
    on_tick_summary     : optional callback(TickSummary) fired after every action tick
    log_payloads        : bool            write full request/response JSON to log_dir
    log_dir             : Path | str
    system_prompt       : override default action system prompt
    reflection_system_prompt / reflection_user_prompt : overrides
    tactical_system_prompt / tactical_user_prompt     : overrides
    """

    _MAX_RECENT_FAILURES = 10
    _MAX_ACTION_HISTORY  = 15

    def __init__(
        self,
        config:                   AgentConfig,
        client:                   TNEClient,
        memory:                   MemoryProvider,
        llm_provider:             LLMProvider,
        name:                     str = "agent",
        on_tick_summary:          Callable[[TickSummary], None] | None = None,
        log_payloads:             bool = False,
        log_dir:                  Path | str = "logs",
        system_prompt:            str | None = None,
        reflection_system_prompt: str | None = None,
        reflection_user_prompt:   str | None = None,
        tactical_system_prompt:   str | None = None,
        tactical_user_prompt:     str | None = None,
    ) -> None:
        self.config     = config
        self.client     = client
        self.memory     = memory
        self.llm        = llm_provider
        self.name       = name
        self._is_running = False

        self._on_tick_summary_cb = on_tick_summary

        # Payload logging
        _log = config.log_payloads or log_payloads
        _dir = config.log_dir if config.log_payloads else Path(log_dir)
        self._payload_log: logging.Logger | None = (
            self._setup_payload_log(name, _dir) if _log else None
        )

        # Prompts — explicit constructor args take priority, then config file
        # paths, then built-in defaults.  The /no_think hint is appended to
        # system prompts at call time when thinking is disabled, so the stored
        # prompts are always the clean base text.
        self.system_prompt = (
            system_prompt
            or config.load_prompt("system_prompt_file", prompts.SYSTEM_PROMPT)
        )
        self.reflection_system_prompt = (
            reflection_system_prompt
            or config.load_prompt("reflection_system_prompt_file", prompts.REFLECTION_SYSTEM)
        )
        self.reflection_user_prompt = (
            reflection_user_prompt
            or config.load_prompt("reflection_user_prompt_file", prompts.REFLECTION_USER)
        )
        self.tactical_system_prompt = (
            tactical_system_prompt
            or config.load_prompt("tactical_system_prompt_file", prompts.TACTICAL_REVIEW_SYSTEM)
        )
        self.tactical_user_prompt = (
            tactical_user_prompt
            or config.load_prompt("tactical_user_prompt_file", prompts.TACTICAL_REVIEW_USER)
        )

        # Hot-directive file path: directives_for_<name>.txt in cwd
        self._directive_file = Path(f"directives_for_{name}.txt")

        # Recent failure ring buffer — persists across ticks so the LLM can
        # see patterns like "craft entropy_blade failed 3 times in a row".
        self._recent_failures: list[dict] = []

        # Action history ring buffer — tracks the agent's own actions for
        # prompt rendering (🔁 YOUR RECENT ACTIONS) and repetition detection.
        self._action_history: list[dict] = []

        # When client-side validation blocks an action and substitutes a wait,
        # we store the blocked action here so the next tick's [LAST] line
        # reflects the real failure rather than "wait → success".
        self._pending_blocked_result: dict | None = None

    # ── Public API ────────────────────────────────────────────────────────── #

    async def run(self) -> None:
        """Start the agent's main loop.  Blocks until connection closes."""
        if self._is_running:
            raise RuntimeError("Agent is already running.")
        self._is_running = True
        logger.info("Starting agent '%s'...", self.name)
        if self.config.enable_thinking:
            logger.info(
                "Thinking mode ON — token budgets auto-scaled "
                "(action %d→%d, reflection %d→%d, tactical %d→%d).",
                self.config.max_tokens_action,     self._effective_max_tokens(self.config.max_tokens_action),
                self.config.max_tokens_reflection,  self._effective_max_tokens(self.config.max_tokens_reflection),
                self.config.max_tokens_tactical,    self._effective_max_tokens(self.config.max_tokens_tactical),
            )
        self.memory.open()
        try:
            await self.client.run(self._on_tick)
        finally:
            self.memory.close()
            self._is_running = False
            logger.info("Agent '%s' stopped.", self.name)

    def add_directive(self, text: str) -> None:
        """Inject a directive into memory immediately (thread-safe from TUI)."""
        with self.memory:
            self.memory.add_directive(text)
        logger.info("Directive added: %s", text)

    # ── Internal lifecycle ────────────────────────────────────────────────── #

    async def _on_tick(self, state: dict) -> dict[str, Any] | None:
        """Main callback: called once per game-state push by TNEClient."""
        if not state:
            logger.warning("Empty state received, waiting.")
            return {"action": "wait", "reasoning": "Null state received."}

        tick       = state.get("tick_info", {}).get("current_tick", 0)
        in_combat  = (state.get("combat_state") or {}).get("in_combat", False)

        # If the previous tick had a client-side blocked action (e.g. gather on
        # cooldown node), the server resolved a wait instead and reported
        # "wait → success".  Override last_action_result so the LLM sees the
        # real failure rather than a misleading success.  We also skip the
        # _recent_failures append below for this result since it was already
        # recorded when the block was detected.
        _skip_failure_append = False
        if self._pending_blocked_result:
            state = {**state, "last_action_result": self._pending_blocked_result}
            self._pending_blocked_result = None
            _skip_failure_append = True

        # Track recent failures so the LLM can see persistent patterns
        last_result = state.get("last_action_result")
        if not _skip_failure_append and last_result and last_result.get("status") not in ("success", None):
            details = last_result.get("details", {})
            detail_str = " ".join(f"{k}={v}" for k, v in details.items()) if details else ""
            self._recent_failures.append({
                "tick": tick,
                "action": last_result.get("action", "?"),
                "status": last_result.get("status", "?"),
                "summary": last_result.get("summary", "")[:150],
                "details": detail_str[:100],
            })
            # Keep only the most recent failures
            if len(self._recent_failures) > self._MAX_RECENT_FAILURES:
                self._recent_failures = self._recent_failures[-self._MAX_RECENT_FAILURES:]

        self._hotload_directives()

        with self.memory:
            last_refl     = self.memory.get_knowledge("last_reflection_tick")     or 0
            last_tactical = self.memory.get_knowledge("last_tactical_review_tick") or 0

        # Cognitive cycles run sequentially — most LLM endpoints serve one
        # request at a time, so parallel requests just cause timeouts.
        if tick > last_refl + self.config.reflection_cooldown_ticks and not in_combat:
            await self._run_reflection(tick)
            return {"action": "wait", "reasoning": "Consolidating episodic memory. Standing by."}

        if tick > last_tactical + self.config.tactical_review_cooldown_ticks and not in_combat:
            await self._run_tactical_review(state)
            return {"action": "wait", "reasoning": "Performing tactical review. Standing by."}

        # Standard action turn
        t0 = time.monotonic()
        with self.memory:
            self.memory.update(state)
            user_prompt = self._build_action_prompt(state)

        action = await self._get_llm_action(user_prompt, tick)
        elapsed_ms = (time.monotonic() - t0) * 1000

        # Handle meta-action: reset_goals
        if action.get("action") == "reset_goals":
            logger.info("Goal reset requested. Clearing stale goals and re-calling LLM.")
            with self.memory:
                num_reset = self.memory.reset_active_tasks()
                # Force a tactical review on the next tick so the agent
                # immediately gets fresh goals instead of wandering aimlessly.
                self.memory.set_knowledge("last_tactical_review_tick", 0)
            logger.info("Cleared %d stale goal(s); tactical review forced next tick.", num_reset)
            with self.memory:
                new_prompt = self._build_action_prompt(state)
            action = await self._get_llm_action(new_prompt, f"{tick}_rerun")
            if action.get("action") == "reset_goals":
                logger.warning("Double goal reset detected, forcing wait.")
                action = {"action": "wait", "reasoning": "Prevented goal reset loop."}

        # Validate the action against available_actions before sending
        original_action = action
        action = self._validate_action(action, state)

        # If validation downgraded the action, record it as a synthetic
        # failure so the LLM sees it in ⛔ RECENT FAILURES next tick.
        if original_action.get("action") != action["action"]:
            blocked_summary = action.get("reasoning", "Validation rejected this action")
            self._recent_failures.append({
                "tick": tick,
                "action": original_action.get("action", "?"),
                "status": "rejected",
                "summary": blocked_summary,
                "details": str(original_action.get("parameters", {}))[:100],
            })
            if len(self._recent_failures) > self._MAX_RECENT_FAILURES:
                self._recent_failures = self._recent_failures[-self._MAX_RECENT_FAILURES:]

            # Store a synthetic last_action_result so the next tick's [LAST]
            # line shows the real blocked action instead of "wait → success".
            self._pending_blocked_result = {
                "action": original_action.get("action", "?"),
                "status": "rejected",
                "summary": blocked_summary,
                "details": original_action.get("parameters", {}),
                "tick": tick,
            }

        # Record this action in the history ring buffer for prompt rendering
        # and repetition detection on subsequent ticks.
        self._action_history.append({
            "tick": tick,
            "action": action.get("action", "wait"),
            "parameters": action.get("parameters", {}),
        })
        if len(self._action_history) > self._MAX_ACTION_HISTORY:
            self._action_history = self._action_history[-self._MAX_ACTION_HISTORY:]

        # Emit tick summary for TUI / external consumers
        self._emit_summary(state, action, elapsed_ms)

        return action

    # ── Hotload directives ────────────────────────────────────────────────── #

    def _hotload_directives(self) -> None:
        """
        If `directives_for_<name>.txt` exists in the cwd, read each non-empty
        line as a directive, inject it into memory, then delete the file.
        """
        if not self._directive_file.exists():
            return
        try:
            lines = self._directive_file.read_text(encoding="utf-8").splitlines()
            injected = 0
            with self.memory:
                for line in lines:
                    line = line.strip()
                    if line:
                        self.memory.add_directive(line)
                        injected += 1
            self._directive_file.unlink(missing_ok=True)
            if injected:
                logger.info("Hotloaded %d directive(s) from %s", injected, self._directive_file)
        except Exception as exc:
            logger.warning("Failed to hotload directives: %s", exc)

    # ── Action validation ─────────────────────────────────────────────────── #

    def _validate_action(self, action: dict[str, Any], state: dict) -> dict[str, Any]:
        """
        Check the LLM's chosen action against the server's available_actions.

        Returns the action unchanged if valid, or a safe fallback if not.
        ``reset_goals`` is a client-side meta-action and always passes.
        """
        action_name = action.get("action")
        if not action_name:
            logger.warning("LLM returned action with no 'action' field, falling back to wait.")
            return {"action": "wait", "reasoning": "No action field in LLM response."}

        # reset_goals is handled by the agent, not the server
        if action_name == "reset_goals":
            return action

        available = state.get("available_actions", [])
        valid_names = {a["action"] for a in available}

        if action_name not in valid_names:
            logger.warning(
                "LLM chose unavailable action '%s' (valid: %s). Falling back to wait.",
                action_name, ", ".join(sorted(valid_names)),
            )
            return {"action": "wait", "reasoning": f"Chose invalid action '{action_name}'."}

        # Find the schema for this action and validate required parameters
        schema = next((a for a in available if a["action"] == action_name), None)

        # Block wasteful rest when context fatigue is low — resting is only
        # valuable above ~50% where debuffs start to matter.  Below that the
        # tick is better spent gathering, crafting, or moving.
        if action_name == "rest":
            context_fatigue = state.get("context_fatigue", 0.0)
            if context_fatigue < 0.50:
                logger.warning(
                    "Blocked low-value rest — context fatigue is only %d%% (threshold 50%%).",
                    int(context_fatigue * 100),
                )
                return {
                    "action": "wait",
                    "reasoning": f"Context fatigue is only {int(context_fatigue * 100)}% — not worth resting yet.",
                }

        # Block accept_quest for quests that are already active — the server
        # will reject these anyway, but catching it here saves a wasted tick.
        if action_name == "accept_quest":
            quest_id = action.get("parameters", {}).get("quest_id")
            active_quests = state.get("active_quests", [])
            active_ids = {q.get("quest_id") for q in active_quests}
            if quest_id and quest_id in active_ids:
                logger.warning(
                    "Blocked duplicate accept_quest for '%s' (already active).",
                    quest_id,
                )
                return {
                    "action": "wait",
                    "reasoning": f"Quest '{quest_id}' is already active — need to pick a different action.",
                }

        # Block gather on invalid or unavailable nodes — the server will reject
        # these anyway, but catching it here saves a wasted tick.
        if action_name == "gather":
            node_id = action.get("parameters", {}).get("node_id")
            if node_id:
                # Catch LLM hallucination: NPC IDs are not resource nodes.
                nearby_nodes = state.get("nearby_nodes", [])
                valid_node_ids = {n.get("node_id") for n in nearby_nodes}
                if node_id not in valid_node_ids:
                    logger.warning(
                        "Blocked gather on unknown node '%s' — not in nearby_nodes. "
                        "LLM may have confused an NPC ID with a resource node.",
                        node_id,
                    )
                    return {
                        "action": "wait",
                        "reasoning": f"Node '{node_id}' is not a valid resource node — need to pick a different action.",
                    }
                node = next((n for n in nearby_nodes if n.get("node_id") == node_id), None)
                if node and not node.get("can_gather"):
                    cd = node.get("cooldown_ticks", 0)
                    skill_ok = node.get("your_skill", 0) >= node.get("required_level", 999)
                    if cd > 0 and skill_ok:
                        reason = f"Node '{node_id}' is on cooldown ({cd} ticks remaining)"
                    elif node.get("is_depleted"):
                        reason = f"Node '{node_id}' is depleted"
                    else:
                        reason = f"Skill too low for node '{node_id}'"
                    logger.warning("Blocked gather on unavailable node '%s': %s", node_id, reason)
                    return {
                        "action": "wait",
                        "reasoning": f"{reason} — need to pick a different action.",
                    }

        # Block list_auction when the agent doesn't have the item in inventory.
        # The server catches this too, but blocking here saves a wasted tick.
        if action_name == "list_auction":
            params = action.get("parameters", {})
            item_id = params.get("item_id")
            quantity = int(params.get("quantity", 1))
            if item_id:
                inventory = state.get("inventory", {})
                held = inventory.get(item_id, 0)
                if held < quantity:
                    logger.warning(
                        "Blocked list_auction for %dx %s — only have %d in inventory.",
                        quantity, item_id, held,
                    )
                    return {
                        "action": "wait",
                        "reasoning": f"Cannot list {quantity}x {item_id} — only have {held} in inventory.",
                    }

        # Block bid_auction when all AH listings for the item are the agent's own.
        # The server skips own listings, so if all listings are ours the bid will fail.
        if action_name == "bid_auction":
            params = action.get("parameters", {})
            item_id = params.get("item_id")
            if item_id:
                my_listings = state.get("my_auction_listings", [])
                my_item_qty = sum(
                    l.get("quantity", 0) for l in my_listings
                    if l.get("item_id") == item_id
                )
                ah_shop = state.get("auction_house_shop", {})
                ah_entry = ah_shop.get(item_id, {})
                total_qty = ah_entry.get("qty", 0)
                # If all available quantity is from our own listings, bid will fail
                if total_qty > 0 and my_item_qty >= total_qty:
                    logger.warning(
                        "Blocked bid_auction for %s — all %d AH units are your own listings.",
                        item_id, total_qty,
                    )
                    return {
                        "action": "wait",
                        "reasoning": f"Cannot buy {item_id} — all {total_qty} units on AH are your own listings.",
                    }

        # Warn (but don't block) when LLM tries to craft something not in
        # craftable_now — let the server reject it so the failure gets
        # recorded and the agent learns from the mistake.
        if action_name == "craft" and schema:
            params = action.get("parameters", {})
            craftable_now = (
                schema.get("parameters", {})
                .get("item_id", {})
                .get("craftable_now", [])
            )
            chosen_item = params.get("item_id", "")

            if not chosen_item:
                logger.warning(
                    "LLM sent craft with no item_id (params: %s) — sending to server anyway.",
                    params,
                )
            elif not craftable_now:
                logger.warning(
                    "LLM trying to craft '%s' but nothing is craftable — sending to server anyway.",
                    chosen_item,
                )
            elif chosen_item not in craftable_now:
                logger.warning(
                    "LLM trying to craft '%s' but only %s are craftable — sending to server anyway.",
                    chosen_item, craftable_now,
                )

        if schema and schema.get("parameters"):
            params = action.get("parameters", {})
            for param_name, param_def in schema["parameters"].items():
                if param_name not in params:
                    continue
                # Check against valid_values if the server provides them
                valid_values = param_def.get("valid_values", [])
                if valid_values and params[param_name] not in valid_values:
                    logger.warning(
                        "Invalid value '%s' for %s.%s (valid: %s). Falling back to wait.",
                        params[param_name], action_name, param_name,
                        valid_values[:6],
                    )
                    return {
                        "action": "wait",
                        "reasoning": f"Invalid param '{params[param_name]}' for {action_name}.{param_name}.",
                    }

        return action

    # ── Emit summary ──────────────────────────────────────────────────────── #

    def _emit_summary(self, state: dict, action: dict, elapsed_ms: float) -> None:
        if self._on_tick_summary_cb is None:
            return
        mem_stats  = None
        tasks:      list[dict] = []
        directives: list[dict] = []
        try:
            with self.memory:
                mem_stats  = self.memory.get_db_stats()
                tasks      = self.memory.get_active_tasks(limit=5)
                directives = self.memory.get_active_directives(limit=3)
        except Exception:
            pass

        summary = TickSummary(
            tick          = state.get("tick_info", {}).get("current_tick", 0),
            territory     = state.get("current_territory", "?"),
            integrity     = state.get("integrity", 0),
            max_integrity = state.get("max_integrity", 1),
            power         = state.get("power", 0),
            max_power     = state.get("max_power", 70),
            credits       = float(state.get("credits", 0)),
            level         = state.get("level", 1),
            faction       = state.get("faction", "?"),
            in_combat     = (state.get("combat_state") or {}).get("in_combat", False),
            last_action   = action.get("action", "wait"),
            action_parameters = action.get("parameters", {}),
            reasoning     = action.get("reasoning", ""),
            elapsed_ms        = elapsed_ms,
            last_action_result = state.get("last_action_result"),
            context           = float(state.get("context_fatigue", 0.0)),
            memory_stats      = mem_stats,
            active_tasks      = tasks,
            active_directives = directives,
            recent_events     = state.get("recent_events", []),
            combat_state      = state.get("combat_state"),
            nearby_agents     = state.get("nearby_agents", []),
            warnings          = state.get("warnings", []),
            kills             = state.get("kills", 0),
            deaths            = state.get("deaths", 0),
            npc_kills         = state.get("npc_kills", 0),
            equipped_weapon   = state.get("equipped_weapon"),
            alliance_id       = state.get("alliance_id"),
            total_wealth      = float(state.get("total_wealth", 0)),
        )
        try:
            self._on_tick_summary_cb(summary)
        except Exception as exc:
            logger.debug("on_tick_summary callback raised: %s", exc)

    # ── LLM helpers ───────────────────────────────────────────────────────── #

    def _system_prompt_for(self, base_prompt: str) -> str:
        """
        Return the system prompt with /no_think appended when thinking is off.

        When thinking is enabled, the model needs freedom to reason in its
        <think> block — the /no_think tag would suppress that.  When thinking
        is disabled, the tag acts as a soft switch for Qwen3-family models
        and a general hint for others to keep responses terse.
        """
        if self.config.enable_thinking:
            return base_prompt
        return base_prompt + prompts.NO_THINK_HINT

    def _effective_max_tokens(self, base: int) -> int:
        """
        When thinking is enabled, the model's <think> block consumes tokens
        from the same max_tokens budget.  If the budget is too small the
        model exhausts it on reasoning and returns no answer at all.

        This doubles the budget when thinking is on, with a floor of 4096,
        so the model has room for both the reasoning chain and the JSON
        response.  Users who set explicit high values keep them as-is.
        """
        if not self.config.enable_thinking:
            return base
        return max(base * 2, 4096)

    async def _get_llm_action(self, user_prompt: str, tick: Any) -> dict[str, Any]:
        kwargs = self.config.default_llm_kwargs.copy()
        kwargs.setdefault("top_k", self.config.top_k)
        kwargs.setdefault("presence_penalty", self.config.presence_penalty)
        thinking_mode = self.config.enable_thinking
        sys_prompt = self._system_prompt_for(self.system_prompt)

        self._log_payload("REQUEST/action", {
            "tick": tick,
            "system": sys_prompt,
            "user": user_prompt,
        }, tick)

        for attempt in range(2):
            try:
                response_text = await asyncio.to_thread(
                    self.llm.chat_completion,
                    messages=[
                        Message(role="system", content=sys_prompt),
                        Message(role="user",   content=user_prompt),
                    ],
                    model         = self.config.model,
                    max_tokens    = self._effective_max_tokens(self.config.max_tokens_action),
                    temperature   = self.config.temperature,
                    top_p         = self.config.top_p,
                    thinking_mode = thinking_mode,
                    **kwargs,
                )

                self._log_payload("RESPONSE/action", {"response": response_text}, tick)

                action = _repair_json(response_text)
                return action

            except json.JSONDecodeError:
                if attempt == 0:
                    logger.warning("JSON parse failed on tick %s, retrying with correction prompt.", tick)
                    user_prompt = (
                        "Your previous response was not valid JSON. "
                        "Respond with ONLY a JSON object, nothing else.\n\n"
                        + user_prompt
                    )
                    continue
                logger.error("JSON parse failed twice on tick %s, giving up.", tick)
                return {"action": "wait", "reasoning": "LLM returned unparseable response."}

            except Exception as exc:
                logger.error("LLM action call failed on tick %s: %s", tick, exc, exc_info=True)
                return {"action": "wait", "reasoning": f"LLM call failed: {type(exc).__name__}"}

    # ── Reflection ────────────────────────────────────────────────────────── #

    async def _run_reflection(self, current_tick: int) -> None:
        logger.info("=== REFLECTION CYCLE (tick %d) ===", current_tick)

        with self.memory:
            last_tick = self.memory.get_knowledge("last_reflection_tick") or 0
            events    = self.memory.get_events_since(last_tick)

        if not events:
            logger.info("No new events since tick %d, skipping reflection.", last_tick)
            with self.memory:
                self.memory.set_knowledge("last_reflection_tick", current_tick)
            return

        logger.info("Processing %d events (ticks %d -> %d)...", len(events), last_tick, current_tick)

        with self.memory:
            knowledge_ctx = self.memory.get_knowledge_summary_text()
            hypotheses    = self.memory.get_knowledge_by_prefix("hypothesis:")

        hypotheses_ctx = "\n".join(f"- {k}: {v}" for k, v in hypotheses.items()) or "(none)"

        # Guard against context overflow
        events_json = json.dumps(events)
        if len(events_json) > self.config.reflection_max_chars:
            events_json = events_json[: self.config.reflection_max_chars]
            logger.warning(
                "Event data truncated to %d chars for reflection.", self.config.reflection_max_chars
            )

        user_prompt = self.reflection_user_prompt.format(
            knowledge_section  = knowledge_ctx or "(none yet, first reflection)",
            hypotheses_section = hypotheses_ctx,
            event_data         = events_json,
        )

        self._log_payload("REQUEST/reflection", {
            "tick": current_tick,
            "system": self.reflection_system_prompt,
            "user": user_prompt,
        }, current_tick)

        try:
            thinking = self.config.enable_thinking
            refl_sys = self._system_prompt_for(self.reflection_system_prompt)
            refl_kwargs = self.config.default_llm_kwargs.copy()
            refl_kwargs.setdefault("top_k", self.config.top_k)
            refl_kwargs.setdefault("presence_penalty",
                self.config.thinking_presence_penalty if thinking else self.config.presence_penalty)

            response_text = await asyncio.to_thread(
                self.llm.chat_completion,
                messages=[
                    Message(role="system", content=refl_sys),
                    Message(role="user",   content=user_prompt),
                ],
                model         = self.config.model,
                max_tokens    = self._effective_max_tokens(self.config.max_tokens_reflection),
                temperature   = self.config.thinking_temperature if thinking else self.config.temperature,
                top_p         = self.config.thinking_top_p if thinking else self.config.top_p,
                thinking_mode = thinking,
                **refl_kwargs,
            )

            self._log_payload("RESPONSE/reflection", {"response": response_text}, current_tick)

            if not response_text or not response_text.strip():
                logger.warning("Reflection returned empty response on tick %d, skipping.", current_tick)
                with self.memory:
                    self.memory.set_knowledge("last_reflection_tick", current_tick)
                return

            result = _repair_json(response_text)

            with self.memory:
                if s := result.get("narrative_summary"):
                    self.memory.set_knowledge("summary:narrative", s)
                if strats := result.get("combat_strategies"):
                    for strat in strats:
                        if "enemy_id" in strat and "strategy" in strat:
                            self.memory.set_knowledge(
                                f"strategy:combat:{strat['enemy_id']}", strat["strategy"]
                            )
                if econ := result.get("economic_notes"):
                    self.memory.set_knowledge("summary:economic", econ)
                if new_kn := result.get("new_knowledge"):
                    for item in new_kn:
                        if "key" in item and "value" in item:
                            self.memory.set_knowledge(item["key"], item["value"])
                if updates := result.get("task_updates"):
                    for u in updates:
                        if "task_id" in u and "status" in u:
                            self.memory.update_task_status(u["task_id"], u["status"])
                if new_tasks := result.get("new_tasks"):
                    self.memory.add_tasks_hierarchical(new_tasks)

                pruned = self.memory.prune_reflected_events(current_tick)
                logger.info("Compacted %d raw events from DB.", pruned)
                self.memory.set_knowledge("last_reflection_tick", current_tick)

            # Vacuum after reflection to reclaim space from pruned events.
            # Must run outside the transaction context.
            try:
                self.memory.vacuum()
            except Exception:
                pass  # non-critical

            logger.info("=== REFLECTION COMPLETE ===")

        except Exception as exc:
            logger.error("Reflection failed on tick %d: %s", current_tick, exc, exc_info=True)
            # Advance the tick marker even on failure so the agent doesn't
            # get stuck retrying the same reflection every tick.
            with self.memory:
                self.memory.set_knowledge("last_reflection_tick", current_tick)

    async def _run_tactical_review(self, state: dict) -> None:
        current_tick = state.get("tick_info", {}).get("current_tick", 0)
        logger.info("--- TACTICAL REVIEW (tick %d) ---", current_tick)

        with self.memory:
            last_tactical_tick = self.memory.get_knowledge("last_tactical_review_tick") or 0
            events             = self.memory.get_events_since(last_tactical_tick, limit=15)
            tasks              = self.memory.get_active_tasks(limit=20)
            narrative          = self.memory.get_knowledge("summary:narrative") or ""
            economic           = self.memory.get_knowledge("summary:economic") or ""
            combat_strats      = self.memory.get_knowledge_by_prefix("strategy:combat:")

        if not tasks and not events:
            logger.info("No active tasks or new events; skipping tactical review.")
            with self.memory:
                self.memory.set_knowledge("last_tactical_review_tick", current_tick)
            return

        inventory   = state.get("inventory", {})
        equip_action = next(
            (a for a in state.get("available_actions", []) if a.get("action") == "equip"), None
        )
        equippable = (
            equip_action.get("parameters", {}).get("item_id", {}).get("equippable_items", [])
            if equip_action else []
        )

        # Crafting context for tactical review
        c_skills = state.get("crafting_skills", {})
        g_skills = state.get("gathering_skills", {})
        known_recipes = state.get("known_recipes", [])
        craft_action = next(
            (a for a in state.get("available_actions", []) if a.get("action") == "craft"), None
        )
        craftable_now = []
        if craft_action:
            craftable_now = (
                craft_action.get("parameters", {})
                .get("item_id", {})
                .get("craftable_now", [])
            )

        recipe_lines: list[str] = []
        for r in known_recipes:
            skill_ok = r.get("your_skill", 0) >= r.get("required_skill", 999)
            item_name = r.get('result', {}).get('item', '?')
            out_qty = r.get('result', {}).get('qty', 1)
            qty_str = f"x{out_qty}" if out_qty > 1 else ""
            if r.get("craftable_now"):
                recipe_lines.append(
                    f"  ⚡ {item_name}{qty_str} [CRAFTABLE]"
                )
            elif not skill_ok:
                recipe_lines.append(
                    f"  ✗ {item_name}{qty_str} "
                    f"[need {r['track']} Lv{r['required_skill']}, have Lv{r.get('your_skill', 1)}]"
                )
            else:
                missing = []
                for i in r.get("ingredients", []):
                    have, need = i.get("have", 0), i["qty"]
                    if have < need:
                        missing.append(f"{need - have}x {i['item']}")
                recipe_lines.append(
                    f"  ✗ {item_name}{qty_str} "
                    f"[missing: {', '.join(missing)}]"
                )

        # Quest context
        active_quests = state.get("active_quests", [])
        avail_quests = state.get("available_quests", [])
        quest_lines: list[str] = []
        if active_quests:
            quest_lines.append("Active Quests:")
            for q in active_quests[:5]:
                quest_lines.append(
                    f"  quest_id={q.get('quest_id', '?')} | {q.get('title', '?')} "
                    f"({q.get('quest_type')}, {q.get('reward_credits', 0):.0f}cr)"
                )
        if avail_quests:
            quest_lines.append(f"Available Quests: {len(avail_quests)} (use accept_quest to start)")

        # Build memory context so tactical decisions are informed by reflection output
        memory_lines: list[str] = []
        if narrative:
            memory_lines.append(f"Narrative: {str(narrative)[:300]}")
        if economic:
            memory_lines.append(f"Economic: {str(economic)[:200]}")
        if combat_strats:
            for key, strat in list(combat_strats.items())[:5]:
                enemy = key.split(":")[-1] if ":" in key else key
                memory_lines.append(f"vs {enemy}: {str(strat)[:120]}")
        memory_section = "\n".join(memory_lines) if memory_lines else "(no reflection data yet)"

        status_section = "\n".join([
            f"Integrity: {state.get('integrity')}/{state.get('max_integrity')}",
            f"Location: {state.get('current_territory')}",
            f"Power: {state.get('power', 0)}/{state.get('max_power', 70)}",
            f"Level: {state.get('level', 1)}  Combat Skill: {state.get('combat_skill', 1)}",
            f"Context: {int(state.get('context_fatigue', 0) * 100)}%",
            f"Combat: {'Yes' if (state.get('combat_state') or {}).get('in_combat') else 'No'}",
            f"Inventory: {', '.join(f'{k}x{v}' for k, v in inventory.items() if v > 0) or 'empty'}",
            f"Equippable Items: {json.dumps(equippable)}",
            f"Gathering Skills: {' '.join(f'{k}={v}' for k, v in g_skills.items())}",
            f"Crafting Skills: {' '.join(f'{k}={v}' for k, v in c_skills.items())}",
            f"Known Recipes:\n" + ("\n".join(recipe_lines) if recipe_lines else "  (none)"),
            f"Craftable Right Now: {', '.join(craftable_now) if craftable_now else 'NOTHING'}",
            ("\n".join(quest_lines) if quest_lines else ""),
        ])

        # Add travel costs so tactical review can reason about reachability
        travel_costs = state.get("travel_costs", {})
        if travel_costs:
            current_power = state.get("power", 0)
            reachable = {t: c for t, c in travel_costs.items() if c <= current_power}
            unreachable = {t: c for t, c in travel_costs.items() if c > current_power}
            tc_lines = [f"Reachable Territories ({len(reachable)}/{len(travel_costs)}):"]
            for t in sorted(reachable, key=lambda x: reachable[x]):
                tc_lines.append(f"  {t} ({reachable[t]} power)")
            if unreachable:
                tc_lines.append(f"Unreachable ({len(unreachable)} — not enough power):")
                for t in sorted(unreachable, key=lambda x: unreachable[x]):
                    tc_lines.append(f"  {t} ({unreachable[t]} power)")
            status_section += "\n" + "\n".join(tc_lines)

        # Append recent failures so tactical review can see persistent patterns
        if self._recent_failures:
            failure_lines = ["Recent Failures (do NOT recreate goals for these):"]
            for fail in self._recent_failures[-5:]:
                detail = f" ({fail['details']})" if fail.get('details') else ""
                failure_lines.append(f"  tick {fail['tick']}: {fail['action']} → {fail['status']} — {fail['summary']}{detail}")
            status_section += "\n" + "\n".join(failure_lines)

        status_section += f"\n\nMemory Context:\n{memory_section}"
        tasks_section  = "\n".join(
            f"- [id={t['task_id']}, pri={t['priority']}] {t['description']}" for t in tasks
        ) or "(none)"
        events_section = json.dumps(events, indent=2)

        user_prompt = self.tactical_user_prompt.format(
            status_section = status_section,
            tasks_section  = tasks_section,
            events_section = events_section,
        )

        self._log_payload("REQUEST/tactical", {
            "tick": current_tick,
            "system": self.tactical_system_prompt,
            "user": user_prompt,
        }, current_tick)

        try:
            thinking = self.config.enable_thinking
            tact_sys = self._system_prompt_for(self.tactical_system_prompt)
            tact_kwargs = self.config.default_llm_kwargs.copy()
            tact_kwargs.setdefault("top_k", self.config.top_k)
            tact_kwargs.setdefault("presence_penalty",
                self.config.thinking_presence_penalty if thinking else self.config.presence_penalty)

            response_text = await asyncio.to_thread(
                self.llm.chat_completion,
                messages=[
                    Message(role="system", content=tact_sys),
                    Message(role="user",   content=user_prompt),
                ],
                model         = self.config.model,
                max_tokens    = self._effective_max_tokens(self.config.max_tokens_tactical),
                temperature   = self.config.thinking_temperature if thinking else self.config.temperature,
                top_p         = self.config.thinking_top_p if thinking else self.config.top_p,
                thinking_mode = thinking,
                **tact_kwargs,
            )

            self._log_payload("RESPONSE/tactical", {"response": response_text}, current_tick)

            if not response_text or not response_text.strip():
                logger.warning("Tactical review returned empty response on tick %d, skipping.", current_tick)
                with self.memory:
                    self.memory.set_knowledge("last_tactical_review_tick", current_tick)
                return

            result = _repair_json(response_text)

            with self.memory:
                if updates := result.get("task_updates"):
                    for u in updates:
                        if "task_id" in u and u.get("status"):
                            self.memory.update_task_status(u["task_id"], u["status"])
                if new_tasks := result.get("new_tasks"):
                    self.memory.add_tasks_hierarchical(new_tasks)
                self.memory.set_knowledge("last_tactical_review_tick", current_tick)

            logger.info("--- TACTICAL REVIEW COMPLETE ---")

        except Exception as exc:
            logger.error("Tactical review failed on tick %d: %s", current_tick, exc, exc_info=True)
            with self.memory:
                self.memory.set_knowledge("last_tactical_review_tick", current_tick)

    # ── Action prompt builder ─────────────────────────────────────────────── #

    def _build_action_prompt(self, state: dict) -> str:
        memory = self.memory

        tick          = state.get("tick_info", {}).get("current_tick", "?")
        territory     = state.get("current_territory", "?")
        integrity     = state.get("integrity", 0)
        max_int       = state.get("max_integrity", 1)
        credits       = state.get("credits", 0)
        level         = state.get("level", 1)
        faction       = state.get("faction", "?")
        warnings      = state.get("warnings", [])
        combat        = state.get("combat_state") or {}
        inventory     = state.get("inventory", {})
        nearby_npcs   = state.get("nearby_npcs", [])
        nearby_agents = state.get("nearby_agents", [])
        recent_events = state.get("recent_events", [])[-5:]
        avail_actions = state.get("available_actions", [])
        world_ctx     = state.get("world_context", "")
        local_market  = state.get("local_market", {})
        shop_inv      = state.get("shop_inventory", {})
        last_result   = state.get("last_action_result")

        # Loadout
        weapon_str = state.get("equipped_weapon") or "NONE (UNARMED ⚠)"
        if state.get("equipped_weapon") and state.get("weapon_durability") is not None:
            max_ch = state.get("weapon_max_charges")
            ch_str = (
                f"{state['weapon_durability']}/{max_ch}"
                if max_ch else f"{state['weapon_durability']}"
            )
            weapon_str += f" [{ch_str} charges]"
        armor_dict = state.get("equipped_armor", {})
        armor_body = armor_dict.get("armor")   or "empty"
        armor_util = armor_dict.get("utility") or "empty"
        aug_list   = [s for s in state.get("augment_slots", []) if s]
        aug_str    = ", ".join(aug_list) if aug_list else "none"

        # Skills
        g_skills      = state.get("gathering_skills", {})
        c_skills      = state.get("crafting_skills", {})
        combat_skill  = state.get("combat_skill", 1)
        context       = state.get("context_fatigue", 0.0)
        agent_class   = state.get("agent_class", "?")
        banked_xp     = state.get("banked_xp_total", 0)

        g_str = " ".join(f"{k}={v}" for k, v in g_skills.items())
        c_str = " ".join(f"{k}={v}" for k, v in c_skills.items())

        bank_credits = state.get("bank_credits", 0)
        total_wealth = state.get("total_wealth", credits + bank_credits)

        lines = [
            f"=== TICK {tick} ===",
            f"Location : {territory}  Class: {agent_class}",
            f"Integrity: {integrity}/{max_int} ({int(integrity / max(1, max_int) * 100)}%)",
            f"Power    : {state.get('power', 0)}/{state.get('max_power', 70)}  "
            f"Credits  : {credits:.0f}cr  Level: {level}  Faction: {faction}",
            f"Context  : {int(context * 100)}%"
            + (" ✓ clear — no need to rest" if context < 0.25 else
               " ⚠ HIGH — rest soon!" if context >= 0.75 else
               " — manageable, keep working" if context < 0.50 else "")
            + (f"  Banked XP: {banked_xp}" if banked_xp else ""),
            f"Weapon   : {weapon_str}",
            f"Armor    : armor={armor_body}  utility={armor_util}",
            f"Augments : {aug_str}",
            f"Combat   : Lv{combat_skill}",
            f"Gathering: {g_str}",
            f"Crafting : {c_str}",
        ]

        # Meta directive — persistent high-level goal from config
        if self.config.meta_directive:
            lines.append(f"\n🏆 META GOAL: {self.config.meta_directive}")

        # Memory context
        with memory:
            directives = memory.get_active_directives()
            if directives:
                lines.append("\n⭐ DIRECTIVES (must follow):")
                for d in directives:
                    lines.append(f"  - {d['text']}")

            tasks = memory.get_active_tasks()
            if tasks:
                lines.append("\n🎯 ACTIVE GOALS (priority-ordered):")
                tasks_by_id = {t["task_id"]: t for t in tasks}
                tree: dict[int | None, list] = {None: []}
                for t in tasks:
                    pid = t.get("parent_id")
                    tree.setdefault(pid, []).append(t)

                displayed: set[int] = set()
                for t in tasks:
                    if t["task_id"] in displayed:
                        continue
                    pid = t.get("parent_id")
                    if pid and pid not in tasks_by_id:
                        parent = memory.get_task(pid)
                        if parent:
                            lines.append(f"  Parent: {parent['description']} [pri={parent['priority']}]")
                    prefix = "    ↳" if pid else "  "
                    lines.append(f"{prefix} [{t['priority']:3d}] {t['description'][:120]}  ({t['status']})")
                    displayed.add(t["task_id"])
                    for child in tree.get(t["task_id"], []):
                        if child["task_id"] not in displayed:
                            lines.append(f"    ↳ [{child['priority']:3d}] {child['description'][:120]}  ({child['status']})")
                            displayed.add(child["task_id"])

            # ── Incomplete quest objectives (high-visibility) ──────────────
            # Placed right after ACTIVE GOALS so the AI can't miss them.
            active_quests = state.get("active_quests", [])
            incomplete_objectives: list[str] = []
            for q in active_quests:
                qtype = q.get("quest_type", "")
                for o in q.get("objectives", []):
                    if o.get("completed"):
                        continue
                    cur = o.get("current", 0)
                    req = o.get("required", 1)
                    desc = o.get("description", "?")
                    title = q.get("title", "?")
                    # Include the exact item_id for list/acquire objectives
                    # so the AI knows precisely which item to use
                    target_hint = ""
                    if o.get("type") in ("list", "acquire") and o.get("target"):
                        target_hint = f" [exact item_id: {o['target']}]"
                    # Flag craft/market commission objectives the agent can't craft
                    infeasible_tag = ""
                    if qtype in ("market_commission", "craft_commission") and o.get("type") in ("list", "acquire"):
                        target_item = o.get("target", "")
                        known_recipes = state.get("known_recipes", [])
                        can_craft = any(
                            r.get("result", {}).get("item") == target_item
                            for r in known_recipes
                        )
                        if not can_craft and target_item:
                            infeasible_tag = " ⛔ CANNOT CRAFT (skill too low or no recipe) — consider abandoning"
                    incomplete_objectives.append(
                        f"  ⚠ [{cur}/{req}] {desc}{target_hint}  (quest: {title}){infeasible_tag}"
                    )
            if incomplete_objectives:
                lines.append("\n📋 QUEST OBJECTIVES (complete these!):")
                lines.extend(incomplete_objectives)

            hypotheses = memory.get_knowledge_by_prefix("hypothesis:")
            if hypotheses:
                lines.append("\n🤔 OPEN QUESTIONS & HYPOTHESES:")
                for v in hypotheses.values():
                    lines.append(f"  - {v}")

            narrative = memory.get_knowledge("summary:narrative")
            if narrative:
                lines.append(f"\n📖 NARRATIVE: {str(narrative)[:280]}")

            in_combat  = combat.get("in_combat", False)
            enemy_ids  = [n.get("npc_id") for n in nearby_npcs[:5] if n.get("npc_id")]
            if in_combat:
                enemy_ids += [c.get("name") for c in combat.get("combatants", []) if c.get("name")]
            for eid in enemy_ids:
                strat = memory.get_knowledge(f"strategy:combat:{eid}")
                if strat:
                    lines.append(f"  ⚔ vs {eid}: {str(strat)[:120]}")

            if shop_inv or local_market:
                econ = memory.get_knowledge("summary:economic")
                if econ:
                    lines.append(f"\n💰 ECONOMIC: {str(econ)[:220]}")

        # Faction context & coordination
        faction_ctx = state.get("faction_context", {})
        if faction_ctx:
            posture = faction_ctx.get("posture", "")
            cautious = faction_ctx.get("cautious", [])
            if posture:
                lines.append(f"\n🏴 FACTION POSTURE: {posture}")
            if cautious:
                lines.append(f"  Cautious toward: {', '.join(cautious)}")
        faction_goal = state.get("faction_goal")
        if faction_goal:
            lines.append(f"  📡 {faction_goal}")

        # Faction reputation
        faction_rep = state.get("faction_reputation", {})
        if faction_rep:
            rep_str = " ".join(f"{k}={v}" for k, v in faction_rep.items())
            lines.append(f"  Reputation: {rep_str}")

        # Known recipes & crafting — show item names only, no recipe IDs
        known_recipes = state.get("known_recipes", [])
        craft_action = next(
            (a for a in avail_actions if a.get("action") == "craft"), None
        )
        craftable_now = []
        if craft_action:
            craftable_now = (
                craft_action.get("parameters", {})
                .get("item_id", {})
                .get("craftable_now", [])
            )
        if known_recipes:
            ready    = [r for r in known_recipes if r.get("craftable_now")]
            notready = [r for r in known_recipes if not r.get("craftable_now")]

            if ready:
                lines.append("\n🔧 CRAFTABLE NOW:")
                for r in ready:
                    item = r.get('result', {}).get('item', '?')
                    qty = r.get('result', {}).get('qty', 1)
                    q = f"×{qty}" if qty > 1 else ""
                    ingr = ", ".join(f"{i['qty']}x {i['item']}" for i in r.get("ingredients", []))
                    lines.append(f"  ⚡ {item}{q} — needs: {ingr}")

            if notready:
                lines.append("\n🔧 RECIPES (not ready):")
                for r in notready:
                    item = r.get('result', {}).get('item', '?')
                    qty = r.get('result', {}).get('qty', 1)
                    q = f"×{qty}" if qty > 1 else ""
                    skill_ok = r.get("your_skill", 0) >= r.get("required_skill", 999)
                    if not skill_ok:
                        lines.append(
                            f"  ✗ {item}{q} — need {r['track']} Lv{r['required_skill']}"
                            f" (have Lv{r.get('your_skill', 1)})"
                        )
                        continue
                    missing = []
                    for i in r.get("ingredients", []):
                        have, need = i.get("have", 0), i["qty"]
                        if have < need:
                            src = i.get("source", "")
                            src_hint = f" ({src})" if src else ""
                            missing.append(f"{need - have}x {i['item']}{src_hint}")
                    lines.append(f"  ✗ {item}{q} — missing: {', '.join(missing)}")

            if not ready:
                lines.append("  ⚡ Nothing craftable right now.")
        else:
            lines.append("\n🔧 No recipes known yet. Explore or buy training_dataset to learn recipes.")

        # Active quests
        active_quests = state.get("active_quests", [])
        if active_quests:
            lines.append("\n📋 ACTIVE QUESTS:")
            for q in active_quests[:5]:
                obj_str = ""
                for o in q.get("objectives", []):
                    target_hint = ""
                    if o.get("type") in ("list", "acquire") and o.get("target"):
                        target_hint = f" (item_id: {o['target']})"
                    obj_str += f" [{o.get('current', 0)}/{o.get('required', 1)} {o.get('description', '')}{target_hint}]"
                lines.append(
                    f"  quest_id={q.get('quest_id', '?')} | {q.get('title', '?')} "
                    f"(reward: {q.get('reward_credits', 0):.0f}cr, {q.get('reward_xp', 0)}xp)"
                    f"{obj_str}"
                )

        # Available quests (brief) — filter out quests already accepted so
        # the LLM doesn't try to re-accept them.
        active_quest_ids = {q.get("quest_id") for q in active_quests}
        avail_quests = [
            q for q in state.get("available_quests", [])
            if q.get("quest_id") not in active_quest_ids
        ]
        if avail_quests:
            lines.append(f"\n📋 AVAILABLE QUESTS ({len(avail_quests)}):")
            for q in avail_quests[:5]:
                lines.append(
                    f"  quest_id={q.get('quest_id')} | {q.get('title', '?')} "
                    f"({q.get('quest_type')}, {q.get('reward_credits', 0):.0f}cr, "
                    f"expires tick {q.get('expires_at_tick', '?')})"
                )
            if len(avail_quests) > 5:
                lines.append(f"  ... and {len(avail_quests) - 5} more")

        # Active bounties
        bounties = state.get("active_bounties", [])
        if bounties:
            lines.append(f"\n💀 ACTIVE BOUNTIES ({len(bounties)}):")
            for b in bounties[:4]:
                inactive_tag = " [OFFLINE]" if b.get("target_inactive") else ""
                lines.append(
                    f"  {b.get('target_name', '?')} ({b.get('target_faction', '?')}) "
                    f"@ {b.get('last_known_territory', '?')} — {b.get('reward_credits', 0):.0f}cr{inactive_tag}"
                )

        # Apex processes (world bosses)
        apex = state.get("active_apex_processes", [])
        if apex:
            lines.append("\n👹 APEX PROCESSES (world bosses):")
            for a in apex:
                hp_pct = int(a.get("integrity", 0) / max(1, a.get("max_integrity", 1)) * 100)
                lines.append(
                    f"  {a['name']} Lv{a['level']} ({hp_pct}% HP) @ {a['territory']}"
                )

        # Bank & storage
        bank_cr = state.get("bank_credits", 0)
        base_storage = state.get("base_storage", {})
        if bank_cr or base_storage:
            storage_str = ", ".join(f"{k}×{v}" for k, v in base_storage.items()) if base_storage else "empty"
            lines.append(f"\n🏦 BANK: {bank_cr:.0f}cr  Storage: {storage_str}")
        lines.append(f"\n💰 TOTAL WEALTH: {total_wealth:,.0f}cr (credits + bank + inventory value)")

        # Pending trades & messages
        pending_trades = state.get("pending_trade_offers", [])
        if pending_trades:
            lines.append(f"\n📨 PENDING TRADE OFFERS: {len(pending_trades)}")
            for t in pending_trades[:3]:
                lines.append(f"  From {t.get('from_name', t.get('from_id', '?'))}: {t.get('description', str(t)[:80])}")

        messages = state.get("message_history", [])
        if messages:
            lines.append(f"\n💬 MESSAGES ({len(messages)}):")
            for m in messages[-3:]:
                lines.append(f"  {m.get('from_name', '?')}: {str(m.get('content', ''))[:80]}")

        # Auction house
        ah_shop = state.get("auction_house_shop", {})
        if ah_shop:
            affordable_ah = {
                item: info for item, info in ah_shop.items()
                if info.get("can_afford")
            }
            if affordable_ah:
                lines.append(f"\n🏛 AUCTION HOUSE (affordable):")
                for item, info in list(affordable_ah.items())[:8]:
                    lines.append(
                        f"  {item}: {info.get('cheapest_price', '?')}cr "
                        f"(max {info.get('max_affordable', 0)} units)"
                    )

        # Last action result
        if last_result:
            icon = {"success": "✓", "partial": "~"}.get(last_result.get("status", ""), "✗")
            lines.append(
                f"\n[LAST: {last_result['action']} → {icon} {last_result.get('status','').upper()}]"
            )
            lines.append(f"  {last_result.get('summary','')}")
            if d := last_result.get("details"):
                lines.append("  " + "  ".join(f"{k}={v}" for k, v in d.items()))

        # Recent failures (persists across ticks)
        if self._recent_failures:
            lines.append("\n⛔ RECENT FAILURES (do NOT repeat these):")
            for fail in self._recent_failures[-5:]:
                detail = f" ({fail['details']})" if fail.get('details') else ""
                lines.append(f"  tick {fail['tick']}: {fail['action']} → {fail['status']} — {fail['summary']}{detail}")

        if warnings:
            lines.append("\n⚠ WARNINGS:\n" + "\n".join(f"  - {w}" for w in warnings))

        if combat.get("in_combat"):
            lines.append("\n⚔ IN COMBAT:\n" + "\n".join(
                f"  - {c['name']} (HP {int(c['integrity_pct'] * 100)}%)"
                for c in combat.get("combatants", [])
            ))

        # Build item type lookup from available_actions for inventory annotations
        _item_types: dict[str, str] = {}
        for a in avail_actions:
            if a.get("action") == "equip_item":
                for eq in a.get("equippable_items", []):
                    slot = eq.get("auto_slot", "equip")
                    _item_types[eq["item_id"]] = f"equip:{slot}"
            if a.get("action") == "use_item":
                for uid in (a.get("parameters", {}).get("item_id", {}).get("valid_values", [])):
                    if uid not in _item_types:
                        _item_types[uid] = "consumable"

        inv_parts = []
        for k, v in inventory.items():
            if v <= 0:
                continue
            tag = _item_types.get(k, "")
            tag_str = f" [{tag}]" if tag else ""
            inv_parts.append(f"{k}×{v}{tag_str}")
        lines.append(f"\nInventory: {', '.join(inv_parts) or 'empty'}")

        nearby_nodes = state.get("nearby_nodes", [])
        if nearby_nodes:
            lines.append("\nResource nodes:")
            current_tick_int = state.get("tick_info", {}).get("current_tick", 0)
            for n in nearby_nodes:
                if n.get("is_depleted"):
                    regen = n.get("regen_at_tick", "?")
                    wait  = (regen - current_tick_int) if isinstance(regen, int) else "?"
                    lines.append(
                        f"  ✗ {n['node_id']} [{n['resource']}] DEPLETED, ready tick {regen} ({wait}t)"
                    )
                elif not n.get("can_gather"):
                    cd = n.get("cooldown_ticks", 0)
                    skill_ok = n.get("your_skill", 0) >= n.get("required_level", 999)
                    if cd > 0 and skill_ok:
                        lines.append(
                            f"  ✗ {n['node_id']} [{n['resource']}] ON COOLDOWN"
                            f" — {cd} ticks remaining"
                        )
                    else:
                        lines.append(
                            f"  ✗ {n['node_id']} [{n['resource']}] skill too low"
                            f" (need {n['track']} Lv{n['required_level']}, have Lv{n.get('your_skill', 1)})"
                        )
                else:
                    lines.append(
                        f"  ✓ {n['node_id']} [{n['resource']}] ready"
                        f" ({n['track']} Lv{n.get('your_skill', 1)})"
                    )

        if nearby_npcs:
            lines.append("\nNearby NPCs:\n" + "\n".join(
                f"  - {n['name']} (id={n['npc_id']}, lv{n['level']}, {n['power_indicator']}"
                f"{'⚡ aggressive' if n.get('is_aggressive') else ''})"
                for n in nearby_npcs[:5]
            ))

        if nearby_agents:
            _rel_icons = {"RIVAL": "⚔", "ALLY": "✓", "CAUTIOUS": "⚠", "NEUTRAL": "~", "UNKNOWN": "?"}
            lines.append("\nNearby agents:\n" + "\n".join(
                f"  - {_rel_icons.get(a.get('relation', ''), '?')} {a.get('relation', '?')} {a['name']} "
                f"(id={a['agent_id']}, lv{a['level']}, {a['faction']}, {a['power_indicator']})"
                for a in nearby_agents[:5]
            ))

        if recent_events:
            lines.append("\nRecent events:\n" + "\n".join(
                f"  - {e.get('description', e)}" for e in recent_events
            ))

        if shop_inv:
            affordable = {
                item: info for item, info in shop_inv.items()
                if info.get("price", 9999) <= credits and info.get("stock", 0) > 0
            }
            if affordable:
                lines.append(
                    f"\n🏪 SHOP ({credits:.0f}cr available): "
                    + ", ".join(
                        f"{item} ({info['price']:.0f}cr, {info['stock']} in stock)"
                        for item, info in list(affordable.items())[:12]
                    )
                )
            else:
                out = {item: info for item, info in shop_inv.items() if info.get("stock", 0) > 0}
                lines.append(
                    f"\n🏪 SHOP: items available but none affordable ({credits:.0f}cr)"
                    if out else "\n🏪 SHOP: all items out of stock"
                )
        else:
            lines.append("\n🏪 No shop at this territory")

        if world_ctx:
            lines.append(f"\nLocation lore: {world_ctx[:220]}")

        # ── 🔁 YOUR RECENT ACTIONS (action history for loop awareness) ──── #
        if self._action_history:
            lines.append("\n🔁 YOUR RECENT ACTIONS:")
            for ah in self._action_history[-8:]:
                p = ah.get("parameters", {})
                p_str = f" {p}" if p else ""
                lines.append(f"  T{ah['tick']}: {ah['action']}{p_str}")

        # ── ⚠ REPETITION DETECTED (loop detection) ────────────────────── #
        if len(self._action_history) >= 3:
            recent_keys = []
            for ah in self._action_history[-6:]:
                key = ah["action"]
                p = ah.get("parameters", {})
                # Include key parameter for finer-grained detection
                if "item_id" in p:
                    key += f":{p['item_id']}"
                elif "node_id" in p:
                    key += f":{p['node_id']}"
                elif "territory" in p:
                    key += f":{p['territory']}"
                elif "target" in p:
                    key += f":{p['target']}"
                recent_keys.append(key)
            counts = Counter(recent_keys)
            repeated = [k for k, c in counts.items() if c >= 3]
            if repeated:
                lines.append(f"\n⚠ REPETITION DETECTED: {', '.join(repeated)} repeated 3+ times in recent history.")

        # ── 📡 SOCIAL INTELLIGENCE (shard feed, high-threat agents) ─────── #
        social_ctx = state.get("social_context", {})
        if social_ctx:
            roster = social_ctx.get("shard_roster", [])
            pvp_feed = social_ctx.get("pvp_feed", [])
            alliances = social_ctx.get("alliances", [])

            # High-threat agents
            high_threats = [r for r in roster if r.get("threat_level") in ("CRITICAL", "HIGH")]
            if high_threats:
                lines.append("\n⚠ HIGH-THREAT AGENTS:")
                for ht in high_threats[:5]:
                    bounty_tag = " 💀 HAS BOUNTY ON YOU" if ht.get("has_bounty_on_you") else ""
                    lines.append(
                        f"  {ht.get('relation', '?')} {ht['name']} lv{ht['level']} "
                        f"({ht['faction']}) @ {ht.get('territory', '?')} "
                        f"— {ht['threat_level']}: {ht.get('threat_reason', '')}{bounty_tag}"
                    )

            # PvP feed
            if pvp_feed:
                lines.append("\n📡 SHARD FEED:")
                for pf in pvp_feed[:5]:
                    me_tag = " ← INVOLVES YOU" if pf.get("involves_me") else ""
                    ally_tag = " ← YOUR ALLIANCE" if pf.get("involves_my_alliance") else ""
                    lines.append(f"  {pf.get('description', str(pf)[:80])}{me_tag}{ally_tag}")

            # Alliance info
            if alliances:
                lines.append("\n🤝 ALLIANCES:")
                for al in alliances[:3]:
                    members = al.get("members", [])
                    member_str = ", ".join(f"{m['name']} ({m['faction']})" for m in members[:5])
                    lines.append(f"  [{al.get('alliance_id', '?')}] {member_str}")

        lines.append("\nAvailable actions:")
        for a in avail_actions:
            aname, params = a["action"], a.get("parameters", {})
            # Skip actions explicitly marked unavailable
            if a.get("available") is False:
                lines.append(f"  {aname} (unavailable here)")
                continue
            # Filter accept_quest valid_values to exclude already-active quests
            # so the LLM doesn't see them as valid choices.
            if aname == "accept_quest" and params.get("quest_id"):
                qid_param = params["quest_id"]
                raw_valid = qid_param.get("valid_values", [])
                if raw_valid:
                    filtered = [q for q in raw_valid if q not in active_quest_ids]
                    if not filtered:
                        lines.append(f"  accept_quest — no new quests available (all already active)")
                        continue
                    # Use filtered list for display
                    params = dict(params)
                    params["quest_id"] = {**qid_param, "valid_values": filtered}
            if aname == "move" and (terr_param := params.get("territory", {})):
                # Rich move display: show all reachable territories with power costs
                travel_costs = terr_param.get("travel_costs", {})
                valid_terrs = terr_param.get("valid_values", [])
                current_power = state.get("power", 0)
                if travel_costs:
                    lines.append("  move:")
                    for terr in sorted(travel_costs, key=lambda t: travel_costs[t]):
                        cost = travel_costs[terr]
                        affordable = "✓" if cost <= current_power else "✗ not enough power"
                        lines.append(f"    - {terr} ({cost} power) {affordable}")
                elif valid_terrs:
                    lines.append(f"  move(territory): {', '.join(valid_terrs)}")
                else:
                    lines.append("  move (no destinations available)")
            elif aname == "gather" and (nodes := params.get("node_id", {}).get("nodes")):
                lines.append("  gather:")
                for n in nodes:
                    cd = n.get("cooldown_ticks", 0)
                    skill_ok = n.get("your_skill", 0) >= n.get("required_level", 999)
                    if not n["can_gather"]:
                        if cd > 0 and skill_ok:
                            reason = f" ✗ COOLDOWN ({cd}t)"
                        else:
                            reason = " ✗ skill too low"
                    else:
                        reason = ""
                    lines.append(
                        f"    - {n['node_id']} → {n['resource']} "
                        f"[{n['track']} lv{n['required_level']} req (you: lv{n['your_skill']})]"
                        f"{reason}"
                    )
            elif aname == "craft":
                cn = params.get("item_id", {}).get("craftable_now", [])
                if cn:
                    lines.append(f"  craft(item_id) — craftable now: {', '.join(cn)}")
                else:
                    # Don't list craft at all when nothing is craftable —
                    # the LLM will try to guess item names if it sees the action.
                    lines.append("  craft — ⛔ BLOCKED: nothing craftable (missing ingredients or skill too low)")
            elif aname == "accept_quest":
                qid_param = params.get("quest_id", {})
                valid = qid_param.get("valid_values", [])
                if valid:
                    lines.append(f"  accept_quest(quest_id) — ONLY these quest_ids are valid: {valid[:8]}"
                                 f"{'...' if len(valid) > 8 else ''}")
                else:
                    lines.append(f"  accept_quest — ⛔ no quests available to accept")
            elif params:
                hints = [
                    f"{pn}: {pd.get('valid_values', [])[:8]}"
                    f"{'...' if len(pd.get('valid_values', [])) > 8 else ''}"
                    if pd.get("valid_values") else pn
                    for pn, pd in params.items()
                ]
                lines.append(f"  {aname}({', '.join(hints)})")
            else:
                lines.append(f"  {aname}")

        lines.append("\nConnect your action to your goals and memory.  Respond with a single JSON object.")
        return "\n".join(lines)

    # ── Payload logging ───────────────────────────────────────────────────── #

    @staticmethod
    def _setup_payload_log(agent_name: str, log_dir: Path) -> logging.Logger:
        log_dir.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = log_dir / f"payloads_{agent_name}_{ts}.log"
        pl   = logging.getLogger(f"tne_sdk.payloads.{agent_name}")
        pl.setLevel(logging.DEBUG)
        pl.propagate = False
        fh  = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        pl.addHandler(fh)
        logger.info("Payload log: %s", path)
        return pl

    def _log_payload(self, direction: str, payload: dict, tick: Any = "?") -> None:
        try:
            body = json.dumps(payload, indent=2, default=str)
        except TypeError:
            body = repr(payload)

        # File-based payload log (truncated to save disk space)
        if self._payload_log is not None:
            truncated = body[:8000] + ("\n... (truncated)" if len(body) > 8000 else "")
            self._payload_log.debug("[tick=%s] [%s]\n%s\n%s", tick, direction, truncated, "─" * 80)

        # TUI-visible payload at VERBOSE level (5) — full, untruncated.
        # The @@PAYLOAD@@ prefix tells LogView to render a collapsible block.
        logger.log(5, "@@PAYLOAD@@%s (tick %s)\n%s", direction, tick, body)

