# NBR Automation Manager

NBR Automation Manager 1.1 is a local Windows desktop interface and deterministic
farming controller for **Naruto Blazing Revival**. It controls the existing
BlueStacks **acc1 / Pie64** instance and provides stage selection, process status,
activity logs, and persistent session loot history. Normal farming and recognition
use local code and image matching; they make no AI or cloud OCR calls.

The GUI adapts the existing `farm.py` controller. Opening or closing it leaves
farming running independently. Existing command-line launchers remain available.

## Capabilities

- Select one of four configured stages while the controller is stopped.
- Start an independent supervisor/worker and request an account-wide safe stop.
- Recover process status and elapsed time when reopening the manager; display ADB
  connection availability separately from process status.
- Recognize verified screens, persist action latches, and wait on unknown screens.
- Decline the verified stamina prompt and wait ten minutes for natural regeneration.
- Stop persistently on detected loss without selecting revival or spending currency.
- Record verified completion runs and browse session history, reward identities,
  warnings, and retained diagnostic screenshots.

**Observed Icons** and **Confirmed Acquired** are separate loot counters. A
recognized icon does not establish an acquired quantity: unavailable quantities
remain **Unknown**, and partial sums are labeled **known only**. Reference coverage
and calibration limits are described in [MANAGER.md](MANAGER.md).

## Environment and prerequisites

The controller is Windows-specific. The existing environment uses Python 3.13,
PowerShell, and BlueStacks with these mappings:

| Setting | Expected value |
| --- | --- |
| BlueStacks instance/display name | `Pie64` / `acc1` |
| ADB serial | `127.0.0.1:5555` |
| ADB executable | `C:\Program Files\BlueStacks_nxt\HD-Adb.exe` |
| BlueStacks configuration | `C:\ProgramData\BlueStacks_nxt\bluestacks.conf` |
| Game package | `com.reblazing.nbrc` |
| Reference screenshot dimensions | 900 × 1600, portrait |

The controller verifies the instance mapping and Android identity against the
local BlueStacks configuration before inputs and after reconnection. These are
existing fixed deployment assumptions, not a configurable multi-account setup.
Keep Windows awake and the intended instance available. No sign-in or reboot task
is installed; farming must be started manually after Windows restarts.

## Installation

