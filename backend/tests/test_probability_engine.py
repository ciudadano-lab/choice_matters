import copy

import pytest

from probability_config import CHANCE_MAX, CHANCE_MIN
from probability_engine import run_chain
from simulation_engine import SimulationEngine, StructuredAction


def build_state(
    *,
    day=1,
    time="08:00",
    weather="clear",
    danger=1,
    zombies=0,
    stress=0,
    status_effects=None,
    companions=None,
):
    return {
        "currentDay": day,
        "currentTime": time,
        "weather": weather,
        "players": [{
            "id": "player_1",
            "location": "school",
            "stress": stress,
            "statusEffects": status_effects or [],
            "entities": {"companions": companions or []},
        }],
        "locations": [{
            "id": "school",
            "name": "School",
            "dangerLevel": danger,
            "zombiePopulation": zombies,
        }],
        "resources": {},
    }


def build_action(action_type="search", *, action_id="search-1", parameters=None):
    return StructuredAction(
        actionId=action_id,
        actionType=action_type,
        actor="player_1",
        target="school",
        parameters=parameters or {},
    )


def steps_by_name(result):
    return {step.name: step for step in result.steps}


def test_same_state_action_and_seed_produce_identical_roll_logs():
    state = build_state()
    action = build_action()

    first = run_chain(state, action, seed=37)
    second = run_chain(copy.deepcopy(state), action, seed=37)

    assert [step.to_dict() for step in first.steps] == [
        step.to_dict() for step in second.steps
    ]


def test_serialized_roll_log_rounds_float_values_to_four_decimals():
    state = build_state(time="22:00", weather="rain", danger=3, zombies=10)
    steps = run_chain(state, build_action(), seed=2026).steps
    roll_log = [step.to_dict() for step in steps]

    assert roll_log[0]["modifiers"][0]["delta"] == 0.12
    assert roll_log[0]["chance"] == 0.41
    assert roll_log[0]["roll"] == round(roll_log[0]["roll"], 6)
    assert roll_log[1]["modifiers"][0]["delta"] == 0.09


def test_missing_state_fields_and_dictionary_players_use_safe_defaults():
    state = {
        "players": {"player_1": {"location": "school", "stress": 0}},
        "locations": [{"id": "school"}],
    }

    result = run_chain(state, build_action(), seed=17)

    assert [step.name for step in result.steps] == ["noticed", "encounter"]
    assert result.steps[0].chance == pytest.approx(0.20)
    assert result.steps[0].roll is not None


def test_different_seed_or_action_id_changes_deterministic_rolls():
    state = build_state()
    action = build_action()

    original_rolls = [step.roll for step in run_chain(state, action, 37).steps]
    changed_seed_rolls = [step.roll for step in run_chain(state, action, 38).steps]
    changed_action_rolls = [
        step.roll
        for step in run_chain(state, build_action(action_id="search-2"), 37).steps
    ]

    assert original_rolls != changed_seed_rolls
    assert original_rolls != changed_action_rolls


@pytest.mark.parametrize("danger", [1, 2, 3, 4])
def test_higher_location_danger_never_lowers_step_chances(danger):
    current = run_chain(build_state(danger=danger), build_action(), 23)
    higher = run_chain(build_state(danger=danger + 1), build_action(), 23)

    current_steps = steps_by_name(current)
    higher_steps = steps_by_name(higher)
    assert higher_steps["noticed"].chance >= current_steps["noticed"].chance
    assert higher_steps["encounter"].chance >= current_steps["encounter"].chance


def test_night_and_bad_weather_increase_gate_chance():
    action = build_action()
    daytime = steps_by_name(run_chain(build_state(time="12:00"), action, 1))["noticed"]
    nighttime = steps_by_name(run_chain(build_state(time="22:00"), action, 1))["noticed"]
    clear = steps_by_name(run_chain(build_state(weather="clear"), action, 1))["noticed"]
    rainy = steps_by_name(run_chain(build_state(weather="rain"), action, 1))["noticed"]
    stormy = steps_by_name(run_chain(build_state(weather="storm"), action, 1))["noticed"]

    assert nighttime.chance > daytime.chance
    assert rainy.chance > clear.chance
    assert stormy.chance > clear.chance


