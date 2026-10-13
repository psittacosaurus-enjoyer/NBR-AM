# NBR Automation Manager 1.0 — validation

## Practice Training [Part 1] extension — October 11, 2026

Stage `practice-part-1` uses the existing acc1 / Pie64 controller, account locks,
ADB identity checks, screenshots, result actions, pending-action latches,
stamina waiting, logs, and safe stopping. The dropdown includes the
new stage; CLI `--stage` and `nbr.ps1` accept its identifier. The first battle is
started manually; no menu navigation or automatic game launch is enabled.

**96 isolated tests passed:** 62 existing farming regression tests (28.940 seconds)
and 34 practice/manager tests (6.840 seconds). New checks include initial-battle
observation without input, two simulated completion/results/Retry cycles, wrong
title and dim-control rejection, stamina waiting, excluded menus/game launch,
account ownership, and real offscreen Qt dropdown/Start/Stop/elapsed-time behavior.
Devices, OCR text, launch commands, and manager controller operations are fake;
all mutable test runtime state uses temporary directories.

The initial implementation was validated offline only. The follow-up live repair
below supersedes its OCR title adapter and prior live-validation limitations.

### Live repair and validation

After the user manually stopped the stalled Practice farmer, controller status
confirmed no active processes. The current game screen was Practice Training
results with Next. Logs showed the previous OCR adapter failing with JSON control
character errors; a saved-image probe also misread the title as Part II.

Captured `patterns/practice_part_1/results_title.png` and `start_title.png` now
guard the existing shared result/start controls through normal template matching.
No routine title OCR is invoked. `dialog_title.png` uses the 0.6077 dimming measured
in both existing stage dialog templates; modal operation is tested offline but has
not yet occurred during a live Practice run. Loss OCR remains the existing safety
mechanism. A title-verified results screen can resume an already finished manual
battle. A fresh Start Mission screen without a pending Retry still waits for the
user's manually started first battle.

The existing manager backend/controller completed two consecutive live battles,
63.37 seconds between completions. Each completed the full completion, Next,
reward reveal, Retry, Start Mission sequence. No game inputs were sent by a
second farmer. Live Stop exited the controller and reported no active processes;
the game was left open. A Windows atomic-state replacement sharing conflict seen
during Start is handled with bounded PermissionError retries while preserving
the previous file on exhaustion.

Final isolated regressions include captured live screens, zero routine title OCR,
other mission rejection, modal guards, persisted Retry resume, Stop/ownership,
and transient/exhausted atomic-state replacement conflicts. Existing stages keep
their original catalogs and controller behavior.

**Final full suite: 105 tests passed in 37.696 seconds.** Practice Training was
resumed through the existing manager backend with the final repaired code after
Stop validation; the controller reported running with verified active processes.

## Original manager validation

Completed October 11, 2026, using the existing Windows Python 3.13 environment
and PySide6 6.12.0. No farming Start, Stop, select, restart, or game-input commands
were issued during this implementation or its validation.

## Automated verification

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s D:\NBR -p 'test_*.py'
```

**86 tests passed in 31.048 seconds:** 62 existing farming regressions and 24 new
manager checks (10 backend adapter tests, 14 real Qt offscreen GUI tests).

The new tests cover supported stage names, command arguments, account-wide stop
delegation, duplicate-click suppression, stage persistence, process-state recovery,
elapsed time, state colors, unexpected exits, connection errors, log rotation and
partial lines, bounded log scrolling, responsive background work, and closing
without a stop request. Regression cases also cover stale poll results, command
completion before fresh verification, and continued log updates during status errors.

Qt widgets and event processing are real. Farming commands, ADB operations, and
process probes in unit tests are mocked; runtime fixtures use temporary directories.
These tests do not establish that a live farming Start/Stop cycle succeeded.

## Read-only integration and launch verification

- The real backend read existing controller status and verified acc1's existing
  BlueStacks mapping and Android identity without connecting or sending game input.
- Two separate real Qt window opens/closes recovered the active Getting Rich
  Quick! [B] run, process IDs, elapsed time, stage lock, connection, and activity
  log. A test backend override explicitly rejected any live control command.
- `Launch NBR Manager.cmd` opened the native Windows desktop window, whose
  process reported **Responding = True**. Closing only that test GUI left farming
  active. The launcher uses the project-local `pythonw.exe`.
- Native Windows layout was rendered and visually inspected: text and controls
  are readable, with no clipping at the default size. The saved
  `development/manager_native_preview.png` uses a fake running-state fixture.
  Windows Qt's offscreen plugin has no system font families here; test previews
  loaded fonts explicitly. Native Windows rendering uses the installed fonts normally.
- Both Python `farm.py status` and PowerShell `nbr.ps1 status` succeeded.
  The PowerShell check used an execution-policy override only for that process.
- `pip check` reported no broken requirements.

The current run at resumed-session inspection had supervisor PID **11652**, worker
launcher PID **39576**, and worker PID **122424**. Those same processes remained
active after validation, with no stop requested and selection still `rich-b`.
The previous session observed an earlier run; development did not change either run.

SHA-256 hashes of `farm.py`, `nbr.ps1`, and `requirements.txt` match their original
inspection values. Existing farming logic, patterns, stage files, and dependencies
were preserved. Only Qt GUI dependencies were added to `.venv`.

## Remaining verification limits

Live farming Start/Stop and a real stage change were deliberately not exercised
because acc1 must remain undisturbed. GUI wiring is tested with isolated fixtures;
the existing controller remains responsible for detached launch, duplicate locks,
safe stop, and device identity validation.

The existing farmer still requires the user to prepare the selected mission's
Start Mission screen after changing stages. That existing navigation limitation is
shown in the GUI and usage guide; v1.0 does not modify the farming logic.
