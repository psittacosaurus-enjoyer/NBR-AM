# NB:R item and character reward recognition

This module interprets results screenshots using the existing Game Asset Library. Manager 1.1 calls
the same API from its background loot collector. The recognition module does not control the game,
use ADB, start/stop farming, modify the library database, or maintain session counters.
CLI outputs remain inside this directory by default; Manager records and diagnostics live under
`D:\NBR\runtime\loot`. Original screenshots and reference images are unchanged.

## Run

Existing `D:\NBR\.venv\Scripts\python.exe` already provides NumPy, OpenCV and Pillow. It is reused with `-B`;
no packages or settings were changed. A separate compatible Python environment can be supplied with `-PythonPath`.

```powershell
& D:\NBR\item_recognition\run.ps1 analyze -InputPath D:\NBR\reference_images\starting_point.png
& D:\NBR\item_recognition\run.ps1 analyze -InputPath D:\NBR\development\rich_b -Recursive
& D:\NBR\item_recognition\run.ps1 test
& D:\NBR\item_recognition\run.ps1 validate
```

Equivalent Python commands, run from this directory:

```powershell
D:\NBR\.venv\Scripts\python.exe -B cli.py D:\NBR\reference_images\starting_point.png
D:\NBR\.venv\Scripts\python.exe -B cli.py D:\NBR\development\rich_b --recursive --output outputs\rich_b
D:\NBR\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
D:\NBR\.venv\Scripts\python.exe -B validate.py
```

Each input produces JSON and an annotated `.debug.png`; batch summaries and a processing manifest are saved
under `outputs/manual` by default. Identical screenshot bytes are processed once, including across resumptions;
use `--force` for deliberate reanalysis. This hash safeguard is not proof that two screenshots belong to the
same game run. Manager 1.1 handles run association and persistent accounting separately.

## Reusable API

```python
import sys
sys.path.insert(0, r"D:\NBR\item_recognition")
from recognizer import ItemRecognizer

recognizer = ItemRecognizer(library_path=r"D:\NBR\game_library")
result = recognizer.analyze_screenshot(
    screenshot_path=r"D:\NBR\reference_images\starting_point.png",
    debug_path=r"D:\NBR\item_recognition\outputs\example.debug.png",
)
```

No browser is required. The original library's `ReferenceLibrary.item_references()` is called read-only.
Only source/manual verified records are loaded. `reward_identity.character_references(source)` uses
the same library object's public `search(record_type='character', status=..., page=..., limit=120)`
and `get(record_id)` APIs. It selects card/portrait assets and supported portrait icon sources;
full art, ability icons, unavailable assets and unverified records are excluded. Stage records are not searched.
`include_characters=False` explicitly disables character lookup.
Library paths may be a root directory or SQLite file. Missing/corrupt references are recorded and skipped.
`reference_report()` gives separate item/character counts, exact library APIs, canonical groups,
transparency, content duplicates and errors.

The library API returns 247 source/manual-verified records with images. Source evidence identifies 62 of these
as combat enemies/characters despite their library item classification. The recognizer excludes those records
conservatively, leaving 185 eligible item records and 229 reference images (218 unique contents), all with
transparent pixels. This filter and its report leave the original database completely unchanged.
Character lookup adds 283 verified character records and 306 card/portrait reference images.
The combined bank decodes 535 images successfully, with zero reference errors; no library rebuild,
recrawl or duplicate catalog is involved.

`match_icon(PIL_image_or_RGB_array_or_path)` examines a supplied crop. `analyze_screenshot()` accepts optional
`reward_region=[x,y,w,h]`, `icon_boxes=[[x,y,w,h], ...]`, and `quantity_boxes={icon_index:[x,y,w,h]}` for offline
inspection of unsupported layouts. No screen recognition is claimed merely because explicit regions were supplied.
Without recognized layout anchors or explicit regions, the image returns `unsupported_screen`, not guesses.
Screenshots are loaded from one byte buffer so their SHA identifies precisely the analyzed input.

## Recognition and confidence

The validated 900x1600 layout is anchored by Mission Results, Acquired Reward/Special Reward Condition,
and inspected stage title images. An aspect-compatible scaled screenshot is normalized to this layout.
The acquired grid is isolated above Completion Reward Conditions. Straight card borders distinguish framed
icons from empty slots. Known sealed-package/chest templates are reported as unrevealed unknowns.
Other resolutions/layouts, animation phases, occlusion, and more than three reward rows require further evidence.

References preserve card frames, rarity and element details, and use transparency masks. Matching combines
masked color error and structural correlation. Alternative references for one identity are consolidated before
computing the best-versus-second-identity margin. Canonical aliases require an explicit shared game identifier
or persisted reviewed calibration evidence, with compatible names, element, rarity and source semantics.
Artwork or matching text alone never establishes an alias. Conflicting metadata prevents merging.
The canonical ID and name remain actual library records; `member_ids` and `reference_item_id` preserve
the alias members and strongest image's owner. Identical/similar competing identities are ambiguous.
Scores are similarities, not probabilities.

