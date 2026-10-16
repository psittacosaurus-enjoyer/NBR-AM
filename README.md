# NBR Automation Manager 1.23

NBR is a local Windows controller and PySide6 desktop manager for Naruto Blazing Revival. It recognizes verified screens, handles mission results and replay, and records persistent session loot. Normal recognition uses local image matching; it makes no AI or cloud OCR calls.

## Requirements and installation

Use Windows, Python 3.13, PowerShell and BlueStacks. The manager discovers local BlueStacks configuration and installation paths, including custom instance names. Open **Instance**, select your instance and apply it while BlueStacks is running. The controller verifies its configuration mapping, ADB endpoint and Android identity before inputs and after reconnection. Only one instance can farm at a time. The game package remains `com.reblazing.nbrc`, with 900 × 1600 portrait screenshots.

From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
```

The core requirements pin NumPy, Pillow and headless OpenCV; GUI requirements pin PySide6. Installation needs internet access. `scripts/Setup NBR Manager.cmd` creates `.venv` when missing and installs both dependency lists. Keep Windows awake and start BlueStacks manually after a restart.

Loot recognition additionally needs the local Game Asset Library. See [library setup](docs/LIBRARY.md). Downloaded references and user annotations stay in ignored `runtime/library/`. Installing packages alone does not reconstruct that data. Calibration applies automatically only when the library database SHA-256 matches; a rebuilt library needs reviewed offline validation before calibrated recognition can be trusted. Farming continues when loot collection fails.

## Launch and control

Double-click `scripts/Launch NBR Manager.cmd`, or run:

```powershell
.\.venv\Scripts\python.exe src/manager_gui.py
```

Open **Instance** to refresh detected installations and choose the desired instance. A single unambiguous instance is selected automatically; multiple instances require a choice unless a saved selection remains valid. **Apply Selection** verifies and saves the target for subsequent launches. Open a mission's **Start Mission** screen in that instance, return to **Dashboard** and press **Start**. Universal handles verified Start, completion/results, rewards, Retry and natural stamina waiting. The game handles combat. Unfamiliar screens wait safely for manual resolution.

The sidebar contains **Dashboard**, **Instance** and **Settings**. Instance selection is locked while any farming process is active or its state cannot be confirmed. The Dashboard continues to show that process's pinned instance. Stopping safely permits another selection; closing the manager continues to leave farming running independently.

**Manual Setup** accepts an exact friendly name or internal identifier. If automatic resolution is unavailable, its advanced option accepts an explicit local ADB endpoint. Targets must pass identity verification; ambiguous names, missing identities and mismatches are rejected. Discovery and Refresh never start, stop or modify BlueStacks, and never connect a disconnected ADB transport. Open BlueStacks and enable its Android Debug Bridge setting if verification is unavailable.

The supervisor runs independently of the GUI. Closing the manager leaves farming running; reopening restores status and elapsed time. **Stop** requests an account-wide safe stop and allows a bounded operation to finish, leaving BlueStacks and the game open.

The default appearance is Original; **Settings → Appearance** also offers Retro 98. **Settings → Activity log** and **Settings → Recognition details** show optional diagnostics. Both start hidden and remember their settings.

PowerShell CLI, from the repository root:

```powershell
.\scripts\nbr.ps1 status
.\scripts\nbr.ps1 start
.\scripts\nbr.ps1 stop
```

Python CLI (also usable when PowerShell execution policy blocks scripts):

```powershell
.\.venv\Scripts\python.exe src/farm.py status
.\.venv\Scripts\python.exe src/farm.py start
.\.venv\Scripts\python.exe src/farm.py stop
```

Repeated Start does not create another worker. `replay --image <path>` classifies a saved image offline. `observe` captures from the device; `step` can send guarded input. These operational commands are unsuitable for offline maintenance checks.

## Status, loot and troubleshooting

Gray **STOPPED**, yellow **STARTING / STOPPING**, green **RUNNING**, and red **ERROR** describe controller processes. Connection availability appears separately: a running process can be waiting for BlueStacks. Enable **Activity log** in Settings to inspect controller events and manager errors.

Loot is always visible as library item icons with quantity overlays. A number is a verified acquired total; `?` means unknown and `+` marks a partial total. Recognition details adds observed/completed/failed counts, historical sessions, warnings and diagnostic screenshots. Observed icons never imply acquired quantities. See [manager details](docs/MANAGER.md).

| Symptom | Check |
| --- | --- |
| Missing environment or PySide6 | Follow installation; use the setup launcher for GUI dependencies. |
| Controller disconnected | Open the selected BlueStacks instance, check its ADB setting and refresh Instance. Identity or mapping changes require a fresh verified selection after farming stops. |
| No input after Start | Prepare a supported Start Mission screen; enable Activity log in Settings to inspect unrecognized screens. |
| Unfamiliar screen or latched action | Stop and resolve the game manually; preserve persisted input latches. |
| Stamina wait | The ten-minute deadline survives restarts and repeats if stamina remains insufficient. |
| Loss stopped farming | Inspect local alert/evidence, resolve the game manually, then explicitly Start. |
| Tracking Unavailable | Check library and collector diagnostics; start a new worker after updating old code. |
| Recognition changes have no effect | Current collectors reload database/calibration revisions before new reward jobs. Restart a worker running older Python code once to install this behavior. |

Never reset runtime files to bypass identity checks, intentional stops or unresolved latches. Unknown screens and unverified dialogs receive no guessed input. Loss OCR stops the controller without revival or currency spending; natural defeat recognition and alert delivery have limited live coverage. Combat settings, purchases and summoning are outside normal operation.

## Project layout

```text
NBR/
├── README.md, requirements.txt, requirements-gui.txt
├── src/                  # Controller, GUI, loot collection, library and recognition code
├── assets/               # Patterns, reference images, recognition templates, browser files
├── tests/                # Isolated tests and required saved-screen fixtures
├── scripts/              # Windows launchers, CLI and maintenance tools
├── docs/                 # Behavior, library/recognition, validation and publication guidance
├── runtime/              # Ignored local state, histories, caches and diagnostic evidence
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

Tests use temporary state and mocked device operations; some integration tests read the local library, and one creates a disposable process. They do not establish fresh live farming cycles. See [validation scope](docs/VALIDATION.md) for historical evidence and [repository review](docs/REPOSITORY.md) for publication boundaries and commands.

Universal means shared verified screen handling without mission-specific setup; it does not establish recognition coverage for every game layout. Controlled live checks verified the shared loop on All Scrolls [A], Getting Rich Quick! [B] and the previously unconfigured All Cups [S]. See [validation details](docs/VALIDATION.md) for reward uncertainty and remaining limits. Run resource-heavy tests sequentially with farming stopped. Reopen an existing Manager window to load the 1.23 interface. Existing running processes retain their current code and target until explicitly stopped by the user.

Live recognition evidence covers a limited Practice Training reward subset. Loading the library does not validate every reward form or layout. Unknown, candidate, ambiguous and failed results remain explicit. Acquired quantities are frequently unknown. Fresh menu recovery, unverified dialogs, broader layouts and extended unattended operation retain documented limits. Local session histories and user annotations require separate backups. Review third-party asset redistribution rights and retained screenshot privacy before publication.
