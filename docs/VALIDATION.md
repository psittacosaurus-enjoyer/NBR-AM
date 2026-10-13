# Validation scope

This document consolidates previous manager, controller, second-stage, library and recognition reports. Historical observations below were recorded on October 11–12, 2026; they were not repeated during repository restructuring.

## Historical evidence

- All Scrolls [A]: normal completion/results/reward/Retry cycles, detached supervisor, duplicate prevention, clean stop/resume, worker restart and a verified rank-up acknowledgement were observed. Fresh mission-banner interaction remained blocked. Ten-mission and sixty-minute soak targets were not established.
- Getting Rich Quick! [B]: consecutive result/replay cycles, stage ownership, duplicate prevention and live Stop/restart were observed. Verified title, rank-up acknowledgement, completion-animation variants and insufficient-stamina No guards were added from inspected screens. The ten-minute timer boundary was checked with a simulated clock. Fresh list/rank entry remained incomplete.
- Practice Training [Part 1]: two consecutive live replay cycles and safe Stop were recorded after replacing routine title OCR with title templates. Modal variants and persisted retry recovery have offline coverage.
- Manager: original Qt backend/widget tests covered state colors, stage persistence/locking, elapsed time, asynchronous status, logs and closing without Stop. Native window layout and launcher were inspected. Mocked tests do not prove live device behavior.
- Library: the original collection reported 1,648 records and 1,266 unique image contents. Hash/provenance/foreign-key/export checks passed. Thirteen forthcoming banners were unavailable and Ninja Gems had no source image. These are a historical snapshot, not fresh-checkout guarantees.
- Recognition: nine held-out saved runs yielded 51/51 verified icon identities. An earlier 48-icon set yielded 42 verified identities, one candidate and five unknowns; six negative screens had no accepted rewards. Four independently reviewed fresh Practice Training cycles yielded 16/16 correct icons: Sakura card 379, Bravery Health Boost and Wisdom Attack Boost. Every acquired quantity remained Unknown. Other stages were not freshly farmed during that recognition validation.

The inspected reference snapshot had 185 eligible items (229 images) and 283 characters (306 card/portrait images), 535 decoded references with no errors. Loading references does not validate all identities. Calibrated aliases require reviewed evidence or explicit compatible game identifiers; five-star Sakura card 380 remains separate from four-star card 379.

## Current verification

Run the offline discovery command in [the README](../README.md). Tests cover controller safety, stages, mock reconnection, latches, stamina deadlines, GUI state/history, collector persistence and deduplication, recognition confidence/quantity semantics, and library APIs. Saved-screen fixtures exercise actual image matching. Integration tests need the ignored local library; HTTP tests use a temporary loopback server and crawler requests are mocked.

On October 13, 2026, the reorganized local project passed 250 tests: 161 controller/manager, 37 library and 52 recognition tests. A clean copy of the prospective repository also passed, skipping the two optional downloaded-library integration tests. Python syntax/imports, four stage catalogs/assets, relocated PowerShell launchers, dependency consistency and fresh dependency resolution, Git ignore rules and the publication file list were checked. No live gameplay was performed; dependency installation was dry-run resolution using the existing Python environment.

## Version 1.2 validation (October 13, 2026)

Universal mode reuses the verified common Start/results/reward/Retry and stamina controls, independently of mission-title guards. Legacy catalogs and workflows remain available. Offline checks cover unknown titles, saved controls and modal rejection, repeated completion/reward events, safe initial entry, persisted latches and stamina deadlines, Stop, cross-mode state preservation, GUI reopening, and conservative per-run reward attribution. All 273 tests passed: 184 controller/Manager, 52 recognition and 37 library tests. The final controller/Manager suite ran alone after live farming stopped and passed in 103.6 seconds. These use isolated runtime state and mocked input, including saved-screen and simulated-clock stamina checks.

Live acc1 testing on the previously unconfigured **All Cups [S]** verified Start Mission input, completion, results Next, reward reveal, Retry, return to Start Mission, and a second confirmed mission start. The bounded test stopped the worker and supervisor cleanly. Reopening the updated Manager offscreen during this run showed RUNNING in Universal mode; closing it left the same supervisor running without a stop request. This checks live process attachment, not native window appearance.

The All Cups completion was durably recorded with six ambiguous acquired icons and retained screenshot/candidates; quantities remained unknown. Its mission attribution remained unknown, rather than being assigned to a legacy stage. This validates reward capture and conservative storage, not universal item identity or quantity accuracy. No stage configuration or drop table was created. Actual unfamiliar-stage Start/results captures are saved as shared-layout regression fixtures.

All Scrolls [A] also passed the same complete loop and clean Stop in Universal mode. Its title was detected, and its reward record was attributed to All Scrolls [A], with ten recognized acquired icons and unknown quantities.

Getting Rich Quick! [B] passed a clean Start/results/Retry/Start loop and Stop after resource recovery. The resumed session recorded two completions with the correct mission identity, twelve recognized icons in total, additional unknown detections, unknown quantities, and retained diagnostic evidence. The test stopped the supervisor and worker, leaving the game alone.

An earlier overlapping regression run and loot integration run exhausted Windows virtual memory during the initial Rich check, causing image/process errors and a harmful exit reported by the user. The test processes exited; the controlled controller was stopped, and available virtual memory recovered to roughly 16 GB. Live validation then ran without concurrent heavy tests and no further memory errors were observed. Run resource-heavy test suites sequentially, with farming stopped. This incident does not establish extended unattended reliability.

Stamina No/wait/retry timing has saved-screen and simulated-clock coverage; natural stamina exhaustion has not been observed in this 1.2 live session. No premium resources were used.

## Continuing limits

### Trial results follow-up (October 13, 2026)

Wager with Tsunade [A] was stalled on its extra Rare Map Completion Reward / Total Acquired Coins / Final Results page after the ordinary results Next. The prior input remained latched and no repeated clicks were sent. Added a shared `TRIAL_COINS_NEXT` results layout with five static visual guards and a button-derived Next target; mission titles and variable coin amounts are excluded. No trial-stage configuration or combat changes were added.

All 70 relevant universal, legacy-stage, safety, completion-recognition and stamina tests passed while the controller was stopped. New saved-screen tests cover the trial layout, every required guard, dimmed dialogs, variable title/totals, recovery of the previous Next latch, one input per transition and no duplicate completion count. After restart, the controller advanced the stalled page, revealed rewards and retried. A fresh mission then completed and automatically passed ordinary results → trial coin summary → acquired rewards → Retry → Start; the macro was left running in Universal mode with the input latch resolved.

Fresh menu navigation, unobserved dialog variants, natural defeat OCR/alert behavior and extended uninterrupted farming retain live verification gaps. The recognized stamina flow and ten-minute deadline do not imply coverage of all stage/dialog forms. Night of Tragedy has saved-screen/controller coverage; the recognition live sample covered Practice only. Broader reward layouts, animations, arbitrary character forms and acquired quantity accuracy require separate evidence. A rebuilt library may disable automatic calibration because its database hash differs.
