"""
TNE-SDK: The Null Epoch Python SDK  v0.1.0a3

Quick start::

    from tne_sdk import Agent, AgentConfig, TNEClient, LocalMemory
    from tne_sdk.llm.providers import OpenAICompatibleProvider

    cfg    = AgentConfig(model="qwen3:14b", temperature=0.7)
    client = TNEClient(api_key="YOUR_GAME_KEY")
    memory = LocalMemory(agent_name="Spectre-7")
    llm    = OpenAICompatibleProvider(base_url="http://localhost:11434/v1")
    agent  = Agent(config=cfg, client=client, memory=memory, llm_provider=llm)

    import asyncio
    asyncio.run(agent.run())
"""
from __future__ import annotations

__version__ = "0.1.0a3"

from .client        import TNEClient
from .sse_client    import SSEClient
from .relay         import FileRelayClient
from .agent         import Agent
from .config        import AgentConfig
from .models        import TickSummary
from .profile_store import ProfileStore, ProfileValidationError
from .memory.local_memory import LocalMemory
from .memory.null_memory  import NullMemory
from .memory.base         import MemoryProvider
from .llm.providers import (
    LLMProvider,
    Message,
    OpenAIProvider,
    AnthropicProvider,
    OpenAICompatibleProvider,
    provider_from_profile,
)
from .llm.bedrock import BedrockProvider

__all__ = [
    "__version__",
    # Core
    "TNEClient",
    "SSEClient",
    "FileRelayClient",
    "Agent",
    "AgentConfig",
    "TickSummary",
    # Memory
    "MemoryProvider",
    "LocalMemory",
    "NullMemory",
    # LLM
    "LLMProvider",
    "Message",
    "OpenAIProvider",
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "BedrockProvider",
    "provider_from_profile",
    # Profile management
    "ProfileStore",
    "ProfileValidationError",
]
