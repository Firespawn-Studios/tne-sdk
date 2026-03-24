"""
NULL EPOCH: Basic Agent Runner
================================
Fill in the four fields in the CONFIG block below, then run:

    python basic_agent.py

The agent will connect to NULL EPOCH, maintain persistent SQLite memory,
and choose actions using your LLM on every game tick.  A compact live
display prints each tick, no extra dependencies required.

To inject a mid-run directive without stopping the agent, create:
    directives_for_<agent-name>.txt
one directive per line.  The file is consumed and deleted on the next tick.

INSTALL
-------
    pip install tne-sdk           # all LLM providers included
    pip install "tne-sdk[all]"    # + interactive TUI launcher

LLM ENDPOINT URLs
-----------------
    Local   vLLM / LM Studio  →  http://localhost:8000/v1
            Ollama             →  http://localhost:11434/v1
    Cloud   OpenAI             →  https://api.openai.com/v1
            Anthropic Claude   →  https://api.anthropic.com/v1
            DeepInfra / Groq   →  https://api.deepinfra.com/v1/openai
                                  https://api.groq.com/openai/v1
    The SDK auto-selects the right provider from the URL, no extra config.
"""
from __future__ import annotations

import asyncio
import logging

from tne_sdk import Agent, AgentConfig, LocalMemory, TNEClient, TickSummary
from tne_sdk import provider_from_profile

# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIG: edit these, then run
# ═══════════════════════════════════════════════════════════════════════════════

PROFILE = {
    # A name for your agent. Also used as the memory DB filename.
    "name":        "Spectre-7",

    # Game API key from your agent registration page.
    "api_key":     "ne_xxxxxxxxxxxx",

    # Your LLM's base URL (see table above).
    "llm_url":     "http://localhost:8000/v1",

    # LLM provider key. Leave blank for local inference.
    "llm_api_key": "",

    # Model name exactly as your endpoint expects it.
    "model":       "Qwen/Qwen3-14B",

    # ── Sampling ───────────────────────────────────────────────────────── #
    # Standard params - used for action turns, and for ALL calls when
    # thinking is off.
    "temperature": 0.7,
    "top_p":       0.8,
    "top_k":       20,
    "presence_penalty": 1.5,

    # ── Thinking mode ──────────────────────────────────────────────────── #
    # Set true for Qwen3, Qwen3.5, DeepSeek-R1, and similar reasoning
    # models.  When enabled, reflection and tactical review use the
    # thinking_* params below.  When disabled, all calls use the standard
    # params above and thinking is turned off everywhere.
    "enable_thinking": False,

    # Sampling overrides for reflection/tactical when thinking is on.
    # Ignored when enable_thinking is False.
    # "thinking_temperature": 1.0,
    # "thinking_top_p": 0.95,
    # "thinking_presence_penalty": 1.5,

    # ── Token budgets ──────────────────────────────────────────────────── #
    # "max_tokens": 2048,              # action turns
    # "max_tokens_reflection": 6144,   # reflection cycles
    # "max_tokens_tactical": 1024,     # tactical reviews

    # ── Cognitive cycles ───────────────────────────────────────────────── #
    # How often to run each cognitive cycle (in ticks).
    "reflection_cooldown_ticks":      200,
    "tactical_review_cooldown_ticks":  10,

    # Seconds to wait for an LLM response. Raise for slow local quants.
    "llm_timeout": 120,

    # ── Custom prompts ─────────────────────────────────────────────────── #
    # Point these at .txt files to override the built-in prompts.
    # "system_prompt_file": "prompts/action_system.txt",
    # "reflection_system_prompt_file": "prompts/reflection_system.txt",
    # "reflection_user_prompt_file": "prompts/reflection_user.txt",
    # "tactical_system_prompt_file": "prompts/tactical_system.txt",
    # "tactical_user_prompt_file": "prompts/tactical_user.txt",

    # ── Meta directive ──────────────────────────────────────────────────── #
    # A persistent high-level goal injected into every action prompt.
    # Unlike directives (one-shot coaching), this is always present.
    # "meta_directive": "Top the leaderboards",

    # Set true to write full LLM request + response JSON to logs/ (debugging).
    "log_payloads": False,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  LIVE DISPLAY: compact per-tick output, no extra dependencies
# ═══════════════════════════════════════════════════════════════════════════════

def _G(s: object) -> str: return f"\033[32m{s}\033[0m"   # green
def _Y(s: object) -> str: return f"\033[33m{s}\033[0m"   # yellow
def _R(s: object) -> str: return f"\033[31m{s}\033[0m"   # red
def _B(s: object) -> str: return f"\033[1m{s}\033[0m"    # bold
def _D(s: object) -> str: return f"\033[2m{s}\033[0m"    # dim


def _bar(pct: int, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    bar    = "█" * filled + "░" * (width - filled)
    if pct < 25:  return _R(bar)
    if pct < 50:  return _Y(bar)
    return _G(bar)


def on_tick(s: TickSummary) -> None:
    hp_pct  = int(s.integrity / max(1, s.max_integrity) * 100)
    pwr_pct = int(s.power     / max(1, s.max_power)     * 100)
    combat  = f"  {_R('⚔ IN COMBAT')}" if s.in_combat else ""
    elapsed = f"{s.elapsed_ms / 1000:.1f}s"

    print(
        f"{_D('─' * 72)}\n"
        f"{_B(f'T{s.tick:,}')}  {s.territory:<22}"
        f"HP {_bar(hp_pct)} {hp_pct:3d}%  "
        f"PWR {_bar(pwr_pct, 8)} {pwr_pct:3d}%  "
        f"CR {s.credits:,.0f}  LV{s.level}{combat}\n"
        f"  → {_B(s.last_action)} ({elapsed})  {_D(s.reasoning[:90])}"
    )

    # Top directive and top goal: the two things a watcher most wants to see
    if s.active_directives:
        print(f"  ⭐ {s.active_directives[0]['text'][:80]}")
    if s.active_tasks:
        t = s.active_tasks[0]
        print(f"  🎯 [{t['priority']:3d}] {t['description'][:80]}")


# ═══════════════════════════════════════════════════════════════════════════════
#  WIRING
# ═══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    cfg = AgentConfig.from_dict(PROFILE)

    client = TNEClient(api_key=PROFILE["api_key"])
    memory = LocalMemory(agent_name=PROFILE["name"], db_path="logs/")
    llm    = provider_from_profile(PROFILE, timeout=cfg.llm_timeout)

    agent = Agent(
        config          = cfg,
        client          = client,
        memory          = memory,
        llm_provider    = llm,
        name            = PROFILE["name"],
        on_tick_summary = on_tick,
        log_payloads    = cfg.log_payloads,
        log_dir         = cfg.log_dir,
    )

    print(
        f"\n{_B('NULL EPOCH')} - {_G(PROFILE['name'])}\n"
        f"  LLM  : {PROFILE['llm_url']}  [{PROFILE['model']}]\n"
        f"  Mem  : logs/agent_memory_{PROFILE['name']}.db\n"
        f"  Stop : Ctrl+C\n"
        f"  Directive injection: create 'directives_for_{PROFILE['name']}.txt'\n"
    )

    await agent.run()


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s  %(name)-20s  %(message)s",
        datefmt = "%H:%M:%S",
    )
    # Keep noisy HTTP/WS libs quiet unless you want the noise
    for _lib in ("httpx", "httpcore", "websockets"):
        logging.getLogger(_lib).setLevel(logging.WARNING)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{_D('Agent stopped.')}")
