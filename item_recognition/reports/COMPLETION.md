# Offline item recognition completion — October 12, 2026

The working module, quantity reader, CLI, calibration, diagnostics, manual ground truth and tests are complete
under `D:\NBR\item_recognition`. The interruption was resumed from the existing implementation and outputs.
No ADB, BlueStacks, live-game inputs, farming processes, Manager code, stage configuration, farming dependencies,
or library records/database were modified. Existing Python packages were reused with bytecode writes disabled.

## Created files

* `recognizer.py`: read-only reference loading, alpha-masked icon matching, anchored result-phase/layout detection,
  acquired-grid extraction, unknown/ambiguous handling, scoped results and annotated diagnostics.
* `quantity.py`: local digit-template OCR, explicit-ROI reading, separate quantity confidence/status and provenance.
* `cli.py`: one-file/directory analysis, JSON/debug output, persistent content-hash duplicate-processing safeguards.
* `validate.py`: calibration on labeled training screens, separate real holdout evaluation and immutability checks.
* `prepare_profiles.py`, `resource_audit.py`: reproducible offline profiles and saved-resource inspection.
* `calibration.json`, `templates/profiles.json`, quantity template/evidence/calibration files.
* `tests/test_recognizer.py`, `tests/test_quantity.py`, `tests/test_cli.py`: 37 isolated regressions.
* `run.ps1`, `requirements.txt`, `README.md`: usage, dependencies and reusable Python interface.
* `reports/ground_truth.json`, `resources.json`, `validation.json`, `tests.txt`, contact sheets and this report.
* `outputs/validation`: eleven per-screenshot JSON/debug pairs. `outputs/cli_smoke`: working single-input output
  and processing manifest; a second analysis correctly returned duplicate without credit.

## Reference inventory and approach

The existing library API returns 247 source/manual-verified records with images, using 293 image paths.
The source metadata exposes 62 combat enemies/characters within the library's item classification. Those records
are excluded conservatively at recognition time, without editing the database. The usable recognition bank is
185 item records, 229 reference images and 218 unique image contents. All loaded images have transparent pixels.
Missing/corrupt references are reported; unrelated character and stage records are not matched.

The method combines transparency-masked color error and structural correlation, retaining rarity and element
information. It consolidates multiple references per item before comparing the best and competing identities.
Low evidence is unknown, near-equal competing identities are ambiguous, strong unvalidated identities are
candidates, and calibrated supported identities are matched. Similarity scores are not probabilities.
The current calibration covers eleven specific item identities and is tied to the unchanged source database SHA.

The inspected 900x1600 Mission Results layout is validated using saved image anchors. The upper acquired area
is isolated from Completion Reward Conditions, and card frames distinguish icons from empty slots. Known sealed
packages/chests remain unrevealed unknowns. Special-condition/summary screens produce separately scoped numeric
observations and never credit acquired items. Aspect-compatible scaling is implemented; broader scales/layouts,
animations and more than three acquired rows require further real-image validation.

## Real saved-screen evaluation

Five fully revealed priority acquired screenshots contain 48 visually labeled icons. Two were reserved for
calibration; three disjoint screenshots were held out. Five more actual results screens exercise sealed rewards
and special-condition summaries, and a battle screenshot is a negative control: 10 actual results screenshots,
11 images analyzed in total. The wider read-only inventory inspected 392 images and reviewed 210 unique full
screens; these inventory counts are not recognition accuracy denominators.

| Screenshot | Split | Icons | Verified correct | Candidate | Unknown |
|---|---|---:|---:|---:|---:|
| `reference_images/starting_point.png` | Calibration | 10 | 10 | 0 | 0 |
| `reference_images/starting_point_lucky_drop.png` | Holdout | 11 | 10 | 1 | 0 |
| `development/retry_live.png` | Holdout | 11 | 11 | 0 | 0 |
| `development/rich_b/after_stamina_no.png` | Calibration | 7 | 5 | 0 | 2 |
| `development/rich_b/ready_to_retry.png` | Holdout | 9 | 6 | 0 | 3 |

