# NBR Automation Manager 1.1

See [README.md](README.md) for current setup and all four configured stages,
including Night of Tragedy [A]. This document retains detailed manager behavior
and historical deployment notes; older stage lists describe the original scope.

Double-click **Launch NBR Manager.cmd** in `D:\NBR`. The application uses the
project's `.venv` and PySide6. GUI dependencies are already installed on this
machine. To restore them, double-click **Setup NBR Manager.cmd** (internet needed).
Setup installs only `requirements-gui.txt`; it does not update farming dependencies.

## Use

- Opening or closing the manager leaves farming running independently. Reopening
  discovers the existing acc1 run and disables stage selection while it is active.
- When stopped, choose **All Scrolls [A]**, **Getting Rich Quick! [B]**, or
  **Practice Training [Part 1]**. The choice
  is saved by the existing controller. Prepare the chosen mission's **Start Mission**
  screen manually after switching stages; existing fresh-menu navigation is incomplete.
- For **Practice Training [Part 1]**, manually start the first battle before pressing
  manager **Start**. The existing controller waits without battle input, then uses
  its results/Retry replay sequence continuously until **Stop**. This stage does
  not navigate mission menus or reopen the game. Subsequent replay confirmation
  uses the existing Start Mission macro if Retry returns to that screen.
  Its identifier is `practice-part-1`; progress is `runtime/practice_part_1.json`.
  Shared result/button templates are reused with captured Practice Training title
  templates checked on two fresh observations. Routine detection needs no OCR.
  Starting from verified Practice Training results safely resumes the replay;
  starting from a fresh Start Mission screen still waits for your manual battle.
  Unknown screens or mismatched titles receive no input.
- Open BlueStacks acc1 and press **Start**. The manager starts the existing
  controller with the chosen stage and waits for verified process status. If ADB
  is not yet connected, the controller performs its existing guarded connection
  and retry procedure; the manager displays connection availability separately.
- Press **Stop** to request the existing controller's safe account-wide stop. A
  bounded device operation may take time to finish. Stop leaves BlueStacks and the
  game open; automatic combat already in progress may continue in the game.
- The activity log shows timestamped controller events and manager errors.

Gray **STOPPED**, yellow **STARTING / STOPPING**, green **RUNNING**, and red
**ERROR** reflect process observations. Connection availability is shown separately;
running processes can remain alive while the controller waits for BlueStacks.
Elapsed time is recovered from the active supervisor's process creation time when
available, with the existing controller's recorded start time as a fallback.

If disconnected, restore BlueStacks **acc1 / Pie64**, with its original ADB mapping
`127.0.0.1:5555`. The manager checks the existing Android identity without sending
game input. The farmer retains its own identity validation before every action.

## Session loot

The **Session Loot** card updates automatically from persisted records. Expand
**Loot history** for item totals, individual runs, warnings and retained screenshot
links. Select a historical session or use **Older runs / Newer runs** to inspect
history in pages of 100 runs. These reads run independently of the farming controls.

- **Observed Icons** counts verified item or character identities in acquired reward grids.
  Candidate, ambiguous and unknown identities stay separate and visible per run.
- **Confirmed Acquired** counts only explicitly verified acquired quantities.
  One icon never defaults to one acquired item. Unknown quantities remain **Unknown**;
  mixed totals are labeled **known only**. Ryo/condition summary fields are excluded.
- Tracking begins with a new instrumented farming worker. The session belongs to
  its supervisor's PID plus creation time (or the direct worker's identity), and
  survives worker reconnects and closing/reopening the Manager. A new farming
  process gets fresh totals; historical sessions remain available.
- Process start and tracking start are distinct timestamps. Normal lifecycle exit
  marks the session ended; a later collector/Manager observation reconciles abnormal
  exits and PID reuse. A stale collector heartbeat shows **Tracking Unavailable**.

The farmer records only newly verified `MISSION_COMPLETE` runs using its existing
run UUID. At the existing `READY_TO_RETRY` observation it saves the already captured
results frame; there are no extra device polls or screenshot requests. Encoding,
saving evidence and small durable event writes occur in the observation path;
reference loading and recognition run in a worker-owned background thread through
the existing `item_recognition` API and read-only `game_library` API. Closing the
Manager does not stop collection. No extra farming supervisor is created.

Records live in `runtime/loot/loot.sqlite3` (SQLite WAL). Completion IDs combine the
durable process session and run UUID; unique constraints prevent replay, screenshot
reprocessing or reopening from increasing totals. Run sequences are assigned in
the durable completion event before recognition, independent of processing order.
The replayable event/evidence spool is `runtime/loot/pending`. Recognition is bounded
to eight pending jobs, with a bounded memory cache. Overload is recorded as a failure
with preserved evidence. Persistence failures keep disk events/evidence, retry up to
three times per collector, and report `runtime/loot/persistence_error.json` until
recovery. Farming continues even if collection fails.

Unknown/candidate/ambiguous icons, incomplete parsing and recognition failures retain
screenshots in `runtime/loot/diagnostics`, named with UTC timestamp, account, stage,
completion ID and category. Temporary copies are removed only after the corresponding
structured record commits. Fully identified grids with unknown quantities do not
retain screenshots solely because quantities are unavailable. Existing screenshots,
logs and reference records are never deleted by loot tracking.

Workers started before loot instrumentation cannot supply associated acquired-results
evidence and show **Tracking Unavailable**. Start an updated worker to enable tracking;
historical counters, logs and screenshots are not backfilled. Recognizers keep a
reference snapshot per collector, so updated recognition/calibration takes effect with
a new worker. Existing session history and diagnostic evidence remain preserved.

