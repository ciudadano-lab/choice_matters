import copy
import json
import shutil
from pathlib import Path

import pytest

from factor_library import _get_action_location, get_library, load_library
from plan_features import PlanFeatures
from probability_engine import run_chain
from simulation_engine import SimulationEngine, StructuredAction


CONTENT_DIR = Path(__file__).resolve().parents[1] / "content"
EXPECTED_FACTOR_IDS = {
    "location_danger_gate",
    "location_danger_encounter",
    "zombie_population",
    "fortified_gate",
    "fortified_encounter",
    "night",
    "weather",
    "loud_action",
    "stealth_intent",
    "active_status_effects",
    "stress",
    "companions",
    "known_target_locations",
    "unknown_target_locations",
    "items_in_hand",
    "missing_items",
    "fallback_plan",
    "countermeasures",
    "approach_fits",
    "approach_mismatch",
}
EXPECTED_LOG_SOURCES = {
    "location_danger",
    "zombie_population",
    "fortified_gate",
    "fortified_encounter",
    "night",
    "weather",
    "loud_action",
    "stealth_intent",
    "active_status_effects",
    "stress",
    "companions",
    "known_target_locations",
    "unknown_target_locations",
    "items_in_hand",
    "missing_items",
    "fallback_plan",
    "countermeasures",
    "approach_fits",
    "approach_mismatch",
}


def _copy_content(tmp_path: Path) -> Path:
    target = tmp_path / "content"
    shutil.copytree(CONTENT_DIR, target)
    return target


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)


def _factor_file(content_dir: Path, category: str) -> Path:
    return content_dir / "factors" / f"{category}.json"


def _change_factor(content_dir: Path, category: str, factor_id: str, change) -> None:
    path = _factor_file(content_dir, category)
    factors = _read_json(path)
    factor = next(item for item in factors if item["id"] == factor_id)
    change(factor)
    _write_json(path, factors)


def _add_factor(content_dir: Path, category: str, factor: dict) -> None:
    path = _factor_file(content_dir, category)
    factors = _read_json(path)
    factors.append(factor)
    _write_json(path, factors)


def _fog_factor(*, status="live", order=90, factor_id="fog") -> dict:
    return {
        "id": factor_id,
        "order": order,
        "label": "Fog",
        "category": "environment",
        "kind": "situation",
        "role": "modifier",
        "applies_to": {"step_kinds": ["gate"]},
        "calc": {
            "type": "threshold",
            "input": "world.weather",
            "op": "eq",
            "value": "fog",
            "delta": 0.08,
        },
        "explanation": "Fog changes how visible movement is.",
        "status": status,
        "source": "human",
    }


def _state(*, weather="clear", danger=0):
    return {
        "currentDay": 1,
        "currentTime": "12:00",
        "weather": weather,
        "players": [{
            "id": "player_1",
            "location": "loc_school",
            "stress": 0,
            "statusEffects": [],
            "entities": {"companions": []},
        }],
        "locations": [{
            "id": "loc_school",
            "name": "School",
            "dangerLevel": danger,
            "zombiePopulation": 0,
        }],
    }


def _action(action_type="search", *, focus=""):
    return StructuredAction(
        actionId=f"factor-{action_type}",
        actionType=action_type,
        actor="player_1",
        target="loc_school",
        parameters={"policy": {"focus": focus}},
    )


def test_action_location_does_not_match_idless_list_item():
    idless_location = {"name": "Id-less location"}
    target_location = {"id": "loc_school", "name": "School"}

    found = _get_action_location(
        [idless_location, target_location],
        "loc_school",
        None,
    )

    assert found is target_location


def _record(result, name):
    return next(step for step in result.steps if step.name == name)


