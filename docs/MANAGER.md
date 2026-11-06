# Manager 1.25

Choose your BlueStacks instance on **Instance**, open a mission's **Start Mission** screen there, then press **Start** on Dashboard. Universal handles verified Start, completion/results, rewards, Retry and natural stamina waiting. The game manages combat.

During Universal battles, two fresh observations of Auto OFF enable Auto once. The controller waits for confirmed Auto ON or mission progression before another toggle, including after a restart. Auto ON is left enabled. Ninja Road keeps its own combat controls.

The sidebar contains exactly Dashboard, Instance and Settings. Instance discovery reads installed configuration and displays friendly names separately from internal identifiers and ADB addresses. Refresh is passive. Applying a selection requires a verified Android identity and persists it locally; a missing saved target is shown as unavailable rather than replaced by another instance. Manual Setup resolves an exact name or identifier, with an advanced explicit ADB endpoint fallback.

Selection is locked during active farming and unknown process state. Each process pins its target across reconnects and worker recovery. Changing a stopped selection keeps that instance's saved progress separate from other instances. The legacy `acc1`/`Pie64` mapping retains its existing progress, pending input and stamina state. Start, Stop and Restart remain available through the existing command-line interface.

Closing the manager with **X** leaves the independent supervisor and loot collector running. Reopening restores process status, elapsed time and persisted loot without issuing a farming command. See [installation and controls](../README.md).

## Appearance and optional details

Fresh settings use **Original**. **Settings → Appearance** also offers **Retro 98**. Both appearances share the same controls and loot display.

For Ninja Road, **Retreated Ninja Roads** shows that instance's saved total of confirmed retreats, beside the status in Original and in the Farming section in Retro 98. It increases after the retreat results are verified and persists across Stop, Restart, and reopening the manager; it is not a session counter. `—` means the matching Ninja Road count is unavailable.

The Ninja Road selector offers three modes (internal ids in brackets; a run keeps the mode it started with, and modes can only be changed while stopped):

* **Stage 11 Exit** (`stage10`, default) - the original rotation. With **Early Exit** on, it retreats when Map 11 comes up; Boss Swap remains available.
* **Stage 16 Exit** (`stage15_clean`) - a clean run: Auto on, Ninjutsu off on Maps 1-4 and on from Map 5, Secret Techniques always off, default team rotation (Team 1 until defeated, then the next living team; One Team Run respected), and the guarded retreat when Map 16 comes up. No per-map teams, skill sequences or backline swaps. Optional **Team 2 on Map 13** (`ninja_map13_team2`, `farm.py map13-team2 --enabled on|off`) switches to Team 2 on Map 13's Ready screen and back to Team 1 on Map 14, using the verified arrow selection; it is skipped when the target team is defeated or missing, and One Team Run overrides it. With it on, defeat recovery may return to an earlier living team.
* **Stage 16 Strategy** (`stage15`) - the full strategy: per-map teams (Team 2 on Map 7, Team 3 on Maps 11-12), chakra-checked frontline/backline skill sequences on Maps 7, 9, 12 and 13, Secret Techniques on for Maps 10 and 15, and the same Map 16 retreat. Usage checks have bounded timeouts that restore known roles.

Both Stage 16 modes read the map counter, check settings only when the controls are visible, and pause instead of guessing on unknown counters, controls or unconfirmed input.

**Settings → Activity log** shows controller events and manager errors. **Settings → Recognition details** shows observed/completed/failed recognition counts, quantity evidence, historical sessions, run history, warnings and retained screenshots. Both settings start off and persist independently of appearance. Changing appearance or visibility does not command the controller.

Loot is always visible without expanding a menu. Each tile uses artwork from the matched Game Asset Library identity and overlays its acquired drop count: one visible acquired icon means one drop. Unidentified drops have a separate tile. `?` means the drop count is unavailable. Hover for evidence and the full item name. Missing or unusable artwork has an explicit placeholder.

Pending recording work and failures show a compact status even when Recognition details is collapsed, in both appearances. It reports outstanding jobs, captures awaiting evidence and jobs requiring attention. Hover for the error and scheduled retry time; Recognition details also shows per-run errors, states and retries. Monitoring does not issue a controller command. During a database outage, disk-backed pending work remains visible; completed-history totals are temporarily unavailable.

Artwork resolves by exact record ID and explicit aliases, honoring preferred eligible assets. Item names and similar images never establish identity. Library lookup runs with the background loot read and does not modify the library.

## Sessions and evidence

Loot sessions belong to a supervisor PID plus creation time, or a direct worker identity. They survive worker reconnects and GUI reopening; a new farming process gets fresh session totals while recovered jobs retain their original session. Recognition details provides historical sessions and pages of 100 runs. Double-click a retained screenshot row to open it. A stale collector heartbeat reports Tracking Unavailable when no outstanding work provides a more specific status.

