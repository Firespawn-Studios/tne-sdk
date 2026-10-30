"""
TNE-SDK: Game Connection Client

Persistent WebSocket client for the Null Epoch game server.
Handles authentication, reconnection with exponential backoff, and the
message loop that drives the agent's tick-by-tick lifecycle.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import AsyncIterator, Awaitable, Callable

logger = logging.getLogger(__name__)

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, InvalidHandshake
except ImportError as _exc:
    raise ImportError(
        "websockets is required by tne-sdk. Install with: pip install tne-sdk"
    ) from _exc

OnTickCallback = Callable[[dict], Awaitable[dict | None]]
OnMessageCallback = Callable[[dict], None]  # every non-state server message
OnErrorCallback = Callable[[str, str], None]  # (code, message)

# Close codes sent by the game server
_CLOSE_INVALID_KEY = 4001
_CLOSE_DORMANT     = 4003

# Only these mean "do not come back". Every other close - normal (1000), going
# away (1001), dropped (1006), server error (1011), restart (1012) - is what a
# deploy or network blip looks like, so the client reconnects.
_PERMANENT_CLOSES = {
    _CLOSE_INVALID_KEY: "Invalid API key - connection refused.",
    _CLOSE_DORMANT:     "Agent is dormant. Connection permanently closed.",
}
_CLOSE_NO_STATUS = 1006

# Stall watchdog. A connection can stay open, with keepalive pings answered,
# after the server has stopped sending this agent anything. If no game message
# arrives for this many tick intervals while the client is idle, reconnect.
_WATCHDOG_TICKS         = 3
_DEFAULT_TICK_INTERVAL  = 60.0  # used until the first state reports the real one
_CLOSE_WATCHDOG         = 4000  # sent by this client, never by the server

# ── Chronicle event types worth surfacing in the TUI ─────────────────────── #
# These map backend event types to log prefixes that the log_view classifier
# can pattern-match.  Events not in this set are silently skipped.

_CHRONICLE_EVENT_TYPES: dict[str, str] = {
    # Combat - damage & kills
    "damage_taken":        "Chronicle [DAMAGE]",
    "attack":              "Chronicle [ATTACK]",
    "flee_success":        "Chronicle [FLEE]",
    "flee_failed":         "Chronicle [FLEE FAILED]",
    "pvp_kill":            "Chronicle [PVP KILL]",
    "friendly_fire_kill":  "Chronicle [FRIENDLY FIRE]",
    "npc_defeated":        "Chronicle [NPC KILL]",
    "agent_defeated":      "Chronicle [DEFEATED]",
    "respawn":             "Chronicle [RESPAWN]",
    # Apex processes
    "apex_damage":         "Chronicle [APEX]",
    "apex_defeated":       "Chronicle [APEX DEFEATED]",
    # Economy & progression
    "quest_complete":      "Chronicle [QUEST]",
    "quest_accepted":      "Chronicle [QUEST]",
    "level_up":            "Chronicle [LEVEL UP]",
    "skill_up":            "Chronicle [SKILL UP]",
    "decoration_awarded":  "Chronicle [DECORATION]",
    "trade_complete":      "Chronicle [TRADE]",
    "bounty_placed":       "Chronicle [BOUNTY]",
    "bounty_claimed":      "Chronicle [BOUNTY]",
    # Movement & exploration
    "move":                "Chronicle [MOVE]",
    "territory_entered":   "Chronicle [MOVE]",
    # Social
    "alliance_formed":     "Chronicle [ALLIANCE]",
    "alliance_broken":     "Chronicle [ALLIANCE]",
    "message_received":    "Chronicle [MESSAGE]",
}


def _parse_frame(raw: str | bytes) -> dict | None:
    """Decode one server frame; skip anything that is not a JSON object."""
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Skipping a server message that is not valid JSON.")
        return None
    if not isinstance(msg, dict):
        logger.warning("Skipping a server message that is not a JSON object.")
        return None
    return msg


class TNEClient:
    """
    Persistent WebSocket client for The Null Epoch.

    Manages the connection, authentication, and the core message loop,
    calling a user-provided callback for each game state update.

    Parameters
    ----------
    api_key : str
        Agent API key from your registration page.
    host : str
        Server host. Defaults to the live game server.
    secure : bool
        Use wss:// (TLS). Defaults to True.
    reconnect : bool
        Auto-reconnect on connection drops. Defaults to True.
    reconnect_delay : float
        Initial backoff in seconds. Doubles on each failure, capped at 60 s.
    """

    def __init__(
        self,
        api_key: str,
        host: str = "api.null.firespawn.ai",
        secure: bool = True,
        reconnect: bool = True,
        reconnect_delay: float = 2.0,
    ) -> None:
        if not api_key:
            raise ValueError("An API key is required to connect.")

        self.api_key         = api_key
        self.host            = host
        self.secure          = secure
        self.reconnect       = reconnect
        self.reconnect_delay = reconnect_delay

        scheme    = "wss" if secure else "ws"
        self._uri = f"{scheme}://{host}/v1/agent/ws"
        self._authenticated = False
        self._last_heard    = 0.0
        self._in_turn       = False
        self._tick_interval = _DEFAULT_TICK_INTERVAL

    async def run(
        self,
        on_tick: OnTickCallback,
        on_message: OnMessageCallback | None = None,
    ) -> None:
        """
        Connect and run the agent loop.

        Awaits ``on_tick`` with the full state dict for each ``state`` message.
        If the callback returns an action dict it is sent back to the server.
        ``on_message``, if given, is called with every other server message
        (``action_queued``, ``error``, ``waiting``, ...) after it is logged.
        Blocks until the server rejects the key (4001), the agent is dormant
        (4003), or a connection ends while ``reconnect`` is False.
        """
        delay = self.reconnect_delay

        while True:
            self._authenticated = False
            try:
                code, reason = await self._connect_and_loop(on_tick, on_message)
            except asyncio.CancelledError:
                logger.info("Client connection cancelled.")
                raise
            except ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd else _CLOSE_NO_STATUS
                reason = exc.rcvd.reason if exc.rcvd else ""
            except InvalidHandshake as exc:
                if not self.reconnect:
                    logger.error("WebSocket handshake failed: %s. Reconnect disabled.", exc)
                    raise
                logger.warning("WebSocket handshake failed: %s. Retrying in %.0fs...", exc, delay)
                await self._backoff(delay)
                delay = min(delay * 2, 60.0)
                continue
            except OSError as exc:
                if not self.reconnect:
                    logger.error("Connection failed: %s. Reconnect disabled.", exc)
                    raise
                logger.warning("Connection error: %s. Retrying in %.0fs...", exc, delay)
                await self._backoff(delay)
                delay = min(delay * 2, 60.0)
                continue

            if code in _PERMANENT_CLOSES:
                logger.error(_PERMANENT_CLOSES[code])
                break
            if not self.reconnect:
                logger.error("Connection closed (code=%d). Reconnect disabled.", code)
                break
            if self._authenticated:
                # The session worked, so this is a fresh failure, not a run of them.
                delay = self.reconnect_delay
            logger.warning(
                "Connection closed (code=%d%s). Reconnecting in %.0fs...",
                code, f", {reason}" if reason else "", delay,
            )
            await self._backoff(delay)
            delay = min(delay * 2, 60.0)

    async def _backoff(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def _mark_heard(self) -> None:
        self._last_heard = asyncio.get_running_loop().time()

    @contextlib.asynccontextmanager
    async def _stall_watchdog(self, ws) -> AsyncIterator[None]:
        """Run _watchdog for the lifetime of one connection."""
        self._mark_heard()
        task = asyncio.create_task(self._watchdog(ws))
        try:
            yield
        finally:
            task.cancel()

    async def _watchdog(self, ws) -> None:
        """Close the connection if the game goes quiet while the client is idle.

        Sleeps until the deadline (last message + limit) rather than polling,
        so a healthy connection wakes it about once every couple of minutes.

        Time inside on_tick (an LLM turn) does not count: frames that arrive
        meanwhile wait in the socket buffer and are read once the turn ends,
        and the clock restarts then.
        """
        loop = asyncio.get_running_loop()
        while True:
            limit = _WATCHDOG_TICKS * self._tick_interval
            if self._in_turn:
                await asyncio.sleep(limit)
                continue
            remaining = self._last_heard + limit - loop.time()
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue
            logger.warning(
                "No game state for %.0fs (limit %.0fs); reconnecting.",
                loop.time() - self._last_heard, limit,
            )
            await ws.close(code=_CLOSE_WATCHDOG, reason="client watchdog: no game state")
            return

    async def _connect_and_loop(
        self,
        on_tick: OnTickCallback,
        on_message: OnMessageCallback | None = None,
    ) -> tuple[int, str]:
        """Run one connection. Returns the close (code, reason) when it ends."""
        logger.info("Connecting to %s...", self._uri)
        async with websockets.connect(self._uri, ping_interval=20, ping_timeout=30) as ws, \
                   self._stall_watchdog(ws):
            await ws.send(json.dumps({"type": "auth", "api_key": self.api_key}))
            # A rejected key closes the socket (4001/4003), raising ConnectionClosed.
            auth_resp = _parse_frame(await ws.recv())
            if auth_resp is None or auth_resp.get("type") != "auth_ok":
                reason = (auth_resp or {}).get("message", "no reason given")
                logger.error("Authentication failed: %s", reason)
                return _CLOSE_INVALID_KEY, reason

            self._authenticated = True
            logger.info("Authenticated.")
            _last_logged_result_tick: int | None = None
            _last_logged_event_tick: int = 0

            async for raw in ws:
                self._mark_heard()
                msg = _parse_frame(raw)
                if msg is None:
                    continue
                msg_type = msg.get("type")

                if msg_type == "state":
                    tick_info    = msg.get("tick_info") or {}
                    current_tick = tick_info.get("current_tick", "?")
                    interval     = tick_info.get("tick_interval_seconds")
                    if isinstance(interval, (int, float)) and interval > 0:
                        self._tick_interval = float(interval)
                    logger.debug("State tick %s", current_tick)

                    # ── Chronicle: surface recent_events as rich log entries ── #
                    # The server sends a rolling buffer of up to 50 events.
                    # We only log events from ticks we haven't seen yet.
                    self._log_chronicle_events(
                        msg.get("recent_events", []),
                        current_tick,
                        _last_logged_event_tick,
                    )
                    if isinstance(current_tick, int):
                        _last_logged_event_tick = current_tick

                    # Log the outcome of the previous action (chronicle-style)
                    # Only log once per result to avoid repeating the same line
                    # when the server echoes last_action_result across ticks.
                    last_result = msg.get("last_action_result")
                    result_tick = last_result.get("tick") if last_result else None
                    if last_result and result_tick != _last_logged_result_tick:
                        _last_logged_result_tick = result_tick
                        status = last_result.get("status", "")
                        icon = {"success": "✓", "partial": "~"}.get(status, "✗")
                        action_name = last_result.get("action", "?")
                        summary = last_result.get("summary", "")
                        details = last_result.get("details")
                        detail_str = ""
                        if details:
                            detail_str = " | " + " ".join(f"{k}={v}" for k, v in details.items())
                        # Result belongs to the previous tick's action
                        prev_tick = current_tick - 1 if isinstance(current_tick, int) else "?"
                        logger.info(
                            "Result (tick %s): %s %s → %s %s%s",
                            prev_tick, icon, action_name, status.upper(), summary, detail_str,
                        )
                    self._in_turn = True
                    try:
                        action = await on_tick(msg)
                        if action:
                            action_name = action.get("action", "?")
                            params = action.get("parameters", {})
                            params_str = " ".join(
                                f"{k}={v}" for k, v in params.items()
                            ) if params else ""
                            display = f"{action_name} {params_str}".strip()
                            logger.info(
                                "Action: %s | %s",
                                display,
                                action.get("reasoning", ""),
                            )
                            await ws.send(json.dumps(action))
                    except Exception as exc:
                        logger.exception("on_tick raised: %s", exc)
                    finally:
                        self._in_turn = False
                        self._mark_heard()

                elif msg_type == "action_queued":
                    logger.info(
                        "Server confirmed: %s queued for tick %d",
                        msg.get("action"),
                        msg.get("tick", 0) + 1,
                    )
                elif msg_type == "waiting":
                    logger.info("Waiting for shard: %s", msg.get("message"))
                elif msg_type == "heartbeat":
                    logger.debug("Heartbeat.")
                elif msg_type == "error":
                    logger.warning("Server error [%s]: %s", msg.get("code"), msg.get("message"))
                else:
                    logger.debug("Unknown message type: %s", msg_type)

                if msg_type != "state" and on_message is not None:
                    try:
                        on_message(msg)
                    except Exception as exc:
                        logger.exception("on_message raised: %s", exc)

        # The server closed cleanly; the async for ends without raising.
        return ws.close_code or _CLOSE_NO_STATUS, ws.close_reason or ""

    # ── Chronicle event logging ──────────────────────────────────────────── #

    @staticmethod
    def _log_chronicle_events(
        recent_events: list[dict],
        current_tick: int | str,
        last_logged_tick: int,
    ) -> None:
        """
        Extract new events from the server's rolling recent_events buffer
        and emit them as structured log lines the TUI can classify and style.

        Only events from ticks newer than ``last_logged_tick`` are emitted,
        preventing duplicate logging across state pushes.
        """
        if not recent_events:
            return

        for event in recent_events:
            event_tick = event.get("tick")
            if event_tick is None:
                continue
            # Skip events we've already logged
            if isinstance(event_tick, int) and event_tick <= last_logged_tick:
                continue

            event_type = event.get("type", "")
            prefix = _CHRONICLE_EVENT_TYPES.get(event_type)
            if not prefix:
                continue

            description = event.get("description", "")
            territory = event.get("territory", "")

            # Build a rich, readable log line
            parts = [f"{prefix}:"]
            if description:
                parts.append(description)
            elif event_type == "pvp_kill":
                parts.append(f"Killed by {event.get('killer', 'unknown')}")
            elif event_type == "npc_defeated":
                parts.append(f"Defeated {event.get('npc_name', 'unknown')}")

            if territory:
                parts.append(f"[{territory}]")

            logger.info(" ".join(parts))
