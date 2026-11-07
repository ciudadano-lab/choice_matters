import random

from rooms_store import ROOM_CODE_ALPHABET, _seed_from_code


def test_room_code_seed_is_order_sensitive_and_wide():
    assert _seed_from_code("ABCDE") != _seed_from_code("EDCBA")

    rng = random.Random(404)
    codes = {"".join(rng.choice(ROOM_CODE_ALPHABET) for _ in range(5)) for _ in range(200)}
    seeds = {_seed_from_code(code) for code in codes}

    assert len(codes) == 200
    assert len(seeds) == 200