# Game Asset Library integration verification

Verified on October 12, 2026 (America/Denver). No recognition algorithms, library records, runtime records, or farming processes were changed. No ADB or game interaction.

## Actual lookup path

`farm._loot_start` -> worker-owned `Collector` -> `loot_tracking._recognizer(D:\NBR)` -> `ItemRecognizer(library_path=D:\NBR\game_library)` -> dynamically loaded `game_library/library.py` -> `ReferenceLibrary(D:\NBR\game_library\data\library.sqlite3).item_references(manually_verified_only=False)`.

The Manager reads the resulting SQLite records through `ControllerBackend.loot_snapshot` -> `read_dashboard`; it does not load a separate recognition catalog. The library API opens its database with `mode=ro` and yields source/manual verified item records with available assets.

## Counts and exclusions

- Database: 248 item records. API: 247 item records with images. Ninja Gems (`item_99aa47028817d20edb51f646`) has no assets and is omitted by the API.
- Recognizer: all 185 eligible IDs have decoded images; 229 resolved, existing, successfully decoded image paths; 218 unique image contents; zero errors.
- The 62 exclusions all have hp, attack and combat cycle source metadata; their images are exclusively enemy/boss assets (58 enemy, four boss). No eligible/excluded overlap of names, asset IDs or image SHA256 was found. No evidence of a legitimate item reference being discarded by this combat filter.
- The library additionally contains 283 character records. The item API excludes every character record by record_type. Independent inspection decoded all 527 unique relevant character art/card/portrait paths, excluding ability icons. This is an item-only scope gap for character drops, not missing library infrastructure. The exact identity of the saved unknown portrait was not established.

## Provenance, calibration and caching

All loaded IDs and names match actual library records, including every top candidate in the traced screenshot. Every traced candidate image belongs to its record and was decoded. No hardcoded item-name bank or alternate incomplete catalog overrides the library.

The calibration lists 11 validated item IDs. The remaining 174 eligible IDs are still scored; they cannot become matched solely by exceeding similarity/margin thresholds. The library database SHA256 matches calibration and remained unchanged across the audit: `e850cd6b970e857b38da723c5f24b441de02c9da7e5d8c2f1aae0def5a5c0b10`.

Each Collector lazily loads references once and retains them in memory; no automatic library refresh exists. Fresh factory output reproduces the stored active-worker output exactly, so stale references are not indicated in the current failures. Later library edits would require a new recognizer instance to refresh its bank.

31 names map to multiple library IDs; 11 image-content groups belong to multiple IDs. The current matcher groups references by item ID, not semantic alias, so duplicate records/artwork can create zero or insufficient margins.

## Saved run trace

Completion: `session_a0e93489156c31069c2f8ee1_3414a274075f2147ee84ecd5`, run 7.

Screenshot: `D:\NBR\runtime\loot\diagnostics\20261013_023746_acc1_night-of-tragedy-a_session_a0e93489156c31069c2f8ee1_3414a274075f2147ee84ecd5_uncertain.png`.

The existing Manager factory called the actual library API (instrumented in this offline audit), then the recognizer detected the acquired grid at `[48, 402, 800, 735]` (x, y, width, height). Header score: 1.0. Visual review of the annotated output shows seven card-aligned crops, with condition rewards below the grid excluded. Result: three candidates, four ambiguous identities, zero verified IDs; all quantities unknown. Scores/IDs/names/candidates reproduce the persisted live-worker result exactly.

