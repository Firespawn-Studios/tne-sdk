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
