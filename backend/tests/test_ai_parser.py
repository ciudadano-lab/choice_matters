from main import _parse_json_payload, parse_reply


def test_parse_reply_handles_fenced_json_without_language_tag():
    text = "Here is the action\n```\n{\"actionId\": \"test\", \"actionType\": \"travel\", \"actor\": \"player_1\", \"target\": \"safehouse\", \"parameters\": {}, \"timeCost\": 1, \"resourceCost\": {}, \"risks\": [], \"prerequisites\": []}\n```"

    narrative, payload = parse_reply(text)

    assert narrative == "Here is the action"
    assert payload is not None
    assert payload["actionId"] == "test"
    assert payload["actionType"] == "travel"


def test_parse_json_payload_handles_uppercase_json_fence_and_prose():
    text = "I will do that.\n```JSON\n{\"actionId\": \"test\", \"actionType\": \"search\", \"actor\": \"player_1\", \"target\": \"warehouse\", \"parameters\": {}, \"timeCost\": 1, \"resourceCost\": {}, \"risks\": [], \"prerequisites\": []}\n```\nThanks!"

    payload = _parse_json_payload(text)

    assert payload is not None
    assert payload["actionType"] == "search"
    assert payload["target"] == "warehouse"
