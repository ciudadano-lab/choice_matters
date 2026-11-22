import pytest

from factor_library import _clock_time_band
from plan_features import PlanFeatures
from probability_config import APPROACH_FIT_BANDS, TIME_BANDS
from probability_engine import run_chain
from simulation_engine import StructuredAction


def _state(*, current_time="12:00", weather="clear"):
    state = {
        "currentDay": 1,
        "weather": weather,
        "players": [{
            "id": "player_1",
            "location": "loc_school",
            "inventory": ["medkit"],
            "entities": {"possessions": ["radio"]},
        }],
        "locations": [
            {"id": "loc_school", "name": "School", "discovered": True},
            {"id": "loc_hidden", "name": "Hidden", "discovered": False},
        ],
        "playerKnownWorld": {"locations": [{"id": "loc_school"}]},
        "resources": {"food": 2, "water": 0},
    }
    if current_time is not None:
        state["currentTime"] = current_time
    return state


def _run_plan(plan_features, *, current_time="12:00", weather="clear"):
    return run_chain(
        _state(current_time=current_time, weather=weather),
        StructuredAction(
            actionId="plan-factor-test",
            actionType="search",
            actor="player_1",
            target="loc_school",
            parameters={"policy": {"planFeatures": plan_features}},
        ),
        seed=7,
        plan_features=PlanFeatures.from_untrusted(plan_features),
    )


def _step(result, name):
    return next(step for step in result.steps if step.name == name)


def _sources(entries):
    return {entry["source"]: entry["delta"] for entry in entries}


@pytest.mark.parametrize(
    ("time_value", "expected_band"),
    [
        ("05:00", "dawn"),
        ("07:59", "dawn"),
        ("08:00", "day"),
        ("17:59", "day"),
        ("18:00", "dusk"),
        ("19:59", "dusk"),
        ("20:00", "night"),
        ("04:59", "night"),
    ],
)
def test_clock_time_bands_cover_the_defined_boundaries(time_value, expected_band):
    assert _clock_time_band(time_value) == expected_band


def test_invalid_or_missing_clock_has_no_time_band():
    assert _clock_time_band(None) is None
    assert _clock_time_band("bad") is None
    assert _clock_time_band("24:00") is None
    assert _clock_time_band("12:60") is None


def test_approach_fit_table_is_configured_for_all_approaches():
    assert set(TIME_BANDS) == {"dawn", "day", "dusk", "night"}
    assert set(APPROACH_FIT_BANDS) == {
        "stealth",
        "deception",
        "force",
        "speed",
        "diplomacy",
        "unspecified",
    }
    assert APPROACH_FIT_BANDS["stealth"]["extra_weather"] == frozenset({
        "rain",
        "storm",
    })
    assert APPROACH_FIT_BANDS["deception"]["extra_weather"] == frozenset({
        "rain",
        "storm",
    })


@pytest.mark.parametrize(
    ("approach", "timing", "current_time", "weather", "fits"),
    [
        ("stealth", "night", "12:00", "clear", True),
        ("stealth", "unspecified", "12:00", "clear", False),
        ("stealth", "unspecified", "12:00", "Rain", True),
        ("force", "night", "12:00", "clear", False),
        ("force", "unspecified", "12:00", "clear", True),
        ("deception", "day", "08:00", "storm", True),
        ("speed", "day", "17:59", "clear", True),
        ("diplomacy", "night", "12:00", "clear", True),
    ],
)
def test_plan_approach_fit_or_mismatch_fires_exactly_one_factor(
    approach,
    timing,
    current_time,
    weather,
    fits,
):
    result = _run_plan(
        {"approach": approach, "timing": timing},
        current_time=current_time,
        weather=weather,
    )
    gate = _step(result, "noticed")
    counters = _sources(gate.counters)
    modifiers = _sources(gate.modifiers)

    assert ("approach_fits" in counters) is fits
    assert ("approach_mismatch" in modifiers) is not fits
    if fits:
        assert counters["approach_fits"] == pytest.approx(0.04)
    else:
        assert modifiers["approach_mismatch"] == pytest.approx(0.04)


def test_unspecified_approach_or_unavailable_band_fires_neither_factor():
    for approach, current_time in (
        ("unspecified", "12:00"),
        ("not_an_approach", "12:00"),
        ("stealth", None),
        ("stealth", "not-a-clock"),
    ):
        result = _run_plan(
            {"approach": approach, "timing": "unspecified"},
            current_time=current_time,
        )
        gate = _step(result, "noticed")
        counters = _sources(gate.counters)
        modifiers = _sources(gate.modifiers)
        assert "approach_fits" not in counters
        assert "approach_mismatch" not in modifiers


