"""
TNE-SDK: AWS Bedrock LLM Provider

Uses the Bedrock Converse API - a unified interface that works across all
Bedrock-hosted models (Anthropic, Meta, Mistral, Cohere, Amazon Nova,
Google Gemma, Qwen, Nvidia, etc.) without per-model request/response
schema handling.

Requires ``boto3``:  pip install "tne-sdk[bedrock]"

Authentication uses the standard boto3 credential chain:
  1. Explicit credentials via constructor (access_key / secret_key)
  2. Environment variables (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
  3. Shared credentials file (~/.aws/credentials)
  4. IAM role (EC2 instance profile, ECS task role, Lambda execution role)

For most server deployments, IAM roles are recommended - no keys to manage.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .providers import LLMProvider, Message

logger = logging.getLogger(__name__)


class BedrockProvider(LLMProvider):
    """
    LLM Provider for AWS Bedrock via the Converse API.

    Parameters
    ----------
    model_id : str
        Bedrock model identifier, e.g. ``"anthropic.claude-sonnet-4-20250514-v1:0"``,
        ``"mistral.ministral-3-8b-instruct"``, ``"qwen.qwen3-32b-v1:0"``.
    region : str
        AWS region for the Bedrock endpoint (default: ``"us-east-1"``).
    access_key : str | None
        Explicit AWS access key. If None, uses the default boto3 credential chain.
    secret_key : str | None
        Explicit AWS secret key. If None, uses the default boto3 credential chain.
    session_token : str | None
        Optional session token for temporary credentials (STS).
    default_max_tokens : int
        Default max tokens if not overridden per call.
    """

    def __init__(
        self,
        model_id: str,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        session_token: str | None = None,
        default_max_tokens: int = 1024,
    ) -> None:
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "boto3 is required for BedrockProvider.\n"
                "  Install with: pip install \"tne-sdk[bedrock]\"\n"
                "  Or directly:  pip install boto3"
            )

        if not model_id:
            raise ValueError("model_id is required for BedrockProvider.")

        self.model_id = model_id
        self.default_max_tokens = default_max_tokens

        # Build client kwargs - only include credentials if explicitly provided.
        # Otherwise boto3 uses its standard credential chain (env vars, IAM role, etc.)
        client_kwargs: dict[str, Any] = {"region_name": region}
        if access_key and secret_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key
            if session_token:
                client_kwargs["aws_session_token"] = session_token

        self._client = boto3.client("bedrock-runtime", **client_kwargs)

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
        model_to_use = model or self.model_id

        # Separate system messages from conversation messages.
        # Converse API takes system prompts as a top-level parameter.
        system_blocks: list[dict[str, str]] = []
        conversation: list[dict[str, Any]] = []

        for m in messages:
            if m.role == "system":
                system_blocks.append({"text": m.content})
            else:
                conversation.append({
                    "role": m.role,
                    "content": [{"text": m.content}],
                })

        # Converse requires at least one user message
        if not conversation:
            logger.warning("BedrockProvider: no user/assistant messages provided.")
            return ""

        # Build inference config
        inference_config: dict[str, Any] = {
            "maxTokens": max_tokens or self.default_max_tokens,
            "temperature": temperature,
            "topP": top_p,
        }

        # Build the Converse call
        converse_kwargs: dict[str, Any] = {
            "modelId": model_to_use,
            "messages": conversation,
            "inferenceConfig": inference_config,
        }
        if system_blocks:
            converse_kwargs["system"] = system_blocks

        # Strip SDK-specific kwargs that Converse doesn't understand
        # (these may be passed through from AgentConfig)
        kwargs.pop("chat_template_kwargs", None)
        kwargs.pop("presence_penalty", None)
        kwargs.pop("top_k", None)
        kwargs.pop("frequency_penalty", None)

        response = self._client.converse(**converse_kwargs)

        # Extract text from response content blocks
        content_blocks = response["output"]["message"]["content"]
        text = next((b["text"] for b in content_blocks if "text" in b), None)

        if text is not None:
            return text

        # Fallback: some models with extended thinking may return only a
        # reasoningContent block when max_tokens is too low to fit both
        # reasoning and the final answer. Try to extract from reasoning.
        reasoning_text = next(
            (
                b["reasoningContent"]["reasoningText"]["text"]
                for b in content_blocks
                if "reasoningContent" in b
                and "reasoningText" in b.get("reasoningContent", {})
            ),
            None,
        )
        if reasoning_text:
            logger.warning(
                "Bedrock returned reasoning-only response for %s - "
                "max_tokens may be too low for thinking + output.",
                model_to_use,
            )
            return reasoning_text

        logger.warning(
            "No text content in Bedrock response for %s: %s",
            model_to_use,
            content_blocks,
        )
        return ""
