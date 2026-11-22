PHASE 4.5: settlement and faction layer
Do NOT edit tests/golden or frozen_content; golden must pass at the end of every stage.
Do not change apply(), the chain/roll logic, or encounter outcome odds. Work stage by stage;
after each stage run the full suite (including fastapi tests), report, and STOP.

PRINCIPLES
- The engine owns every number. The LLM only narrates and extracts facts; it never invents
  stats, deltas or state. Closed vocabularies for effects and events.
- Every tuning number lives in content/*.json (pydantic-validated, status live|draft), never
  inline in code.
- Deterministic and replayable: all random choices use hashing like roll_value, with distinct
  step names (e.g. "faction_tick:<faction_id>:<day>"). Iterate in sorted-id order, never
  dict-insertion order. Same seed gives the same history.
- Old saved rooms must still load: missing fields get defaults in WorldState.from_dict.
- The player's shared resource pool (world.resources) keeps its current behaviour.
  Settlements have their own stores; transfers happen only via explicit actions.
- Every tick decision is logged with its reasons (like plan_reasons) for the narrator and
  the harness.
- Stat clamps, a per-tick cap on any change, and a rubber-band so one faction or settlement
  cannot snowball unboundedly. A maximum number of events per tick.
- This phase produces STATE and EVENTS. It does not decide what the player sees or
  pauses on; that is Phase 4.6 (event director) and Phase 5.

STAGE 0: audit (report only, no code)
List what already exists for factions, settlements, NPCs and relationships (world_state.py,
simulation_engine.py, relationships.py, information_layer.py), including the hardcoded
faction_survivors deltas, establish_settlement, and where NPCs live. Say what a new design
would collide with. Wait for my go-ahead.

STAGE A: data model, content and seeding
1. Settlement: id, name, location_id, faction_id, leader_npc_id, population, morale (0-100),
   stores {food, water, building_materials, medical_supplies}, defense (0-100), unrest
   (0-100), status (growing|stable|strained|starving|collapsing|abandoned), needs (derived),
   founded_day.
2. Faction: id, name, ideology tags, goals (list of ids from a goal vocabulary), strength,
   resources, leader_npc_id, disposition toward each other faction and each player
   (-100..100), stance per pair (allied|friendly|neutral|wary|hostile|war), council_seats.
3. NPC: id, name, role, faction_id, settlement_id, traits (reuse existing), loyalty (0-100),
   status (alive|missing|dead), arc_id and arc_stage. Reuse RelationshipSystem for
   player-NPC relationships.
4. Content files: content/factions.json, content/settlements.json, content/npcs.json,
   content/faction_rules.json (goal vocabulary, tick rules, thresholds). Pydantic models with
   extra="forbid".
5. Seed a new world with 3 factions, 3-4 settlements and ~8 NPCs from content, deterministic by
   world seed. faction_survivors remains valid.
6. Tests: validation, seeding determinism, old-room loading, golden unchanged.

STAGE B: settlement tick
1. New module settlement_tick.py, called from WorldTick after daily upkeep, never from apply().
2. Daily light tick: consume and produce stores from population and location yields (reuse
   economy.json style data), update morale and unrest, status transitions.
3. Weekly tick (every 7 days): population growth/decline, defense changes, leader checks.
4. Fast-forward: weekly/monthly ticks must be callable for long jumps without per-day loops
   where possible. Add a test that skipping 90 days works and is deterministic.
5. Harness: add a "world_sim" mode with no players acting for N days that reports settlement
   status distribution, population and stores over time.
STOP and report.

STAGE C: faction tick
1. faction_tick.py: weekly decisions (propose trade, request help, send warning, raid, recruit,
   expand) chosen from a closed list by data-driven scoring (goals, strength, disposition,
   settlement needs). Monthly tick: goal progress, stance changes, council influence.
2. Every decision emits a structured FactionEvent {type, actors, day, reasons}, appended to a
   world event queue. Do not surface them to players or pause anything.
3. Disposition changes from player actions go through one function, replacing the hardcoded
   faction_survivors deltas, with the same default numbers so existing behaviour is kept.
4. world_sim harness: report stance distribution, event counts per tick, and runaway checks.
STOP and report.

STAGE D: NPC arcs
1. content/arcs.json: arcs as small state machines (stages with conditions and effects from
   the closed vocabulary) such as loyal-to-defector, rival rising, missing-and-found.
2. Arc advancement is checked on the weekly tick, deterministic, and logged with reasons.
3. An NPC can die, go missing or change faction only through arc effects or explicit events.
STOP and report.

STAGE E: action wiring
1. Extend the closed effect vocabulary (action_registry.py) with: change_faction_disposition,
   change_settlement_stat, change_npc_loyalty. Validation fails on anything else.
2. Make negotiate, trade, recruit, send_mission and establish_settlement apply real
   settlement/faction/NPC effects through the registry, keeping existing outputs identical
   where the golden apply cases cover them. Move the signal action to live and give it a
   real effect (it raises settlement/faction attention, for example).
3. Differential test: pre-existing apply() outputs are unchanged for all 15 old actions.
STOP and report.

STAGE F: knowledge and narration
1. Players only see what they have discovered (extend playerKnownWorld). No leaking of hidden
   faction goals or events.
2. Narrator receives a compact summary of recent tick events and reasons without numbers.
STOP and report.

STAGE G: acceptance targets (PASS/WARN, not assertions) from world_sim, 100 days, 30 seeds
- Most seeds keep at least one settlement alive at day 100; settlement collapse before day 40
  is rare.
- Faction stances are varied: not all hostile and not all allied.
- No stat sits pinned at its bound for more than 50% of ticks; event counts per tick stay
  within the cap.
- Same seed gives the same history.
Report tables, the golden sha256, and the full test count.