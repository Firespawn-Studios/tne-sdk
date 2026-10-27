"""tne-mcp passes the server's reason for a failed request to the assistant."""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import tne_sdk.mcp_server as mcp

_URL = "http://test/v1/agent/action"


def _response(status, *, json=None, text=None):
    request = httpx.Request("POST", _URL)
    if json is not None:
        return httpx.Response(status, json=json, request=request)
    return httpx.Response(status, text=text or "", request=request)


def _api_returning(monkeypatch, response):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response)
    return mcp._GameAPI(api_key="k", host="test", secure=False)


@pytest.mark.parametrize("response, expected", [
    (_response(400, json={"detail": "Invalid action 'decline_alliance'. Valid actions: ['wait']"}),
     "HTTP 400: Invalid action 'decline_alliance'. Valid actions: ['wait']"),
    (_response(422, json={"detail": [{"loc": ["body", "action"], "msg": "Input should be a valid string"}]}),
     "HTTP 422: body.action: Input should be a valid string"),
    (_response(429, json={"error": "Rate limit exceeded: 60 per 1 minute"}),
     "HTTP 429: Rate limit exceeded: 60 per 1 minute"),
    (_response(502, text="Bad Gateway"),
     "HTTP 502: Bad Gateway"),
])
def test_error_message_carries_the_server_reason(monkeypatch, response, expected):
    api = _api_returning(monkeypatch, response)
    with pytest.raises(RuntimeError) as info:
        api.submit_action("decline_alliance")
    assert str(info.value) == expected


def test_long_messages_are_capped(monkeypatch):
    api = _api_returning(monkeypatch, _response(400, json={"detail": "x" * 10_000}))
    with pytest.raises(RuntimeError) as info:
        api.get_state()
    assert len(str(info.value)) <= len("HTTP 400: ") + mcp._MAX_ERROR_CHARS


def test_success_is_returned_unchanged(monkeypatch):
    api = _api_returning(monkeypatch, _response(202, json={"status": "queued"}))
    assert api.submit_action("wait") == {"status": "queued"}


def test_assistant_sees_the_reason_in_the_tool_result(monkeypatch):
    sent = []
    monkeypatch.setattr(mcp, "_send_message", sent.append)
    api = _api_returning(monkeypatch, _response(
        400, json={"detail": "Invalid action 'decline_alliance'. There is no action for declining."},
    ))

    mcp._handle_request(
        "tools/call", {"name": "submit_action", "arguments": {"action": "decline_alliance"}}, 1, api,
    )

    result = sent[0]["result"]
    assert result["isError"] is True
    assert "There is no action for declining." in result["content"][0]["text"]