Automatic `calibration.json` is accepted only for the same library database revision. Passing similarity/margin criteria
within its validated item or character subset yields `identity_status: matched`; strong unvalidated identities remain
`candidate`. Weak evidence yields `unknown`; insufficient separation yields `ambiguous`. `calibration_path=None`
disables calibration. Explicit alternate calibration paths are supported. Version 2 supports reviewed
`canonical_groups`, separate `supported_item_ids` / `supported_character_ids`, and per-identity thresholds.
An item calibration gate never validates a character, and a validated alias never promotes unrelated records.
Current calibration keeps the original global threshold/margin (0.75 / 0.019265), adds four reviewed
boost identity groups and validates two character forms separately: Kushina Uzumaki "The Red-Hot Habanero"
(card 231) at threshold 0.78 / margin 0.04, and four-star Body Sakura Haruno "Dancing Cherry Blossoms"
(card 379) at threshold 0.70 / margin 0.055. Loading all 185 items and 283 characters does not validate
every identity. The Sakura limits accepted 10/10 inspected real positive crops across one training frame
and four held-out frames; testing the other 534 library images produced zero false acceptances as card 379.
Five-star card 380 remains a separate identity. The existing visual matching formula is unchanged.

Some source records share artwork under different labels. Such collisions are not merged or guessed.
Missing references, unvalidated identities and visually indistinguishable forms remain explicit limitations.

## Quantities

The inspected acquired grids contain no explicit quantity numerals. Rarity stars and Lucky Drop text are not
quantities. Repeated cards also do not establish a verified quantity per card. Acquired quantities therefore
remain null, and numerical totals remain empty. Item identity is preserved independently.

`quantity.py` implements conservative localized digit-template OCR from visually checked saved screenshot fields.
It reads explicit condition amounts and Ryo Earned fields, whose semantic scopes are kept separate from acquired drops.
It is also available for explicitly supplied quantity boxes. Failed/ambiguous readings stay unknown and cannot
enter totals. Existing evidence covers digits 0–7; 8/9, percentages, truncated glyphs and unsupported styles are rejected.
Templates, full evidence crops, source hashes and calibration live in `templates/quantity`.
Their six numeric source fields also supplied training glyphs: reproduction success is not held-out drop quantity accuracy.

## Results and evidence

`reports/ground_truth.json` contains visually labeled real screenshots and disjoint calibration/holdout assignments.
`reports/validation.json` gives identity, unknown/ambiguous, quantity, detection and end-to-end measures separately.
`outputs/validation` contains JSON and annotated images for each evaluated case. Green means calibrated identity,
orange means candidate/ambiguous, red means unknown; cyan bounds the acquired reward area. Quantity `?` means unknown.
`reports/resources.json` documents the read-only screenshot audit and contact sheets.
`reports/COMPLETION.md` records final results, gaps, and Manager readiness.

Synthetic/unit regressions verify code behavior, not real-world accuracy. Manager's collector tests separately
verify run association, replay rejection and persistent totals. Live recognition evidence and its measured
limits are recorded separately from unit-test results; quantities still require explicit acquired-drop evidence.

`D:\NBR\development\live_recognition\offline_validation.json` records reanalysis of real saved results:
nine held-out runs produced 51/51 correct verified icon identities. The legacy dataset's 48 acquired icons
produced 42 correct verified identities, one candidate and five unknowns; all six negative screens were
rejected. Original screenshots and library data remained unchanged, and acquired quantities remained unknown.
These saved-screen measurements are distinct from fresh farming-cycle validation.

Four fresh acc1 **Practice Training [Part 1]** production farming cycles were independently reviewed after
the final calibration reload. All 16 acquired icons matched the actual library identities, with zero unknown
or ambiguous rewards. All 16 quantities remained null/unknown, and Completion Reward Conditions were excluded.
The inspected cards were Sakura card 379, two-star Bravery Health Boost and two-star Wisdom Attack Boost.
All Scrolls [A] and Getting Rich Quick! [B] have saved-screen and regression coverage; they were not freshly
farmed during this validation. Other reward forms, layouts and acquired quantities require further evidence.

The worker keeps one recognizer/reference/calibration snapshot per collector. Changes take effect in a new
worker; reopening the Manager only rereads persisted history. The bounded four-frame development capture
marker is now disabled. Unknown or failed production recognition still retains diagnostics normally.

The full offline regression suite passed 213 tests (161 Manager/controller/control and 52 recognizer),
with zero failures. Fresh-cycle evidence, independent review, contact sheet and capture records are linked
from [LIVE_VALIDATION.md](../development/live_recognition/LIVE_VALIDATION.md).
