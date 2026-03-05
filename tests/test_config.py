"""Tests for AgentConfig.from_dict(): ensures profile dicts map correctly."""
import sys
from pathlib import Path

# Add src to path so we can import tne_sdk.config directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tne_sdk.config import AgentConfig


class TestFromDict:
    def test_defaults(self):
        cfg = AgentConfig.from_dict({})
        assert cfg.temperature == 0.7
        assert cfg.top_p == 0.8
        assert cfg.top_k == 20
        assert cfg.model == "local-model"
        assert cfg.enable_thinking is False
        assert cfg.log_payloads is False
        assert cfg.reflection_cooldown_ticks == 200
        assert cfg.tactical_review_cooldown_ticks == 10
        assert cfg.default_llm_kwargs == {}

    def test_overrides(self):
        cfg = AgentConfig.from_dict({
            "temperature": 0.9,
            "top_p": 0.95,
            "top_k": 40,
            "model": "qwen3:14b",
            "enable_thinking": True,
            "log_payloads": True,
            "llm_timeout": 300,
            "reflection_cooldown_ticks": 100,
            "tactical_review_cooldown_ticks": 10,
        })
        assert cfg.temperature == 0.9
        assert cfg.top_p == 0.95
        assert cfg.top_k == 40
        assert cfg.model == "qwen3:14b"
        assert cfg.enable_thinking is True
        assert cfg.log_payloads is True
        assert cfg.llm_timeout == 300.0
        assert cfg.reflection_cooldown_ticks == 100
        assert cfg.tactical_review_cooldown_ticks == 10

    def test_default_llm_kwargs(self):
        cfg = AgentConfig.from_dict({
            "default_llm_kwargs": {"repetition_penalty": 1.1, "top_k": 50},
        })
        assert cfg.default_llm_kwargs == {"repetition_penalty": 1.1, "top_k": 50}

    def test_log_dir(self):
        cfg = AgentConfig.from_dict({"log_dir": "/tmp/my_logs"})
        assert cfg.log_dir == Path("/tmp/my_logs")

    def test_none_values_keep_defaults(self):
        cfg = AgentConfig.from_dict({"temperature": None, "model": None})
        assert cfg.temperature == 0.7
        assert cfg.model == "local-model"

    def test_string_coercion(self):
        cfg = AgentConfig.from_dict({"temperature": "0.5", "top_k": "30"})
        assert cfg.temperature == 0.5
        assert cfg.top_k == 30
