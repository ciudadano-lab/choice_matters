import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from env_config import get_backend_setting
from game_data import build_system_prompt, fresh_room_state
from main import _build_ai_action_parse_prompt, _resolve_payload_feasibility
from main import _parse_action_with_llm
from world_rules import WorldRules, load_world_rules, render_world_rules


BACKEND_DIR = Path(__file__).resolve().parents[1]
FEASIBILITY_CASES_PATH = (
    BACKEND_DIR / "tests" / "fixtures" / "feasibility_cases.json"
)


def test_world_rules_are_validated_and_rendered_into_parse_and_narrator_prompts():
    rules = load_world_rules()
    rendered = render_world_rules(rules)
    parse_prompt = _build_ai_action_parse_prompt(
        1,
        "player_1",
        fresh_room_state(seed=414),
        "Wrap the car in scrap armour.",
    )
    narrator_prompt = build_system_prompt(1, fresh_room_state(seed=414))

    assert rules.physics_statement in rendered
    assert "Instant mind-reading or telepathy" in rendered
    assert "Grounded: Wrap a car in scrap armour." in rendered
    assert rendered in parse_prompt
    assert rendered in narrator_prompt
    assert parse_prompt.count("WORLD PHYSICS:") == 1

    raw = rules.model_dump()
    raw["unexpected"] = True
    with pytest.raises(ValidationError):
        WorldRules.model_validate(raw)


def test_feasibility_fixture_has_three_balanced_regression_groups():
    with FEASIBILITY_CASES_PATH.open("r", encoding="utf-8") as handle:
        groups = json.load(handle)

    assert set(groups) == {
        "must-be-impossible",
        "must-be-partial",
        "must-be-grounded",
    }
    assert all(len(cases) == 10 for cases in groups.values())
    assert all(
        isinstance(case.get("input"), str)
        for cases in groups.values()
        for case in cases
    )


def test_unknown_feasibility_uses_content_categories_across_fixture_cases():
    with FEASIBILITY_CASES_PATH.open("r", encoding="utf-8") as handle:
        groups = json.load(handle)

    for case in groups["must-be-impossible"]:
        assert _resolve_payload_feasibility(
            {"feasibility": "unrecognized"},
            case["input"],
        ) == "impossible", case["id"]
    for case in groups["must-be-grounded"]:
        assert _resolve_payload_feasibility(
            {"feasibility": "unrecognized"},
            case["input"],
        ) == "grounded", case["id"]


@pytest.mark.skipif(
    not get_backend_setting("GROQ_API_KEY", ""),
    reason="GROQ_API_KEY is required for the live feasibility parser regression.",
)
def test_live_feasibility_parser_reports_fixture_pass_rate():
    from llm_client import GROQ_API_KEY

    if not GROQ_API_KEY:
        pytest.skip("GROQ_API_KEY is not available to the configured LLM client.")

    from main import _build_ai_action_parse_prompt
    from simulation_engine import StructuredAction

    with FEASIBILITY_CASES_PATH.open("r", encoding="utf-8") as handle:
        groups = json.load(handle)
    room = fresh_room_state(seed=415)
    passed = 0
    total = sum(len(cases) for cases in groups.values())
    for group, cases in groups.items():
        expected = group.removeprefix("must-be-")
        for case in cases:
            action, _, _, actual, _ = asyncio.run(
                _parse_action_with_llm(
                    1,
                    "player_1",
                    room,
                    case["input"],
                )
            )
            if actual == expected and isinstance(action, StructuredAction):
                if expected != "partial" or action.actionType != "failed_attempt":
                    passed += 1

    pass_rate = passed / total if total else 0.0
    print(f"Live feasibility parser pass rate: {passed}/{total} ({pass_rate:.1%})")
