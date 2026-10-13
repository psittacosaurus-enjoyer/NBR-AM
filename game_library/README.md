# NB:R Game Asset Library 1.0

The current manager consumes this library through offline loot recognition.
The integration plans near the end describe the original standalone delivery.
Downloaded assets, databases (including user annotations), exports and generated
reports remain local; a fresh clone needs `run.ps1 resume` with an explicit Python
environment before library use. See [repository maintenance](../docs/REPOSITORY.md)
for calibration and safe rebuild limits.

Standalone local GachaWiki reference library. All project data lives in this directory.
The farming controller, acc1, BlueStacks, ADB, APK, and existing PNG collections are not used or modified.

## Launch and browse

Double-click **Launch Library.cmd**, or run in PowerShell:

```powershell
& D:\NBR\game_library\run.ps1 browser
```

Open http://127.0.0.1:8765. The server binds only to loopback. Use `-Port 8766` if the default port is occupied.
Search names, original filenames, IDs, element, rarity, tags, notes, or supplied metadata; combine the filters.
Click a card to inspect images, all source metadata, local paths, source links, and related references.
The grid uses cached thumbnails and pages of 48, 96, or 120 records.

Edit names, categories, tags, notes, and verification status in the detail form, then Save.
Manual changes live in a separate SQLite annotations table and survive source updates and rebuilds.
**Source verified** means the value was explicitly supplied by GachaWiki, not tested in the game.
**Manually verified** is a user annotation. **Unidentified** uses a source filename and lacks a canonical label.
Source directory classifications and public-client helper labels have their basis recorded in metadata.
Unknown identifiers and fields remain null. Numeric client element codes are retained where no explicit named value is provided.
Character variants have separate records for their explicitly documented `cardNo`; this number is never extracted from image filenames.

## Maintain and export

```powershell
& D:\NBR\game_library\run.ps1 resume     # reuse successful downloads; retry missing/failed resources
& D:\NBR\game_library\run.ps1 update     # rediscover and conditionally revalidate using ETag/Last-Modified
& D:\NBR\game_library\run.ps1 build      # reconstruct source records/links/search/exports from cached data
& D:\NBR\game_library\run.ps1 export     # fresh JSON exports with current manual labels
& D:\NBR\game_library\run.ps1 test       # offline fixture tests; no game or network needed
& D:\NBR\game_library\run.ps1 validate   # complete local hash/provenance/export audit
```

The launch script uses Codex's bundled Python if available; otherwise specify `-PythonPath C:\path\to\python.exe`.
Python 3.10+ and Pillow (`requirements.txt`) are needed. The pre-existing `.venv` was left untouched.
Source rebuilding uses transactions; annotated records removed from the website are retained, with their images.
Original downloaded files are never removed automatically. An interrupted transaction rolls back safely.

The crawler uses four HTTP workers, an aggregate rate of four requests/second, persistent HTTPS connections,
bounded retries, rate-limit handling, and SQLite progress. Public structured catalogs are discovered from the
site's own modules. Recursive JSON image references and client-rendered asset paths are collected.
Account, authentication, profile, community, unrelated-game data, and external provenance sites are excluded.
There is no authentication, token use, or anti-bot bypass.

This source is a single-page application. Logical ninja/mission/navigation pages are enumerated from the
public router and catalogs; identical HTML shells are not fetched hundreds of times. Story chapters are
modal state within the Story page. Crawl reports distinguish logical pages from downloaded HTTP resources.
Do not interpret route counts as distinct server-rendered HTML pages.

## Storage and provenance

* `data/library.sqlite3`: master database, relationships, search index, persistent annotations.
* `data/crawl_state.sqlite3`: crawl progress and cached HTTP validators.
* `data/raw/`: original public structured catalogs and discovery modules.
* `data/manifest.json`: all resource URLs, statuses, hashes, local paths, discovery basis, and routes.
* `assets/{characters,items,stages,interface,miscellaneous}/`: original image bytes with SHA-256 filenames.
* `assets/thumbnails/`: cached previews, without altering originals.
* `catalogs/`: character/item/stage/interface/miscellaneous records and image exports.
* `reports/`: crawl, extraction, deduplication, perceptual similarity, validation, and completion evidence.

Multiple image URLs with identical bytes share one original/thumbnail, retaining every source URL.
Perceptual neighbors are review suggestions, not merged identities. Similar variants are not deleted.
GachaWiki's source metadata includes Fandom CC BY-SA 3.0 attribution and game-client provenance where supplied;
these are retained inside record metadata and raw source files. External source sites were not crawled.

## Read-only references for future automation

Run Python from this directory (or add this directory to your application's Python import path):

```python
from library import ReferenceLibrary

with ReferenceLibrary() as refs:
    results = refs.search(q='Awakening Scroll', record_type='item', rarity='4')
    record = refs.get(results['records'][0]['id'])
    for item in refs.item_references():
        print(item['display_name'], item['reference_paths'])
    # Only user-confirmed items:
    confirmed = list(refs.item_references(manually_verified_only=True))
```

Each item provides its full metadata, multiple images, explicit IDs where supplied, rarity, element, and paths
ready to load with `cv2.imread`. This interface opens SQLite in read-only mode and does not depend on the browser.
Uncertain/unidentified records are excluded from verified item references. No manual verification is invented.
HTTP alternatives: `/api/records`, `/api/records/{id}`, `/api/item-references`, `/api/stats`.

Manager 1.1 should consume the read-only API and test matching against representative saved results screenshots,
including small icons, overlays, scale and rarity/element distinctions. Website artwork is not evidence of
accurate screenshot recognition. No recognition integration into the active farming controller is included.
For website gaps, later import approved PNG references with source/provenance and explicit labels; existing
collections remain untouched in this project.
