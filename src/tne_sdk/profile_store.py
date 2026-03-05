"""
TNE-SDK: Profile Store

Manages agent profiles (agents.json) with full CRUD + validation.

Default location : ~/.tne_sdk/agents.json
Override via env : TNE_PROFILES_PATH=/path/to/agents.json

Schema matches the format described in examples/agent.yaml.example.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_DEFAULT_PROFILES_DIR  = Path.home() / ".tne_sdk"
_DEFAULT_PROFILES_FILE = _DEFAULT_PROFILES_DIR / "agents.json"

_PLACEHOLDER_KEYS = {"YOUR_NULLEPOCH_GAME_API_KEY", "YOUR_KEY", "PLACEHOLDER", ""}
LIVE_GAME_HOST    = "api.null.firespawn.ai"


class ProfileValidationError(ValueError):
    """Raised when a profile fails validation."""


def _profiles_path() -> Path:
    env = os.environ.get("TNE_PROFILES_PATH")
    return Path(env) if env else _DEFAULT_PROFILES_FILE


class ProfileStore:
    """
    CRUD store for agent profiles backed by a JSON file.

    Usage
    -----
        store = ProfileStore()
        profiles = store.load()
        store.add({"name": "Spectre-7", "api_key": "...", ...})
        store.save()
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path: Path = Path(path) if path else _profiles_path()
        self._profiles: list[dict[str, Any]] = []
        self._dirty = False

    # ── I/O ───────────────────────────────────────────────────────────────── #

    def load(self) -> list[dict[str, Any]]:
        """Load profiles from disk.  Returns [] if file doesn't exist."""
        if not self._path.exists():
            self._profiles = []
            return self._profiles
        with open(self._path, encoding="utf-8") as f:
            data = json.load(f)
        self._profiles = data.get("agents", [])
        self._dirty = False
        return self._profiles

    def save(self) -> None:
        """Persist profiles to disk (creates parent dirs as needed)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"agents": self._profiles}, f, indent=2)
        self._dirty = False

    # ── CRUD ──────────────────────────────────────────────────────────────── #

    def get(self, name: str) -> dict[str, Any] | None:
        """Return a profile dict by name, or None."""
        return next((p for p in self._profiles if p.get("name") == name), None)

    def list_names(self) -> list[str]:
        return [p.get("name", "") for p in self._profiles]

    def add(self, profile: dict[str, Any], *, validate: bool = True) -> None:
        """Add a new profile.  Raises ProfileValidationError on bad data."""
        if validate:
            _validate_profile(profile)
        name = profile["name"]
        if self.get(name):
            raise ValueError(f"Profile '{name}' already exists.")
        self._profiles.append(profile)
        self._dirty = True

    def update(self, name: str, updates: dict[str, Any], *, validate: bool = True) -> None:
        """Merge *updates* into the named profile."""
        profile = self.get(name)
        if profile is None:
            raise KeyError(f"Profile '{name}' not found.")
        profile.update(updates)
        if validate:
            _validate_profile(profile)
        self._dirty = True

    def delete(self, name: str) -> None:
        """Remove a profile by name.  Raises KeyError if not found."""
        before = len(self._profiles)
        self._profiles = [p for p in self._profiles if p.get("name") != name]
        if len(self._profiles) == before:
            raise KeyError(f"Profile '{name}' not found.")
        self._dirty = True

    def replace_all(self, profiles: list[dict[str, Any]]) -> None:
        self._profiles = profiles
        self._dirty = True

    @property
    def profiles(self) -> list[dict[str, Any]]:
        return self._profiles

    @property
    def path(self) -> Path:
        return self._path


# ── Validation ────────────────────────────────────────────────────────────── #

def _validate_profile(profile: dict[str, Any]) -> None:
    """Raise ProfileValidationError if the profile is incomplete or invalid."""
    name = profile.get("name", "").strip()
    if not name:
        raise ProfileValidationError("Profile must have a non-empty 'name'.")

    api_key = profile.get("api_key", "").strip()
    if not api_key or api_key in _PLACEHOLDER_KEYS:
        raise ProfileValidationError(
            f"Profile '{name}': 'api_key' must be a valid game API key (not a placeholder)."
        )

    llm_url = profile.get("llm_url", "").strip()
    if not llm_url or not llm_url.startswith("http"):
        raise ProfileValidationError(
            f"Profile '{name}': 'llm_url' must start with http:// or https://."
        )

    model = profile.get("model", "").strip()
    if not model:
        raise ProfileValidationError(f"Profile '{name}': 'model' must not be empty.")
