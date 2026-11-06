# NBR architecture

The authoritative technical description of NBR. Usage: `README.md`. Quick AI orientation: `CLAUDE.md`. Loot pipeline details: `MANAGER.md`. Reward recognition: `RECOGNITION.md`. Library: `LIBRARY.md`. `VALIDATION.md` and `CLEANUP-1.25.md` are historical records only.

## Components

```text
Manager GUI (PySide6, optional)        farm.py / nbr.ps1 CLI
   src/app/gui  ─────────────┐         │
   src/app/backend/controller.py ──────┤  start / stop / status (process adapter; no farming logic)
                                       ▼
                      supervise()  ── one per account, independent of the GUI
                         │ restarts the worker with backoff if it exits
                         ▼
                      worker()  ── holds the account input lock, owns one Engine
                         │ loop: delay = engine.tick(); exceptions → bounded recovery
                         ▼
   Engine (Universal)  /  NinjaRoadEngine (Ninja Road)
      Device   ADB: identity, foreground, capture, guarded input   (automation/controller.py + adb.py)
      Detector catalog-driven template matching                    (assets/patterns/*.json)
      safety   Pearl guards                                        (automation/safety.py)
      loot     RecoveringLootCollector → app/backend/loot.py      (never blocks recovery)
```

The GUI is a view: it reads `runtime/*.json` and `events.jsonl` and starts/stops processes. Closing it does not affect farming. There is exactly one supervisor and one worker per account (file locks `control`, `supervisor`, `worker`; process identities in `supervisor.json` / `controller.json`).

## The tick (Universal)

`Engine.tick()` is the state machine. One tick:

1. Honour `STOP`; consume a stamina-wait skip request.
2. Identity check (`Device.verify`: BlueStacks mapping + Android ID) — at most every `IDENTITY_RECHECK_SECONDS` on observation ticks. **Input always re-verifies** inside `Device._perform`.
3. Foreground check (`dumpsys activity`) — skipped while the last screen was a settled, recognized game screen checked within `FOREGROUND_RECHECK_SECONDS`. Not foreground → foreground recovery.
4. Capture (raw `screencap`, PNG fallback) and classify. Any recognized screen must be seen **twice in agreement** (three times with stable geometry for moving lists).
5. `update()`: screen events, pending-transition confirmation, run start/completion/failure bookkeeping, loot recording hooks; state persisted.
6. UNKNOWN / LOADING → transition handling (below). Otherwise choose the catalog action for the state, unless blocked (pending transition, stamina wait, maintenance wait, loot recording gate).
7. Persist `pending`, then dispatch through `_perform_verified_action`: a final fresh observation must show the same state and the identical guarded action, then the Pearl guard runs, then input.

