from main import _build_action_from_payload


def test_llm_cannot_override_plan_quality_or_seed():
    action, error = _build_action_from_payload({
        "actionId": "policy-untrusted-parameters",
        "actionType": "resource_policy",
        "actor": "player_1",
        "target": "settlement",
        "parameters": {
            "focus": "food production",
            "planQuality": 1,
            "seed": 12345,
        },
        "timeCost": 21,
        "resourceCost": {},
    })

    assert error is None
    assert action is not None
    assert action.parameters == {"focus": "food production"}


def test_plan_features_are_sanitized_and_stored_only_in_policy():
    action, error = _build_action_from_payload({
        "actionId": "policy-plan-features",
        "actionType": "resource_policy",
        "actor": "player_1",
        "target": "settlement",
        "parameters": {
            "focus": "food production",
            "planFeatures": {"hasFallback": True},
            "policy": {"focus": "nested", "planFeatures": {"hasFallback": True}},
        },
        "policy": {
            "focus": "food production",
            "planFeatures": {"itemsPlanned": ["untrusted nested item"]},
        },
        "planFeatures": {
            "targetLocationIds": [" loc-a ", "LOC-A", 2],
            "itemsPlanned": [" Water ", "water"],
            "hasFallback": True,
            "unknown": "ignored",
        },
        "seed": 123,
        "planQuality": 1,
    })

    assert error is None
    assert action is not None
    assert action.parameters["focus"] == "food production"
    assert action.parameters["policy"] == {
        "focus": "food production",
        "planFeatures": {
            "targetLocationIds": ["loc-a"],
            "peopleMentioned": [],
            "itemsPlanned": ["Water"],
            "timing": "unspecified",
            "approach": "unspecified",
            "countermeasures": [],
            "hasFallback": True,
        },
    }
    assert "planFeatures" not in action.parameters
