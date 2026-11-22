# Project conventions

- Use `hashlib` for deterministic identifiers and seeds; never use Python's `hash()`.
- Treat input world and room state as immutable; copy it before changing nested data.
- Keep shared game rules, action types, and constants in the existing `backend/game_data.py`.
- Preserve the world-state JSON schema unless a task explicitly asks to change it.
- Keep player-facing strategic actions distinct from internal tactical simulation actions.
- Keep parsing, simulation, and narration responsibilities separate.
- Put backend tests in `backend/tests/` and use `pytest`.
- Run the full backend test suite with `pytest` from `backend/`.
