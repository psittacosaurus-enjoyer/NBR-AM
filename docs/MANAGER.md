# Manager and loot behavior

See [the README](../README.md) for installation, stages and controls.

The GUI delegates to the existing controller through `src/manager_backend.py`. It observes process status independently of command completion, restores elapsed time from supervisor creation time where available, and tails the rotating activity log. Closing the GUI does not stop the supervisor or loot collection.

## Durable sessions and counters

Loot sessions belong to a supervisor PID plus creation time (or direct worker identity). They survive worker reconnects and GUI reopening; a new farming process gets fresh totals. Historical sessions remain available. History pages contain 100 runs. Normal exit ends the session; later observations reconcile abnormal exits and PID reuse. A stale collector heartbeat shows Tracking Unavailable.

Observed Icons counts verified acquired-grid identities. Candidate, ambiguous and unknown identities remain visible separately. Confirmed Acquired counts only explicit acquired quantities. Rarity stars, Lucky Drop labels, repeated icons, Ryo and Completion Reward Conditions never imply an acquired amount. Unknown quantities remain Unknown; mixed totals are known only.

Only newly verified MISSION_COMPLETE runs emit durable events. The READY_TO_RETRY observation saves its already captured results frame, without extra device polls. A worker-owned background collector uses the read-only library and recognizer APIs. Completion IDs combine process session and run UUID; uniqueness prevents duplicate replay, reprocessing or reopening from increasing totals. Durable events assign sequence numbers before recognition.

Records live in `runtime/loot/loot.sqlite3` (SQLite WAL), with replayable events/evidence in `runtime/loot/pending/`. Recognition has eight pending jobs and bounded memory. Overload records failure with evidence. Persistence failures preserve events/evidence and retry up to three times per collector; `runtime/loot/persistence_error.json` reports unresolved errors. Farming continues when collection fails.

Unknown/candidate/ambiguous identities, incomplete parsing and failures retain evidence in `runtime/loot/diagnostics/`. Temporary copies are removed only after structured records commit. Fully identified grids do not retain screenshots solely because quantities are unknown. Historical records are not backfilled from old screenshots. Workers predating instrumentation cannot supply loot evidence.

Each collector holds one library/reference/calibration snapshot. Start a new worker to load revisions; reopening the GUI only rereads persisted history. Automatic calibration requires the exact database revision. See [recognition details](RECOGNITION.md).

## Safety and controller limits

Start launches an independent hidden supervisor; duplicate starts are guarded. Stop is account-wide, persists the explicit stop, blocks further inputs and restarts, and allows a bounded operation to complete. A later explicit Start retains pending latches and stamina deadlines. The controller verifies BlueStacks mapping and Android identity before actions and after reconnection.

Verified titles authorize ordinary battle/results handling after a stage switch. Two fresh agreeing observations gate inputs. Unknown screens, unverified popup variants and unresolved transitions receive no guessed input. Detected loss persists STOP, marks the run failed, saves evidence and requests a local Windows alert; it does not revive, decline or exit the loss dialog. The verified insufficient-stamina prompt is declined once and starts a persisted ten-minute natural-regeneration wait. Unverified dialogs remain observation-only.