Observed Icons counts verified acquired-grid identities. Drops counts each visible acquired icon once, independently of any printed quantity. Rarity stars, Lucky Drop labels, Ryo and Completion Reward Conditions do not multiply an acquired drop. Unconfirmed identities remain separate from item-specific credits.

## Recording pipeline and recovery

### Recording transaction

1. A verified MISSION_COMPLETE for the current run saves its original completion time, account, Android device identity, stage and recording transaction in that instance's existing progress file. If progress persistence fails, the worker keeps the transaction in memory and waits before sending another input.
2. The collector atomically saves and verifies a uniquely identified JSON job in `runtime/loot/pending/`. Returning its completion ID acknowledges durable registration; failure leaves registration pending and blocks results navigation. New job IDs derive from account, device, stage and run identity. Repeated submissions use the same job and completion time.
3. On READY_TO_RETRY, the worker waits for rewards to finish revealing and saves its already captured results frame. The collector writes a temporary PNG, flushes and fsyncs it, decodes it, checks its SHA-256 digest, atomically replaces the final file and saves verified job metadata. Automatic Retry stays blocked until both screenshot persistence and the farmer's acknowledgement are saved. Failed writes remain eligible for another capture.
4. Once evidence is acknowledged, farming can continue. The existing background collector maintains the SQLite projection of registered jobs and runs read-only recognition on saved evidence. It never delays Retry for reference loading or recognition. SQLite completion constraints and guarded final updates prevent replay from crediting rewards twice.
5. After SQLite commits, a durable receipt is written before pending files are removed. The receipt also acknowledges an already recorded job if the farmer restarts before saving its screenshot acknowledgement, including during a later SQLite outage.

Recording states are persisted in the existing farm progress and job/status records:

| State | Meaning |
| --- | --- |
| `completion_registration_pending` | The verified completion transaction is saved; durable job registration is still required. |
| `screenshot_capture_pending`, `awaiting_evidence`, `capture_pending` | A registered job still needs verified screenshot evidence. |
| `screenshot_saved` | The farmer has persisted the screenshot acknowledgement and may continue. |
| `recognition_pending`, legacy `ready` | Saved evidence awaits background processing. |
| `recognized` | Recognition and accounting committed successfully; displayed as Successfully Recorded. |
| `retryable_failure` | Retained work has a scheduled retry. |
| `operator_attention` | Retained evidence has a non-retryable integrity/safety fault; normal capture failures use automatic retry instead. |
| `collector_initialization_retry` | Collector startup is unavailable and will retry within the same worker. |

Existing recognition outcomes such as `uncertain`, `incomplete_results` and unsupported layouts retain their established meaning and diagnostic evidence. Recognition confidence thresholds and image matching are unchanged.

### Retry and restart recovery

Temporary SQLite, file access, runtime/import and recognition infrastructure failures retain pending metadata and evidence. The collector persists retry counts and the next attempt time; exponential backoff has no terminal attempt limit. Its processing batch limit is separate from the disk backlog, so excess jobs wait on disk rather than becoming failed overload records. A failed heartbeat update does not prevent job replay.

Collector initialization retries within the existing farming worker, with backoff, without requiring a new farming session. Registration and capture remain protected while it is unavailable. Farmer registration/capture failures stay retryable; after three attempts the recovery tier broadens and continues automatically, with mission progression paused only until evidence is safe. Progress-file persistence failures retain the in-memory completion transaction and the worker/controller recovery loop retries without ending the session.

On Universal worker shutdown for a configured BlueStacks instance, an unfinished registered completion gets a final read-only capture window before the collector closes. It allows up to four seconds for an already running reward animation to settle, and saves evidence only when two fresh observations verify fully revealed results on the same Android device. This attempt sends no game input. If results are not ready within that window, device access fails, or storage fails, the original pending job and input latch remain available for recovery. A force-killed process cannot perform this final capture. Advanced manual endpoints preserve their pending work for ordinary restart recovery instead of running the shutdown capture.

Stopping or exiting preserves capture-pending jobs. A new process can resume a saved farm transaction only for its matching account, device and stage. Legacy progress without a transaction may adopt an existing persisted outbox job only when run, account and stage match and the stored device is compatible with the pinned target. While the collector is unavailable, legacy completed progress waits for that verification. Old counters and unrelated results screens cannot create past completion jobs. Saved pending input latches continue to prevent duplicate inputs.

If an interruption or navigation has already left results, two fresh agreeing observations of Start Mission or battle prove that the old screenshot can no longer be captured. The live worker then verifies the exact run/account/device/stage transaction, durably records `missing_screenshot` with zero reward credits, audits the original latch and resumes farming automatically. Existing or recoverable screenshot evidence is never abandoned. The stopped-only `resolve-missing-evidence` commands remain forensic repair tools for cases where live recovery itself cannot run; normal farming does not depend on them.

