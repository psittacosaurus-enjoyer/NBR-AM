# NBR Automation Manager 1.25

NBR is a local Windows controller and PySide6 desktop manager for Naruto Blazing Revival. It recognizes verified screens, handles mission results and replay, and records persistent session loot. Normal recognition uses local image matching; it makes no AI or cloud OCR calls.

Version **1.25 — File Structure Cleanup** separates the desktop application, controller and integrations into named packages and adds convenient root launchers. Existing commands, saved settings, stage recognition, recording recovery and runtime locations remain compatible. See the [cleanup report](docs/CLEANUP-1.25.md), [validation history](docs/VALIDATION.md) and [recording lifecycle](docs/MANAGER.md#recording-pipeline-and-recovery).

## Requirements and installation

Use Windows, Python 3.13, PowerShell and BlueStacks. The manager discovers local BlueStacks configuration and installation paths, including custom instance names. Open **Instance**, select your instance and apply it while BlueStacks is running. The controller verifies its configuration mapping, ADB endpoint and Android identity before inputs and after reconnection. Only one instance can farm at a time. The game package remains `com.reblazing.nbrc`, with 900 × 1600 portrait screenshots.

From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
```

The core requirements pin NumPy, Pillow and headless OpenCV; GUI requirements pin PySide6. Installation needs internet access. `Setup NBR Manager.cmd` creates `.venv` when missing and installs both dependency lists. The original launcher in `scripts/` remains available. Keep Windows awake and start BlueStacks manually after a restart.

Loot recognition additionally needs the local Game Asset Library. See [library setup](docs/LIBRARY.md). Downloaded references and user annotations stay in ignored `runtime/library/`. Installing packages alone does not reconstruct that data. Calibration applies automatically only when the library database SHA-256 matches; a rebuilt library needs reviewed offline validation before calibrated recognition can be trusted. Saved evidence remains pending when recognition infrastructure is unavailable. Registration or screenshot persistence failures pause mission progression until recording recovers.

## Launch and control

Double-click `Launch NBR Manager.cmd`, or run:

```powershell
.\.venv\Scripts\python.exe manager_gui.py
```

Open **Instance** to refresh detected installations and choose the desired instance. A single unambiguous instance is selected automatically; multiple instances require a choice unless a saved selection remains valid. **Apply Selection** verifies and saves the target for subsequent launches. Open a mission's **Start Mission** screen in that instance, return to **Dashboard** and press **Start**. Universal handles verified Start, completion/results, rewards, Retry and natural stamina waiting. The game handles combat. Unfamiliar screens wait safely for manual resolution.

The sidebar contains **Dashboard**, **Instance** and **Settings**. Instance selection is locked while any farming process is active or its state cannot be confirmed. The Dashboard continues to show that process's pinned instance. Stopping safely permits another selection; closing the manager continues to leave farming running independently.

**Manual Setup** accepts an exact friendly name or internal identifier. If automatic resolution is unavailable, its advanced option accepts an explicit local ADB endpoint. Targets must pass identity verification; ambiguous names, missing identities and mismatches are rejected. Discovery and Refresh never start, stop or modify BlueStacks, and never connect a disconnected ADB transport. Open BlueStacks and enable its Android Debug Bridge setting if verification is unavailable.

The supervisor runs independently of the GUI. Closing the manager leaves farming running; reopening restores status and elapsed time. **Stop** requests an account-wide safe stop and allows a bounded operation to finish, leaving BlueStacks and the game open.

The default appearance is Original; **Settings → Appearance** also offers Retro 98. **Settings → Activity log** and **Settings → Recognition details** show optional diagnostics. Both start hidden and remember their settings.

PowerShell CLI, from the repository root:

```powershell
.\nbr.ps1 status
.\nbr.ps1 start
.\nbr.ps1 stop
```

Python CLI (also usable when PowerShell execution policy blocks scripts):

```powershell
.\.venv\Scripts\python.exe farm.py status
.\.venv\Scripts\python.exe farm.py start
.\.venv\Scripts\python.exe farm.py stop
```

The original `src/farm.py`, `src/manager_gui.py`, `scripts/nbr.ps1` and CMD launchers remain supported. Root launchers derive paths from their own location and can be invoked from another directory. Repeated Start does not create another worker. `replay --image <path>` classifies a saved image offline. `observe` captures from the device; `step` can send guarded input. Use saved-image replay for offline maintenance checks.

## Status, loot and troubleshooting

Gray **STOPPED**, yellow **STARTING / STOPPING**, green **RUNNING**, and red **ERROR** describe controller processes. Connection availability appears separately: a running process can be waiting for BlueStacks. Enable **Activity log** in Settings to inspect controller events and manager errors.

Loot is always visible as library item icons with drop-count overlays. Each visible acquired icon counts as one drop; printed quantities and rarity stars do not multiply it. Unidentified drops remain separate, and `?` means the drop count is unavailable. Recognition details adds observed/completed/failed counts, historical sessions, warnings and diagnostic screenshots. See [manager details](docs/MANAGER.md).

Recording health remains visible when work is pending or failing, even with Recognition details hidden. States distinguish completion registration, capture pending, screenshot saved, recognition pending, successfully recorded, retryable failure and operator attention. Temporary failures retry with backoff; after three failed registration/capture attempts, operator attention is shown while safe retries continue. Stop attempts one final read-only capture of already revealed results and preserves unfinished work if it cannot save them. Restart recovery requires a previously persisted matching transaction; a results screen alone cannot create past loot.

| Symptom | Check |
| --- | --- |
| Missing environment or PySide6 | Follow installation; use the setup launcher for GUI dependencies. |
| Controller disconnected | Open the selected BlueStacks instance, check its ADB setting and refresh Instance. Identity or mapping changes require a fresh verified selection after farming stops. |
| No input after Start | Prepare a supported Start Mission screen; enable Activity log in Settings to inspect unrecognized screens. |
| Unfamiliar screen or latched action | Stop and resolve the game manually; preserve persisted input latches. |
| Stamina wait | The ten-minute deadline survives restarts and repeats if stamina remains insufficient. |
| Loss stopped farming | Inspect local alert/evidence, resolve the game manually, then explicitly Start. |
| Capture pending / Operator Attention Required | Keep the results screen open. Check disk space, runtime permissions and the displayed error; automatic Retry stays blocked until evidence is saved. |
| Capture pending after results were already left | Stop farming. With the same instance on Start Mission, run `.\.venv\Scripts\python.exe farm.py resolve-missing-evidence --reason "Results left after interruption"`, then Start. This explicitly retains the old completion as missing evidence with no reward credits. It refuses active controllers, pending inputs and recoverable screenshots. |
| Recognition Pending / Retryable Failure | Saved evidence remains on disk and retries automatically. Check the reported database/library error and storage limits. |
| Tracking Unavailable | Check collector diagnostics. Initialization retries within the same worker; an already-running older worker loads the update only on its next start. |
| Recognition changes have no effect | Current collectors reload database/calibration revisions before new reward jobs. Restart a worker running older Python code once to install this behavior. |

Never reset runtime files to bypass identity checks, intentional stops or unresolved latches. Unknown screens and unverified dialogs receive no guessed input. Loss OCR stops the controller without revival or currency spending; natural defeat recognition and alert delivery have limited live coverage. Combat settings, purchases and summoning are outside normal operation.

## Project layout

```text
NBR/
├── farm.py, manager_gui.py, nbr.ps1
├── Launch NBR Manager.cmd, Setup NBR Manager.cmd
├── requirements.txt, requirements-gui.txt
├── src/
│   ├── app/
│   │   ├── gui/          # Manager window, appearance, artwork and loot presentation
│   │   ├── backend/      # Controller adapter and durable loot collection
│   │   └── core/         # Process identity checks
│   ├── automation/       # Farming controller and BlueStacks/ADB integration
│   ├── game_library/     # Local reference library, browser and collection tools
│   ├── item_recognition/ # Offline recognition and reusable evidence utilities
│   └── *.py              # Compatibility entry points for existing imports/commands
├── assets/               # Stage catalogs, patterns, recognition templates and browser files
├── tests/                # Regression tests and saved-screen fixtures
├── scripts/              # Existing launchers, PowerShell and integration helpers
├── docs/                 # Current usage, validation history and design reference
├── runtime/              # Ignored state, logs, loot, local library and diagnostic evidence
└── .venv/                # Ignored local Python environment
```

## Offline verification and limits

From the root, with an installed environment:

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_*.py' -q
.\.venv\Scripts\python.exe -B -m unittest discover -s tests/game_library -p 'test_*.py' -q
.\.venv\Scripts\python.exe -B -m unittest discover -s tests/item_recognition -p 'test_*.py' -q
```

Tests use temporary state and mocked device operations; some integration tests read the local library, and one creates a disposable process. They do not establish fresh live farming cycles. See [validation scope](docs/VALIDATION.md), [cleanup details](docs/CLEANUP-1.25.md) and [repository data boundaries](docs/REPOSITORY.md).

Universal means shared verified screen handling without mission-specific setup; it does not establish recognition coverage for every game layout. Historical controlled checks verified the shared loop on All Scrolls [A], Getting Rich Quick! [B] and All Cups [S]. See [validation details](docs/VALIDATION.md) for reward uncertainty and remaining limits. Run resource-heavy test suites sequentially. Existing running processes retain their current code and target until explicitly stopped by the user. This cleanup leaves the existing acc1 session, game and BlueStacks untouched; no live farming validation is part of it.

Live recognition evidence covers a limited Practice Training reward subset. Loading the library does not validate every reward form or layout. Unknown, candidate, ambiguous and failed results remain explicit. Acquired quantities are frequently unknown. Fresh menu recovery, unverified dialogs, broader layouts and extended unattended operation retain documented limits. Local session histories and user annotations require separate backups. Review third-party asset redistribution rights and retained screenshot privacy before publication.
