# Game Asset Library

The manager's recognizer consumes the library read-only. Collection and browsing are separate tools; they do not control BlueStacks or the game. Install the root Python requirements first. From the repository root:

```powershell
.\scripts\library.ps1 resume -PythonPath "$PWD\.venv\Scripts\python.exe"
.\scripts\library.ps1 browser -PythonPath "$PWD\.venv\Scripts\python.exe"
```

Open `http://127.0.0.1:8765`; the server binds only to loopback. Pass `-Port 8766` if occupied. Search/filter records, inspect images and provenance, and edit names, categories, tags, notes or verification status. Source verified means supplied by the source website, not verified in the game. Manually verified is a user annotation; unknown fields remain unknown.

## Maintenance and storage

The same launcher accepts `update` (rediscover/revalidate downloads), `build` (rebuild from cached resources), `export` (export current labels), `test` (offline tests) and `validate` (local hash/provenance/export audit). Supply the explicit PythonPath shown above for reproducible environment selection. Resume/build depend on upstream availability and are not part of offline repository checks.

All local data lives under ignored `runtime/library/`:

- `data/library.sqlite3`: records, relationships, search and persistent user annotations.
- `data/crawl_state.sqlite3`, `data/raw/`, `data/manifest.json`: crawl state, public source resources and provenance.
- `assets/`: downloaded original images and cached thumbnails.
- `catalogs/`, `reports/`: exports and generated audit reports.

Annotations survive source rebuilds; annotated source-removed records are retained. Original downloaded files are not automatically deleted. Transactions roll back on interruption. Back up databases with SQLite-aware tooling or while closed, including any live WAL state; packages cannot reconstruct user annotations.

The crawler discovers public structured catalogs and routes, uses four HTTP workers, an aggregate four requests per second, bounded retries, cached validators and persistent progress. It excludes accounts/authentication/community/unrelated-game resources. A single-page site's logical route counts are separate from fetched HTTP resource counts. Exact duplicate image bytes share originals while retaining all source URLs; perceptual neighbors are review suggestions, not merged identities. Review supplied Fandom/game-client attribution and redistribution rights before publishing downloaded assets.

## Read-only API

With `src` on PYTHONPATH:

```python
from game_library.library import ReferenceLibrary

with ReferenceLibrary() as refs:
    results = refs.search(q='Awakening Scroll', record_type='item', rarity='4')
    record = refs.get(results['records'][0]['id'])
    items = list(refs.item_references())
    confirmed = list(refs.item_references(manually_verified_only=True))
```

References contain actual IDs/names, metadata, and existing image paths. Uncertain/unidentified records are excluded from verified item references. SQLite opens read-only; the browser is unnecessary. HTTP alternatives are `/api/records`, `/api/records/{id}`, `/api/item-references` and `/api/stats`.

## Fandom materials (Library 1.1)

The existing database also includes materials from the [Limit Break](https://naruto-blazing.fandom.com/wiki/Limit_Break) and [Encyclopedia](https://naruto-blazing.fandom.com/wiki/Encyclopedia) pages and their associated material pages. Collection uses the public MediaWiki API and original wiki images. It does not recrawl GachaWiki or interact with the game.

```powershell
.\scripts\library.ps1 fandom-crawl
.\scripts\library.ps1 fandom-import
```

Collection resumes from `runtime/library/data/fandom/`; import is transactional and repeatable. SQLite-aware backups are saved in `runtime/library/backups/` before schema migration. Original IDs, annotations and source relationships survive. Confirmed duplicate identities have a canonical ID and readable historical alias IDs; search and `item_references()` return one logical identity. Names, identifiers and conflicting facts remain available separately for each source in `sources` and in the browser detail pane. Unresolved source discrepancies are excluded from verified references.

SHA-256 duplicates reuse existing assets. Perceptual similarity produces review suggestions, never proof of identity. Comparable card images are ranked by native pixel area and lossless encoding, with the higher-fidelity image preferred. Useful older variants remain available. Full artwork is displayed in the browser and returned in `artwork_assets`, separately from recognizer icon paths. Cached Fandom originals and imported originals share physical storage through hard links where supported.

Importing references does not calibrate them. The offline compatibility check in `game_library.recognition_compat` compares the expanded library with the pre-import snapshot on saved reward images before updating the calibration database hash. Supported item IDs and recognition thresholds stay unchanged; new items remain uncalibrated. Results and remaining review cases are recorded in `runtime/library/reports/FANDOM_1_1.md` and the adjacent JSON reports.

Library coverage and screenshot matching accuracy are different. The recognizer filters combat-enemy records from items and selects eligible character card/portrait references separately. See [recognition](RECOGNITION.md) and [historical validation](VALIDATION.md).
