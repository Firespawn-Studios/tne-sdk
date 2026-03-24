"""
TNE-SDK: LLM Providers

Supports:
- OpenAIProvider           : official OpenAI API
- AnthropicProvider        : Anthropic Claude API
- OpenAICompatibleProvider : any OpenAI-compatible endpoint via direct httpx
  (vLLM, LM Studio, llama.cpp, Ollama, DeepInfra, Together, Groq, etc.)
  Uses httpx directly so all body params (top_k, presence_penalty, etc.) are
  forwarded without the openai SDK stripping unknown fields.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

import anthropic as _anthropic_sdk
import httpx
import openai as _openai_sdk


@dataclass
class Message:
    """A single message in a chat conversation."""
    role: str     # "system" | "user" | "assistant"
    content: str


class LLMProvider(ABC):
    """Abstract base class for all LLM providers."""

    @abstractmethod
    def chat_completion(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        top_p: float = 0.9,
        thinking_mode: bool = False,
        **kwargs: Any,
    ) -> str:
        """Return the assistant response text."""
        ...


class OpenAIProvider(LLMProvider):
    """LLM Provider for the official OpenAI API."""

    def __init__(self, api_key: str | None = None, default_model: str = "gpt-4o") -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or ""
        if not self.api_key:
            raise ValueError(
                "OpenAI API key not provided or found in OPENAI_API_KEY environment variable."
            )
        self._client = _openai_sdk.OpenAI(api_key=self.api_key)
        self.default_model = default_model

    def chat_completion(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        top_p: float = 0.9,
        thinking_mode: bool = False,
        **kwargs: Any,
    ) -> str:
        model_to_use = model or self.default_model
        # Strip SDK-unknown params
        kwargs.pop("chat_template_kwargs", None)
        response = self._client.chat.completions.create(
            model=model_to_use,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            **kwargs,
        )
        return response.choices[0].message.content or ""


class AnthropicProvider(LLMProvider):
    """LLM Provider for the Anthropic Claude API."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "claude-sonnet-4-6",
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or ""
        if not self.api_key:
            raise ValueError(
                "Anthropic API key not provided or found in ANTHROPIC_API_KEY environment variable."
            )
        self._client = _anthropic_sdk.Anthropic(api_key=self.api_key)
        self.default_model = default_model

    def chat_completion(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        top_p: float = 0.9,
        thinking_mode: bool = False,
        **kwargs: Any,
    ) -> str:
        model_to_use = model or self.default_model

        # Anthropic uses a top-level system param; strip unsupported kwargs
        system_prompt = ""
        filtered: list[dict[str, str]] = []
        for m in messages:
            if m.role == "system":
                system_prompt = m.content
            else:
                filtered.append({"role": m.role, "content": m.content})

        # Anthropic does not support these; strip silently
        for unsupported in ("top_p", "presence_penalty", "chat_template_kwargs",
                            "top_k", "frequency_penalty"):
            kwargs.pop(unsupported, None)

        response = self._client.messages.create(
            model=model_to_use,
            system=system_prompt,
            messages=filtered,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )
        return response.content[0].text


class OpenAICompatibleProvider(LLMProvider):
    """
    LLM Provider for any OpenAI-compatible endpoint.

    Uses direct httpx POST so all body params (top_k, presence_penalty,
    chat_template_kwargs, etc.) are forwarded without stripping by the
    openai SDK.  Ideal for vLLM, LM Studio, llama.cpp, Ollama, DeepInfra,
    OpenRouter, Together, Groq, etc.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "not-needed",
        default_model: str = "local-model",
        timeout: float = 120.0,
    ) -> None:
        if not base_url:
            raise ValueError("base_url must be provided for OpenAICompatibleProvider.")
        # Normalise: strip trailing slash, ensure /chat/completions is appended later
        self._completions_url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self.default_model = default_model
        self._timeout = timeout
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def chat_completion(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        top_p: float = 0.9,
        thinking_mode: bool = False,
        **kwargs: Any,
    ) -> str:
        model_to_use = model or self.default_model

        body: dict[str, Any] = {
            "model":       model_to_use,
            "messages":    [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "top_p":       top_p,
        }

        # Forward all extra params directly (top_k, presence_penalty, etc.)
        body.update(kwargs)

        # Thinking toggle via chat_template_kwargs (Qwen3, Qwen3.5, vLLM, SGLang)
        body.setdefault("chat_template_kwargs", {})["enable_thinking"] = thinking_mode

        response = self._http.post(self._completions_url, json=body)
        response.raise_for_status()
        data = response.json()
        msg = data["choices"][0]["message"]

        # Log reasoning_content if the server separates it (--reasoning-format auto).
        # This doesn't affect the return value - just makes it visible in logs.
        reasoning = msg.get("reasoning_content")
        if reasoning:
            logger.debug(
                "Model reasoning (%d chars): %.200s...", len(reasoning), reasoning
            )

        return msg.get("content") or ""

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._http.close()


# ── Provider factory ──────────────────────────────────────────────────────── #

def provider_from_profile(profile: dict[str, Any], timeout: float = 120.0) -> LLMProvider:
    """
    Auto-select the right LLM provider from a profile dict.

    Selection rules (based on llm_url):
      - "bedrock://"    -> BedrockProvider    (AWS Bedrock Converse API)
      - "anthropic.com" -> AnthropicProvider  (native Anthropic API)
      - "openai.com"    -> OpenAIProvider      (official OpenAI SDK)
      - anything else   -> OpenAICompatibleProvider (httpx direct, all params forwarded)

    Bedrock profiles use a special URL format:
      ``llm_url: "bedrock://us-east-1"``  (region in the host portion)
    The model field is the Bedrock model ID (e.g. ``"anthropic.claude-sonnet-4-20250514-v1:0"``).

    ``llm_api_key`` is passed through to whichever provider is selected.
    For Anthropic/OpenAI it falls back to the standard env vars
    (ANTHROPIC_API_KEY / OPENAI_API_KEY) if the profile key is empty.
    For Bedrock, authentication uses the boto3 credential chain (env vars,
    IAM role, ~/.aws/credentials).
    """
    url   = (profile.get("llm_url") or "").lower()
    key   = profile.get("llm_api_key") or ""
    model = profile.get("model", "local-model")

    if url.startswith("bedrock://"):
        from .bedrock import BedrockProvider
        # Extract region from URL: "bedrock://us-east-1" -> "us-east-1"
        region = url.replace("bedrock://", "").strip("/") or "us-east-1"
        return BedrockProvider(
            model_id          = model,
            region            = region,
            default_max_tokens = int(profile.get("max_tokens", 1024)),
        )
    if "anthropic.com" in url:
        return AnthropicProvider(
            api_key       = key or None,   # falls back to ANTHROPIC_API_KEY env var
            default_model = model,
        )
    if "openai.com" in url:
        return OpenAIProvider(
            api_key       = key or None,   # falls back to OPENAI_API_KEY env var
            default_model = model,
        )
    return OpenAICompatibleProvider(
        base_url      = profile.get("llm_url", ""),
        api_key       = key or "not-needed",
        default_model = model,
        timeout       = timeout,
    )
