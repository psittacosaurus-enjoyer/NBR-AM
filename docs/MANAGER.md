# Manager 1.25

Choose your BlueStacks instance on **Instance**, open a mission's **Start Mission** screen there, then press **Start** on Dashboard. Universal handles verified Start, completion/results, rewards, Retry and natural stamina waiting. The game manages combat.

The sidebar contains exactly Dashboard, Instance and Settings. Instance discovery reads installed configuration and displays friendly names separately from internal identifiers and ADB addresses. Refresh is passive. Applying a selection requires a verified Android identity and persists it locally; a missing saved target is shown as unavailable rather than replaced by another instance. Manual Setup resolves an exact name or identifier, with an advanced explicit ADB endpoint fallback.

Selection is locked during active farming and unknown process state. Each process pins its target across reconnects and worker recovery. Changing a stopped selection keeps that instance's saved progress separate from other instances. The legacy `acc1`/`Pie64` mapping retains its existing progress, pending input and stamina state. Start, Stop and Restart remain available through the existing command-line interface.

Closing the manager with **X** leaves the independent supervisor and loot collector running. Reopening restores process status, elapsed time and persisted loot without issuing a farming command. See [installation and controls](../README.md).

## Appearance and optional details

Fresh settings use **Original**. **Settings → Appearance** also offers **Retro 98**. Both appearances share the same controls and loot display.

For Ninja Road, **Retreated Ninja Roads** shows that instance's saved total of confirmed retreats, beside the status in Original and in the Farming section in Retro 98. It increases after the retreat results are verified and persists across Stop, Restart, and reopening the manager; it is not a session counter. `—` means the matching Ninja Road count is unavailable.

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
| `operator_attention` | Recording is paused or retained evidence requires investigation. |
| `collector_initialization_retry` | Collector startup is unavailable and will retry within the same worker. |

Existing recognition outcomes such as `uncertain`, `incomplete_results` and unsupported layouts retain their established meaning and diagnostic evidence. Recognition confidence thresholds and image matching are unchanged.

### Retry and restart recovery

Temporary SQLite, file access, runtime/import and recognition infrastructure failures retain pending metadata and evidence. The collector persists retry counts and the next attempt time; exponential backoff has no terminal attempt limit. Its processing batch limit is separate from the disk backlog, so excess jobs wait on disk rather than becoming failed overload records. A failed heartbeat update does not prevent job replay.

Collector initialization retries within the existing farming worker, with backoff, without requiring a new farming session. Registration and capture remain protected while it is unavailable. Farmer registration/capture failures become Operator Attention Required after three attempts; the worker keeps observing and retrying safely, with mission progression paused until recovery. Progress-file persistence failures also pause inputs and retain the in-memory completion transaction for another save attempt.

On Universal worker shutdown for a configured BlueStacks instance, an unfinished registered completion gets a final read-only capture window before the collector closes. It allows up to four seconds for an already running reward animation to settle, and saves evidence only when two fresh observations verify fully revealed results on the same Android device. This attempt sends no game input. If results are not ready within that window, device access fails, or storage fails, the original pending job and input latch remain available for recovery. A force-killed process cannot perform this final capture. Advanced manual endpoints preserve their pending work for ordinary restart recovery instead of running the shutdown capture.

Stopping or exiting preserves capture-pending jobs. A new process can resume a saved farm transaction only for its matching account, device and stage. Legacy progress without a transaction may adopt an existing persisted outbox job only when run, account and stage match and the stored device is compatible with the pinned target. While the collector is unavailable, legacy completed progress waits for that verification. Old counters and unrelated results screens cannot create past completion jobs. Saved pending input latches continue to prevent duplicate inputs.

If an interruption or manual navigation has already left results, the missing screenshot cannot be recaptured from Start Mission. The controller identifies this case in its blocked message and points to the recovery command. Stop farming and explicitly resolve that completion with `python farm.py resolve-missing-evidence --reason "Results left after interruption"` using the repository environment. Recovery requires the same verified instance, two fresh Start Mission observations, no active controller and no pending input. It preserves the original completion and counters, records `missing_screenshot` with the supplied reason and credits no rewards. Existing screenshot evidence cannot be abandoned this way. The durable acknowledgement survives a crash before the farmer saves its resolution; Start can then begin a new recording transaction.

Saved evidence can finish under its original session after a process restart. Changed library/calibration revisions are loaded before subsequent recognition attempts. `Collector.retry(completion_id)` supports retrying retained pending evidence requiring attention after its checksum is verified, under the same accounting ID. This is an operator/developer API; there is no new GUI or CLI reprocessing control, and finalized history is not automatically reprocessed. See [recognition details](RECOGNITION.md) for calibration and coverage limits.

### Limits, storage and upgrade behavior

The following are source defaults, not new Manager settings:

