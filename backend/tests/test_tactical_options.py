import copy

import pytest
from pydantic import ValidationError

from information_layer import InformationLayer
from simulation_engine import SimulationEngine, StructuredAction
from encounter_resolution import (
    apply_pending_encounter,
    get_available_tactical_options,
    get_default_tactical_option_id,
    resolve_pending_encounter,
)
from tactical_options import TacticalOption, TacticalOptionsContent, load_tactical_options
from world_state import WorldState
from world_tick import PendingEncounter, WorldTick


def _world():
    return {
        "currentDay": 4,
        "currentTime": "22:00",
        "players": [{
            "id": "player_1",
            "health": 100,
            "stamina": 100,
            "hunger": 50,
            "stress": 10,
            "location": "school",
            "inventory": [],
            "statusEffects": [],
        }],
        "locations": [{
            "id": "school",
            "name": "School",
            "dangerLevel": 0,
            "flags": {},
            "noise": 2,
        }],
        "resources": {
            "food": 3,
            "water": 2,
            "building_materials": 2,
        },
        "discoveredInformation": [],
    }


def _pending():
    return {
        "playerId": "player_1",
        "actionId": "tactical-option-test",
        "actionType": "search",
        "target": "school",
        "parameters": {},
        "day": 4,
        "time": "22:00",
        "seed": 42,
        "planQuality": 0.0,
    }


def _available_ids(world=None):
    return {
        option["id"]
        for option in get_available_tactical_options(_pending(), world or _world())
    }


def test_tactical_options_content_is_valid_and_default_is_available():
    content = load_tactical_options()

    assert isinstance(content, TacticalOptionsContent)
    assert {option.id for option in content.options if option.status == "live"} == {
        "investigate",
        "hide",
        "ambush",
        "barricade",
        "flee",
        "fight",
        "eat",
        "hold",
    }
    assert get_default_tactical_option_id() == "hold"
    assert "hold" in _available_ids()


def test_available_options_follow_item_stat_and_flag_requirements():
    world = _world()
    options = _available_ids(world)
    assert {"hide", "fight", "barricade", "eat", "hold"}.issubset(options)
    assert "ambush" not in options

    world["players"][0]["stamina"] = 4
    world["resources"]["building_materials"] = 0
    world["resources"]["food"] = 0
    assert _available_ids(world) == {"hold"}

    world["players"][0]["stamina"] = 100
    world["resources"]["ammunition"] = 2
    world["locations"][0]["flags"]["fortified"] = 1
    assert "ambush" in _available_ids(world)
    world["locations"][0]["flags"].pop("fortified")
    assert "ambush" not in _available_ids(world)


def test_tactical_effect_vocabulary_rejects_unknown_effect_types():
    with pytest.raises(ValidationError):
        TacticalOption.model_validate({
            "id": "invalid",
            "label": "Invalid",
            "description": "Invalid option",
            "requirements": {},
            "costs": {},
            "follow_up_effects": [{"type": "unknown_effect"}],
            "status": "live",
        })


def test_tactical_modifier_is_logged_and_shifts_odds_safely():
    world = _world()
    baseline, reason = resolve_pending_encounter(_pending(), world)
    hidden, hidden_reason = resolve_pending_encounter(
        _pending(),
        world,
        chosen_option="hide",
    )
    repeated, _ = resolve_pending_encounter(
        _pending(),
        world,
        chosen_option="hide",
    )

    assert reason is None
    assert hidden_reason is None
    assert hidden is not None and baseline is not None
    assert hidden == repeated
    assert hidden.probabilities["hurt"] < baseline.probabilities["hurt"]
    assert any(
        modifier["source"] == "tactic:hide"
        for modifier in hidden.modifiers
    )


def test_invalid_or_unavailable_tactical_option_is_rejected():
    world = _world()
    world["players"][0]["stamina"] = 4

    with pytest.raises(ValueError, match="requirements are not met"):
        resolve_pending_encounter(_pending(), world, chosen_option="fight")
    with pytest.raises(ValueError, match="Unknown or unavailable"):
        resolve_pending_encounter(_pending(), world, chosen_option="teleport")


def test_default_and_no_option_preserve_legacy_encounter_outcome():
    world = _world()
    no_option, _ = resolve_pending_encounter(_pending(), world)
    explicit_none, _ = resolve_pending_encounter(
        _pending(),
        world,
        chosen_option=None,
    )
    default, _ = resolve_pending_encounter(
        _pending(),
        world,
        chosen_option=get_default_tactical_option_id(),
    )

    assert no_option == explicit_none == default
    assert no_option is not None
    assert "tacticalOption" not in no_option.to_dict()

