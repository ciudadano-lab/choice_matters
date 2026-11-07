from narrator import narrate_simulation_result
from simulation_engine import SimulationResult, StructuredAction


def test_settlement_narration_includes_population_and_tier():
    action = StructuredAction(
        actionId="settlement-narration-1",
        actionType="establish_settlement",
        actor="player_1",
        target="school",
    )
    result = SimulationResult(
        actionId=action.actionId,
        actionType=action.actionType,
        timeConsumed=3,
        resourceChanges={"food": -2, "water": -1},
        playerStatusChanges=[],
        locationChanges=[],
        encounterProbability=0.0,
        encounterOccurred=False,
        encounterDetails={},
        factionRelationshipChanges=[],
        informationDiscovered=[],
        worldEventsTriggered=[],
        updatedWorldState={
            "locations": [{"id": "school", "name": "School"}],
            "settlements": [{
                "name": "School Settlement",
                "location": "school",
                "population": 31,
            }],
        },
    )

    narrative = narrate_simulation_result({}, action, result)

    assert "School Settlement" in narrative
    assert "31 residents" in narrative
    assert "settlement tier 2" in narrative