def test_shipped_content_has_twenty_factor_records_nineteen_sources_and_five_templates():
    library = get_library()

    assert {factor.id for factor in library.factors} == EXPECTED_FACTOR_IDS
    assert len(library.factors) == 20
    assert {factor.log_source or factor.id for factor in library.factors} == EXPECTED_LOG_SOURCES
    assert len(library.templates) == 5
    assert set(library.templates) == {
        "noticed_chain", "noise_chain", "travel_chain", "attack_chain",
        "ambient_night_chain",
    }
    assert set(library.actions) == {
        "travel", "search", "scavenge", "gather", "build", "reinforce",
        "rest", "communicate", "negotiate", "trade", "recruit", "send_mission",
        "establish_settlement", "defend", "attack", "fortify", "craft_repair",
    }


def test_fortification_counters_gate_and_encounter_until_its_expiry():
    world = {
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [{"id": "player_1", "location": "shelter"}],
        "locations": [{
            "id": "shelter",
            "name": "Shelter",
            "discovered": True,
            "dangerLevel": 0,
            "flags": {},
        }],
        "resources": {"building_materials": 3},
    }
    fortify = StructuredAction(
        actionId="fortify-shelter",
        actionType="fortify",
        actor="player_1",
        target="shelter",
    )
    fortified = SimulationEngine(seed=1).apply(world, fortify).updatedWorldState
    check_action = StructuredAction(
        actionId="fortified-search",
        actionType="search",
        actor="player_1",
        target="shelter",
    )

    active = run_chain(
        fortified,
        check_action,
        seed=1,
        library=get_library(),
    )
    gate = next(step for step in active.steps if step.kind == "gate")
    encounter = next(step for step in active.steps if step.kind == "encounter")
    assert {factor["source"] for factor in gate.counters} >= {"fortified_gate"}
    assert {factor["source"] for factor in encounter.counters} >= {"fortified_encounter"}
    assert next(item["delta"] for item in gate.counters if item["source"] == "fortified_gate") == 0.08

    fortified["currentDay"] = 6
    expired = run_chain(
        fortified,
        check_action,
        seed=1,
        library=get_library(),
    )
    assert "fortified_gate" not in {
        factor["source"]
        for step in expired.steps
        for factor in step.counters
    }


def test_crafted_repair_kit_is_recognized_by_plan_item_factor():
    world = {
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [{"id": "player_1", "location": "shelter", "inventory": []}],
        "locations": [{"id": "shelter", "name": "Shelter", "discovered": True}],
        "resources": {"medical_supplies": 1},
    }
    crafted = SimulationEngine(seed=1).apply(
        world,
        StructuredAction(
            actionId="craft-repair-kit",
            actionType="craft_repair",
            actor="player_1",
            target="shelter",
        ),
    )
    plan = PlanFeatures.from_untrusted({"itemsPlanned": ["repair kit"]})
    chain = run_chain(
        crafted.updatedWorldState,
        StructuredAction(
            actionId="plan-with-repair-kit",
            actionType="search",
            actor="player_1",
            target="shelter",
        ),
        seed=1,
        library=get_library(),
        plan_features=plan,
    )
    encounter = next(step for step in chain.steps if step.kind == "encounter")

    assert any(item["source"] == "items_in_hand" for item in encounter.counters)


def test_factor_ids_are_unique_across_files(tmp_path):
    content = _copy_content(tmp_path)
    factors_path = _factor_file(content, "environment")
    factors = _read_json(factors_path)
    factors.append(copy.deepcopy(factors[0]))
    _write_json(factors_path, factors)

    with pytest.raises(ValueError, match="duplicate factor id") as error:
        load_library(content)

    assert "night" in str(error.value)
    assert str(factors_path) in str(error.value)


def test_factor_category_must_match_its_file_stem(tmp_path):
    content = _copy_content(tmp_path)
    path = _factor_file(content, "environment")
    _change_factor(
        content,
        "environment",
        "night",
        lambda factor: factor.update(category="approach"),
    )

    with pytest.raises(ValueError, match="category must be 'environment'") as error:
        load_library(content)

    assert "night" in str(error.value)
    assert str(path) in str(error.value)


