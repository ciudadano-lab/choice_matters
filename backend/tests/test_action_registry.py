import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from action_registry import (
    ActionDefinition,
    get_action_chain_map,
    get_live_action_ids,
    get_prompt_action_types,
    load_action_registry,
)
from factor_library import get_library


CONTENT_DIR = Path(__file__).resolve().parents[1] / "content"
LEGACY_CHAIN_MAP = {
    "travel": "travel_chain",
    "search": "noticed_chain",
    "scavenge": "noticed_chain",
    "gather": "noticed_chain",
    "build": "noise_chain",
    "reinforce": "noise_chain",
    "rest": "noise_chain",
    "communicate": "noticed_chain",
    "negotiate": "noticed_chain",
    "trade": "noticed_chain",
    "recruit": "noticed_chain",
    "send_mission": "noticed_chain",
    "establish_settlement": "noticed_chain",
    "defend": "noticed_chain",
    "attack": "attack_chain",
}


def test_registry_loads_live_policy_and_tactical_actions():
    registry = load_action_registry()
    assert "resource_policy" in registry
    assert "travel" in registry
    assert registry["resource_policy"].category == "policy"
    assert registry["travel"].category == "tactical"
    assert "resource_policy" in get_live_action_ids(category="policy")
    assert "travel" in get_live_action_ids(category="tactical")


def test_registry_rejects_unknown_effect_types():
    with pytest.raises(ValidationError):
        ActionDefinition.model_validate({
            "id": "invalid_action",
            "action_type": "invalid_action",
            "label": "Invalid action",
            "category": "utility",
            "chain_template": "noticed_chain",
            "time_cost": 1,
            "required_inputs": [],
            "effects": [{"type": "unknown_effect", "amount": 1}],
            "status": "live",
            "source": "legacy",
        })


def test_parse_prompt_uses_live_registry_policy_types():
    rendered = get_prompt_action_types(include_policy_only=True)
    assert rendered.startswith("diplomatic_stance") or "resource_policy" in rendered
    assert "travel" not in rendered


def test_registry_chain_map_replaces_the_duplicate_chains_map():
    action_map = get_action_chain_map()
    legacy_map = {action: action_map[action] for action in LEGACY_CHAIN_MAP}
    chains = json.loads((CONTENT_DIR / "chains.json").read_text(encoding="utf-8"))

    assert legacy_map == LEGACY_CHAIN_MAP
    assert "actions" not in chains
    assert dict(get_library().actions) == action_map


def test_registry_includes_new_legacy_and_phase_four_actions():
    actions = load_action_registry()

    assert set(LEGACY_CHAIN_MAP).issubset(actions)
    assert {"fortify", "craft_repair", "signal"}.issubset(actions)
    assert actions["signal"].status == "draft"
    assert all(actions[action].chain_template == template for action, template in LEGACY_CHAIN_MAP.items())
