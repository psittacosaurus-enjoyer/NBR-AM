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

The expanded local library supplies 363 eligible item identities and 570 decoded item variants, including Fandom images, after 62 combat records are excluded. Character matching retains its separate 283 identities and 306 card/portrait variants. The public API unions source aliases and prioritizes higher-fidelity assets; the recognizer scores every eligible variant and computes margins between canonical identities. Explicit library aliases also retain historical calibration membership. Identical artwork alone does not merge identities.

Reviewed alternate item names can share a canonical group only with persisted evidence and an agreed explicit source card number; conflicting known type, element, rarity, game ID or category still blocks merging. Body Crystal's mission-reward and limit-break records are grouped this way as card 949. Member-specific calibration gates survive alias consolidation, using stricter limits when they conflict. Persisted screenshot diagnostics identify the effective library/calibration snapshot and loaded reference counts.

## Confidence and quantities

The inspected 900 × 1600 layout uses Mission Results and reward-label anchors; compatible scaled images are normalized. The acquired grid excludes Completion Reward Conditions. Card borders identify occupied slots; sealed chests remain unrevealed unknowns. Other layouts, animation phases, occlusion and more than three acquired rows need further evidence.

Matching retains rarity/element/frame details and uses masked color error plus structural correlation. Scores are similarities, not probabilities. Alternate images consolidate per identity before margin calculation. Aliases require explicit compatible game IDs or reviewed calibration, preserving canonical library IDs, member IDs and winning-image provenance. Conflicting metadata blocks merging.

Automatic `assets/item_recognition/calibration.json` applies only to its exact library database revision. Calibrated supported identities can be matched; strong unvalidated forms remain candidates, weak evidence is unknown and competing identities are ambiguous. `calibration_path=None` disables calibration. Separate item/character gates and per-identity thresholds prevent blanket promotion. The reviewed subset currently contains 17 item IDs and two character IDs. Additions use immutable independently labeled screenshots at unchanged thresholds; evidence consumed for calibration is excluded from new-identity holdout claims.

An icon, rarity star, repeated card or Lucky Drop label never proves acquired quantity. Unknown quantities remain null. The localized quantity reader keeps condition/Ryo observations separate from acquired drops and requires explicit verified evidence for totals. Existing digit templates cover 0–7; unsupported 8/9, percentages, truncated glyphs and styles are rejected. Training-field reproduction is not held-out acquired-quantity accuracy.

`reviewed_rewards.json` adds independently reviewed source-card aliases to an exact library and base-calibration fingerprint. The Heart Crystal card 947 review consolidates its mission and limit-break records before computing the competing-identity margin and adds that canonical identity to the supported subset. Existing thresholds, member-specific limits, quantity rules and unrelated identity gates remain unchanged. Invalid or stale reviews cannot promote identities. Collector versions containing this change also watch the supplemental file between jobs; existing Python workers need a future restart to load the code.

For passive diagnosis, run `python src/item_recognition/passive_viewer.py` and open `http://127.0.0.1:8766`. It reads existing loot records and retained screenshots with a read-only SQLite connection. The displayed capture can be older than the current battle; its age is shown. It does not capture screenshots or access ADB.

`src/game_library/deduplicate.py` prepares an isolated library database with physically consolidated item records, remapped provenance/relationships and existing fidelity-ranked references. It refuses to overwrite the source or write inside the active library. Review its report and remap calibration to the new database fingerprint before future activation; replacing a live library can trigger a collector reload. Different rarity/element forms remain distinct even when names coincide.

Templates and quantity evidence live in `assets/item_recognition/`; labeled calibration/holdout cases are `ground_truth.json` there. Generated validation reports live in `runtime/item_recognition/reports/`. Routine validation evaluates the existing reviewed calibration and writes reports without replacing it; only an absent calibration triggers initial calibration. The explicit calibrate API remains available for deliberate recalibration. See [historical evidence and limits](VALIDATION.md). Each recognizer instance snapshots references/calibration. The Manager collector checks database/WAL and calibration revisions before new jobs and atomically replaces changed snapshots without restarting farming. A worker running older Python code needs a restart to receive this behavior. Existing persisted results are not reprocessed.
