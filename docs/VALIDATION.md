# Validation scope

This document retains earlier manager, controller, stage, library and recognition evidence. Each dated section describes its own scope; historical live observations were not repeated during the 1.25 repository cleanup.

## Version 1.25 cleanup (October 17, 2026)

The application and automation source have been reorganized into named packages, with compatibility modules and root launchers preserving existing entry points. Runtime state, logs, pending evidence, the local library, account mappings and active farming processes remain in place. Recognition catalogs and reusable regression tests are retained. See the [cleanup report](CLEANUP-1.25.md) for the complete move/retention inventory and rollback reference.

Static resource inspection confirmed all **266** pattern template/alternative references resolve, covering **137** unique files. Syntax checks passed for 44 source/entry-point files, and dependency/resource roots resolve. Canonical and legacy module aliases, PowerShell syntax, CMD paths and root CLI help from another working directory were checked. Runtime/environment paths are ignored and no disposable outputs are tracked.

| Offline verification | Tests | Result | Seconds |
| --- | ---: | --- | ---: |
| Controller/GUI discovery suite | 395 | Passed | 160.633 |
| Game library discovery suite | 63 | Passed | 6.569 |
| Focused structure/entry-point suite | 7 | Passed | 4.543 |
| Recognition discovery suite | 94 | Passed | 22.047 |

Six of the seven structure tests are also included in the 395-test discovery run; the seventh was added after correcting the legacy GUI shim's exit status. Together these runs cover 553 distinct tests, with no failures or skips. Offscreen GUI tests use fake controller operations. Runtime settings, state and lock file hashes matched before and after the final suites. No live farming, GUI Start/Stop/Restart operation, ADB input or BlueStacks modification is part of this validation.

## Historical evidence

- All Scrolls [A]: normal completion/results/reward/Retry cycles, detached supervisor, duplicate prevention, clean stop/resume, worker restart and a verified rank-up acknowledgement were observed. Fresh mission-banner interaction remained blocked. Ten-mission and sixty-minute soak targets were not established.
- Getting Rich Quick! [B]: consecutive result/replay cycles, stage ownership, duplicate prevention and live Stop/restart were observed. Verified title, rank-up acknowledgement, completion-animation variants and insufficient-stamina No guards were added from inspected screens. The ten-minute timer boundary was checked with a simulated clock. Fresh list/rank entry remained incomplete.
- Practice Training [Part 1]: two consecutive live replay cycles and safe Stop were recorded after replacing routine title OCR with title templates. Modal variants and persisted retry recovery have offline coverage.
- Manager: original Qt backend/widget tests covered state colors, control locking, elapsed time, asynchronous status, logs and closing without Stop. Native window layout and launcher were inspected. Mocked tests do not prove live device behavior.
- Library: the original collection reported 1,648 records and 1,266 unique image contents. Hash/provenance/foreign-key/export checks passed. Thirteen forthcoming banners were unavailable and Ninja Gems had no source image. These are a historical snapshot, not fresh-checkout guarantees.
- Recognition: nine held-out saved runs yielded 51/51 verified icon identities. An earlier 48-icon set yielded 42 verified identities, one candidate and five unknowns; six negative screens had no accepted rewards. Four independently reviewed fresh Practice Training cycles yielded 16/16 correct icons: Sakura card 379, Bravery Health Boost and Wisdom Attack Boost. Every acquired quantity remained Unknown. Other stages were not freshly farmed during that recognition validation.

The inspected reference snapshot had 185 eligible items (229 images) and 283 characters (306 card/portrait images), 535 decoded references with no errors. Loading references does not validate all identities. Calibrated aliases require reviewed evidence or explicit compatible game identifiers; five-star Sakura card 380 remains separate from four-star card 379.

## Current verification

### Interrupted screenshot capture (October 20, 2026)

The acc1 worker stopped after sending `reveal_rewards` and before observing `READY_TO_RETRY`. Its next session observed Start Mission, leaving one registered completion awaiting a screenshot that was no longer available. Capture attempts were zero; no PNG or recoverable reward screenshot existed for that job. This was an interrupted capture, not a disk write failure.