| Setting | Default and behavior |
| --- | --- |
| Farmer `LOOT_MAX_ATTEMPTS` | 3 failures before visible operator attention; safe retries continue. |
| Farmer `LOOT_RETRY_BASE_SECONDS` / `LOOT_RETRY_MAX_SECONDS` | 1 / 30 seconds for registration, capture and collector initialization backoff. Progress-save failures use the worker's 5-second initial backoff, capped at 30 seconds. |
| Collector `retry_base` / `retry_max` | 0.2 / 30 seconds; retry scheduling survives restarts. |
| Collector `max_pending` | 8 recognition jobs per background scan, independently of backlog size. |
| Collector `max_jobs` | 10,000 pending JSON jobs; new registration pauses at the limit without deleting existing work. |
| Collector `max_storage_bytes` | 1 GiB screenshot admission limit, counting PNGs in pending and diagnostics; new captures pause at the limit. |

`runtime/loot/loot.sqlite3` keeps session/completion history using the existing tables and uniqueness constraints; this release performs no destructive database migration. Version 2 pending JSON adds identity, retry and screenshot-verification metadata. Valid older pending jobs remain readable and keep their original IDs. Malformed pending metadata is moved to `runtime/loot/quarantine/` with an error record; transient file-access errors leave it in place for retry.

Pending jobs/evidence are never evicted to meet a limit. Confirmed screenshots are removed from pending storage only after accounting and the durable receipt commit. Uncertain or invalid recognition retains an atomic diagnostic copy in `runtime/loot/diagnostics/`. Receipts in `runtime/loot/receipts/`, SQLite history, diagnostics and quarantine records have no automatic age-based deletion. The screenshot limit controls new admission, rather than pruning retained data. Archive completed diagnostic screenshots manually when needed, preserving backups; opening their old paths then requires restoring them. Do not delete pending jobs/evidence to make room.

Existing optional validation capture remains opt-in through `NBR_LOOT_VALIDATION_DIR` or an enabled, expiring `runtime/validation/validation_capture.json`. Its directory must be inside `runtime/validation/`; the default capture count is 4 and the supported maximum is 10. Those validation copies are separate from the production screenshot admission limit.

### Troubleshooting recording failures

- For registration/capture failures, inspect the compact status tooltip and Recognition details. Restore free disk space and write permissions for the instance's progress file and `runtime/loot/`; leave the results screen available while safe retries continue.
- For temporary SQLite or reference-loading failures, retain pending files and allow the scheduled retries. `runtime/loot/health_<account>.json` reports collector health; `runtime/loot/persistence_error.json` preserves the last unresolved persistence error. The latter is cleared after that collector's backlog drains.
- For storage-limit errors, restore recognition processing or archive backed-up completed diagnostics. Pending work remains recoverable on disk.
- For identity mismatches, restore the pinned account/device mapping before resuming; do not alter transaction identity to bypass the guard. For quarantine or invalid evidence, inspect the retained error/evidence before operator recovery.

An already-running worker keeps its loaded code until the user stops or restarts it. Reopening the Manager loads the current interface and rereads persisted health/history without restarting farming. The 1.25 cleanup preserves runtime paths and leaves running controllers untouched. See [validation results](VALIDATION.md).

## Stop, stamina and restart

**Stop** persists an account-wide stop and allows a bounded game operation to finish. Duplicate starts are guarded. Device mapping and Android identity are verified before inputs and after reconnection. Two fresh agreeing observations gate inputs; unknown screens and unverified dialogs receive no guessed input. Detected loss stops farming and retains evidence for manual resolution.

**Exit & Stop Program** asks for confirmation, uses the controller's safe stop, then checks for remaining NBR automation processes and their private children. After a bounded wait it can terminate only processes whose NBR ownership and creation identity are verified. BlueStacks, the game, unrelated Python processes and shared services stay open. Status and the activity log show progress; the manager closes only after shutdown is verified. If cleanup fails, it stays open and displays an error so you can retry. The button waits for any current controller command to finish before becoming available.

Saved pending inputs and natural stamina deadlines remain protected across upgrades. If an unresolved saved input prevents starting, resolve the game state manually. Restore a supported Start Mission screen after reaching an unsupported menu.

Stamina shows WAITING, READY or UNKNOWN and a countdown from the worker's persisted deadline. READY means the delay has elapsed; fresh game checks still gate farming. Choose 5, 10, 15, 20 or 25 minutes. The initial duration is ten minutes; unsupported saved values fall back to five. Changes affect subsequent waits.

**End wait** interrupts a verified live wait with no pending input and requests fresh game/stamina checks. Insufficient stamina starts another persisted wait. Requests for another worker or a replaced deadline are rejected.

**Restart** safely stops and confirms all recorded controller processes have exited before reconnecting, verifying identity and launching Universal. It preserves pending input latches, counters, natural stamina deadlines, loot history and logs. A concurrent Stop cancels relaunch; failure to confirm shutdown launches nothing. BlueStacks remains open. Each step is recorded in the optional activity log.

CLI equivalents are `start`, `stop`, `restart`, `end-stamina-wait`, and `stamina-duration --minutes 5|10|15|20|25`.

## Source organization

The manager window and presentation code live in `src/app/gui/`; the controller adapter and loot collector live in `src/app/backend/`. The farming engine and BlueStacks/ADB implementation live in `src/automation/`. Existing `src/manager_*.py`, `src/loot_tracking.py` and `src/farm.py` imports remain supported through compatibility modules. Root launchers provide the same application entry points; see [the project layout](../README.md#project-layout).

The [preferred Retro layout reference](images/preferred_retro_layout.png) is retained as design documentation. It is not loaded by the application.
