"""
TNE-SDK: File Relay Client

Bridges the game's WebSocket connection to/from plain files so that
CLI coding agents (Claude Code, Gemini CLI, OpenHands, etc.) or any
tool that can read/write files can play the game without native
WebSocket or SSE support.

The relay holds a persistent WebSocket connection.  Each tick it writes
the full game state to relay/state.json and waits for an action to
appear in relay/action.json.  If no action arrives within the timeout,
it sends a safe "wait" so the game never stalls.

Usage
-----
    from tne_sdk.relay import FileRelayClient

    relay = FileRelayClient(api_key="ne_xxx", host="api.null.firespawn.ai", secure=True)
    asyncio.run(relay.run())

Or via CLI::

    tne-relay --api-key ne_xxx --live
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.2  # seconds between action.json checks


class FileRelayClient:
    """
    File-based relay for AI coding assistants.

    Writes game state to ``relay_dir/state.json`` each tick.
    Reads actions from ``relay_dir/action.json`` (consumed on read).
    Writes server confirmations to ``relay_dir/result.json``.

    Parameters
    ----------
    api_key : str
        Your agent API key.
    host : str
        Game server host. Defaults to the live game server.
    secure : bool
        Use wss:// (TLS). Defaults to True.
    relay_dir : Path | str
        Directory for relay files. Created automatically.
    action_timeout : float | None
        Seconds to wait for action.json before auto-wait. None = wait forever.
    reconnect : bool
        Auto-reconnect on drops.
    reconnect_delay : float
        Initial backoff seconds.
    """

    def __init__(
        self,
        api_key: str,
        host: str = "api.null.firespawn.ai",
        secure: bool = True,
        relay_dir: Path | str = "relay",
        action_timeout: float | None = 45.0,
        reconnect: bool = True,
        reconnect_delay: float = 2.0,
    ):
        if not api_key:
            raise ValueError("An API key is required to connect.")

        self.api_key = api_key
        self.host = host
        self.secure = secure
        self.reconnect = reconnect
        self.reconnect_delay = reconnect_delay
        self.action_timeout = action_timeout

        self.relay_dir = Path(relay_dir)
        self.relay_dir.mkdir(parents=True, exist_ok=True)

        self._state_file  = self.relay_dir / "state.json"
        self._action_file = self.relay_dir / "action.json"
        self._result_file = self.relay_dir / "result.json"
        self._tick_file   = self.relay_dir / "tick.txt"

        scheme = "wss" if secure else "ws"
        self._uri = f"{scheme}://{host}/v1/agent/ws"

    async def run(self) -> None:
        """Start the relay. Blocks until stopped or the connection ends for good.

        The connection itself (reconnects, stall watchdog) is TNEClient's; the
        relay only moves state, actions and server replies to and from files.
        """
        from .client import TNEClient

        self._print_banner()
        client = TNEClient(
            api_key         = self.api_key,
            host            = self.host,
            secure          = self.secure,
            reconnect       = self.reconnect,
            reconnect_delay = self.reconnect_delay,
        )
        await client.run(self._on_state, on_message=self._on_message)

    async def _on_state(self, state: dict[str, Any]) -> dict[str, Any]:
        tick = state.get("tick_info", {}).get("current_tick", "?")
        territory = state.get("current_territory", "?")
        hp = state.get("integrity", 0)
        max_hp = state.get("max_integrity", 1)

        # Write state atomically (tmp + rename)
        tmp = self._state_file.with_suffix(".tmp")
        self._write_json(tmp, state)
        tmp.replace(self._state_file)
        self._tick_file.write_text(str(tick), encoding="utf-8")

        timeout_str = f"{self.action_timeout:.0f}s" if self.action_timeout else "no timeout"
        logger.info(
            "Tick %s | %s | HP %s/%s | Waiting for action (%s)",
            tick, territory, hp, max_hp, timeout_str,
        )
        return await self._wait_for_action()

    def _on_message(self, msg: dict[str, Any]) -> None:
        """Report the server's reply to the last action in result.json."""
        msg_type = msg.get("type")
        if msg_type == "action_queued":
            self._write_json(self._result_file, {
                "status": "queued",
                "action": msg.get("action"),
                "for_tick": (msg.get("tick") or 0) + 1,
            })
        elif msg_type == "error":
            self._write_json(self._result_file, {
                "status": "error",
                "code": msg.get("code"),
                "message": msg.get("message"),
            })

    async def _wait_for_action(self) -> dict[str, Any]:
        elapsed = 0.0
        while self.action_timeout is None or elapsed < self.action_timeout:
            if self._action_file.exists():
                try:
                    raw = self._action_file.read_text(encoding="utf-8").strip()
                    action = json.loads(raw)
                except (json.JSONDecodeError, OSError) as e:
                    self._reject_action_file(f"action.json is not valid JSON: {e}")
                else:
                    if isinstance(action, dict):
                        self._action_file.unlink(missing_ok=True)
                        return action
                    self._reject_action_file(
                        'action.json must be a JSON object such as {"action": "wait"}.'
                    )
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

        return {"action": "wait", "reasoning": "Timeout, no action submitted."}

    def _reject_action_file(self, message: str) -> None:
        logger.warning("Bad action.json: %s", message)
        self._action_file.unlink(missing_ok=True)
        self._write_json(self._result_file, {
            "status": "error", "code": "invalid_action_file", "message": message,
        })

    def _print_banner(self) -> None:
        abs_dir = self.relay_dir.absolute()
        print(f"""
╔══════════════════════════════════════════════════════╗
║         NULL EPOCH - File Relay                      ║
╠══════════════════════════════════════════════════════╣
║  Server : {self._uri:<42}║
║  Relay  : {str(abs_dir):<42}║
╠══════════════════════════════════════════════════════╣
║  READ STATE  →  cat {self.relay_dir}/state.json
║  SEND ACTION →  write to {self.relay_dir}/action.json
║  CHECK RESULT→  cat {self.relay_dir}/result.json
╚══════════════════════════════════════════════════════╝
Timeout: {f"{self.action_timeout:.0f}s" if self.action_timeout else "none (wait forever)"}
Press Ctrl+C to stop.
""")

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