def test_unknown_factor_input_is_rejected_with_path_and_id(tmp_path):
    content = _copy_content(tmp_path)
    path = _factor_file(content, "environment")
    _change_factor(
        content,
        "environment",
        "night",
        lambda factor: factor["calc"].update(input="world.unknown"),
    )

    with pytest.raises(ValueError, match="unknown input") as error:
        load_library(content)

    assert "night" in str(error.value)
    assert str(path) in str(error.value)


def test_plan_factors_require_plan_inputs(tmp_path):
    content = _copy_content(tmp_path)
    _add_factor(content, "approach", {
        "id": "invalid_plan_input",
        "order": 110,
        "label": "Invalid plan input",
        "category": "approach",
        "kind": "plan",
        "role": "counter",
        "applies_to": {"step_kinds": ["gate"]},
        "calc": {
            "type": "threshold",
            "input": "world.weather",
            "op": "eq",
            "value": "rain",
            "delta": 0.1,
        },
        "explanation": "A test plan factor.",
        "status": "live",
        "source": "human",
    })

    with pytest.raises(ValueError, match="plan factors must use plan\\.\\* inputs"):
        load_library(content)


def test_situation_factors_cannot_use_plan_inputs(tmp_path):
    content = _copy_content(tmp_path)
    _add_factor(content, "environment", {
        "id": "invalid_situation_input",
        "order": 110,
        "label": "Invalid situation input",
        "category": "environment",
        "kind": "situation",
        "role": "counter",
        "applies_to": {"step_kinds": ["gate"]},
        "calc": {
            "type": "threshold",
            "input": "plan.has_fallback",
            "op": "eq",
            "value": True,
            "delta": 0.1,
        },
        "explanation": "A test situation factor.",
        "status": "live",
        "source": "human",
    })

    with pytest.raises(
        ValueError,
        match="situation factors must not use plan\\.\\* inputs",
    ):
        load_library(content)


def test_linear_factors_require_numeric_inputs(tmp_path):
    content = _copy_content(tmp_path)
    _change_factor(
        content,
        "location",
        "location_danger_gate",
        lambda factor: factor["calc"].update(input="world.weather"),
    )

    with pytest.raises(ValueError, match="is not numeric"):
        load_library(content)


def test_threshold_comparison_requires_numeric_value(tmp_path):
    content = _copy_content(tmp_path)
    _change_factor(
        content,
        "environment",
        "weather",
        lambda factor: factor["calc"].update(op="gte"),
    )

    with pytest.raises(ValueError, match="comparison value must be a number") as error:
        load_library(content)

    assert "weather" in str(error.value)


def test_in_threshold_requires_list_value(tmp_path):
    content = _copy_content(tmp_path)
    _change_factor(
        content,
        "environment",
        "weather",
        lambda factor: factor["calc"].update(value="rain"),
    )

    with pytest.raises(ValueError, match="value must be a list when op is 'in'") as error:
        load_library(content)

    assert "weather" in str(error.value)


def test_empty_step_kinds_are_rejected(tmp_path):
    content = _copy_content(tmp_path)
    _change_factor(
        content,
        "environment",
        "night",
        lambda factor: factor["applies_to"].update(step_kinds=[]),
    )

    with pytest.raises(ValueError) as error:
        load_library(content)

    assert "night" in str(error.value)
    assert "step_kinds" in str(error.value)


@pytest.mark.parametrize("required_step", ["missing", "encounter"])
def test_chain_requires_must_name_an_earlier_step(tmp_path, required_step):
    content = _copy_content(tmp_path)
    path = content / "chains.json"
    chains = _read_json(path)
    chains["templates"]["noticed_chain"]["steps"][0]["requires"] = required_step
    _write_json(path, chains)

    with pytest.raises(ValueError, match="requires unknown or later step") as error:
        load_library(content)

    assert "noticed_chain" in str(error.value)
    assert str(path) in str(error.value)


