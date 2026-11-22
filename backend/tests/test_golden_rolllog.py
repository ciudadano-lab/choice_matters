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
    "chains.json": "015a932a839af5c46a7efd71a85154b1813b12ad25e047fdf15d4adad90282e6",
    "factors/approach.json": "0eff89615a6dc309035212f5dfb2483dd7f3f8c28fde147062224b2fb9570cde",
    "factors/environment.json": "ed69856ea9ecdf0ce11740efe427092cf299b323d5efb4237219a773c5702c66",
    "factors/location.json": "38bc2db1e9ee5dadae28cf0edc8d14b9c9ec3f0d7ba0cf5fe3b01a6e54be9ba1",
    "factors/player_condition.json": "27486e6f188577ad9dbf755b8c78917414c95eceef83620b29f7284a96e651e5",
    "factors/social.json": "b8b5a3269a83ba7a99def4d27192607b7e191bda010a839e4505b2047a177f5b",
}


def test_frozen_content_matches_its_sha256_baseline():
    actual = {
        path.relative_to(FROZEN_CONTENT_DIR).as_posix(): hashlib.sha256(
            path.read_bytes().replace(b"\r\n", b"\n")
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
