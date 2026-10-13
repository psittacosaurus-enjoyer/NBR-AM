# Manager 1.2 and loot behavior

See [the README](../README.md) for installation, stages and controls.

The GUI delegates to the existing controller through `src/manager_backend.py`. It observes process status independently of command completion, restores elapsed time from supervisor creation time where available, and tails the rotating activity log. Closing the GUI does not stop the supervisor or loot collection.

## Universal Start

The default `universal` mode starts from any supported mission's Start Mission screen prepared manually in acc1. Press Start without choosing a predefined mission. Shared screen handling drives Start → completion/results → Retry, with persisted natural stamina waits and bounded supported interruption recovery. Combat remains entirely managed by the game. Universal mode has no mission menu navigation.

The dropdown keeps the four legacy modes available. A fresh stopped Manager defaults locally to Universal, preserving the CLI's saved selection until an explicit command. Polling preserves the user's local mode choice. Active runs retain their identities, including legacy runs. Reopening never starts, stops or switches a run. While active, the current mission label uses the worker's optional confidently detected `mission_name`; without that information it says not identified. A name is display information, not a prerequisite for farming or a guessed reward attribution.

CLI users can explicitly select `universal` or use `start --stage universal`; existing legacy identifiers and account-wide Stop remain available. The backend also accepts Start without a stage and selects Universal explicitly.

## Durable sessions and counters

Loot sessions belong to a supervisor PID plus creation time (or direct worker identity). They survive worker reconnects and GUI reopening; a new farming process gets fresh totals. Historical sessions remain available. History pages contain 100 runs. Normal exit ends the session; later observations reconcile abnormal exits and PID reuse. A stale collector heartbeat shows Tracking Unavailable.

Observed Icons counts verified acquired-grid identities. Candidate, ambiguous and unknown identities remain visible separately. Confirmed Acquired counts only explicit acquired quantities. Rarity stars, Lucky Drop labels, repeated icons, Ryo and Completion Reward Conditions never imply an acquired amount. Unknown quantities remain Unknown; mixed totals are known only.

Only newly verified MISSION_COMPLETE runs emit durable events. The READY_TO_RETRY observation saves its already captured results frame, without extra device polls. A worker-owned background collector uses the read-only library and recognizer APIs. Completion IDs combine process session and run UUID; uniqueness prevents duplicate replay, reprocessing or reopening from increasing totals. Durable events assign sequence numbers before recognition.

Records live in `runtime/loot/loot.sqlite3` (SQLite WAL), with replayable events/evidence in `runtime/loot/pending/`. Recognition has eight pending jobs and bounded memory. Overload records failure with evidence. Persistence failures preserve events/evidence and retry up to three times per collector; `runtime/loot/persistence_error.json` reports unresolved errors. Farming continues when collection fails.

Universal sessions retain `universal` as their automation mode. Each run uses its own confidently recognized mission identity, or `unknown`; a later run cannot relabel earlier rewards. Existing visual reward detection and quantity rules apply without a configured drop table. Unfamiliar rewards can remain ambiguous, with their candidates and evidence recorded rather than added to confirmed totals.

A fresh verified results-title guard can supply the mission name when the reward recognizer has no title reference. This title is stored with that result event; it is not copied from an earlier Start screen. Conflicting title and reward-screen identities stay unknown with a warning.

Trial missions can insert a coin-summary page between ordinary results and acquired rewards. Universal recognizes the shared Rare Map Completion Reward / Total Acquired Coins / Final Results layout and its orange Next button. It advances that page once, using the same persisted input latch and two fresh agreeing observations, then resumes reward reveal and Retry. Coin amounts on that summary do not imply acquired item quantities.

Unknown/candidate/ambiguous identities, incomplete parsing and failures retain evidence in `runtime/loot/diagnostics/`. Temporary copies are removed only after structured records commit. Fully identified grids do not retain screenshots solely because quantities are unknown. Historical records are not backfilled from old screenshots. Workers predating instrumentation cannot supply loot evidence.

