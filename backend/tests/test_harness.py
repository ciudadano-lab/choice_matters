import hashlib
import math
import random
from pathlib import Path

from factor_library import get_library
from probability_engine import run_chain
from simulation_engine import StructuredAction
from world_state import generate_initial_world_state
from tools.harness import (
    BOT_NAMES,
    _check_determinism,
    _known_location_ids,
    _owned_item_ids,
    _policy_for,
    _print_economy_health,
    _print_encounter_outcomes,
    _print_impact_checks,
    _print_play_report,
    _run_game,
    _write_play_outputs,
    _write_world_sim_acceptance,
    build_sweep_rows,
    build_plan_sweep_rows,
    run_play,
    run_world_sim,
    run_world_sim_acceptance,
    run_world_sim_scenarios,
)


def _content_digest() -> str:
    content_dir = Path(__file__).resolve().parents[1] / "content"
    digest = hashlib.sha256()
    for path in sorted(content_dir.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(content_dir).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_tiny_play_completes_for_every_bot_and_player_count():
    report, rows = run_play(
        games=1,
        days=2,
        players=[1, 2, 3],
        bots=list(BOT_NAMES),
        replan=2,
        workers=1,
        lite=True,
    )

    assert len(report["groups"]) == len(BOT_NAMES) * 3
    assert len(rows) == len(BOT_NAMES) * sum([1, 2, 3]) * 2
    assert all(row["action_type"] for row in rows)
    assert all(group["player_days"] == 2 * group["games"] * group["players"] for group in report["groups"])
    assert all(sum(group["death_causes"].values()) == group["deaths"] for group in report["groups"])
    assert all(
        set(group["pause_rate_by_slot"]) == {"dawn", "day", "dusk", "night"}
        for group in report["groups"]
    )


def test_economy_health_report_shows_death_causes_and_resource_zero_rates(capsys):
    _print_economy_health({
        "groups": [{
            "bot": "careful",
            "players": 1,
            "deaths": 2,
            "death_causes": {
                "starvation": 1,
                "dehydration": 0,
                "combat/injury": 1,
                "other": 0,
            },
            "player_days": 10,
            "food_zero_player_days": 2,
            "water_zero_player_days": 3,
            "resource_zero_player_days": 4,
        }],
    })
    output = capsys.readouterr().out

    assert "starvation" in output
    assert "careful" in output
    assert "20.0" in output
    assert "30.0" in output


def test_idle_bot_dies_from_depletion_between_days_twenty_and_forty():
    game = _run_game({
        "seed": 1,
        "bot": "idle",
        "players": 1,
        "days": 40,
        "replan": 1,
        "lite": True,
    })
    death_days = [day["day"] for day in game["days"] if day["deaths"]]
    starvation_deaths = sum(
        day["death_causes"]["starvation"] for day in game["days"]
    )
    dehydration_deaths = sum(
        day["death_causes"]["dehydration"] for day in game["days"]
    )

    assert death_days
    assert 20 <= min(death_days) <= 40
    assert starvation_deaths + dehydration_deaths > 0


def test_tiny_play_report_json_is_byte_deterministic(tmp_path):
    kwargs = {
        "games": 3,
        "days": 5,
        "players": [1],
        "bots": ["careful"],
        "replan": 7,
        "workers": 1,
        "lite": True,
    }
    report_a, rows_a = run_play(**kwargs)
    report_b, rows_b = run_play(**kwargs)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_play_outputs(first, report_a, rows_a)
    _write_play_outputs(second, report_b, rows_b)

    assert (first / "report.json").read_bytes() == (second / "report.json").read_bytes()


def test_world_pause_rate_bounds_player_interrupt_rate():
    report, _ = run_play(
        games=3,
        days=10,
        players=[1, 3],
        bots=["careful"],
        replan=7,
        workers=1,
        lite=True,
    )
    groups = {(item["players"], item["windows"][0]["window"]): item["windows"][0] for item in report["groups"]}

    for player_count in (1, 3):
        metrics = groups[(player_count, 1)]
        if player_count == 1:
            assert metrics["world_pause_rate"] == metrics["per_player_interrupt_rate"]
        else:
            assert metrics["world_pause_rate"] >= metrics["per_player_interrupt_rate"]


def test_world_sim_reports_settlement_history_and_is_deterministic():
    first = run_world_sim(days=14, seeds=2)
    second = run_world_sim(days=14, seeds=2)

    assert first == second
    assert len(first["runs"]) == 2
    assert all(len(run["daily"]) == 14 for run in first["runs"])
    assert all(
        {"population", "stores", "status_distribution"} <= set(snapshot)
        for run in first["runs"]
        for snapshot in run["daily"]
    )
    assert set(first["summary"]["decision_influence"]) == {
        "hostile_disposition",
        "friendly_disposition",
        "attention_pressure",
    }
    assert all(
        values["decisions"] > 0
        for values in first["summary"]["decision_influence"].values()
    )


def test_world_sim_scenarios_report_depleted_and_high_unrest_paths():
    report = run_world_sim_scenarios(days=20, seeds=2)

    assert set(report["scenarios"]) == {
        "baseline",
        "depleted_stores",
        "mild_shortage",
        "high_unrest",
    }
    assert all(
        sum(statuses.values()) == 6
        for statuses in report["scenarios"].values()
    )


def test_world_sim_acceptance_report_builds_and_saves_pass_warn_table(tmp_path):
    report = run_world_sim_acceptance(days=2, seeds=2)

    assert set(report["scenarios"]) == {
        "baseline",
        "depleted_stores",
        "mild_shortage",
        "high_unrest",
    }
    assert report["configuration"] == {
        "days": 2,
        "seeds": 2,
        "scenarios": [
            "baseline",
            "depleted_stores",
            "mild_shortage",
            "high_unrest",
        ],
    }
    assert report["summary_table"]
    assert all(row["status"] in {"PASS", "WARN"} for row in report["summary_table"])

    _write_world_sim_acceptance(tmp_path, report)
    assert (tmp_path / "world_sim_acceptance.csv").is_file()
    assert (tmp_path / "world_sim_acceptance.json").is_file()
    assert "scenario,target,observed,criterion,status" in (
        tmp_path / "world_sim_acceptance.csv"
    ).read_text(encoding="utf-8")


def test_encounter_outcome_target_checks_report_prepared_and_unprepared_bands(capsys):
    report = {
        "groups": [
            {
                "bot": "careful",
                "players": 1,
                "games": 10,
                "encounters": 20,
                "encounter_outcome_counts": {
                    "evaded": 7,
                    "close_call": 7,
                    "hurt": 5,
                    "serious": 1,
                },
                "injuries_per_encounter": 0.3,
                "deaths": 0,
                "deaths_before_day_15": 0,
            },
            {
                "bot": "careful_planned",
                "players": 1,
                "games": 10,
                "encounters": 20,
                "encounter_outcome_counts": {
                    "evaded": 9,
                    "close_call": 7,
                    "hurt": 4,
                    "serious": 0,
                },
                "injuries_per_encounter": 0.2,
                "deaths": 0,
                "deaths_before_day_15": 0,
            },
        ],
    }

    _print_encounter_outcomes(report)
    output = capsys.readouterr().out

    assert "careful K=1: hurt+serious PASS (30.0% target 25%-40%)" in output
    assert "careful_planned K=1: hurt+serious PASS (20.0% vs careful 30.0%)" in output
    assert "deaths before day 15 PASS" in output


def test_sweep_covers_every_factor_combination_and_chain_probability():
    library = get_library()
    rows = build_sweep_rows(library)

    assert len(rows) == len(library.actions) * 6 * 2 * 2 * 3 * 2
    row = rows[0]
    template = library.templates[library.actions[row["action_type"]]]
    interrupt_step = next(step for step in template.steps if step.interrupt)
    state = {
        "currentDay": 1,
        "currentTime": row["time"],
        "weather": row["weather"],
        "players": [{
            "id": "player_1",
            "location": "sweep-location",
            "stress": 0,
            "statusEffects": [],
            "entities": {"companions": []},
        }],
        "locations": [{
            "id": "sweep-location",
            "name": "Sweep Location",
            "dangerLevel": row["danger_level"],
            "zombiePopulation": 0,
        }],
    }
    chain = run_chain(
        state,
        StructuredAction(
            actionId="harness-sweep",
            actionType=row["action_type"],
            actor="player_1",
            target="sweep-location",
            parameters={"policy": {"focus": "quiet stealth" if row["stealth"] else ""}},
        ),
        seed=1,
        plan_quality=row["plan_quality"],
        library=library,
    )
    logged_steps = [step.to_dict() for step in chain.steps]
    chance_by_name = {step["name"]: step["chance"] for step in logged_steps}
    path_names = {interrupt_step.name}
    current_step = interrupt_step
    while current_step.requires is not None:
        current_step = next(step for step in template.steps if step.name == current_step.requires)
        path_names.add(current_step.name)
    expected_probability = math.prod(
        chance_by_name[step.name]
        for step in template.steps
        if step.name in path_names
    )

    assert row["p_interrupt"] == expected_probability
    assert row["expected_days_between_interrupts"] == 1 / row["p_interrupt"]
    assert interrupt_step.interrupt


def test_search_plan_quality_changes_analytic_interrupt_wait():
    rows = build_sweep_rows()
    search = {
        row["plan_quality"]: row["expected_days_between_interrupts"]
        for row in rows
        if row["action_type"] == "search"
        and row["danger_level"] == 2
        and row["time"] == "12:00"
        and row["weather"] == "clear"
        and row["stealth"] is False
    }

    assert search[1] > search[0] > search[-1]


def test_search_plan_feature_pivot_orders_good_and_bad_plans():
    rows = build_plan_sweep_rows()
    search = {
        row["scenario"]: row["expected_days_between_interrupts"]
        for row in rows
        if row["danger_level"] == 2
    }

    assert search["good plan"] > search["none"] > search["bad plan"]


def test_random_bot_does_not_change_global_random_state():
    random.seed(123)
    expected_next = random.random()
    random.seed(123)

    _run_game({
        "seed": 1,
        "bot": "random",
        "players": 1,
        "days": 4,
        "replan": 1,
        "lite": True,
    })

    assert random.random() == expected_next


def test_harness_does_not_modify_factor_content():
    before = _content_digest()
    run_play(
        games=1,
        days=2,
        players=[1],
        bots=["forager"],
        replan=2,
        workers=1,
        lite=True,
    )
    assert _content_digest() == before


def test_check_determinism_passes_for_first_three_games():
    _check_determinism(
        games=3,
        days=5,
        players=[1],
        bots=["careful"],
        replan=7,
        workers=1,
        lite=True,
    )


def test_plan_variants_keep_careful_policy_sequence_and_build_plan_features():
    world = generate_initial_world_state([], 41).to_dict()
    player = world["players"][0]
    player["inventory"] = ["bandage", {"id": "empty", "quantity": 0}]
    player["possessions"] = [{"name": "radio", "quantity": 1}]
    player["entities"]["possessions"] = [{"name": "knife"}]
    world["resources"] = {"food": 4, "water": 0}
    random_source = random.Random(41)

    for day_index in (0, 7, 14, 21):
        baseline = _policy_for(
            "careful", day_index, day_index + 1, 7, random_source, world, player
        )
        for bot in ("careful_planned", "careful_sloppy"):
            variant = _policy_for(
                bot, day_index, day_index + 1, 7, random_source, world, player
            )
            assert {
                key: value
                for key, value in variant.items()
                if key != "planFeatures"
            } == baseline

    planned = _policy_for(
        "careful_planned", 0, 1, 7, random_source, world, player
    )["planFeatures"]
    expected_known_ids = {
        location["id"]
        for location in world["playerKnownWorld"]["locations"]
    }
    expected_known_ids.update(
        location["id"]
        for location in world["locations"]
        if location.get("discovered") is True
    )
    assert planned["targetLocationIds"] == sorted(expected_known_ids)[:5]
    assert planned["itemsPlanned"] == sorted(_owned_item_ids(world, player))[:6]
    assert set(planned["itemsPlanned"]) == set(_owned_item_ids(world, player))
    assert planned["hasFallback"] is True
    assert len(planned["countermeasures"]) == 2
    assert planned["approach"] == "stealth"
    assert planned["timing"] == "night"

    sloppy = _policy_for(
        "careful_sloppy", 0, 1, 7, random_source, world, player
    )["planFeatures"]
    world_location_ids = {
        location["id"] for location in world["locations"]
    }
    if sloppy["targetLocationIds"][0] in world_location_ids:
        target = next(
            location
            for location in world["locations"]
            if location["id"] == sloppy["targetLocationIds"][0]
        )
        assert target["discovered"] is False
        assert target["id"] not in _known_location_ids(world, player)
    assert set(sloppy["itemsPlanned"]).isdisjoint(
        _owned_item_ids(world, player)
    )
    assert sloppy["hasFallback"] is False
    assert sloppy["countermeasures"] == []
    assert sloppy["approach"] == "force"
    assert sloppy["timing"] == "night"


def test_impact_report_uses_same_k_baseline_and_pause_rate_bands(capsys):
    def group(bot, players, gap, pause_rate):
        return {
            "bot": bot,
            "players": players,
            "games": 10,
            "windows": [],
            "target_days_1_20": {
                "mean_days_between_world_pauses": gap,
                "world_pause_rate": pause_rate,
                "zero_pause_games_percent": 0.0,
                "measured_days": 20,
                "pause_days": int(pause_rate * 200),
            },
        }

    report = {
        "groups": [
            group("careful", 1, 2.0, 0.50),
            group("careful", 3, 4.0, 0.25),
            group("careful", 2, 3.0, 0.50),
            group("careful_planned", 1, 2.6, 0.125),
            group("careful_planned", 3, 4.8, 0.15),
            group("careful_planned", 2, 2.0, 0.40),
            group("careful_sloppy", 1, 1.4, 0.85),
            group("careful_sloppy", 3, 3.2, 0.875),
            group("careful_sloppy", 2, 2.0, 0.60),
        ]
    }
    _print_play_report(report, elapsed=1.0, game_count=60)
    output = capsys.readouterr().out
    target_check = output.split("Target check", 1)[1].split(
        "Plan impact check", 1
    )[0]
    impact_check = output.split("Plan impact check", 1)[1]

    assert "careful K=1" in target_check
    assert "careful K=3" in target_check
    assert "careful_planned" not in target_check
    assert "careful_sloppy" not in target_check
    assert "PASS careful_planned K=1: pause-rate ratio 0.25x (target 0.25-0.60x)" in impact_check
    assert "PASS careful_planned K=3: pause-rate ratio 0.60x (target 0.25-0.60x)" in impact_check
    assert "WARN careful_planned K=2: pause-rate ratio 0.80x (target 0.25-0.60x)" in impact_check
    assert "PASS careful_sloppy K=1: pause-rate ratio 1.70x (target 1.70-3.50x)" in impact_check
    assert "PASS careful_sloppy K=3: pause-rate ratio 3.50x (target 1.70-3.50x)" in impact_check
    assert "WARN careful_sloppy K=2: pause-rate ratio 1.20x (target 1.70-3.50x)" in impact_check
    assert "world_pause_rate 0.125 vs 0.500" in impact_check
    assert impact_check.count("(small sample)") == 6


def test_impact_report_warns_when_no_plan_variant_pauses_are_observed(capsys):
    def group(bot, gap, pause_rate):
        return {
            "bot": bot,
            "players": 1,
            "games": 20,
            "target_days_1_20": {
                "mean_days_between_world_pauses": gap,
                "world_pause_rate": pause_rate,
                "measured_days": 20,
            },
        }

    _print_impact_checks({
        "groups": [
            group("careful", 5.0, 0.20),
            group("careful_planned", None, 0.0),
            group("careful_sloppy", 2.5, 0.40),
        ]
    })
    output = capsys.readouterr().out

    assert (
        "WARN careful_planned K=1: pause-rate ratio no pauses observed "
        "(target 0.25-0.60x)" in output
    )
    assert "PASS careful_sloppy K=1: pause-rate ratio 2.00x" in output