The controller was stopped, and the original completion was explicitly resolved through `resolve-missing-evidence` after its device and two fresh Start Mission observations were verified. Its receipt and SQLite history retain `missing_screenshot` with an empty recognition result. Counters remained 894 starts, 885 completions and zero failures, and the pending backlog cleared. Original progress, pending metadata and process/health records were backed up under `runtime/recovery/screenshot-persistence-20261020-163756/`.

Worker shutdown now provides a bounded read-only capture window for a matching registered Universal completion. Two fresh fully revealed results observations and fresh device/foreground checks are required; no game inputs are sent. Failed or unavailable captures preserve the original transaction and input latch. Returning to Start Mission with unsaved evidence now gives explicit recovery instructions, and explicit resolution removes the stale progress health snapshot. Forced process termination or leaving results before capture can still make reward evidence unavailable; those completions remain visible without fabricated reward credits.

The final controller/Manager discovery run executed 528 tests: 527 passed and one canonical-import subprocess exceeded its 30-second timeout. All seven structure/import tests passed immediately when rerun alone in 3.10 seconds; the formerly timed-out test completed in 0.69 seconds. The shutdown regressions cover reveal/unknown/revealed transitions, placeholder waits, shared deadlines, identity and foreground mismatches, unavailable collectors, capture and acknowledgement failures, stale health rollback, preserved Stop/input/counters, and callback order before collector close. A separate earlier recording/recovery run passed 67 tests. `git diff --check` reported no whitespace errors.

The existing acc1 supervisor was resumed with the updated worker. It passed Start Mission into battle, advancing starts to 895 with completions still 885 and failures zero. Collector health and disk backlog both showed zero capture-pending jobs. The game reported Auto off, so a fresh live reward screenshot and the new shutdown capture were not verified live; the worker remains paused in battle pending Auto being enabled.

### Missing reward evidence recovery (October 17, 2026)

The live acc1 controller was running at Start Mission but blocked by a completed transaction still awaiting its reward screenshot. Earlier logs recorded OpenCV memory/decode errors and an ADB interruption; the old results were no longer visible. Explicit `resolve-missing-evidence --reason` now preserves that completion as `missing_screenshot` with zero reward credits, after stopped-controller, target identity, pending-input, fresh Start Mission and durable acknowledgement checks. Archived latches from unrelated stages remain untouched.

Offline validation passed 79 collector/recording/control tests, 15 focused controller recovery tests and 13 offscreen loot GUI tests. The focused durability tests cover write failure, orphan screenshots, identity conflicts, replay, SQLite outage and separation of missing-evidence acknowledgements from screenshot acknowledgements. Independent review found no actionable issues in the new recovery paths.

One fresh production-supervisor run of **Getting Rich Quick [B]** then passed Start Mission, battle, completion, results, reward reveal and saved screenshot accounting. Auto was observed off and explicitly enabled once; a later live capture and controller state verified it on. Counters advanced from 832 starts / 824 completions to 833 / 825, with no failures. The new completion was `recognized` with nine acquired icons, while the interrupted completion remained `missing_screenshot` with an empty result. Local evidence is retained under `runtime/validation/live-recovery-20261017/`, including the saved rewards and `verified-completion.json`. The independent supervisor and worker remained running in their normal natural-stamina wait. This is one verified live cycle, not an unattended soak test.

### Version 1.24 (October 16, 2026)

**NBR-AM v1.24 — Loot Recording Reliability Update** preserves the Universal farming engine, stage catalogs, account/device isolation and independent controller. Release fixes address the supplied descriptions of audit findings **F01–F05 and F11**:

