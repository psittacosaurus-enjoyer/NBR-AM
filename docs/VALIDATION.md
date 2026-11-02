> **Historical validation log.** Kept for provenance; it does not describe the current design. See [ARCHITECTURE.md](ARCHITECTURE.md).

# Validation scope

This document retains earlier manager, controller, stage, library and recognition evidence. Each dated section describes its own scope; historical live observations were not repeated during repository cleanup. Historical captures and reports are not guaranteed to remain on disk; calibration, regression and persistent loot evidence are retained where referenced.

## Autonomous recovery (October 30, 2026)

Removed the manual Skip control and backend. Universal and Ninja Road now log bounded recognition, foreground, transition and connection recovery, escalate through identity-verified game activity relaunch, and resume from fresh verified states. Uncertain input is retained; retries require the original action geometry and expected transition. Observation-only calls cannot launch the game. Native dispatch rechecks the screen immediately before input.

Recording failures remain retryable. Departed results first recover any retained same-job screenshot; only verified unavailable evidence receives durable missing-image acknowledgement with zero credits. The original completion, account/device identity and audit remain intact. Stamina cancellation is pinned to the recognized No button, premium action identifiers are rejected, and an unverified loss/revival exit remains a Pearl-safety stop.

The combined offline run passed 361 tests covering recovery, retained evidence, Pearl safety, Universal farming, manager appearances/controls/shutdown, and Ninja Road detection/strategy/modes. Compilation and diff checks passed. Tests used temporary runtime data, saved images and fake devices; no live emulator, ADB connection or farming session was controlled. Running workers retain their loaded code until their next normal start.

## Ninja Road: two observed cycles (October 25, 2026)

Live validation on acc1/Pie64 completed two consecutive configured cycles with Boss Swap and Early Exit enabled: map 10 cleared, followed by verified retreat at map 11. Runs `33598adf44a146539137331e015b67a0` and `674f2a6ec27141a8a6a909c6df25de7d` took 8m 41s and 8m 54s. Early exits increased 40 to 42; starts increased 72 to 74; failed attempts stayed at 28.

Each cycle visually verified three entry swaps on map 5, three restorations on map 6, and three entry swaps on map 10. All 18 swaps received distinct visual verification, with no ambiguous or rejected swipes. Auto OFF/ON controls and resumes worked; the second cycle also enabled ordinary Auto from its disabled startup state. Ninjutsu was verified for both teams. Team 1 fell on map 9 in each run, and automatic Team 2 selection continued both attempts successfully. No manual action, persistent stall, connection recovery or worker error occurred within either run or between them. The first results loop reset into the second attempt with Team 1.

Before these attempts, an old Universal rewards reveal latch blocked switching stages. Its original durable completion had no recoverable rewards image. Explicit missing-evidence recovery now supports that exact results latch at two fresh, identity-verified Ninja Road Home observations, with original job/evidence checks and durable zero-credit acknowledgement before the audited latch is cleared. The game initially reported no Ninja Road team; visiting the already populated Team Setup list and returning restored entry without changing units. These preparations preceded the two measured attempts. The recovery and adjacent offline regressions passed 73 tests; the focused Ninja Road suite passed 92.

The controller was safely stopped after the second result, with no active controller PIDs. Its final Results Next navigation latch remains available for normal resumption. Worker frames, state snapshots, event records, checks and final process status are retained in [the live report](../runtime/validation/ninja_two_live_1792906897/report.md). Early Exit's synthesized screen leaves the progress `auto_enabled` field stale; the actual Auto OFF screenshots and confirmed transition establish its behavior.

## Recording capacity and offline recovery (October 24, 2026)

Follow-up recheck found six subsequent completions captured and processed, no new loot screenshot-persistence errors, and 869,477,205 bytes of retained screenshots. All 85 pruning-journal entries still agreed with the filesystem, cleared database references and history warnings. A seventh completion was interrupted before rewards appeared; after a later agreed Start Mission observation, its capture was correctly revoked with zero capture attempts. This is a missing-evidence block, rather than renewed storage failure.

The former Dashboard **Skip error** control and its button-specific backend were removed. Live farming now retries capture failures with backoff and, only after two verified post-results observations prove the image unavailable, durably closes the exact transaction with zero credits and resumes automatically. The Manager no longer offers a manual discard control.

