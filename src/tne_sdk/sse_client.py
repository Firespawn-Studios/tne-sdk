"""
TNE-SDK: SSE Client

Server-Sent Events client for connecting to the Null Epoch game server.
An alternative to the WebSocket-based TNEClient for environments where
WebSockets are unavailable or impractical (CLI agents, proxied networks,
simpler client code).

State is received via GET /v1/agent/stream (SSE).
Actions are submitted via POST /v1/agent/action (HTTP).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

OnTickCallback = Callable[[dict], Awaitable[dict | None]]


class SSEClient:
    """
    SSE-based game client for The Null Epoch.

    Receives game state via Server-Sent Events and submits actions via
    HTTP POST.  Drop-in alternative to TNEClient for environments where
    WebSockets aren't available.

    Parameters
    ----------
    api_key : str
        Your agent API key (from registration).
    host : str
        Server host. Defaults to the live game server.
    secure : bool
        Use https:// (TLS). Defaults to True.
    reconnect : bool
        Auto-reconnect on connection drops. Defaults to True.
    reconnect_delay : float
        Initial backoff in seconds. Doubles on each failure, capped at 60s.
    """

    def __init__(
        self,
        api_key: str,
        host: str = "api.null.firespawn.ai",
        secure: bool = True,
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

        scheme = "https" if secure else "http"
        self._stream_url = f"{scheme}://{host}/v1/agent/stream"
        self._action_url = f"{scheme}://{host}/v1/agent/action"
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def run(self, on_tick: OnTickCallback) -> None:
        """
        Connect and run the agent loop.

        The `on_tick` callback is awaited with the full state dict for each
        state event from the SSE stream.  If the callback returns an action
        dict, it is POSTed to the action endpoint.

        Blocks until the connection is permanently closed or reconnect is
        exhausted.
        """
        delay = self.reconnect_delay

        while True:
            try:
                await self._stream_and_loop(on_tick)
                logger.info("SSE stream ended cleanly.")
                if not self.reconnect:
                    break
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code in (401, 403):
                    logger.error("Authentication failed (HTTP %d). Check your API key.", code)
                    break
                if not self.reconnect:
                    raise
                logger.warning("HTTP %d from SSE stream. Reconnecting in %.0fs...", code, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
            except (httpx.ConnectError, httpx.ReadError, OSError) as e:
                if not self.reconnect:
                    raise
                logger.warning("SSE connection error: %s. Reconnecting in %.0fs...", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
            else:
                delay = self.reconnect_delay

    async def _stream_and_loop(self, on_tick: OnTickCallback) -> None:
        logger.info("Connecting to SSE stream at %s...", self._stream_url)

        async with httpx.AsyncClient(timeout=None) as http:
            async with http.stream("GET", self._stream_url, headers=self._headers) as resp:
                resp.raise_for_status()
                logger.info("SSE stream connected.")

                event_type = ""
                data_buf = ""

                async for line in resp.aiter_lines():
                    # SSE protocol: "event: <type>", "data: <json>", blank line = dispatch
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_buf = line[5:].strip()
                    elif line == "" and data_buf:
                        # Dispatch the event
                        await self._handle_event(event_type, data_buf, on_tick, http)
                        event_type = ""
                        data_buf = ""

    async def _handle_event(
        self,
        event_type: str,
        data: str,
        on_tick: OnTickCallback,
        http: httpx.AsyncClient,
    ) -> None:
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("SSE: could not parse data: %s", data[:200])
            return

        if event_type == "state":
            tick = msg.get("tick_info", {}).get("current_tick", "?")
            logger.debug("SSE: received state for tick %s", tick)
            try:
                action = await on_tick(msg)
                if action:
                    await self._submit_action(http, action)
            except Exception:
                logger.exception("on_tick callback raised an unhandled exception.")

        elif event_type == "heartbeat":
            logger.debug("SSE: heartbeat received.")

        elif event_type == "waiting":
            logger.info("SSE: waiting for shard assignment: %s", msg.get("message"))

        elif event_type == "error":
            logger.warning("SSE: server error [%s]: %s", msg.get("code"), msg.get("message"))

        else:
            logger.debug("SSE: unknown event type: %s", event_type)

    async def _submit_action(self, http: httpx.AsyncClient, action: dict[str, Any]) -> None:
        try:
            resp = await http.post(
                self._action_url,
                json=action,
                headers=self._headers,
                timeout=10.0,
            )
            if resp.status_code == 202:
                logger.debug("Action submitted: %s", action.get("action"))
            else:
                logger.warning(
                    "Action submission returned HTTP %d: %s",
                    resp.status_code, resp.text[:200],
                )
        except Exception as exc:
            logger.error("Failed to submit action: %s", exc)
