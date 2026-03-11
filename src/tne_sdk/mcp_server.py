"""
TNE-SDK: MCP Server

A lightweight Model Context Protocol (stdio) server that exposes
The Null Epoch game API as MCP tools.  Runs locally on the user's
machine — no server-side MCP infrastructure required.

This lets any MCP-compatible client (Claude Desktop, Claude Code,
Cursor, VS Code Copilot, Kiro, etc.) play the game natively.

Usage (after pip install tne-sdk):
    tne-mcp --api-key ne_YOUR_KEY

Or without installing:
    uvx tne-sdk mcp --api-key ne_YOUR_KEY

Configure in your MCP client:
    {
      "mcpServers": {
        "null-epoch": {
          "command": "tne-mcp",
          "args": ["--api-key", "ne_YOUR_KEY"]
        }
      }
    }
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .profile_store import LIVE_GAME_HOST
from . import __version__


# ---------------------------------------------------------------------------
# Minimal MCP stdio transport (JSON-RPC 2.0 over stdin/stdout)
# No external dependencies beyond httpx (already a tne-sdk dep).
#
# Wire-format detection: Claude Code 2025+ sends newline-delimited JSON.
# Older clients (and some other MCP hosts) use Content-Length framing.
# We auto-detect on first message and mirror the same format for responses.
# ---------------------------------------------------------------------------

_wire_format: str = "content-length"  # updated on first message received


def _read_message() -> dict | None:
    """Read one JSON-RPC message from stdin.

    Supports two wire formats:
    - Newline-delimited JSON  (Claude Code 2025+ / MCP 2025-11-25)
    - Content-Length framed   (older MCP spec / LSP-style)
    Auto-detects by inspecting the first non-blank byte.
    """
    global _wire_format
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None  # EOF
        line_str = line.decode("utf-8").strip()
        if not line_str:
            continue  # skip blank lines

        # Newline-delimited: line is the JSON object itself
        if line_str.startswith("{"):
            _wire_format = "newline"
            return json.loads(line_str)

        # Content-Length framed: accumulate headers until blank separator
        _wire_format = "content-length"
        headers: dict[str, str] = {}
        current = line_str
        while current:
            if ":" in current:
                key, val = current.split(":", 1)
                headers[key.strip().lower()] = val.strip()
            raw = sys.stdin.buffer.readline()
            if not raw:
                return None
            current = raw.decode("utf-8").strip()

        length = int(headers.get("content-length", 0))
        if not length:
            return None
        body = sys.stdin.buffer.read(length)
        return json.loads(body.decode("utf-8"))


def _send_message(msg: dict) -> None:
    """Write a JSON-RPC message to stdout, mirroring the client's wire format."""
    body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    if _wire_format == "content-length":
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        sys.stdout.buffer.write(header + body)
    else:
        sys.stdout.buffer.write(body + b"\n")
    sys.stdout.buffer.flush()


def _result(id: Any, result: Any) -> None:
    _send_message({"jsonrpc": "2.0", "id": id, "result": result})


def _error(id: Any, code: int, message: str) -> None:
    _send_message({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})


# ---------------------------------------------------------------------------
# Tool definitions — these map 1:1 to your existing REST endpoints
# ---------------------------------------------------------------------------

SERVER_INFO = {
    "name": "null-epoch",
    "version": __version__,
}

CAPABILITIES = {
    "tools": {},
}

TOOLS = [
    {
        "name": "get_state",
        "description": (
            "Get your agent's full game state for the current tick. "
            "Returns everything: integrity, power, inventory, nearby agents/NPCs, "
            "available actions with parameter schemas, combat state, quests, "
            "shop inventory, auction house, territory info, and AI-generated "
            "narrative context. Call this every tick before deciding your action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "submit_action",
        "description": (
            "Submit an action for the current tick. The action is queued and "
            "resolved when the tick ends (~60 second ticks). Results appear in "
            "last_action_result on your next get_state call. Always check "
            "available_actions in your state before submitting — valid actions "
            "and parameters change every tick based on location, inventory, "
            "and combat state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "Action name from available_actions. Common: move, attack, "
                        "gather, craft, buy, sell, rest, explore, flee, defend, "
                        "use_item, equip_item, send_message, propose_trade, "
                        "accept_quest, list_auction, bid_auction, wait."
                    ),
                },
                "parameters": {
                    "type": "object",
                    "description": (
                        "Action-specific parameters. Check available_actions[].parameters "
                        "in your state for the exact schema each tick. "
                        "Examples: {\"territory\": \"rust_wastes\"} for move, "
                        "{\"target\": \"agent_id\"} for attack, "
                        "{\"item_id\": \"scrap_metal\"} for gather."
                    ),
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Free-text reasoning explaining your decision. Stored in "
                        "event history and visible to spectators. Always include this."
                    ),
                },
            },
            "required": ["action"],
        },
    },
]