Retained diagnostics consumed 1,072,920,154 bytes of the 1 GiB admission budget, leaving only 821,670 bytes for a roughly 2.5 MB capture. The original completed transaction accumulated 566 capture attempts without a saved PNG; a later Windows health-file replacement error masked the capacity failure. Saved events and images established that the available later reward screenshot belonged to another mission.

Added journaled pruning of eligible finalized diagnostics toward 80% usage, with pending work, newest-three/account and referenced evidence protected. Admission is serialized through PNG commit. Shared atomic JSON utilities use unique fsynced temporary snapshots, short coordinated Windows readers and bounded sharing/access-conflict retries. Capture errors remain keyed to their completion separately from status-publication failures. Completed Universal runs lose capture eligibility after confirmed new mission entry/battle; shutdown and restart cannot attach later results to the revoked run.

Passed the combined 159-test recording, retention, Windows atomic I/O, recovery, dashboard and GUI suite. A subsequent 63-test run covering dashboard, GUI, loot tracking and source entry points also passed after final resolved-error presentation checks. Coverage includes a simulated full diagnostic budget with all eight completions/drops preserved, real Windows handle contention, concurrent publication, unreadable pending metadata, crash recovery and zero-credit offline closure. These are offline tests using temporary state and saved images, not new live farming runs.

With all farming processes stopped, the exact pending completion `job_a0d71403b29ee74bab6fe3e8e100b43d889005397d97497d` was closed through the guarded offline CLI as `missing_screenshot`, with zero credits and a durable receipt. Mission verification reset; Stop, all 1,127 completion identities, every original recognition result and mission counters were preserved. The later screenshot's SHA-256 remained unchanged. Automatic retention pruned 85 eligible diagnostics and reclaimed 216,098,642 bytes, leaving 856,821,512 bytes in the screenshot budget. Every deletion has a committed journal entry and obsolete database links were cleared.

Read-only integrity audits passed for both retained validation windows (three runs and two runs), with no recording backlog, duplicate identities or database integrity errors. Original state, database backup, audits and the final report are retained in `runtime/recovery/recording_failure_20261024_1792854838/`. Farming remains stopped; no live viewer or device input was used.

## Manager connection and results recovery (October 21, 2026)

Reproduced the cold ADB connection failure on this Windows installation: an unused loopback port takes about two seconds to return `ConnectionRefusedError`, exceeding the old 1.5-second connection budget. The client now allows three seconds for connection/refusal and retains a separate 1.5-second protocol response timeout. Cold startup on private ADB port 5041 successfully started the local server, connected the selected emulator and verified its Android identity. The private daemon was then stopped; the main server remained available.

An old completed Universal transaction also retained a `next` input from `RESULTS_NEXT` after the game had returned to Start Mission. The explicit missing-evidence recovery command now accepts only that narrow results latch with matching destinations and a valid timestamp after completion. Two fresh identity-verified Start Mission observations and durable missing-evidence acknowledgement precede clearing it. The original latch, transaction identity, reason and observations remain in `results_input_resolution` across subsequent missions. The old completion is retained as `missing_screenshot`, without reward credits.

Passed 169 focused offline tests: 107 manager/recording regressions, 39 BlueStacks/ADB checks and 23 missing-evidence recovery/durability checks. A separate review found no durability regression in the recovery path.

Live verification on acc1/Pie64 completed two fresh runs of **The Evil Snake Shows its Fangs [B]**, increasing completed missions from 917 to 919 with no failures. Auto was initially off and was enabled after two verified observations. Run 1 recorded two acquired icons; run 2 recorded four. Full 900 × 1600 reward PNGs, job metadata, visual review and a read-only integrity report are retained in `runtime/validation/manager_recovery_two_runs_20261021_205635/`. Checksums, database entries and durable receipts agreed; SQLite checks passed, with no duplicates or recording backlog. Visual review confirmed all six acquired icons were captured, excluding Completion Reward Conditions.

