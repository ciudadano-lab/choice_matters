import copy
import json

import world_tick as world_tick_module
from encounter_resolution import EncounterOutcome, resolve_pending_encounter
from world_state import generate_initial_world_state
from world_tick import PausedRun, PendingEncounter, WorldEventQueue, WorldTick


def _world(
    seed: int,
    *,
    player_count: int = 1,
    timing: str | None = "day",
    commitment_days: int = 1,
    outdoors: bool = False,
) -> dict:
    world = generate_initial_world_state([], seed).to_dict()
    world["players"] = world["players"][:player_count]
    if outdoors:
        world["locations"].append({
            "id": "test_outdoors",
            "name": "Outdoors",
            "type": "street",
            "dangerLevel": 5,
            "zombiePopulation": 20,
        })
    for player in world["players"]:
        player["location"] = (
            "test_outdoors" if outdoors else world["locations"][0]["id"]
        )
        if timing is None:
            player.pop("currentPolicy", None)
            continue
        player["currentPolicy"] = {
            "actionType": "resource_policy",
            "focus": "search for supplies",
            "commitmentDays": commitment_days,
            "planFeatures": {"timing": timing},
        }
    if not outdoors:
        world["locations"][0]["dangerLevel"] = 5
    return world


def _canonical_world(world):
    canonical = world.to_dict()
    canonical["players"] = sorted(
        canonical["players"],
        key=lambda player: str(player.get("id", "")),
    )
    return canonical


def test_json_round_trip_pause_resume_matches_straight_through_period():
    initial = _world(8, commitment_days=3)
    straight_tick = WorldTick(seed=8)
    straight_world = straight_tick.advance(
        initial,
        WorldEventQueue(),
        hours=72,
    )

    resumed_tick = WorldTick(seed=8)
    resumed_world, paused = resumed_tick.advance_period_until_interrupt(
        copy.deepcopy(initial),
        WorldEventQueue(),
        hours=72,
    )
    pauses = 0
    while paused is not None:
        pauses += 1
        assert pauses <= 12
        restored = PausedRun.from_dict(
            json.loads(json.dumps(paused.to_dict()))
        )
        assert restored == paused
        choices = {
            item.player_id: "hold"
            for item in restored.pending_encounters
        }
        resumed_world, paused = resumed_tick.resume_period(
            resumed_world,
            WorldEventQueue(),
            restored,
            choices,
        )

    assert _canonical_world(resumed_world) == _canonical_world(straight_world)
    assert resumed_tick.last_period_state_hashes == straight_tick.last_period_state_hashes
    assert len(resumed_tick.last_period_state_hashes) == 3
    assert resumed_tick.last_period_outcome == straight_tick.last_period_outcome


def test_multiple_same_slot_interrupts_and_player_order_independence():
    initial = _world(8, player_count=2)
    reversed_players = copy.deepcopy(initial)
    reversed_players["players"].reverse()
    results = []

    for state in (initial, reversed_players):
        tick = WorldTick(seed=8)
        world, paused = tick.advance_period_until_interrupt(
            state,
            WorldEventQueue(),
            hours=24,
        )
        assert paused is not None
        assert paused.slot == "day"
        assert [item.player_id for item in paused.pending_encounters] == [
            "player_1",
            "player_2",
        ]
        world, paused = tick.resume_period(
            world,
            WorldEventQueue(),
            paused,
            {"player_1": "hold", "player_2": "hold"},
        )
        assert paused is None
        results.append((tick, world))

    assert _canonical_world(results[0][1]) == _canonical_world(results[1][1])
    assert results[0][0].last_period_outcome == results[1][0].last_period_outcome


def test_night_ambient_interrupt_pauses_before_slot_advance():
    tick = WorldTick(seed=8)
    world, paused = tick.advance_period_until_interrupt(
        _world(8, timing=None, outdoors=True),
        WorldEventQueue(),
        hours=24,
    )

    assert paused is not None
    assert paused.slot == "night"
    assert paused.resume_cursor == "dawn"
    assert all(item.is_ambient for item in paused.pending_encounters)
    assert world.currentTime == "22:00"
    assert world.currentDay == 1


