# NBR 1.25 cleanup report

This maintenance update organizes existing code and entry points without changing farming behavior. Runtime locations and active processes are preserved. Existing uncommitted implementation, asset and regression-test changes were carried forward.

## Directory structure

```text
NBR/
├── farm.py, manager_gui.py, nbr.ps1
├── Launch NBR Manager.cmd, Setup NBR Manager.cmd
├── README.md, requirements.txt, requirements-gui.txt
├── src/
│   ├── app/
│   │   ├── gui/             # window.py, theme.py, art.py, loot.py
│   │   ├── backend/         # controller.py, loot.py
│   │   └── core/            # process_state.py
│   ├── automation/          # controller.py, adb.py
│   ├── game_library/        # Local reference database/browser and collection tools
│   ├── item_recognition/    # Offline recognition and reusable evidence tools
│   └── *.py                 # Existing entry points and import compatibility
├── assets/
│   ├── patterns/            # Stage/shared catalogs and image guards
│   ├── item_recognition/    # Layout/quantity templates and reviewed calibration
│   ├── manager/             # Qt arrows
│   ├── library_browser/     # Local browser interface
│   └── reference_images/    # Required saved recognition evidence
├── tests/                   # Reusable regressions and saved fixtures
├── scripts/                 # Existing launchers, PowerShell and loss helpers
├── docs/images/             # Retained manager design reference
├── runtime/                 # Ignored live state, logs, loot and local library
└── .venv/                   # Ignored installed environment
```

No empty package or separate stage/config hierarchy was introduced. Current stage definitions remain in the existing catalogs; Universal uses their verified layouts for All Scrolls [A], Getting Rich Quick! [B] and other recognized screens.

## Source moves and references

| Previous implementation | Current implementation |
| --- | --- |
| `src/manager_gui.py` | `src/app/gui/window.py` |
| `src/manager_theme.py` | `src/app/gui/theme.py` |
| `src/manager_art.py` | `src/app/gui/art.py` |
| `src/manager_loot.py` | `src/app/gui/loot.py` |
| `src/manager_backend.py` | `src/app/backend/controller.py` |
| `src/loot_tracking.py` | `src/app/backend/loot.py` |
| `src/process_state.py` | `src/app/core/process_state.py` |
| `src/farm.py` | `src/automation/controller.py` |
| `src/bluestacks.py` | `src/automation/adb.py` |
| `refs/preferred_retro_layout.png` | `docs/images/preferred_retro_layout.png` |

The old Python modules remain thin aliases so existing imports, monkeypatch-based regression tests and launch commands resolve the same module objects. `src/farm.py` and `src/manager_gui.py` remain executable. Root Python, PowerShell and CMD wrappers add convenient launch paths. Imports, project-root discovery and GUI resources are updated for the implementation locations. Notification-helper paths and detached controller commands retain their existing entry points, preserving process ownership checks. Library and recognition package/resource paths stay stable. README and manager/repository documentation describe the current layout; prior validation evidence is retained with its original scope.

## Resource and retention review

| Group | Classification and action |
| --- | --- |
| Controller, GUI, backend and ADB integration | Moved into the named packages above; original entry points retained. |
| Stage catalogs and pattern images | Retained. All 266 template/alternative references resolve to 137 unique files. |
| Recognition templates, calibration, ground truth, browser and Qt assets | Retained as application or regression dependencies. |
| `tests/` | Retained reusable safety, persistence, GUI, library and recognition regressions, including tests already uncommitted at cleanup start. |
| `runtime/` state, locks, logs, loot, library, reports and screenshots | Retained in place. Includes pending recovery evidence, unique local history and absolute references. |
| `runtime/NBR-AM/.git` | Independent Git history retained, including its existing local change. |
| `refs/preferred_retro_layout.png` | Unique design reference moved to documented `docs/images/`. |
| `config/` and former `refs/` | Physically empty and untracked. No new empty hierarchy added. Removal was deferred after automatic policy rejection. |
| `.venv/`, caches and runtime outputs | Remain ignored; no tracked disposable outputs identified. |

Unreferenced recognition files `assets/item_recognition/stage-0.png`, `stage-1.png`, and `assets/patterns/night_of_tragedy_a/mission_complete/variant_09_0.png` are retained because their future recovery/calibration value is uncertain. `assets/patterns/universal_results.json` is a valid shared-results source catalog whose state also appears in `universal.json`; it remains useful provenance and is retained. Dynamic fixture loading and JSON references mean a missing literal filename reference alone does not establish that an asset is disposable.

Historical captures, local repair tools and validation reports under `runtime/validation/`, `runtime/item_recognition/` and `runtime/navigation_references/` are also retained: some preserve unique diagnosis or recovery evidence. None was executed during cleanup.

## Deferred removals and rollback

Six ignored, unreferenced navigation/investigation probes were confirmed obsolete: `runtime/build_navigation.py`, `check_navigation.py`, `manager_live.py`, `mission_metadata.py`, `nav_metrics.py`, and `nav_probe.py`. Several depend on a nonexistent `navigation` module or `assets/navigation/`; others hard-code the completed acc2 investigation. They are outside every application launcher/import path.

All six files were archived and their individual SHA-256 checks verified in:

```text
C:\Users\natey\.codex\attachments\a05040a3-2c93-4357-a682-8ba33e2015da\v1.25-removed-probes.zip
Archive SHA-256: f3ad8e5ec30293d96af2e9ee96b5a74b73c110188111dff07e5e46a59b56f1e5
```

Automatic approval review rejected the removal command as `blocked by policy`, with no specific reason supplied. The six originals remain at their original runtime paths. No source, test, asset or runtime data files were deleted. Empty directory removal is also deferred.

The working branch is `codex/v1.25-cleanup`. Rollback is available through `refs/cleanup-backups/v1.25-before`, commit `ebfdf94e38245934033b6e5eb11b44d568cb9743`. It preserves the pre-cleanup working state as well as existing Git history. Inspect the backup before restoring files; avoid a blanket checkout/reset while farming is active. Ignored runtime data and `.venv/` remain separate from Git and require their own backups.

## Validation

Completed static audit: 209 asset files classified; all 266 pattern template/alternative references resolve; no disposable runtime/environment outputs are tracked by Git. The calibration/reviewed-reward JSON files include fixture basenames that tests deliberately resolve under `tests/fixtures/item_recognition/`; these are evidence labels rather than missing adjacent resources.

Syntax checks passed for 44 source/entry-point files. Canonical and legacy aliases, package/resource roots, PowerShell syntax, CMD paths and root CLI help from a different working directory passed. The controller/GUI suite passed 395 tests in 160.633 seconds; the library suite passed 63 tests in 6.569 seconds. Focused structure/entry-point checks passed seven tests in 4.543 seconds, including one added after the full discovery run (the other six overlap). GUI checks use offscreen windows and fake controllers.

The recognition suite passed 94 tests in 22.047 seconds. Together the full and focused runs cover 553 distinct tests, with no failures or skips; see [VALIDATION.md](VALIDATION.md). Runtime settings, state and lock file hashes matched before and after the final suites. Snapshot comparison confirmed 440 existing files outside the explicitly changed paths were preserved. The complete patch, including new files, passed whitespace checks. No live farming commands, BlueStacks operations, device input or active-session restart were performed during this cleanup.