- **F01:** verified completion state and original timestamp are persisted before registration or results input. Registration returns a durable job acknowledgement and retries under the same identity; unsuccessful persistence pauses progression.
- **F02:** screenshot writes use a temporary PNG, flush/fsync, decode/checksum verification and atomic replacement. Automatic Retry waits for saved evidence and its persisted farmer acknowledgement. Bounded capture failures show operator attention while safe retries continue.
- **F03:** the disk outbox survives SQLite outages and processing limits. Memory capacity no longer discards excess work; transient failures retry with bounded exponential backoff without a terminal attempt count.
- **F04:** collector initialization retries within the existing worker, including during device recovery. Health and pending failures are visible without opening Recognition details.
- **F05:** account/device/stage/run identities and durable receipts recover outstanding transactions across process restarts without duplicate item credits. Matching legacy pending jobs can be adopted from persisted progress; unrelated results cannot register historical completions.
- **F11:** temporary screenshot/reference/supplement access and recognition infrastructure failures retain evidence for retry. Partially loaded references caused by temporary access errors are not installed. Retained operator-attention jobs can be retried under their original accounting ID. Matching thresholds and reference coverage are unchanged.

Final validation used isolated temporary databases/runtime directories, saved-screen fixtures, fake devices, mocked controller operations and offscreen Qt. Suites ran sequentially:

| Final command (using `.venv/Scripts/python.exe -B -m unittest`) | Tests | Result | Seconds |
| --- | ---: | --- | ---: |
| `discover -s tests -p 'test_*.py' -q` | 329 | Passed | 144.296 |
| `discover -s tests/game_library -p 'test_*.py' -q` | 63 | Passed | 6.846 |
| `discover -s tests/item_recognition -p 'test_*.py' -q` | 94 | Passed | 23.413 |

**486 tests passed; no failures or skips.** `PYTHONPATH` included `src` (and `tests` for focused module runs); `QT_QPA_PLATFORM=offscreen` was used for the controller/manager suite. The available optional local-library integration ran successfully using read-only library inputs. Source syntax and `git diff --check` also passed. Earlier focused runs and the initial 323-test full run passed; the final 329-test run includes the last legacy migration and recognition-classification regressions.

New regression files:

- `tests/test_recording_recovery.py` (18): registration/capture retries and backoff, repeated failure pause/recovery, persisted timestamps, transaction-write failures, Stop, collector startup recovery, identity mismatch rejection, session verification recovery, legacy job adoption and the CLI step recording gate.
- `tests/test_loot_durability.py` (12): atomic/partial/corrupt PNG recovery, durable IDs/receipts, SQLite outages beyond the old retry limit, disk backlog beyond memory capacity, restart before capture, storage refusal without eviction, retained-job reprocessing, and real recognizer error classification.
- `tests/test_recording_dashboard.py` (5): passive backlog visibility without SQLite, deduplicated counts, account/device isolation and malformed metadata handling.
- `tests/test_recording_integration.py` (1): real Engine/Collector restart with fake device/recognizer, original completion identity/session/time, Retry while recognition is blocked, and exactly one reward credit.

Existing tests changed in `tests/test_loot_tracking.py`, `tests/test_loot_gui.py` and `tests/test_universal.py`: terminal loss/overflow expectations now assert recoverability, farmer hooks require durable acknowledgements, and compact health visibility remains compatible with both appearances.

Application file manifest: `src/farm.py`, `src/loot_tracking.py`, `src/item_recognition/recognizer.py`, `src/manager_backend.py`, `src/manager_gui.py`, `src/manager_loot.py`, and `src/manager_theme.py`. Documentation updated after implementation and final testing: `README.md`, `docs/MANAGER.md`, and this release/validation history. The seven test files listed above complete the changed/added file manifest. No mission catalogs or combat/navigation rules changed.

