# Quick offline regressions

Run Ninja Road detection, transitions, boss swaps and fixture-cache checks from any working directory:

```powershell
D:\NBR\.venv\Scripts\python.exe -B D:\NBR\scripts\test_offline.py -q
```

The runner supplies import paths and offscreen Qt, limits OpenCV to one thread in its own process, and reports the slowest tests including setup and cleanup. The default group uses saved screenshots and fake input. It does not start the macro or connect a new device.

Repeat in a reproducible mixed order to expose state leaks:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_offline.py -q --repeat 3 --shuffle-seed 42
```

Each repeat adds one to the seed. A failing run returns a nonzero exit code and stops subsequent repeats. To run one regression, pass a dotted unittest name:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_offline.py test_ninja_road.NinjaRoadTests.test_two_full_fixture_attempts_reset_and_reenter_team_one
```

Transition handling, LOADING, dispatch deferral and raw capture: `scripts/test_offline.py -q test_transition_grace`.

Use `--suite controller`, `--suite library`, `--suite recognition`, or `--suite all` for broader coverage. Suites run sequentially to limit memory pressure. `--cv-threads N` permits an explicit thread comparison; the limit affects only the test process. Existing `python -m unittest` discovery commands remain supported.

The recording-failure regressions can be run with:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_offline.py -q test_atomic_io test_loot_retention test_recording_persistence test_loot_durability test_recording_recovery test_recording_stop_capture test_missing_evidence_durability test_missing_evidence_recovery test_start_restart_recovery test_connection_recovery test_recording_integration test_recording_dashboard test_loot_gui test_manager_loot
```

These tests cover bounded diagnostic pruning, protected evidence, interrupted pruning, Windows replacement conflicts, capture-error provenance, stale-run capture rejection and explicit offline missing-evidence recovery. They use disposable state; retention tests never prune production diagnostics.

Saved screenshot tests use `tests/fixture_images.py` to avoid decoding the same large PNG repeatedly. Its LRU holds at most 64 MiB, checks file modification/change times and size, and returns a fresh writable copy so edits cannot contaminate another test. Missing/corrupt inputs fail with their absolute path. Production templates and hot reload tests retain the actual image-loading path; the helper is for test input screenshots only.

## Live Ninja Road observation

For live Ninja Road observation, run `scripts/monitor_ninja_runs.py --output-dir <folder> --expected 3` with the repository Python. It records three distinct successful exits for the saved mode, reports defeats separately, and retains worker screenshots without sending device input. Reusing the output folder resumes the same observation window; `--once` refreshes its `summary.json`. A resumed attempt is labelled as partially observed. The monitor reports persistent stalls, stale heartbeats and a bounded timeout; controller Start and Stop remain separate commands.

## Retained loot evidence

Audit the latest three saved runs against the existing loot database, durable receipts and pending spool:

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_loot_evidence.py --evidence-dir runtime/validation/adb_three_runs_20261021_88f93315 --expected 3 --require-count
```

Use another `--evidence-dir <folder>` to check a different saved observation window. `--expected` defaults to three; `--require-count` makes fewer captured runs fail the command. The verifier checks saved PNG integrity/hashes, receipt and database identities/results, reward boxes, quantities, global duplicate identities and pending work. Candidate identities remain candidates; integrity passing does not establish that every reward was automatically identified or that item quantities are known.

The default prints the audit summary and creates no reports. To explicitly save JSON and text reports, append `--report-dir <folder>`. Existing evidence, loot databases and receipts remain unchanged. These checks use the current local store and require its retained data; they do not start a collector, connect ADB or run the macro.

Keep regression fixtures that tests reference, calibration evidence, retained diagnostic screenshots and the latest three-run PNG/JSON evidence. Remove completed one-time monitors and unreferenced captures only after checking code, catalogs, calibration, SQLite/receipt references and persisted recovery state. Reusable verification scripts belong in `scripts/`, rather than a dated runtime evidence folder.