def test_unknown_action_template_is_rejected(tmp_path):
    content = _copy_content(tmp_path)
    path = content / "chains.json"
    action_path = content / "actions" / "search.json"
    action = _read_json(action_path)
    action["chain_template"] = "missing_chain"
    _write_json(action_path, action)

    with pytest.raises(ValueError, match="unknown chain template") as error:
        load_library(content)

    assert "search" in str(error.value)
    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    "field_location",
    ["factor", "calc", "applies_to", "chain_step", "chain_template", "chain_file"],
)
def test_unknown_fields_are_rejected_at_every_content_level(tmp_path, field_location):
    content = _copy_content(tmp_path)
    if field_location in {"factor", "calc", "applies_to"}:
        path = _factor_file(content, "environment")
        factors = _read_json(path)
        if field_location == "factor":
            factors[0]["unexpected"] = True
        elif field_location == "calc":
            factors[0]["calc"]["unexpected"] = True
        else:
            factors[0]["applies_to"]["unexpected"] = True
        _write_json(path, factors)
        identifier = "night"
    else:
        path = content / "chains.json"
        chains = _read_json(path)
        if field_location == "chain_step":
            chains["templates"]["noticed_chain"]["steps"][0]["unexpected"] = True
        elif field_location == "chain_template":
            chains["templates"]["noticed_chain"]["unexpected"] = True
        else:
            chains["unexpected"] = True
        _write_json(path, chains)
        identifier = "noticed_chain" if field_location == "chain_step" else "chain"

    with pytest.raises(ValueError, match="Extra inputs are not permitted") as error:
        load_library(content)

    assert identifier in str(error.value)
    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    "factor_id, update",
    [
        ("location_danger_gate", lambda factor: factor["calc"].update(per_unit=-0.1)),
        ("location_danger_gate", lambda factor: factor["calc"].update(unit_size=0)),
        ("location_danger_gate", lambda factor: factor["calc"].update(per_unit=float("nan"))),
        ("zombie_population", lambda factor: factor.update(cap=-0.1)),
        ("zombie_population", lambda factor: factor.update(cap=float("inf"))),
    ],
)
def test_linear_values_and_caps_must_be_sane(tmp_path, factor_id, update):
    content = _copy_content(tmp_path)
    category = "location"
    path = _factor_file(content, category)
    _change_factor(content, category, factor_id, update)

    with pytest.raises(ValueError) as error:
        load_library(content)

    assert factor_id in str(error.value)
    assert str(path) in str(error.value)


def test_threshold_delta_must_be_finite_and_non_negative(tmp_path):
    content = _copy_content(tmp_path)
    path = _factor_file(content, "environment")
    _change_factor(
        content,
        "environment",
        "weather",
        lambda factor: factor["calc"].update(delta=-0.1),
    )

    with pytest.raises(ValueError, match="delta must be non-negative") as error:
        load_library(content)

    assert "weather" in str(error.value)
    assert str(path) in str(error.value)


def test_threshold_delta_must_be_finite(tmp_path):
    content = _copy_content(tmp_path)
    path = _factor_file(content, "environment")
    _change_factor(
        content,
        "environment",
        "weather",
        lambda factor: factor["calc"].update(delta=float("nan")),
    )

    with pytest.raises(ValueError, match="delta must be finite") as error:
        load_library(content)

    assert "weather" in str(error.value)
    assert str(path) in str(error.value)


def test_chain_step_names_must_be_unique_and_base_and_interrupt_valid(tmp_path):
    content = _copy_content(tmp_path)
    path = content / "chains.json"
    chains = _read_json(path)
    chains["templates"]["noticed_chain"]["steps"][1]["name"] = "noticed"
    _write_json(path, chains)

    with pytest.raises(ValueError, match="duplicate step name") as error:
        load_library(content)

    assert "noticed_chain" in str(error.value)
    assert str(path) in str(error.value)


