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

## Remaining limits

Fresh menu navigation, unobserved dialog variants, natural defeat OCR/alert behavior and extended uninterrupted farming retain live verification gaps. The recognized stamina flow and ten-minute deadline do not imply coverage of all stage/dialog forms. Night of Tragedy has saved-screen/controller coverage; the recognition live sample covered Practice only. Broader reward layouts, animations, arbitrary character forms and acquired quantity accuracy require separate evidence. A rebuilt library may disable automatic calibration because its database hash differs.