Five identities matched: three Health Boost and two Attack Boost icons. One Orochimaru “The Snake Ninja” icon remains a candidate and is not credited as a confirmed character. No printed quantities were visible; quantities remain unknown while drop counts use one per acquired icon. At final verification the manager responded, the selected Android identity verified, tracking was active and the next mission was in battle. The bounded validation copies are complete; normal loot recording and farming continue.

## Follow-up repository cleanup (October 21, 2026)

The additional cleanup removes unreferenced fixtures/setup captures, a duplicate Tsunade image and the completed bounded monitor. Including the old verifier path promoted to reusable code, 12 old files totaling 15,403,017 bytes (14.69 MiB) gross were removed. The earlier October 20 counts and test results below describe that separate cleanup pass.

The redundant `runtime/NBR-AM/` directory contains only Git metadata; all three commits and all 460 stored objects were verified in the outer repository's reachable history. Automatic approval review rejected deletion as "blocked by policy", so all 34 files remain intact (328,757,876 bytes / 313.53 MiB). The follow-up deletion totals exclude that directory.

The evidence verifier now lives in `scripts/verify_loot_evidence.py` and accepts `--evidence-dir <folder> --expected 3 --require-count`. It prints a read-only audit by default; report creation requires an explicit `--report-dir`. The latest three-run reward PNG/JSON pairs and their review/reports remain, as do all loot databases/receipts/diagnostics, calibration data, library references and recovery folders referenced by persisted state. Root launchers still expose the same single Manager implementation, and `src/farm.py` remains the process identity entry point.

The final complete offline suite passed **730 tests in 205.431 seconds**, including 19 isolated verifier tests covering valid evidence, corrupted PNG pixels with valid checksums, receipt/database mismatches, conservative identity and quantity states, legacy run-ID reuse, Unicode console output, read-only defaults and explicit report writing. All **5,633 protected files** matched their original fingerprints. The promoted verifier passed all three retained jobs without modifying their historical results. Main-repository Git integrity, all five PowerShell helper/entrypoint parsers, foreign-working-directory launch paths, asset references and whitespace checks passed. The local [cleanup audit](../runtime/cleanup/disposable_tests_20261021/deletion_results.json) records the completed and blocked paths. No live macro run, controller restart or game input forms part of this cleanup.

## Repository cleanup (October 20, 2026)

The pre-cleanup offline baseline passed all 685 tests in 224.989 seconds. After cleanup, the complete suite passed **698 tests in 208.335 seconds** using `.venv/Scripts/python.exe -B scripts/test_offline.py --suite all -q`. Tests used temporary state, fake devices/backends and offscreen Qt. No live farming Start/Stop, game input or active-process restart was performed.

The active Library preserved recognition results across **28 saved screenshots and 158 reward icons**, including identity status, scores, margins, quantities and calibration gates. All 31 supported item identities and two character identities remained supported. Database integrity/foreign keys, 1,974 original hashes and thumbnails, and 1,432 cached manifest resources passed validation. Canonical pattern paths, imports, root/source entry points and Windows launcher syntax passed; no references to deleted paths remain. The final 478 protected state/evidence files retained their hashes. See the [cleanup report](CLEANUP-1.25.md) for the deletion and retention scope.

## Version 1.25 cleanup (October 17, 2026)

This earlier reorganization introduced named application/automation packages, compatibility modules and root launchers. Its validation below describes the October 17 state; subsequent cleanup removes redundant aliases and assets. The current entry points and retention rules are documented in the [cleanup report](CLEANUP-1.25.md).

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

### ADB cold startup and three live runs (October 21, 2026)

The installed BlueStacks ADB 1.0.36 client failed to connect when its local server was absent because the explicit `-H 127.0.0.1` argument suppresses automatic server startup. Explicit connection now starts the local server only after a refused loopback socket, then connects the pinned endpoint and verifies its configuration mapping, Android identity and, for manual targets, BlueStacks vendor evidence. `Device.connect()` uses that shared path. Discovery and passive verification remain read-only; server protocol errors and timeouts do not trigger bootstrap.

All **125 focused offline tests passed**: 81 discovery, identity, target ownership and connection recovery tests, plus 44 safety, Start/restart recovery, live control and Manager instance tests. New regressions cover absent-server bootstrap, local server override isolation, bootstrap failures, unchanged passive behavior and retained identity/mapping guards. The server had already been restored before live validation; the corrected cold bootstrap was exercised with offline mocks, not reproduced against an absent live daemon after the fix.

