from plan_features import PlanFeatures


def test_from_untrusted_normalizes_limits_duplicates_and_wrong_types():
    features = PlanFeatures.from_untrusted({
        "targetLocationIds": [
            " loc-a ",
            "LOC-A",
            7,
            "loc-b",
            "x" * 81,
            "loc-c",
            "loc-d",
            "loc-e",
            "loc-f",
        ],
        "peopleMentioned": [" Alex ", "alex", None, "Morgan"],
        "itemsPlanned": [
            "Medical Supplies",
            " medical_supplies ",
            "water",
            4,
            "x" * 41,
            "food",
            "radio",
            "medkit",
            "rope",
        ],
        "timing": " LATE NIGHT ",
        "approach": 17,
        "countermeasures": [" Scout first ", "scout first", "alternate route", None],
        "hasFallback": 1,
        "unrecognized": "ignored",
    })

    assert features is not None
    assert features.targetLocationIds == ["loc-a", "loc-b", "loc-c", "loc-d", "loc-e"]
    assert features.peopleMentioned == ["Alex", "Morgan"]
    assert features.itemsPlanned == [
        "Medical Supplies",
        "water",
        "food",
        "radio",
        "medkit",
        "rope",
    ]
    assert features.timing == "unspecified"
    assert features.approach == "unspecified"
    assert features.countermeasures == ["Scout first", "alternate route"]
    assert features.hasFallback is False
    assert "unrecognized" not in features.to_dict()


def test_from_untrusted_accepts_only_real_true_for_fallback():
    assert PlanFeatures.from_untrusted({"hasFallback": True}).hasFallback is True
    for value in (False, 1, "true", None, []):
        assert PlanFeatures.from_untrusted({"hasFallback": value}) is None


def test_from_untrusted_handles_non_dict_and_empty_features():
    for value in (None, [], "plan", 2, {"timing": "unspecified", "approach": "unspecified"}):
        assert PlanFeatures.from_untrusted(value) is None


def test_from_untrusted_bounds_very_large_lists_and_long_strings():
    features = PlanFeatures.from_untrusted({
        "targetLocationIds": [f"loc-{index}" for index in range(1000)],
        "peopleMentioned": [f"person-{index}" for index in range(1000)],
        "itemsPlanned": [f"item-{index}" for index in range(1000)],
        "countermeasures": [f"measure-{index}" for index in range(1000)],
    })

    assert features is not None
    assert len(features.targetLocationIds) == 5
    assert len(features.peopleMentioned) == 5
    assert len(features.itemsPlanned) == 6
    assert len(features.countermeasures) == 4
    assert all(len(value) <= 80 for value in features.targetLocationIds)
    assert all(len(value) <= 60 for value in features.peopleMentioned)
    assert all(len(value) <= 40 for value in features.itemsPlanned)
    assert all(len(value) <= 40 for value in features.countermeasures)
