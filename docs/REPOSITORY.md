# Repository and local data

Layout and entry points: [CLAUDE.md](../CLAUDE.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

- **Tracked:** `src/`, `assets/` (catalogs, templates, calibration), `tests/` (incl. screenshot fixtures), `scripts/`, `docs/`, root launchers.
- **Ignored, unique local data:** `runtime/` — account/device state, locks, `STOP`, input latches, sessions, `events.jsonl`, loot database/receipts/pending evidence, library downloads and annotations, diagnostics. Back up `runtime/loot/`, `runtime/library/` annotations and account mappings separately.
- `src/farm.py` must stay at that path: worker launch and process-ownership checks use it.
- `runtime/NBR-AM/` holds only redundant Git metadata (≈314 MiB, audited 2026-10-21); it can be deleted manually.
- Loot diagnostics are pruned automatically to a 1 GiB budget (journaled; protected evidence kept). Never clear `runtime/` wholesale to fix a problem — it holds latches and identity pins that protect against wrong-account input.
- `.gitattributes`: LF for Python/docs/config, CRLF for `.cmd`/`.ps1`, binary for images/databases. Before publishing, review `git status --short --untracked-files=all`, retained screenshots (privacy) and third-party asset redistribution rights.
- Audit retained loot evidence read-only with `scripts/verify_loot_evidence.py --evidence-dir <folder>` (see `TESTING.md`).
