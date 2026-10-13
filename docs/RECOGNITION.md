# Reward recognition

`src/item_recognition/` analyzes saved reward screenshots through the read-only Game Asset Library. The manager uses the same API in a background collector. This module does not control the game, use ADB, alter library data or maintain session counters.

From the repository root:

```powershell
.\scripts\recognition.ps1 analyze -InputPath "$PWD\assets\reference_images\starting_point.png" -PythonPath "$PWD\.venv\Scripts\python.exe"
.\scripts\recognition.ps1 test -PythonPath "$PWD\.venv\Scripts\python.exe"
.\scripts\recognition.ps1 validate -PythonPath "$PWD\.venv\Scripts\python.exe"
```

Analysis accepts files or directories, with `-Recursive` for nested images. Outputs default to ignored `runtime/item_recognition/manual/`: per-image JSON, annotated debug PNG, summaries and a hash manifest. Identical screenshot bytes are processed once across resumptions; `--force` deliberately reanalyzes them. Content deduplication does not establish whether two images belong to the same game run; the collector handles durable run association separately.

## API and references

With `src` on PYTHONPATH:

```python
from item_recognition.recognizer import ItemRecognizer

recognizer = ItemRecognizer()
result = recognizer.analyze_screenshot(
    screenshot_path='assets/reference_images/starting_point.png',
    debug_path='runtime/item_recognition/manual/example.debug.png',
)
```

`library_path` accepts a library root or SQLite database. `match_icon` accepts a PIL image, RGB array or path. `analyze_screenshot` optionally accepts `reward_region=[x,y,w,h]`, `icon_boxes=[[x,y,w,h], ...]`, and `quantity_boxes={icon_index:[x,y,w,h]}` for explicit offline inspection. Supplying boxes does not prove automatic layout recognition. Without recognized anchors or explicit regions, results are unsupported rather than guessed.

Items come from `ReferenceLibrary.item_references()`. Character references use public paged `search(record_type='character', status=...)` and `get(record_id)` calls, selecting eligible card/portrait assets. Full art, abilities, unavailable assets and unverified records are excluded. Missing/corrupt references are reported and skipped. `reference_report()` exposes counts, API provenance, duplicates, canonical groups and errors.

## Confidence and quantities

The inspected 900 × 1600 layout uses Mission Results, reward labels and stage-title anchors; compatible scaled images are normalized. The acquired grid excludes Completion Reward Conditions. Card borders identify occupied slots; sealed chests remain unrevealed unknowns. Other layouts, animation phases, occlusion and more than three acquired rows need further evidence.

Matching retains rarity/element/frame details and uses masked color error plus structural correlation. Scores are similarities, not probabilities. Alternate images consolidate per identity before margin calculation. Aliases require explicit compatible game IDs or reviewed calibration, preserving canonical library IDs, member IDs and winning-image provenance. Conflicting metadata blocks merging.

Automatic `assets/item_recognition/calibration.json` applies only to its exact library database revision. Calibrated supported identities can be matched; strong unvalidated forms remain candidates, weak evidence is unknown and competing identities are ambiguous. `calibration_path=None` disables calibration. Separate item/character gates and per-identity thresholds prevent blanket promotion.

An icon, rarity star, repeated card or Lucky Drop label never proves acquired quantity. Unknown quantities remain null. The localized quantity reader keeps condition/Ryo observations separate from acquired drops and requires explicit verified evidence for totals. Existing digit templates cover 0–7; unsupported 8/9, percentages, truncated glyphs and styles are rejected. Training-field reproduction is not held-out acquired-quantity accuracy.

Templates and quantity evidence live in `assets/item_recognition/`; labeled calibration/holdout cases are `ground_truth.json` there. Generated validation reports live in `runtime/item_recognition/reports/`. The validate tool performs calibration/evaluation and writes reports; review intended changes before running it. See [historical evidence and limits](VALIDATION.md). Each collector snapshots references/calibration; start a new worker after reviewed changes.