Saved evidence can finish under its original session after a process restart. Changed library/calibration revisions are loaded before subsequent recognition attempts. `Collector.retry(completion_id)` supports retrying retained pending evidence requiring attention after its checksum is verified, under the same accounting ID. This is an operator/developer API; there is no new GUI or CLI reprocessing control, and finalized history is not automatically reprocessed. See [recognition details](RECOGNITION.md) for calibration and coverage limits.

### Limits, storage and upgrade behavior

The following are source defaults, not new Manager settings:

| Setting | Default and behavior |
| --- | --- |
| Farmer `LOOT_MAX_ATTEMPTS` | 3 failures before broader automatic recording recovery; safe retries continue without an operator gate. |
| Farmer `LOOT_RETRY_BASE_SECONDS` / `LOOT_RETRY_MAX_SECONDS` | 1 / 30 seconds for registration, capture and collector initialization backoff. Progress-save failures use the worker's 5-second initial backoff, capped at 30 seconds. |
| Collector `retry_base` / `retry_max` | 0.2 / 30 seconds; retry scheduling survives restarts. |
| Collector `max_pending` | 8 recognition jobs per background scan, independently of backlog size. |
| Collector `max_jobs` | 10,000 pending JSON jobs; new registration pauses at the limit without deleting existing work. |
| Collector `max_storage_bytes` | 1 GiB screenshot admission limit, counting PNGs in pending and diagnostics. Old finalized diagnostics are pruned when necessary, targeting 80% usage and reserving capacity for a new capture and diagnostic copy. |

`runtime/loot/loot.sqlite3` keeps session/completion history using the existing tables and uniqueness constraints; this release performs no destructive database migration. Version 2 pending JSON adds identity, retry and screenshot-verification metadata. Valid older pending jobs remain readable and keep their original IDs. Malformed pending metadata is moved to `runtime/loot/quarantine/` with an error record; transient file-access errors leave it in place for retry.

Pending jobs/evidence are never evicted to meet a limit. Confirmed screenshots are removed from pending storage only after accounting and the durable receipt commit. Uncertain or invalid recognition retains an atomic diagnostic copy in `runtime/loot/diagnostics/`. When capacity is needed, only old diagnostics belonging to finalized completions are eligible for pruning. The newest three finalized diagnostics per account and evidence referenced by calibration, tests, validation, recovery or active progress remain protected. Receipts, SQLite results, quarantine and pending work are retained. If protected evidence fills the budget, captures remain paused with the used, required and allowed byte counts.

`runtime/loot/retention_journal.json` records each pruning operation's completion, path, checksum, size and deletion state. Interrupted operations reconcile before subsequent admission. Pruning clears the obsolete screenshot link and adds a history warning without changing recognition results or drop counts. Admission and pruning share a cross-process lock. Keep the journal with loot backups.

Existing optional validation capture remains opt-in through `NBR_LOOT_VALIDATION_DIR` or an enabled, expiring `runtime/validation/validation_capture.json`. Its directory must be inside `runtime/validation/`; the default capture count is 4 and the supported maximum is 10. Those validation copies are separate from the production screenshot admission limit.

### Troubleshooting recording failures

- For registration/capture failures, inspect the compact status tooltip and Recognition details. Automatic bounded retries and broader recovery continue; restoring disk space or permissions simply lets the next attempt succeed.
- For temporary SQLite or reference-loading failures, retain pending files and allow the scheduled retries. `runtime/loot/health_<account>.json` reports collector health; `runtime/loot/persistence_error.json` preserves the unresolved recording error. Capture errors retain their completion ID and operation separately from health-publication errors, so a status-file failure cannot conceal a full recording budget.
- Storage admission automatically prunes eligible finalized diagnostics. If protection or a filesystem failure prevents reclamation, the actual capacity error stays visible and pending work remains recoverable. Health-file publication uses bounded retries for Windows access/sharing conflicts; persistent status-publication errors appear separately and cannot invalidate a durable screenshot acknowledgment.
- For identity mismatches, restore the pinned account/device mapping before resuming; do not alter transaction identity to bypass the guard. For quarantine or invalid evidence, inspect the retained error/evidence before operator recovery.

An already-running worker keeps its loaded code until the user stops or restarts it. Reopening the Manager loads the current interface and rereads persisted health/history without restarting farming. The 1.25 cleanup preserves runtime paths and leaves running controllers untouched. See [validation results](VALIDATION.md).

If the original reward image was never saved and results have been left, explicit offline closure is available while all controllers are stopped:

