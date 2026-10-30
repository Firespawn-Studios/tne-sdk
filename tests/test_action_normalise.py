"""_normalise_action_params: what the agent sends after parsing the LLM's JSON.

The server (v1.0.34+) rejects a whole action if a parameter other than the
trade item maps holds an object, so the SDK must never forward one.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tne_sdk.agent import _normalise_action_params


def test_clean_action_is_unchanged():
    action = {"action": "move", "parameters": {"territory": "rust_wastes"}, "reasoning": "go"}
    assert _normalise_action_params(dict(action)) == action


def test_missing_parameters_becomes_empty_dict():
    assert _normalise_action_params({"action": "rest", "reasoning": "hp low"}) == {
        "action": "rest", "parameters": {}, "reasoning": "hp low",
    }


def test_flat_plain_keys_are_folded_into_parameters():
    action = {"action": "list_auction", "item_id": "scrap_metal", "quantity": 3,
              "starting_price": 10.5, "reasoning": "sell"}
    assert _normalise_action_params(action)["parameters"] == {
        "item_id": "scrap_metal", "quantity": 3, "starting_price": 10.5,
    }


def test_explicit_parameters_win_over_flat_keys():
    action = {"action": "move", "parameters": {"territory": "a"}, "territory": "b"}
    assert _normalise_action_params(action)["parameters"] == {"territory": "a"}


@pytest.mark.parametrize("extra", [
    {"thoughts": {"risk": "low"}},
    {"plan": ["move", "gather"]},
    {"memory": {"notes": ["x"]}},
])
def test_extra_objects_and_lists_are_dropped(extra):
    action = {"action": "move", "parameters": {"territory": "rust_wastes"}, "reasoning": "go", **extra}
    assert _normalise_action_params(action) == {
        "action": "move", "parameters": {"territory": "rust_wastes"}, "reasoning": "go",
    }


def test_object_inside_parameters_is_dropped():
    action = {"action": "move", "parameters": {"territory": "rust_wastes", "context": {"why": "x"}}}
    assert _normalise_action_params(action)["parameters"] == {"territory": "rust_wastes"}


def test_trade_item_maps_are_kept_nested_or_flat():
    nested = {"action": "propose_trade",
              "parameters": {"target": "a1", "offer_items": {"scrap_metal": 2}}}
    flat = {"action": "propose_trade", "target": "a1", "request_items": {"repair_kit": 1}}
    assert _normalise_action_params(nested)["parameters"] == {
        "target": "a1", "offer_items": {"scrap_metal": 2},
    }
    assert _normalise_action_params(flat)["parameters"] == {
        "target": "a1", "request_items": {"repair_kit": 1},
    }


@pytest.mark.parametrize("bad", [["territory", "x"], "rust_wastes", 5])
def test_parameters_that_are_not_an_object_become_empty(bad):
    # The old code raised on a list here, so the whole turn fell back to "wait".
    assert _normalise_action_params({"action": "move", "parameters": bad})["parameters"] == {}