Three consecutive acc1 Universal runs on visually identified **Cold Eyes [A]** completed with saved reward evidence. Counters advanced from **901 starts / 891 completions to 904 / 894**, with **zero failures**. Safe Stop left the game on results after the third completion, with no fourth mission started.

Visual review covered all **10 acquired, revealed icons**: four Attack Boost "Sphere of Strength", four Health Boost "Health and Endurance", and two Sai "Mask-like Smile". Eight icons were automatically matched. Both Sai icons were manually verified against reference artwork but remain automatic candidates; their two completions retain `uncertain` status. All ten quantities remain unknown/null because no explicit quantity was displayed; visible icon counts are not numerical item quantities.

The read-only integrity audit passed for all three saved jobs: PNG hashes, chunk integrity and dimensions; durable receipt hashes, recorded flags and account/device/run identities; exact SQLite recognition results and completion statuses; global completion/run uniqueness; acquired/revealed entries and unique bounding boxes inside their screenshots. SQLite `quick_check` and foreign-key checks passed, with zero database or disk-spool backlog. The [integrity report](../runtime/validation/adb_three_runs_20261021_88f93315/integrity_report.json), [live report](../runtime/validation/adb_three_runs_20261021_88f93315/live_report.json) and [visual review](../runtime/validation/adb_three_runs_20261021_88f93315/REVIEW.md) are ignored local evidence, not committed repository artifacts or fresh-checkout guarantees.

### Interrupted screenshot capture (October 20, 2026)

The acc1 worker stopped after sending `reveal_rewards` and before observing `READY_TO_RETRY`. Its next session observed Start Mission, leaving one registered completion awaiting a screenshot that was no longer available. Capture attempts were zero; no PNG or recoverable reward screenshot existed for that job. This was an interrupted capture, not a disk write failure.

The controller was stopped, and the original completion was explicitly resolved through `resolve-missing-evidence` after its device and two fresh Start Mission observations were verified. Its receipt and SQLite history retain `missing_screenshot` with an empty recognition result. Counters remained 894 starts, 885 completions and zero failures, and the pending backlog cleared. The temporary recovery snapshot is not required after that terminal receipt and history were verified.

Worker shutdown now provides a bounded read-only capture window for a matching registered Universal completion. Two fresh fully revealed results observations and fresh device/foreground checks are required; no game inputs are sent. Failed or unavailable captures preserve the original transaction and input latch. Returning to Start Mission with unsaved evidence now gives explicit recovery instructions, and explicit resolution removes the stale progress health snapshot. Forced process termination or leaving results before capture can still make reward evidence unavailable; those completions remain visible without fabricated reward credits.

The final controller/Manager discovery run executed 528 tests: 527 passed and one canonical-import subprocess exceeded its 30-second timeout. All seven structure/import tests passed immediately when rerun alone in 3.10 seconds; the formerly timed-out test completed in 0.69 seconds. The shutdown regressions cover reveal/unknown/revealed transitions, placeholder waits, shared deadlines, identity and foreground mismatches, unavailable collectors, capture and acknowledgement failures, stale health rollback, preserved Stop/input/counters, and callback order before collector close. A separate earlier recording/recovery run passed 67 tests. `git diff --check` reported no whitespace errors.

The existing acc1 supervisor was resumed with the updated worker. It passed Start Mission into battle, advancing starts to 895 with completions still 885 and failures zero. Collector health and disk backlog both showed zero capture-pending jobs. The game reported Auto off, so a fresh live reward screenshot and the new shutdown capture were not verified live; the worker was paused in battle at that observation.

### Missing reward evidence recovery (October 17, 2026)

The live acc1 controller was running at Start Mission but blocked by a completed transaction still awaiting its reward screenshot. Earlier logs recorded OpenCV memory/decode errors and an ADB interruption; the old results were no longer visible. Explicit `resolve-missing-evidence --reason` now preserves that completion as `missing_screenshot` with zero reward credits, after stopped-controller, target identity, pending-input, fresh Start Mission and durable acknowledgement checks. Archived latches from unrelated stages remain untouched.