| Crop (x,y,w,h) | Library ID | Library name | Similarity | Reference image path |
|---|---|---|---:|---|
| [219, 411, 134, 139] | `item_cdfc7d04af581f2c4523368e` | Health Boost "Large Sphere of Endurance" | 0.857772 | `D:\NBR\game_library\assets\stages\e9c0d0a91187fd78a84a99f938a36c45345b00a1f0fa7d311347afb7da9c9e93.webp` |
| [219, 411, 134, 139] | `item_05a14bf65d718f395a1ba9b3` | Health Boost "Large Sphere of Endurance" | 0.825755 | `D:\NBR\game_library\assets\stages\de2544a9221ba1cf78d934a491e6a56c0765d860955eeeb861320c078bf284a9.webp` |
| [219, 411, 134, 139] | `item_f2616312e0532b2751841d9a` | Health Boost "Large Sphere of Endurance" | 0.825755 | `D:\NBR\game_library\assets\stages\de2544a9221ba1cf78d934a491e6a56c0765d860955eeeb861320c078bf284a9.webp` |
| [57, 411, 134, 139] | `item_04df2a7d248028326f275d5d` | Attack Boost "Large Sphere of Strength" | 0.838729 | `D:\NBR\game_library\assets\stages\9795d442405eb0dc40a09b0798666e16fab5fe556d4e61f03a9660247fd53e21.webp` |
| [57, 411, 134, 139] | `item_31ac7f4f725807dff03f5472` | Attack Boost "Large Sphere of Strength" | 0.832362 | `D:\NBR\game_library\assets\stages\0095533d5b22eeae908d3a1e6a3f063a9761b5dfb9130a012adc76e61bc122b5.webp` |
| [57, 411, 134, 139] | `item_8fc490fd1449b75acbd8d475` | Attack Boost "Large Sphere of Strength" | 0.832362 | `D:\NBR\game_library\assets\stages\0095533d5b22eeae908d3a1e6a3f063a9761b5dfb9130a012adc76e61bc122b5.webp` |
| [543, 411, 134, 139] | `item_31ac7f4f725807dff03f5472` | Attack Boost "Large Sphere of Strength" | 0.82522 | `D:\NBR\game_library\assets\stages\0095533d5b22eeae908d3a1e6a3f063a9761b5dfb9130a012adc76e61bc122b5.webp` |
| [543, 411, 134, 139] | `item_8fc490fd1449b75acbd8d475` | Attack Boost "Large Sphere of Strength" | 0.82522 | `D:\NBR\game_library\assets\stages\0095533d5b22eeae908d3a1e6a3f063a9761b5dfb9130a012adc76e61bc122b5.webp` |
| [543, 411, 134, 139] | `item_04df2a7d248028326f275d5d` | Attack Boost "Large Sphere of Strength" | 0.824908 | `D:\NBR\game_library\assets\stages\9795d442405eb0dc40a09b0798666e16fab5fe556d4e61f03a9660247fd53e21.webp` |

The Health Boost crop passes similarity 0.75 and margin 0.019265 (actual top margin 0.032017), but its ID is outside calibration and remains candidate. The first Attack Boost crop has margin 0.006367 and is ambiguous. At `[543,411,134,139]`, two distinct Attack Boost IDs share the exact reference image and tie at 0.825220: margin zero.

## Failure origin

- Current boost results: references exist and decode; blockers are calibration coverage and competing library identities. No missing boost references or integration errors were found.
- Crops: the traced grid aligns with cards and separates condition rewards; incorrect cropping is not evidenced as the primary blocker. This audit does not establish every stage/animation/crop variant as correct.
- Earlier five low-score unknowns in this session are visible five-star character portrait rewards, including Lucky Drop overlays. Item-only lookup excludes character references. Low scores (~0.571-0.594) are not evidence of missing boost assets. Exact character identity/library coverage remains unverified.
- Ninja Gems: an actual missing-image limitation, but it is not implicated in this traced run.
- Quantities: no explicit acquired-quantity evidence is displayed; icon identity does not establish acquired units.

At the audit snapshot, nine persisted runs were uncertain: 22 candidate, 25 ambiguous and five unknown icons, with zero verified identities. This demonstrates live background processing from durable events, while verified item recognition remains blocked as above.

## Artifacts and replay

`integration_audit.json` contains the complete top-five candidate trace, absolute image paths, image hashes, record provenance, API invocation, and before/after source hashes. `trace.debug.png` shows existing recognizer crop diagnostics.

```powershell
.\.venv\Scripts\python.exe -B development\loot_library_audit\verify_integration.py --completion-id session_a0e93489156c31069c2f8ee1_3414a274075f2147ee84ecd5
```
