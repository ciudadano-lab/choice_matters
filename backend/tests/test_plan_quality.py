import pytest

from factor_library import get_library
from plan_features import PlanFeatures
from probability_config import PLAN_MODIFIER_CAP
from probability_engine import run_chain
from simulation_engine import SimulationEngine, StructuredAction


def _state():
    return {
        "currentDay": 1,
        "currentTime": "12:00",
        "weather": "clear",
        "players": [{
            "id": "player_1",
            "location": "loc_known",
            "health": 100,
            "stamina": 100,
            "stress": 0,
            "inventory": ["medical_supplies"],
            "entities": {
                "companions": [],
                "possessions": [{"name": "radio"}],
            },
            "statusEffects": [],
        }],
        "locations": [
            {
                "id": "loc_known",
                "name": "Known",
                "dangerLevel": 1,
                "zombiePopulation": 0,
                "discovered": True,
            },
            {
                "id": "loc_unknown",
                "name": "Unknown",
                "dangerLevel": 1,
                "zombiePopulation": 0,
                "discovered": False,
            },
        ],
        "playerKnownWorld": {"locations": [{"id": "loc_known"}]},
        "resources": {"water": 1},
    }


def _action(*, action_type="search", parameters=None):
    return StructuredAction(
        actionId="plan-quality-test",
        actionType=action_type,
        actor="player_1",
        target="loc_known",
        parameters=parameters or {},
    )


def _good_plan():
    return PlanFeatures.from_untrusted({
        "targetLocationIds": ["loc_known"],
        "itemsPlanned": ["medical supplies", "radio", "water"],
        "timing": "night",
        "approach": "stealth",
        "countermeasures": ["alternate route", "scout first"],
        "hasFallback": True,
    })


def _bad_plan():
    return PlanFeatures.from_untrusted({
        "targetLocationIds": ["loc_unknown"],
        "itemsPlanned": ["rocket launcher"],
        "timing": "night",
        "approach": "force",
        "countermeasures": [],
        "hasFallback": False,
    })


def _signed_plan_effect(step, plan_sources):
    modifiers = sum(
        entry["delta"]
        for entry in step.modifiers
        if entry["source"] in plan_sources or entry["source"] == "plan_cap"
    )
    counters = sum(
        entry["delta"]
        for entry in step.counters
        if entry["source"] in plan_sources or entry["source"] == "plan_cap"
    )
    return modifiers - counters


def test_plan_cap_and_reasons_reflect_fired_plan_factors():
    library = get_library()
    plan_sources = {
        factor.log_source or factor.id
        for factor in library.factors
        if factor.kind == "plan"
    }
    good = run_chain(
        _state(),
        _action(),
        seed=7,
        plan_features=_good_plan(),
    )
    bad = run_chain(
        _state(),
        _action(),
        seed=7,
        plan_features=_bad_plan(),
    )
    good_encounter = next(step for step in good.steps if step.kind == "encounter")

    assert abs(_signed_plan_effect(good_encounter, plan_sources)) <= PLAN_MODIFIER_CAP
    assert any(entry["source"] == "plan_cap" for entry in good_encounter.modifiers)
    assert good.plan_quality == pytest.approx(1.0)
    assert bad.plan_quality < 0.0
    assert {reason["factor"] for reason in good.plan_reasons} == {
        "known_target_locations",
        "items_in_hand",
        "fallback_plan",
        "countermeasures",
        "approach_fits",
    }
    assert all(
        reason["effect"] == "helps" and reason["explanation"]
        for reason in good.plan_reasons
    )
    assert {reason["factor"] for reason in bad.plan_reasons} == {
        "unknown_target_locations",
        "missing_items",
        "approach_mismatch",
    }
    assert all(reason["effect"] == "hurts" for reason in bad.plan_reasons)


def test_plan_features_override_numeric_quality_but_absence_keeps_legacy_path():
    state = _state()
    action = _action()
    without_features = run_chain(state, action, seed=3, plan_quality=1.0)
    with_features = run_chain(
        state,
        action,
        seed=3,
        plan_quality=0.0,
        plan_features=_good_plan(),
    )
    ignored_numeric = run_chain(
        state,
        action,
        seed=3,
        plan_quality=-1.0,
        plan_features=_good_plan(),
    )

    assert without_features.plan_quality == 0.0
    assert any(
        entry["source"] == "plan"
        for step in without_features.steps
        for entry in step.modifiers
    )
    assert [step.to_dict() for step in with_features.steps] == [
        step.to_dict() for step in ignored_numeric.steps
    ]
    assert with_features.plan_quality == ignored_numeric.plan_quality


def test_simulation_exposes_plan_results_and_policy_persistence_reuses_features():
    engine = SimulationEngine(seed=5)
    world = _state()
    policy = {
        "actionType": "resource_policy",
        "focus": "scout quietly, stealth",
        "commitmentDays": 7,
        "startDay": 1,
        "pivotalTiming": "culmination",
        "planFeatures": _good_plan().to_dict(),
    }
    committed = engine.apply(
        world,
        _action(action_type="resource_policy", parameters={"policy": policy}),
    )
    stored_policy = committed.updatedWorldState["players"][0]["currentPolicy"]
    daily = engine.auto_resolve_policy_day(
        committed.updatedWorldState,
        "player_1",
        stored_policy,
        1,
    )

    assert stored_policy["planFeatures"] == policy["planFeatures"]
    assert daily.planQuality > 0.0
    assert any(
        entry["source"] == "known_target_locations"
        for step in daily.rollLog
        for entry in step["counters"]
    )
    assert daily.planReasons


def test_plan_quality_shifts_outcome_tier_and_zero_preserves_existing_tier():
    engine = SimulationEngine(seed=1)
    state = _state()
    action = _action()
    result = {}

    baseline = engine._resolve_outcome_tier(state, action, result, 20)
    helped = engine._resolve_outcome_tier(state, action, result, 20, 1.0)
    hurt = engine._resolve_outcome_tier(state, action, result, 20, -1.0)

    assert baseline == "success"
    assert helped == "critical_success"
    assert hurt == "partial_success"
