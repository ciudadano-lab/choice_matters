"""Probability-engine limits and deterministic roll formatting.

Chance modifiers and action chains now live in backend/content/factors/*.json
and backend/content/chains.json so they can be tuned without Python changes.
"""

CHANCE_MIN = 0.03
CHANCE_MAX = 0.90
PLAN_MODIFIER_CAP = 0.12
# Keep the legacy numeric plan_quality path byte-stable.
LEGACY_PLAN_MODIFIER_CAP = 0.30
PLAN_TIER_SHIFT = 0.15
ZERO_CHANCE = 0.0

DEFAULT_PLAN_QUALITY = 0.0
PLAN_QUALITY_MIN = -1.0
PLAN_QUALITY_MAX = 1.0

ROLL_HASH_PREFIX_HEX_LENGTH = 8
ROLL_HASH_BASE = 16
ROLL_VALUE_SCALE = 2**32

DEFAULT_CURRENT_DAY = 1
DEFAULT_CURRENT_TIME = "00:00"

DAWN_TIME_BAND = ((5 * 60, 7 * 60 + 59),)
DAY_TIME_BAND = ((8 * 60, 17 * 60 + 59),)
DUSK_TIME_BAND = ((18 * 60, 19 * 60 + 59),)
NIGHT_TIME_BAND = ((20 * 60, 23 * 60 + 59), (0, 4 * 60 + 59))
TIME_BANDS = {
    "dawn": DAWN_TIME_BAND,
    "day": DAY_TIME_BAND,
    "dusk": DUSK_TIME_BAND,
    "night": NIGHT_TIME_BAND,
}
TIME_SLOTS = (
    ("dawn", "06:00"),
    ("day", "12:00"),
    ("dusk", "18:00"),
    ("night", "22:00"),
)

APPROACH_FIT_BANDS = {
    "stealth": {
        "bands": frozenset({"dawn", "dusk", "night"}),
        "extra_weather": frozenset({"rain", "storm"}),
    },
    "deception": {
        "bands": frozenset({"dawn", "dusk", "night"}),
        "extra_weather": frozenset({"rain", "storm"}),
    },
    "force": {
        "bands": frozenset({"dawn", "day", "dusk"}),
        "extra_weather": frozenset(),
    },
    "speed": {
        "bands": frozenset({"dawn", "day", "dusk"}),
        "extra_weather": frozenset(),
    },
    "diplomacy": {
        "bands": frozenset(TIME_BANDS),
        "extra_weather": frozenset(),
    },
    "unspecified": {
        "bands": frozenset(),
        "extra_weather": frozenset(),
    },
}

PLAYER_STAT_RANGE = (0, 100)


def clamp_player_stat(value: int | float) -> int | float:
    return max(PLAYER_STAT_RANGE[0], min(PLAYER_STAT_RANGE[1], value))
