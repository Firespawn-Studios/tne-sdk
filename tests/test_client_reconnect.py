"""TNEClient reconnect rules, against a real local WebSocket server.

Only 4001 (invalid key) and 4003 (dormant) stop the client. Every other close,
clean or not, is what a deploy or network blip looks like, so it reconnects.
"""
import asyncio
import json
import sys
from pathlib import Path

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
#   ("custom", fn)     send auth_ok, then await fn(ws)


def _run(script, *, reconnect=True, on_tick=None, monkeypatch):
    connections = []
    delays = []
    ticks = []

    async def fake_backoff(self, seconds):
        delays.append(seconds)

    monkeypatch.setattr(TNEClient, "_backoff", fake_backoff)

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
        elif kind == "custom":
            await arg(ws)

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


# ── Stall watchdog ────────────────────────────────────────────────────────── #

from websockets.exceptions import ConnectionClosed

# With these settings the watchdog limit is 3 x 0.1 s = 0.3 s.
FAST_TICK = 0.1


def _state(tick):
    return json.dumps({"type": "state",
                       "tick_info": {"current_tick": tick, "tick_interval_seconds": FAST_TICK}})


@pytest.fixture
def fast_watchdog(monkeypatch):
    # The first sleep uses the default interval, before any state arrives.
    monkeypatch.setattr(client_mod, "_DEFAULT_TICK_INTERVAL", FAST_TICK)


def _closed_by_client(record):
    async def wait_for_close(ws):
        try:
            await ws.recv()
            await ws.recv()  # nothing more is expected from the client
        except ConnectionClosed as exc:
            record.append(exc.rcvd.code if exc.rcvd else None)
    return wait_for_close


def test_silent_connection_is_closed_and_reconnected(fast_watchdog, monkeypatch):
    seen_by_server = []

    async def go_silent(ws):
        await ws.send(_state(1))
        await _closed_by_client(seen_by_server)(ws)

    connections, _delays, _ticks = _run([("custom", go_silent)], monkeypatch=monkeypatch)

    assert seen_by_server == [4000]
    assert len(connections) == 2


def test_regular_states_never_trigger_the_watchdog(fast_watchdog, monkeypatch):
    async def steady(ws):
        for tick in range(20):  # one state every 0.05 s for 1 s, limit 0.3 s
            await ws.send(_state(tick))
            await asyncio.sleep(0.05)
        await ws.close(4001)

    async def no_action(state):
        return None

    connections, _delays, _ticks = _run(
        [("custom", steady)], on_tick=no_action, monkeypatch=monkeypatch,
    )
    assert len(connections) == 1


def test_time_inside_a_long_turn_does_not_count(fast_watchdog, monkeypatch):
    seen_by_server = []

    async def slow_agent(ws):
        await ws.send(_state(1))
        try:
            await ws.recv()  # the action, sent once the 1 s turn ends
        except ConnectionClosed as exc:
            seen_by_server.append(exc.rcvd.code if exc.rcvd else None)
            return
        await ws.close(4001)

    async def one_second_turn(state):
        await asyncio.sleep(1.0)  # over 3x the 0.3 s limit
        return {"action": "wait", "parameters": {}}

    connections, _delays, _ticks = _run(
        [("custom", slow_agent)], on_tick=one_second_turn, monkeypatch=monkeypatch,
    )
    assert seen_by_server == []
    assert len(connections) == 1


def test_default_limit_is_three_tick_intervals():
    assert client_mod._WATCHDOG_TICKS == 3
    assert TNEClient(api_key="k")._tick_interval == 60.0
