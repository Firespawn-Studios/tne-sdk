"""tne-relay end to end: a real local WebSocket server plays the game and a
small task plays the coding assistant, reading state.json and writing
action.json."""
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

import tne_sdk.relay as relay_mod
from tne_sdk.client import TNEClient
from tne_sdk.relay import FileRelayClient

STATE = {"type": "state", "tick_info": {"current_tick": 7, "tick_interval_seconds": 60},
         "current_territory": "rust_wastes", "integrity": 80, "max_integrity": 100}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    async def no_backoff(self, seconds):
        return None

    monkeypatch.setattr(TNEClient, "_backoff", no_backoff)
    monkeypatch.setattr(relay_mod, "POLL_INTERVAL", 0.02)


def _run(tmp_path, handler, assistant=None, action_timeout=5.0):
    connections = []

    async def counted(ws):
        connections.append(ws)
        await ws.recv()  # the auth message
        await ws.send(json.dumps({"type": "auth_ok"}))
        await handler(ws, len(connections))

    async def main():
        async with serve(counted, "127.0.0.1", 0) as server:
            port = next(iter(server.sockets)).getsockname()[1]
            relay = FileRelayClient(
                api_key="k", host=f"127.0.0.1:{port}", secure=False,
                relay_dir=tmp_path, action_timeout=action_timeout,
            )
            helper = asyncio.create_task(assistant(tmp_path)) if assistant else None
            try:
                await asyncio.wait_for(relay.run(), timeout=10)
            finally:
                if helper:
                    helper.cancel()

    asyncio.run(main())
    return connections


def _writes_action(action, after_state=True):
    async def assistant(relay_dir):
        while after_state and not (relay_dir / "state.json").exists():
            await asyncio.sleep(0.01)
        (relay_dir / "action.json").write_text(json.dumps(action) if not isinstance(action, str) else action)
    return assistant


def _read(relay_dir, name):
    return json.loads((relay_dir / name).read_text())


def test_state_out_action_in_and_queued_result(tmp_path):
    received = []

    async def game(ws, n):
        await ws.send(json.dumps(STATE))
        received.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"type": "action_queued", "action": "move", "tick": 7}))
        await ws.close(4001)

    action = {"action": "move", "parameters": {"territory": "data_spire"}, "reasoning": "go"}
    _run(tmp_path, game, assistant=_writes_action(action))

    assert _read(tmp_path, "state.json")["tick_info"]["current_tick"] == 7
    assert (tmp_path / "tick.txt").read_text() == "7"
    assert received == [action]
    assert _read(tmp_path, "result.json") == {"status": "queued", "action": "move", "for_tick": 8}
    assert not (tmp_path / "action.json").exists()


def test_server_error_is_written_to_result(tmp_path):
    async def game(ws, n):
        await ws.send(json.dumps(STATE))
        await ws.recv()
        await ws.send(json.dumps({"type": "error", "code": "invalid_action",
                                  "message": "Unknown action 'decline_alliance'."}))
        await ws.close(4001)

    _run(tmp_path, game, assistant=_writes_action({"action": "decline_alliance"}))

    assert _read(tmp_path, "result.json") == {
        "status": "error", "code": "invalid_action",
        "message": "Unknown action 'decline_alliance'.",
    }


def test_no_action_file_sends_wait_after_the_timeout(tmp_path):
    received = []

    async def game(ws, n):
        await ws.send(json.dumps(STATE))
        received.append(json.loads(await ws.recv()))
        await ws.close(4001)

    _run(tmp_path, game, action_timeout=0.2)

    assert received == [{"action": "wait", "reasoning": "Timeout, no action submitted."}]


def test_bad_action_file_is_reported_and_a_fixed_one_is_still_taken(tmp_path):
    received = []
    reported = []

    async def game(ws, n):
        await ws.send(json.dumps(STATE))
        received.append(json.loads(await ws.recv()))
        await ws.close(4001)

    async def assistant(relay_dir):
        while not (relay_dir / "state.json").exists():
            await asyncio.sleep(0.01)
        (relay_dir / "action.json").write_text("[1, 2]")
        while not (relay_dir / "result.json").exists():
            await asyncio.sleep(0.01)
        reported.append(_read(relay_dir, "result.json"))
        (relay_dir / "action.json").write_text(json.dumps({"action": "rest"}))

    _run(tmp_path, game, assistant=assistant)

    assert reported[0]["status"] == "error"
    assert reported[0]["code"] == "invalid_action_file"
    assert received == [{"action": "rest"}]


def test_relay_reconnects_after_a_clean_close(tmp_path):
    async def game(ws, n):
        await ws.close(1001 if n == 1 else 4001)

    assert len(_run(tmp_path, game)) == 2