def test_chain_step_base_must_be_between_zero_and_one(tmp_path):
    content = _copy_content(tmp_path)
    path = content / "chains.json"
    chains = _read_json(path)
    chains["templates"]["attack_chain"]["steps"][0]["base"] = 1
    _write_json(path, chains)

    with pytest.raises(ValueError, match="base must be between zero and one"):
        load_library(content)


def test_chain_interrupt_must_be_boolean(tmp_path):
    content = _copy_content(tmp_path)
    path = content / "chains.json"
    chains = _read_json(path)
    chains["templates"]["noticed_chain"]["steps"][0]["interrupt"] = "false"
    _write_json(path, chains)

    with pytest.raises(ValueError) as error:
        load_library(content)

    assert "noticed_chain" in str(error.value)
    assert "interrupt" in str(error.value)


def test_factor_step_kinds_must_be_valid(tmp_path):
    content = _copy_content(tmp_path)
    path = _factor_file(content, "environment")
    _change_factor(
        content,
        "environment",
        "night",
        lambda factor: factor["applies_to"].update(step_kinds=["tactical"]),
    )

    with pytest.raises(ValueError) as error:
        load_library(content)

    assert "night" in str(error.value)
    assert str(path) in str(error.value)


def test_factor_explanation_must_be_non_empty(tmp_path):
    content = _copy_content(tmp_path)
    _change_factor(
        content,
        "environment",
        "night",
        lambda factor: factor.update(explanation="  "),
    )

    with pytest.raises(ValueError, match="explanation must be non-empty"):
        load_library(content)


