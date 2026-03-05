"""Tests for ProfileStore CRUD and validation."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tne_sdk.profile_store import ProfileStore, ProfileValidationError, _validate_profile


VALID_PROFILE = {
    "name": "TestAgent",
    "api_key": "ne_test_key_12345",
    "llm_url": "http://localhost:11434/v1",
    "model": "qwen3:14b",
}


class TestProfileStore:
    def test_load_empty(self, tmp_path):
        store = ProfileStore(path=tmp_path / "agents.json")
        profiles = store.load()
        assert profiles == []

    def test_add_and_save(self, tmp_path):
        path = tmp_path / "agents.json"
        store = ProfileStore(path=path)
        store.load()
        store.add(VALID_PROFILE.copy())
        store.save()

        store2 = ProfileStore(path=path)
        store2.load()
        assert len(store2.profiles) == 1
        assert store2.profiles[0]["name"] == "TestAgent"

    def test_get(self, tmp_path):
        store = ProfileStore(path=tmp_path / "agents.json")
        store.load()
        store.add(VALID_PROFILE.copy())
        assert store.get("TestAgent") is not None
        assert store.get("NonExistent") is None

    def test_list_names(self, tmp_path):
        store = ProfileStore(path=tmp_path / "agents.json")
        store.load()
        store.add(VALID_PROFILE.copy())
        store.add({**VALID_PROFILE, "name": "Agent2"})
        assert store.list_names() == ["TestAgent", "Agent2"]

    def test_update(self, tmp_path):
        store = ProfileStore(path=tmp_path / "agents.json")
        store.load()
        store.add(VALID_PROFILE.copy())
        store.update("TestAgent", {"model": "gpt-4o"})
        assert store.get("TestAgent")["model"] == "gpt-4o"

    def test_update_nonexistent_raises(self, tmp_path):
        store = ProfileStore(path=tmp_path / "agents.json")
        store.load()
        with pytest.raises(KeyError):
            store.update("Ghost", {"model": "x"})

    def test_delete(self, tmp_path):
        store = ProfileStore(path=tmp_path / "agents.json")
        store.load()
        store.add(VALID_PROFILE.copy())
        store.delete("TestAgent")
        assert store.list_names() == []

    def test_delete_nonexistent_raises(self, tmp_path):
        store = ProfileStore(path=tmp_path / "agents.json")
        store.load()
        with pytest.raises(KeyError):
            store.delete("Ghost")

    def test_duplicate_name_raises(self, tmp_path):
        store = ProfileStore(path=tmp_path / "agents.json")
        store.load()
        store.add(VALID_PROFILE.copy())
        with pytest.raises(ValueError, match="already exists"):
            store.add(VALID_PROFILE.copy())

    def test_json_roundtrip(self, tmp_path):
        path = tmp_path / "agents.json"
        store = ProfileStore(path=path)
        store.load()
        store.add(VALID_PROFILE.copy())
        store.save()

        raw = json.loads(path.read_text())
        assert "agents" in raw
        assert len(raw["agents"]) == 1
        assert raw["agents"][0]["name"] == "TestAgent"


class TestValidation:
    def test_valid_profile_passes(self):
        _validate_profile(VALID_PROFILE)

    def test_empty_name_fails(self):
        with pytest.raises(ProfileValidationError, match="name"):
            _validate_profile({**VALID_PROFILE, "name": ""})

    def test_placeholder_key_fails(self):
        with pytest.raises(ProfileValidationError, match="api_key"):
            _validate_profile({**VALID_PROFILE, "api_key": "YOUR_KEY"})

    def test_empty_key_fails(self):
        with pytest.raises(ProfileValidationError, match="api_key"):
            _validate_profile({**VALID_PROFILE, "api_key": ""})

    def test_bad_llm_url_fails(self):
        with pytest.raises(ProfileValidationError, match="llm_url"):
            _validate_profile({**VALID_PROFILE, "llm_url": "not-a-url"})

    def test_empty_model_fails(self):
        with pytest.raises(ProfileValidationError, match="model"):
            _validate_profile({**VALID_PROFILE, "model": ""})