Both All Scrolls [A] (`scrolls-a`) and Getting Rich Quick! [B] (`rich-b`) use the same
recognizer and library. Existing other stages and controls remain available; unsupported
reward layouts produce diagnostic failures instead of guessed rewards. Current
recognizer calibration covers a validated item/character subset and the inspected
results layout. Acquired quantities remain unknown without explicit evidence.

The primary reference source remains `D:\NBR\game_library\data\library.sqlite3`.
Item lookup calls `ReferenceLibrary.item_references()` read-only. Character lookup
uses that library's public paged `search(record_type='character', status=...)` and
`get(record_id)` APIs, selecting verified card/portrait assets instead of ability/full-art
images. Names and canonical IDs come from actual library records; matching keeps the
strongest image owner's ID and alias member IDs for traceability. Canonical merging
requires shared game identifiers or reviewed calibration evidence and compatible
semantic metadata. Unvalidated identities remain candidates, and conflicting records
remain ambiguous. Separate item/character calibration gates prevent blanket promotion.
The current bank loads all 185 eligible item records (229 images) and 283 character
records (306 card/portrait images): 535 decoded references, zero decoding errors.

## Controller compatibility

`manager_gui.py` supplies the desktop interface. `manager_backend.py` adapts the
existing `farm.py` commands and rotating activity log. `test_manager.py` contains
isolated manager checks. The launch/setup scripts and `requirements-gui.txt` handle
GUI installation and launching. `farm.py`, `nbr.ps1`, and stage configuration
include Practice Training; existing pattern files and farming dependencies are unchanged.

The original CLI remains available, including `nbr.ps1 status` and
`.venv\Scripts\python.exe farm.py status`. The GUI calls Python directly, so its
launcher needs no PowerShell execution-policy change. There is no additional
farming supervisor or account lock system.

If restoring the entire project on a fresh machine, install Python 3.13 and use:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
```

Do this only for a missing environment; keep an active farmer's environment intact.
PySide6 installation is described in the [official Qt guide](https://doc.qt.io/qtforpython-6/gettingstarted.html).

## Validation

Run isolated offline suites with:

```powershell
.\.venv\Scripts\python.exe -B -m unittest test_manager test_loot_gui test_loot_tracking test_live_control test_safety test_stages test_practice test_night test_connection_recovery test_completion_recognition test_stamina_timer -q
.\.venv\Scripts\python.exe -B -m unittest discover -s item_recognition/tests -q
```

Tests cover durable replay/deduplication, identical images from different runs,
quantity semantics, unknown/ambiguous rewards, recognition/persistence failures,
retention, bounded memory under database outage, both stage hooks, real saved-screen
API integration, GUI updates/history pages, and a disposable process lifecycle.
These unit tests do not run ADB or send live Start/Stop commands. The recognition
upgrade's offline regression run passed 161 Manager/collector/controller/control tests and
52 recognizer tests (213 total, zero failures). Fifteen new recognizer tests cover conservative
alias grouping, conflicting metadata, character reference filtering and library
provenance, calibration gates and unknown quantity semantics. Saved-source and library
database hashes were checked unchanged by the real recognizer integration test.
Eight development-control tests verify atomic owned-Stop restart guards, ordinary Start
compatibility, bounded validation capture limits/path ownership, durable history and
normal production screenshot cleanup.
An active project-local capture marker is bounded to its requested run count, and an
expired marker does not enable capture. Per-identity limits affect only their target;
unsupported identities stay candidates, invalid limits are rejected, and the actual
five-star Sakura card 380 reference is not accepted as four-star card 379.

Reanalysis of nine held-out real saved runs verified 51/51 icon identities. The legacy
dataset's 48 acquired icons yielded 42 correct verified identities, one candidate and
five unknowns; six negative screens yielded zero accepted rewards. Quantities stayed
unknown. Detailed evidence is in `development/live_recognition/offline_validation.json`;
saved-screen performance remains separate from fresh farming-cycle results.

Fresh live validation completed four independently reviewed production farming cycles
on acc1 **Practice Training [Part 1]** after the final calibration reload. All 16
acquired icons matched the correct library records; none were unknown or ambiguous.
All 16 quantities remained **Unknown**, and Completion Reward Conditions were excluded.
The inspected rewards were four-star Body Sakura card 379, two-star Bravery Health
Boost and two-star Wisdom Attack Boost. The Manager reads their persisted observed
icon counts separately from confirmed acquired quantities. Detailed evidence is in
[LIVE_VALIDATION.md](development/live_recognition/LIVE_VALIDATION.md), with independent
review and four original production capture PNG/JSON pairs.

This validates those observed forms and the inspected Practice Training layout.
All Scrolls [A] and Getting Rich Quick! [B] were covered by saved-screen recognition
and offline regressions; neither was freshly farmed in this validation. The complete
reference bank is loaded, but calibration still covers a limited identity subset.
Other forms, layouts, animations and acquired quantities remain unverified. Global
matching limits stayed unchanged; Sakura card 379 has its own 0.70 similarity / 0.055
margin gate, with 10/10 inspected positives and zero false acceptances among the other
534 library images. Five-star Sakura card 380 remains separate.

Reference and calibration changes require a new farming worker; closing/reopening
the Manager rereads records without refreshing the worker's recognizer. Existing
history remains preserved. Development capture was bounded to four frames, and its
project-local marker is now disabled. Normal diagnostic retention remains active.

Isolated tests use temporary state and fake controller/device operations; they do
not prove a live farming start/stop cycle. Initial Manager validation is recorded in
`MANAGER_VALIDATION.md`; subsequent live recognition evidence is recorded separately.
Live development control is restricted to verified acc1, ordinary farming and known
screens. Summoning, purchases and Ninja Pearl spending are forbidden; unexpected or
premium-currency screens receive no guessed input. A user Stop request ends development
inputs and is never counteracted by validation automation.
