"""TNEClient reconnect rules, against a real local WebSocket server.

Only 4001 (invalid key) and 4003 (dormant) stop the client. Every other close,
clean or not, is what a deploy or network blip looks like, so it reconnects.
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    from websockets.asyncio.server import serve
except ImportError:  # websockets < 13
    from websockets import serve

import tne_sdk.client as client_mod
from tne_sdk.client import TNEClient

BASE_DELAY = 0.5

# Each connection plays the next step in the script:
#   ("close", code)    send auth_ok, then close with code
#   ("reject", code)   close with code instead of auth_ok (how the server refuses a key)
#   ("drop", None)     send auth_ok, then cut the TCP connection (no close frame)
#   ("frames", [...])  send auth_ok, then each raw frame, then close 1000


def _run(script, *, reconnect=True, on_tick=None, monkeypatch):
    connections = []
    delays = []
    ticks = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(
        client_mod, "asyncio",
        SimpleNamespace(sleep=fake_sleep, CancelledError=asyncio.CancelledError),
    )

    async def handler(ws):
        step = script[len(connections)] if len(connections) < len(script) else ("reject", 4001)
        connections.append(step)
        kind, arg = step
        await ws.recv()  # the auth message
        if kind == "reject":
            await ws.close(arg, "refused")
            return
        await ws.send(json.dumps({"type": "auth_ok"}))
        if kind == "close":
            await ws.close(arg, "test close")
        elif kind == "drop":
            ws.transport.abort()
        elif kind == "frames":
            for frame in arg:
                await ws.send(frame)
            await ws.close(1000)

    async def record_tick(state):
        ticks.append(state)
        return None

    async def main():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = next(iter(server.sockets)).getsockname()[1]
            client = TNEClient(
                api_key="k", host=f"127.0.0.1:{port}", secure=False,
                reconnect=reconnect, reconnect_delay=BASE_DELAY,
            )
            await asyncio.wait_for(client.run(on_tick or record_tick), timeout=10)

    asyncio.run(main())
    return connections, delays, ticks


@pytest.mark.parametrize("code", [1000, 1001, 1011, 1012])
def test_server_close_reconnects(code, monkeypatch):
    connections, _delays, _ticks = _run([("close", code)], monkeypatch=monkeypatch)
    # Reconnected once, then the default ("reject", 4001) ended the run.
    assert len(connections) == 2


def test_dropped_connection_reconnects(monkeypatch):
    connections, _delays, _ticks = _run([("drop", None)], monkeypatch=monkeypatch)
    assert len(connections) == 2


@pytest.mark.parametrize("code", [4001, 4003])
def test_invalid_key_and_dormant_stop_for_good(code, monkeypatch):
    connections, delays, _ticks = _run(
        [("reject", code), ("close", 1000)], monkeypatch=monkeypatch,
    )
    assert len(connections) == 1
    assert delays == []


def test_reconnect_disabled_stops_on_any_close(monkeypatch):
    connections, _delays, _ticks = _run(
        [("close", 1001)], reconnect=False, monkeypatch=monkeypatch,
    )
    assert len(connections) == 1


def test_delay_resets_after_a_session_that_logged_in(monkeypatch):
    # Two failures before login grow the delay; a session that logged in resets it.
    _connections, delays, _ticks = _run(
        [("reject", 1012), ("reject", 1012), ("close", 1001)], monkeypatch=monkeypatch,
    )
    assert delays == [BASE_DELAY, BASE_DELAY * 2, BASE_DELAY]


def test_bad_frames_are_skipped(monkeypatch):
    state = json.dumps({"type": "state", "tick_info": {"current_tick": 7}})
    _connections, _delays, ticks = _run(
        [("frames", ["not json", "[1, 2]", state])], monkeypatch=monkeypatch,
    )
    assert [t["tick_info"]["current_tick"] for t in ticks] == [7]