```powershell
.\.venv\Scripts\python.exe farm.py resolve-missing-evidence --offline --completion-id <exact-completion-id> --reason "Original reward evidence was not saved; results were left"
```

This requires the existing Stop flag, matching account/device/stage and transaction identity, no pending input, and no recoverable evidence for that completion. It records the original completion as missing evidence with zero reward credits, resets mission verification and retains Stop. Repeated closure cannot duplicate credits. The live command without `--offline` normally requires two fresh Start Mission observations. A departed Universal `next / RESULTS_NEXT` or `reveal_rewards / REWARD_REVEAL` latch can also be resolved from two fresh, identity-verified Ninja Road Home observations. The Home path requires Stop, validates the original durable job and absence of retained evidence, and preserves the complete original latch and observations in its audit. Reveal recovery requires Home; it does not accept Start Mission. Neither path reassigns another mission's screenshot to the missing transaction.

After two agreeing observations of a new Start Mission screen or battle, an unresolved completed Universal run permanently loses capture eligibility for later rewards. The worker closes that exact transaction automatically as missing evidence with zero credits and resumes from the verified state. Unknown screens, elapsed time and normal same-run rank/stamina/results transitions alone do not revoke capture eligibility.

Explicit connection checks allow three seconds for Windows to establish or refuse the local ADB socket, then use a separate 1.5-second response timeout. A refused socket starts the local server before connecting the selected instance. A response timeout from an existing server does not trigger a server replacement.

## Stop, stamina and restart

**Stop** persists an account-wide stop and allows a bounded game operation to finish. Duplicate starts are guarded. Device mapping and Android identity are verified before inputs and after reconnection. Two fresh agreeing observations gate inputs; unknown screens and unverified dialogs receive no guessed input. Recognition, ADB, foreground, transition and connection failures use logged bounded recovery and verified same-device relaunch. Detected loss/revival stops only because no verified Pearl-free exit is cataloged.

**Exit & Stop Program** asks for confirmation, uses the controller's safe stop, then checks for remaining NBR automation processes and their private children. After a bounded wait it can terminate only processes whose NBR ownership and creation identity are verified. BlueStacks, the game, unrelated Python processes and shared services stay open. Status and the activity log show progress; the manager closes only after shutdown is verified. If cleanup fails, it stays open and displays an error so you can retry. The button waits for any current controller command to finish before becoming available.

Saved pending inputs and natural stamina deadlines remain protected across upgrades. Normal farming resolves transitions through bounded automatic recovery and fresh visual checks. An intentional switch between farming modes can still require the separate guarded Resolve input workflow below to establish the newly chosen mission.

When Universal Start reports an existing saved input, **Resolve input** becomes available. Stop farming and prepare the desired Universal mission's Start Mission screen, then select **Resolve input** and **Start**. Recovery requires two fresh identity-verified Start Mission observations. It can close a superseded Ninja Road menu input or an interrupted Boss Swap restore swipe; it archives the complete original state and both observations before clearing the latch. An abandoned Ninja Road run is retained as abandoned. Connection, strategy, same-stage and unresolved reward inputs remain protected; Resolve input never discards missing reward evidence.

The CLI equivalent is `python farm.py resolve-saved-input --stage universal --reason "Prepared a new Universal mission after leaving Ninja Road"`. Recovery leaves farming stopped; use `start --stage universal` afterwards.

Stamina shows WAITING, READY or UNKNOWN and a countdown from the worker's persisted deadline. READY means the delay has elapsed; fresh game checks still gate farming. Choose 5, 10, 15, 20 or 25 minutes. The initial duration is ten minutes; unsupported saved values fall back to five. Changes affect subsequent waits.

**End wait** interrupts a verified live wait with no pending input and requests fresh game/stamina checks. Insufficient stamina starts another persisted wait. Requests for another worker or a replaced deadline are rejected.

**Restart** safely stops and confirms all recorded controller processes have exited before reconnecting, verifying identity and launching Universal. It preserves pending input latches, counters, natural stamina deadlines, loot history and logs. A concurrent Stop cancels relaunch; failure to confirm shutdown launches nothing. BlueStacks remains open. Each step is recorded in the optional activity log.

CLI equivalents are `start`, `stop`, `restart`, `end-stamina-wait`, and `stamina-duration --minutes 5|10|15|20|25`.

## Source organization

The manager window and presentation code live in `src/app/gui/`; the controller adapter and loot collector live in `src/app/backend/`. The farming engine and BlueStacks/ADB implementation live in `src/automation/`. Python callers import those packages directly. Root launchers provide the application entry points, while `src/farm.py` remains the controller subprocess entry point; see [the project layout](../README.md#project-layout).

The [preferred Retro layout reference](images/preferred_retro_layout.png) is retained as design documentation. It is not loaded by the application.
