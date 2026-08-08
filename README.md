# Zombie Sandbox — Online

A small multiplayer co-op storytelling sandbox where each human player
controls a survivor and receives AI-generated narrative from a "narrator"
LLM. The backend offers REST + WebSocket APIs for room creation, slot
claiming, live chat, and turn decisions. Room state is saved in a small
SQLite database.

## Key features

- Rooms with 5-letter join codes
- Real-time sync and chat via WebSockets
- Per-player AI narrator (configurable LLM adapter)
- Simple SQLite-backed persistence (one row per room)

## Repo layout

```
zombie-sandbox-online/
  backend/               # FastAPI backend (API + WebSockets)
    main.py
    game_data.py
    rooms_store.py
    llm_client.py
    ws_manager.py
    requirements.txt
    .env.example
    tests/
  frontend/
    index.html           # Single-file UI that connects to backend websocket
  data/                  # runtime folder, contains rooms.db created at runtime
  LICENSE
  README.md
```

## Requirements

- Python 3.10+ recommended
- See `backend/requirements.txt` (FastAPI, Uvicorn, httpx, python-dotenv, pydantic)

## Quick local setup

From the project root:

```powershell
Set-Location "C:\Users\Dell\Downloads\zombie-sandbox-online\zombie-sandbox-online"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r backend\requirements.txt
```

Create a local env file (do not commit this):

```powershell
Copy-Item backend\.env.example backend\.env
notepad backend\.env   # add your API keys (e.g. GEMINI_API_KEY)
```

Run the backend server:

```powershell
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Then open `frontend/index.html` in your browser. If you need remote access
from other devices on your LAN, use your machine's IP and update the
`API_BASE` constant in the frontend if necessary.

## Environment variables

- `GEMINI_API_KEY` - API key for the configured LLM in `backend/llm_client.py`. Put it in `backend/.env` locally (never commit).

## Tests

Run unit tests from the `backend` folder:

```powershell
cd backend
pytest
```

## What to commit

Commit the backend source, tests, `backend/.env.example`, `frontend/index.html`,
`backend/requirements.txt`, and this `README.md`. Do NOT commit `backend/.env`,
`data/rooms.db`, `venv/`, or `__pycache__` — `.gitignore` is configured to exclude
those.

## License

This project is available under the MIT License (see `LICENSE`).

---

If you'd like, I can also: add a `Makefile` or PowerShell script to automate
setup, open a PR with these changes, or run the commit/push commands for you.
