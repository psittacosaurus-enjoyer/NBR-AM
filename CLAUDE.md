# NBR — guide for AI sessions

Unattended farming for *Naruto Blazing Revival* on one identity-verified BlueStacks instance over ADB (Windows, Python 3.13). Full design: `docs/ARCHITECTURE.md`. User guide: `README.md`.

## Hard invariants (never weaken)
- **Never spend Pearls.** Every action is allowlisted (`ALLOWED_ACTIONS`), catalog-declared `verified_safe`, checked by `automation/safety.py` (`require_pearl_safe_action`, `require_pearl_free_action_id`), and re-verified by a fresh observation at dispatch. Loss/revival screens → safety stop, never a click.
- **Never guess input.** UNKNOWN / LOADING / disagreeing frames never receive input. Recovery = re-observe, retry the *same* verified action, or relaunch the same verified activity (`am start`), nothing else.
- **Right device only.** `Device.verify()` (Android ID + BlueStacks mapping) runs before every input; identity mismatch writes `runtime/STOP`.
- **Persist before input.** `data["pending"]` is saved before dispatch so a crash can't replay a click.
- Don't reset `runtime/` files to bypass latches, stops or identity checks.

## Map
| Path | What |
| --- | --- |
| `src/automation/controller.py` | Everything for Universal farming: CLI (`main`), supervisor/worker lifecycle, locks, `Device` (ADB), `Detector` (template matching), `Engine.tick()` state machine, recovery ladder, loot hooks. Tests patch its globals (`farm.STOP`, `farm.RUNTIME`, `farm.time`…), so keep names stable. |
| `src/automation/ninja_road.py` (+`ninja_boss_swap.py`, `ninja_stage15.py`, `ninja_strategy_vision.py`) | Ninja Road engine (subclass with its own `tick`/recovery). |
| `src/automation/adb.py` | BlueStacks discovery, instance mapping, identity verification, ADB server. |
| `src/automation/safety.py` | Pearl-safety guards. |
| `src/app/backend/controller.py` | Manager ↔ controller process adapter (start/stop/status). |
| `src/app/backend/loot.py`, `retention.py` | Durable loot recording (SQLite + receipts), diagnostic retention. |
| `src/app/gui/` | PySide6 Manager (presentation only; reads controller state). |
| `src/item_recognition/`, `src/game_library/` | Reward recognition and local asset library (separate from farming). |
| `assets/patterns/*.json` | Stage catalogs: states → anchors (template, region, thresholds) → optional action. `universal.json` drives Universal. |
| `src/farm.py` | Worker/supervisor subprocess entry (path is part of process identity checks — don't move). |

Entry points: `manager_gui.py` (GUI), `farm.py start|stop|status|observe|replay --image` (CLI), `nbr.ps1`.

## Runtime flow
`farm.py start` → supervisor (`supervise`) → worker subprocess (`worker`) → `Engine.tick()` loop: identity check (throttled) → foreground check (throttled) → capture → classify twice (must agree) → transitions/run bookkeeping (`update`) → loot hooks → choose catalog action → persist pending → fresh re-observation guard → input. UNKNOWN/LOADING inside the transition grace window (`TRANSITION_GRACE_SECONDS`) is a normal wait; beyond it the bounded recovery ladder runs (`_recover_unknown`: 5/10/20 s, then activity relaunch, repeating).

State lives in `runtime/` (ignored): `supervisor.json`, `controller.json`, per-instance progress `universal.json` / `ninja_road.json` (runtime root for the original acc1 target, `runtime/instances/<hash>/` for others: pending, run, recovery, counts), `events.jsonl` (rotating JSON events), `STOP`.

## Commands (Windows, repo root)
```powershell
.\.venv\Scripts\python.exe -B scripts\test_offline.py --suite controller -q   # main regression suite
.\.venv\Scripts\python.exe -B scripts\test_offline.py --suite all -q          # + library, recognition
.\.venv\Scripts\python.exe -B scripts\test_offline.py test_transition_grace   # one module
.\.venv\Scripts\python.exe farm.py replay --image path\to\screen.png          # classify a saved screen offline
```
Tests use fixtures and fake devices; they never touch BlueStacks. Don't send live input to validate changes when fixtures can.

## Don't read / search
`runtime/` (state, logs, GBs of screenshots), `.venv/`, `tests/fixtures/` and `assets/**/*.png` (binary), `docs/images/`, `docs/VALIDATION.md` and `docs/CLEANUP-1.25.md` (historical logs). `.ignore` excludes these from ripgrep.

## Adding a screen/state
Crop a template from a saved 900×1600 screenshot into `assets/patterns/<name>/`, add the state to the catalog (≥2 anchors if it has an action; actions must be in `STATE_ACTIONS` and pass `safety.py`), then check every fixture still classifies the same (`replay`) and add a fixture test.
