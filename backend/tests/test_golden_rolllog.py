import hashlib
import json

import probability_engine
from golden_cases import build_apply_cases, build_cases
from make_golden import (
    _apply_record,
    _chain_record,
    FROZEN_CONTENT_DIR,
    FROZEN_LIBRARY,
    GOLDEN_PATH,
)


FROZEN_CONTENT_SHA256 = {
    "chains.json": "a302b6d052fc466122010b81fb70e918cc216b9da32fa166ab7cf3cc8ad753ff",
    "factors/approach.json": "4a1eb72fcb5aae0a7b63593c41929dae16f3e9ed9ce9bb7b1c677f93889579b0",
    "factors/environment.json": "77b89ffac90263b67558cb216262196d98c89051dd2e2e5b302f1e69e1fbe501",
    "factors/location.json": "21dc55ba46644cef38554d5f7b3a235c0f9d7690385068548c89d504e45cac68",
    "factors/player_condition.json": "c2d353bdf7dd4b0e3972299608c08630975ec60b1dd9a1722a7c4b012cf05c49",
    "factors/social.json": "f779baf469f7460b5c21d4b597da3ab5e09a6cf54871b7437d794cf44300aa7e",
}


def test_frozen_content_matches_its_sha256_baseline():
    actual = {
        path.relative_to(FROZEN_CONTENT_DIR).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in FROZEN_CONTENT_DIR.rglob("*")
        if path.is_file()
    }

    assert actual == FROZEN_CONTENT_SHA256, (
        "golden/frozen_content is a frozen baseline; do not modify, add, or "
        "remove its files."
    )


def test_probability_roll_log_matches_immutable_golden_baseline(monkeypatch):
    monkeypatch.setattr(probability_engine, "_DEFAULT_LIBRARY", FROZEN_LIBRARY)

    with GOLDEN_PATH.open("r", encoding="utf-8") as handle:
        golden = json.load(handle)

    cases = build_cases()
    apply_cases = build_apply_cases()

    assert len(cases) == 1500
    assert len(apply_cases) == 20
    assert golden["cases"] == [
        _chain_record(index, case)
        for index, case in enumerate(cases)
    ]
    assert golden["apply_cases"] == [
        _apply_record(index, case)
        for index, case in enumerate(apply_cases)
    ]