def test_gate_and_encounter_modifiers_use_zombies_loud_actions_status_and_stress():
    search = build_action()
    build = build_action("build")
    base_gate = steps_by_name(run_chain(
        build_state(danger=0), search, 1
    ))["noticed"].chance
    zombie_gate = steps_by_name(run_chain(
        build_state(danger=0, zombies=10), search, 1
    ))["noticed"].chance
    loud_gate = steps_by_name(run_chain(
        build_state(danger=0), build, 1
    ))["noise"].chance
    base_encounter = steps_by_name(run_chain(
        build_state(danger=0), search, 1
    ))["encounter"].chance
    status_encounter = steps_by_name(run_chain(
        build_state(
            danger=0,
            status_effects=[{"id": "effect-1", "expiresDay": 2}],
        ),
        search,
        1,
    ))["encounter"].chance
    stress_encounter = steps_by_name(run_chain(
        build_state(danger=0, stress=50), search, 1
    ))["encounter"].chance

    assert zombie_gate - base_gate == pytest.approx(0.04)
    assert loud_gate - base_gate == pytest.approx(0.15)
    assert status_encounter - base_encounter == pytest.approx(0.10)
    assert stress_encounter - base_encounter == pytest.approx(0.05)


def test_stealth_reduces_gate_and_companions_reduce_encounter_chance():
    action = build_action()
    regular = steps_by_name(run_chain(build_state(danger=1), action, 1))
    stealth = steps_by_name(run_chain(
        build_state(danger=1),
        build_action(parameters={"policy": {"focus": "Stay quiet and stealthy"}}),
        1,
    ))
    no_companions = steps_by_name(run_chain(build_state(danger=1), action, 1))
    one_companion = steps_by_name(run_chain(
        build_state(danger=1, companions=[{"name": "A"}]), action, 1
    ))
    three_companions = steps_by_name(run_chain(
        build_state(danger=1, companions=[{"name": "A"}, {"name": "B"}, {"name": "C"}]),
        action,
        1,
    ))
    extra_companions = steps_by_name(run_chain(
        build_state(
            danger=1,
            companions=[{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}],
        ),
        action,
        1,
    ))

    assert regular["noticed"].chance - stealth["noticed"].chance == pytest.approx(0.10)
    assert no_companions["encounter"].chance - one_companion["encounter"].chance == pytest.approx(0.05)
    assert no_companions["encounter"].chance - three_companions["encounter"].chance == pytest.approx(0.12)
    assert extra_companions["encounter"].chance == three_companions["encounter"].chance


def test_chances_are_clamped_under_extreme_modifiers():
    state = build_state(
        time="23:00",
        weather="storm",
        danger=5,
        zombies=500,
        stress=1000,
        status_effects=[{"id": "effect-1", "expiresDay": 2}],
    )
    result = run_chain(state, build_action("build"), seed=9)

    assert all(CHANCE_MIN <= step.chance <= CHANCE_MAX for step in result.steps)
    assert steps_by_name(result)["encounter"].chance == CHANCE_MAX


def test_untriggered_gate_skips_encounter_without_a_roll():
    state = build_state()
    action = build_action()
    seed = next(
        candidate
        for candidate in range(100)
        if not run_chain(state, action, candidate).steps[0].triggered
    )

    result = run_chain(state, action, seed)
    encounter = steps_by_name(result)["encounter"]

    assert encounter.status == "skipped"
    assert encounter.roll is None
    assert encounter.triggered is False
    assert result.encounter_occurred is False
    assert result.encounter_probability == 0.0


def test_interrupt_step_is_set_only_when_interrupting_step_triggers():
    action = build_action("attack", action_id="attack-1")
    triggered_seed = next(
        candidate
        for candidate in range(100)
        if run_chain(build_state(), action, candidate).encounter_occurred
    )
    avoided_seed = next(
        candidate
        for candidate in range(100)
        if not run_chain(build_state(), action, candidate).encounter_occurred
    )

    assert run_chain(build_state(), action, triggered_seed).interrupt_step == "encounter"
    assert run_chain(build_state(), action, avoided_seed).interrupt_step is None