States (Universal catalog): `READY_TO_START → (LOADING) → IN_BATTLE → MISSION_COMPLETE → RESULTS_NEXT / TRIAL_COINS_NEXT / RANK_UP_* → REWARD_REVEAL → READY_TO_RETRY → READY_TO_START`, plus `STAMINA_PROMPT`, `MAINTENANCE_PROMPT`, `COMMUNICATION_ERROR`. `IN_BATTLE` is input-free except the verified Auto-OFF toggle. `LOADING` (the game's "Now loading…" overlay, matched as a bright-glyph mask) is observation-only and outranks whatever is visible beneath it.

## Recovery philosophy: detect → wait → recover → verify → resume

Ordinary variation must not stop farming, and uncertainty must never produce a guessed click.

| Situation | Handling |
| --- | --- |
| UNKNOWN/LOADING/disagreeing frames | **Transition window first**: quiet re-observation every `TRANSITION_POLL_SECONDS`, no input, no recovery record, no screenshot. Window (`TRANSITION_GRACE_SECONDS`): 60 s after a sent action, 90 s during a run, 20 s otherwise. |
| Still unsettled after the window | Recovery ladder (`_recover_unknown`): diagnostic screenshot, retries after 5/10/20 s, then relaunch of the same verified activity (`am start`, never a UI click), repeating. Logged as `recovery_detected` / `recovery_retry_scheduled` / `recovery_escalated`. |
| Screen changes between recognition and dispatch | `ScreenChanged` → `action_deferred`, re-observe after 0.5 s (no input was sent). After `DISPATCH_DEFER_LIMIT` consecutive deferrals → worker recovery. |
| Source screen still visible after `TRANSITION_TIMEOUT_SECONDS` | Retry the *same* verified action (same geometry, same expected transition), then relaunch. |
| Game not foreground | Bounded observation, then verified relaunch. |
| ADB/capture/command errors | Worker logs `worker_error`, rebuilds the engine (reconnect + identity verification) with 5→60 s backoff. Supervisor restarts a crashed worker with backoff. |
| Emulator crashed / ADB unreachable | Worker crash tier (`crash_recovery.py`): after `UNREACHABLE_RESTART_SECONDS` (180 s) of unreachable-ADB errors, or 2 errors with the instance's HD-Player process gone, close exactly that instance's PID (window close, then force), relaunch `HD-Player.exe --instance <id>`, wait for `sys.boot_completed`, re-verify identity, `am start`. Events `emulator_restart_*`. Budget (persisted in `crash_recovery.json`): 4/hour, then a 30 min cooldown; 3 h without a recognized screen → `crash_recovery_exhausted` + STOP. `farm.py crash-test` asks the running worker to do one restart on purpose. |
| Game title / quicksave after a restart (Ninja Road) | `TITLE_START` (static Delete Cache / Data Transfer / Discord anchors) → tap the fixed Tap To Start point (450, 1008). Seeing the title releases any input latched before the crash (`ninja_pending_ended_at_title`, never replayed). `NR_QUICKSAVE_PROMPT` (exact question text + both buttons) → tap Yes; the interrupted run resumes and is tracked as the same run. Both actions are pinned in `safety.py` (`require_pearl_safe_title_tap`, `require_pearl_safe_quicksave_resume`). Universal stops at the title (no menu navigation yet). |
| Loot recording failures | Retry with backoff in the recording pipeline; never enter device recovery; mission progression pauses only where evidence would be lost. |
| Identity/mapping mismatch (including after an emulator restart), loss/revival screen, recording account mismatch | **Terminal safety stop** (`runtime/STOP`, `worker_safety_stop` / `loss_stop`). |

A recovered state emits `recovery_verified` + `farming_resumed`. Normal transitions emit only `screen` and `transition_confirmed`, so a `recognition_failure` in the log now means a real problem.

## Safety invariants

- Actions: allowlist (`ALLOWED_ACTIONS`) × catalog `verified_safe` × per-state permitted action (`STATE_ACTIONS`) × `safety.require_pearl_safe_action` (geometry/ID screens) × fresh dispatch observation. Binarized (overlay) anchors cannot authorize input.
- `pending` is persisted before input; an uncertain input stays latched until a fresh observation proves the outcome.
- Stop is checked before every ADB input command.
- Identity is re-verified before every input and after every reconnect.

## Performance notes

Per observation tick the worker now runs ~2 ADB commands (two raw captures) instead of ~4 (identity `settings` + `dumpsys activity` + two PNG captures). Raw capture avoids on-device PNG encoding; its exact byte-length/format check falls back to PNG once per process if a device produces anything unexpected. Loss OCR (a PowerShell call) runs at most every `LOSS_OCR_INTERVAL_SECONDS` during a run. Diagnostic UNKNOWN screenshots are only written once recovery starts.

## Stage engines

`create_engine()` returns `Engine` (Universal: whatever mission is open, driven by `universal.json`) or `NinjaRoadEngine` (own catalog, tick and recovery: team rotation, boss swap, stage-15 strategy). With **One Team Run** (`ninja_one_team` in `manager_settings.json`, `farm.py one-team --enabled on|off`), a verified `NR_TEAM_DEFEATED` is observed as `NR_ONE_TEAM_EXIT_PREPARE` / `NR_ONE_TEAM_EXIT` and reuses Early Exit's Auto-off → Retreat → confirm sequence instead of selecting the next team; Stage 15 keeps the current team on every map. Ninja Road shares Universal's responsiveness rules: unrecognized frames inside `TRANSITION_GRACE_SECONDS` are re-observed every `UNKNOWN_POLL_SECONDS` (0.5 s) before its 5/10/20/40 s recovery ladder; a sent tap is re-checked every 0.5 s, idle combat every 2 s; a pre-dispatch `ScreenChanged` defers 0.5 s (bounded by `DISPATCH_DEFER_LIMIT`); stage-engine dispatch compares specs ignoring only recognition evidence (`anchors`, `notes`); observation ticks reuse a foreground check for `FOREGROUND_RECHECK_SECONDS` while every input re-verifies. Tests: `tests/test_ninja_responsiveness.py`. A new mission that only needs different screens is a catalog change, not code. A new stage with its own flow subclasses `Engine` like Ninja Road.

## Testing

`scripts/test_offline.py --suite controller|ninja|library|recognition|all` (see `TESTING.md`). Tests drive engines with saved screenshots and fake devices; `tests/test_transition_grace.py` covers transition windows, LOADING, dispatch deferral, raw capture and check throttling; `test_pearl_safety.py` / `test_safety.py` cover the Pearl guards. Run the controller suite after any change to `controller.py` or a catalog.

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| Many `recognition_failure` events | A screen not in the catalog; its screenshot is in `runtime/screenshots/`. Add a state (see `CLAUDE.md`). |
| `capture_format_fallback` event | Device returned unexpected raw screencap; PNG is used automatically (slower, still correct). |
| `worker_safety_stop` | Wrong/changed device identity — re-select the instance in the Manager. |
| `loss_stop` | Loss/revival screen; NBR stops rather than risk Pearls. Resolve in game, then Start. |
| Stuck "Awaiting confirmed transition" | The transition timeout retry/relaunch ladder handles it; check `transition_*` events. |
