import random
from typing import Any


CHAIN_ACTIONS = [
    "travel",
    "search",
    "scavenge",
    "gather",
    "build",
    "reinforce",
    "rest",
    "communicate",
    "negotiate",
    "trade",
    "recruit",
    "send_mission",
    "establish_settlement",
    "defend",
    "attack",
]
ALL_ACTIONS = CHAIN_ACTIONS + ["resource_policy", "failed_attempt"]
MISSING = object()

_DIMENSIONS = {
    "action_type": ALL_ACTIONS,
    "danger": [0, 1, 2, 3, 4, 5, MISSING],
    "zombies": [0, 4, 5, 12, 40, MISSING],
    "current_time": [
        "03:00", "12:00", "19:59", "20:00", "05:59", "06:00", "bad", MISSING,
    ],
    "weather": ["clear", "rain", "storm", "Rain", MISSING],
    "stress": [0, 24, 25, 80, MISSING],
    "status_effects": ["none", "active", "expired", "no_expiry", "non_dict"],
    "companions": ["none", "one", "three", "five", "with_none"],
    "focus": ["", "Stealth run", "move quiet", "SNEAK past", "loud and fast"],
    "plan_quality": [-1, -0.4, 0, 0.5, 1, 2, "bad"],
    "seed": [0, 7, 12345],
    "current_day": [1, 10],
    "target_kind": ["location", "missing_location", "player"],
    "actor_present": [True, False],
    "players_shape": ["list", "dict"],
    "locations_shape": ["list", "dict"],
}


def _random_value(rng: random.Random, values: list[Any]) -> Any:
    return rng.choice(values)


def _build_case(
    rng: random.Random,
    index: int,
    overrides: dict[str, Any] | None = None,
    *,
    apply_compatible: bool = False,
) -> dict[str, Any]:
    selected = {
        key: _random_value(rng, values)
        for key, values in _DIMENSIONS.items()
    }
    if overrides:
        selected.update(overrides)

    action_type = selected["action_type"]
    current_day = selected["current_day"]
    player_id = "player_1" if selected["actor_present"] else "player_9"
    if apply_compatible:
        player_id = "player_1"

    target_kind = selected["target_kind"]
    if apply_compatible:
        target_kind = "player" if action_type == "send_mission" else "location"
    elif target_kind == "player" and action_type != "send_mission":
        target_kind = "missing_location"
    target = {
        "location": "loc_school",
        "missing_location": "loc_missing",
        "player": "player_2",
    }[target_kind]

    location: dict[str, Any] = {"id": "loc_school", "name": "School"}
    if selected["danger"] is not MISSING:
        location["dangerLevel"] = selected["danger"]
    if selected["zombies"] is not MISSING:
        location["zombiePopulation"] = selected["zombies"]

    player: dict[str, Any] = {
        "id": "player_1",
        "location": "loc_school",
    }
    if selected["stress"] is not MISSING:
        player["stress"] = selected["stress"]
    if selected["status_effects"] == "active":
        player["statusEffects"] = [{"id": "active", "expiresDay": current_day + 1}]
    elif selected["status_effects"] == "expired":
        player["statusEffects"] = [{"id": "expired", "expiresDay": current_day - 1}]
    elif selected["status_effects"] == "no_expiry":
        player["statusEffects"] = [{"id": "no-expiry", "expiresDay": None}]
    elif selected["status_effects"] == "non_dict":
        player["statusEffects"] = ["malformed effect"]
    else:
        player["statusEffects"] = []

    companion_names = {
        "none": [],
        "one": ["A"],
        "three": ["A", "B", "C"],
        "five": ["A", "B", "C", "D", "E"],
        "with_none": [None, "A", None],
    }
    player["entities"] = {
        "companions": companion_names[selected["companions"]]
    }

    players: Any
    if selected["actor_present"]:
        players = [player]
    else:
        players = []
    if apply_compatible:
        players = [player]
    if action_type == "send_mission" and target == "player_2":
        players.append({"id": "player_2", "stress": 0})

    if selected["players_shape"] == "dict" and not apply_compatible:
        players = {entry["id"]: entry for entry in players}

    locations: Any = [location]
    if selected["locations_shape"] == "dict" and not apply_compatible:
        locations = {location["id"]: location}

    state: dict[str, Any] = {
        "players": players,
        "locations": locations,
    }
    if current_day is not MISSING:
        state["currentDay"] = current_day
    if selected["current_time"] is not MISSING:
        state["currentTime"] = selected["current_time"]
    if selected["weather"] is not MISSING:
        state["weather"] = selected["weather"]
    if apply_compatible:
        state.setdefault("currentDay", 1)
        state.setdefault("currentTime", "08:00")
        state.setdefault("weather", "clear")
        state.setdefault("resources", {"food": 10, "water": 10})

    parameters = {"policy": {"focus": selected["focus"]}}
    action_fields = {
        "actionId": f"golden-{index}-{action_type}",
        "actionType": action_type,
        "actor": player_id,
        "target": target,
        "parameters": parameters,
        "timeCost": 1,
        "resourceCost": {},
    }
    return {
        "state": state,
        "action_fields": action_fields,
        "seed": selected["seed"],
        "plan_quality": selected["plan_quality"],
    }


def build_cases() -> list[dict[str, Any]]:
    rng = random.Random(1234)
    cases = []

    for dimension, values in _DIMENSIONS.items():
        for value in values:
            cases.append(
                _build_case(rng, len(cases), {dimension: value})
            )

    if len(cases) > 1500:
        raise AssertionError("Coverage anchors exceed the 1,500-case golden set.")
    while len(cases) < 1500:
        cases.append(_build_case(rng, len(cases)))
    return cases


def build_apply_cases() -> list[dict[str, Any]]:
    rng = random.Random(9876)
    action_types = (
        CHAIN_ACTIONS
        + ["resource_policy", "failed_attempt", "search", "build", "attack"]
    )
    cases = []
    for index, action_type in enumerate(action_types):
        case = _build_case(
            rng,
            1500 + index,
            {
                "action_type": action_type,
                "actor_present": True,
                "players_shape": "list",
                "locations_shape": "list",
                "target_kind": "player" if action_type == "send_mission" else "location",
                "danger": index % 6,
                "zombies": [0, 4, 5, 12, 40, MISSING][index % 6],
                "current_time": ["03:00", "12:00", "20:00", "06:00"][index % 4],
                "weather": ["clear", "rain", "storm"][index % 3],
                "stress": [0, 25, 80][index % 3],
                "status_effects": ["none", "active", "expired"][index % 3],
                "companions": ["none", "one", "three", "five"][index % 4],
                "focus": ["", "Stealth run", "SNEAK past"][index % 3],
                "plan_quality": [-1, 0, 0.5, 1][index % 4],
                "current_day": [1, 10][index % 2],
            },
            apply_compatible=True,
        )
        case["action_fields"]["parameters"]["seed"] = case["seed"]
        case["action_fields"]["parameters"]["planQuality"] = case["plan_quality"]
        cases.append(case)
    return cases