@pytest.mark.parametrize("action_type", ["resource_policy", "failed_attempt"])
def test_non_chain_actions_have_no_roll_log_or_interrupt(action_type):
    action = build_action(action_type)
    result = SimulationEngine(seed=5).apply(build_state(), action)

    assert result.rollLog == []
    assert result.interruptStep is None


def test_rejected_action_has_no_roll_log_or_interrupt():
    state = build_state()
    state["players"].append({
        "id": "player_2",
        "entities": {"companions": [], "possessions": []},
    })
    action = StructuredAction(
        actionId="invalid-claim",
        actionType="attack",
        actor="player_1",
        target="player_2",
        parameters={
            "targetEntity": {
                "owner": "player_2",
                "type": "companion",
                "name": "Unknown",
            },
        },
    )

    result = SimulationEngine(seed=5).apply(state, action)

    assert result.rejected is True
    assert result.rollLog == []
    assert result.interruptStep is None


def test_plan_quality_changes_chance_and_zero_adds_no_plan_modifier():
    state = build_state(danger=5)
    action = build_action()
    neutral = steps_by_name(run_chain(state, action, 3))
    good = steps_by_name(run_chain(state, action, 3, plan_quality=1.0))
    bad = steps_by_name(run_chain(state, action, 3, plan_quality=-1.0))

    assert neutral["noticed"].chance - good["noticed"].chance == pytest.approx(0.27)
    assert bad["noticed"].chance - neutral["noticed"].chance == pytest.approx(0.30)
    assert neutral["encounter"].chance - good["encounter"].chance == pytest.approx(0.24)
    assert bad["encounter"].chance - neutral["encounter"].chance == pytest.approx(0.30)
    assert all(
        modifier["source"] != "plan"
        for step in neutral.values()
        for modifier in step.modifiers
    )


def test_apply_passes_action_plan_quality_to_probability_chain():
    result = SimulationEngine(seed=3).apply(
        build_state(danger=5),
        build_action(parameters={"planQuality": 1.0}),
    )

    assert all(
        any(modifier["source"] == "plan" for modifier in step["modifiers"])
        for step in result.rollLog
    )


def test_apply_does_not_mutate_input_state():
    state = build_state(danger=3, zombies=8)
    original_state = copy.deepcopy(state)

    SimulationEngine(seed=11).apply(state, build_action())

    assert state == original_state


def test_physically_impossible_resolver_discards_precomputed_chain(monkeypatch):
    resolve_search = SimulationEngine._resolve_search

    def resolve_impossible(self, state, action, rng):
        result = resolve_search(self, state, action, rng)
        result["physicallyImpossible"] = True
        result["impossibleReason"] = "test impossible action"
        return result

    monkeypatch.setattr(SimulationEngine, "_resolve_search", resolve_impossible)
    result = SimulationEngine(seed=11).apply(build_state(), build_action())

    assert result.physicallyImpossible is True
    assert result.rollLog == []
    assert result.interruptStep is None
    assert result.encounterProbability == 0.0
    assert result.encounterOccurred is False
    assert result.encounterDetails["outcome"] == "not_applicable"


def test_apply_encounter_fields_match_the_encounter_roll_log_step():
    result = SimulationEngine(seed=2).apply(build_state(danger=3), build_action())
    encounter = next(step for step in result.rollLog if step["kind"] == "encounter")

    assert result.encounterProbability == (
        encounter["chance"] if encounter["status"] == "rolled" else 0.0
    )
    assert result.encounterOccurred is encounter["triggered"]
    assert result.encounterDetails["roll"] == encounter["roll"]
    assert result.encounterDetails["outcome"] == (
        "occurred"
        if encounter["triggered"]
        else "avoided"
        if encounter["status"] == "rolled"
        else "not_applicable"
    )