On a **fresh checkout with no active farmer**, open PowerShell in the repository
root and create the environment:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
```

The farming requirements pin NumPy, Pillow, and headless OpenCV. The separate GUI
requirements pin PySide6. `Setup NBR Manager.cmd` installs only the GUI requirements
into an existing `.venv`; it does not create the environment or install farming
requirements. Internet access is needed for package installation.

**Preserve an environment used by a running farmer.** Do not recreate or upgrade
its `.venv`, overwrite runtime state, or move files that its processes use.

Loot recognition reads the local Game Asset Library under `game_library/data/`.
Downloaded library data and local annotations are kept out of source control. For
a new checkout, follow [the library setup and collection instructions](game_library/README.md)
to obtain/build that data; its launcher accepts `-PythonPath` for an explicit
Python environment. For example, on an idle fresh installation:

```powershell
.\game_library\run.ps1 resume -PythonPath "$PWD\.venv\Scripts\python.exe"
```

The recognizer automatically applies existing calibration only when its library
database SHA-256 matches. A rebuilt database may differ and require offline
validation and reviewed recalibration; results can remain unknown or candidates.
Farming controls do not require successful loot recognition:
collection failures are reported while farming continues. Recognition calibration
and templates live under `item_recognition/` and remain part of the source.

## Launch the GUI

Double-click **Launch NBR Manager.cmd**, or run from the repository root:

```powershell
.\.venv\Scripts\python.exe manager_gui.py
```

The launcher uses `.venv\Scripts\pythonw.exe`; it requires no PowerShell
execution-policy change. Opening the manager discovers an existing controller.
Stage selection is locked while it is active.

## Stage selection and execution

| Display name | CLI identifier | Preparation |
| --- | --- | --- |
| All Scrolls [A] | `scrolls-a` | Manually prepare the selected mission's Start Mission screen. |
| Getting Rich Quick! [B] | `rich-b` | Manually prepare the selected mission's Start Mission screen. |
| Practice Training [Part 1] | `practice-part-1` | Manually begin the first battle, then press Start. |
| Night of Tragedy [A] | `night-of-tragedy-a` | Manually begin the first battle, then press Start. |

Select a stage while stopped. The choice is saved by the existing controller.
A verified mission title is required before ordinary battle/results handling can
proceed after a switch. Fresh menu navigation is incomplete; do the preparation
above instead of relying on automatic mission entry.

Practice Training and Night of Tragedy use the manual-first-battle replay mode:
they observe the battle without combat inputs, then handle recognized completion,
results, and Retry screens. They do not navigate mission menus or reopen the game.
A verified results screen can resume the replay; a fresh Start Mission screen still
waits for the first manual battle unless it belongs to a pending Retry sequence.

## Existing CLI

Run these commands from the repository root when you intend to operate the farmer:

```powershell
.\nbr.ps1 status
.\nbr.ps1 select -Stage rich-b
.\nbr.ps1 start
.\nbr.ps1 stop
```

Select only while stopped. `start -Stage rich-b` also selects that stage while
stopped; a different active configuration is refused. The same stage identifiers
work with the Python CLI, which avoids PowerShell script execution-policy issues:

```powershell
.\.venv\Scripts\python.exe farm.py status
.\.venv\Scripts\python.exe farm.py select --stage practice-part-1
.\.venv\Scripts\python.exe farm.py start
.\.venv\Scripts\python.exe farm.py stop
```

These examples are operational commands, not offline validation commands.
`farm.py replay --image <path> --stage <id>` classifies a saved image offline.
`observe` captures from the connected device, and `step` can perform a guarded
input; neither belongs in a non-invasive repository maintenance check.

## Start, Stop, and status

**Start** launches a hidden supervisor independent of the manager and launching
shell. Repeated starts do not create another worker. The manager waits for observed
process status rather than treating command completion as proof of a running worker.
The controller retains its guarded ADB connection/retry behavior.

**Stop** requests the existing account-wide stop, regardless of stage selection.
It persists the intentional stop, blocks further inputs and supervisor restarts,
and allows a bounded in-progress operation to finish. It leaves BlueStacks and
the game open; automatic combat already underway may continue in the game.
A later explicit Start resumes from the observed screen while preserving pending
action latches and stamina deadlines.

The GUI shows gray **STOPPED**, yellow **STARTING / STOPPING**, green **RUNNING**,
and red **ERROR**. These describe controller processes. A running process may still
be waiting for BlueStacks; connection availability is shown separately. Closing
the GUI does not stop farming or loot collection.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| GUI environment missing | Create `.venv` only on an idle/fresh installation; see Installation. |
| GUI cannot import PySide6 | Run `Setup NBR Manager.cmd` when it is safe to install GUI dependencies. |
| Controller disconnected | Restore the expected acc1/Pie64 instance and ADB mapping; inspect the activity log. |
| No input after a stage switch | Prepare the matching mission screen; manually begin the first battle for the two replay stages. |
| Worker waits on an unfamiliar screen | Resolve it manually after stopping; do not delete the persisted input latch. |
| Stamina wait appears stalled | The ten-minute deadline survives restarts and repeats if stamina is still insufficient. |
| Loss stopped farming | Inspect the local alert/evidence, fix the game manually, then explicitly Start. |
| Tracking Unavailable | Check collector diagnostics and library availability. Older, uninstrumented workers cannot supply loot evidence; enable tracking with a new worker at a planned stop/start. |
| Updated recognition has no effect | Collectors hold a reference snapshot; a new worker is needed to load reference/calibration changes. |

Do not reset runtime files to bypass identity checks, intentional stops, or unresolved
input latches. See [MANAGER.md](MANAGER.md) for detailed process and loot behavior.

## Project layout

The flat application entry points are intentional: launchers, imports, and active
processes rely on these paths. Supporting modules already use separate directories.

```text
NBR/
├── farm.py                     # Controller, supervisor, screen/action state machine
├── nbr.ps1                     # Existing PowerShell CLI wrapper
├── manager_gui.py              # PySide6 desktop interface
├── manager_backend.py          # Adapter to existing controller commands
├── loot_tracking.py            # Durable completion events and session loot collector
├── loss_notify.py, loss_ocr.ps1 # Local Windows loss detection/notification helpers
├── Launch NBR Manager.cmd      # GUI launcher
├── Setup NBR Manager.cmd       # GUI dependency installer
├── requirements*.txt           # Separate farming and GUI dependency pins
├── stages/                     # Four stage configurations
├── patterns/                   # Required catalogs and recognition images
├── reference_images/           # Existing reference assets; preserve provenance
├── game_library/               # Library tools, API, catalogs, tests, local data
├── item_recognition/           # Offline recognizer, templates, calibration, tests
├── test_*.py                   # Isolated controller, manager, and collector tests
├── development/                # Utilities, saved evidence, and historical snapshots
├── docs/                       # Repository maintenance and publishing guidance
├── *.md                        # Usage and historical verification notes
├── runtime/                    # Local process state, logs, loot DB, diagnostic evidence
└── .venv/                      # Local installed environment
```

Runtime data, downloaded caches, process records, credentials, and the virtual
environment stay local. Required stage catalogs, pattern/reference assets, source,
dependency pins, and tests belong in source control. Do not delete uncertain
reference files or historical evidence as part of routine cleanup.

## Offline validation and technical documentation

The suites mainly use temporary state and mocked device/controller operations.
Run the full commands below on an idle checkout: they also include a disposable
process test and integrations that read the installed library database. They are
not the guarded subset used for cleanup beside an active farmer.

```powershell
.\.venv\Scripts\python.exe -B -m unittest test_manager test_loot_gui test_loot_tracking test_live_control test_safety test_stages test_practice test_night test_connection_recovery test_completion_recognition test_stamina_timer -q
.\.venv\Scripts\python.exe -B -m unittest discover -s item_recognition/tests -q
```

Do not substitute live Start/Stop, ADB, `observe`, or `step` for these checks while
maintaining a repository with active farming. Historical validation results are
recorded separately from the outcome of any current offline test run:

- [Repository maintenance](docs/REPOSITORY.md): source-control boundaries, cleanup decisions, and publishing commands.
- [MANAGER.md](MANAGER.md): GUI operation, loot semantics, collector persistence, and recognition limits.
- [MANAGER_VALIDATION.md](MANAGER_VALIDATION.md): original manager and Practice Training validation history.
- [VALIDATION.md](VALIDATION.md): original controller verification and unresolved navigation/dialog cases.
- [SECOND_MISSION.md](SECOND_MISSION.md): Getting Rich Quick! [B] configuration and evidence.
- [Item recognition README](item_recognition/README.md): offline analysis API and recognition calibration.
- [Game Asset Library README](game_library/README.md): library collection, provenance, browsing, and rebuilds.
- [Live recognition validation](development/live_recognition/LIVE_VALIDATION.md): previously reviewed production captures and their limits.

## Current limitations

- Only the fixed Windows acc1/Pie64 deployment is supported. There is no general
  account manager, scheduler, or portable controller setup.
- Fresh menu navigation is incomplete. Unknown screens, inventory limits, and
  unverified popup variants receive no guessed input. Pending inputs can remain
  latched when their expected transition is not recognized.
- Battle input, combat settings, purchases, summoning, and premium-currency
  recovery are outside normal controller operation. Failure OCR only stops the
  controller; unfamiliar or unreadable loss dialogs may remain observation-only.
- Historical live recognition evidence covers a limited Practice Training reward
  subset. Other stages have saved-screen/offline coverage where documented; the
  presence of a configuration or passing unit test does not prove fresh live cycles.
- Loading the full library does not verify every reward form or layout. Candidate,
  ambiguous, unknown, and failed recognition results remain explicit. Acquired
  quantities require separate evidence and are frequently unknown.
- Local annotations, session histories, and captured evidence are not reconstructed
  by installing Python packages. Keep local backups and review third-party asset
  provenance and redistribution permissions before publishing reference assets.

