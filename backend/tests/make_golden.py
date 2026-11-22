import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from golden_cases import build_apply_cases, build_cases
from factor_library import load_library
from probability_engine import run_chain
from simulation_engine import SimulationEngine, StructuredAction


GOLDEN_PATH = TESTS_DIR / "golden" / "probability_rolllog.json"
FROZEN_CONTENT_DIR = TESTS_DIR / "golden" / "frozen_content"
FROZEN_LIBRARY = load_library(FROZEN_CONTENT_DIR)


def _action_from_case(case: dict) -> StructuredAction:
    return StructuredAction(**case["action_fields"])


def _chain_record(index: int, case: dict) -> dict:
    result = run_chain(
        case["state"],
        _action_from_case(case),
        case["seed"],
        plan_quality=case["plan_quality"],
        library=FROZEN_LIBRARY,
    )
    return {
        "index": index,
        "interrupt_step": result.interrupt_step,
        "encounter_probability": result.encounter_probability,
        "encounter_occurred": result.encounter_occurred,
        "encounter_roll": result.encounter_roll,
        "steps": [step.to_dict() for step in result.steps],
    }


def _apply_record(index: int, case: dict) -> dict:
    result = SimulationEngine(seed=case["seed"]).apply(
        case["state"],
        _action_from_case(case),
    )
    return {
        "index": index,
        "rollLog": result.rollLog,
        "encounterProbability": result.encounterProbability,
        "encounterOccurred": result.encounterOccurred,
        "encounterDetails": result.encounterDetails,
        "interruptStep": result.interruptStep,
    }


def main() -> None:
    if GOLDEN_PATH.exists():
        raise FileExistsError(
            f"Golden baseline already exists and must not be overwritten: {GOLDEN_PATH}"
        )

    cases = build_cases()
    apply_cases = build_apply_cases()
    if len(cases) != 1500 or len(apply_cases) != 20:
        raise AssertionError(
            f"Expected 1500 chain and 20 apply cases; got {len(cases)} and {len(apply_cases)}."
        )

    output = {
        "cases": [
            _chain_record(index, case)
            for index, case in enumerate(cases)
        ],
        "apply_cases": [
            _apply_record(index, case)
            for index, case in enumerate(apply_cases)
        ],
    }
    serialized = json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with GOLDEN_PATH.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
    print(
        f"Created {GOLDEN_PATH} once with "
        f"{len(cases)} chain cases and {len(apply_cases)} apply cases."
    )


if __name__ == "__main__":
    main()