def test_legacy_encounter_resolver_accepts_and_applies_a_tactical_option():
    world = _world()
    seed = next(
        candidate
        for candidate in range(1000)
        if SimulationEngine(seed=candidate).apply(
            world,
            StructuredAction(
                actionId="direct-tactical-option",
                actionType="search",
                actor="player_1",
                target="school",
                parameters={"seed": candidate},
            ),
            rules="current",
        ).encounterOccurred
    )
    action = StructuredAction(
        actionId="direct-tactical-option",
        actionType="search",
        actor="player_1",
        target="school",
        parameters={"seed": seed},
    )
    result = SimulationEngine(seed=seed).apply(world, action, rules="current")

    resolved = WorldTick(seed=seed).resolve_encounter_after_action(
        world,
        result,
        action,
        seed=seed,
        chosen_option="hide",
    )

    assert resolved.encounterOutcome["tacticalOption"]["id"] == "hide"
    assert any(
        modifier["source"] == "tactic:hide"
        for modifier in resolved.encounterOutcome["modifiers"]
    )
    assert resolved.updatedWorldState["players"][0]["stamina"] <= 98


def test_barricade_costs_resources_and_adds_temporary_fortification():
    world = _world()
    outcome, reason = resolve_pending_encounter(
        _pending(),
        world,
        chosen_option="barricade",
    )
    assert reason is None and outcome is not None

    updated, apply_reason = apply_pending_encounter(
        copy.deepcopy(world),
        "player_1",
        outcome,
    )

    assert apply_reason is None
    assert updated["resources"]["building_materials"] == 1
    location = updated["locations"][0]
    assert location["flags"]["fortified"] == 1
    assert location["flagExpiries"]["fortified"] == 6
    assert location["noise"] == 3
    assert updated["players"][0]["stamina"] <= 97


def test_hide_reduces_noise_and_investigate_records_player_scoped_information():
    world = _world()
    world["players"].append({
        "id": "player_2",
        "health": 100,
        "stamina": 100,
        "location": "school",
        "inventory": [],
    })
    hidden, _ = resolve_pending_encounter(
        _pending(),
        world,
        chosen_option="hide",
    )
    assert hidden is not None
    hidden_world, reason = apply_pending_encounter(
        copy.deepcopy(world),
        "player_1",
        hidden,
    )
    assert reason is None
    assert hidden_world["locations"][0]["noise"] == 1

    investigated, _ = resolve_pending_encounter(
        _pending(),
        world,
        chosen_option="investigate",
    )
    assert investigated is not None
    informed_world, reason = apply_pending_encounter(
        copy.deepcopy(world),
        "player_1",
        investigated,
    )
    assert reason is None
    assert informed_world["discoveredInformation"][0]["player_id"] == "player_1"
    layer = InformationLayer()
    assert layer.get_visible_state(
        informed_world,
        player_id="player_1",
    )["school"]["knownInformation"]
    assert layer.get_visible_state(
        informed_world,
        player_id="player_2",
    )["school"]["knownInformation"] == []


def test_eat_consumes_food_and_reduces_hunger():
    world = _world()
    outcome, reason = resolve_pending_encounter(
        _pending(),
        world,
        chosen_option="eat",
    )
    assert reason is None and outcome is not None

    updated, apply_reason = apply_pending_encounter(
        copy.deepcopy(world),
        "player_1",
        outcome,
    )

    assert apply_reason is None
    assert updated["resources"]["food"] == 2
    assert updated["players"][0]["hunger"] == 40


def test_harness_bots_use_simple_deterministic_tactical_policies():
    world = _world()
    pending = PendingEncounter.from_dict({
        **_pending(),
        "encounterStep": "encounter",
        "stepIndex": 0,
        "rollLog": [],
        "isAmbient": False,
        "outcomeTier": "success",
        "recordIndex": 0,
    })

    assert WorldTick(seed=42, tactical_option_policy="careful")._choose_tactical_option_for_bot(
        pending,
        WorldState.from_dict(world),
    ) == "hide"
    assert WorldTick(seed=42, tactical_option_policy="aggressive")._choose_tactical_option_for_bot(
        pending,
        WorldState.from_dict(world),
    ) == "fight"
