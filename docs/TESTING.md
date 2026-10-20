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

Use `--suite controller`, `--suite library`, `--suite recognition`, or `--suite all` for broader coverage. Suites run sequentially to limit memory pressure. `--cv-threads N` permits an explicit thread comparison; the limit affects only the test process. Existing `python -m unittest` discovery commands remain supported.

Saved screenshot tests use `tests/fixture_images.py` to avoid decoding the same large PNG repeatedly. Its LRU holds at most 64 MiB, checks file modification/change times and size, and returns a fresh writable copy so edits cannot contaminate another test. Missing/corrupt inputs fail with their absolute path. Production templates and hot reload tests retain the actual image-loading path; the helper is for test input screenshots only.
