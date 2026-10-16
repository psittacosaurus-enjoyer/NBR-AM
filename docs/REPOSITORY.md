# Repository review and publication

Source lives in `src/`, required recognition/browser assets in `assets/`, tests and saved regression fixtures in `tests/`, Windows/maintenance tools in `scripts/`, and documentation in `docs/`. Runtime storage is separate from source.

## Local data and Git boundaries

`runtime/` retains device/account state, stop/latch/session records, loot histories, diagnostic evidence, downloaded library data, annotations, generated exports and recognition output. `.venv/`, Python caches, process records, secrets and local configuration overrides are ignored. Required recognition catalogs, calibration, templates, labeled ground truth and saved test images remain eligible for source control.

`runtime/NBR-AM/` is an independent pre-existing Git repository. It stays ignored locally to preserve its Git history; it is not an application source directory. The root repository's history is preserved. No commit, remote creation or push is part of this cleanup.

`.gitattributes` normalizes text with LF for Python/docs/config and CRLF for PowerShell/CMD launchers, and marks images/databases binary. `.gitignore` never substitutes for reviewing already tracked files. Check the prospective file list and staged diff before publication.

Downloaded library resources can be rebuilt from public sources, but upstream availability and database hashes can change. User annotations, session histories and account identity are unique local data requiring backups. Calibration is gated by database SHA-256; a new library may require reviewed revalidation. The fixed BlueStacks paths, acc1/Pie64 mapping and portrait resolution remain deployment constraints.

Retained reference screenshots and game assets require a privacy/provenance/redistribution review. No license grant is assumed or invented. Credentials and machine-specific identifiers belong in ignored local configuration; the source includes only implementation defaults required for the supported deployment.

## Owner review, commit and push

These commands are recommendations only. They have not been executed. From the cleaned repository root:

```powershell
Set-Location D:\NBR
git status --short --untracked-files=all
git ls-files --others --exclude-standard
git diff --check
git add -- .
git diff --cached --stat
git diff --cached --check
git diff --cached
git commit -m "Clean and reorganize NBR automation manager"
git remote -v  # Existing origin: https://github.com/psittacosaurus-enjoyer/NBR-AM.git
git push -u origin main
```

The existing origin already points to the NBR-AM repository. Review that destination before pushing; change it deliberately with `git remote set-url origin <URL>` only if desired. Configure author name/email if Git requests them. If the destination already has commits, reconcile that history before pushing; do not overwrite it with a force push. The ignored nested repository's history is independent.

Run [the README's offline tests](../README.md) before committing. The restructuring completion report records actual verification and cleanup counts; [validation scope](VALIDATION.md) distinguishes earlier live evidence from current offline checks. Installing into a fresh environment and natural gameplay were not necessary to validate file relocation and must not be claimed unless independently performed.
