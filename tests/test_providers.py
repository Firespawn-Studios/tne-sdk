"""Tests for provider_from_profile() routing logic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tne_sdk.llm.providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    provider_from_profile,
)


class TestProviderFromProfile:
    def test_anthropic_url_returns_anthropic_provider(self):
        profile = {
            "llm_url": "https://api.anthropic.com/v1",
            "llm_api_key": "sk-ant-test",
            "model": "claude-sonnet-4-20250514",
        }
        p = provider_from_profile(profile)
        assert isinstance(p, AnthropicProvider)

    def test_openai_url_returns_openai_provider(self):
        profile = {
            "llm_url": "https://api.openai.com/v1",
            "llm_api_key": "sk-test",
            "model": "gpt-4o",
        }
        p = provider_from_profile(profile)
        assert isinstance(p, OpenAIProvider)

    def test_local_url_returns_compatible_provider(self):
        profile = {
            "llm_url": "http://localhost:11434/v1",
            "model": "qwen3:14b",
        }
        p = provider_from_profile(profile)
        assert isinstance(p, OpenAICompatibleProvider)

    def test_deepinfra_returns_compatible_provider(self):
        profile = {
            "llm_url": "https://api.deepinfra.com/v1/openai",
            "llm_api_key": "di-test",
            "model": "meta-llama/Llama-3.1-70B",
        }
        p = provider_from_profile(profile)
        assert isinstance(p, OpenAICompatibleProvider)

    def test_groq_returns_compatible_provider(self):
        profile = {
            "llm_url": "https://api.groq.com/openai/v1",
            "llm_api_key": "gsk-test",
            "model": "llama-3.1-70b",
        }
        p = provider_from_profile(profile)
        assert isinstance(p, OpenAICompatibleProvider)

    def test_empty_key_falls_through(self):
        profile = {
            "llm_url": "http://localhost:8000/v1",
            "llm_api_key": "",
            "model": "local-model",
        }
        p = provider_from_profile(profile)
        assert isinstance(p, OpenAICompatibleProvider)

    def test_timeout_forwarded(self):
        profile = {
            "llm_url": "http://localhost:8000/v1",
            "model": "local-model",
        }
        p = provider_from_profile(profile, timeout=300.0)
        assert isinstance(p, OpenAICompatibleProvider)
        assert p._timeout == 300.0

    def test_case_insensitive_url_matching(self):
        profile = {
            "llm_url": "https://API.ANTHROPIC.COM/v1",
            "llm_api_key": "sk-ant-test",
            "model": "claude-sonnet-4-20250514",
        }
        p = provider_from_profile(profile)
        assert isinstance(p, AnthropicProvider)


# ── OpenAIProvider request building ──────────────────────────────────────── #

import json
import logging

import httpx
import openai

from tne_sdk.llm.providers import Message


def _openai_provider(sent: list) -> OpenAIProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 0, "model": "gpt-4o",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": '{"action": "wait"}'}}],
        })

    provider = OpenAIProvider(api_key="sk-test")
    provider._client = openai.OpenAI(
        api_key="sk-test", http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return provider


class TestOpenAIProviderRequest:
    def test_agent_kwargs_reach_the_api(self):
        # The agent always passes top_k and presence_penalty (agent.py).
        sent: list = []
        provider = _openai_provider(sent)

        text = provider.chat_completion(
            [Message("system", "s"), Message("user", "u")],
            top_k=20, presence_penalty=1.5, chat_template_kwargs={"enable_thinking": False},
        )

        assert text == '{"action": "wait"}'
        assert len(sent) == 1
        body = sent[0]
        assert body["presence_penalty"] == 1.5
        assert "top_k" not in body
        assert "chat_template_kwargs" not in body

    def test_each_dropped_param_is_warned_once(self, caplog):
        provider = _openai_provider([])
        with caplog.at_level(logging.WARNING, logger="tne_sdk.llm.providers"):
            for _ in range(3):
                provider.chat_completion([Message("user", "u")], top_k=20)
        warnings = [r for r in caplog.records if "top_k" in r.getMessage()]
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING

    def test_signature_is_read_once_per_provider(self, monkeypatch):
        import tne_sdk.llm.providers as providers_mod

        provider = _openai_provider([])
        calls = []
        real = providers_mod.inspect.signature

        def counting(obj):
            calls.append(obj)
            return real(obj)

        monkeypatch.setattr(providers_mod.inspect, "signature", counting)
        for _ in range(3):
            provider.chat_completion([Message("user", "u")], top_k=20, presence_penalty=1.5)
        assert len(calls) == 1

    def test_unreadable_signature_falls_back_to_known_list(self, monkeypatch):
        provider = _openai_provider([])

        def no_signature(_obj):
            raise ValueError("no signature")

        monkeypatch.setattr("tne_sdk.llm.providers.inspect.signature", no_signature)
        kept = provider._supported_kwargs({"top_k": 20, "presence_penalty": 1.5, "seed": 7})
        assert kept == {"presence_penalty": 1.5, "seed": 7}
