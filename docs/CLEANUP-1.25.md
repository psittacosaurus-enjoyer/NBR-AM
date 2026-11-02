> **Historical cleanup report.** Kept for provenance; it does not describe the current design. See [ARCHITECTURE.md](ARCHITECTURE.md).

# NBR 1.25 cleanup report

The cleanup keeps the existing manager and farming behavior while removing redundant source, assets and local investigation output. Active controllers, account/device state, pending evidence, loot history and the installed `.venv` are protected. No new archives or backup copies are created.

## Source and entry points

- `src/app/` owns manager presentation, backend and process checks; `src/automation/` owns farming and BlueStacks/ADB integration. Callers and tests import these packages directly.
- Eight redundant source aliases are removed. `src/farm.py` remains the detached controller entry point because process ownership checks and running controllers depend on that path.
- There is one Manager implementation in `src/app/gui/`. Root Python files call the named packages; root CMD and PowerShell files provide public shortcuts to the shared scripts, which also serve library, recognition, notification and offline-test workflows. These small entry points remain supported.
- Catalog references use canonical pattern files. Duplicate screenshot fixtures share one saved input, with tests updated to preserve their assertions.

## Assets and local data (October 20, 2026)

The tracked asset cleanup removes 52 files totaling 22,740,641 bytes: 35 duplicate pattern images, seven unused images, nine duplicate fixtures and one redundant catalog. Required stage guards, recognition templates, calibration evidence and the documented Retro layout reference remain.

The October 20 pass deleted 623 unnecessary files totaling approximately 553 MiB, including source aliases, local probes/captures, obsolete Library snapshots/reports, duplicate downloads and caches. These are historical totals for that pass, separate from the October 21 follow-up below. Shared hard-link storage is not counted as additional savings. Temporary validation databases and cleanup scripts were removed.

Library consolidation keeps one record per proven identity and one asset per identical image content. Historical IDs resolve through redirects, preserving annotations, scoped source provenance and calibration membership without duplicate records. Different rarity/element forms remain distinct. Database and supplemental calibration fingerprints are refreshed only after saved-image compatibility checks pass; `--baseline-calibration` explicitly selects the calibration matching an earlier snapshot.

The active Library has 1,822 records after removing 43 duplicate item records, with 43 historical-ID redirects. Consolidation removed 148 duplicate image rows and 65 redundant image files; 65 manifest and crawler-cache references now use canonical paths. Five obsolete database snapshot directories were deleted. Available image content has no duplicate rows.

Obsolete runtime probes, reports and captures are deleted only after checking production, calibration, test, live-state and loot-history references. Persistent settings, counters, input latches, locks, session history and pending jobs/evidence keep their existing paths. The active `.venv` is retained.

## Follow-up cleanup (October 21, 2026)

This additional pass removes six unreferenced screenshot fixtures, three obsolete setup PNGs, a duplicate Tsunade image and the completed three-run monitor. The five thin root launchers remain, together totaling 1,327 bytes; `src/farm.py` retains its process ownership role.

The audit also identified redundant `runtime/NBR-AM/` Git metadata. The nested repository contains no application working files; its three commits and all 460 stored objects were verified in the outer repository's reachable history. Automatic approval review rejected deletion as "blocked by policy". Its 34 files totaling 328,757,876 bytes (313.53 MiB) remain intact, and no removal savings are claimed for that directory.

The dated `verify_integrity.py` is promoted to the reusable `scripts/verify_loot_evidence.py`. Callers select evidence with `--evidence-dir <folder>` and can enforce a minimum observation count with `--expected 3 --require-count`. The default prints a read-only audit without creating reports; `--report-dir <folder>` explicitly requests JSON and text reports. The completed monitor is removed, while the saved review points to the reusable verifier.

The completed removals cover 12 old paths totaling 15,403,017 bytes (14.69 MiB) gross, including the old verifier path now replaced by reusable code. This gross figure excludes added/revised source and is separate from the earlier 623-file cleanup. The local [deletion manifest](../runtime/cleanup/disposable_tests_20261021/deletion_manifest.json) records exact paths, hashes, sizes and reasons; its [results](../runtime/cleanup/disposable_tests_20261021/deletion_results.json) distinguish the 12 removed paths from the 34 blocked Git files. These audit files are ignored local data.

The final complete offline suite passed **730 tests in 205.431 seconds**, including 19 isolated verifier regressions. All **5,633 protected files** retained their fingerprints. Saved three-run evidence passed the promoted checker; candidate identities and unknown quantities remained distinct from integrity results. Main-repository Git integrity, PowerShell syntax, foreign-working-directory entrypoints, asset references and whitespace checks passed. Independent review corrections cover Windows console Unicode, fully decoded PNG evidence and legacy session-scoped run identities.

The latest three reward PNG/JSON pairs, baseline, live/integrity reports and review remain. All loot databases, receipts, diagnostics, pending evidence, library references and calibration data are retained. Recovery folders referenced by live persisted state remain, along with account/device settings, locks, latches, counters and session histories. This pass does not operate the game, restart controllers or establish new live farming coverage.

## Historical validation (October 20, 2026)

The pre-cleanup baseline passed all **685 tests** in **224.989 seconds** with:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_offline.py --suite all -q
```

The final suite passed all **698 tests** in **208.335 seconds**. Active-Library comparisons preserved recognition on **28 saved screenshots / 158 reward icons**, including thresholds, quantities and supported identities. SQLite integrity, foreign keys, 1,974 original hashes/thumbnails, 1,432 manifest resources, launcher syntax and deleted-path checks passed. All 478 protected state/evidence files retained their hashes.

The suite uses temporary writable state, saved images, fake devices/backends and offscreen Qt. No live farming actions or game inputs were used. See [validation history](VALIDATION.md) for dated evidence and [repository data boundaries](REPOSITORY.md) for retention rules.
