# Getting Rich Quick [B] — 2026-10-11

Uses the existing acc1-only engine, ADB identity checks, screenshot recognition, persistent input latch, local loss OCR/alert, logging, recovery and independent WMI supervisor. No combat settings, purchases, stamina restoration, inventory sale/release or game resets were performed.

## Commands

```powershell
& D:\NBR\nbr.ps1 stop
& D:\NBR\nbr.ps1 select -Stage rich-b    # Getting Rich Quick [B]
# Manually open this mission's Start Mission screen if changing missions.
& D:\NBR\nbr.ps1 start
& D:\NBR\nbr.ps1 status
& D:\NBR\nbr.ps1 stop

& D:\NBR\nbr.ps1 select -Stage scrolls-a # All Scrolls [A], while stopped
& D:\NBR\nbr.ps1 start
& D:\NBR\nbr.ps1 status -Stage scrolls-a
```

Python equivalent: `D:\NBR\.venv\Scripts\python.exe D:\NBR\farm.py <command> --stage rich-b` (or `scrolls-a`). Omitting the stage uses the saved selection. Stop always applies to acc1. Start refuses a different active farm; repeated same-stage start is idempotent.

## Configuration and preservation

- `stages/scrolls-a.json`: original `patterns/catalog.json`, original `runtime/farm.json`.
- `stages/rich-b.json`: separate `patterns/rich_b.json`, separate `runtime/rich_b.json`.
- Account-wide STOP, worker/control/supervisor byte locks, PID records and mission-identity session prevent competing controllers. Stage switches retain counts, pending input and stamina deadlines. A matching title must authorize generic completion.
- Original catalog and progress were byte-identical to the saved pre-change copies after testing. Backups are in `development/backup_second_stage`, `development/farm.before_stages.py` and `development/nbr.before_stages.ps1`.
- NumPy/OpenCV use one numeric thread to avoid large allocations across Windows launcher processes. The supervisor remains independent of Codex.

## Verified

- Visually inspected actual acc1 Getting Rich Quick [B] rank and Select Team screens; natural stamina cost 10, Pearl balance 413.
- Start transitioned through loading into unchanged automatic combat. Completion recognition and passive battle observation reused existing patterns.
- Exact B title registered for Start, results Next, acquired reward reveal and Retry. Next/reveal/Retry all produced verified transitions.
- Three missions completed. The second went through the entire results/rewards/Retry path without development intervention; the third also returned through rewards/Retry and started a fourth. The B farm was left running independently in its fourth automatic battle.
- Independent supervisor launch, clean stop/restart, same-stage duplicate start, active opposite-stage start/selection rejection, and stopped selection in both directions exercised live. Only one input worker held the global lock.
- After a read-only selection test during the known third B mission, its identity was restored under all global locks from its recorded B-title Start event; no intervening game inputs occurred.
- 42 offline tests pass, including the original 25, repeated screenshot replay cycles, wrong-stage and wrong-rank rejection, modal guards, saved latches/progress, real Windows lock contention, launcher parent PID claims and cross-stage authorization.

## Limits

The first B-rank input did not advance. It was sent once, retained as a pending latch, and the controller stopped. The user manually opened Start Mission; its verified transition resolved that latch. Fresh mission-list/rank navigation is therefore incomplete. Banner/list patterns are registered from inspected saved evidence but are not a verified unattended navigation route. Solo/helper screens remain unsupported for this stage because this observed Retry path did not require them.

The B rank-up stamina acknowledgement was subsequently captured at rank 80 to 81. Its exact message, OK and dimmed B-title anchors now reuse the existing safe dismissal. Live testing confirmed one OK followed by results Next, rewards and Retry; farming resumed. The acknowledgement regression rejects altered text/button/title, checks persistent latching and verifies that STOP is not set.

After 14 completions, another captured reward layout exposed a blank-footer background brightness shift: its average grayscale difference was 12.57 against a limit of 12, while all title/label guards matched. Only that background guard now permits a difference of 16; correlation and title/label limits are unchanged. The orange Retry footer differs by 116.69 and remains rejected as reward reveal. A regression verifies that the old Next latch resolves once on this variant and dim overlays still block input. All 44 offline checks pass.

A subsequent manually resumed B mission reached completion with an old `select_rank_b` latch. The engine now accepts battle/completion as a recovery specifically from that B-rank selection when the persisted account session already verifies B. It tracks a recovered run and advances completion once; it does not resend rank selection. Missing stage authorization and unrelated pending controls remain blocked. All 46 checks pass, including the captured stuck completion frame.

Communication-error recovery now also accepts verified battle/completion after the exact `retry_connection` / `COMMUNICATION_ERROR` input when both the engine and saved account session agree on the stage. This fixes a later completed mission blocked by an old connection latch without issuing another Retry.

Quick completion recognition: four captured glow variants are supported per fixed Mission/Complete text anchor for B, retaining the original .97 correlation and difference limits. Undimmed AUTO/Menu landmarks additionally guard the completion action against overlay dialogs. All 25 animation frames (including eight holdout captures) classify as complete, while dimmed versions, missing words/HUD landmarks, battle-only and menu/results screenshots are rejected. Variants are restricted to fixed completion anchors and do not alter the original Scrolls catalog. Battle polling is 2 seconds instead of 10, with two fresh agreeing observations still required before input. Unknown animation frames during a verified unfinished mission also retain the 2-second poll instead of waiting 30 seconds; their stop-only loss OCR remains active. Other unknown states retain the longer wait. All 56 checks pass. Live recovery passed through results/rewards/Retry and another B mission completed successfully.

The B insufficient-stamina popup is now captured. Its exact header, refill question, green No button and dimmed B title guard a single No tap. The confirmed return to the next screen starts a persisted 600-second (10-minute) wait before Retry. A provisional deadline and input latch are saved before No, so interruption cannot replay the tap or skip the wait. A returning popup repeats this cycle. Timer boundary, persistence, transition and screenshot-guard regressions pass; the full suite has 62 tests. Live resume sent one No at (299, 1018), confirmed READY_TO_RETRY after 3.9 seconds, and entered the persisted countdown without a Retry input. Evidence: `development/rich_b/not_enough_stamina.png` and `development/rich_b/after_stamina_no.png`. The 600-second boundary was verified with a simulated clock; the live farmer was left running through its natural wait.

Actual defeat/revival and additional dialog variants have not been captured for B. Unrecognized screens remain observation-only; loss OCR is stop-only, and detected loss persists STOP and launches the local alert. No restoration/revival action is permitted. Existing All Scrolls safety/replay tests pass; a new live Scrolls mission was not run during this B-only implementation. Its pre-existing fresh-menu blocker remains.

Run checks: `D:\NBR\.venv\Scripts\python.exe -m unittest discover -s D:\NBR -p test_*.py`.