def test_invalid_json_error_includes_the_file_path(tmp_path):
    content = _copy_content(tmp_path)
    path = content / "chains.json"
    path.write_text('{"templates":', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON") as error:
        load_library(content)

    assert str(path) in str(error.value)


@pytest.mark.parametrize("calc_type", ["flag", "table"])
def test_reserved_calculations_raise_not_implemented(tmp_path, calc_type):
    content = _copy_content(tmp_path)
    _change_factor(
        content,
        "environment",
        "night",
        lambda factor: factor.update(calc={"type": calc_type}),
    )

    with pytest.raises(NotImplementedError, match="not yet supported") as error:
        load_library(content)

    assert "night" in str(error.value)


def test_draft_factors_are_loaded_but_do_not_change_chance(tmp_path):
    content = _copy_content(tmp_path)
    _add_factor(content, "environment", _fog_factor(status="draft"))
    library = load_library(content)
    state = _state(weather="fog")
    action = _action()
    baseline = _record(run_chain(state, action, 91), "noticed")
    with_draft = _record(run_chain(state, action, 91, library=library), "noticed")

    assert any(factor.id == "fog" for factor in library.factors)
    assert with_draft.chance == baseline.chance
    assert all(item["source"] != "fog" for item in with_draft.modifiers)


def test_live_fog_factor_is_extensible_from_json_only(tmp_path):
    content = _copy_content(tmp_path)
    _add_factor(content, "environment", _fog_factor())
    library = load_library(content)
    state = _state(weather="fog")
    action = _action()

    baseline = _record(run_chain(state, action, 91), "noticed")
    extended = _record(run_chain(state, action, 91, library=library), "noticed")

    assert extended.chance - baseline.chance == pytest.approx(0.08)
    assert {"source": "fog", "delta": 0.08} in extended.modifiers


def test_counter_and_modifier_roles_apply_signs_and_zero_deltas_are_omitted(tmp_path):
    content = _copy_content(tmp_path)
    _add_factor(content, "approach", {
        "id": "test_raise",
        "order": 70,
        "label": "Test raise",
        "category": "approach",
        "kind": "situation",
        "role": "modifier",
        "applies_to": {"step_kinds": ["gate"]},
        "calc": {
            "type": "threshold",
            "input": "world.weather",
            "op": "eq",
            "value": "rain",
            "delta": 0.08,
        },
        "explanation": "Raises the test chance.",
        "status": "live",
        "source": "human",
    })
    _add_factor(content, "approach", {
        "id": "test_lower",
        "order": 80,
        "label": "Test lower",
        "category": "approach",
        "kind": "situation",
        "role": "counter",
        "applies_to": {"step_kinds": ["gate"]},
        "calc": {
            "type": "threshold",
            "input": "world.weather",
            "op": "eq",
            "value": "rain",
            "delta": 0.03,
        },
        "explanation": "Lowers the test chance.",
        "status": "live",
        "source": "human",
    })
    _add_factor(content, "approach", {
        "id": "zero_modifier",
        "order": 90,
        "label": "Zero modifier",
        "category": "approach",
        "kind": "situation",
        "role": "modifier",
        "applies_to": {"step_kinds": ["gate"]},
        "calc": {
            "type": "threshold",
            "input": "world.weather",
            "op": "eq",
            "value": "fog",
            "delta": 0.2,
        },
        "explanation": "Adds nothing unless the weather is foggy.",
        "status": "live",
        "source": "human",
    })
    _add_factor(content, "approach", {
        "id": "zero_counter",
        "order": 100,
        "label": "Zero counter",
        "category": "approach",
        "kind": "situation",
        "role": "counter",
        "applies_to": {"step_kinds": ["gate"]},
        "calc": {
            "type": "threshold",
            "input": "world.weather",
            "op": "eq",
            "value": "fog",
            "delta": 0.2,
        },
        "explanation": "Subtracts nothing unless the weather is foggy.",
        "status": "live",
        "source": "human",
    })
    library = load_library(content)
    result = run_chain(_state(weather="rain"), _action(), 17, library=library)
    gate = _record(result, "noticed")

    assert {"source": "test_raise", "delta": 0.08} in gate.modifiers
    assert {"source": "test_lower", "delta": 0.03} in gate.counters
    assert all(item["source"] != "zero_modifier" for item in gate.modifiers)
    assert all(item["source"] != "zero_counter" for item in gate.counters)


def test_factors_only_apply_to_configured_step_kinds(tmp_path):
    content = _copy_content(tmp_path)
    _add_factor(content, "environment", _fog_factor())
    library = load_library(content)
    result = run_chain(_state(weather="fog"), _action(), 3, library=library)

    assert any(item["source"] == "fog" for item in _record(result, "noticed").modifiers)
    assert all(
        item["source"] != "fog"
        for item in _record(result, "encounter").modifiers
    )


def test_factor_log_order_is_by_order_then_id_not_file_order(tmp_path):
    content = _copy_content(tmp_path)
    factors = _read_json(_factor_file(content, "approach"))
    for factor_id in ("z_factor", "a_factor"):
        factors.append({
            "id": factor_id,
            "order": 25,
            "label": factor_id,
            "category": "approach",
            "kind": "situation",
            "role": "modifier",
            "applies_to": {"step_kinds": ["gate"]},
            "calc": {
                "type": "threshold",
                "input": "action.type",
                "op": "eq",
                "value": "search",
                "delta": 0.01,
            },
            "explanation": "Test ordering.",
            "status": "live",
            "source": "human",
        })
    _write_json(_factor_file(content, "approach"), factors)
    library = load_library(content)

    gate = _record(run_chain(_state(), _action(), 3, library=library), "noticed")
    sources = [item["source"] for item in gate.modifiers]

    assert sources.index("a_factor") < sources.index("z_factor")


def test_loaded_library_data_is_read_only():
    library = get_library()
    night = next(factor for factor in library.factors if factor.id == "night")

    with pytest.raises((AttributeError, TypeError)):
        night.calc.value[0] = 12
