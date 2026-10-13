# Live recognition validation — October 12, 2026

The updated recognition pipeline was observed processing real acc1 farming completions. Four fresh solo Practice Training [Part 1] cycles were independently reviewed: **16/16 acquired reward icons correct**, zero unknown/ambiguous identities and zero recognition failures in those cycles. All quantities remain **Unknown**. This is scoped validation of the encountered reward forms, not a claim that every library reference is live validated.

## Implemented changes

- All 185 eligible item records / 229 item images remain loaded from the existing read-only `ReferenceLibrary.item_references()` API.
- Character references use the same library's public `ReferenceLibrary.search(record_type='character', status=..., page=..., limit=120)` and `get(record_id)` APIs: 283 records / 306 eligible card/portrait images. The total bank has 535 decoded images, zero errors. No library database, source assets or installed game files were changed.
- `item_recognition/reward_identity.py` groups compatible records by explicit shared source game ID. Reviewed calibration evidence bridges mission/unit duplicates for four Health/Attack boost forms. Different names, known element/rarity/game IDs and character forms cannot be merged merely because their artwork resembles each other. Canonical IDs/names remain actual library records; aliases and winning-asset provenance remain in results.
- Four-star Sakura card379 and five-star Kushina card231 have independently reviewed acquired-screen calibration. Sakura card380 remains separate and uncalibrated. Global threshold0.75/margin0.019265 is unchanged; Sakura379 uses0.70/0.055, Kushina231 uses0.78/0.04. Other unvalidated forms remain candidates/unknown.
- Full-image matching retains element badges, frames and rarity stars. Overlay masking was evaluated and rejected because it reduced discrimination. The Sakura gate passed10/10 real crops and produced zero false Sakura379 accepts across534 other decoded library images, including the five-star form.
- The Manager labels the summary table Item / Character and exposes type/rarity/element provenance in tooltips; confirmed quantity handling is unchanged.
- Development restart checks Stop ownership inside the existing account control lock. Missing/replaced tokens cancel restart. This does not change ordinary Start/Stop semantics.

## Fresh production evidence

`live_runs/` contains four bounded PNG/JSON pairs captured by the actual farmer-owned collector, after the completed calibration was loaded. Each run's acquired grid contains:

| Actual library identity | Observed icons across four validated runs | Confirmed acquired |
|---|---:|---|
| Sakura Haruno "Dancing Cherry Blossoms", card379,4-star Body |8|Unknown|
| Health Boost "Health and Endurance", unit11812,2-star Bravery |4|Unknown|
| Attack Boost "Sphere of Strength", unit11822,2-star Wisdom |4|Unknown|

The Completion Reward Conditions area was excluded. An independent reviewer compared all four PNGs with authoritative library images and the persisted IDs. Every screenshot hash matches its recognition JSON. The recognizer reports partial because quantities are unavailable; the collector correctly reports recognized because the identities are verified. Neither status creates inferred acquired units.

`independent_live_review.json` and `validated_live_cycles_contact.png` provide the visual check. `live_validation_summary.json` records completion IDs, hashes, database/retention checks and limitations. `manager_live_records.png` shows the actual persisted records rendered by the Manager with automatic background reads; no controls or connection checks were invoked by that offscreen GUI verification.

At the Manager check, the continuing live session had seven completed runs and28 recognized icons, zero unknown/ambiguous/failures, and no confirmed quantities. Those ongoing statistics are distinct from the four cycles that received full independent visual review.

## Persistence, retention and safeguards

At the audit snapshot, all35 durable completions had unique completion IDs and four historical sessions remained present. The four reviewed runs were persisted as recognized, their production temporary screenshots removed, and their separate bounded development copies retained. Ordinary known results retain structured records only; uncertain/failed results retain diagnostics. Existing history and screenshots were not reprocessed, reset or deleted. The project-local validation marker is now disabled; it was bounded to four frames and expires independently.

One completion in the preceding development session was marked missing_screenshot when the controlled reload happened before fully revealed results. Its existing `before_reload_1791861889.png` capture is preserved. The missing status was not replaced with fabricated loot. Initial Practice cycles also exposed uncalibrated small boosts and the Lucky Drop overlay, which led to the evidence-backed changes above. A logging error in the first manual-start attempt occurred before input and was corrected; conservative reload guards also aborted transient/unsupported phases before process control.

Only acc1 was controlled. Its identity was verified before input. The only direct development game input was the verified one-stamina solo Start Mission action; subsequent replay used the existing guarded farmer. No summoning, ticket/free summons, purchases, Ninja Pearl spending, APK modification or game reset was performed. A user Stop supersedes development ownership; no automatic restart is allowed after a replaced latch. Acc1 was left farming independently after validation.

## Tests and limits

- Full offline regression:213 tests passed, zero failures (161 Manager/controller/control,52 recognition).
- Latest affected-suite rerun:67 passed after final Manager/core changes.
- Real saved Night results:51/51 correct icons across nine held-out runs, without changing runtime history. Original48-icon dataset retains42 correct verified identities, one candidate and five unknowns; six negative screens have zero accepted identities.
- Fresh post-update farming validation covered Practice Training only. All Scrolls [A] / Getting Rich Quick! [B] retain saved-screen and offline controller regression coverage; they were not newly farmed during this validation.
- All185 item and283 character references load, but only the validated forms may be accepted. Arbitrary character forms, future layouts and acquired-quantity semantics remain unverified.
- References/calibration are snapshots per collector; a new worker loads a changed revision. Runtime source reload is not claimed.

## Relevant files

Production changes: `item_recognition/recognizer.py`, `item_recognition/reward_identity.py`, `item_recognition/calibration.json`, `loot_tracking.py`, `farm.py`, `manager_gui.py`.

Tests/documentation: `item_recognition/tests/test_live_upgrade.py`, `test_live_control.py`, `MANAGER.md`, `item_recognition/README.md`.

Development verification tools: `live_control.py`, `evaluate_saved_rewards.py`; action history: `actions.jsonl`. The pre-upgrade calibration remains preserved as `calibration_before.json`.