Compatibility: the existing SQLite tables/history remain intact; new job metadata and recording states are additive, with legacy spool replay supported. Existing running processes keep their loaded code until a later user-controlled start. Storage limits preserve pending evidence and pause new recording rather than deleting it; see [pipeline, retention and troubleshooting](MANAGER.md#recording-pipeline-and-recovery).

Limits: these tests establish offline behavior, not fresh live-device reliability. No BlueStacks inputs, live Start/Stop/Restart commands, or reset of the existing acc1 session were performed. No physical disk/power-loss or native window interaction testing was performed. A later explicitly authorized controlled run should verify capture-failure visibility, restart recovery and normal mission/stamina/Stop behavior on the intended device. Audit report files were not available; findings beyond the supplied F01–F05/F11 scope were not evaluated or claimed resolved. Broader recognition coverage and the historical live-testing limits below remain unchanged.

### Version 1.23 (October 15, 2026)

The full offline controller/manager suite passed **288 tests in 127.227 seconds**. Two additional CLI integration regressions were then added; the complete **35-test targeting module passed in 0.603 seconds**, including selection → status → detached launch with the same custom endpoint and manual selection verification. This covers 290 distinct tests across those runs. Syntax checks passed for all seven changed application/script files, and the patch whitespace check passed.

Instance discovery and controller targeting were validated with synthetic configurations, temporary runtime directories, mocked subprocesses/devices, and offscreen Qt windows. Coverage includes single/multiple/custom names, saved and missing selections, invalid or ambiguous manual setup, endpoint resolution, mismatched identities, active and uncertain process locks, immutable supervisor/worker targets, legacy migration and unresolved ownership quarantine, and separate per-instance input latches, stamina state and loot history. Existing Start/Stop/Restart, End Wait, timer, loot and appearance regressions are included in the controller/manager suite.

A passive check of the installed BlueStacks configuration found three instances: `acc1`/`Pie64` running with its Android identity verified, and `acc2`/`Pie64_1` plus `acc3`/`Pie64_2` stopped. The integrated backend retained the running target and reported selection locked. These observations used existing ADB transports and read-only Windows process/configuration access. No live farming Start, Stop, Restart or input tests were performed. The existing supervisor and worker were left running. Both Instance page appearances were rendered with a fake backend at normal and compact window sizes and visually inspected.

After testing, the original supervisor PID 57908 and worker PID 3960 retained their original process creation identities and start times. Neither `runtime/instance_selection.json` nor `runtime/legacy_instance.json` was created in the live runtime. The existing controller was not restarted to load new code.

Changed application files: `src/bluestacks.py` (discovery, passive probes and verification), `src/farm.py` (selection persistence, frozen targets and state isolation), `src/manager_backend.py` (instance adapter), `src/manager_gui.py` and `src/manager_theme.py` (v1.23 sidebar and Instance page), `src/loot_tracking.py` (instance-scoped history reads), and `scripts/loss_notify.py` (instance-neutral alert text). Added tests are `test_bluestacks.py`, `test_instance_controller.py`, `test_manager_instance_backend.py`, `test_manager_instances.py` and `instance_fixtures.py`; existing manager, appearance, safety, stage, Universal, live-control and restart test fixtures were adapted to explicit offline targets. README and Manager documentation describe setup and migration.

Public distribution limits: discovery targets local Windows BlueStacks registrations and readable `bluestacks.conf` files. A nonstandard data location can be supplied through `NBR_BLUESTACKS_CONFIG`; Manual Setup is the fallback. Automatic selection requires a configured Android identity and a discovered BlueStacks ADB executable. Manual endpoints require an existing authorized local ADB transport, BlueStacks vendor evidence and a uniquely resolved executable. Open BlueStacks and enable its ADB setting before applying a target. If process metadata is inaccessible, status is unavailable rather than guessed. Arbitrary registry/install variants and a fresh machine were not tested live; the supported cases have isolated fixture coverage. No changes were made to game files, APKs, stage definitions or the farming loop.

Version 1.22 (October 15, 2026) passed 195 offline controller/manager tests. Coverage includes the sole Universal automation path, account ownership and Stop/restart safeguards, saved input/stamina state, persisted visibility settings, both appearances, and always-visible loot with exact library artwork and evidence-based quantity overlays. Both appearances were rendered with a fake controller and read-only local library artwork for visual inspection. No live gameplay was performed for this update.

Run the offline discovery command in [the README](../README.md). Tests cover controller safety, stages, mock reconnection, latches, stamina deadlines, GUI state/history, collector persistence and deduplication, recognition confidence/quantity semantics, and library APIs. Saved-screen fixtures exercise actual image matching. Integration tests need the ignored local library; HTTP tests use a temporary loopback server and crawler requests are mocked.

On October 13, 2026, the reorganized local project passed 250 tests: 161 controller/manager, 37 library and 52 recognition tests. A clean copy of the prospective repository also passed, skipping the two optional downloaded-library integration tests. Python syntax/imports, four stage catalogs/assets, relocated PowerShell launchers, dependency consistency and fresh dependency resolution, Git ignore rules and the publication file list were checked. No live gameplay was performed; dependency installation was dry-run resolution using the existing Python environment.

## Version 1.2 validation (October 13, 2026)

Universal mode reuses the verified common Start/results/reward/Retry and stamina controls, independently of mission-title guards. Offline checks cover unknown titles, saved controls and modal rejection, repeated completion/reward events, safe initial entry, persisted latches and stamina deadlines, Stop, saved-state preservation, GUI reopening, and conservative per-run reward attribution. All 273 tests passed: 184 controller/Manager, 52 recognition and 37 library tests. The final controller/Manager suite ran alone after live farming stopped and passed in 103.6 seconds. These use isolated runtime state and mocked input, including saved-screen and simulated-clock stamina checks.

Live acc1 testing on the previously unconfigured **All Cups [S]** verified Start Mission input, completion, results Next, reward reveal, Retry, return to Start Mission, and a second confirmed mission start. The bounded test stopped the worker and supervisor cleanly. Reopening the updated Manager offscreen during this run showed RUNNING in Universal mode; closing it left the same supervisor running without a stop request. This checks live process attachment, not native window appearance.

The All Cups completion was durably recorded with six ambiguous acquired icons and retained screenshot/candidates; quantities remained unknown. Its mission attribution remained unknown, without assigning an unverified mission. This validates reward capture and conservative storage, not universal item identity or quantity accuracy. No stage configuration or drop table was created. Actual unfamiliar-stage Start/results captures are saved as shared-layout regression fixtures.

All Scrolls [A] also passed the same complete loop and clean Stop in Universal mode. Its title was detected, and its reward record was attributed to All Scrolls [A], with ten recognized acquired icons and unknown quantities.

Getting Rich Quick! [B] passed a clean Start/results/Retry/Start loop and Stop after resource recovery. The resumed session recorded two completions with the correct mission identity, twelve recognized icons in total, additional unknown detections, unknown quantities, and retained diagnostic evidence. The test stopped the supervisor and worker, leaving the game alone.

An earlier overlapping regression run and loot integration run exhausted Windows virtual memory during the initial Rich check, causing image/process errors and a harmful exit reported by the user. The test processes exited; the controlled controller was stopped, and available virtual memory recovered to roughly 16 GB. Live validation then ran without concurrent heavy tests and no further memory errors were observed. Run resource-heavy test suites sequentially, with farming stopped. This incident does not establish extended unattended reliability.

Stamina No/wait/retry timing has saved-screen and simulated-clock coverage; natural stamina exhaustion has not been observed in this 1.2 live session. No premium resources were used.

## Continuing limits

### Trial results follow-up (October 13, 2026)

Wager with Tsunade [A] was stalled on its extra Rare Map Completion Reward / Total Acquired Coins / Final Results page after the ordinary results Next. The prior input remained latched and no repeated clicks were sent. Added a shared `TRIAL_COINS_NEXT` results layout with five static visual guards and a button-derived Next target; mission titles and variable coin amounts are excluded. No trial-stage configuration or combat changes were added.

All 70 relevant shared-layout, safety, completion-recognition and stamina tests passed while the controller was stopped. New saved-screen tests cover the trial layout, every required guard, dimmed dialogs, variable title/totals, recovery of the previous Next latch, one input per transition and no duplicate completion count. After restart, the controller advanced the stalled page, revealed rewards and retried. A fresh mission then completed and automatically passed ordinary results → trial coin summary → acquired rewards → Retry → Start; the macro was left running in Universal mode with the input latch resolved.

Fresh menu navigation, unobserved dialog variants, natural defeat OCR/alert behavior and extended uninterrupted farming retain live verification gaps. The recognized stamina flow and ten-minute deadline do not imply coverage of all stage/dialog forms. Night of Tragedy has saved-screen/controller coverage; the recognition live sample covered Practice only. Broader reward layouts, animations, arbitrary character forms and acquired quantity accuracy require separate evidence. A rebuilt library may disable automatic calibration because its database hash differs.
