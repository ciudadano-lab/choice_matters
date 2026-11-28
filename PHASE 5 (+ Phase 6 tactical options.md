PHASE 5 (+ Phase 6 tactical options): interrupts, shared-world pause and resume.

RULES (apply to every stage)
- Do NOT edit tests/golden or frozen_content. Golden must pass after every stage.
- Do NOT change apply() or its legacy output. All live paths already use rules="current".
- The engine owns the numbers. No new LLM calls inside the pause/resume path (existing
  narration after resolution is fine). All tuning numbers live in content/*.json.
- Rolls stay deterministic: hash(seed, actionId, day, time, step). Pausing and resuming
  must give EXACTLY the same result as an uninterrupted run with the same choices.
- Work stage by stage. After each: full suite (incl. fastapi tests), golden sha256, report,
  then STOP.

DESIGN (read first)
- One shared chronology. The world advances slot by slot (dawn/day/dusk/night). In a slot,
  every player's primary chain (and the ambient night chain) resolves. If any chain triggered an
  interrupt (the encounter step), the WHOLE WORLD pauses at the end of that slot, BEFORE the
  encounter outcome is resolved. Everyone interrupted in that slot decides together.
- Decisions are collected with one shared timer. When all interrupted players have chosen (or
  the timer expires and a safe default is applied), outcomes are resolved in slot order, then the
  run resumes from the next slot. Non-interrupted players wait.
- Interrupt options are tactical choices (investigate, hide, ambush, barricade, flee, fight, eat,
  hold). They are DATA, validated, and shift the encounter outcome odds through the same
  factor mechanism as plan quality. They never invent numbers outside content.

STAGE 0: audit (report only, no code)
Confirm and list: where interruptStep is produced and consumed; the order inside
advance_policy_day (player chains, ambient night chain, advance_slot); what
resolve_encounter_after_action needs (action, result, seed) so a pending encounter can be
serialized; how _run_round, _advance_policy_period, pendingDecisions, _round_timeout and
ROUND_TIMEOUT_SECONDS work; what the harness's world-pause rate measures. Say what would break
if the day loop became resumable. Wait for my go-ahead.

STAGE A: resumable day loop (engine only, no server, no UI)
1. A serializable PausedRun: runId, day, slot, resume cursor (next slot), the list of pending
   encounters (playerId, actionId, actionType, target, parameters, seed, chain rollLog,
   interruptStep, isAmbient), remaining days in the commitment period, days resolved so far, the
   decisions collected so far, and a version counter.
2. A new method advance_period_until_interrupt(world, queue, ...) returning
   (world, PausedRun | None). It runs the period slot by slot like today; at the end of a slot, if
   any chain was interrupted, it does NOT resolve those encounter outcomes: it returns a
   PausedRun. Slot processing order stays sorted by player id.
3. resume_period(world, queue, pausedRun, choices, ...) resolves the pending encounters in slot
   order using each player's choice (a default when missing), then continues the loop, which may
   pause again.
4. Keep the existing advance()/_advance_strategic_period path working for non-interactive use
   (harness autopilot) with a flag so it can still run straight through (interrupts auto-resolved
   with the default option).
5. Tests: pause+resume equals an uninterrupted run with the same choices (same state hash per
   day), multiple interrupted players in one slot, an interrupt in the ambient night chain, an
   interrupt on the last slot of the period, a player dying while paused, determinism.
STOP and report.

STAGE B: tactical options (Phase 6)
1. content/tactical_options.json, pydantic-validated: id, label, description, requirements
   (items, stat minimums, location flags), costs (stamina, items, noise), the effect on encounter
   outcome odds (modifiers in the same format as content/encounter_outcomes.json, logged with
   source "tactic:<id>"), follow-up effects from the closed effect vocabulary (barricade adds a
   temporary fortified flag; hide lowers noise; investigate may reveal information; eat consumes
   food for a small recovery), and a status live|draft.
2. Options: investigate, hide, ambush, barricade, flee, fight, eat, hold. Each has requirements.
3. The engine computes the available options for a pending encounter from state (a function in
   encounter_resolution.py); the default option (used for timeouts and disconnects) is "hold" or
   the safest available, defined in content.
4. resolve_encounter_after_action takes an optional chosen option and applies its modifiers
   and costs; with no option it behaves exactly as today (so the golden path and autopilot are
   unchanged).
5. Harness bots choose options by a simple policy per bot type.
6. Tests: availability by state, requirements enforced, the option shifts odds in the intended
   direction, invalid option rejected, default is valid, determinism.
STOP and report.

STAGE C: server flow (main.py, rooms_store, websocket)
1. Replace the single tick.advance(hours=24*commitment_days) in _advance_policy_period with the
   pausable loop. If a PausedRun is returned: persist it in the room state (pausedRun), broadcast
   world_paused to everyone (day, slot, who is deciding, deadline; no private details) and
   decision_prompt to each interrupted player (runId, scene summary from engine data, options with
   labels/costs/requirements, deadline). Add client->server message "interrupt_choice"
   {runId, optionId}.
2. A shared timer (INTERRUPT_TIMEOUT_SECONDS in config). When all interrupted players have chosen
   or the timer fires, apply defaults for the rest, call resume_period, then continue the round
   (round_complete as today) or pause again.
3. Idempotency and safety: ignore duplicate, late or stale choices (runId/version check); validate
   that the sender is an interrupted player; use get_room_lock for all state changes; reject
   invalid options with an error message.
4. Disconnect/reconnect: a disconnected interrupted player gets the default at the timer; on
   reconnect the client state includes their pending prompt. Server restart: on startup, rooms
   with a pausedRun reschedule their timer.
5. Persist pausedRun with the room (save_room) and include a player-scoped view of it in
   build_client_state.
6. Narration: after the outcome is resolved, use the existing narrator with the encounter outcome
   and the chosen option; no new LLM call for the prompt itself.
7. Tests: end-to-end with a fake websocket: pause, choose, resume; timeout default; duplicate
   choice ignored; reconnect shows the prompt; two players interrupted together; one player
   interrupted while another is not; restart recovery.
STOP and report.

STAGE D: pacing for multiplayer
1. Harness: add a lockstep multi-player mode (K=1..4 bots) that uses the pausable loop and bot
   option choices, and reports the WORLD-level pause rate and gap between world pauses (not just
   per player), pauses per player, option usage, injury/death rates by option, and the usual economy
   tables.
2. Add a player-count pacing factor as a factor record in content (a counter on the interrupt
   step driven by the number of living players) so the world-level gap stays about 5-10 days
   (days 1-20) for K=1..4 for the careful bot. Use the harness to calibrate. The golden uses frozen
   content, so it is unaffected.
3. Targets (PASS/WARN): world gap 5-10 days for careful at K=1,2,3,4; careful_planned clearly
   fewer pauses; careful_sloppy clearly more; option choice measurably changes outcomes.
Report the tables. STOP.

STAGE E: frontend (index.html)
1. A decision panel: scene text, option buttons showing costs/requirements, a countdown to the
   deadline, and a disabled state with the reason for unavailable options.
2. A "world paused" banner for other players showing who is deciding and the countdown, with no
   private details.
3. Handle world_resumed, reconnect (re-show the prompt), stale runId (ignore), and an error
   message for an invalid choice.
4. Manual test checklist in the report (2 browser tabs).
STOP and report.