import copy

import pytest
from pydantic import ValidationError

from world_content import FactionSeed, load_world_content
from world_state import WorldState, generate_initial_world_state, normalize_settlement_record


def test_world_content_is_strict_and_references_are_valid():
    content = load_world_content()

    assert len(content.factions.factions) == 3
    assert len(content.settlements.settlements) == 3
    assert len(content.npcs.npcs) == 8
    assert content.faction_rules.faction_disposition_scale_factor == 5
    assert content.faction_rules.surface_faction_events_to_active_events is True
    assert content.faction_rules.decisions.weighted_draw_k == 2
    assert content.faction_rules.max_factions == 8
    with pytest.raises(ValidationError):
        FactionSeed.model_validate({
            "id": "faction_extra",
            "name": "Unexpected",
            "legacy_ideology": "survival",
            "ideology_tags": [],
            "goals": [],
            "strength": 1,
            "roaming_population": 0,
            "military_strength": 1,
            "resources": {},
            "territory_types": [],
            "initial_dispositions": {},
            "initial_stances": {},
            "player_disposition": 0,
            "leader_npc_id": "npc_missing",
            "council_seats": 0,
            "unexpected": True,
        })


def test_new_world_seeding_is_deterministic_and_references_are_resolved():
    first = generate_initial_world_state([], seed=38).to_dict()
    second = generate_initial_world_state([], seed=38).to_dict()

    assert first == second
    assert len(first["factions"]) == 3
    assert len(first["settlements"]) == 3
    assert len(first["npcs"]) == 8
    assert set(first["npcs"]) == {
        npc["id"] for npc in first["npcs"].values()
    }
    location_ids = {location["id"] for location in first["locations"]}
    faction_ids = {faction["id"] for faction in first["factions"]}
    for settlement in first["settlements"]:
        assert settlement["location_id"] in location_ids
        assert settlement["location"] == settlement["location_id"]
        assert settlement["tier"] == 1
        assert settlement["faction_id"] in faction_ids
        assert settlement["leader_npc_id"] in first["npcs"]
    for faction in first["factions"]:
        assert set(faction["player_dispositions"]) == {
            player["id"] for player in first["players"]
        }
        assert set(faction["relationships"]) == faction_ids - {faction["id"]}


def test_legacy_room_loads_and_normalizes_settlements_without_converting_npcs():
    old_npc = {
        "id": "npc_player_only",
        "name": "Legacy Character",
        "traits": ["resourceful"],
    }
    old_settlement = {
        "id": "old_settlement",
        "name": "Old School",
        "location": "school",
        "coordinates": {"x": 2, "y": 3},
        "population": 42,
        "tier": 99,
        "foundedDay": 4,
        "founderId": "player_1",
    }
    raw = {
        "players": [{
            "id": "player_1",
            "entities": {"knownCharacters": [copy.deepcopy(old_npc)]},
        }],
        "settlements": [copy.deepcopy(old_settlement)],
    }
    original = copy.deepcopy(raw)

    world = WorldState.from_dict(raw)

    assert raw == original
    assert world.npcs == {}
    assert world.players[0]["entities"]["knownCharacters"] == [old_npc]
    normalized = world.settlements[0]
    assert normalized["location_id"] == normalized["location"] == "school"
    assert normalized["coordinates"] == {"x": 2, "y": 3}
    assert normalized["founderId"] == "player_1"
    assert normalized["founded_day"] == normalized["foundedDay"] == 4
    assert normalized["tier"] == 2
    assert normalized["stores"]["food"] == 0
    assert normalized["status"] == "stable"
    assert "food" in normalized["needs"]


def test_settlement_normalizer_keeps_legacy_aliases_and_derives_tier():
    legacy = {
        "id": "legacy",
        "location": "loc_school",
        "coordinates": {"x": 1, "y": 2},
        "population": 31,
        "foundedDay": 3,
        "founderId": "player_1",
    }

    normalized = normalize_settlement_record(legacy)

    assert normalized["location_id"] == normalized["location"] == "loc_school"
    assert normalized["tier"] == 2
    assert normalized["founderId"] == "player_1"
    assert normalized["founded_day"] == normalized["foundedDay"] == 3
