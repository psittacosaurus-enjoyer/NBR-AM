# NBR Automation Manager

NBR is a local Windows controller and PySide6 desktop manager for Naruto Blazing Revival. It recognizes verified screens, handles mission results and replay, and records persistent session loot. Normal recognition uses local image matching; it makes no AI or cloud OCR calls.

## Requirements and installation

Use Windows, Python 3.13, PowerShell and BlueStacks. The supported deployment is the `acc1` display name / `Pie64` instance, ADB at `127.0.0.1:5555`, the game package `com.reblazing.nbrc`, and 900 × 1600 portrait screenshots. ADB is expected at `C:\Program Files\BlueStacks_nxt\HD-Adb.exe`; configuration is read from `C:\ProgramData\BlueStacks_nxt\bluestacks.conf`. The controller checks the instance mapping and Android identity before inputs and after reconnection. A generic multi-account setup is not supported.

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

Select a stage while stopped, prepare the game screen below, and press **Start**. The supervisor runs independently of the GUI. Opening or closing the manager leaves farming running; reopening restores status and elapsed time. Stage selection is locked while active. **Stop** requests an account-wide safe stop, persists that intention, and allows a bounded operation to finish. It leaves BlueStacks and the game open. Combat already underway may continue in the game.

| Stage | CLI identifier | Initial preparation |
| --- | --- | --- |
| All Scrolls [A] | `scrolls-a` | Open the selected mission's Start Mission screen. |
| Getting Rich Quick! [B] | `rich-b` | Open the selected mission's Start Mission screen. |
| Practice Training [Part 1] | `practice-part-1` | Manually begin the first battle. |
| Night of Tragedy [A] | `night-of-tragedy-a` | Manually begin the first battle. |

Practice and Night stages observe battle without combat input, then handle verified completion/results/Retry screens. A verified results screen can resume their replay; a fresh Start Mission screen waits for a manual first battle unless a Retry sequence is pending. Fresh menu navigation is incomplete for all stages.

PowerShell CLI, from the repository root:

```powershell
.\scripts\nbr.ps1 status
.\scripts\nbr.ps1 select -Stage rich-b
.\scripts\nbr.ps1 start
.\scripts\nbr.ps1 stop
```

Python CLI (also usable when PowerShell execution policy blocks scripts):

```powershell
.\.venv\Scripts\python.exe src/farm.py status
.\.venv\Scripts\python.exe src/farm.py select --stage practice-part-1
.\.venv\Scripts\python.exe src/farm.py start
.\.venv\Scripts\python.exe src/farm.py stop
```

`start --stage <id>` selects while stopped; a different active configuration is refused. Repeated Start does not create another worker. `replay --image <path> --stage <id>` classifies a saved image offline. `observe` captures from the device; `step` can send guarded input. These operational commands are unsuitable for offline maintenance checks.

## Status, loot and troubleshooting

Gray **STOPPED**, yellow **STARTING / STOPPING**, green **RUNNING**, and red **ERROR** describe controller processes. Connection availability appears separately: a running process can be waiting for BlueStacks. The activity log records controller events and manager errors.

Session Loot and Loot history show persisted runs, warnings and diagnostic screenshots. **Observed Icons** counts verified identities. **Confirmed Acquired** counts only explicitly verified acquired quantities. An icon never implies one acquired item; unavailable quantities remain **Unknown**, and partial totals are labeled **known only**. See [manager details](docs/MANAGER.md).

| Symptom | Check |
| --- | --- |
| Missing environment or PySide6 | Follow installation; use the setup launcher for GUI dependencies. |
| Controller disconnected | Restore acc1/Pie64 and its expected ADB mapping; inspect the activity log. |
| No input after selecting a stage | Prepare the matching mission; manually start battle for Practice/Night. |
| Unfamiliar screen or latched action | Stop and resolve the game manually; preserve persisted input latches. |
| Stamina wait | The ten-minute deadline survives restarts and repeats if stamina remains insufficient. |
| Loss stopped farming | Inspect local alert/evidence, resolve the game manually, then explicitly Start. |
| Tracking Unavailable | Check library and collector diagnostics; start a new worker after updating old code. |
| Recognition changes have no effect | A new worker loads the revised reference/calibration snapshot. |

Never reset runtime files to bypass identity checks, intentional stops or unresolved latches. Unknown screens and unverified dialogs receive no guessed input. Loss OCR stops the controller without revival or currency spending; natural defeat recognition and alert delivery have limited live coverage. Combat settings, purchases and summoning are outside normal operation.

## Project layout

```text
NBR/
├── README.md, requirements.txt, requirements-gui.txt
├── src/                  # Controller, GUI, loot collection, library and recognition code
├── assets/               # Patterns, reference images, recognition templates, browser files
├── config/        # Four stage definitions
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

Live recognition evidence covers a limited Practice Training reward subset. Loading the library does not validate every reward form or layout. Unknown, candidate, ambiguous and failed results remain explicit. Acquired quantities are frequently unknown. Fresh menu recovery, unverified dialogs, broader layouts and extended unattended operation retain documented limits. Local session histories and user annotations require separate backups. Review third-party asset redistribution rights and retained screenshot privacy before publication.
