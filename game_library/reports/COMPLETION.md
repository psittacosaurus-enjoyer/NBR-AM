# NB:R library completion — October 12, 2026

The authorized public Naruto Blazing GachaWiki collection and local library are implemented at `D:\NBR\game_library`.
No farming scripts, stage recognition files, BlueStacks, ADB, game processes, APKs, or existing PNG collections were modified or used.

| Measure | Result |
|---|---:|
| Relevant logical pages discovered / processed | 166 / 166 |
| Downloaded HTTP reference resources | 18 (12 JSON, 3 JavaScript, 2 CSS, 1 HTML shell) |
| Total library records | 1,648 |
| Character records | 283 |
| Item/material/currency records | 248 |
| Stage/mission/chapter/achievement records | 732 |
| Interface references | 127 |
| Other references | 258 |
| Downloaded image source URLs | 1,414 |
| Unique original image contents | 1,266 |
| Exact duplicate image URL relationships | 148 in 143 groups |
| Image URLs with cached thumbnails | 1,414 |
| Unidentified visual records | 137 |
| Source-verified records | 1,511 |
| Manually verified annotations | 0 (none invented) |
| Failed source image requests | 13 forthcoming banners |
| Extraction failures | 0 |
| Focused automated tests | 37 passed |
| Perceptual similarity pairs for review | 790 |

This site is a single-page application. The 166 logical pages comprise 124 ninja pages, 31 mission pages, and
11 reference navigation pages. They were processed from complete public catalogs and client route definitions,
using one HTML shell rather than redundant requests to identical shells. Story/Gaiden chapters are client modal
state, not separate web routes. HTTP resource counts and logical page counts are intentionally separate.

The 124 roster ninjas supply 247 explicitly numbered forms. Another 36 character records are supplied by drop
data. Stages include 31 mission groups and their difficulties, 25 Story/Gaiden chapter groups and their individual
missions, Ninja Road/maps, Ninja Trial tasks, and panel tasks. Every supplied field, including full abilities,
skills, statistics, rewards, battle maps, and supplemental tables, is retained with source-data URLs and pointers.

Item coverage includes awakening scrolls, element crystals, character crystals, Gama-chan rewards, ramen,
stat spheres, acquisition/awakening stones, and currencies. Variants are separate by explicit rarity and element.
Original unit IDs and numbered card forms are taken from fields, not arbitrary filenames. Exact byte duplicates
share canonical catalog paths; all original files already collected remain preserved. Perceptual similarities
are review suggestions and have not caused any merges or deletions.

The 13 failures are explicitly referenced `/naruto-blazing/upcoming/*.webp` banners. The server returns HTTP200
HTML instead of an image, so they are accurately recorded as unavailable. The source also explicitly provides
`icon: null` for Ninja Gems. It is cataloged without an image; the other 247 item records have references.
Some interface assets have a known source filename but no canonical name. They remain unidentified.
Public data does not establish complete coverage of every in-game screen/resource; forthcoming/unfinished modes
and results-screen references remain gaps. No screenshot matching accuracy is claimed.

Validation checked SQLite integrity and foreign keys, every downloaded image SHA-256 and thumbnail,
all 247 roster-form fields/portrait associations against the raw source, all 248 item icon associations,
all record provenance, and all five record export counts. No validation failure was found.
The browser passed desktop search, rarity/type filters, detail metadata and image selection, plus a 390px mobile
layout check with no horizontal page overflow. Manual editing was tested on isolated fixture databases;
the collected production data was not marked manually verified. A repeated offline build reported exactly
1,648 unchanged records, zero updates/new records/removals. Tests exercise failed-update cache retention,
raw source version preservation, missing-file recovery, bounded retries, pagination, resume, annotations,
read-only lookup, downloads/deduplication, extraction, malformed data, exports, and local HTTP browser APIs.

The standalone browser is available at http://127.0.0.1:8765. Launch command:

```powershell
& D:\NBR\game_library\run.ps1 browser
```

Database: `D:\NBR\game_library\data\library.sqlite3`.
Resume/update/export/test/audit commands and the Python read-only API are documented in `README.md`.
Use `ReferenceLibrary.item_references()` for source-confirmed item paths and
`item_references(manually_verified_only=True)` to require user verification.

For Manager 1.1, first evaluate these references against representative saved results screenshots and record
confidence/rarity/element mistakes, then add explicitly labeled screenshot icons for artwork mismatch or missing
references. Read-only library access is ready; integration into the active farming controller was deliberately
outside this standalone project.

Evidence: `crawl.json`, `build.json`, `validation.json`, `tests.txt`, `exact_duplicates.json`,
`perceptual_similarity.json`, `browser.png`, and `../data/manifest.json`.