def test_plan_ratios_count_known_targets_and_owned_items():
    result = _run_plan({
        "targetLocationIds": ["loc_school", "loc_hidden", "missing_location"],
        "itemsPlanned": ["food", "MEDKIT", "rocket launcher"],
        "hasFallback": True,
        "countermeasures": ["alternate route", "scout first", "radio"],
    })
    gate = _step(result, "noticed")
    encounter = _step(result, "encounter")
    gate_counters = _sources(gate.counters)
    gate_modifiers = _sources(gate.modifiers)
    encounter_counters = _sources(encounter.counters)

    assert gate_counters["known_target_locations"] == pytest.approx(0.04 / 3)
    assert gate_modifiers["unknown_target_locations"] == pytest.approx(0.05 * 2 / 3)
    assert gate_modifiers["missing_items"] == pytest.approx(0.04 / 3)
    assert "items_in_hand" not in gate_counters
    assert encounter_counters["items_in_hand"] == pytest.approx(0.03 * 2 / 3)
    assert encounter_counters["fallback_plan"] == pytest.approx(0.03)
    assert encounter_counters["countermeasures"] == pytest.approx(0.04)


def test_plan_factors_are_omitted_when_plan_data_is_unavailable():
    result = _run_plan({})
    assert all(
        not any(
            entry["source"] in {
                "known_target_locations",
                "unknown_target_locations",
                "items_in_hand",
                "missing_items",
                "fallback_plan",
                "countermeasures",
                "approach_fits",
                "approach_mismatch",
            }
            for entry in (*step.counters, *step.modifiers)
        )
        for step in result.steps
    )


def test_plan_inputs_deduplicate_repeated_padded_features_before_counting():
    single = _run_plan({
        "targetLocationIds": ["loc_school", "loc_hidden"],
        "itemsPlanned": ["food", "medical supplies"],
        "countermeasures": ["alternate route", "scout first"],
        "hasFallback": True,
    })
    repeated = _run_plan({
        "targetLocationIds": [
            " loc_school ", "LOC_SCHOOL", "loc_hidden", "loc_hidden",
        ] * 5,
        "itemsPlanned": [" Food ", "food", "MEDICAL SUPPLIES", "medical supplies"] * 5,
        "countermeasures": [" Alternate Route ", "alternate route", "SCOUT FIRST"] * 6,
        "hasFallback": True,
    })

    assert [step.to_dict() for step in repeated.steps] == [
        step.to_dict() for step in single.steps
    ]


def test_item_matching_normalizes_underscores_and_case_on_both_sides():
    state = _state()
    state["resources"]["medical_supplies"] = 1
    result = run_chain(
        state,
        StructuredAction(
            actionId="item-normalization",
            actionType="search",
            actor="player_1",
            target="loc_school",
        ),
        seed=7,
        plan_features=PlanFeatures.from_untrusted({
            "itemsPlanned": [" Medical Supplies ", " RADIO "],
        }),
    )

    assert _sources(_step(result, "encounter").counters)["items_in_hand"] == pytest.approx(0.03)


def test_discovered_location_counts_as_known_without_player_known_world_entry():
    state = _state()
    state["playerKnownWorld"] = {"locations": []}
    result = run_chain(
        state,
        StructuredAction(
            actionId="discovered-location",
            actionType="search",
            actor="player_1",
            target="loc_school",
        ),
        seed=7,
        plan_features=PlanFeatures.from_untrusted({
            "targetLocationIds": ["loc_school"],
        }),
    )

    assert _sources(_step(result, "noticed").counters)["known_target_locations"] == pytest.approx(0.04)


def test_missing_location_is_unknown_even_if_its_id_is_in_player_known_world():
    state = _state()
    state["playerKnownWorld"] = {"locations": [{"id": "missing_location"}]}
    result = run_chain(
        state,
        StructuredAction(
            actionId="missing-location",
            actionType="search",
            actor="player_1",
            target="loc_school",
        ),
        seed=7,
        plan_features=PlanFeatures.from_untrusted({
            "targetLocationIds": ["missing_location"],
        }),
    )
    gate = _step(result, "noticed")

    assert "known_target_locations" not in _sources(gate.counters)
    assert _sources(gate.modifiers)["unknown_target_locations"] == pytest.approx(0.05)
