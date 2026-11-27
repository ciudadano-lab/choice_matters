# Field Log — Survivor Sandbox

A multiplayer survival sandbox where you can attempt *anything* — and the world
answers honestly. Players write free-form plans ("sneak into the east market at
dusk with a torch and a fallback route, trade the salvaged radio to the watch for
passage"), a deterministic simulation decides what actually happens, and an LLM
narrates the result as a story that stays consistent across rounds.

The core idea: **the engine owns the numbers and the consistency; the LLM owns the
words.** The LLM never invents probabilities, resources or outcomes. It extracts
what the player meant, then tells the story of what the engine resolved.

---

## What makes it different

- **Free-form plans, honest odds.** Plan quality changes the outcome. A well-prepared
  plan (known locations, the right items, good timing, a fallback) is measurably
  safer than a sloppy one, and the engine can explain *why* in the narration.
- **Replayable dice.** Every roll is a hash of (seed, action, day, time, step), so the
  same inputs give the same story. Every step logs its base chance, modifiers and
  counters.
- **Long-term pacing.** Time passes in autopilot stretches. The game pauses only at
  meaningful decision points, not every turn.
- **A living world.** Settlements consume and produce, factions decide and drift,
  and relationships change because of what players did.
- **Ridiculous actions stay possible.** A large data-driven library of actions and
  factors covers the strange cases; a rare adjudicator model handles the truly new
  ones by drafting reviewable content, not by deciding outcomes.

---

## How a turn works

```
player text ──► PARSE (LLM, facts only) ──► SIMULATE (engine, deterministic) ──► NARRATE (LLM)
                 action + planFeatures        probability chain, effects,          story + reasons
                                              encounter outcome, world tick
```

1. **Parse.** An LLM turns the player's text into a structured action plus
   `planFeatures` (referenced locations, preparation items, timing, approach,
   countermeasures, fallback). Factual extraction only — no scoring. Every parse and
   fallback is logged.
2. **Simulate.** The engine validates those facts against real state (what the player
   knows, owns, the time band, the weather), then runs a **probability chain** for the
   action. Each step has a base chance, situation and plan modifiers, counters, and a
   deterministic roll. Triggered encounters get their own outcome roll
   (evaded / close call / hurt / serious).
3. **Tick the world.** Time advances in slots (dawn, day, dusk, night). Needs are
   consumed, settlements and factions update on daily, weekly and monthly cadences.
4. **Narrate.** A second LLM call narrates the resolved result, including the reasons
   the plan helped or hurt.

---

## Status

| Area | Status |
|---|---|
| Rooms, WebSocket sync, chat, SQLite persistence | Done |
| Procedural map, road network, road tile rendering | Done |
| Parse logging, conventions file | Done |
| Probability engine (step chains, replayable rolls, factor-driven) | Done |
| Golden test (frozen 1,500-case baseline) | Done |
| Plan quality (LLM extracts facts, engine validates and prices them) | Done |
| Encounter consequences (outcome roll, injuries, health recovery) | Done |
| Economy (per-player consumption, yields, starvation, recovery) | Done |
| Time slots and ambient night risk | Done |
| Data-driven action registry (live/draft, closed effect vocabulary) | Done |
| Simulation harness (bots, sweeps, world-pause rates, world sim) | Done |
| Settlements (stores, needs, hysteresis, founding grants) | Done |
| Factions (weekly weighted decisions, splinters, dispositions) | Done |
| Action wiring into settlements, factions, NPCs | Done (consumers in progress) |
| Knowledge filtering, narrator world-state block, thread links | In progress |
| Interrupts, shared-world pause/resume, tactical options | Planned |
| Event director, threat clocks, finale | Planned |
| NPC character layer (drives, secrets, traits, arcs) | Planned |
| Production, agreements and institutions | Planned |
| Adjudicator and LLM playtester | Planned |

---

## Planned features

### Interrupts and a shared timeline (Phase 5/6)
The world advances one day at a time. When any player hits a decision point, the
**whole world pauses** so every player shares one chronology. Decisions are collected
together under a shared timer, resolved in slot order, and timeouts apply a safe
default. Tactical options at an interrupt: investigate, hide, ambush, barricade, eat.
A player-count pacing factor keeps the *world-level* pause rate at roughly one
pause every 5–10 days regardless of how many players there are.

### Event director
Instead of waiting for random encounters, a director generates a small number of
pressures per round across six layers — internal, external, survival, mystery,
conflict and wildcard — using faction and settlement state. Pressures surface as
threads the narrator pays off, with threat clocks leading to a finale.

### Character layer
Major NPCs get engine-owned **traits, drives, loyalties and secrets** chosen from
closed vocabularies, plus LLM-written voice, backstory and mannerisms (generated once
and cached). Arcs are small state machines triggered by drives and world state. NPCs
make weekly choices — support, undermine, confront, confess, betray, make secret
contact — and those choices become story threads. About 10–20 major NPCs are fully
simulated; minor NPCs stay LLM flavor. The LLM can never kill, move or re-stat a
world NPC.

### Production, agreements and institutions
Creative plays are compositions of general systems, not new hard-coded verbs:
- **Resource sites and a production graph:** deposits at locations, recipes
  (inputs → outputs, requiring facilities, labor and time).
- **Agreements:** treaties as stored data with clauses (grant access, share resource,
  mutual defense, tribute, non-aggression, trade terms, council seat), enforced by the
  tick, with consequences for breach. "Give a faction mining rights" works because ore
  flows, a workshop turns it into equipment, and equipment raises military strength.
- **Institutions:** governance, military, economy, culture, science and diplomacy
  structures with cost, upkeep, ramp-up and bounded effects (a debate forum lowers
  unrest; a training program shifts population from workers to fighters).
- **Population pools:** people grouped by role, moved by policies at a rate and cost.

### Adjudicator and playtester
When a play needs something the libraries lack, a third model **drafts a new library
entry** (recipe, clause, institution, action) that the engine validates and bounds. A
human reviews drafts, and approved entries become ordinary data. A separate LLM
playtester plays the game with instructions to be unusual, recording the actions and
factors that appear so the libraries grow from real play.

---

## Architecture

**Determinism and content-driven design**
- All tuning numbers live in `backend/content/*.json`, validated with pydantic.
  Actions, factors, chains, economy, encounter outcomes, factions, settlements and
  NPCs are data. `status: live|draft` keeps unreviewed content inert.
- The effect vocabulary is closed. Unknown effect types fail validation. Every stat
  change is clamped.
- All randomness is hash-based (`hashlib`, never `hash()`), iterates in sorted-id
  order, and is replayable.
- `SimulationEngine.apply()` takes a `rules` argument. The default (`"legacy"`) is
  frozen to match the golden test; live game paths use `rules="current"`.

**Safety nets**
- `backend/tests/golden/` holds a frozen snapshot of 1,500 chain cases plus apply()
  cases, run against frozen content. **Never edit or regenerate it.** If it fails, the
  change is wrong.
- `backend/tools/harness.py` plays seeded games with scripted bots through the real
  engine and reports pause rates, near-misses, injuries, deaths, economy and faction
  behavior. Tuning is done against it, not by guessing.

---

## Repository layout

```
backend/
  main.py                 FastAPI app, WebSockets, parse/simulate/narrate pipeline
  simulation_engine.py    action resolution, effects, registry handlers
  probability_engine.py   step chains, replayable rolls
  factor_library.py       data-driven modifiers/counters and input registry
  plan_features.py        sanitised plan facts
  encounter_resolution.py encounter outcome rolls
  economy.py              consumption, yields, recovery
  world_state.py          world model, seeding, normalisation
  world_tick.py           slots, upkeep, faction and settlement cadence
  settlement_tick.py      settlement simulation
  action_registry.py      data-driven actions
  information_layer.py    what each player knows
  narrator.py, llm_client.py, rooms_store.py, ws_manager.py
  content/                actions, factors, chains, economy, factions, settlements, npcs
  tools/harness.py        simulation harness
  tests/                  pytest suite and golden baseline
frontend/index.html       single-file UI (map, textures, road tiles)
tools/                    asset generation scripts
data/                     runtime folder (rooms.db)
.github/copilot-instructions.md   project conventions
```

---

## Local setup

Python 3.10+ recommended.

```bash
python -m venv venv
source venv/bin/activate        # Windows: .\venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
cp .env.example .env            # then add your key
cd backend
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Open `frontend/index.html` in your browser (or the served URL on port 8000).

### Environment variables

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | API key for the LLM calls (never commit it) |
| `GROQ_MODEL` | Narration model (default `openai/gpt-oss-120b`) |
| `GROQ_PARSE_MODEL` | Parsing model (default `openai/gpt-oss-20b`) |
| `DATA_DIR` | Runtime data folder (used in deployment) |

Keep secrets in `.env` (git-ignored). Only `.env.example` is committed.

---

## Tests and the harness

```bash
cd backend
pytest                                       # full suite incl. golden test
python tools/harness.py --help               # list harness modes
python tools/harness.py world_sim --days 100 --seeds 30
```

Harness output is written to `backend/tools/harness_out/` (git-ignored).

---

## Working on the project

- Read `.github/copilot-instructions.md` first. Key rules: deterministic hashing,
  never mutate input state, keep parsing, simulation and narration separate, keep
  numbers in content files.
- Work in small, reviewable steps. After each step: run the full suite, check the
  golden hash is unchanged, and report harness numbers for anything that affects pacing.
- One commit per step.

---

## License

MIT — see `LICENSE`.
