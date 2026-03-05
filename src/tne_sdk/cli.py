"""
TNE-SDK: CLI Entry Points

tne-run     : headless agent runner (intermediate / advanced users, Docker, CI)
tne-launcher: Textual TUI launcher (beginner-friendly desktop experience)
tne-relay   : file relay for CLI coding agents (Claude Code, Gemini CLI, etc.)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .profile_store import LIVE_GAME_HOST


# ── tne-run ───────────────────────────────────────────────────────────────── #

def run_main() -> None:
    parser = argparse.ArgumentParser(
        prog="tne-run",
        description="Run a NULL EPOCH agent in headless mode.",
        epilog=(
            "Profile file: ~/.tne_sdk/agents.json  (override: TNE_PROFILES_PATH env var)\n"
            "Use 'tne-launcher' for an interactive setup UI."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--agent",        default=None,        help="Agent profile name from agents.json")
    parser.add_argument("--list",         action="store_true", help="List configured agents and exit")
    parser.add_argument("--game-host",    default=None,        help="Override game server host (default: live server)")
    parser.add_argument("--no-memory",    action="store_true", help="Stateless mode (use NullMemory)")
    parser.add_argument("--log-payloads", action="store_true", help="Write full request/response JSON to logs/")
    parser.add_argument("--verbose",      action="store_true", help="DEBUG-level logging")
    parser.add_argument("--profiles",     default=None,        help="Path to agents.json (overrides default / env)")
    args = parser.parse_args()

    _configure_logging(args.verbose)
    logger = logging.getLogger("tne-run")

    from .profile_store import ProfileStore, ProfileValidationError
    store = ProfileStore(path=args.profiles)
    store.load()

    # ── --list ────────────────────────────────────────────────────────────── #
    if args.list:
        names = store.list_names()
        if not names:
            print(
                f"No agents configured.\n"
                f"  Profile file: {store.path}\n\n"
                f"  Add one with: tne-launcher\n"
                f"  Or edit JSON: {store.path}"
            )
        else:
            print(f"Configured agents ({store.path}):")
            for p in store.profiles:
                print(f"  {p['name']:<20}  model={p.get('model','?')}")
        sys.exit(0)

    # ── --agent required for everything else ─────────────────────────────── #
    if not args.agent:
        print(
            "Usage: tne-run --agent <name> [options]\n\n"
            f"  Profile file : {store.path}\n"
            f"  Configured   : {', '.join(store.list_names()) or '(none yet)'}\n\n"
            "  Quick start:\n"
            "    tne-run --list                     show configured agents\n"
            "    tne-launcher                       interactive setup UI\n"
            "    tne-run --help                     full option reference\n",
            file=sys.stderr,
        )
        sys.exit(1)

    profile = store.get(args.agent)
    if profile is None:
        names = store.list_names()
        if names:
            logger.error(
                "Agent '%s' not found.  Available: %s\n"
                "  Run 'tne-run --list' to see all configured agents.",
                args.agent, ", ".join(names),
            )
        else:
            logger.error(
                "No agents configured in %s.\n"
                "  Run 'tne-launcher' to add one, or create %s manually.",
                store.path, store.path,
            )
        sys.exit(1)

    try:
        _validate_profile(profile)
    except ProfileValidationError as exc:
        logger.error("Profile validation failed: %s", exc)
        sys.exit(1)

    # ── Build components ──────────────────────────────────────────────────── #
    from .config import AgentConfig
    from .client import TNEClient
    from .llm.providers import provider_from_profile
    from .agent import Agent

    cfg = AgentConfig.from_dict(profile)
    if args.log_payloads:
        cfg.log_payloads = True

    # Game host: --game-host override or live server default
    if args.game_host:
        host   = args.game_host
        secure = not host.startswith("127.")
    else:
        host   = LIVE_GAME_HOST
        secure = True

    client = TNEClient(api_key=profile["api_key"], host=host, secure=secure)

    if args.no_memory:
        from .memory.null_memory import NullMemory
        memory = NullMemory()
    else:
        from .memory.local_memory import LocalMemory
        memory = LocalMemory(agent_name=profile["name"], db_path=cfg.log_dir)

    llm = provider_from_profile(profile, timeout=cfg.llm_timeout)

    agent = Agent(
        config       = cfg,
        client       = client,
        memory       = memory,
        llm_provider = llm,
        name         = profile["name"],
        log_payloads = cfg.log_payloads,
        log_dir      = cfg.log_dir,
    )

    logger.info(
        "Starting '%s'  |  game: %s  llm: %s",
        profile["name"], host, profile["llm_url"],
    )
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("Interrupted, agent stopped.")


# ── tne-launcher ─────────────────────────────────────────────────────────── #

def launcher_main() -> None:
    parser = argparse.ArgumentParser(
        prog="tne-launcher",
        description="NULL EPOCH: interactive TUI launcher.",
    )
    parser.add_argument("--data-dir", default=None, help="Custom data directory for profiles + logs")
    args = parser.parse_args()

    try:
        from .launcher.app import TNELauncherApp
    except ImportError as exc:
        print(
            f"ERROR: Textual not installed: {exc}\n"
            "  Run: pip install \"tne-sdk[launcher]\"",
            file=sys.stderr,
        )
        sys.exit(1)

    data_dir = Path(args.data_dir) if args.data_dir else None
    app = TNELauncherApp(data_dir=data_dir)
    app.run()


# ── tne-relay ─────────────────────────────────────────────────────────────── #

def relay_main() -> None:
    parser = argparse.ArgumentParser(
        prog="tne-relay",
        description=(
            "NULL EPOCH file relay: bridges the game WebSocket to plain files.\n"
            "Designed for CLI coding agents (Claude Code, Gemini CLI, OpenHands, etc.).\n\n"
            "Each tick the relay writes state to relay/state.json.\n"
            "Write your action to relay/action.json and the relay picks it up and sends it."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--api-key",    required=True, metavar="KEY",
                        help="NULL EPOCH game API key")
    parser.add_argument("--host",       default=LIVE_GAME_HOST, metavar="HOST:PORT",
                        help=f"Game server host (default: {LIVE_GAME_HOST})")
    parser.add_argument("--no-secure",  action="store_true",
                        help="Disable TLS (wss:// → ws://)")
    parser.add_argument("--timeout",    default=45, type=float, metavar="SEC",
                        help="Seconds to wait for action.json before auto-wait (default: 45)")
    parser.add_argument("--no-timeout", action="store_true",
                        help="Wait forever for an action (overrides --timeout)")
    parser.add_argument("--relay-dir",  default="relay", metavar="DIR",
                        help="Directory for relay files (default: relay/)")
    parser.add_argument("--verbose",    action="store_true", help="DEBUG-level logging")
    args = parser.parse_args()

    _configure_logging(args.verbose)

    from .relay import FileRelayClient

    host   = args.host
    secure = not args.no_secure
    timeout: float | None = None if args.no_timeout else args.timeout

    relay = FileRelayClient(
        api_key         = args.api_key,
        host            = host,
        secure          = secure,
        relay_dir       = args.relay_dir,
        action_timeout  = timeout,
    )

    try:
        asyncio.run(relay.run())
    except KeyboardInterrupt:
        print("\nRelay stopped.")


# ── Shared ────────────────────────────────────────────────────────────────── #

def _validate_profile(profile: dict) -> None:
    from .profile_store import _validate_profile as _vp
    _vp(profile)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level   = logging.DEBUG if verbose else logging.INFO,
        format  = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt = "%H:%M:%S",
    )
    if not verbose:
        for noisy in ("httpx", "httpcore", "websockets"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
