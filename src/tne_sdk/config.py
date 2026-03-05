"""
TNE-SDK: Agent Configuration

Every tunable parameter for the agent's cognitive loop, LLM calls, and
prompt customisation lives here.  ``AgentConfig.from_dict()`` is the
canonical way to hydrate a config from a profile dict (agents.json).

All fields have sensible defaults so a minimal profile only needs
``model``, ``api_key``, and ``llm_url``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_REFLECTION_COOLDOWN  = 200   # ticks between reflection cycles
DEFAULT_TACTICAL_COOLDOWN    = 10    # ticks between tactical reviews
DEFAULT_LLM_TIMEOUT          = 120.0
REFLECTION_MAX_CHARS         = 250_000  # ~50-60k tokens; guards against context overflow


@dataclass
class AgentConfig:
    """All tunable parameters for the agent's cognitive loop and LLM calls."""

    # ── Cognitive cycle timing ────────────────────────────────────────────── #
    reflection_cooldown_ticks:      int   = DEFAULT_REFLECTION_COOLDOWN
    tactical_review_cooldown_ticks: int   = DEFAULT_TACTICAL_COOLDOWN

    # ── LLM sampling — standard (action turns, and all calls when thinking is off) ── #
    temperature:       float = 0.7
    top_p:             float = 0.8
    top_k:             int   = 20
    presence_penalty:  float = 1.5

    # ── LLM sampling — thinking mode (reflection + tactical when thinking is on) ── #
    thinking_temperature:       float = 1.0
    thinking_top_p:             float = 0.95
    thinking_presence_penalty:  float = 1.5

    # ── LLM request timeout (seconds) ────────────────────────────────────── #
    llm_timeout: float = DEFAULT_LLM_TIMEOUT

    # ── Thinking toggle ───────────────────────────────────────────────────── #
    # When True, sends chat_template_kwargs.enable_thinking=True and uses
    # the thinking_* sampling params for reflection and tactical review.
    # When False, all calls use the standard sampling params and thinking
    # is disabled via chat_template_kwargs + /no_think prompt hint.
    # Supported by Qwen3, Qwen3.5, DeepSeek-R1, and similar reasoning models.
    enable_thinking: bool = False

    # ── Meta directive ────────────────────────────────────────────────────── #
    # A persistent high-level goal injected into every action prompt.
    # Examples: "Top the leaderboards", "Dominate the auction house",
    # "Become the strongest combat agent in the Grid".
    # Unlike directives (which are one-shot coaching), this is always present.
    meta_directive: str = ""

    # ── Model identifier ──────────────────────────────────────────────────── #
    model: str = "local-model"

    # ── Token budgets per cognitive function ───────────────────────────────── #
    max_tokens_action:     int = 2048
    max_tokens_reflection: int = 6144
    max_tokens_tactical:   int = 1024

    # Max characters of event JSON fed into a reflection prompt
    reflection_max_chars: int = REFLECTION_MAX_CHARS

    # ── Custom prompt file paths ──────────────────────────────────────────── #
    # Point these at .txt files to override the built-in prompts.
    # Paths are resolved relative to cwd.  Leave empty to use defaults.
    system_prompt_file:            str = ""
    reflection_system_prompt_file: str = ""
    reflection_user_prompt_file:   str = ""
    tactical_system_prompt_file:   str = ""
    tactical_user_prompt_file:     str = ""

    # ── Payload logging ───────────────────────────────────────────────────── #
    log_payloads: bool = False
    log_dir:      Path = field(default_factory=lambda: Path("logs"))

    # ── Extra kwargs forwarded verbatim to every LLM call ─────────────────── #
    default_llm_kwargs: dict[str, Any] = field(default_factory=dict)

    # ── Factory ──────────────────────────────────────────────────────────── #

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentConfig":
        """Build an AgentConfig from a profile dict (agents.json schema)."""
        cfg = cls()

        # Sampling — standard
        if (v := d.get("temperature"))      is not None: cfg.temperature      = float(v)
        if (v := d.get("top_p"))             is not None: cfg.top_p            = float(v)
        if (v := d.get("top_k"))             is not None: cfg.top_k            = int(v)
        if (v := d.get("presence_penalty"))  is not None: cfg.presence_penalty = float(v)

        # Sampling — thinking mode overrides
        if (v := d.get("thinking_temperature"))      is not None: cfg.thinking_temperature      = float(v)
        if (v := d.get("thinking_top_p"))             is not None: cfg.thinking_top_p             = float(v)
        if (v := d.get("thinking_presence_penalty"))  is not None: cfg.thinking_presence_penalty  = float(v)

        # Model / provider
        if (v := d.get("model"))             is not None: cfg.model            = str(v)
        if (v := d.get("llm_timeout"))       is not None: cfg.llm_timeout      = float(v)
        if (v := d.get("enable_thinking"))   is not None: cfg.enable_thinking  = bool(v)
        if (v := d.get("meta_directive"))    is not None: cfg.meta_directive   = str(v)

        # Token budgets
        if (v := d.get("max_tokens"))            is not None: cfg.max_tokens_action     = int(v)
        if (v := d.get("max_tokens_reflection")) is not None: cfg.max_tokens_reflection = int(v)
        if (v := d.get("max_tokens_tactical"))   is not None: cfg.max_tokens_tactical   = int(v)
        if (v := d.get("reflection_max_chars"))  is not None: cfg.reflection_max_chars  = int(v)

        # Cognitive cycle timing
        if (v := d.get("reflection_cooldown_ticks"))      is not None:
            cfg.reflection_cooldown_ticks = int(v)
        if (v := d.get("tactical_review_cooldown_ticks")) is not None:
            cfg.tactical_review_cooldown_ticks = int(v)

        # Custom prompt files
        if (v := d.get("system_prompt_file"))            is not None: cfg.system_prompt_file            = str(v)
        if (v := d.get("reflection_system_prompt_file")) is not None: cfg.reflection_system_prompt_file = str(v)
        if (v := d.get("reflection_user_prompt_file"))   is not None: cfg.reflection_user_prompt_file   = str(v)
        if (v := d.get("tactical_system_prompt_file"))   is not None: cfg.tactical_system_prompt_file   = str(v)
        if (v := d.get("tactical_user_prompt_file"))     is not None: cfg.tactical_user_prompt_file     = str(v)

        # Logging
        if (v := d.get("log_payloads"))      is not None: cfg.log_payloads     = bool(v)
        if (v := d.get("log_dir"))           is not None: cfg.log_dir          = Path(v)

        # Passthrough
        if (v := d.get("default_llm_kwargs"))             is not None:
            cfg.default_llm_kwargs = dict(v)

        cfg._validate()
        return cfg

    # ── Prompt loading ────────────────────────────────────────────────────── #

    def load_prompt(self, field_name: str, default: str) -> str:
        """
        Return the contents of the prompt file pointed to by *field_name*,
        or *default* if the field is empty or the file doesn't exist.
        """
        path_str = getattr(self, field_name, "")
        if not path_str:
            return default
        p = Path(path_str)
        if not p.is_file():
            return default
        return p.read_text(encoding="utf-8")

    # ── Validation ────────────────────────────────────────────────────────── #

    def _validate(self) -> None:
        """Reject obviously wrong values so misconfigurations fail early."""
        if not (0.0 <= self.temperature <= 2.0):
            raise ValueError(f"temperature must be 0.0–2.0, got {self.temperature}")
        if not (0.0 < self.top_p <= 1.0):
            raise ValueError(f"top_p must be 0.0–1.0, got {self.top_p}")
        if self.top_k < 0:
            raise ValueError(f"top_k must be >= 0, got {self.top_k}")
        if not (0.0 <= self.thinking_temperature <= 2.0):
            raise ValueError(f"thinking_temperature must be 0.0–2.0, got {self.thinking_temperature}")
        if not (0.0 < self.thinking_top_p <= 1.0):
            raise ValueError(f"thinking_top_p must be 0.0–1.0, got {self.thinking_top_p}")
        if self.max_tokens_action < 1:
            raise ValueError(f"max_tokens must be >= 1, got {self.max_tokens_action}")
        if self.max_tokens_reflection < 1:
            raise ValueError(f"max_tokens_reflection must be >= 1, got {self.max_tokens_reflection}")
        if self.max_tokens_tactical < 1:
            raise ValueError(f"max_tokens_tactical must be >= 1, got {self.max_tokens_tactical}")
        if self.llm_timeout <= 0:
            raise ValueError(f"llm_timeout must be > 0, got {self.llm_timeout}")
        if self.reflection_cooldown_ticks < 1:
            raise ValueError(f"reflection_cooldown_ticks must be >= 1, got {self.reflection_cooldown_ticks}")
        if self.tactical_review_cooldown_ticks < 1:
            raise ValueError(f"tactical_review_cooldown_ticks must be >= 1, got {self.tactical_review_cooldown_ticks}")