def test_pending_encounter_missing_actor_is_skipped_with_reason():
    pending = {
        "playerId": "missing_player",
        "actionId": "missing-action",
        "actionType": "search",
        "target": "school",
        "parameters": {},
        "day": 1,
        "time": "12:00",
        "seed": 3,
        "planQuality": 0.0,
    }

    outcome, reason = resolve_pending_encounter(
        pending,
        {"players": [], "locations": []},
    )

    assert outcome is None
    assert reason == "actor_missing"


def test_last_slot_interrupt_resumes_rollover_and_finishes_period():
    tick = WorldTick(seed=5)
    world, paused = tick.advance_period_until_interrupt(
        _world(5, timing="night"),
        WorldEventQueue(),
        hours=24,
    )

    assert paused is not None
    assert paused.slot == "night"
    assert paused.remaining_days == 1
    assert paused.days_resolved == 0

    world, paused = WorldTick(seed=5).resume_period(
        world,
        WorldEventQueue(),
        PausedRun.from_dict(json.loads(json.dumps(paused.to_dict()))),
        {"player_1": "hold"},
    )

    assert paused is None
    assert world.currentDay == 2
    assert world.currentTime == "06:00"


def test_same_slot_resolution_uses_current_world_and_skips_a_newly_dead_actor(
    monkeypatch,
):
    tick = WorldTick(seed=8)
    world, paused = tick.advance_period_until_interrupt(
        _world(8, player_count=2),
        WorldEventQueue(),
        hours=24,
    )
    assert paused is not None

    first = paused.pending_encounters[0]
    duplicate = PendingEncounter.from_dict(first.to_dict())
    duplicate.action_id = "ambient-player_1-1-night"
    duplicate.action_type = "ambient_noise"
    duplicate.is_ambient = True
    duplicate.record_index = len(paused.current_slot_results)
    paused.pending_encounters.insert(1, duplicate)
    paused.current_slot_results.append({
        "day": paused.day,
        "slot": paused.slot,
        "slotTime": "12:00",
        "playerId": "player_1",
        "actionType": "ambient_noise",
        "outcomeTier": "success",
        "rollLog": [],
        "interruptStep": "encounter",
        "encounterOccurred": True,
        "encounterOutcome": None,
        "injuries": 0,
        "isAmbient": True,
    })

    real_resolver = world_tick_module.resolve_pending_encounter
    first_resolution = True

    def kill_first_actor(pending, current_state, *, chosen_option=None):
        nonlocal first_resolution
        if pending["playerId"] == "player_1" and first_resolution:
            first_resolution = False
            return EncounterOutcome(
                outcome="serious",
                roll=0.99,
                probabilities={
                    "evaded": 0.3,
                    "close_call": 0.3,
                    "hurt": 0.3,
                    "serious": 0.1,
                },
                modifiers=[],
                reasons=[],
                player_changes={"health": -100},
                status_effect=None,
                resource_changes={},
            ), None
        return real_resolver(
            pending,
            current_state,
            chosen_option=chosen_option,
        )

    monkeypatch.setattr(
        world_tick_module,
        "resolve_pending_encounter",
        kill_first_actor,
    )
    world, paused = tick.resume_period(
        world,
        WorldEventQueue(),
        paused,
        {"player_1": "hold", "player_2": "hold"},
    )

    assert paused is None
    player_states = {player["id"]: player for player in world.players}
    assert player_states["player_1"]["health"] == 0
    assert player_states["player_2"]["health"] > 0
    records = tick.last_policy_day_outcomes
    skipped = next(
        record for record in records
        if record.get("isAmbient") and record.get("playerId") == "player_1"
    )
    assert skipped["encounterOutcome"] == {
        "skipped": True,
        "reason": "actor_dead",
    }