Offline validation passed 79 collector/recording/control tests, 15 focused controller recovery tests and 13 offscreen loot GUI tests. The focused durability tests cover write failure, orphan screenshots, identity conflicts, replay, SQLite outage and separation of missing-evidence acknowledgements from screenshot acknowledgements. Independent review found no actionable issues in the new recovery paths.

One fresh production-supervisor run of **Getting Rich Quick [B]** then passed Start Mission, battle, completion, results, reward reveal and saved screenshot accounting. Auto was observed off and explicitly enabled once; a later live capture and controller state verified it on. Counters advanced from 832 starts / 824 completions to 833 / 825, with no failures. The new completion was `recognized` with nine acquired icons, while the interrupted completion remained `missing_screenshot` with an empty result. This is a historical observation; temporary validation copies may be removed independently of persistent loot history. The independent supervisor and worker remained running in their normal natural-stamina wait. This is one verified live cycle, not an unattended soak test.

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

The controller, loot collector, recognizer and manager were updated with the seven regression files listed above. No mission catalogs or combat/navigation rules changed.

Compatibility: the existing SQLite tables/history remain intact; new job metadata and recording states are additive, with legacy spool replay supported. Existing running processes keep their loaded code until a later user-controlled start. Storage limits preserve pending evidence and pause new recording rather than deleting it; see [pipeline, retention and troubleshooting](MANAGER.md#recording-pipeline-and-recovery).

Limits: these tests establish offline behavior, not fresh live-device reliability. No BlueStacks inputs, live Start/Stop/Restart commands, or reset of the existing acc1 session were performed. No physical disk/power-loss or native window interaction testing was performed. A later explicitly authorized controlled run should verify capture-failure visibility, restart recovery and normal mission/stamina/Stop behavior on the intended device. Audit report files were not available; findings beyond the supplied F01–F05/F11 scope were not evaluated or claimed resolved. Broader recognition coverage and the historical live-testing limits below remain unchanged.

### Version 1.23 (October 15, 2026)

The full offline controller/manager suite passed **288 tests in 127.227 seconds**. Two additional CLI integration regressions were then added; the complete **35-test targeting module passed in 0.603 seconds**, including selection → status → detached launch with the same custom endpoint and manual selection verification. This covers 290 distinct tests across those runs. Syntax checks passed for all seven changed application/script files, and the patch whitespace check passed.

Instance discovery and controller targeting were validated with synthetic configurations, temporary runtime directories, mocked subprocesses/devices, and offscreen Qt windows. Coverage includes single/multiple/custom names, saved and missing selections, invalid or ambiguous manual setup, endpoint resolution, mismatched identities, active and uncertain process locks, immutable supervisor/worker targets, legacy migration and unresolved ownership quarantine, and separate per-instance input latches, stamina state and loot history. Existing Start/Stop/Restart, End Wait, timer, loot and appearance regressions are included in the controller/manager suite.

A passive check of the installed BlueStacks configuration found three instances: `acc1`/`Pie64` running with its Android identity verified, and `acc2`/`Pie64_1` plus `acc3`/`Pie64_2` stopped. The integrated backend retained the running target and reported selection locked. These observations used existing ADB transports and read-only Windows process/configuration access. No live farming Start, Stop, Restart or input tests were performed. The existing supervisor and worker were left running. Both Instance page appearances were rendered with a fake backend at normal and compact window sizes and visually inspected.

After testing, the original supervisor PID 57908 and worker PID 3960 retained their original process creation identities and start times. Neither `runtime/instance_selection.json` nor `runtime/legacy_instance.json` was created in the live runtime. The existing controller was not restarted to load new code.

This update changed discovery/passive verification, selection persistence, frozen targets, state isolation, the manager Instance page, instance-scoped history reads and notification text. Added tests are `test_bluestacks.py`, `test_instance_controller.py`, `test_manager_instance_backend.py`, `test_manager_instances.py` and `instance_fixtures.py`; existing manager, appearance, safety, stage, Universal, live-control and restart test fixtures were adapted to explicit offline targets. README and Manager documentation describe setup and migration.

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
