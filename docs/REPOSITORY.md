# Repository and local data

Application source lives in `src/app/`, the farming controller and BlueStacks integration in `src/automation/`, and library/recognition packages alongside them in `src/`. Required assets belong in `assets/`, regression tests and saved fixtures in `tests/`, reusable Windows tools in `scripts/`, and documentation in `docs/`. Root Python, PowerShell and CMD launchers provide convenient entry points. Existing source-level entry points remain compatible.

`runtime/` holds local account/device state, locks, stop and input latches, sessions, logs, loot histories and pending evidence, downloaded library data, annotations, generated reports and diagnostic captures. These paths are deliberately unchanged so loaded controllers and persisted references remain valid. Preserve pending jobs and evidence when recovering failures; see [manager retention](MANAGER.md#limits-storage-and-upgrade-behavior).

`.gitignore` excludes runtime storage, local environments, bytecode, caches, process records, credentials and local configuration overrides. `.gitattributes` keeps Python/docs/config text in LF, Windows launchers in CRLF, and images/databases binary. Required catalogs, calibration, templates, ground truth and regression fixtures remain tracked. Ignore rules do not remove files already tracked: inspect `git ls-files` and `git status --short --untracked-files=all` before publishing.

`runtime/NBR-AM/` contains an independent pre-existing Git repository. It stays ignored and untouched to preserve its history and existing local changes. It is not application source. Do not use recursive cleanup commands against runtime storage.

User annotations, account mappings, session histories and pending loot are unique local data requiring separate backups. Downloaded library resources can be rebuilt, but upstream availability and database hashes can change; a rebuilt library may need reviewed recalibration. Retained screenshots and third-party game assets need appropriate privacy, provenance and redistribution review before publication.

For 1.25, the working branch is `codex/v1.25-cleanup`. The pre-cleanup snapshot is retained at `refs/cleanup-backups/v1.25-before` (`ebfdf94e38245934033b6e5eb11b44d568cb9743`), including the working changes present when cleanup began. The nested repository remains independent. See [the cleanup report](CLEANUP-1.25.md) for deferred removals and the external probe archive.

Run [the README's offline checks](../README.md#offline-verification-and-limits), review the complete diff, then stage/commit only intended source changes. The working branch has no new release commit and has not been pushed; the snapshot commit lives under the backup reference without rewriting existing history.