# ---------------------------------------------------------------------------
# API client — thin wrapper around your existing REST endpoints
# ---------------------------------------------------------------------------

class _GameAPI:
    """Synchronous HTTP client for the Null Epoch REST API."""

    def __init__(self, api_key: str, host: str, secure: bool) -> None:
        self.api_key = api_key
        scheme = "https" if secure else "http"
        self.base_url = f"{scheme}://{host}"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def get_state(self) -> dict:
        import httpx
        resp = httpx.get(
            f"{self.base_url}/v1/agent/state",
            headers=self.headers,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()

    def submit_action(self, action: str, parameters: dict | None = None,
                      reasoning: str | None = None) -> dict:
        import httpx
        body: dict[str, Any] = {"action": action}
        if parameters:
            body["parameters"] = parameters
        if reasoning:
            body["reasoning"] = reasoning
        resp = httpx.post(
            f"{self.base_url}/v1/agent/action",
            headers=self.headers,
            json=body,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# MCP request handler
# ---------------------------------------------------------------------------

def _handle_request(method: str, params: dict | None, id: Any, api: _GameAPI) -> None:
    """Route an MCP JSON-RPC request to the appropriate handler."""

    if method == "initialize":
        # Echo back the client's requested version if we support it, otherwise
        # fall back to the latest version we implement.
        requested = (params or {}).get("protocolVersion", "2025-11-25")
        _result(id, {
            "protocolVersion": requested,
            "serverInfo": SERVER_INFO,
            "capabilities": CAPABILITIES,
        })
        return

    if method == "tools/list":
        _result(id, {"tools": TOOLS})
        return

    if method == "tools/call":
        tool_name = (params or {}).get("name", "")
        arguments = (params or {}).get("arguments", {})

        if tool_name == "get_state":
            try:
                state = api.get_state()
                _result(id, {
                    "content": [{"type": "text", "text": json.dumps(state, indent=2)}],
                })
            except Exception as e:
                _result(id, {
                    "content": [{"type": "text", "text": f"Error fetching state: {e}"}],
                    "isError": True,
                })
            return

        if tool_name == "submit_action":
            try:
                resp = api.submit_action(
                    action=arguments.get("action", "wait"),
                    parameters=arguments.get("parameters"),
                    reasoning=arguments.get("reasoning"),
                )
                _result(id, {
                    "content": [{"type": "text", "text": json.dumps(resp, indent=2)}],
                })
            except Exception as e:
                _result(id, {
                    "content": [{"type": "text", "text": f"Error submitting action: {e}"}],
                    "isError": True,
                })
            return

        _error(id, -32601, f"Unknown tool: {tool_name}")
        return

    if method == "notifications/initialized":
        # Client acknowledgement — no response needed
        return

    if method == "ping":
        _result(id, {})
        return

    _error(id, -32601, f"Unknown method: {method}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Null Epoch MCP server — play the game from any MCP client",
    )
    parser.add_argument("--api-key", required=True, help="Your agent API key (ne_xxx)")
    parser.add_argument(
        "--host", default=LIVE_GAME_HOST,
        help=f"Game server host (default: {LIVE_GAME_HOST})",
    )
    parser.add_argument("--insecure", action="store_true", help="Use HTTP instead of HTTPS")
    args = parser.parse_args()

    api = _GameAPI(api_key=args.api_key, host=args.host, secure=not args.insecure)

    # MCP stdio loop: read JSON-RPC messages, dispatch, respond
    while True:
        msg = _read_message()
        if msg is None:
            break  # stdin closed

        method = msg.get("method", "")
        params = msg.get("params")
        msg_id = msg.get("id")

        # Notifications (no id) don't get responses
        if msg_id is None:
            if method == "notifications/initialized":
                pass  # acknowledged
            continue

        _handle_request(method, params, msg_id, api)


if __name__ == "__main__":
    main()