Across all 48 icons: 42 correctly verified identities, zero incorrectly accepted identities, one uncalibrated
candidate, five unknowns, zero ambiguous matches, zero missed icons and zero extra icon detections in revealed
grids. All five unknown icons are four-star gold-bar Gama-chan images with no named library reference.
The candidate is a Skill three-star awakening scroll with the correct top-ranked source ID but no calibration
example, so the system deliberately does not mark it verified. Synthetic tests separately demonstrate ambiguity
when distinct item IDs share artwork; no real ambiguity case occurred in this small sample.

Held out: 31 icons, of which 28 have supported library labels. The recognizer verifies 27/28 supported icons
(96.4% identification coverage); one remains a candidate. The other three are missing-reference unknowns.
Accepted-identity precision is 27/27 (100%) on these three saved holdout screens; this is a small scoped result,
not a general-game guarantee. No matched or candidate item identity was emitted on the six negative controls.
Twenty-six unopened tiles were reported as unknown; their package colors were not treated as item identities.

## Quantities and end-to-end limits

The acquired icons show rarity stars, element markers and sometimes Lucky Drop text, but no explicit quantity
numerals. All 48 acquired quantities remain null, quantity status unknown, and numerical totals empty.
Drop quantity accuracy is not measurable; zero end-to-end rewards have a verified identity AND quantity.
No implicit quantity of one is used, and repeated cards are not converted into numerical loot totals.

Localized OCR successfully reproduces six explicitly labeled real condition/summary amounts:
All Scrolls condition amounts 20000 and 25000, Ryo Earned 6000; Rich B condition amounts 30000 and 500,
Ryo Earned 100000. These fields supplied the glyph templates and therefore constitute a six-of-six reproduction
check, not a held-out quantity accuracy claim. Condition amounts and Ryo Earned stay separate from acquired-drop
accounting. Quantity identity/confidence is independent of item identity; failed readings retain any valid item ID.
Digits 0–7 have actual glyph evidence; digits 8/9, truncated glyphs, percentages and unsupported font styles are
rejected conservatively. Quantity-free or semantically wrong regions are not inferred from arbitrary screen text.

## Tests and diagnostics

37 software tests passed. They cover library/reference loading, wrong enemy classification exclusion, image
loading, unknown/ambiguous matches, transparency, calibration scope, missing/corrupt images, invalid bounds,
quantity success/failure, quantity gating of totals, read-only database hashes, deterministic results, original
image preservation, persistent duplicate processing, forced reanalysis and retrying failed inputs.
These synthetic/fixture tests are separate from the real-screen measurements above.

Diagnostics were visually checked: cyan bounds cover acquired rewards and exclude lower conditions; green
marks calibrated identities, orange candidate/ambiguous identities, and red unknowns. Two-line labels are placed
above each icon, preserving element badges and rarity stars; `qty ?` is explicit. Summary numeric boxes are
annotated with their scopes. All tested originals and the source database passed before/after SHA checks.

Examples: `outputs/validation/00-starting_point.debug.png` shows correctly identified scrolls;
`04-ready_to_retry.debug.png` shows six correctly identified frogs and three safely unknown gold-bar frogs;
`01-starting_point_lucky_drop.json` preserves the uncalibrated Skill three-star candidate;
`06-results_next.debug.png` distinguishes condition amounts from Ryo Earned.

## Usage and Manager 1.1 readiness

```powershell
& D:\NBR\item_recognition\run.ps1 analyze -InputPath D:\NBR\reference_images\starting_point.png
& D:\NBR\item_recognition\run.ps1 analyze -InputPath D:\NBR\development\rich_b -Recursive
& D:\NBR\item_recognition\run.ps1 test
& D:\NBR\item_recognition\run.ps1 validate
```

```python
from recognizer import ItemRecognizer
recognizer = ItemRecognizer(library_path=r"D:\NBR\game_library")
result = recognizer.analyze_screenshot(screenshot_path=r"D:\NBR\reference_images\starting_point.png")
```

The reusable offline interpretation API is ready for further Manager development, independently of the browser.
Automatic live loot credit is not ready: add an independently labeled Skill three-star calibration example,
provide named four-star frog references through a separately authorized library update, establish acquired
quantity semantics, and expand held-out evidence. Manager must later associate screenshots with runs and persist
credit safeguards; a screenshot content hash alone is not a run ID. No active-controller integration was performed.