Each collector caches a library/reference/calibration snapshot and checks database/WAL and calibration file revisions before each new recognition job. Changed inputs load a complete replacement in the background; failed or changing revisions record a recognition failure and retry on the next completion. Reviewed library/calibration changes therefore need no farmer restart once this collector version is running. Deploying changed Python code to an older running worker still requires one restart. Reopening the GUI only rereads persisted history; old completions are never reprocessed. Automatic calibration requires the exact database revision. See [recognition details](RECOGNITION.md).

## Safety and controller limits

Start launches an independent hidden supervisor; duplicate starts are guarded. Stop is account-wide, persists the explicit stop, blocks further inputs and restarts, and allows a bounded operation to complete. A later explicit Start retains pending latches and stamina deadlines. The controller verifies BlueStacks mapping and Android identity before actions and after reconnection.

Legacy stage verification remains available; Universal verifies common workflow screens independently of the selected mission. Two fresh agreeing observations gate inputs. Unknown screens, unverified popup variants and unresolved transitions receive no guessed input. Detected loss persists STOP, marks the run failed, saves evidence and requests a local Windows alert; it does not revive, decline or exit the loss dialog. The verified insufficient-stamina prompt is declined once and starts a persisted natural-regeneration wait using the saved duration (initially ten minutes). Unverified dialogs remain observation-only.

Switching to or from Universal refuses an unresolved input in the source or destination mode, retaining its latch and the intentional Stop. Unrelated older legacy progress remains untouched. Natural stamina deadlines carry into Universal and back into legacy mode. If the game closes or reaches an unsupported menu, restore the intended Start Mission screen manually; Universal does not launch a menu navigation sequence.


## Stamina controls and Full Restart

The stamina section shows WAITING, READY, or UNKNOWN and the remaining time from the worker's persisted deadline. READY means the delay has elapsed, not a numeric stamina measurement; fresh verified game screens still gate every farming action. Missing/stale worker observations, unsupported screens, stopped processes, and monitoring failures show UNKNOWN. The GUI countdown is a display of the controller deadline, not a separate farming timer.

Choose 5, 10, 15, 20, or 25 minutes. The selection is saved in `runtime/manager_settings.json` and survives manager reopening. The original ten-minute duration is the initial default; an unsupported saved value falls back to five minutes. Changes affect subsequent waits and do not shorten or extend a wait already underway, including its pending dialog transition.

**End Stamina Wait** is available during a verified live wait with no unresolved input. It sends a worker/deadline-specific request through `runtime/stamina_request.json`. The worker interrupts its short polling sleep, clears that matching wait, and takes fresh game observations through the existing input safeguards. It skips only the delay. A verified insufficient-stamina prompt is declined once and creates another saved wait; it does not repeatedly attempt mission entry. Requests for an expired/replaced wait or another worker are rejected. Unknown screens remain observation-only. Workers running code from before this update cannot consume these requests; use an explicit Full Restart when ready to load the new controls. Opening the updated manager does not interrupt them.

**Full Restart**, beside Start/Stop, runs in a GUI background thread and is also available for ERROR recovery. It serializes the entire transaction, persists a safe Stop, and waits up to 35 seconds for all recorded controller processes and worker/supervisor locks to exit. If exit cannot be confirmed, it reports failure and launches nothing. A concurrent explicit Stop cancels the relaunch. Start and stage changes cannot launch or switch automation during the restart transaction.

After confirmed exit it clears only stale worker display/heartbeat fields and a pending timer-control request, resets mission verification, and checks the ADB connection and acc1 Android identity using the existing safeguards. It then launches the existing supervisor, whose worker repeats those checks before farming. Active runs restart their actual stage; stopped runs use the manager's selected stage. Pending input latches, mission/run counters, natural stamina deadlines, persistent account data, stage configuration, loot history, and activity logs remain intact. An unresolved input can therefore still require manual resolution after restart. BlueStacks and the Android device are not restarted, and no game data is modified.

Each restart step and failure is recorded in the activity log. Controls are locked while a command runs and until a fresh status poll verifies the result. A failed connection/identity check or changed Stop request prevents relaunch. CLI equivalents are `restart [--stage STAGE]`, `end-stamina-wait`, and `stamina-duration --minutes 5|10|15|20|25`.
